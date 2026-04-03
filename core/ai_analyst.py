"""AI-powered fight analysis — works with any OpenAI-compatible API.

Supports: OpenAI, MiniMax, Groq, Together AI, Mistral, OpenRouter,
Anthropic (via proxy), local Ollama, LM Studio, and any service that
implements the /v1/chat/completions endpoint.

Requires Python 3.9+.
"""

import json
import logging
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
import requests
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


# Vocabulary is loaded from sparkybot_vocabulary.json by VocabularyConfig.
# Do not add terms here — edit the JSON file instead.


# Pattern for stripping LLM think tags in any state (closed, unclosed, stray)
_THINK_TAG_RE = re.compile(r'<think>.*?</think>', re.DOTALL)
_THINK_UNCLOSED_RE = re.compile(r'<think>.*', re.DOTALL)
_THINK_STRAY_RE = re.compile(r'</?think>')

# Sentence splitting that respects common abbreviations and decimals
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')

# Detect stat/number references in generated responses.
# Matches digit sequences (optionally with k/m suffix or commas), percentages,
# and ratio patterns like "5.7 KDR".  Used to classify responses as
# stat-heavy vs narrative-only for variety tracking.
_STAT_RE = re.compile(
    r'\b\d[\d,]*\.?\d*\s*[kKmM]?\b'   # 560k, 24,000, 5.73, etc.
    r'|\b\d+%'                          # 45%
    r'|\b\d+\s*:\s*\d+'                 # 3:1 ratio
    r'|\b\d+\.\d+\s+KDR\b',            # 5.73 KDR
    re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Default prompt version — increment when _core_system_prompt or _rules_section
# changes so users on custom prompts can be notified of improvements.
# ---------------------------------------------------------------------------
DEFAULT_PROMPT_VERSION = 2

DEFAULT_PROMPT_CHANGELOG = {
    1: {
        "title": "Dynamic vocabulary and pre-analysis",
        "changes": [
            "Fight data is now pre-analyzed in Python before reaching the AI, producing more accurate commentary",
            "Vocabulary terms are dice-rolled each call so the AI uses different language every time",
            "Stats can now appear in commentary when they're dramatic enough to land",
            "Stomp discipline is properly graded, including calling out when downed enemies are rallying",
            "The AI can now invent its own phrases instead of only using predefined terms",
            "Overused terms are automatically blocked to prevent repetitive commentary",
        ],
        "reason": "These changes cut prompt size by ~35% while producing more varied, accurate, and entertaining commentary.",
    },
    2: {
        "title": "Anti-pattern examples and opener variety",
        "changes": [
            "Removed cross-response variety instructions that the model cannot follow in stateless calls. Structural guidance now uses concrete WRONG/RIGHT examples instead.",
            "WRONG/RIGHT example pairs added to prevent '[Player] was a [noun]', 'turning X into Y', and double-player 'while' constructions",
            "Opener strategy: choose an opening that fits the fight data, do not default to shock exclamation when another opener fits better",
            "Sub-300s fights now require the AI to reference fight speed in the opening sentence",
            "Post-processing monitors stat density and overused term violations in AI output",
            "Vocabulary saturation collapses the ban list when all terms are overused, preventing priming",
            "Gate conditions are now enforced in Python before reaching the AI",
            "Stat guidance tightened: 'pick the single most dramatic number' replaces 'rotate through different categories'",
        ],
        "reason": "Analysis of 15 responses showed recurring structural patterns and rule violations that these changes address.",
    },
}

# ---------------------------------------------------------------------------
# Tag distance thresholds (distance-to-tag in fight summary units)
# Used by _pre_analyze() to grade squad spread relative to commander tag
# ---------------------------------------------------------------------------
TAG_DISTANCE_EXCELLENT = 1200  # <= 1200: tight, well-positioned squad
TAG_DISTANCE_ACCEPTABLE = 2000  # 1201-2000: acceptable spread
TAG_DISTANCE_LOOSE = 3500       # 2001-3500: squad drifting, call it out
                                 # > 3500: very loose, commander should address

# ---------------------------------------------------------------------------
# VocabularyConfig — loads vocabulary from JSON, compiles regex patterns,
# and handles dice rolling for per-call term selection.
# ---------------------------------------------------------------------------

class VocabularyConfig:
    """Loads vocabulary from a JSON config file, compiles regex patterns,
    and handles dice rolling for per-call term selection.

    Caches the loaded config. Call reload() or create a new instance
    to pick up file changes.
    """

    def __init__(self, config_path: Path = None):
        self.config_path = config_path or Path.cwd() / "sparkybot_vocabulary.json"
        self._raw: dict = {}           # raw JSON data
        self._compiled: list = []      # [(name, [compiled_regex, ...]), ...] for tracker
        self._mtime: float = 0         # last modified time for auto-reload
        self.load()

    @staticmethod
    def _default_vocabulary() -> dict:
        """Return the full default vocabulary structure.

        This is the single source of truth for all default terms.
        Used to generate sparkybot_vocabulary.json on first run.
        """
        return {
            "version": 2,
            "weights": {
                "shock": 0.33,
                "positive": 0.33,
                "negative": 0.33,
                "gates": 0.33
            },
            "shock": [
                {"term": "HOLY SHIT", "pattern": "\\bholy\\s+shit\\b", "desc": "reserved for the most legendary wins or most catastrophic losses only", "caps": "always"},
                {"term": "WHAT THE HELL", "pattern": "\\bwhat\\s+the\\s+hell\\b", "desc": "disbelief at an unexpected result or a badly executed push", "caps": "always"},
                {"term": "HOLY HELL", "pattern": "\\bholy\\s+hell\\b", "desc": "slightly milder shock, good for surprising wins or alarming loss margins", "caps": "always"},
                {"term": "JESUS CHRIST", "pattern": "\\bjesus\\s+christ\\b", "desc": "genuine awe at an outlier performance or a brutal wipeout", "caps": "always"},
                {"term": "GOD DAMNIT", "pattern": "\\bgod\\s+damn?it\\b", "desc": "direct frustration at a preventable loss or a tactical failure", "caps": "always"},
                {"term": "WHAT THE FUCK WAS THAT", "pattern": "\\bwhat\\s+the\\s+fuck\\s+was\\s+that\\b", "desc": "reserved for losses under 300 seconds or catastrophically bad stomp discipline", "caps": "always"},
                {"term": "WHAT THE ACTUAL FUCK", "pattern": "\\bwhat\\s+the\\s+actual\\s+fuck\\b", "desc": "the most extreme version, for truly inexcusable data", "caps": "always"},
                {"term": "COME ON GUYS", "pattern": "\\bcome\\s+on\\s+guys\\b", "desc": "targeted frustration at the squad for a winnable fight they threw away", "caps": "always"},
            ],
            "positive": [
                {"term": "massacre", "alt": "slaughter", "pattern": "\\bmassacre\\b", "alt_pattern": "\\bslaughter(?:ed)?\\b", "desc": "when enemy deaths vastly outnumber squad deaths and the fight was completely one-sided. Can also describe the squad getting wiped.", "caps": "optional"},
                {"term": "ABSOLUTE MONSTERS", "pattern": "\\babsolute\\s+monsters?\\b", "desc": "squad-wide dominant performance in an outnumbered win", "caps": "always"},
                {"term": "GIGACHADS", "pattern": "\\bgigachads?\\b", "desc": "elite level performance under pressure", "caps": "always"},
                {"term": "here to pump", "pattern": "\\bhere\\s+to\\s+pump\\b", "desc": "when top damage dealers were clearly the deciding factor", "caps": "optional"},
                {"term": "BIG DAMAGE", "pattern": "\\bbig\\s+damage\\b", "desc": "callout for an individual player with outsized damage contribution", "caps": "always"},
                {"term": "battering ram", "pattern": "\\bbattering\\s+ram\\b", "desc": "squad pushed through everything without breaking", "caps": "optional"},
                {"term": "relentless", "pattern": "\\brelentless\\b", "desc": "sustained pressure across a long fight that never let the enemy breathe", "caps": "optional"},
                {"term": "absolute/absolutely", "pattern": "\\babsolute(?:ly)?\\b", "desc": "Generic intensifier. Tracked to prevent overuse, not a specific catchphrase.", "caps": "optional"},
                {"term": "a free win", "pattern": "\\ba\\s+free\\s+win\\b", "desc": "only for Decisive Win with numbers advantage where the outcome was never in doubt", "caps": "optional"},
                {"term": "MAGNIFICENT MOTHERFUCKERS", "pattern": "\\bmagnificent\\s+motherfuckers?\\b", "desc": "squad-wide praise when the group performed brilliantly as a unit", "caps": "always"},
                {"term": "RIDE 'EM LIKE A PONY", "pattern": "ride\\s*['\u2019e]e?m\\s+like\\s+a\\s+pony", "desc": "squad absolutely steamrolls the enemy start to finish", "caps": "always"},
                {"term": "YEET YEET DELETE", "pattern": "\\byeet\\s+yeet\\s+delete\\b", "desc": "instantaneous down-to-kill conversion, enemies deleted before anyone could blink", "caps": "always"},
                {"term": "optimal button pressing", "pattern": "\\boptimal\\s+button\\s+pressing\\b", "desc": "player or squad executing class rotations with high efficiency. Can be sarcastic in a loss.", "caps": "optional"},
            ],
            "negative": [
                {"term": "fed to the wolves", "pattern": "\\bfed\\s+to\\s+the\\s+wolves\\b", "desc": "squad walked into a situation they were never going to survive", "caps": "optional"},
                {"term": "TOIGHT LIKE A TIGER", "pattern": "\\btoight\\s+like\\s+a?\\s*tiger\\b", "desc": "squad not tight enough on tag, players spread out or drifting. Spelled 'toight' not 'tight'.", "caps": "always"},
            ],
            "gates": [
                {"gate": 1, "term": "Siege Humping", "pattern": "\\bsiege\\s+humping\\b", "condition": "top_enemy_skills contains siege weapon skills (Arrow Cart, Superior Arrow Cart, Mortar Shot, Ballista, Trebuchet) AND siege damage meaningfully contributed to squad deaths", "instruction": "Mock the enemy for hiding behind catapults instead of fighting in the open. Fires regardless of outcome.", "caps": "always"},
                {"gate": 2, "term": "Skill Lag", "pattern": "\\bskill\\s+lag\\b", "condition": "enemy_teams has 2+ servers AND outcome is Loss, Draw, or squad_deaths is high relative to squad size", "instruction": "Blame server infrastructure for the chaos.", "caps": "always"},
                {"gate": 3, "term": "On Tag", "pattern": "\\bon\\s+tag\\b", "condition": "outcome is Win or Decisive Win AND squad_cleanses and squad_healing are both very high", "instruction": "Squad was stacked tight and disciplined. Praise positioning.", "caps": "always"},
                {"gate": 4, "term": "Bags", "pattern": "\\bbags\\b", "condition": "outcome is Win or Decisive Win AND enemy took massive casualties", "instruction": "The enemy push became loot bags on the ground.", "caps": "always"},
                {"gate": 5, "term": "Police the Rosters", "pattern": "\\bpolice\\s+(the\\s+)?rosters?\\b", "condition": "outcome is Win or Decisive Win AND you want to mock the enemy's inability to handle the squad", "instruction": "Mock the enemy for letting players this bad represent their server.", "caps": "always"},
                {"gate": 6, "term": "Rallybot", "pattern": "\\brallybot\\b", "condition": "outcome is Loss or Draw AND ally_count exceeds 20% of friendly_count", "instruction": "PUGs gifted the enemy free rallies by dying at exactly the wrong moment.", "caps": "always"},
                {"gate": 7, "term": "Mudda Fucka", "pattern": "\\bmudda\\s+fucka\\b", "condition": "outcome is Decisive Loss", "instruction": "One expression of maximum commander frustration.", "caps": "always"},
                {"gate": 8, "term": "Blob", "alt": "Zerg", "pattern": "\\bblob\\b", "alt_pattern": "\\bzerg\\b", "condition": "enemy was a numerically overwhelming, uncoordinated mass", "instruction": "Mock the enemy for being an uncoordinated mass.", "caps": "always"},
            ]
        }

    def _write_defaults(self) -> None:
        """Write the default vocabulary config to disk."""
        try:
            self.config_path.write_text(
                json.dumps(self._raw, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            self._mtime = self.config_path.stat().st_mtime
            logger.info("VocabularyConfig: wrote default config to %s", self.config_path)
        except Exception as exc:
            logger.warning("VocabularyConfig: could not write default config: %s", exc)

    def load(self) -> None:
        """Load and compile vocabulary from the config file."""
        try:
            stat = self.config_path.stat()
            if stat.st_mtime == self._mtime and self._raw:
                return  # no change
            self._raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            self._mtime = stat.st_mtime
            self._compile_patterns()
            logger.info("VocabularyConfig: loaded %d terms from %s",
                        len(self._compiled), self.config_path)
        except FileNotFoundError:
            logger.info("VocabularyConfig: no config file at %s, generating defaults", self.config_path)
            self._raw = self._default_vocabulary()
            self._compile_patterns()
            self._write_defaults()
        except Exception as exc:
            logger.warning("VocabularyConfig: failed to load config: %s", exc)
            if not self._raw:
                self._raw = {"shock": [], "positive": [], "negative": [], "gates": []}
                self._compiled = []

    def reload_if_changed(self) -> None:
        """Reload config if the file has been modified since last load."""
        try:
            stat = self.config_path.stat()
            if stat.st_mtime != self._mtime:
                self.load()
        except Exception:
            pass

    @property
    def compiled_patterns(self) -> list:
        """Return [(term_name, [compiled_regex, ...]), ...] for VocabularyTracker."""
        return self._compiled

    def all_terms(self) -> list:
        """Return flat list of all term dicts across all categories."""
        result = []
        for cat in ("shock", "positive", "negative", "gates"):
            result.extend(self._raw.get(cat, []))
        return result

    def roll_active_terms(self, overused_terms: set,
                          weight_overrides: dict = None,
                          fight_summary: dict = None) -> Dict[str, list]:
        """Dice-roll each term. Overused terms get 0% chance.

        Precedence: weight_overrides (GUI/slider) > file weights block > default 0.33.

        Gate conditions are evaluated against fight_summary before the dice roll
        for gates entries. Entries whose condition is not met are skipped.

        Returns dict with keys 'shock', 'positive', 'negative', 'gates',
        each containing a list of term dicts that survived.
        """
        self.reload_if_changed()

        file_weights = self._raw.get("weights", {})
        weights = {**file_weights, **(weight_overrides or {})}
        default_weight = 1 / 3

        def gate_matches(gate, summary):
            """Check if a gate's condition is plausibly met by the fight data."""
            if not summary:
                return True  # no summary = no filtering, let the model decide

            outcome = summary.get("outcome", "")
            term = gate.get("term", "").lower()

            # Mudda Fucka: Decisive Loss only
            if "mudda" in term and outcome != "Decisive Loss":
                return False

            # Rallybot: Loss or Draw with significant PUGs
            if "rallybot" in term:
                if outcome not in ("Loss", "Decisive Loss", "Draw"):
                    return False
                friendly = summary.get("friendly_count", 0)
                ally = summary.get("ally_count", 0)
                if friendly > 0 and (ally / friendly) < 0.20:
                    return False

            # Siege Humping: requires siege skills in top_enemy_skills
            if "siege" in term:
                siege_names = {"Arrow Cart", "Superior Arrow Cart", "Ballista", "Mortar Shot", "Trebuchet"}
                top_skills = [s.get("name", "") for s in summary.get("top_enemy_skills", [])]
                if not any(s in siege_names for s in top_skills):
                    return False

            # Skill Lag: requires 2+ enemy teams AND loss/draw/high deaths
            if "skill lag" in term:
                teams = summary.get("enemy_teams", {})
                if len(teams) < 2:
                    return False
                if outcome not in ("Loss", "Decisive Loss", "Draw"):
                    squad_deaths = summary.get("squad_deaths", 0)
                    squad_count = summary.get("squad_count", 1)
                    if squad_deaths / squad_count < 0.5:
                        return False

            # On Tag: Win or Decisive Win with high cleanses and healing
            if "on tag" in term:
                if "Win" not in outcome:
                    return False

            # Bags: Win or Decisive Win with high enemy casualties
            if term == "bags":
                if "Win" not in outcome:
                    return False

            # Police the Rosters: Win or Decisive Win
            if "police" in term:
                if "Win" not in outcome:
                    return False

            # a free win: Decisive Win with numbers advantage only
            if "free win" in term:
                if outcome != "Decisive Win":
                    return False
                friendly = summary.get("friendly_count", 0)
                enemy = summary.get("enemy_count", 0)
                if friendly > 0 and enemy / friendly > 0.85:
                    return False

            return True

        def roll(category_key, terms):
            prob = weights.get(category_key, default_weight)
            prob = max(0.0, min(1.0, float(prob)))
            result = []
            for t in terms:
                name = t["term"]
                alt = t.get("alt", "")
                if name in overused_terms or alt in overused_terms:
                    continue
                # Gate condition check
                if category_key == "gates" and not gate_matches(t, fight_summary):
                    continue
                if random.random() < prob:
                    result.append(t)
            return result

        return {
            "shock": roll("shock", self._raw.get("shock", [])),
            "positive": roll("positive", self._raw.get("positive", [])),
            "negative": roll("negative", self._raw.get("negative", [])),
            "gates": roll("gates", self._raw.get("gates", [])),
        }

    def _compile_patterns(self) -> None:
        """Build compiled regex list from all categories for the tracker."""
        self._compiled = []
        for cat in ("shock", "positive", "negative", "gates"):
            for entry in self._raw.get(cat, []):
                name = entry["term"]
                patterns = []
                if "pattern" in entry:
                    patterns.append(re.compile(entry["pattern"], re.IGNORECASE))
                if "alt_pattern" in entry:
                    patterns.append(re.compile(entry["alt_pattern"], re.IGNORECASE))
                if patterns:
                    self._compiled.append((name, patterns))
                # Also track the alt term name if present
                alt = entry.get("alt")
                if alt and "alt_pattern" in entry:
                    self._compiled.append((alt, [re.compile(entry["alt_pattern"], re.IGNORECASE)]))

    # ------------------------------------------------------------------
    # Version / update management
    # ------------------------------------------------------------------

    def update_available(self) -> bool:
        """Return True if the default vocabulary version is newer than the user's file."""
        file_version = self._raw.get("version", 0)
        default_version = self._default_vocabulary().get("version", 0)
        return file_version < default_version

    def get_update_diff(self) -> dict:
        """Return a summary of what changed between user's version and defaults.

        Returns dict with keys 'added', 'removed', 'version_from', 'version_to'.
        """
        defaults = self._default_vocabulary()
        current_terms = set()
        default_terms = set()

        for cat in ("shock", "positive", "negative", "gates"):
            for entry in self._raw.get(cat, []):
                current_terms.add(entry.get("term", ""))
            for entry in defaults.get(cat, []):
                default_terms.add(entry.get("term", ""))

        return {
            "added": sorted(default_terms - current_terms),
            "removed": sorted(current_terms - default_terms),
            "version_from": self._raw.get("version", 0),
            "version_to": defaults.get("version", 0),
        }

    def apply_default_update(self, merge: bool = True) -> None:
        """Update the vocabulary file to the latest defaults.

        Args:
            merge: If True, add new terms but keep user's existing terms and
                   modifications. If False, replace everything with defaults.
        """
        defaults = self._default_vocabulary()

        if not merge:
            self._raw = defaults
        else:
            # Preserve user's weights if they've customized them
            if "weights" in self._raw:
                defaults["weights"] = self._raw["weights"]

            # For each category: keep user's existing terms, append new ones
            for cat in ("shock", "positive", "negative", "gates"):
                existing_terms = {e["term"] for e in self._raw.get(cat, [])}
                new_entries = [
                    e for e in defaults.get(cat, [])
                    if e["term"] not in existing_terms
                ]
                if new_entries:
                    self._raw.setdefault(cat, []).extend(new_entries)

            # Update version stamp
            self._raw["version"] = defaults["version"]

        self._compile_patterns()
        self._write_defaults()

    def is_user_modified(self) -> bool:
        """Return True if the user has ever customized the vocabulary."""
        return self._raw.get("user_modified", False)

    def mark_modified(self) -> None:
        """Mark the vocabulary as user-customized."""
        self._raw["user_modified"] = True
        self._write_defaults()


# ---------------------------------------------------------------------------
# Vocabulary tracker — records which SparkyBot terms have been used recently
# so the AI can be told to vary its language across a session.
# ---------------------------------------------------------------------------


class VocabularyTracker:
    """Tracks SparkyBot vocabulary usage over a rolling time window.

    Persists to a JSON file on disk so counts survive process restarts.
    Thread-safety is not guaranteed; fine for single-process use.
    Concurrent access from multiple processes may lose writes (last-write-wins).
    """

    def __init__(self, store_path: Path = None, window_hours: int = 2,
                 vocab_config: VocabularyConfig = None):
        self.store_path = store_path or Path.cwd() / "sparkybot_vocab_usage.json"
        self.window_seconds = window_hours * 3600
        self.vocab_config = vocab_config
        self._events: list = []        # list of {"term": str, "ts": float}
        self._stat_events: list = []   # list of {"count": int, "ts": float}
        self._style_events: list = []  # list of {"used_palette": bool, "ts": float}
        self._opener_events: list = []  # list of {"strategy": str, "ts": float}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, response_text: str) -> None:
        """Scan a response and record vocabulary terms, stat density, and style."""
        if not response_text:
            return
        now = time.time()

        # Vocabulary tracking
        matched_any_palette = False
        patterns = self.vocab_config.compiled_patterns if self.vocab_config else []
        for name, compiled in patterns:
            for pattern in compiled:
                if pattern.search(response_text):
                    self._events.append({"term": name, "ts": now})
                    matched_any_palette = True
                    break  # only count each term once per response

        # Stat density tracking: count distinct number references
        stat_matches = _STAT_RE.findall(response_text)
        self._stat_events.append({"count": len(stat_matches), "ts": now})

        # Style tracking: did this response use predefined palette terms or go freestyle?
        self._style_events.append({"used_palette": matched_any_palette, "ts": now})

        # Opener strategy tracking
        self.record_opener(response_text)

        self._prune()
        self._save()

    def record_opener(self, response_text: str) -> None:
        """Classify and record the opener strategy used."""
        if not response_text:
            return
        now = time.time()
        first_words = response_text.split()[:5]
        first_text = ' '.join(first_words).lower()
        first_word = first_words[0].lower() if first_words else ""

        # Classify opener strategy
        if any(w.isupper() and len(w) > 2 for w in first_words[:2]):
            strategy = "exclamation"
        elif any(kw in first_text for kw in ["minute", "second", "under ", "seconds"]):
            strategy = "duration"
        elif any(kw in first_text for kw in ["scourge", "weaver", "enemy", "their "]):
            strategy = "enemy_comp"
        elif first_word in ("that", "the", "this", "a", "an", "in", "for", "after",
                            "despite", "outnumbered", "bálls", "balls"):
            # Demonstratives, articles, and common narrative openers that are
            # not player names — classify as narrative to avoid over-filling
            # the player bucket and starving other strategies in recent_3.
            strategy = "narrative"
        elif first_words[0][0].isupper():
            strategy = "player"
        else:
            strategy = "narrative"

        self._opener_events.append({"strategy": strategy, "ts": now})

    def _build_opener_guidance(self) -> str:
        """Suggest an opener strategy based on recent usage.

        Looks back at the last 3 openers (not just the last 1) to avoid
        cycling between only two styles. The chosen strategy is stated as
        a directive, not a suggestion.
        """
        if not self._opener_events:
            return ""

        recent = sorted(self._opener_events, key=lambda e: e["ts"], reverse=True)[:5]
        strategies = [e["strategy"] for e in recent]

        last = strategies[0] if strategies else None
        # Exclude any strategy used in the last 3 responses so we don't
        # just oscillate between two styles
        recent_3 = set(strategies[:3])

        all_strategies = ["player", "duration", "enemy_comp", "exclamation", "narrative"]
        available = [s for s in all_strategies if s not in recent_3]

        # If all strategies have been used recently, just avoid the last one
        if not available:
            available = [s for s in all_strategies if s != last]

        chosen = random.choice(available) if available else "narrative"

        descriptions = {
            "player": "Open with a player name as the first word and lead with what they did.",
            "duration": "Open with a reference to the fight duration or speed.",
            "enemy_comp": "Open with a callout to the enemy composition or strategy.",
            "exclamation": "Open with a shock exclamation or invented phrase.",
            "narrative": "Open with a narrative statement about the fight outcome.",
        }

        return (
            "\n\nOPENER GUIDANCE\n"
            f"Your recent openers have favored '{last}' style. "
            f"For this response: {descriptions.get(chosen, 'Vary your opener.')}"
        )

    def build_injection_block(self) -> str:
        """Return a prompt block describing recent vocabulary, stat, and style usage."""
        self._prune()

        # --- Vocabulary section ---
        counts: Dict[str, int] = {}
        for event in self._events:
            counts[event["term"]] = counts.get(event["term"], 0) + 1

        if self.vocab_config:
            all_names = [name for name, _ in self.vocab_config.compiled_patterns]
        else:
            all_names = []
        unused    = [n for n in all_names if n not in counts]
        used_once = [n for n in all_names if counts.get(n) == 1]
        overused  = sorted(
            [(n, counts[n]) for n in all_names if counts.get(n, 0) >= 3],
            key=lambda x: -x[1]
        )

        lines: list = []

        if counts:
            lines += [
                "\n\nRECENT VOCABULARY USAGE",
                "The following palette terms have been used in the last 2 hours of commentary.",
                "Actively vary your language. Terms marked as overused are OFF LIMITS for this response.",
            ]
            if overused:
                parts = ", ".join(f"{n} (x{c})" for n, c in overused)
                lines.append(f"OVERUSED — DO NOT USE these terms in this response: {parts}.")
            if used_once:
                lines.append(f"Used once — okay to reuse if it genuinely fits: {', '.join(used_once)}.")
            if unused:
                lines.append(f"Not yet used — preferred if the fight data supports them: {', '.join(unused)}.")

            # Saturation collapse: if too many terms are overused, replace the
            # entire vocabulary section with a compact directive to avoid
            # priming the model with banned terms
            if len(overused) > 15:
                lines = [
                    "\n\nRECENT VOCABULARY USAGE",
                    "ALL predefined palette terms are currently overused. "
                    "Do not use any predefined term. Invent all vocabulary from scratch.",
                ]

        # --- Stat density section ---
        stat_guidance = self._build_stat_guidance()
        if stat_guidance:
            lines.append(stat_guidance)

        # --- Style section (palette vs freestyle) ---
        style_guidance = self._build_style_guidance()
        if style_guidance:
            lines.append(style_guidance)

        return "\n".join(lines) if lines else ""

    def get_overused_terms(self) -> set:
        """Return a set of term names that have been used 2+ times in the window.

        Used by VocabularyConfig.roll_active_terms to auto-exclude overused
        terms from the dice roll (0% inclusion chance).
        """
        self._prune()
        counts: Dict[str, int] = {}
        for event in self._events:
            counts[event["term"]] = counts.get(event["term"], 0) + 1
        return {name for name, count in counts.items() if count >= 3}

    def _build_stat_guidance(self) -> str:
        """Analyze recent stat usage and return guidance for the next response.

        Looks at the last several responses to determine whether the model
        has been leaning too heavily on numbers or too heavily on pure
        narrative, then nudges it toward the opposite style.
        """
        if not self._stat_events:
            return ""

        # Consider the last 5 responses (or fewer if not enough history)
        recent = sorted(self._stat_events, key=lambda e: e["ts"], reverse=True)[:5]
        stat_heavy = sum(1 for e in recent if e["count"] >= 2)
        narrative_only = sum(1 for e in recent if e["count"] == 0)
        total = len(recent)

        # If we only have one data point, no meaningful trend yet
        if total <= 1:
            return ""

        lines = ["\n\nSTAT USAGE GUIDANCE"]

        if stat_heavy >= 3:
            # Majority of recent responses used multiple stats — push narrative
            lines.append(
                "Your recent commentary has been stat-heavy. For this response, "
                "lean into pure narrative: describe what happened on the field "
                "using vivid conclusions, not numbers. Let the story carry the "
                "weight instead of the data. If you absolutely must reference "
                "one stat for impact, make it a single number woven into a "
                "sentence, never two or more."
            )
        elif narrative_only >= 3:
            # Majority of recent responses had zero stats — encourage some
            lines.append(
                "Your recent commentary has been purely narrative with no "
                "concrete stats. For this response, anchor one of your points "
                "with exactly one number from the data: a kill count, a damage "
                "figure, a KDR, or a player count disparity. Pick the single "
                "most dramatic number, weave it into a narrative sentence, and "
                "stop there. One stat only."
            )
        else:
            # Mixed usage — light guidance toward variety
            lines.append(
                "Your recent commentary has a healthy mix of stats and "
                "narrative. Continue varying your approach. If the last "
                "response used numbers, consider going pure narrative this "
                "time. If it was all narrative, consider dropping one "
                "punchy stat into this one."
            )

        return "\n".join(lines)

    def _build_style_guidance(self) -> str:
        """Analyze recent palette vs freestyle usage and nudge the model.

        Tracks whether recent responses used predefined palette terms or
        went freestyle (no palette matches detected), then pushes the model
        toward whichever style has been used less recently.
        """
        if not self._style_events:
            return ""

        recent = sorted(self._style_events, key=lambda e: e["ts"], reverse=True)[:5]
        total = len(recent)

        if total <= 1:
            return ""

        palette_count = sum(1 for e in recent if e["used_palette"])
        freestyle_count = total - palette_count

        lines = ["\n\nSTYLE GUIDANCE"]

        if palette_count >= 3:
            lines.append(
                "Your recent commentary has relied heavily on predefined palette "
                "terms. For this response, GO FREESTYLE: invent your own vivid "
                "phrases, insults, hype language, or exclamations that fit the "
                "fight. Do not use any term from the palette list. Create "
                "something original with the same energy and specificity. "
                "Generic praise like 'great job' or 'well played' is not "
                "freestyle, it is lazy. Invent something that only a deranged "
                "WvW commentator would say."
            )
        elif freestyle_count >= 3:
            lines.append(
                "Your recent commentary has been freestyle with no palette "
                "terms. For this response, use one of the predefined palette "
                "terms from the sections above if the fight data supports it. "
                "Pick one that fits naturally."
            )
        else:
            if recent[0]["used_palette"]:
                lines.append(
                    "Your last response used a palette term. Consider going "
                    "freestyle this time: invent your own phrase or exclamation "
                    "instead of reaching for the predefined list."
                )
            else:
                lines.append(
                    "Your last response was freestyle. You can use a palette "
                    "term this time if one fits the fight data naturally, or "
                    "continue freestyling if you have something good."
                )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prune(self) -> None:
        """Remove events older than the rolling window."""
        cutoff = time.time() - self.window_seconds
        self._events = [e for e in self._events if e["ts"] >= cutoff]
        self._stat_events = [e for e in self._stat_events if e["ts"] >= cutoff]
        self._style_events = [e for e in self._style_events if e["ts"] >= cutoff]
        self._opener_events = [e for e in self._opener_events if e["ts"] >= cutoff]

    def _load(self) -> None:
        """Load persisted events from disk, ignoring errors.

        Backward-compatible: old stores without ``stat_events`` or
        ``style_events`` are handled gracefully (histories start empty).
        """
        try:
            if self.store_path.exists():
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                self._events = data.get("events", [])
                self._stat_events = data.get("stat_events", [])
                self._style_events = data.get("style_events", [])
                self._opener_events = data.get("opener_events", [])
                self._prune()
        except Exception as exc:
            logger.warning("VocabularyTracker: could not load store: %s", exc)
            self._events = []
            self._stat_events = []
            self._style_events = []
            self._opener_events = []

    def _save(self) -> None:
        """Persist current events to disk, ignoring errors."""
        try:
            self.store_path.write_text(
                json.dumps({
                    "events": self._events,
                    "stat_events": self._stat_events,
                    "style_events": self._style_events,
                    "opener_events": self._opener_events,
                }, indent=2),
                encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("VocabularyTracker: could not save store: %s", exc)


# Preset configurations for popular providers
PRESETS = {
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
    "Google Gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-2.5-flash",
        "models": [
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
        ],
    },
    "MiniMax": {
        "base_url": "https://api.minimaxi.chat/v1",
        "default_model": "MiniMax-M2.7",
        "models": [
            "MiniMax-M2.7",
            "MiniMax-M2.7-highspeed",
            "MiniMax-M2.5",
            "MiniMax-M2.5-highspeed",
            "MiniMax-M2.1",
            "MiniMax-M2.1-highspeed",
            "MiniMax-M2",
            "MiniMax-Text-01",
        ],
    },
    "Groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.1-8b-instant",
    },
    "Together AI": {
        "base_url": "https://api.together.xyz/v1",
        "default_model": "meta-llama/Llama-3.1-8B-Instruct-Turbo",
    },
    "Mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-small-latest",
    },
    "OpenRouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "meta-llama/llama-3.1-8b-instruct:free",
    },
    "Ollama (Local)": {
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3.1",
    },
    "LM Studio (Local)": {
        "base_url": "http://localhost:1234/v1",
        "default_model": "local-model",
    },
    "Custom": {
        "base_url": "",
        "default_model": "",
    },
}

