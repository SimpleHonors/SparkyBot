"""VocabularyConfig: loads vocabulary from JSON, compiles regex, dice-rolls terms.

Single-class module. Depends only on ai_helpers.
"""
import json
import logging
import random
import re
from pathlib import Path
from typing import Dict

from ai_helpers import (
    HYPE_HEAVY,
    HYPE_OUTSIZED,
    HYPE_RESERVED,
    _atomic_write_json,
    _is_siege_skill,
)

logger = logging.getLogger(__name__)


class VocabularyConfig:
    """Loads vocabulary from JSON, compiles regex, dice-rolls per-call term selection."""

    def __init__(self, config_path: Path = None):
        self.config_path = config_path or Path(__file__).parent.parent / "sparkybot_vocabulary.json"
        self._raw: dict = {}           # raw JSON data
        self._compiled: list = []      # [(name, [compiled_regex, ...]), ...] for tracker
        self._mtime: float = 0         # last modified time for auto-reload
        self.load()

    @staticmethod
    def _default_vocabulary() -> dict:
        """Return the full default vocabulary structure. Single source of truth for defaults."""
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
            _atomic_write_json(self.config_path, self._raw)
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

    def find_matches(self, text: str, term_filter: set = None) -> list:
        """Return list of term names whose patterns match `text`.

        If `term_filter` is given, only terms whose name is in that set are
        checked. Each term is reported at most once even if multiple of its
        patterns match.
        """
        if not text:
            return []
        matches = []
        for name, patterns in self._compiled:
            if term_filter is not None and name not in term_filter:
                continue
            for pattern in patterns:
                if pattern.search(text):
                    matches.append(name)
                    break
        return matches

    def all_terms(self) -> list:
        """Return flat list of all term dicts across all categories."""
        result = []
        for cat in ("shock", "positive", "negative", "gates"):
            result.extend(self._raw.get(cat, []))
        return result


    def _gate_matches(self, gate: dict, summary: dict) -> bool:
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

    def _context_blocks_term(self, term_entry: dict, summary: dict) -> bool:
        """Suppress terms that don't fit the fight context."""
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

        # Tier 1: 2x+ advantage (ratio < 0.5)
        if ratio < 0.5 and (term_name in HYPE_HEAVY or alt_name in HYPE_HEAVY):
            return True

        # Tier 2: 1.3-2x advantage (0.5 <= ratio < 0.75)
        if 0.5 <= ratio < 0.75 and (term_name in HYPE_RESERVED or alt_name in HYPE_RESERVED):
            return True

        # Tier 3: modest advantage (0.75 <= ratio < 0.85)
        if 0.75 <= ratio < 0.85 and (term_name in HYPE_OUTSIZED or alt_name in HYPE_OUTSIZED):
            return True

        return False

    def roll_active_terms(self, overused_terms: set,
                          weight_overrides: dict = None,
                          fight_summary: dict = None) -> Dict[str, list]:
        """Dice-roll each term. Overused terms get 0% chance."""
        self.reload_if_changed()

        file_weights = self._raw.get("weights", {})
        weights = {**file_weights, **(weight_overrides or {})}
        default_weight = 1 / 3

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
                if self._context_blocks_term(t, fight_summary):
                    continue
                # Gate condition check (gates category only)
                if category_key == "gates" and not self._gate_matches(t, fight_summary):
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
        """Return a summary of what changed between user's version and defaults."""
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
        """Update the vocabulary file to the latest defaults."""
        defaults = self._default_vocabulary()

        if not merge:
            self._raw = defaults
        else:
            # Preserve user's weights if they've customized them
            if "weights" in self._raw:
                defaults["weights"] = self._raw["weights"]

            # For each category: refresh metadata of existing terms from defaults,
            # append genuinely new terms, and preserve any user-added custom terms.
            for cat in ("shock", "positive", "negative", "gates"):
                current_by_term = {e["term"]: e for e in self._raw.get(cat, [])}
                default_by_term = {e["term"]: e for e in defaults.get(cat, [])}

                merged = []
                for term_name, cur in current_by_term.items():
                    if term_name in default_by_term:
                        # Refresh structural fields from defaults, keep any
                        # user-added keys that defaults don't know about.
                        refreshed = dict(default_by_term[term_name])
                        refreshed.update({k: v for k, v in cur.items()
                                          if k not in refreshed})
                        merged.append(refreshed)
                    else:
                        # User-added custom term — preserve untouched
                        merged.append(cur)

                # Append brand-new default terms
                for term_name, def_entry in default_by_term.items():
                    if term_name not in current_by_term:
                        merged.append(def_entry)

                self._raw[cat] = merged

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
