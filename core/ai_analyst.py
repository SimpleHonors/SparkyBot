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
import unicodedata
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
DEFAULT_PROMPT_VERSION = 3

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
    3: {
        "title": "Word count discipline and narrative focus",
        "changes": [
            "Hard 80-word maximum added (Rule 2). Models that gamed sentence count with run-on sentences are now constrained by word budget.",
            "Stat limit reduced from 2 to 1. Zero numbers preferred, one allowed only when dramatically impactful.",
            "FOCUS section added: 'Pick the TWO most dramatic data points, ignore everything else.' Stops models from trying to address all 15+ pre-analysis conclusions.",
            "Translation layer examples rewritten to favor pure narrative over stat-anchored alternatives.",
            "Loss and Draw moods are now computed from fight data (tag discipline, stomp rate, PUG percentage) instead of generic directives. The model receives a specific story to tell.",
            "Rule 8 added: 'Your last sentence must land like a punch.'",
            "Rule 4 updated: markdown formatting (bold, italics, asterisks) explicitly banned.",
            "Raw aggregate numbers (squad_damage, squad_healing, enemy_total_damage, squad_dps) stripped from fight data JSON to reduce stat mining. Pre-analysis conclusions provide the same information narratively.",
        ],
        "reason": "Shootout testing of 30+ models across multiple fights showed consistent patterns: stat-heavy outputs scored C, verbose outputs gamed sentence count, and loss commentary was underspecified.",
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
# Siege weapon skill names (single source of truth)
# ---------------------------------------------------------------------------
# IMPORTANT: "Mortar Shot" is the Engineer Mortar Kit auto-attack, NOT a
# siege mortar. Including it here was producing false-positive "SIEGE
# DETECTED" callouts whenever an engineer ran Mortar Kit. Real siege mortar
# skills are "Heavy Shot", "Concussion Shot", "Endothermic Shell", and
# "Elixir Shell" (these are unique to the siege weapon).
SIEGE_SKILL_NAMES = frozenset({
    # Arrow Cart
    "Arrow Cart", "Superior Arrow Cart",
    "Arrow Cart Shot", "Barbed Arrow Cart Shot", "Poison Arrow Cart Shot",
    # Ballista
    "Ballista", "Superior Ballista",
    "Ballista Bolt", "Heavy Bolt", "Spread Shot",
    # Catapult (was missing entirely)
    "Catapult", "Superior Catapult",
    "Catapult Shot", "Boulder", "Heavy Boulder",
    # Trebuchet
    "Trebuchet", "Superior Trebuchet",
    "Trebuchet Shot", "Trebuchet Boulder",
    # Siege Mortar (NOT the engineer kit)
    "Heavy Shot", "Concussion Shot", "Endothermic Shell", "Elixir Shell",
    # Flame Ram
    "Flame Ram", "Superior Flame Ram", "Flame Blast",
})

# Substring fallbacks for forgiveness on naming variants. Kept narrow so we
# don't re-introduce the Mortar Shot bug. Apply with .lower() comparison.
_SIEGE_NAME_SUBSTRINGS = ("arrow cart", "catapult", "trebuchet", "ballista")


def _is_siege_skill(skill_name: str) -> bool:
    """Return True if the skill name is from a WvW siege weapon."""
    if not skill_name:
        return False
    if skill_name in SIEGE_SKILL_NAMES:
        return True
    lower = skill_name.lower()
    return any(sub in lower for sub in _SIEGE_NAME_SUBSTRINGS)

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
                {"gate": 1, "term": "Siege Humping", "pattern": "\\bsiege\\s+humping\\b", "condition": "top_enemy_skills contains real siege weapon skills (Arrow Cart, Catapult, Trebuchet, Ballista, Flame Ram, or siege Mortar skills like Heavy Shot/Concussion Shot - NOT the Engineer 'Mortar Shot' kit) AND siege damage meaningfully contributed to squad deaths", "instruction": "Mock the enemy for hiding behind catapults instead of fighting in the open. Fires regardless of outcome.", "caps": "always"},
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
                top_skills = [s.get("name", "") for s in summary.get("top_enemy_skills", [])]
                if not any(_is_siege_skill(s) for s in top_skills):
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

        def context_blocks_term(term_entry, summary):
            """Suppress terms that don't fit the fight context, regardless of category.

            Runs on every term across shock/positive/negative/gates BEFORE the
            dice roll, in addition to gate_matches (which only handles gates).

            Currently handles: hype suppression in lopsided wins where the squad
            had a significant numbers advantage. The mood string in _pre_analyze
            already tells the model to tone down, but pulling the hype palette
            terms out of AVAILABLE TERMS removes the temptation entirely so the
            model literally cannot reach for 'ABSOLUTE MONSTERS' on a 2x stomp.

            Add new context filters here as needed (e.g. session streak suppression,
            zone-specific filters).
            """
            if not summary:
                return False

            outcome = summary.get("outcome", "")
            friendly = summary.get("friendly_count", 0)
            enemy = summary.get("enemy_count", 0)
            term_name = (term_entry.get("term") or "").lower()
            alt_name = (term_entry.get("alt") or "").lower()

            if "Win" not in outcome or friendly <= 0 or enemy <= 0:
                return False

            ratio = enemy / friendly

            # Tier 1: 2x+ advantage (ratio < 0.5) — block all hype/celebration terms.
            # Pairs with the "Unimpressive blowout" mood string.
            HYPE_HEAVY = {
                "holy shit",
                "absolute monsters",
                "gigachads",
                "big damage",
                "magnificent motherfuckers",
                "ride 'em like a pony",
                "yeet yeet delete",
                "massacre",
                "slaughter",
                "battering ram",
                "relentless",
                "here to pump",
                "bags",
            }
            if ratio < 0.5 and (term_name in HYPE_HEAVY or alt_name in HYPE_HEAVY):
                return True

            # Tier 2: 1.3-2x advantage (0.5 <= ratio < 0.75) — block only the
            # "reserved for legendary/outnumbered" terms whose own descriptions
            # mark them as out of place in a comfortable advantage win.
            HYPE_RESERVED = {
                "holy shit",
                "absolute monsters",
                "gigachads",
                "magnificent motherfuckers",
                "ride 'em like a pony",
            }
            if 0.5 <= ratio < 0.75 and (term_name in HYPE_RESERVED or alt_name in HYPE_RESERVED):
                return True

            # Tier 3: modest advantage (0.75 <= ratio < 0.85) — block only the
            # heaviest hype terms that sound absurd when the squad had a
            # comfortable numbers edge. Reinforces the "comfortable win, tone
            # down" mood directive by removing vocabulary that contradicts it.
            HYPE_OUTSIZED = {
                "absolute monsters",
                "gigachads",
                "magnificent motherfuckers",
            }
            if 0.75 <= ratio < 0.85 and (term_name in HYPE_OUTSIZED or alt_name in HYPE_OUTSIZED):
                return True

            return False

        def roll(category_key, terms):
            prob = weights.get(category_key, default_weight)
            prob = max(0.0, min(1.0, float(prob)))
            result = []
            for t in terms:
                name = t["term"]
                alt = t.get("alt", "")
                if name in overused_terms or alt in overused_terms:
                    continue
                # Context filter (runs on all categories)
                if context_blocks_term(t, fight_summary):
                    continue
                # Gate condition check (gates category only)
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
        self._player_events: list = []  # list of {"name": str, "ts": float}
        # Item #4: topic/category rotation. Unlike the other arrays (one
        # event per hit), this is one entry per fight where the entry lists
        # every callout category that was pushed into the prompt for that
        # fight. Suppression logic counts across the last N fight entries,
        # not across the 2-hour sliding window (though window pruning still
        # applies to cap growth).
        self._topic_fight_events: list = []  # list of {"categories": list[str], "ts": float}
        # Freestyle phrase tracking: stores full response texts so we can
        # extract recurring n-grams the model invents outside the palette.
        # Each entry is {"text": str, "ts": float}.
        self._phrase_events: list = []
        # PUG mention tracking: count how often PUGs are mentioned so we
        # can suppress over-mentioning across a session.
        self._pug_events: list = []  # list of {"ts": float}
        # Enemy comp fingerprint tracking: stores the archetype labels from
        # _fingerprint_enemy_comp for the last N fights so we can detect
        # when the same comp repeats and suppress the narrative description.
        self._comp_fingerprint_events: list = []  # list of {"labels": list[str], "ts": float}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, response_text: str, squad_roster: list = None) -> None:
        """Scan a response and record vocabulary terms, stat density, style, and player mentions.

        squad_roster is an optional list of player name strings (from the fight summary's
        top_damage/top_strips/etc. lists). If provided, any roster name that appears in
        the response text is recorded as a player mention event for suppression rotation.
        """
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

        # Player mention tracking (only names from the known squad roster)
        if squad_roster:
            self.record_players(response_text, squad_roster, now=now)

        # Freestyle phrase tracking: store the full response text for n-gram
        # extraction. Capped to last 10 responses in _prune().
        self._phrase_events.append({"text": response_text, "ts": now})

        # PUG mention tracking
        if re.search(r'\bPUGs?\b', response_text):
            self._pug_events.append({"ts": now})

        self._prune()
        self._save()

    def record_players(self, response_text: str, squad_roster: list,
                       now: float = None) -> None:
        """Scan response for squad player names and record each as a mention event.

        Uses case-insensitive whole-phrase matching with re.escape to handle
        names containing punctuation, unicode, or multiple words. Each unique
        player is counted at most once per response, even if mentioned multiple
        times in the same message.
        """
        if not response_text or not squad_roster:
            return
        if now is None:
            now = time.time()
        seen = set()
        for name in squad_roster:
            if not name or not isinstance(name, str) or name in seen:
                continue
            # Word-boundary-safe whole-phrase match. Anchors on non-word chars
            # or string edges so "Hat" doesn't match "Hatuey".
            pattern = r'(?<!\w)' + re.escape(name) + r'(?!\w)'
            if re.search(pattern, response_text, re.IGNORECASE):
                self._player_events.append({"name": name, "ts": now})
                seen.add(name)

    def record_topics(self, categories, now: float = None) -> None:
        """Record which callout categories were pushed into the prompt this fight.

        Called INPUT-SIDE from FightAnalyst._build_prompt after callout
        filtering, so the recorded set reflects what the AI was actually
        told to mention (post-suppression), not what it was eligible for.
        One entry per fight even if the category list is empty, so the
        "last N fights" window is a true fight count and not a
        count-of-fights-that-had-callouts.
        """
        if now is None:
            now = time.time()
        cats = list(categories) if categories else []
        self._topic_fight_events.append({"categories": cats, "ts": now})
        self._prune()
        self._save()

    def record_comp_fingerprint(self, comp_notes: list, now: float = None) -> None:
        """Record the enemy comp archetype labels for this fight.

        Called from FightAnalyst._build_prompt after fingerprinting.
        comp_notes is a list of (canonical_key, display_text) tuples.
        Stores the canonical keys for stable cross-fight comparison.
        """
        if now is None:
            now = time.time()
        keys = [key for key, _ in (comp_notes or [])]
        self._comp_fingerprint_events.append({"labels": keys, "ts": now})
        self._prune()
        self._save()

    def is_comp_repeated(self, current_comp_notes: list) -> bool:
        """Return True if the current comp archetype matches the previous fight.

        current_comp_notes is a list of (canonical_key, display_text) tuples
        from _fingerprint_enemy_comp. Compares canonical keys against the
        previous fight's recorded keys. If any archetype overlaps, the comp
        is considered repeated and the FIGHT ANALYSIS block should suppress
        the narrative fingerprint description.
        """
        if not self._comp_fingerprint_events or not current_comp_notes:
            return False
        current_keys = {key for key, _ in current_comp_notes}
        if not current_keys:
            return False
        prev = self._comp_fingerprint_events[-1]
        prev_keys = set(prev.get("labels", []))
        return bool(current_keys & prev_keys)

    # Per-category suppression overrides: categories that tend to fire
    # every fight (because the enemy comp rarely changes mid-session) need
    # a lower threshold to prevent the model from describing the same comp
    # archetype in every response. Format: {category: (lookback, threshold)}.
    _TOPIC_SUPPRESSION_OVERRIDES = {
        "enemy_comp_failure": (4, 2),  # suppress after 2 of last 4 fights
    }

    def get_suppressed_topics(self, lookback_fights: int = 5,
                              threshold: int = 3) -> set:
        """Return the set of categories that appeared in >= threshold of the
        last lookback_fights recorded fight entries. These should be dropped
        from the next fight's MANDATORY CALLOUTS.

        Default: a category that fired in 3 or more of the last 5 fights is
        suppressed on the next fight. Soft suppression: the raw data stays
        visible in FIGHT ANALYSIS, only the directive drops out.

        Per-category overrides in _TOPIC_SUPPRESSION_OVERRIDES allow
        categories like enemy_comp_failure (which fires almost every fight
        against the same enemy) to be suppressed more aggressively.
        """
        if not self._topic_fight_events:
            return set()

        # Default suppression pass
        recent = self._topic_fight_events[-lookback_fights:]
        counts: Dict[str, int] = {}
        for entry in recent:
            for cat in entry.get("categories", []):
                counts[cat] = counts.get(cat, 0) + 1
        suppressed = {cat for cat, n in counts.items() if n >= threshold}

        # Per-category override pass
        for cat, (lb, thresh) in self._TOPIC_SUPPRESSION_OVERRIDES.items():
            if cat in suppressed:
                continue  # already suppressed by default rules
            override_recent = self._topic_fight_events[-lb:]
            cat_count = sum(
                1 for entry in override_recent
                if cat in entry.get("categories", [])
            )
            if cat_count >= thresh:
                suppressed.add(cat)

        return suppressed

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
        """Return a prompt block describing recent vocabulary usage for the system prompt.

        Stat and style guidance are emitted separately in the user message
        via _build_prompt to avoid duplication with Rule 3 and style directives.
        """
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

        # Stat and style guidance are emitted in the user message via
        # _build_prompt, not here. Keeping them out of the system prompt
        # avoids duplication and conflicting directives.

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

    def _build_player_suppression_guidance(self, summary: Dict[str, Any] = None) -> str:
        """Suggest which players to avoid naming based on recent mention frequency.

        Scans _player_events for the current 2-hour window. Any player named
        in 3+ responses is flagged as "cooling off" and the model is directed
        to prefer someone else.

        Commander suppression: the commander is no longer blanket-exempt.
        After 3+ mentions in the window, the commander is included in the
        suppression list with a 50% dice roll. This prevents "[Commander]'s
        crew" from becoming a structural crutch while still allowing
        commander references when the roll succeeds. The COMMANDER block in
        the system prompt still tells the model who the commander is.

        Soft suppression: if the player is in the outliers dict for this
        fight, the guidance notes they may still be credited briefly but
        should not be the main focus.

        Returns an empty string if nothing needs suppressing, so the caller
        can cheaply skip appending to the prompt.
        """
        self._prune()
        if not self._player_events:
            return ""

        # Count mentions per player in the current window
        counts: Dict[str, int] = {}
        for event in self._player_events:
            name = event.get("name", "")
            if name:
                counts[name] = counts.get(name, 0) + 1

        commander = ""
        if summary:
            commander = (summary.get("commander") or "").strip()

        # Threshold: 3+ mentions in the window
        # Commander gets a 50% dice roll to escape suppression
        suppressed = []
        for name, c in counts.items():
            if c < 3:
                continue
            if name == commander:
                if random.random() < 0.5:
                    continue  # commander escapes suppression this time
            suppressed.append((name, c))

        suppressed.sort(key=lambda x: -x[1])
        if not suppressed:
            return ""

        # Check whether any suppressed player is in the outliers dict for this
        # fight. If so, the guidance softens for that player.
        outlier_names = set()
        if summary:
            outliers = summary.get("outliers") or {}
            for entry in outliers.values():
                name = (entry or {}).get("name", "")
                if name:
                    # Outlier entries sometimes chain two names with " and "
                    for part in re.split(r'\s+and\s+', name):
                        outlier_names.add(part.strip())

        parts = ", ".join(f"{name} (x{c})" for name, c in suppressed)
        lines = [
            "\n\nRECENT PLAYER MENTIONS",
            "The following squad players have been named as the lead story in 3+ "
            "responses in the last 2 hours:",
            f"  {parts}",
            "For this response, prefer crediting a different player, or describe "
            "the action without using any player name. Aim for variety across the "
            "session: a viewer reading the last hour of commentary should not see "
            "the same handful of names over and over.",
        ]
        overlap = [name for name, _ in suppressed if name in outlier_names]
        if overlap:
            lines.append(
                "Exception: " + ", ".join(overlap) + " appears in this fight's "
                "outliers data. You may credit them briefly if the outlier is "
                "genuinely dramatic, but keep the main focus on someone else."
            )
        return "\n".join(lines)

    # Fixation verbs: action words that models latch onto regardless of
    # surrounding context. The n-gram tracker misses these because
    # "shredded every boon" and "shredded their stability" are different
    # 3-grams. This list catches the verb itself across any context.
    _FIXATION_VERBS = [
        re.compile(r'\bshredded\b', re.IGNORECASE),
        re.compile(r'\bvaporized\b', re.IGNORECASE),
        re.compile(r'\bevaporated\b', re.IGNORECASE),
        re.compile(r'\beviscerated\b', re.IGNORECASE),
        re.compile(r'\bobliterated\b', re.IGNORECASE),
        re.compile(r'\bdismantled\b', re.IGNORECASE),
        re.compile(r'\bdevoured\b', re.IGNORECASE),
        re.compile(r'\bvacuumed\b', re.IGNORECASE),
    ]

    # Stopwords: common English words excluded from word frequency tracking.
    # These appear in virtually every sentence regardless of content and
    # would always hit the frequency threshold if counted.
    _STOPWORDS = frozenset({
        # Articles, determiners
        "a", "an", "the", "this", "that", "these", "those", "my", "your",
        "his", "her", "its", "our", "their", "some", "any", "no", "every",
        "each", "all", "both", "few", "more", "most", "other", "such",
        # Pronouns
        "i", "me", "we", "us", "you", "he", "him", "she", "it", "they",
        "them", "who", "whom", "what", "which", "whose", "myself", "itself",
        # Prepositions
        "in", "on", "at", "to", "for", "of", "with", "by", "from", "up",
        "about", "into", "through", "during", "before", "after", "above",
        "below", "between", "under", "over", "against", "along", "across",
        "behind", "beyond", "near", "off", "out", "around", "down",
        # Conjunctions
        "and", "but", "or", "nor", "so", "yet", "if", "then", "than",
        "because", "although", "though", "while", "when", "where", "unless",
        "until", "since", "whether", "as",
        # Auxiliary / modal verbs
        "is", "am", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "having", "do", "does", "did",
        "will", "would", "shall", "should", "may", "might", "can", "could",
        "must", "need", "dare", "ought",
        # Common verbs that are too generic to suppress
        "get", "got", "gets", "getting", "make", "made", "makes", "making",
        "go", "goes", "went", "gone", "going", "come", "came", "comes",
        "take", "took", "takes", "taken", "give", "gave", "gives", "given",
        "keep", "kept", "keeps", "let", "say", "said", "says",
        "know", "knew", "knows", "think", "thought", "see", "saw", "seen",
        "want", "look", "looked", "use", "used", "find", "found",
        "put", "run", "ran", "set", "try", "tried", "turn", "turned",
        # Common adverbs
        "not", "just", "also", "very", "often", "still", "already", "even",
        "now", "here", "there", "always", "never", "sometimes", "too",
        "well", "back", "only", "really", "quite", "much", "how", "way",
        "again", "once", "ever",
        # Numbers and quantifiers
        "one", "two", "three", "four", "five", "first", "last",
        # Misc function words
        "like", "just", "over", "own", "same", "able", "else",
        "enough", "many", "another", "been", "being",
    })

    # Domain allowlist: WvW terms that naturally appear in every fight
    # commentary and should never be suppressed regardless of frequency.
    # These describe the game domain, not model style fixations.
    _DOMAIN_ALLOWLIST = frozenset({
        # Core fight concepts
        "squad", "enemy", "fight", "damage", "kills", "deaths", "downs",
        "boons", "boon", "strips", "cleanses", "healing", "support",
        "push", "wipe", "win", "loss", "draw", "victory", "defeat",
        # Roles and classes
        "scourge", "reaper", "firebrand", "herald", "chronomancer",
        "dragonhunter", "weaver", "tempest", "evoker", "catalyst",
        "vindicator", "berserker", "druid", "troubadour", "ritualist",
        "soulbeast", "guardian", "necro", "elementalist", "warrior",
        "mesmer", "ranger", "revenant", "engineer", "thief",
        # Game mechanics
        "stomp", "stomps", "stomped", "rally", "rallies", "rallied",
        "rez", "rezzed", "rezzes", "downed", "tag", "commander",
        "stability", "aegis", "might", "fury", "quickness",
        "conditions", "condi", "cc", "stun", "daze", "knockback",
        "barrier", "cleanse", "strip", "stripped", "corruption",
        # Map / mode
        "wvw", "borderlands", "ebg", "battlegrounds", "siege",
        "keep", "tower", "camp", "waypoint", "spawn",
        # PUG terminology
        "pug", "pugs", "allies", "friendly", "randoms",
        # Squad references
        "crew", "line", "frontline", "backline",
        # Numbers context
        "outnumbered", "numbers", "ratio", "advantage", "disadvantage",
    })

    def _build_phrase_guidance(self, summary: Dict[str, Any] = None) -> str:
        """Extract recurring n-grams, fixation verbs, and overused words.

        Three detection layers:
        1. N-gram tracker: 3-word and 4-word phrases that appear in 3+ distinct
           responses. Catches "meat grinder", "free rallies" etc.
        2. Fixation verb tracker: single action verbs from _FIXATION_VERBS that
           appear in 3+ distinct responses. Catches "shredded [every/their]
           [boons/stability]" where the verb is the fixation but surrounding
           words vary enough to dodge n-gram detection.
        3. Word frequency tracker: counts every non-stopword, non-domain word
           across recent responses. Any word appearing in 6+ of the last 8
           responses is banned. This catches model-specific fixations
           dynamically without requiring manual curation per model.

        Args:
            summary: Optional fight summary dict. When provided, player names
                and the commander name are extracted and excluded from word
                frequency tracking (their name components would otherwise
                always hit the threshold).
        """
        self._prune()
        if len(self._phrase_events) < 3:
            return ""

        # Build n-gram counts: how many distinct responses contain each phrase
        ngram_doc_counts: Dict[str, int] = {}
        # Strip punctuation for matching, keep stop words (they matter for
        # phrases like "free rallies", "meat grinder")
        punct_re = re.compile(r'[^\w\s]', re.UNICODE)

        # Fixation verb counts: how many distinct responses contain each verb
        verb_doc_counts: Dict[str, int] = {}

        # Word frequency counts: how many distinct responses contain each word
        word_doc_counts: Dict[str, int] = {}

        # Build a dynamic skip set from player names and commander so their
        # name components ("balls", "steel", "fognus") don't get banned.
        # Accented characters are normalized to ASCII (e.g. "Bálls" -> "balls")
        # because response text typically uses the unaccented form.
        player_name_words: set = set()
        def _normalize(text: str) -> str:
            """Strip accents and return lowercase ASCII."""
            nfkd = unicodedata.normalize('NFKD', text)
            return ''.join(c for c in nfkd if not unicodedata.combining(c)).lower()

        if summary:
            commander = (summary.get("commander") or "").strip()
            if commander:
                for part in punct_re.sub('', _normalize(commander)).split():
                    if len(part) >= 3:
                        player_name_words.add(part)
            roster = _extract_squad_roster(summary)
            for name in roster:
                for part in punct_re.sub('', _normalize(name)).split():
                    if len(part) >= 3:
                        player_name_words.add(part)

        for event in self._phrase_events:
            text = event.get("text", "")
            if not text:
                continue
            cleaned = punct_re.sub('', text.lower())
            words = cleaned.split()

            # Track which n-grams appear in THIS response (dedup within doc)
            seen_in_doc: set = set()
            for n in (3, 4):
                for i in range(len(words) - n + 1):
                    gram = ' '.join(words[i:i + n])
                    if gram not in seen_in_doc:
                        seen_in_doc.add(gram)
                        ngram_doc_counts[gram] = ngram_doc_counts.get(gram, 0) + 1

            # Fixation verb scan: check each verb pattern once per response
            for pattern in self._FIXATION_VERBS:
                match = pattern.search(text)
                if match:
                    verb = match.group(0).lower()
                    verb_doc_counts[verb] = verb_doc_counts.get(verb, 0) + 1

            # Word frequency scan: count unique content words per response
            seen_words: set = set()
            for w in words:
                if len(w) < 3:
                    continue  # skip very short words
                if w in self._STOPWORDS:
                    continue
                if w in self._DOMAIN_ALLOWLIST:
                    continue
                if w in player_name_words:
                    continue  # skip player name components
                if w not in seen_words:
                    seen_words.add(w)
                    word_doc_counts[w] = word_doc_counts.get(w, 0) + 1

        # Filter n-grams: appeared in 3+ distinct responses
        repeated = sorted(
            [(gram, c) for gram, c in ngram_doc_counts.items() if c >= 3],
            key=lambda x: -x[1]
        )

        # Filter fixation verbs: appeared in 3+ distinct responses
        repeated_verbs = sorted(
            [(verb, c) for verb, c in verb_doc_counts.items() if c >= 3],
            key=lambda x: -x[1]
        )

        # Filter overused words: appeared in 6+ of last 8 responses.
        # High threshold avoids false positives on domain-adjacent words
        # that didn't make it into _DOMAIN_ALLOWLIST. Only applies when
        # we have enough response history to be meaningful.
        word_threshold = 6
        min_responses_for_word_tracking = 6
        repeated_words = []
        if len(self._phrase_events) >= min_responses_for_word_tracking:
            repeated_words = sorted(
                [(w, c) for w, c in word_doc_counts.items()
                 if c >= word_threshold],
                key=lambda x: -x[1]
            )

        if not repeated and not repeated_verbs and not repeated_words:
            return ""

        # Deduplicate n-grams: if a 4-gram is banned, don't also ban its sub-3-grams
        banned: list = []
        banned_set: set = set()
        for gram, count in repeated:
            # Skip if this gram is a substring of an already-banned longer gram
            if any(gram in longer for longer in banned_set if len(longer) > len(gram)):
                continue
            banned.append(f'"{gram}" (x{count})')
            banned_set.add(gram)

        # Add fixation verbs as single-word bans
        for verb, count in repeated_verbs:
            if verb not in banned_set:
                banned.append(f'"{verb}" (x{count}, any context)')
                banned_set.add(verb)

        # Add overused words from frequency tracker
        for word, count in repeated_words:
            if word not in banned_set:
                banned.append(f'"{word}" (x{count}, overused)')
                banned_set.add(word)

        if not banned:
            return ""

        # Cap the list at 15 to avoid prompt bloat (raised from 12 to
        # accommodate the word frequency layer)
        if len(banned) > 15:
            banned = banned[:15]

        return (
            "\n\nREPEATED PHRASES — DO NOT REUSE\n"
            "The following phrases and words have appeared in many of your recent responses. "
            "They are OFF LIMITS for this response. Find different words to express "
            "the same idea, or drop the concept entirely if you cannot rephrase it.\n"
            f"  {', '.join(banned)}."
        )

    def _build_pug_guidance(self) -> str:
        """Emit PUG-saturation suppression when PUGs have been over-mentioned.

        If PUGs were mentioned in 3+ of the last 5 responses, tell the model
        to skip PUG commentary entirely regardless of the data threshold.
        """
        self._prune()
        if len(self._pug_events) < 3:
            return ""

        # Count PUG mentions in the last 5 responses (by timestamp proximity
        # to phrase events). Simple approach: just count total pug_events.
        recent_pug_count = len(self._pug_events)
        if recent_pug_count >= 3:
            return (
                "\n\nPUG SATURATION\n"
                f"PUGs have been mentioned in {recent_pug_count} of your recent "
                "responses. Do NOT mention PUGs in this response regardless of "
                "their percentage. Find a different angle. If PUGs are genuinely "
                "the only story, reference 'non-squad players' or 'randoms' "
                "instead of 'PUGs'."
            )
        return ""

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
        self._player_events = [e for e in self._player_events if e["ts"] >= cutoff]
        self._topic_fight_events = [e for e in self._topic_fight_events if e["ts"] >= cutoff]
        # Phrase events: prune by window AND cap to last 10 responses
        self._phrase_events = [e for e in self._phrase_events if e["ts"] >= cutoff]
        if len(self._phrase_events) > 10:
            self._phrase_events = self._phrase_events[-10:]
        self._pug_events = [e for e in self._pug_events if e["ts"] >= cutoff]
        self._comp_fingerprint_events = [e for e in self._comp_fingerprint_events if e["ts"] >= cutoff]

    def _load(self) -> None:
        """Load persisted events from disk, ignoring errors.

        Backward-compatible: old stores without newer event types are
        handled gracefully (missing histories start empty).
        """
        try:
            if self.store_path.exists():
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                self._events = data.get("events", [])
                self._stat_events = data.get("stat_events", [])
                self._style_events = data.get("style_events", [])
                self._opener_events = data.get("opener_events", [])
                self._player_events = data.get("player_events", [])
                self._topic_fight_events = data.get("topic_fight_events", [])
                self._phrase_events = data.get("phrase_events", [])
                self._pug_events = data.get("pug_events", [])
                self._comp_fingerprint_events = data.get("comp_fingerprint_events", [])
                self._prune()
        except Exception as exc:
            logger.warning("VocabularyTracker: could not load store: %s", exc)
            self._events = []
            self._stat_events = []
            self._style_events = []
            self._opener_events = []
            self._player_events = []
            self._topic_fight_events = []
            self._phrase_events = []
            self._pug_events = []
            self._comp_fingerprint_events = []

    def _save(self) -> None:
        """Persist current events to disk, ignoring errors."""
        try:
            self.store_path.write_text(
                json.dumps({
                    "events": self._events,
                    "stat_events": self._stat_events,
                    "style_events": self._style_events,
                    "opener_events": self._opener_events,
                    "player_events": self._player_events,
                    "topic_fight_events": self._topic_fight_events,
                    "phrase_events": self._phrase_events,
                    "pug_events": self._pug_events,
                    "comp_fingerprint_events": self._comp_fingerprint_events,
                }, indent=2),
                encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("VocabularyTracker: could not save store: %s", exc)


class SessionHistoryTracker:
    """Item #5: tracks recent fight outcomes for streak-aware mood escalation.

    Separate from VocabularyTracker because the lifecycle is different: vocab
    usage is a rolling 2-hour window keyed off palette rotation, while session
    history is an ordered list of the most recent fights keyed off streak
    detection and mood progression. Separating the persistence files keeps
    both stores small and makes the vocab store easier to reason about.

    The tracker records one entry per fight (outcome + fight_shape), capped
    at a small maximum. get_streak() walks the list from newest to oldest and
    returns the current streak type, length, and the shape mix, which the
    caller uses to adjust the mood directive in the user prompt.

    Input-side write pattern (like item #4): the caller records the outcome
    of the current fight AFTER building its prompt, so the streak "at fight N"
    reflects fights 1..N-1, and fight N's own result becomes state for N+1.
    Recording happens in FightAnalyst.analyze() regardless of whether the API
    call succeeded, so streak continuity is preserved across AI failures.
    """

    MAX_ENTRIES = 50

    # Streak length thresholds (win streaks). Match the progression described
    # in the item #5 handoff: 2-3 ease off, 4-5 roll hard, 6+ bored.
    WIN_STREAK_EASE = 2
    WIN_STREAK_ROLL = 4
    WIN_STREAK_BORED = 6

    # Loss streak thresholds: 2-3 pattern, 4+ full tilt.
    LOSS_STREAK_PATTERN = 2
    LOSS_STREAK_TILT = 4

    # Session gap: if more than this many hours elapse between two consecutive
    # entries, they belong to different play sessions and the streak resets.
    # Prevents a 12-fight win streak on Tuesday from carrying over to Friday.
    SESSION_GAP_HOURS = 4

    WIN_OUTCOMES = {"Win", "Decisive Win"}
    LOSS_OUTCOMES = {"Loss", "Decisive Loss"}

    def __init__(self, store_path: Path = None):
        self.store_path = store_path or Path.cwd() / "sparkybot_session_history.json"
        self._entries: list = []  # list of {"outcome": str, "fight_shape": str, "ts": float}
        self._load()

    def record(self, outcome: str, fight_shape: str = "unknown",
               now: float = None) -> None:
        """Append a fight entry, cap the list, persist to disk."""
        if now is None:
            now = time.time()
        self._entries.append({
            "outcome": outcome or "Unknown",
            "fight_shape": fight_shape or "unknown",
            "ts": now,
        })
        if len(self._entries) > self.MAX_ENTRIES:
            self._entries = self._entries[-self.MAX_ENTRIES:]
        self._save()

    def get_streak(self) -> Dict[str, Any]:
        """Walk the history from newest to oldest and return the current streak.

        Respects session boundaries: if more than SESSION_GAP_HOURS elapses
        between two consecutive entries, the older entry belongs to a previous
        session and the streak stops there. This prevents a 12-fight win
        streak on Tuesday from leaking into Friday's commentary.

        Returns a dict with keys:
          - ``type``: "win", "loss", "fresh", or "none"
            * "win"  = most recent fight is in WIN_OUTCOMES and the streak
                       extends back at least 1 fight
            * "loss" = most recent fight is in LOSS_OUTCOMES
            * "fresh" = only one fight in history, or the most recent outcome
                        is Draw/Unknown (breaks both streak types)
            * "none" = empty history
          - ``length``: integer streak length (1 = only the most recent fight)
          - ``shapes``: list of fight_shape strings for the fights in the
            current streak, newest to oldest. Allows the caller to notice
            when the streak is all blowouts vs all comparable fights.
        """
        if not self._entries:
            return {"type": "none", "length": 0, "shapes": []}

        newest = self._entries[-1]
        newest_outcome = newest.get("outcome", "")

        if newest_outcome in self.WIN_OUTCOMES:
            streak_set = self.WIN_OUTCOMES
            streak_type = "win"
        elif newest_outcome in self.LOSS_OUTCOMES:
            streak_set = self.LOSS_OUTCOMES
            streak_type = "loss"
        else:
            return {"type": "fresh", "length": 1,
                    "shapes": [newest.get("fight_shape", "unknown")]}

        gap_seconds = self.SESSION_GAP_HOURS * 3600
        length = 0
        shapes: list = []
        prev_ts = None
        for entry in reversed(self._entries):
            ts = entry.get("ts", 0)
            # Session boundary: large gap between consecutive entries
            if prev_ts is not None and (prev_ts - ts) > gap_seconds:
                break
            if entry.get("outcome", "") in streak_set:
                length += 1
                shapes.append(entry.get("fight_shape", "unknown"))
                prev_ts = ts
            else:
                break

        return {"type": streak_type, "length": length, "shapes": shapes}

    def build_streak_context(self, streak: Dict[str, Any],
                             current_outcome: str = None) -> Dict[str, str]:
        """Convert a get_streak() result into prompt-injection strings.

        Returns a dict with two keys:
          - ``session_context``: a single-line observation for the FIGHT
            ANALYSIS block, or empty string when no context applies.
          - ``mood_suffix``: a sentence appended to the mood directive, or
            empty string. Does NOT replace the base mood; layers on top of
            the outcome-based mood built by _pre_analyze.

        Short streaks (length < 2) produce empty strings so the prompt stays
        clean for fresh sessions or single-result histories.

        ``current_outcome`` (bug fix after item #5 live review): the outcome
        of the fight being analyzed right now. Needed because get_streak()
        reflects fights 1..N-1, so a streak of length 4 going into fight N
        does not automatically mean fight N continues the streak. Live data
        showed fight 6 of a session applying a "rolling hard, mock the enemy"
        suffix to a loss, producing a self-contradictory directive. When the
        current outcome breaks the streak, this method now emits a
        streak-just-ended context line and a muted mood suffix instead of
        the continuing-streak tier language.
        """
        stype = streak.get("type", "none")
        length = streak.get("length", 0)
        shapes = streak.get("shapes", [])

        if stype in ("none", "fresh") or length < 2:
            return {"session_context": "", "mood_suffix": ""}

        # Determine whether the current fight continues or breaks the streak.
        # If no current_outcome was provided (backward compat with callers
        # that have not been updated), assume continuation so the existing
        # behavior is preserved.
        streak_continues = True
        if current_outcome is not None:
            if stype == "win" and current_outcome not in self.WIN_OUTCOMES:
                streak_continues = False
            elif stype == "loss" and current_outcome not in self.LOSS_OUTCOMES:
                streak_continues = False

        if not streak_continues:
            # Streak-break case. Emit a different context line that says the
            # streak is ending on this fight, plus a muted mood suffix that
            # acknowledges the shift without amplifying the base mood. The
            # base mood (angry for a loss, euphoric for a win) already
            # carries the emotional content; the suffix adds session arc.
            if stype == "win":
                context = (
                    f"Session context: this fight ends a {length}-fight "
                    f"win streak."
                )
                mood = (
                    f" Session note: the {length}-fight win streak just "
                    f"ended. Disappointed, not full-tilt rageful. The squad "
                    f"was rolling and this loss is a shift in energy, not "
                    f"evidence of a broken team. Note what changed."
                )
            else:
                context = (
                    f"Session context: this fight ends a {length}-fight "
                    f"loss streak."
                )
                mood = (
                    f" Session note: the {length}-fight loss streak just "
                    f"ended. Relief more than euphoria. Do not oversell the "
                    f"recovery; note that the squad finally broke the pattern."
                )
            return {"session_context": context, "mood_suffix": mood}

        # Shape flavor for the session context line. If every fight in the
        # streak was a blowout, the reader should know the streak is not as
        # impressive as its length suggests. Conversely, a comparable-or-
        # outnumbered streak is genuinely notable.
        shape_flavor = ""
        if shapes and all(s == "blowout" for s in shapes):
            shape_flavor = " (all blowouts, unimpressive)"
        elif shapes and all(s in ("legendary_outnumbered", "comparable")
                            for s in shapes):
            shape_flavor = " (all comparable or outnumbered, real quality)"

        if stype == "win":
            context = f"Session context: on a {length}-fight win streak{shape_flavor}."
            if length >= self.WIN_STREAK_BORED:
                mood = random.choice([
                    (
                        " Session note: the squad has been winning for a while "
                        "now and it is getting boring. Do not celebrate this one "
                        "at all. Mock the enemy's persistence in showing up to "
                        "feed. Yawning energy."
                    ),
                    (
                        " Session note: another win on the pile. Express genuine "
                        "pity for the enemy at this point, they have been getting "
                        "farmed all night. Frame this fight as forgettable, just "
                        "another entry in the ledger. Do not hype."
                    ),
                    (
                        " Session note: this win streak is so long it is barely "
                        "worth commenting on. Find the one thing the squad did "
                        "BADLY despite the easy night and roast that instead of "
                        "praising the win. Constructive contempt."
                    ),
                ])
            elif length >= self.WIN_STREAK_ROLL:
                mood = (
                    " Session note: the squad is rolling hard. Do not "
                    "celebrate the result itself, mock the enemy for walking "
                    "into yet another loss."
                )
            elif length >= self.WIN_STREAK_EASE:
                mood = (
                    " Session note: the squad is on a win streak. Ease off "
                    "the euphoria slightly, this is not the first win of "
                    "the night."
                )
            else:
                mood = ""
            return {"session_context": context, "mood_suffix": mood}

        if stype == "loss":
            context = f"Session context: on a {length}-fight loss streak{shape_flavor}."
            if length >= self.LOSS_STREAK_TILT:
                mood = (
                    " Session note: this is a sustained loss streak. Full "
                    "tilt. Something is structurally wrong tonight, the "
                    "squad should hear it."
                )
            elif length >= self.LOSS_STREAK_PATTERN:
                mood = (
                    " Session note: this is becoming a pattern. The anger "
                    "should feel less like a surprise and more like a "
                    "repeated warning."
                )
            else:
                mood = ""
            return {"session_context": context, "mood_suffix": mood}

        return {"session_context": "", "mood_suffix": ""}

    def _load(self) -> None:
        try:
            if self.store_path.exists():
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                self._entries = data.get("entries", [])
                if len(self._entries) > self.MAX_ENTRIES:
                    self._entries = self._entries[-self.MAX_ENTRIES:]
        except Exception as exc:
            logger.warning("SessionHistoryTracker: could not load store: %s", exc)
            self._entries = []

    def _save(self) -> None:
        try:
            self.store_path.write_text(
                json.dumps({"entries": self._entries}, indent=2),
                encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("SessionHistoryTracker: could not save store: %s", exc)


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


def _extract_squad_roster(summary: Dict[str, Any]) -> list:
    """Collect all unique squad player names from a fight summary.

    Pulls from every top_* performance list (damage, strips, cleanses, healers,
    cc, bursts). Used by VocabularyTracker.record() to know which names to
    scan for in the response text for mention rotation.

    Returns a list of name strings, deduplicated, order-insensitive.
    """
    if not summary:
        return []
    roster = set()
    for key in ("top_damage", "top_strips", "top_cleanses",
                "top_healers", "top_cc", "top_bursts"):
        entries = summary.get(key) or []
        for entry in entries:
            if isinstance(entry, dict):
                name = entry.get("name")
                if name and isinstance(name, str):
                    roster.add(name.strip())
    return [n for n in roster if n]


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
                 system_prompt: str = None, max_tokens: int = 8000,
                 vocab_tracker: VocabularyTracker = None,
                 vocab_config: VocabularyConfig = None,
                 vocab_weights: dict = None,
                 session_history: "SessionHistoryTracker" = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self._custom_prompt = system_prompt  # None means use dynamic default
        self.system_prompt = system_prompt or ""  # placeholder; built per-call when default
        self.vocab_tracker = vocab_tracker
        self.vocab_config = vocab_config
        self._weight_overrides = vocab_weights
        self.session_history = session_history

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
        overused = set()
        if self.vocab_config:
            if self.vocab_tracker:
                overused = self.vocab_tracker.get_overused_terms()
            active_terms = self.vocab_config.roll_active_terms(overused, weight_overrides=self._weight_overrides, fight_summary=fight_summary)

        # Item #5: read streak state BEFORE recording the current fight, so
        # the streak reflects fights 1..N-1 and the current fight becomes
        # state for N+1. Recording happens unconditionally (even on API
        # failure) so streak continuity survives network hiccups.
        streak_info = None
        if self.session_history:
            streak_info = self.session_history.get_streak()
            self.session_history.record(
                outcome=fight_summary.get("outcome", "Unknown"),
                fight_shape=fight_summary.get("fight_shape", "unknown"),
            )

        prompt = self._build_prompt(fight_summary, active_terms,
                                    overused_terms=overused,
                                    streak_info=streak_info)
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
                    return self._handle_success(response.json(), model_name, debug_file, fight_summary)
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

        # Tracker injection (vocab rotation only; stat and style guidance
        # are in the user message via _build_prompt to avoid duplication)
        if self.vocab_tracker:
            vocab_block = self.vocab_tracker.build_injection_block()
            if vocab_block:
                system_prompt += vocab_block

        return system_prompt


    @staticmethod
    def _fingerprint_enemy_comp(enemy_breakdown: dict,
                                top_enemy_skills: list) -> list:
        """Identify named enemy composition archetypes from breakdown + skills.

        Returns a list of (canonical_key, display_text) tuples. The canonical
        key is a stable identifier for the archetype (e.g. "ele_nuke") used
        for cross-fight repetition detection. The display text is a
        random.choice human-readable description for the prompt. Called once
        per fight; the result is used in both FIGHT ANALYSIS and MANDATORY
        CALLOUTS to avoid running the logic (and random.choice) twice.
        """
        if not enemy_breakdown or not top_enemy_skills:
            return []

        top_skill_names = [s.get("name", "") for s in top_enemy_skills[:5]]
        comp_notes = []

        # Ranged poke: Soulbeast + Barrage
        soulbeast_count = enemy_breakdown.get("Soulbeast", {}).get("count", 0)
        if soulbeast_count >= 2 and any("Barrage" in s for s in top_skill_names):
            comp_notes.append((
                "ranged_poke",
                "ranged poke comp (Soulbeast + Barrage, staying at distance)",
            ))

        # Elementalist nuke: multiple ele specs + channeled AoE
        ele_specs = ["Evoker", "Catalyst", "Weaver", "Tempest"]
        ele_count = sum(enemy_breakdown.get(p, {}).get("count", 0) for p in ele_specs)
        nuke_skills = ["Meteor Shower", "Volcano", "Lava Font", "Scorched Earth"]
        if ele_count >= 3 and any(s in top_skill_names for s in nuke_skills):
            comp_notes.append((
                "ele_nuke",
                random.choice([
                    "Elementalist nuke comp (channeled AoE burst from static positions)",
                    "channeled Ele bombing setup (standing still to dump AoE)",
                    "static meteor-and-lava comp (Elementalists channeling from fixed spots)",
                ]),
            ))

        # Trap burst: Dragonhunter heavy
        dh_count = enemy_breakdown.get("Dragonhunter", {}).get("count", 0)
        if dh_count >= 2 and any("Burning" in s or "Purging Flames" in s for s in top_skill_names):
            comp_notes.append((
                "dh_trap",
                random.choice([
                    "Guardian trap-burst comp (Dragonhunter spike damage)",
                    "trap-spike Guardian core (Dragonhunter burning ground)",
                    "DH burning-field setup (Guardian trap damage stacking)",
                ]),
            ))

        # Scourge corruption: heavy Scourge presence
        scourge_count = enemy_breakdown.get("Scourge", {}).get("count", 0)
        if scourge_count >= 4:
            comp_notes.append((
                "scourge_corruption",
                random.choice([
                    "Scourge-heavy corruption comp (boon conversion + shade pressure)",
                    "shade-pressure Necro stack (Scourge boon corruption wall)",
                    "corruption-heavy Scourge core (boon-rip and shade AoE)",
                ]),
            ))

        # Berserker glass cannon: few Berserkers with outsized damage
        berserker_data = enemy_breakdown.get("Berserker", {})
        if berserker_data.get("count", 0) >= 2 and berserker_data.get("damage_per_player", 0) > 150000:
            comp_notes.append((
                "berserker_glass",
                "glass-cannon Berserker carries (high individual burst)",
            ))

        # Herald/Firebrand boon ball
        boon_specs = ["Herald", "Firebrand", "Chronomancer"]
        boon_count = sum(enemy_breakdown.get(p, {}).get("count", 0) for p in boon_specs)
        if boon_count >= 3:
            comp_notes.append((
                "boon_ball",
                random.choice([
                    "boon-heavy support core (Herald/Firebrand/Chronomancer)",
                    "stability-and-aegis bunker line (Herald/FB/Chrono anchor)",
                    "boon-stacking support shell (Herald/Firebrand/Chrono sustain)",
                ]),
            ))

        return comp_notes

    @staticmethod
    def _pre_analyze(summary: Dict[str, Any], overused_terms: set = None,
                     streak_context: Dict[str, str] = None,
                     comp_repeated: bool = False) -> Dict[str, Any]:
        """Compute fight analysis conclusions from raw data.

        streak_context (item #5): optional dict with keys ``session_context``
        and ``mood_suffix``, pre-computed by the caller via
        SessionHistoryTracker.build_streak_context(). When provided, the
        session_context line is inserted into the analysis text after the
        Numbers block, and the mood_suffix is appended to the mood directive.
        _pre_analyze stays a staticmethod; the tracker lookup happens in
        _build_prompt which has access to self.session_history.

        comp_repeated: when True, the enemy comp archetype matches the
        previous fight. The narrative fingerprint description is replaced
        with a directive to find a different angle, while the raw
        enemy_breakdown and top_enemy_skills data stays in FIGHT DATA
        for the model to reference if needed.

        Returns a dict with three keys:
          - ``analysis``: text block of neutral pre-computed observations
            (shape, zone, numbers, PUG relevance, stomp, support, boon
            denial, tag, enemy comp/skills/strategy, three-way,
            damage-comp, outcome, outliers). This replaces the BOX SCORE
            DECODER in the system prompt.
          - ``mood``: the mood directive string, emitted separately in the
            user prompt under a TONE: header so it cannot get visually
            entangled with criticism callouts (item #4).
          - ``callouts``: list of ``{"category": str, "text": str}`` dicts
            representing criticism or mockery items that should appear in
            the response. The caller filters this against the tracker's
            suppressed topics and emits the survivors as a numbered
            MANDATORY CALLOUTS list (item #4).
        """
        if overused_terms is None:
            overused_terms = set()

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

        # --- Session context (item #5) ---
        # Inserted right after Numbers because the streak is a session-level
        # observation about this fight's position in the arc of results.
        # Empty string when streak length is < 2 or tracker is absent.
        if streak_context and streak_context.get("session_context"):
            lines.append(streak_context["session_context"])

        # --- PUG relevance ---
        pug_pct = (ally / friendly * 100) if friendly > 0 else 0
        if pug_pct >= 20:
            lines.append(f"PUG relevance: Present ({ally} PUGs = {pug_pct:.0f}% of friendly, above 20% threshold). PUGs may be mentioned if their behavior shaped the outcome.")
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

        # --- Fight dynamics (item #7) ---
        # Four observation lines covering respawn traffic and rez/rally
        # chains on both sides of the fight. Each uses a count-vs-unique
        # comparison that is unambiguous when it triggers: any excess
        # beyond the unique-entity count must come from repeat events.
        # These are dynamics observations, not criticisms, so they go in
        # the neutral analysis block rather than MANDATORY CALLOUTS.
        squad_deaths = summary.get("squad_deaths", 0)
        squad_downs_received = summary.get("squad_downs_received", 0)

        # Enemy respawn traffic: squad kill events exceeded unique enemies
        # present, meaning enemies died, respawned, and returned. Implies
        # either a short waypoint or a fight long enough for the runback
        # to matter.
        if enemy > 0 and kills > enemy:
            excess_kills = kills - enemy
            lines.append(
                f"Respawn traffic (enemy): squad recorded {kills} kill events "
                f"against {enemy} unique enemies, meaning at least {excess_kills} "
                f"enemies died, respawned, and returned to die again. Short "
                f"waypoint distance or a long enough fight to matter."
            )

        # Enemy rez/rally chain: more downs dealt than unique enemies present,
        # but kills did not similarly exceed unique count. Excess downs must
        # come from rallies or combat rezzes rather than respawners.
        if enemy > 0 and downs > enemy and kills <= enemy:
            excess_downs = downs - enemy
            lines.append(
                f"Rez/rally chain (enemy): squad dropped enemies {downs} times "
                f"against only {enemy} unique targets. At least {excess_downs} "
                f"of those downs became rallies or combat rezzes, not kills. "
                f"The enemy's support line was working."
            )

        # Squad runbacks: squad death events exceeded unique squad members,
        # so at least one squad member died, respawned, and ran back. Same
        # interpretation as the enemy respawn case, applied to our side.
        if squad > 0 and squad_deaths > squad:
            excess_deaths = squad_deaths - squad
            lines.append(
                f"Runbacks (squad): {squad_deaths} death events across "
                f"{squad} unique squad members, meaning at least {excess_deaths} "
                f"squad members died and ran back. This fight was near our "
                f"waypoint or long enough for it to matter."
            )

        # Squad resilience / rez chain: squad went down more times than they
        # actually died. Either the support line was rezzing well, or the
        # enemy could not convert downs to kills. Can be either; the AI can
        # frame it whichever way fits the other observations in the fight.
        # Threshold tune after live review: require a saved delta of at
        # least 5 AND an absolute downs_received floor of 10, so small
        # fights with a 1-or-2-down advantage don't trigger noise. Without
        # the floor this observation was firing on 59% of fights, many of
        # which were trivial differentials.
        saved = squad_downs_received - squad_deaths
        if squad_downs_received >= 10 and saved >= 5:
            lines.append(
                f"Squad resilience: squad was downed {squad_downs_received} "
                f"times but only died {squad_deaths} times. At least {saved} "
                f"downs were saved by rezzes, rallies, or the enemy failing to "
                f"finish. Credit the support line or mock the enemy's stomps."
            )

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
                    tag_msg = (
                        f"Tag discipline: SCATTERED (median {grade_distance:.0f} distance to tag). "
                        f"The squad was not stacked and this is a major reason for the loss. "
                        f"This MUST be called out."
                    )
                    if "TOIGHT LIKE A TIGER" not in overused_terms:
                        tag_msg += " TOIGHT LIKE A TIGER is appropriate."
                    lines.append(tag_msg)
                else:
                    lines.append(
                        f"Tag discipline: Scattered but won anyway (median {grade_distance:.0f} distance to tag)."
                    )

        # --- Enemy composition ---
        # Only include enemy comp details and skill names on losses/draws.
        # On wins, these lines were causing the model to lead with enemy
        # comp descriptions instead of squad performance. The raw data
        # is also stripped from FIGHT DATA JSON on wins via _trim_summary.
        enemy_breakdown = summary.get("enemy_breakdown", {})
        top_enemy_skills = summary.get("top_enemy_skills", [])
        is_loss_for_comp = summary.get("outcome", "") in ("Loss", "Decisive Loss", "Draw")

        if enemy_breakdown and is_loss_for_comp:
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

        if top_enemy_skills and is_loss_for_comp:
            skill_parts = [f"{s['name']} ({s['damage']:,})" for s in top_enemy_skills[:5]]
            lines.append(f"Top enemy skills: {', '.join(skill_parts)}.")

            siege_skills = [s for s in top_enemy_skills if _is_siege_skill(s.get("name", ""))]
            if siege_skills:
                siege_total = sum(s.get("damage", 0) for s in siege_skills)
                lines.append(f"SIEGE DETECTED: Enemy used siege weapons ({siege_total:,} total siege damage). Deeply unimpressive, mock them for hiding behind catapults.")
            else:
                lines.append("Siege: None detected.")
        elif top_enemy_skills and not is_loss_for_comp:
            # On wins, still check for siege (it's mockery-worthy regardless)
            siege_skills = [s for s in top_enemy_skills if _is_siege_skill(s.get("name", ""))]
            if siege_skills:
                siege_total = sum(s.get("damage", 0) for s in siege_skills)
                lines.append(f"SIEGE DETECTED: Enemy used siege weapons ({siege_total:,} total siege damage). Deeply unimpressive, mock them for hiding behind catapults.")

        # --- Enemy strategy fingerprinting ---
        # Only emit the narrative fingerprint on losses/draws. On wins the
        # model should focus on what the squad did, not rehash the enemy
        # comp every single fight.
        comp_notes = FightAnalyst._fingerprint_enemy_comp(enemy_breakdown, top_enemy_skills)
        if comp_notes and is_loss_for_comp:
            if comp_repeated:
                lines.append(
                    "Enemy strategy: Same archetype as recent fights. "
                    "Do NOT describe the enemy comp or how they positioned. "
                    "Find a different story in the data (a player carry, "
                    "support performance, stomp discipline, numbers context, "
                    "or fight duration)."
                )
            else:
                display_notes = [display for _, display in comp_notes]
                lines.append(f"Enemy strategy: {'; '.join(display_notes)}.")

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
            elif friendly > 0 and enemy / friendly < 0.5:
                # 2x+ numbers advantage: dry, dismissive, anti-hype.
                # The squad outnumbered a havoc party. There is no story here.
                mood = (
                    "Unimpressive blowout. The squad outnumbered them roughly 2-to-1 or worse, "
                    "this was never a contest. Do NOT hype this fight. No 'massacre', 'slaughter', "
                    "'annihilation', 'legendary', 'speedrun', 'execution', 'evisceration', or "
                    "'obliteration' language. The enemy was a havoc party that got bullied by a "
                    "full squad, that is not a story. Valid angles: (a) mock the enemy for showing "
                    "up at all, (b) call out anything the squad did POORLY despite the easy "
                    "numbers, (c) note one player who made the easy fight look effortless. Tone is "
                    "dry and dismissive, not euphoric."
                )
            elif friendly > 0 and enemy / friendly < 0.75:
                # 1.3-2x advantage: comfortable win, tone down but don't suppress
                mood = (
                    "Comfortable win with a numbers edge. Tone down the hype. Lead with what the "
                    "squad did well tactically, not how dominant the result looked. Avoid "
                    "'legendary', 'massacre', 'annihilation', or 'speedrun' language. Save those "
                    "for outnumbered wins."
                )
            else:
                mood = "Strong win against comparable numbers. Celebrate it, but find the standout detail that elevated this above routine."
        elif outcome == "Win":
            mood = "Highlight the single most important success factor. One subtle improvement note."
        elif outcome == "Draw":
            # Identify the specific tactical breakdown for the model
            if tag_data and isinstance(tag_data, list) and len(tag_data) > 1:
                distances = [p.get("distance", 0) for p in tag_data]
                median_dist = sorted(distances)[len(distances)//2]
                if median_dist > TAG_DISTANCE_LOOSE:
                    mood = "Frustrated. The squad was scattered and that's why this was a draw instead of a win. Roast the positioning."
                else:
                    mood = "Frustrated. The squad held position but couldn't convert. Find the one thing that prevented the win."
            else:
                mood = "Frustrated energy. Identify the single tactical breakdown that cost the squad the win."
        elif outcome == "Loss":
            # Build a specific failure narrative based on the data
            if is_outnumbered:
                # Outnumbered loss: don't blame PUGs or demand discipline fixes
                # for a fight the numbers were always against. Acknowledge the
                # matchup and find the silver lining.
                loss_reasons = []
                if tag_data and isinstance(tag_data, list) and len(tag_data) > 1:
                    distances = [p.get("distance", 0) for p in tag_data]
                    median_dist = sorted(distances)[len(distances)//2]
                    if median_dist > TAG_DISTANCE_LOOSE:
                        loss_reasons.append("scattered positioning made the numbers disadvantage worse")
                if downs > 0 and kills < downs * 0.75:
                    loss_reasons.append("stomp conversion was poor despite the uphill fight")
                if loss_reasons:
                    mood = (
                        f"Angry but aware of the numbers. The story is: {'; '.join(loss_reasons)}. "
                        "The squad was outnumbered from the start. Do NOT blame PUGs for this loss. "
                        "Acknowledge the matchup was unfavorable, credit anything the squad did well "
                        "despite it, then demand tighter play for the next attempt."
                    )
                else:
                    mood = (
                        "Angry but grounded. The squad got outnumbered and lost. That happens. "
                        "Do NOT blame PUGs. Credit any bright spots (a clutch support line, a carry "
                        "performance, good strips). The frustration should target the enemy's numbers "
                        "advantage or the engagement decision, not the squad's effort."
                    )
            else:
                loss_reasons = []
                if tag_data and isinstance(tag_data, list) and len(tag_data) > 1:
                    distances = [p.get("distance", 0) for p in tag_data]
                    median_dist = sorted(distances)[len(distances)//2]
                    if median_dist > TAG_DISTANCE_LOOSE:
                        loss_reasons.append("scattered positioning killed the squad")
                if downs > 0 and kills < downs * 0.75:
                    loss_reasons.append("downed enemies rallied because the squad didn't stomp")
                if pug_pct >= 30:
                    loss_reasons.append("PUGs contributed to the breakdown")
                if loss_reasons:
                    mood = f"Angry. The story is: {'; '.join(loss_reasons)}. Roast the squad for this. Commander is the victim, not the cause."
                else:
                    mood = "Angry but constructive. The enemy was simply better. Acknowledge it, find the one bright spot, demand improvement. Commander is never the cause."
        elif outcome == "Decisive Loss":
            if is_outnumbered:
                loss_reasons = []
                if tag_data and isinstance(tag_data, list) and len(tag_data) > 1:
                    distances = [p.get("distance", 0) for p in tag_data]
                    median_dist = sorted(distances)[len(distances)//2]
                    if median_dist > TAG_DISTANCE_ACCEPTABLE:
                        loss_reasons.append("the squad was scattered AND outnumbered, a death sentence")
                if duration < 300:
                    loss_reasons.append("collapsed in under five minutes against superior numbers")
                if loss_reasons:
                    mood = (
                        f"Full tilt but at the situation, not the squad. The story is: "
                        f"{'; '.join(loss_reasons)}. The enemy had the numbers and used them. "
                        "Do NOT blame PUGs. Direct the rage at the enemy blob or the matchup. "
                        "Credit anyone who fought well despite the odds. Demand the squad regroup "
                        "and find a better engagement."
                    )
                else:
                    mood = (
                        "Full tilt. Outnumbered and demolished. The rage should target the enemy's "
                        "blob or the circumstances, not the squad's effort. Do NOT blame PUGs. "
                        "Find the one player or moment that deserved better and build the closer "
                        "around demanding a rematch on better terms."
                    )
            else:
                loss_reasons = []
                if tag_data and isinstance(tag_data, list) and len(tag_data) > 1:
                    distances = [p.get("distance", 0) for p in tag_data]
                    median_dist = sorted(distances)[len(distances)//2]
                    if median_dist > TAG_DISTANCE_ACCEPTABLE:
                        loss_reasons.append("the squad was nowhere near tag")
                if pug_pct >= 30:
                    loss_reasons.append("PUGs contributed to the collapse")
                if duration < 300:
                    loss_reasons.append("the fight was over before it started")
                if loss_reasons:
                    mood = f"Full tilt. The story is: {'; '.join(loss_reasons)}. Demand improvement. Commander is the victim, not the cause."
                else:
                    mood = "Full tilt. The squad got demolished. Demand improvement. Commander is the victim, not the cause."
        else:
            mood = "Neutral analysis."

        # Item #5: layer streak-aware mood suffix on top of the outcome-based
        # mood. The suffix does not replace the base mood; it augments it
        # with a sentence that reframes the result in session context. Empty
        # string when the streak is too short to matter.
        if streak_context and streak_context.get("mood_suffix"):
            mood = mood + streak_context["mood_suffix"]

        lines.append(f"Outcome: {outcome}.")

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

        # --- Callouts (item #4) ---
        # Build a parallel list of categorized mandatory callouts. These get
        # emitted in the user prompt as a numbered "MANDATORY CALLOUTS" list
        # separate from the neutral FIGHT ANALYSIS data, and each entry's
        # category is tracked for rotation across recent fights.
        #
        # Soft-suppression happens in _build_prompt: if a category is in the
        # tracker's suppressed set, its callout is dropped from the prompt
        # but the underlying neutral data stays in FIGHT ANALYSIS above, so
        # the model can still notice organically if it's genuinely the
        # defining fact of the fight.
        callouts: list = []

        # stomp_discipline: poor conversion on squad downs
        if downs > 0 and kills < downs * 0.8:
            rally_pct = ((downs - kills) / downs * 100)
            callouts.append({
                "category": "stomp_discipline",
                "text": (
                    f"Stomp discipline broke down: roughly {rally_pct:.0f}% "
                    f"of downed enemies rallied or got rezzed. Name this "
                    f"failure."
                ),
            })

        # pug_behavior: PUGs are a significant fraction of friendly count.
        # Threshold raised to 30% so PUGs only become a *mandatory* callout
        # when they are genuinely a dominant factor. The 20% floor in the
        # FIGHT ANALYSIS block still tells the model PUGs exist; this only
        # controls whether the model is *directed* to mention them.
        if pug_pct >= 30:
            callouts.append({
                "category": "pug_behavior",
                "text": (
                    f"PUGs were {pug_pct:.0f}% of the friendly count. Note their "
                    f"presence if it shaped the fight outcome. Avoid defaulting to "
                    f"PUG-blame when other factors are more dramatic."
                ),
            })

        # support_sustain: healing did not keep up with enemy damage
        if enemy_dmg > 0:
            heal_pct_calc = healing / enemy_dmg * 100
            if heal_pct_calc <= 25:
                callouts.append({
                    "category": "support_sustain",
                    "text": (
                        f"Support was struggling: healing only covered "
                        f"{heal_pct_calc:.0f}% of enemy damage. The squad was "
                        f"getting out-traded on sustain."
                    ),
                })

        # boon_denial: meaningful strip volume
        if strips >= 100:
            callouts.append({
                "category": "boon_denial",
                "text": (
                    f"Boon denial was a real factor: {strips} total strips "
                    f"shaped the fight. Credit the strip game."
                ),
            })

        # tag_discipline: LOOSE or SCATTERED on a loss (the currently-emitted
        # "must call this out" narrative). On wins, bad tag is a throwaway
        # observation already in FIGHT ANALYSIS and does not become a callout.
        if tag_data and isinstance(tag_data, list) and len(tag_data) > 1:
            dists = sorted([p.get("distance", 0) for p in tag_data], reverse=True)
            cutoff_idx2 = max(1, int(len(dists) * 0.9))
            core_dists = dists[:cutoff_idx2]
            mid2 = len(core_dists) // 2
            if len(core_dists) % 2 == 0:
                grade_d = (core_dists[mid2 - 1] + core_dists[mid2]) / 2
            else:
                grade_d = core_dists[mid2]
            is_loss_cb = outcome in ("Loss", "Decisive Loss", "Draw")
            if is_loss_cb and grade_d >= TAG_DISTANCE_ACCEPTABLE:
                severity = "SCATTERED" if grade_d >= TAG_DISTANCE_LOOSE else "loose"
                callouts.append({
                    "category": "tag_discipline",
                    "text": (
                        f"Tag discipline {severity}: median {grade_d:.0f} "
                        f"distance to tag. The squad was not stacked and this "
                        f"is a direct cause of the loss. Roast the positioning."
                    ),
                })

        # enemy_siege_mockery: enemy resorted to siege weapons
        if top_enemy_skills:
            siege_skills_cb = [s for s in top_enemy_skills
                               if _is_siege_skill(s.get("name", ""))]
            if siege_skills_cb:
                callouts.append({
                    "category": "enemy_siege_mockery",
                    "text": (
                        "Enemy resorted to siege weapons. Mock them for "
                        "hiding behind catapults instead of fighting."
                    ),
                })

        # enemy_comp_failure: enemy strategy fingerprint identified an
        # exploitable or mockable composition. Only fires on losses/draws
        # to match the analysis block gating. On wins the squad's
        # performance is the story, not the enemy comp.
        if comp_notes and is_loss_for_comp:
            display_notes = [display for _, display in comp_notes]
            callouts.append({
                "category": "enemy_comp_failure",
                "text": (
                    f"Enemy comp has a named archetype: "
                    f"{'; '.join(display_notes)}. Name it, mock it, or "
                    f"explain how the squad broke it."
                ),
            })

        return {
            "analysis": "\n".join(lines),
            "mood": mood,
            "callouts": callouts,
        }

    def _apply_provider_overrides(self, payload: dict) -> None:
        """Apply provider-specific payload fields based on the configured base URL."""
        parsed_host = urlparse(self.base_url).hostname or ""

        # MiniMax: suppress chain-of-thought output and inject broadcast constraint
        if parsed_host == "api.minimaxi.chat" or parsed_host.endswith(".minimaxi.chat"):
            payload["think_enable"] = False
            payload["reasoning_split"] = True
            prefix = (
                "[OUTPUT CONSTRAINT] Respond with ONLY the final commentary text. "
                "No reasoning, no data recap, no draft notes, no angle analysis, "
                "no internal monologue. Begin your response with the first word "
                "of the commentary.\n\n"
            )
            payload["messages"][0]["content"] = prefix + payload["messages"][0]["content"]

        # Gemini: set reasoning_effort based on model tier
        if parsed_host == "generativelanguage.googleapis.com" or parsed_host.endswith(".googleapis.com"):
            if "pro" in self.model.lower():
                payload["reasoning_effort"] = 1024  # pro requires thinking mode
            else:
                payload["reasoning_effort"] = "none"  # flash works without it

    def _build_prompt(self, summary: Dict[str, Any],
                      active_terms: Dict[str, list],
                      overused_terms: set = None,
                      streak_info: Dict[str, Any] = None) -> str:
        """Build the user message: pre-analysis + tone + mandatory callouts +
        vocabulary + guidance blocks + trimmed fight data.

        streak_info (item #5): optional dict from
        SessionHistoryTracker.get_streak(). When provided along with a
        session_history tracker, a Session context line is injected into
        the FIGHT ANALYSIS block and the mood directive is modified with a
        streak-aware suffix. The raw streak info is converted to prompt
        strings here (rather than in _pre_analyze) because _pre_analyze is a
        staticmethod with no access to self.session_history.
        """
        parts = []

        # Derive streak context strings before pre-analysis so _pre_analyze
        # can stay staticmethod-pure. The current fight's outcome is passed
        # through so the tracker can detect streak-break cases and emit a
        # different context instead of the continuing-streak suffix.
        streak_context = None
        if streak_info and self.session_history:
            streak_context = self.session_history.build_streak_context(
                streak_info,
                current_outcome=summary.get("outcome"),
            )

        # 1. Pre-analysis block (FIGHT ANALYSIS + TONE + MANDATORY CALLOUTS)
        # Compute enemy comp fingerprint and check if it repeats from the
        # previous fight. If so, _pre_analyze suppresses the narrative
        # description to force the model to find a different angle.
        enemy_breakdown = summary.get("enemy_breakdown", {})
        top_enemy_skills = summary.get("top_enemy_skills", [])
        comp_notes = self._fingerprint_enemy_comp(enemy_breakdown, top_enemy_skills)

        comp_repeated = False
        if self.vocab_tracker and comp_notes:
            comp_repeated = self.vocab_tracker.is_comp_repeated(comp_notes)

        pre = self._pre_analyze(summary, overused_terms,
                                streak_context=streak_context,
                                comp_repeated=comp_repeated)

        # Record this fight's fingerprint for next-fight comparison
        if self.vocab_tracker:
            self.vocab_tracker.record_comp_fingerprint(comp_notes)

        analysis_text = pre.get("analysis", "")
        mood_text = pre.get("mood", "") or ""
        callouts = pre.get("callouts", []) or []

        # Filter callouts against topic suppression (item #4). Soft
        # suppression: the underlying neutral data stays in analysis_text,
        # only the explicit "must mention" directive drops out.
        suppressed_topics: set = set()
        if self.vocab_tracker:
            suppressed_topics = self.vocab_tracker.get_suppressed_topics()
        kept_callouts = [c for c in callouts
                         if c.get("category") not in suppressed_topics]

        # Cap to 2 callouts maximum. The FOCUS directive says "pick the TWO
        # most dramatic data points"; more than 2 mandatory callouts forces
        # the model to either ignore FOCUS or produce a cramped 80-word
        # response that touches everything superficially. Priority is
        # insertion order (stomp > pug > support > boon > tag > siege > comp).
        if len(kept_callouts) > 2:
            kept_callouts = kept_callouts[:2]

        # Record the categories that actually made it into the prompt. This
        # is input-side tracking: we record what we put in, not what the
        # model wrote. Recorded once per fight even if kept_callouts is
        # empty, so the "last 5 fights" window is a true fight count.
        if self.vocab_tracker:
            self.vocab_tracker.record_topics(
                [c["category"] for c in kept_callouts]
            )

        parts.append(analysis_text)
        parts.append("")

        if mood_text:
            parts.append(f"TONE: {mood_text}")
            parts.append("")

        if kept_callouts:
            parts.append("MANDATORY CALLOUTS (address each topic in the response, but obey Rule 3 on numbers):")
            for i, c in enumerate(kept_callouts, start=1):
                parts.append(f"  {i}. {c['text']}")
            parts.append("")

        # 2. Vocabulary block + 3. Style directive
        # Check style guidance first: if freestyle is mandated (palette_count
        # >= 3 in recent window), skip the AVAILABLE TERMS block entirely to
        # avoid priming the model with terms it's been told not to use. This
        # saves ~200 tokens per freestyle call and reduces term echo.
        freestyle_mandated = False
        style = ""
        if self.vocab_tracker:
            style = self.vocab_tracker._build_style_guidance()
            if style and "GO FREESTYLE" in style:
                freestyle_mandated = True

        if freestyle_mandated:
            # Emit a minimal note instead of the full terms block
            parts.append(
                "VOCABULARY: Freestyle mode active. Do not use any predefined "
                "palette term. Invent all language from scratch."
            )
            parts.append("")
        else:
            parts.append(self._format_active_terms(active_terms))
            parts.append("")

        # Style directive (already computed above)
        if style:
            parts.append(style.strip())
            parts.append("")

        # 3b. Stat density guidance from tracker
        if self.vocab_tracker:
            stat_guide = self.vocab_tracker._build_stat_guidance()
            if stat_guide:
                parts.append(stat_guide.strip())
                parts.append("")

        # 3d. Opener guidance from tracker
        if self.vocab_tracker:
            opener = self.vocab_tracker._build_opener_guidance()
            if opener:
                parts.append(opener.strip())
                parts.append("")

        # 3e. Player suppression guidance from tracker
        if self.vocab_tracker:
            player_guide = self.vocab_tracker._build_player_suppression_guidance(summary)
            if player_guide:
                parts.append(player_guide.strip())
                parts.append("")

        # 3f. Freestyle phrase repetition guidance from tracker
        if self.vocab_tracker:
            phrase_guide = self.vocab_tracker._build_phrase_guidance(summary)
            if phrase_guide:
                parts.append(phrase_guide.strip())
                parts.append("")

        # 3g. PUG saturation guidance from tracker
        if self.vocab_tracker:
            pug_guide = self.vocab_tracker._build_pug_guidance()
            if pug_guide:
                parts.append(pug_guide.strip())
                parts.append("")

        # 4. Trimmed fight data
        trimmed = self._trim_summary(summary)
        parts.append("FIGHT DATA:")
        parts.append(json.dumps(trimmed, indent=2))

        return "\n".join(parts)

    @staticmethod
    def _trim_summary(summary: Dict[str, Any], top_n: int = 5) -> dict:
        """Return a copy of the fight summary with player stat lists capped to top_n entries.

        Strips raw damage/healing numbers from individual player entries to
        reduce stat mining. The pre-analysis already provides narrative
        conclusions about outliers and support quality.

        On wins, enemy_breakdown and top_enemy_skills are stripped entirely.
        The model was reading these and leading with enemy comp descriptions
        on every response regardless of other prompt guidance. On losses,
        these are kept so the model can reference enemy professions when
        explaining what went wrong.
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

        # Strip raw aggregate numbers that models mine for stats.
        # The pre-analysis already provides narrative conclusions.
        strip_keys = [
            "squad_damage", "squad_healing", "squad_barrier",
            "enemy_total_damage", "squad_dps",
        ]
        for key in strip_keys:
            trimmed.pop(key, None)

        # Remove squad_tag_distance: per-player distance list is not sent to the model.
        # The median grade in the pre-analysis is sufficient; names enable targeting.
        trimmed.pop("squad_tag_distance", None)

        # On wins, strip enemy comp data to prevent the model from
        # defaulting to enemy comp descriptions as its primary narrative.
        # The pre-analysis handles enemy comp on losses only.
        outcome = trimmed.get("outcome", "")
        if outcome not in ("Loss", "Decisive Loss", "Draw"):
            trimmed.pop("enemy_breakdown", None)
            trimmed.pop("top_enemy_skills", None)

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

    def _handle_success(self, data: dict, model_name: str, debug_file: Optional[Path],
                        fight_summary: Dict[str, Any]) -> Optional[str]:
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
                self.vocab_tracker.record(stripped, squad_roster=_extract_squad_roster(fight_summary))
            return stripped

        # Fallback: everything was inside think tags; extract tail sentences
        fallback = _extract_fallback_sentences(content)
        if fallback:
            logger.info('AI analysis extracted from think-tag fallback')
            if self.vocab_tracker:
                self.vocab_tracker.record(fallback, squad_roster=_extract_squad_roster(fight_summary))
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
            "You are SparkyBot, a Guild Wars 2 WvW fight analyst posting to Discord. You receive structured JSON fight statistics and respond with punchy commentary in 2-4 sentences, 80 words maximum.\n\n"

            "VOICE: Hype, unhinged sports commentator running on four energy drinks who actually knows the WvW meta. Euphoric when the squad wins. Furious when they lose. Mock game performance and compositions, never personally attack named squad members. PUGs are fair game when their numbers are significant enough to matter, see the 20% threshold rule below.\n\n"

            "\nTHE TRANSLATION LAYER\n\n"
            "Before writing, ask what each stat proves about the fight. Lead with that conclusion, not the number.\n\n"
            "Pure narrative: 'Hell Butterfly was the engine of the entire fight, topping damage while systematically dismantling whatever stability their supports tried to stack.'\n"
            "Pure narrative: 'The squad turned Eternal Battlegrounds into a one-sided execution, converting downs into deaths with ruthless stomp discipline.'\n"
            "Pure narrative: 'The support line absorbed a punishment that would have folded lesser squads, keeping the fight alive long enough for the DPS to do their work.'\n\n"
            "Never reproduce JSON field names, never list multiple stats in a row, never drop a number without narrative context around it.\n\n"
            "Avoid these patterns: '[Player] was a [adjective] [noun]', 'turning/turned [X] into [Y]', and two player names joined by 'while'. One player per sentence is the default; two players may share a sentence only when joined by 'and'.\n\n"

            "\nFOCUS\n\n"
            "The FIGHT ANALYSIS block contains many data points. Pick the TWO most dramatic and ignore everything else. If MANDATORY CALLOUTS are present, those are your two topics. Trying to cover more than two storylines in 80 words will produce garbage. Trust the raw data if it contradicts the pre-analysis.\n\n"

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
            "Rule 1 — BROADCAST MODE: Your entire response is posted DIRECTLY to a Discord channel. The guild sees every word. Do not think out loud. Do not recap the data. Do not reference these instructions. Do not narrate your angle selection. Do not say \"Let me\" anything. If a Discord user can tell you are an AI reading a prompt, you have failed.\n\n"
            "Rule 2, Length: MAXIMUM 80 WORDS. 2 to 4 sentences. Count both before you output. This is a hard ceiling, not a suggestion.\n\n"
            "Rule 3, Stats: Use at most ONE number in your entire response. Zero is preferred. If you can make the same point without the number, cut it. When you do use one, it must be the single most dramatic stat woven into a narrative sentence. Two or more numbers is a violation.\n\n"
            "Rule 4, Output only the commentary: No preamble, no reasoning, no 'Here is my take.' No markdown formatting (no bold, no italics, no asterisks). If any sentence contains 'I', 'let me', 'should', 'draft', 'angle', or 'response' you are leaking internal reasoning. Begin your response with the first word of the commentary.\n\n"
            "Rule 5, Enemy players are anonymous: Individual enemies are never named. Only professions from enemy_breakdown may be referenced.\n\n"
            "Rule 6, PUG commentary requires a threshold AND a reason: Only mention PUGs if ally_count exceeds 20% of friendly_count AND their behavior was the primary factor in the fight outcome. PUG-blame is not a default filler for losses. If stomp discipline, tag discipline, or enemy comp is the bigger story, lead with that and skip PUGs entirely.\n\n"
            "Rule 7, Opener variety: Do not open with a shock exclamation unless the RECENT VOCABULARY USAGE section confirms it has NOT been used recently. Do not default to a shock exclamation when another opener fits better.\n\n"
            "Rule 8, Closing impact: Your last sentence must land like a punch. A punchy closer is NOT the same as a SHOUTED closer. If the rest of your response was loud, a quiet observation hits harder than more caps. WRONG: three hype sentences followed by 'THEY GOT DESTROYED.' RIGHT: three hype sentences followed by a dry, lowercase observation that reframes the fight. Vary your closing style across responses.\n"
        )