# Minimum required keys in fight_summary for a meaningful analysis.
_REQUIRED_SUMMARY_KEYS = {"outcome", "friendly_count", "enemy_count", "squad_count"}


def _strip_think_tags(content: str) -> str:
    """Remove LLM <think> tags and their content from a response.

    Handles three cases in order:
    1. Properly closed <think>...</think> blocks
    2. Unclosed <think> tag (model hit token limit mid-thought)
    3. Stray orphaned </think> tags
    """
    result = _THINK_TAG_RE.sub('', content)
    result = _THINK_UNCLOSED_RE.sub('', result)
    result = _THINK_STRAY_RE.sub('', result)
    return result.strip()


def _extract_fallback_sentences(content: str, max_sentences: int = 4) -> Optional[str]:
    """Extract the last few sentences from think-tag inner content as a fallback.

    Uses a regex split that respects capitalization boundaries rather than
    naively splitting on every period (which breaks on abbreviations,
    decimals, and URLs).
    """
    inner = _THINK_STRAY_RE.sub('', content).strip()
    if not inner:
        return None
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(inner) if s.strip()]
    if not sentences:
        return None
    tail = sentences[-max_sentences:]
    result = ' '.join(tail)
    # Ensure it ends with terminal punctuation
    if result and result[-1] not in '.!?':
        result += '.'
    return result


