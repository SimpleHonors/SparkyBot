"""VocabularyTracker: rolling-window usage tracking and prompt-injection guidance.

Depends on ai_helpers and vocabulary_config.
"""
import json
import logging
import random
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict

from ai_helpers import (
    _STAT_RE,
    _atomic_write_json,
    _extract_squad_roster,
)
from pre_digester import (
    fight_duration_bucket,
    numbers_context,
    squad_strip_volume,
    squad_cleanse_volume,
)
from vocabulary_config import VocabularyConfig
from stochastic_seeds import sample_seed, format_seed_block

logger = logging.getLogger(__name__)


# VocabularyTracker — records SparkyBot terms used recently for language variation
class VocabularyTracker:
    """Tracks vocabulary usage over a rolling 2-hour window. Persists to JSON."""

    # Anti-repetition must hold for at least this many fights (rounds), even when
    # the time window would otherwise evict an event sooner (slow live play). See
    # _prune(): the most recent ROUND_FLOOR fights are never clock-evicted.
    ROUND_FLOOR = 5

    def __init__(self, store_path: Path = None, window_hours: int = 2,
                 vocab_config: VocabularyConfig = None):
        self.store_path = store_path or Path(__file__).parent.parent / "sparkybot_vocab_usage.json"
        self.window_seconds = window_hours * 3600
        self.vocab_config = vocab_config
        self._events: list = []        # list of {"term": str, "ts": float}
        self._stat_events: list = []   # list of {"count": int, "ts": float}
        self._style_events: list = []  # list of {"used_palette": bool, "ts": float}
        self._opener_events: list = []  # list of {"strategy": str, "ts": float}
        self._player_events: list = []  # list of {"name": str, "ts": float}
        self._topic_fight_events: list = []  # list of {"categories": list[str], "ts": float}
        self._phrase_events: list = []
        self._pug_events: list = []  # list of {"ts": float}
        self._comp_fingerprint_events: list = []  # list of {"labels": list[str], "ts": float}
        self._directive_events: list = []  # list of {"key": str, "ts": float}
        self._seed_events: list = []  # list of {"noun": str, "register": str, "ts": float}
        # M5: commander pacing — additive fields,不影响现有 record()/get_overused_terms()
        self._max_barks_per_fight: int = 3
        self._commander_bark_counts: Dict[str, int] = {}    # fight_id -> count
        self._commander_last_seen: Dict[str, float] = {}    # "cmd:<name>" -> ts
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, response_text: str, squad_roster: list = None,
               commander: str = None, fight_id: str = None) -> None:
        """Scan response and record terms, stat density, style, player mentions.

        M5: when `commander` and `fight_id` are passed and the commander name
        appears in `response_text`, register a commander bark so subsequent
        fights can rate-limit via `commander_bark_allowed`.
        """
        if not response_text:
            return
        now = time.time()

        # Vocabulary tracking
        matches = self.vocab_config.find_matches(response_text) if self.vocab_config else []
        for name in matches:
            self._events.append({"term": name, "ts": now})
        matched_any_palette = bool(matches)

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

        # M5: register commander bark if the commander name appears in the response.
        # Word-boundary-safe match avoids a short name matching inside a
        # longer one (e.g. "Sam" inside "Samson").
        if commander and fight_id:
            cmd_pattern = r'(?<!\w)' + re.escape(commander) + r'(?!\w)'
            if re.search(cmd_pattern, response_text, re.IGNORECASE):
                self.register_commander_bark(commander, fight_id)

        self._prune()
        self._save()

    # ------------------------------------------------------------------
    # M5: Commander pacing — uses only real _events append + additive dicts
    # ------------------------------------------------------------------

    def commander_bark_allowed(self, phrase: str, fight_id: str,
                              cooldown_seconds: float = 300.0,
                              max_barks_per_fight: int = None) -> bool:
        """Return True if commander bark is allowed (not rate-limited).

        Uses only real _events append + additive dicts. No hallucinated methods.
        """
        if not phrase or not fight_id:
            return False
        if max_barks_per_fight is None:
            max_barks_per_fight = self._max_barks_per_fight
        cmd_key = f"cmd:{phrase.lower()}"
        now = time.time()
        # Per-fight cap
        count = self._commander_bark_counts.get(fight_id, 0)
        if count >= max_barks_per_fight:
            return False
        # Cooldown window
        last = self._commander_last_seen.get(cmd_key, 0)
        if now - last < cooldown_seconds:
            return False
        return True

    def register_commander_bark(self, phrase: str, fight_id: str) -> None:
        """Record a commander bark: appends to _events, updates counters.

        Uses only real _events append + additive dicts. No hallucinated methods.
        """
        if not phrase or not fight_id:
            return
        now = time.time()
        cmd_key = f"cmd:{phrase.lower()}"
        self._events.append({"term": cmd_key, "ts": now})
        self._commander_bark_counts[fight_id] = self._commander_bark_counts.get(fight_id, 0) + 1
        self._commander_last_seen[cmd_key] = now

    def reset_commander_barks(self, fight_id: str) -> None:
        """Clear commander pacing state for a fight (called when fight ends).

        Does NOT clear _events — those are pruned by _cleanup_old_entries() on each record().
        """
        if not fight_id:
            return
        self._commander_bark_counts.pop(fight_id, None)
        # Also clear any cmd:<*> entries whose last_seen belong to this fight_id
        # by nuking all last_seen entries (simpler, avoids complex tracking)
        self._commander_last_seen = {
            k: v for k, v in self._commander_last_seen.items()
            if not k.startswith("cmd:")
        }

    def record_players(self, response_text: str, squad_roster: list,
                       now: float = None) -> None:
        """Scan response for squad player names and record mentions."""
        if not response_text or not squad_roster:
            return
        if now is None:
            now = time.time()
        seen = set()
        for name in squad_roster:
            if not name or not isinstance(name, str) or name in seen:
                continue
            # Word-boundary-safe match (anchors on non-word chars to avoid a short name matching inside a longer one)
            pattern = r'(?<!\w)' + re.escape(name) + r'(?!\w)'
            if re.search(pattern, response_text, re.IGNORECASE):
                self._player_events.append({"name": name, "ts": now})
                seen.add(name)

    def record_topics(self, categories, now: float = None) -> None:
        """Record callout categories pushed into the prompt this fight."""
        if now is None:
            now = time.time()
        cats = list(categories) if categories else []
        self._topic_fight_events.append({"categories": cats, "ts": now})
        self._prune()
        self._save()

    def record_comp_fingerprint(self, comp_notes: list, now: float = None) -> None:
        """Record enemy comp archetype labels for this fight."""
        if now is None:
            now = time.time()
        keys = [key for key, _ in (comp_notes or [])]
        self._comp_fingerprint_events.append({"labels": keys, "ts": now})
        self._prune()
        self._save()

    def is_comp_repeated(self, current_comp_notes: list) -> bool:
        """Return True if current comp matches previous fight."""
        if not self._comp_fingerprint_events or not current_comp_notes:
            return False
        current_keys = {key for key, _ in current_comp_notes}
        if not current_keys:
            return False
        prev = self._comp_fingerprint_events[-1]
        prev_keys = set(prev.get("labels", []))
        return bool(current_keys & prev_keys)

    _TOPIC_SUPPRESSION_OVERRIDES = {
        "enemy_comp_failure": (4, 2),  # suppress after 2 of last 4 fights
        "stomp_discipline": (3, 2),    # suppress after 2 of last 3 fights
        "tag_discipline": (3, 2),      # suppress after 2 of last 3 fights
    }

    def get_suppressed_topics(self, lookback_fights: int = 5,
                              threshold: int = 3) -> set:
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
        if not response_text or not response_text.strip():
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
            strategy = "narrative"
        elif first_words[0][0].isupper():
            strategy = "player"
        else:
            strategy = "narrative"

        self._opener_events.append({"strategy": strategy, "ts": now})

    def _build_opener_guidance(self) -> str:
        """Suggest an opener strategy based on recent usage."""
        if not self._opener_events:
            return ""

        recent = sorted(self._opener_events, key=lambda e: e["ts"], reverse=True)[:5]
        strategies = [e["strategy"] for e in recent]

        last = strategies[0] if strategies else None
        # Exclude any strategy used in the last 3 responses
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
        """Return a prompt block describing recent vocabulary usage."""
       
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

            # Saturation collapse: if too many terms are overused, replace
            if len(overused) > 15:
                lines = [
                    "\n\nRECENT VOCABULARY USAGE",
                    "ALL predefined palette terms are currently overused. "
                    "Do not use any predefined term. Invent all vocabulary from scratch.",
                ]

        return "\n".join(lines) if lines else ""

    def get_overused_terms(self) -> set:
        """Return term names used 3+ times in the window.

        Caller is responsible for pruning first (e.g. _build_prompt calls
        prune() once at the top of the fight cycle).
        """
        counts: Dict[str, int] = {}
        for event in self._events:
            counts[event["term"]] = counts.get(event["term"], 0) + 1
        return {name for name, count in counts.items() if count >= 3}

    def _build_stat_guidance(self) -> str:
        """Analyze recent stat usage, return guidance for next response."""
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
        """Analyze palette vs freestyle usage, nudge the model."""
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
        Commander suppression: the commander is no longer blanket-exempt."""
        # Prune handled by _build_prompt caller.
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

        # Threshold: 2+ mentions in the window.
        # M5: commander escapes suppression if a bark is allowed for this fight
        # (per-fight cap + cross-fight cooldown via `commander_bark_allowed`),
        # replacing the prior 50% dice roll. fight_id is injected by FightAnalyst
        # at analyze() entry; falls back to "default" for callers that omit it.
        fight_id_for_bark = (summary.get("_fight_id") if summary else None) or "default"
        suppressed = []
        for name, c in counts.items():
            if c < 2:
                continue
            if name == commander:
                if self.commander_bark_allowed(commander, fight_id_for_bark):
                    continue  # commander escapes suppression this fight
            suppressed.append((name, c))

        suppressed.sort(key=lambda x: -x[1])
        if not suppressed:
            return ""

        # Check whether any suppressed player is in the outliers dict
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
            "The following squad players have been named as the lead story in 2+ "
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
        """Extract recurring n-grams, fixation verbs, and overused words."""
        # Prune handled by _build_prompt caller.
        if len(self._phrase_events) < 3:
            return ""

        # Build n-gram counts: how many distinct responses contain each phrase.
        # Keyed by compound form (spaces stripped) so "meat grinder" and
        # "meatgrinder" increment the same counter.
        ngram_doc_counts: Dict[str, int] = {}
        ngram_display: Dict[str, str] = {}   # compound key -> first-seen display form
        # Strip punctuation for matching, keep stop words (they matter for
        # phrases like "free rallies", "meat grinder")
        punct_re = re.compile(r'[^\w\s]', re.UNICODE)

        # Fixation verb counts: how many distinct responses contain each verb
        verb_doc_counts: Dict[str, int] = {}

        # Word frequency counts: how many distinct responses contain each word
        word_doc_counts: Dict[str, int] = {}

        # 2-gram creative pairs: both words must clear stopwords + domain allowlist.
        # Catches short improvised idioms ("wet paper", "tasting dirt", "HOLY HELL")
        # that fall below the 3-gram threshold.
        ngram_2_doc_counts: Dict[str, int] = {}
        ngram_2_display: Dict[str, str] = {}  # compound key -> first-seen display form

        # Build a dynamic skip set from player names and commander
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

            # Track which n-grams appear in THIS response (dedup within doc).
            # Compound key merges spaced/unspaced/hyphenated variants.
            seen_in_doc: set = set()
            for n in (3, 4):
                for i in range(len(words) - n + 1):
                    gram = ' '.join(words[i:i + n])
                    key = gram.replace(' ', '')
                    if key not in seen_in_doc:
                        seen_in_doc.add(key)
                        if key not in ngram_display:
                            ngram_display[key] = gram
                        ngram_doc_counts[key] = ngram_doc_counts.get(key, 0) + 1

            # 2-gram creative pairs: both words non-stopword and non-domain.
            # Compound key so "meat-grinder" (punct-stripped) and "meat grinder" merge.
            seen_2gram_in_doc: set = set()
            for i in range(len(words) - 1):
                w1, w2 = words[i], words[i + 1]
                if (len(w1) >= 3 and len(w2) >= 3
                        and w1 not in self._STOPWORDS
                        and w2 not in self._STOPWORDS
                        and w1 not in self._DOMAIN_ALLOWLIST
                        and w2 not in self._DOMAIN_ALLOWLIST
                        and w1 not in player_name_words
                        and w2 not in player_name_words):
                    gram2 = f"{w1} {w2}"
                    key2 = gram2.replace(' ', '')
                    if key2 not in seen_2gram_in_doc:
                        seen_2gram_in_doc.add(key2)
                        if key2 not in ngram_2_display:
                            ngram_2_display[key2] = gram2
                        ngram_2_doc_counts[key2] = ngram_2_doc_counts.get(key2, 0) + 1

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

        # Filter n-grams: appeared in 2+ distinct responses (display form via ngram_display)
        repeated = sorted(
            [(ngram_display[key], c) for key, c in ngram_doc_counts.items() if c >= 2],
            key=lambda x: -x[1]
        )

        # Filter 2-gram creative pairs: appeared in 2+ distinct responses.
        # Threshold is lower than 3/4-grams because the double-filter (both words
        # non-stopword + non-domain) already suppresses most common collocations.
        repeated_2grams = sorted(
            [(ngram_2_display[key], c) for key, c in ngram_2_doc_counts.items() if c >= 2],
            key=lambda x: -x[1]
        )

        # Filter fixation verbs: appeared in 2+ distinct responses
        repeated_verbs = sorted(
            [(verb, c) for verb, c in verb_doc_counts.items() if c >= 2],
            key=lambda x: -x[1]
        )

        # Filter overused words: appeared in 6+ of last 8 responses.
        word_threshold = 6
        min_responses_for_word_tracking = 6
        repeated_words = []
        if len(self._phrase_events) >= min_responses_for_word_tracking:
            repeated_words = sorted(
                [(w, c) for w, c in word_doc_counts.items()
                 if c >= word_threshold],
                key=lambda x: -x[1]
            )

        if not repeated and not repeated_2grams and not repeated_verbs and not repeated_words:
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

        # Add 2-gram creative pairs (after 3/4-grams to preserve priority)
        for gram, count in repeated_2grams:
            # Skip if already covered by a longer banned gram
            if any(gram in longer for longer in banned_set if len(longer) > len(gram)):
                continue
            if gram not in banned_set:
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

        # Cap the list at 15 to avoid prompt bloat
        if len(banned) > 15:
            banned = banned[:15]

        return (
            "\n\nREPEATED PHRASES — DO NOT REUSE\n"
            "The following phrases and words have appeared in many of your recent responses. "
            "They are OFF LIMITS for this response. Find different words to express "
            "the same idea, or drop the concept entirely if you cannot rephrase it.\n"
            f"  {', '.join(banned)}."
        )

    # ------------------------------------------------------------------
    # Narrative directive rotation
    # ------------------------------------------------------------------

    _DIRECTIVE_COOLDOWN = 3  # fights before a directive can repeat

    # Each entry: key (tracking), text (injected into prompt), context_key (gate).
    # Texts are attention constraints — WHERE to focus — not positive examples.
    # Context keys: None = always available; others gated in get_narrative_directive.
    _DIRECTIVES = [
        {
            "key": "tactical_autopsy",
            "text": (
                "NARRATIVE ANGLE: One decision or positioning error defined this outcome. "
                "Find it in the data and make it the central fact everything else orbits."
            ),
            "context": None,
        },
        {
            "key": "inflection_point",
            "text": (
                "NARRATIVE ANGLE: This fight had a turning point. Find where control shifted — "
                "everything before it is setup, everything after is consequence."
            ),
            "context": None,
        },
        {
            "key": "enemy_first",
            "text": (
                "NARRATIVE ANGLE: Start from the enemy's side — what they brought and how they "
                "executed — before the outcome lands."
            ),
            "context": None,
        },
        {
            "key": "efficiency",
            "text": (
                "NARRATIVE ANGLE: The ratio, not the result. How efficiently was this won or "
                "lost? Make the waste or the precision the story."
            ),
            "context": None,
        },
        {
            "key": "chaos",
            "text": (
                "NARRATIVE ANGLE: Multiple factors pulled in different directions here. "
                "Capture the disorder — don't collapse it into a single thread."
            ),
            "context": None,
        },
        {
            "key": "clock_story",
            "text": (
                "NARRATIVE ANGLE: The duration is the story. Make the reader feel how fast "
                "control was established or how long the grind lasted."
            ),
            "context": "fast_or_long",  # fight_duration_bucket → BLITZ or LONG
        },
        {
            "key": "numbers_weight",
            "text": (
                "NARRATIVE ANGLE: The squad differential is the dominant fact. "
                "Build the reader's sense of scale before anything else."
            ),
            "context": "outnumbered",  # is_outnumbered or enemy >= squad * 1.4
        },
        {
            "key": "support_story",
            "text": (
                "NARRATIVE ANGLE: The support layer — strips, cleanses, healing — "
                "determined this outcome more than the damage numbers did. Make that visible."
            ),
            "context": "support_notable",  # squad_strip_volume or squad_cleanse_volume → HEAVY/EXTREME
        },
        {
            "key": "single_pivot",
            "text": (
                "NARRATIVE ANGLE: One player's numbers stand apart from the rest. "
                "Build around what they did and why it mattered to the overall outcome."
            ),
            "context": "outlier_exists",  # outliers dict non-empty
        },
    ]

    def get_narrative_directive(self, summary: Dict[str, Any] = None) -> str:
        """Pick a narrative angle with cooldown rotation and context gating.

        Returns the directive text to inject into the prompt, or "" if the
        tracker has fewer than 2 events (session too short to be meaningful).
        Records the chosen directive key for future cooldown enforcement.
        """
        now = time.time()

        # Keys used in the last _DIRECTIVE_COOLDOWN fights
        recent_keys = {
            e["key"] for e in self._directive_events[-self._DIRECTIVE_COOLDOWN:]
        }

        def _context_ok(context_key: str) -> bool:
            if context_key is None:
                return True
            if summary is None:
                return False
            dur = summary.get("duration_seconds", 0) or 0
            if context_key == "fast_or_long":
                return fight_duration_bucket(dur) in ("BLITZ", "LONG")
            if context_key == "outnumbered":
                friendly = (summary.get("friendly_count") or summary.get("squad_count") or 0)
                enemy = (summary.get("enemy_count") or 0)
                return numbers_context(friendly, enemy) in (
                    "THEY_OUTNUMBERED_US_SOFT", "THEY_OUTNUMBERED_US_HARD"
                )
            if context_key == "support_notable":
                strips = summary.get("squad_strips") or 0
                cleanses = summary.get("squad_cleanses") or 0
                return (
                    squad_strip_volume(strips, dur) in ("HEAVY", "EXTREME")
                    or squad_cleanse_volume(cleanses, dur) in ("HEAVY", "EXTREME")
                )
            if context_key == "outlier_exists":
                return bool(summary.get("outliers"))
            return True

        eligible = [
            d for d in self._DIRECTIVES
            if _context_ok(d["context"]) and d["key"] not in recent_keys
        ]

        # Fallback: if all context-eligible directives are on cooldown, ignore cooldown
        if not eligible:
            eligible = [d for d in self._DIRECTIVES if _context_ok(d["context"])]
        if not eligible:
            eligible = list(self._DIRECTIVES)

        chosen = random.choice(eligible)
        self._directive_events.append({"key": chosen["key"], "ts": now})
        self._save()
        return chosen["text"]

    # ------------------------------------------------------------------
    # Stochastic seed — high-entropy conditioning anchor
    # ------------------------------------------------------------------

    _SEED_NOUN_COOLDOWN = 20      # fights before a noun can repeat
    _SEED_REGISTER_COOLDOWN = 10  # fights before a register can repeat

    def get_stochastic_seed(self) -> str:
        """Pick a fresh (noun, register) lens, record it, return prompt block.

        Injects high-entropy conditioning to break cross-call template
        recurrence. Pool size is
        ~17k combinations vs the 9-directive narrative-angle rotation.
        """
        now = time.time()
        recent_nouns = [e["noun"] for e in self._seed_events[-self._SEED_NOUN_COOLDOWN:]]
        recent_regs = [e["register"] for e in self._seed_events[-self._SEED_REGISTER_COOLDOWN:]]
        seed = sample_seed(recent_nouns=recent_nouns, recent_registers=recent_regs)
        self._seed_events.append({
            "noun": seed["noun"],
            "register": seed["register"],
            "ts": now,
        })
        self._save()
        return format_seed_block(seed)

    def _build_pug_guidance(self) -> str:
        """Emit PUG-saturation suppression when PUGs mentioned 3+ times."""
        if len(self._pug_events) < 3:
            return ""

        # Count PUG mentions in the last 5 responses
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

    def prune(self) -> None:
        """Remove events older than the rolling window. Public wrapper."""
        self._prune()

    def _prune(self) -> None:
        """Remove events older than the rolling window, but never evict events
        from the last ROUND_FLOOR fights, so anti-repetition holds for at least
        that many rounds even in slow live play (fights spaced hours apart)."""
        cutoff = time.time() - self.window_seconds
        # phrase_events has exactly one entry per fight, so it is the fight clock.
        # Floor the cutoff at the ROUND_FLOOR-th most recent fight's timestamp so
        # the most recent rounds survive even when older than the time window.
        if len(self._phrase_events) >= self.ROUND_FLOOR:
            floor_ts = self._phrase_events[-self.ROUND_FLOOR]["ts"]
            cutoff = min(cutoff, floor_ts)
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
        self._directive_events = [e for e in self._directive_events if e["ts"] >= cutoff]
        self._seed_events = [e for e in self._seed_events if e["ts"] >= cutoff]

    def _load(self) -> None:
        """Load persisted events from disk, ignoring errors. Backward-compatible."""
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
                self._directive_events = data.get("directive_events", [])
                self._seed_events = data.get("seed_events", [])
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
            self._directive_events = []
            self._seed_events = []

    def _save(self) -> None:
        """Persist current events to disk, ignoring errors."""
        try:
            _atomic_write_json(self.store_path, {
                "events": self._events,
                "stat_events": self._stat_events,
                "style_events": self._style_events,
                "opener_events": self._opener_events,
                "player_events": self._player_events,
                "topic_fight_events": self._topic_fight_events,
                "phrase_events": self._phrase_events,
                "pug_events": self._pug_events,
                "comp_fingerprint_events": self._comp_fingerprint_events,
                "directive_events": self._directive_events,
                "seed_events": self._seed_events,
            })
        except Exception as exc:
            logger.warning("VocabularyTracker: could not save store: %s", exc)
