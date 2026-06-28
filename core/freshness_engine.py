"""Freshness engine for SparkyBot — keeps the LLM's voice from going stale.

Three layers:
  1. Banned-vocab scanner  — catches overused phrases, returns rejection + suggestions
  2. Axis rotation tracker — rotates opener/verb/angle/lens across calls
  3. Lens assembler        — composes a freshness hint string for the system prompt

Designed to sit between M1 (pre_digester buckets) and M6 (system prompt rewrite).
M3 (silent_failure_guard) handles the API call; this module handles the *voice*.
"""
from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

# ─── Banned vocab ────────────────────────────────────────────────────────────

DEFAULT_BANNED_PHRASES: list[str] = [
    "in conclusion",
    "it is worth noting",
    "it goes without saying",
    "at the end of the day",
    "needless to say",
    "it should be noted",
    "to summarize",
    "in summary",
    "all things considered",
    "when all is said and done",
    "the fact of the matter is",
    "bottom line",
    "game changer",
    "cutting edge",
    "synergy",
    "leverage",
    "paradigm shift",
    "circle back",
    "touch base",
    "move the needle",
    "low-hanging fruit",
    "deep dive",
    "unpack",
    "nuanced",
    "robust",
]

# Per-axis rotation pools — each axis rotates through its pool round-robin.
OPENER_POOL: list[str] = [
    "FIGHT REPORT:",
    "POST-GAME READ:",
    "FIELD NOTES:",
    "ENGAGEMENT LOG:",
    "BATTLE BREAKDOWN:",
    "STRAT READ:",
    "AAR:",               # After-Action Report
    "SCOUTING REPORT:",
]

VERB_POOL: list[str] = [
    "crushed", "dominated", "held", "clutched", "collapsed",
    "grinded out", "forced", "pulled", "secured", "stole",
    "shredded", "bulldozed", "outhealed", "outrotated", "punished",
    "fumbled", "choked", "got rolled by", "barely edged", "stalemated",
]

ANGLE_POOL: list[str] = [
    "numbers dont lie",
    "the macro was the real story",
    "comp diff decided this one",
    "positioning was everything",
    "cooldowns won that fight",
    "the engage timing broke it open",
    "sustain carried",
    "damage check was clean",
    "strip war decided it",
    "rally discipline made the diff",
]

LENS_POOL: list[str] = [
    "spicy",            # hot takes, trash talk
    "analytical",       # clean breakdown
    "narrative",        # story-telling frame
    "coach",            # instructional, next-time-do-this
    "hype",             # energy, caps, excitement
    "deadpan",          # dry humor, understatement
    "statistical",      # lead with the numbers
    "comparative",      # this fight vs last, team A vs team B
]


# ─── Data types ──────────────────────────────────────────────────────────────

@dataclass
class BannedVocabResult:
    """Outcome of a banned-vocab scan."""
    clean: bool                          # True = no banned phrases found
    violations: list[str] = field(default_factory=list)  # matched phrases
    suggestions: list[str] = field(default_factory=list)  # replacement hints


@dataclass
class AxisState:
    """Tracks rotation indices across the 4 axes."""
    opener_idx: int = 0
    verb_idx: int = 0
    angle_idx: int = 0
    lens_idx: int = 0

    def advance(self) -> None:
        """Bump all four indices round-robin."""
        self.opener_idx = (self.opener_idx + 1) % len(OPENER_POOL)
        self.verb_idx = (self.verb_idx + 1) % len(VERB_POOL)
        self.angle_idx = (self.angle_idx + 1) % len(ANGLE_POOL)
        self.lens_idx = (self.lens_idx + 1) % len(LENS_POOL)

    def current(self) -> dict[str, str]:
        return {
            "opener": OPENER_POOL[self.opener_idx],
            "verb": VERB_POOL[self.verb_idx],
            "angle": ANGLE_POOL[self.angle_idx],
            "lens": LENS_POOL[self.lens_idx],
        }


@dataclass
class FreshnessHint:
    """Composed hint string + metadata for the system prompt."""
    hint_text: str
    axis_snapshot: dict[str, str]
    banned_result: Optional[BannedVocabResult] = None


# ─── Layer 1: Banned-vocab scanner ───────────────────────────────────────────

def scan_banned_vocab(
    text: str,
    banned: list[str] | None = None,
    cooldown_seconds: int = 0,
    _cache: dict | None = None,
) -> BannedVocabResult:
    """Scan text for banned phrases. Case-insensitive substring match.

    Args:
        text: The LLM-generated text to scan.
        banned: Override list of banned phrases (defaults to DEFAULT_BANNED_PHRASES).
        cooldown_seconds: If >0, phrases already flagged in the last N seconds
                          are skipped (via _cache dict keyed by phrase).
    """
    if banned is None:
        banned = DEFAULT_BANNED_PHRASES

    lowered = text.lower()
    violations: list[str] = []
    now = time.monotonic()

    for phrase in banned:
        if phrase.lower() in lowered:
            if cooldown_seconds > 0 and _cache is not None:
                last_seen = _cache.get(phrase, 0)
                if now - last_seen < cooldown_seconds:
                    continue
                _cache[phrase] = now
            violations.append(phrase)

    suggestions = _build_suggestions(violations)
    return BannedVocabResult(
        clean=(len(violations) == 0),
        violations=violations,
        suggestions=suggestions,
    )