class FightAnalyst:
    """Sends fight summary data to any OpenAI-compatible API for analysis.

    Timeout behavior: the ``timeout`` parameter on ``analyze()`` applies
    per HTTP attempt. With up to 3 attempts (1 initial + 2 retries) and
    3-second sleeps between retries, the worst-case wall time is
    approximately ``3 * timeout + 6`` seconds.
    """

    @staticmethod
    def fetch_models(base_url: str, api_key: str = "", timeout: int = 10) -> List[str]:
        """Fetch available models from the API's /models endpoint.

        Works with any OpenAI-compatible API (OpenAI, MiniMax, Groq,
        Together, Mistral, OpenRouter, Ollama, LM Studio, etc.)

        Returns:
            List of model ID strings, sorted alphabetically. Empty list on failure.
        """
        url = f"{base_url.rstrip('/')}/models"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                models = data.get("data", [])
                model_ids = sorted([
                    m.get("id", "").removeprefix("models/")
                    for m in models if m.get("id")
                ])
                return model_ids
            else:
                return []
        except Exception:
            return []

    def __init__(self, base_url: str, api_key: str, model: str,
                 system_prompt: str = None, max_tokens: int = 1000,
                 vocab_tracker: VocabularyTracker = None,
                 vocab_config: VocabularyConfig = None,
                 vocab_weights: dict = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self._custom_prompt = system_prompt  # None means use dynamic default
        self.system_prompt = system_prompt or ""  # placeholder; built per-call when default
        self.vocab_tracker = vocab_tracker
        self.vocab_config = vocab_config
        self._weight_overrides = vocab_weights

    def analyze(self, fight_summary: Dict[str, Any], timeout: int = 30) -> Optional[str]:
        """Send fight data to the configured LLM and return analysis text.

        Args:
            fight_summary: Dict containing fight statistics. Must include at
                minimum the keys: outcome, friendly_count, enemy_count,
                squad_count. Missing keys are logged as a warning but do not
                prevent the request.
            timeout: Per-attempt HTTP timeout in seconds. See class docstring
                for worst-case wall time.

        Returns:
            Analysis text string, or None on failure.
        """
        if not self.base_url or not self.model:
            logger.warning("AI analysis skipped: no base URL or model configured")
            return None

        # Validate required keys
        missing = _REQUIRED_SUMMARY_KEYS - fight_summary.keys()
        if missing:
            logger.warning(
                "fight_summary is missing recommended keys: %s. "
                "Analysis quality may be degraded.",
                ", ".join(sorted(missing))
            )

        # Dice-roll vocabulary
        active_terms = {"shock": [], "positive": [], "negative": [], "gates": []}
        if self.vocab_config:
            overused = set()
            if self.vocab_tracker:
                overused = self.vocab_tracker.get_overused_terms()
            active_terms = self.vocab_config.roll_active_terms(overused, weight_overrides=self._weight_overrides, fight_summary=fight_summary)

        prompt = self._build_prompt(fight_summary, active_terms)
        endpoint = f"{self.base_url}/chat/completions"

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Strip "models/" prefix that some providers include in model IDs
        model_name = self.model
        if model_name.startswith("models/"):
            model_name = model_name[7:]

        system_prompt = self._build_system_prompt(fight_summary)

        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": self.max_tokens,
            "temperature": 0.7,
        }

        # Provider-specific payload adjustments
        self._apply_provider_overrides(payload)

        # Debug: dump full AI prompt + response if SPARKY_DEBUG_AI_PROMPT is set
        debug_file = self._write_debug_request(endpoint, headers, payload)

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                logger.info(
                    "Requesting AI analysis from %s using %s%s",
                    self.base_url, model_name,
                    f" (retry {attempt})" if attempt > 0 else ""
                )
                response = requests.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )

                if response.status_code == 200:
                    return self._handle_success(response.json(), model_name, debug_file)
                elif response.status_code >= 500 and attempt < max_retries:
                    logger.warning("AI API returned %d, retrying in 3s...", response.status_code)
                    time.sleep(3)
                    continue
                else:
                    logger.error("AI API error: %d - %s", response.status_code, response.text[:300])
                    return None

            except requests.Timeout:
                if attempt < max_retries:
                    logger.warning(
                        "AI API timed out after %ds, retrying in 3s... (attempt %d/%d)",
                        timeout, attempt + 1, max_retries + 1
                    )
                    time.sleep(3)
                    continue
                else:
                    logger.error("AI API timed out after %ds: all retries exhausted", timeout)
                    return None
            except requests.ConnectionError:
                if attempt < max_retries:
                    logger.warning("AI API connection failed, retrying in 3s...")
                    time.sleep(3)
                    continue
                else:
                    logger.error("AI API connection failed: is %s reachable?", self.base_url)
                    return None
            except Exception as e:
                logger.error("AI analysis failed: %s", e)
                return None

    # ------------------------------------------------------------------
    # Internal: prompt and payload construction
    # ------------------------------------------------------------------

    def _build_system_prompt(self, fight_summary: Dict[str, Any]) -> str:
        if self._custom_prompt:
            system_prompt = self._custom_prompt
        else:
            system_prompt = self._core_system_prompt() + self._rules_section()

        # Commander block
        commander = fight_summary.get('commander')
        if commander and str(commander).strip():
            system_prompt += (
                "\n\nCOMMANDER:\n"
                "The squad commander is " + commander + ". You can reference them by name "
                "when praising or criticizing the squad's performance "
                "(e.g. \"" + commander + " should be proud of that push\")."
            )

        # Tracker injection (vocab rotation + stat guidance, NOT style guidance;
        # style guidance now goes in user message via _build_prompt)
        if self.vocab_tracker:
            vocab_block = self.vocab_tracker.build_injection_block()
            if vocab_block:
                system_prompt += vocab_block

        return system_prompt


    @staticmethod
    def _pre_analyze(summary: Dict[str, Any]) -> str:
        """Compute fight analysis conclusions from raw data.

        Returns a compact text block of pre-computed conclusions that
        replaces the BOX SCORE DECODER in the system prompt. The model
        reads these conclusions instead of learning the analytical rules.
        """
        lines = ["FIGHT ANALYSIS:"]

        # --- Fight shape ---
        duration = summary.get("duration_seconds", 0)
        dur_str = summary.get("duration", "")
        if duration < 300:
            lines.append(f"Shape: Execution ({dur_str or str(duration) + 's'}). One side collapsed instantly.")
        elif duration < 900:
            lines.append(f"Shape: Standard engagement ({dur_str or str(duration) + 's'}).")
        elif duration < 1500:
            lines.append(f"Shape: Extended war of attrition ({dur_str or str(duration) + 's'}). Both sides were committed.")
        else:
            lines.append(f"Shape: Epic sustained brawl ({dur_str or str(duration) + 's'}). Rare and grueling.")

        # --- Zone ---
        zone = summary.get("zone", "")
        if zone:
            lines.append(f"Zone: {zone}.")

        # --- Numbers context ---
        friendly = summary.get("friendly_count", 0)
        enemy = summary.get("enemy_count", 0)
        squad = summary.get("squad_count", 0)
        ally = summary.get("ally_count", 0)

        if friendly > 0 and enemy > 0:
            ratio = enemy / friendly
            if ratio > 1.15:
                lines.append(f"Numbers: Outnumbered ({friendly} friendly vs {enemy} enemy, enemy has {ratio:.0%} advantage).")
            elif ratio < 0.85:
                lines.append(f"Numbers: Numbers advantage ({friendly} friendly vs {enemy} enemy).")
            else:
                lines.append(f"Numbers: Even fight ({friendly} friendly vs {enemy} enemy, within 15%).")

        # --- PUG relevance ---
        pug_pct = (ally / friendly * 100) if friendly > 0 else 0
        if pug_pct >= 20:
            lines.append(f"PUG relevance: SIGNIFICANT ({ally} PUGs = {pug_pct:.0f}% of friendly, above 20% threshold). PUGs can be blamed or mocked.")
        else:
            lines.append(f"PUG relevance: Irrelevant ({ally} PUGs = {pug_pct:.0f}% of friendly, below 20% threshold). Do NOT mention PUGs.")

        # --- Stomp discipline ---
        downs = summary.get("squad_downs", 0)
        kills = summary.get("squad_kills", 0)
        enemy_deaths = summary.get("enemy_deaths", 0)

        if downs > 0:
            if kills > downs:
                lines.append(f"Stomp discipline: EXCELLENT ({downs} downs, {kills} kills). Squad was stomping efficiently, finishing before rallies.")
            elif kills >= downs * 0.8:
                lines.append(f"Stomp discipline: Solid ({downs} downs, {kills} kills). Good conversion rate.")
            else:
                rally_pct = ((downs - kills) / downs * 100) if downs > 0 else 0
                lines.append(f"Stomp discipline: POOR ({downs} downs but only {kills} kills). Roughly {rally_pct:.0f}% of downed enemies rallied or were rezzed. This must be called out.")
                if pug_pct >= 20:
                    lines.append("PUG deaths near downed enemies likely gifted free rallies to the enemy.")

        # --- Support quality ---
        healing = summary.get("squad_healing", 0)
        barrier = summary.get("squad_barrier", 0)
        enemy_dmg = summary.get("enemy_total_damage", 0)

        if enemy_dmg > 0:
            heal_pct = healing / enemy_dmg * 100
            if heal_pct > 50:
                lines.append(f"Support quality: Exceptional (healing covered {heal_pct:.0f}% of enemy damage).")
            elif heal_pct > 25:
                lines.append(f"Support quality: Solid (healing covered {heal_pct:.0f}% of enemy damage).")
            else:
                lines.append(f"Support quality: Struggling (healing only {heal_pct:.0f}% of enemy damage). Squad was getting out-traded.")
            if barrier > 0:
                lines.append(f"Barrier mitigation: {barrier:,} total barrier from Scourge boon corruption.")

        # --- Boon denial ---
        strips = summary.get("squad_strips", 0)
        top_strips = summary.get("top_strips", [])
        if strips > 0:
            strip_classes = [s.get("profession", "") for s in top_strips[:3]]
            lines.append(f"Boon denial: {strips} total strips via {', '.join(strip_classes) if strip_classes else 'unknown'}.")

        # --- Cleanses ---
        cleanses = summary.get("squad_cleanses", 0)
        if cleanses > 0:
            lines.append(f"Condition cleansing: {cleanses} total cleanses.")

        # --- Tag discipline ---
        # squad_tag_distance: list of {"name": player_name, "distance": float} entries
        tag_data = summary.get("squad_tag_distance", [])
        if tag_data and isinstance(tag_data, list) and len(tag_data) > 1:
            distances = sorted([p.get("distance", 0) for p in tag_data], reverse=True)
            logger.debug("Tag distances (sorted): %s", distances)

            # worst_distance from full list BEFORE trimming outliers
            worst_distance = distances[0]
            worst_player = next(
                (p["name"] for p in tag_data if p.get("distance", 0) == worst_distance),
                "unknown"
            )

            # Remove top 10% as extreme outliers (dead players at spawn, etc.)
            cutoff_idx = max(1, int(len(distances) * 0.9))
            core_distances = distances[:cutoff_idx]

            # Use median for robustness against remaining outliers
            mid = len(core_distances) // 2
            if len(core_distances) % 2 == 0:
                median_distance = (core_distances[mid - 1] + core_distances[mid]) / 2
            else:
                median_distance = core_distances[mid]

            avg_distance = sum(core_distances) / len(core_distances) if core_distances else 0

            logger.debug("Core distances (after 10%% trim): %s, median=%.0f, avg=%.0f", core_distances, median_distance, avg_distance)

            # Use median for grading, avg for reporting
            grade_distance = median_distance

            outcome = summary.get("outcome", "")
            is_loss = outcome in ("Loss", "Decisive Loss", "Draw")

            if grade_distance < TAG_DISTANCE_EXCELLENT:
                lines.append(
                    f"Tag discipline: Tight (median {grade_distance:.0f} distance to tag). "
                    f"Squad was stacked well."
                )
            elif grade_distance < TAG_DISTANCE_ACCEPTABLE:
                if is_loss:
                    lines.append(
                        f"Tag discipline: Acceptable but could be tighter (median {grade_distance:.0f} distance to tag)."
                    )
                else:
                    lines.append(
                        f"Tag discipline: Acceptable (median {grade_distance:.0f} distance to tag)."
                    )
            elif grade_distance < TAG_DISTANCE_LOOSE:
                if is_loss:
                    lines.append(
                        f"Tag discipline: LOOSE (median {grade_distance:.0f} distance to tag). "
                        f"Players were drifting off the group and this likely contributed to the loss. "
                        f"Call out the squad's positioning."
                    )
                else:
                    lines.append(
                        f"Tag discipline: Loose (median {grade_distance:.0f} distance to tag). "
                        f"Won despite some drifters."
                    )
            else:
                if is_loss:
                    lines.append(
                        f"Tag discipline: SCATTERED (median {grade_distance:.0f} distance to tag). "
                        f"The squad was not stacked and this is a major reason for the loss. "
                        f"This MUST be called out. TOIGHT LIKE A TIGER is appropriate."
                    )
                else:
                    lines.append(
                        f"Tag discipline: Scattered but won anyway (median {grade_distance:.0f} distance to tag)."
                    )

        # --- Enemy composition ---
        enemy_breakdown = summary.get("enemy_breakdown", {})
        top_enemy_skills = summary.get("top_enemy_skills", [])

        if enemy_breakdown:
            comp_parts = []
            for prof, data in sorted(enemy_breakdown.items(),
                                     key=lambda x: -(x[1].get("count", 0) if isinstance(x[1], dict) else x[1])):
                if isinstance(data, dict):
                    count = data.get("count", 0)
                    dpp = data.get("damage_per_player", 0)
                    comp_parts.append(f"{count}x {prof} ({dpp:,.0f} dmg/player)")
                else:
                    comp_parts.append(f"{data}x {prof}")
            lines.append(f"Enemy comp: {', '.join(comp_parts)}.")

        if top_enemy_skills:
            skill_parts = [f"{s['name']} ({s['damage']:,})" for s in top_enemy_skills[:5]]
            lines.append(f"Top enemy skills: {', '.join(skill_parts)}.")

            siege_names = {"Arrow Cart", "Superior Arrow Cart", "Ballista", "Mortar Shot", "Trebuchet"}
            siege_skills = [s for s in top_enemy_skills if s.get("name", "") in siege_names]
            if siege_skills:
                siege_total = sum(s.get("damage", 0) for s in siege_skills)
                lines.append(f"SIEGE DETECTED: Enemy used siege weapons ({siege_total:,} total siege damage). Deeply unimpressive, mock them for hiding behind catapults.")
            else:
                lines.append("Siege: None detected.")

        # --- Enemy strategy fingerprinting ---
        if enemy_breakdown and top_enemy_skills:
            top_skill_names = [s.get("name", "") for s in top_enemy_skills[:5]]
            comp_notes = []

            # Ranged poke: Soulbeast + Barrage
            soulbeast_count = enemy_breakdown.get("Soulbeast", {}).get("count", 0)
            if soulbeast_count >= 2 and any("Barrage" in s for s in top_skill_names):
                comp_notes.append("ranged poke comp (Soulbeast + Barrage, staying at distance)")

            # Elementalist nuke: multiple ele specs + channeled AoE
            ele_specs = ["Evoker", "Catalyst", "Weaver", "Tempest"]
            ele_count = sum(enemy_breakdown.get(p, {}).get("count", 0) for p in ele_specs)
            nuke_skills = ["Meteor Shower", "Volcano", "Lava Font", "Scorched Earth"]
            if ele_count >= 3 and any(s in top_skill_names for s in nuke_skills):
                comp_notes.append("Elementalist nuke comp (channeled AoE burst from static positions)")

            # Trap burst: Dragonhunter heavy
            dh_count = enemy_breakdown.get("Dragonhunter", {}).get("count", 0)
            if dh_count >= 2 and any("Burning" in s or "Purging Flames" in s for s in top_skill_names):
                comp_notes.append("Guardian trap-burst comp (Dragonhunter spike damage)")

            # Scourge corruption: heavy Scourge presence
            scourge_count = enemy_breakdown.get("Scourge", {}).get("count", 0)
            if scourge_count >= 4:
                comp_notes.append("Scourge-heavy corruption comp (boon conversion + shade pressure)")

            # Berserker glass cannon: few Berserkers with outsized damage
            berserker_data = enemy_breakdown.get("Berserker", {})
            if berserker_data.get("count", 0) >= 2 and berserker_data.get("damage_per_player", 0) > 150000:
                comp_notes.append("glass-cannon Berserker carries (high individual burst)")

            # Herald/Firebrand boon ball
            boon_specs = ["Herald", "Firebrand", "Chronomancer"]
            boon_count = sum(enemy_breakdown.get(p, {}).get("count", 0) for p in boon_specs)
            if boon_count >= 3:
                comp_notes.append("boon-heavy support core (Herald/Firebrand/Chronomancer)")

            if comp_notes:
                lines.append(f"Enemy strategy: {'; '.join(comp_notes)}.")

        # --- Three-way fight ---
        enemy_teams = summary.get("enemy_teams", {})
        if len(enemy_teams) >= 2:
            lines.append(f"Three-way fight: {len(enemy_teams)} servers present ({', '.join(enemy_teams.keys())}). Multiple fronts, server infrastructure stressed.")

        # --- Damage comparison ---
        if summary.get("squad_outdamaged_enemy") is False:
            outcome = summary.get("outcome", "")
            if "Win" in outcome:
                lines.append("Squad was OUT-DAMAGED but still won. Victory came through boon denial and stomp efficiency, not raw DPS. Tactically sophisticated.")

        # --- Outcome and mood ---
        outcome = summary.get("outcome", "Unknown")
        is_outnumbered = summary.get("is_outnumbered", False)

        mood = ""
        if outcome == "Decisive Win":
            if friendly > 0 and enemy / friendly > 1.15:
                mood = "MAXIMUM HYPE. Decisive Win while outnumbered. Legendary."
            elif friendly > 0 and enemy / friendly < 0.75:
                mood = "Dominant but expected against a smaller force. Find the one thing that made this special and punch it up."
            else:
                mood = "Strong win against comparable numbers. Celebrate it, but find the standout detail that elevated this above routine."
        elif outcome == "Win":
            mood = "Highlight the single most important success factor. One subtle improvement note."
        elif outcome == "Draw":
            mood = "Frustrated energy. Identify the tactical breakdown."
        elif outcome == "Loss":
            mood = "Angry but constructive. Name the specific failure. Commander is never the cause."
        elif outcome == "Decisive Loss":
            mood = "Full tilt. Demand improvement from the squad. Commander is the victim, not the cause."
        else:
            mood = "Neutral analysis."

        lines.append(f"Outcome: {outcome}. Mood: {mood}")

        # --- Outliers ---
        outliers = summary.get("outliers", {})
        if outliers:
            outlier_parts = []
            for category, data in outliers.items():
                name = data.get("name", "Unknown")
                value = data.get("value", "")
                unit = data.get("unit", "")
                outlier_parts.append(f"{name} ({category}: {value} {unit})")
            lines.append(f"Outlier players (MUST be highlighted): {', '.join(outlier_parts)}.")

        return "\n".join(lines)

    def _apply_provider_overrides(self, payload: dict) -> None:
        """Apply provider-specific payload fields based on the configured base URL."""
        parsed_host = urlparse(self.base_url).hostname or ""

        # MiniMax: suppress chain-of-thought output
        if parsed_host == "api.minimaxi.chat" or parsed_host.endswith(".minimaxi.chat"):
            payload["think_enable"] = False

        # Gemini: disable thinking to preserve tokens for output
        if parsed_host == "generativelanguage.googleapis.com" or parsed_host.endswith(".googleapis.com"):
            payload["reasoning_effort"] = "none"

    def _build_prompt(self, summary: Dict[str, Any],
                      active_terms: Dict[str, list]) -> str:
        """Build the user message: pre-analysis + vocabulary + trimmed fight data."""
        parts = []

        # 1. Pre-analysis block
        parts.append(self._pre_analyze(summary))
        parts.append("")

        # 2. Vocabulary block
        parts.append(self._format_active_terms(active_terms))
        parts.append("")

        # 3. Style directive from tracker
        if self.vocab_tracker:
            style = self.vocab_tracker._build_style_guidance()
            if style:
                parts.append(style.strip())
                parts.append("")

        # 3b. Opener guidance from tracker
        if self.vocab_tracker:
            opener = self.vocab_tracker._build_opener_guidance()
            if opener:
                parts.append(opener.strip())
                parts.append("")

        # 4. Trimmed fight data
        trimmed = self._trim_summary(summary)
        parts.append("FIGHT DATA:")
        parts.append(json.dumps(trimmed, indent=2))

        return "\n".join(parts)

    @staticmethod
    def _trim_summary(summary: Dict[str, Any], top_n: int = 5) -> dict:
        """Return a copy of the fight summary with player stat lists capped to top_n entries.

        Does NOT trim enemy_breakdown (compositional data, always needed in full)
        or top_enemy_skills (needed for siege detection and comp fingerprinting).
        """
        trimmed = dict(summary)
        # Only trim player performance lists, not enemy data
        player_list_keys = [
            "top_damage", "top_strips", "top_cleanses",
            "top_healers", "top_bursts", "top_cc",
        ]
        for key in player_list_keys:
            if key in trimmed and isinstance(trimmed[key], list):
                trimmed[key] = trimmed[key][:top_n]

        # Remove squad_tag_distance: per-player distance list is not sent to the model.
        # The median grade in the pre-analysis is sufficient; names enable targeting.
        trimmed.pop("squad_tag_distance", None)

        return trimmed

    @staticmethod
    def _format_active_terms(active: Dict[str, list]) -> str:
        """Format dice-rolled terms as a compact block for the user message."""
        lines = ["AVAILABLE TERMS (use at most one per category, or invent your own):"]

        any_terms = False

        if active["shock"]:
            for t in active["shock"]:
                lines.append(f"  Shock: \"{t['term']}\" - {t['desc']} [ALL CAPS]")
                any_terms = True

        if active["positive"]:
            for t in active["positive"]:
                caps_note = " [ALL CAPS]" if t.get("caps") == "always" else ""
                alt = f" / \"{t['alt']}\"" if t.get("alt") else ""
                lines.append(f"  Hype: \"{t['term']}\"{alt} - {t['desc']}{caps_note}")
                any_terms = True

        if active["negative"]:
            for t in active["negative"]:
                caps_note = " [ALL CAPS]" if t.get("caps") == "always" else ""
                lines.append(f"  Negative: \"{t['term']}\" - {t['desc']}{caps_note}")
                any_terms = True

        if active["gates"]:
            for g in active["gates"]:
                alt = f" / \"{g['alt']}\"" if g.get("alt") else ""
                lines.append(f"  Gate: \"{g['term']}\"{alt} - IF {g['condition']}: {g['instruction']}")
                any_terms = True

        if not any_terms:
            lines.append("  No palette terms available this round. INVENT YOUR OWN with the same energy.")

        lines.append("")
        lines.append("You may always invent your own vivid phrases, insults, or exclamations instead of using palette terms.")
        lines.append("Freestyle inventions should be written in ALL CAPS when used as punchlines or exclamations.")

        return "\n".join(lines)

    def _check_overused_terms(self, response: str) -> list:
        """Check if the response contains overused vocabulary terms.

        Returns a list of overused term names found in the response.
        This is monitoring-only — re-requesting for violations would likely
        produce the same terms since the model generates them from knowledge.
        """
        if not self.vocab_tracker or not self.vocab_config:
            return []

        overused = self.vocab_tracker.get_overused_terms()
        if not overused:
            return []

        violations = []
        for name, compiled in self.vocab_config.compiled_patterns:
            if name in overused:
                for pattern in compiled:
                    if pattern.search(response):
                        violations.append(name)
                        break
        return violations

    # ------------------------------------------------------------------
    # Internal: response handling
    # ------------------------------------------------------------------

    def _handle_success(self, data: dict, model_name: str, debug_file: Optional[Path]) -> Optional[str]:
        """Process a successful API response and return the analysis text."""
        choice = data.get('choices', [{}])[0]
        finish_reason = choice.get('finish_reason')
        if finish_reason == 'length':
            logger.warning("AI response was truncated due to max_tokens limit (%d)", self.max_tokens)

        content = choice.get('message', {}).get('content', '')

        stripped = _strip_think_tags(content)

        # Write debug data regardless of stripping outcome
        if debug_file and debug_file.exists():
            self._append_debug_response(debug_file, data, model_name, content, stripped)

        if stripped:
            # Post-processing: check stat density
            stat_matches = _STAT_RE.findall(stripped)
            if len(stat_matches) > 3:
                logger.warning(
                    "AI response contains %d stat references (limit ~2): %s",
                    len(stat_matches), stat_matches[:5]
                )

            # Post-processing: check for overused vocabulary violations
            overused_violations = self._check_overused_terms(stripped)
            if overused_violations:
                logger.warning(
                    "AI response contains overused terms: %s",
                    ", ".join(overused_violations)
                )

            logger.info('AI analysis generated successfully')
            if self.vocab_tracker:
                self.vocab_tracker.record(stripped)
            return stripped

        # Fallback: everything was inside think tags; extract tail sentences
        fallback = _extract_fallback_sentences(content)
        if fallback:
            logger.info('AI analysis extracted from think-tag fallback')
            if self.vocab_tracker:
                self.vocab_tracker.record(fallback)
            return fallback

        logger.warning('AI response was empty after stripping think tags')
        return None

    # ------------------------------------------------------------------
    # Internal: debug file helpers
    # ------------------------------------------------------------------

    def _write_debug_request(self, endpoint: str, headers: dict, payload: dict) -> Optional[Path]:
        """Write the outbound request to a debug JSON file if enabled."""
        if os.environ.get("SPARKY_DEBUG_AI_PROMPT") != "1":
            return None

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        debug_file = Path.cwd() / f"ai_prompt_{timestamp}.json"
        debug_data = {
            "endpoint": endpoint,
            "headers": {k: v for k, v in headers.items() if k != "Authorization"},
            "payload": payload,
        }
        try:
            with open(debug_file, "w", encoding="utf-8") as f:
                json.dump(debug_data, f, indent=2, ensure_ascii=False)
            logger.info("[DEBUG] AI prompt saved to %s", debug_file)
        except Exception as exc:
            logger.warning("[DEBUG] Failed to write debug file: %s", exc)
            return None
        return debug_file

    @staticmethod
    def _append_debug_response(
        debug_file: Path, data: dict, model_name: str,
        raw_content: str, stripped_content: str
    ) -> None:
        """Append the API response data to an existing debug file."""
        try:
            with open(debug_file, "r", encoding="utf-8") as f:
                debug_data = json.load(f)

            usage = data.get("usage", {})
            debug_data["response"] = {
                "model": model_name,
                "finish_reason": data.get('choices', [{}])[0].get('finish_reason'),
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                },
                "raw_content": raw_content,
                "stripped_content": stripped_content if stripped_content else None,
                "content_length": len(raw_content),
            }

            with open(debug_file, "w", encoding="utf-8") as f:
                json.dump(debug_data, f, indent=2, ensure_ascii=False)
            logger.info("[DEBUG] AI response appended to %s", debug_file)
        except Exception as exc:
            logger.warning("[DEBUG] Failed to append debug response: %s", exc)

    @staticmethod
    def _core_system_prompt() -> str:
        """Return the static core of the system prompt (voice, translation layer, angles).

        Vocabulary sections are now in the user message (via _format_active_terms).
        The BOX SCORE DECODER is replaced by the pre-analysis block (_pre_analyze).
        """
        return (
            "You are SparkyBot, a Guild Wars 2 WvW fight analyst posting to Discord. You receive structured JSON fight statistics and respond with commentary in EXACTLY 2-4 sentences.\n\n"

            "VOICE: Hype, unhinged sports commentator running on four energy drinks who actually knows the WvW meta. Euphoric when the squad wins. Furious when they lose. Mock game performance and compositions, never personally attack named squad members. PUGs are fair game when their numbers are significant enough to matter, see the 20% threshold rule below.\n\n"

            "\nTHE TRANSLATION LAYER\n\n"
            "Before writing, ask what each stat proves about the fight. Lead with that conclusion. Only include the raw number when it's dramatic enough to strengthen the sentence on its own.\n\n"
            "Pure narrative: 'Hell Butterfly was the engine of the entire fight, topping damage while systematically dismantling whatever stability their supports tried to stack.'\n"
            "Stat-anchored: 'Hell Butterfly put up 560k damage while ripping apart enemy boons, a one-player wrecking crew that their supports had no answer for.'\n\n"
            "Pure narrative: 'The squad turned Eternal Battlegrounds into a one-sided execution, converting downs into deaths with ruthless stomp discipline.'\n"
            "Stat-anchored: 'A 5.7 KDR tells you everything about how that fight went, every enemy that hit the ground stayed there and the squad made sure of it.'\n\n"
            "Pure narrative: 'The support line absorbed a punishment that would have folded lesser squads, keeping the fight alive long enough for the DPS to do their work.'\n"
            "Stat-anchored: 'The supports poured out 24 million healing into a 43 million damage firestorm, and the fact that the squad was still standing at the end is a testament to how hard that backline worked.'\n\n"
            "Never reproduce JSON field names, never list multiple stats in a row, never drop a number without narrative context around it.\n\n"
            "Avoid these patterns: '[Player] was a [adjective] [noun]', 'turning/turned [X] into [Y]', and two player names joined by 'while'. One player per sentence is the default; two players may share a sentence only when joined by 'and'.\n\n"

            "The FIGHT ANALYSIS block provides pre-computed conclusions — trust the raw data if it contradicts them.\n\n"

            "\nNARRATIVE ANGLES\n\n"
            "Identify internally which single story the data tells most loudly. Commit to one angle. Do not blend them.\n\n"
            "Angle A, The Carry: Check the outliers dictionary FIRST. If players are listed there, they must be praised. If outliers is empty, look for a player dominating multiple categories simultaneously. If no clear carry exists, move to another angle — do not manufacture one.\n\n"
            "Angle B, The Enemy Failed: The enemy's composition or strategy was dismantled by the squad's tools. Use enemy_breakdown plus top_enemy_skills plus squad_strips. Best when the enemy had a readable comp and the squad's strips, cleanses, or CC directly neutralized it.\n\n"
            "Angle C, The War of Attrition: The fight was long, the numbers were brutal, and the squad refused to fold. Best for fights over 1200 seconds or very high kill counts relative to squad size.\n\n"
            "Angle D, The Execution or The Collapse: Reserved for fights under 300 seconds. One side was vaporized before establishing anything. The brevity IS the story — do not write about this fight the same way you would write a standard engagement.\n\n"
        )

    @staticmethod
    def _rules_section() -> str:
        """Return the ABSOLUTE RULES section of the system prompt."""
        return (
            "\nABSOLUTE RULES\n\n"
            "Rule 1, Sentence count: EXACTLY 2 to 4 sentences. Count them before you output. Not 5. Not 1.\n\n"
            "Rule 2, Stats with purpose: Hard limit of two number references in your entire response. Count them before you output. Obey the STAT USAGE GUIDANCE — if it says pure narrative, use zero stats. If it allows a stat, pick the single most impactful number and weave it into a narrative sentence.\n\n"
            "Rule 3, Output only the commentary: No preamble, no reasoning, no 'Here is my take.' If any sentence contains 'I', 'let me', 'should', 'draft', 'angle', or 'response' you are leaking internal reasoning — delete everything and start over. Begin your response now with the first word of the commentary.\n\n"
            "Rule 4, Enemy players are anonymous: Individual enemies are never named. Only professions from enemy_breakdown may be referenced.\n\n"
            "Rule 5, PUG commentary requires a threshold: Only mention PUGs if ally_count exceeds 20% of friendly_count.\n\n"
            "Rule 6, Opener variety: Do not open with a shock exclamation unless the RECENT VOCABULARY USAGE section confirms it has NOT been used recently. Do not default to a shock exclamation when another opener fits better.\n"
        )