def _build_suggestions(violations: list[str]) -> list[str]:
    """Map common banned phrases to punchier alternatives."""
    alt_map = {
        "in conclusion": "just say the verdict — drop 'in conclusion'",
        "it is worth noting": "lead with it or cut it",
        "at the end of the day": "delete this — it says nothing",
        "game changer": "say WHAT changed specifically",
        "cutting edge": "just describe the tech",
        "bottom line": "start with the bottom line instead",
        "deep dive": "say 'here's what happened' instead",
        "nuanced": "describe the nuance instead of labeling it",
        "robust": "say what it actually did",
    }
    return [alt_map.get(v, f"rephrase or remove '{v}'") for v in violations]


# ─── Layer 2: Axis rotation ─────────────────────────────────────────────────

class AxisRotator:
    """Round-robin rotation across the 4 voice axes.

    Persists state in-memory per process lifetime. For multi-process
    deployments, serialize AxisState to redis/file (not in this MVP).
    """

    def __init__(self, initial_state: AxisState | None = None):
        self.state = initial_state or AxisState()

    def next_axes(self) -> dict[str, str]:
        """Return the current axis values, then advance."""
        snapshot = self.state.current()
        self.state.advance()
        return snapshot

    def peek(self) -> dict[str, str]:
        """Return current axis values without advancing."""
        return self.state.current()


# ─── Layer 3: Lens assembler ────────────────────────────────────────────────

def assemble_hint(
    axes: dict[str, str],
    buckets: dict[str, str | list[str]] | None = None,
    banned_result: BannedVocabResult | None = None,
) -> FreshnessHint:
    """Compose a freshness hint for injection into the system prompt.

    Args:
        axes: Output of AxisRotator.next_axes().
        buckets: Optional M1 pre_digester output — used to modulate lens.
        banned_result: Optional scan result — if dirty, inject reminder.
    """
    lens = axes["lens"]
    opener = axes["opener"]

    # Modulate lens based on fight outcome if available
    if buckets:
        outcome_shape = buckets.get("outcome_shape", "")
        if outcome_shape == "COLLAPSE" and lens in ("hype", "deadpan"):
            lens = "spicy"           # losses get trash-talk, not dry recaps
        elif outcome_shape == "EXECUTION" and lens == "deadpan":
            lens = "hype"            # dominant wins deserve energy

    parts = [
        f"Voice lens: {lens}",
        f"Open with: {opener}",
        f"Use verb energy: {axes['verb']}",
        f"Angle: {axes['angle']}",
    ]

    if banned_result and not banned_result.clean:
        parts.append(
            f"AVOID these stale phrases: {', '.join(banned_result.violations)}"
        )
        parts.append(
            f"Replacements: {'; '.join(banned_result.suggestions[:3])}"
        )

    # Attach bucket context from M1 if present
    if buckets:
        shape = buckets.get("outcome_shape", "")
        comp = buckets.get("comp_archetype", [])
        parts.append(f"Fight shape: {shape}")
        if comp:
            parts.append(f"Comp archetype: {', '.join(comp)}")

    hint_text = " | ".join(parts)

    return FreshnessHint(
        hint_text=hint_text,
        axis_snapshot=axes,
        banned_result=banned_result,
    )


# ─── High-level entry point ─────────────────────────────────────────────────

class FreshnessEngine:
    """Composes all three layers into a single call.

    Usage:
        engine = FreshnessEngine()
        hint = engine.process(
            previous_response="...",    # scan for banned vocab
            buckets=m1_output,          # M1 pre_digester buckets
        )
        # inject hint.hint_text into the system prompt (M6)
    """

    def __init__(
        self,
        banned_phrases: list[str] | None = None,
        banned_cooldown: int = 300,  # seconds
    ):
        self.banned_phrases = banned_phrases or DEFAULT_BANNED_PHRASES
        self.banned_cooldown = banned_cooldown
        self._banned_cache: dict[str, float] = {}
        self.rotator = AxisRotator()

    def process(
        self,
        previous_response: str = "",
        buckets: dict[str, str | list[str]] | None = None,
    ) -> FreshnessHint:
        """Full pipeline: scan → rotate → assemble."""
        # Layer 1: scan previous response for banned vocab
        banned_result = None
        if previous_response:
            banned_result = scan_banned_vocab(
                previous_response,
                banned=self.banned_phrases,
                cooldown_seconds=self.banned_cooldown,
                _cache=self._banned_cache,
            )

        # Layer 2: rotate axes
        axes = self.rotator.next_axes()

        # Layer 3: assemble hint
        return assemble_hint(axes, buckets=buckets, banned_result=banned_result)
