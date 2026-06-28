"""FightAnalyst: sends fight-summary data to any OpenAI-compatible API.

Supports OpenAI, MiniMax, Groq, Together AI, Mistral, OpenRouter,
Anthropic (via proxy), local Ollama, LM Studio, and any service that
implements the /v1/chat/completions endpoint.

Requires Python 3.10+.
"""
import json
import logging
import os
import random
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional

import requests

from ai_helpers import (
    TAG_DISTANCE_ACCEPTABLE,
    TAG_DISTANCE_EXCELLENT,
    TAG_DISTANCE_LOOSE,
    _STAT_RE,
    _extract_fallback_sentences,
    _extract_squad_roster,
    _grade_tag_distance,
    _is_siege_skill,
    _strip_think_tags,
)
from session_history import SessionHistoryTracker
from vocabulary_config import VocabularyConfig
from vocabulary_tracker import VocabularyTracker
from pre_digester import (
    bucket as pre_digester_bucket,
    numbers_context,
    squad_stomp_discipline,
    squad_strip_volume,
)
from freshness_engine import FreshnessEngine

from response_post_processor import post_process
from silent_failure_guard import SilentFailureGuard

# v3 (2026-05-09) — narrative_facts pipeline for the v3 prompt path.
# Imported lazily-friendly: if these modules fail to load, v2 path still works.
try:
    from narrative_facts import (
        build_narrative_facts, render_narrative_block,
        extract_roster_names, filter_palette_for_name_poisoning,
    )
    from callout_cooldown import CalloutCooldown
    _V3_AVAILABLE = True
except ImportError as _exc:
    logger.warning("v3 pipeline unavailable: %s", _exc)
    _V3_AVAILABLE = False

logger = logging.getLogger(__name__)


# Minimum required keys in fight_summary for a meaningful analysis.
_REQUIRED_SUMMARY_KEYS = {"outcome", "friendly_count", "enemy_count", "squad_count"}


# ---------------------------------------------------------------------------
# Default prompt version — increment when _core_system_prompt or _rules_section
# changes so users on custom prompts can be notified of improvements.
# ---------------------------------------------------------------------------
DEFAULT_PROMPT_VERSION = 4
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
            "Stat focus rewritten: numbers are encouraged when ONE or TWO specific values anchor the moment (headcount, duration, signature DPS); stat-wall recitation is what's banned, not numerals.",
            "FOCUS section added: 'Pick the TWO most dramatic data points, ignore everything else.' Stops models from trying to address all 15+ pre-analysis conclusions.",
            "Translation layer examples rewritten to favor pure narrative over stat-anchored alternatives.",
            "Loss and Draw moods are now computed from fight data (tag discipline, stomp rate, PUG percentage) instead of generic directives. The model receives a specific story to tell.",
            "Rule 8 added: 'Your last sentence must land like a punch.'",
            "Rule 4 updated: markdown formatting (bold, italics, asterisks) explicitly banned.",
            "Raw aggregate numbers (squad_damage, squad_healing, enemy_total_damage, squad_dps, squad_tag_distance) kept in fight data JSON so the model can anchor narrative on real numbers. Stat-wall vomit is prevented by prompt guidance, not data hiding — hiding the numbers forced the model into vague quantitative hedges ('more than you'd think', 'a hefty number').",
        ],
        "reason": "Shootout testing of 30+ models across multiple fights showed consistent patterns: stat-heavy outputs scored C, verbose outputs gamed sentence count, and loss commentary was underspecified.",
    },
    4: {
        "title": "Phrase freshness and narrative variety",
        "changes": [
            "Phrase-level tracking blocks repeated words and n-gram constructions across fights, not just banned terms",
            "Rotating narrative directives (e.g. 'find the inflection point', 'lead with the enemy') give each fight a different analytical angle",
            "Outnumbered, stomp discipline, and boon-denial thresholds are now calibrated against the actual fight corpus instead of hardcoded ratios",
            "Narrative directives are context-gated — clock-story only fires on blitz/long fights, numbers-weight only when outnumbered, etc.",
        ],
        "reason": "A/B testing across 49 fights showed vocabulary-tic patterns and repetitive analytical angles even when individual words rotated correctly. These changes target the structure of the commentary, not just the vocabulary.",
    },
}


class FightAnalyst:
    """Sends fight summary data to any OpenAI-compatible API for analysis."""

    @staticmethod
    def fetch_models(base_url: str, api_key: str = "", timeout: int = 10) -> List[str]:
        """Fetch available models from the API's /models endpoint."""
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
                 session_history: "SessionHistoryTracker" = None,
                 thinking: bool = True,
                 prompt_version: str = "v2",
                 callout_cooldown: "Optional[CalloutCooldown]" = None):
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
        self.thinking = thinking
        # M4 freshness engine — prepended to system prompt when previous_response is provided
        self._freshness = FreshnessEngine()
        self._previous_response: Optional[str] = None
        # M3 silent-failure guard — detects finish_reason=length + near-empty content
        self._silent_guard = SilentFailureGuard()
        # v3 wiring — opt-in. When prompt_version == 'v3', _build_system_prompt
        # and _build_prompt branch into the narrative_facts pipeline.
        self.prompt_version = prompt_version
        self._callout_cooldown = callout_cooldown
        # Pending records — populated by _build_prompt_v3, applied to the
        # cooldown ledger only after a successful LLM response (so retries
        # don't burn cooldowns).
        self._pending_topic_emits: set[str] = set()
        self._pending_player_emits: set[str] = set()
        self._pending_commander_emit: Optional[str] = None

    def analyze(self, fight_summary: Dict[str, Any], timeout: int = 30,
                previous_response: Optional[str] = None) -> Optional[str]:
        """Send fight data to LLM, return analysis text. Returns None on failure."""
        self._previous_response = previous_response
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

        # M5: stable per-fight id for commander-bark pacing (cap + cooldown)
        if "_fight_id" not in fight_summary:
            fight_summary["_fight_id"] = uuid.uuid4().hex[:8]

        # Dice-roll vocabulary
        active_terms = {"shock": [], "positive": [], "negative": [], "gates": []}
        overused = set()
        if self.vocab_config:
            if self.vocab_tracker:
                overused = self.vocab_tracker.get_overused_terms()
            active_terms = self.vocab_config.roll_active_terms(overused, weight_overrides=self._weight_overrides, fight_summary=fight_summary)

        # Read streak state BEFORE recording the current fight
        streak_info = None
        if self.session_history:
            streak_info = self.session_history.get_streak()
            self.session_history.record(
                outcome=fight_summary.get("outcome", "Unknown"),
                fight_shape=fight_summary.get("fight_shape", "unknown"),
            )

        prompt, comp_notes = self._build_prompt(fight_summary, active_terms,
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
                    data = response.json()
                    # M3: silent-failure guard — detect finish_reason=length + near-empty content
                    choice = data.get("choices", [{}])[0]
                    raw_content = choice.get("message", {}).get("content", "") or ""
                    ct = data.get("usage", {}).get("completion_tokens", 0)
                    if self._silent_guard.is_silent_failure(ct, self.max_tokens, raw_content):
                        recovered, log_msg = self._silent_guard.handle_failure(data, self.max_tokens)
                        if log_msg == "retry_required" and attempt < max_retries:
                            # Retry once with HOT TAKE forcing and reduced token budget
                            logger.warning("silent_failure detected — retrying with short format")
                            payload["messages"][1]["content"] += (
                                " Output the commentary now. "
                                "Begin with 'HOT TAKE:' — no thinking, no preamble, "
                                "just 2-3 sentences of Discord-ready text."
                            )
                            payload["max_tokens"] = self._silent_guard.fallback_token_limit
                            time.sleep(3)
                            continue
                        if recovered:
                            logger.info("silent_failure recovered via reasoning_content: %s", recovered[:60])
                            return recovered
                    result = self._handle_success(data, model_name, debug_file, fight_summary)
                    # Post-process: clean banned words and check for leaks
                    if result:
                        _banned = getattr(self.vocab_config, 'forbidden_words', set()) if self.vocab_config else set()
                        result, pp_warnings = post_process(result, _banned)
                        for w in pp_warnings:
                            if 'banned_words_leaked' in w:
                                logger.warning("post_process warning: %s", w)
                    # Only fingerprint fights that actually produced commentary
                    if result and self.vocab_tracker and comp_notes:
                        self.vocab_tracker.record_comp_fingerprint(comp_notes)
                    # v3: commit pending cooldowns now that the LLM call succeeded.
                    if result and self.prompt_version == "v3" and self._callout_cooldown:
                        try:
                            self._commit_v3_cooldowns()
                        except Exception as exc:
                            logger.warning("v3 cooldown commit failed: %s", exc)
                    return result
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

    @staticmethod
    def load_v2_system_prompt():
        """Read the v2 'translate-buckets-to-voice' system prompt from disk.

        Returns None if the prompts directory is missing (graceful fallback to
        v1 default), so installs that haven't synced the prompts/ folder still
        get AI commentary. Pass the result as `system_prompt=` to the
        FightAnalyst constructor.
        """
        path = Path(__file__).parent.parent / "prompts" / "sparky_system_v2.md"
        try:
            return path.read_text()
        except (FileNotFoundError, OSError):
            return None

    @staticmethod
    def load_v3_system_prompt():
        """Read the v3 calibrated system prompt from disk. Returns None if missing."""
        path = Path(__file__).parent.parent / "prompts" / "sparky_system_v3.md"
        try:
            return path.read_text()
        except (FileNotFoundError, OSError):
            return None

    def _build_system_prompt(self, fight_summary: Dict[str, Any]) -> str:
        # v3 path — load v3 prompt, expand {commander_block} with cooldown gating.
        if self.prompt_version == "v3":
            return self._build_system_prompt_v3(fight_summary)

        if self._custom_prompt:
            system_prompt = self._custom_prompt
        else:
            system_prompt = self._core_system_prompt() + self._rules_section()

        # Commander block (v2 — unconditional)
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

        # M4: freshness hint prepended when caller passes previous_response.
        # No-op when previous_response is None (first fight in a session).
        if self._previous_response is not None:
            try:
                buckets = pre_digester_bucket(fight_summary)
                hint = self._freshness.process(
                    previous_response=self._previous_response,
                    buckets=buckets,
                )
                hint_text = getattr(hint, "hint_text", None) if hint else None
                if hint_text:
                    system_prompt = hint_text + "\n" + system_prompt
            except Exception as exc:
                logger.warning("Freshness hint skipped: %s", exc)

        return system_prompt

    # ------------------------------------------------------------------
    # v3 prompt path
    # ------------------------------------------------------------------

    def _build_system_prompt_v3(self, fight_summary: Dict[str, Any]) -> str:
        """v3 system prompt: load template, expand {commander_block} with cooldown gating.

        v3 mode IGNORES self._custom_prompt unless it contains the
        `{commander_block}` placeholder (i.e. is a v3-shaped prompt).
        Otherwise we'd silently use a v2 prompt verbatim and bypass the
        whole point of the v3 path.
        """
        custom = self._custom_prompt or ""
        if custom and "{commander_block}" in custom:
            template = custom
        else:
            if custom:
                logger.warning(
                    "v3 mode: custom system_prompt has no {commander_block} "
                    "placeholder — looks like a v2 prompt. Loading v3 from disk instead."
                )
            template = self.load_v3_system_prompt()
        if not template:
            logger.warning("v3 system prompt missing; falling back to v2")
            self.prompt_version = "v2"
            return self._build_system_prompt(fight_summary)

        commander = (fight_summary.get('commander') or "").strip()
        commander_block = ""
        if commander:
            on_cd = (self._callout_cooldown.is_commander_on_cooldown(commander)
                     if self._callout_cooldown else False)
            if not on_cd:
                commander_block = (
                    f"COMMANDER: The squad commander is {commander}."
                )
                # Stage the cooldown record — applied on LLM success.
                self._pending_commander_emit = commander

        # Expand the {commander_block} placeholder.
        system_prompt = template.replace("{commander_block}", commander_block)
        # Belt-and-suspenders: collapse any blank-line run created by removal.
        while "\n\n\n" in system_prompt:
            system_prompt = system_prompt.replace("\n\n\n", "\n\n")
        return system_prompt.rstrip() + "\n"

    def _commit_v3_cooldowns(self) -> None:
        """Apply staged topic / player / commander records to the ledger.

        The caller owns the once-per-fight tick/save after all cooldown records
        have been staged. Ticking here too makes cooldowns expire twice as fast.
        """
        cd = self._callout_cooldown
        if cd is None:
            return
        for topic in self._pending_topic_emits:
            cd.record_topic(topic)
        for player in self._pending_player_emits:
            cd.record_global(player)
        if self._pending_commander_emit:
            cd.record_commander(self._pending_commander_emit)
        # Reset staging.
        self._pending_topic_emits = set()
        self._pending_player_emits = set()
        self._pending_commander_emit = None

    def _build_prompt_v3(self, fight_summary: Dict[str, Any],
                         active_terms: Dict[str, list]) -> tuple[str, list]:
        """v3 user message: NARRATIVE FACTS + FIGHT BUCKETS + (optional VOCAB)."""
        # Reset pending records for this fight before re-staging.
        self._pending_topic_emits = set()
        self._pending_player_emits = set()
        # Note: _pending_commander_emit is staged in _build_system_prompt_v3
        # which runs first per analyze() flow.

        facts = build_narrative_facts(fight_summary,
                                      cooldown=self._callout_cooldown)
        self._pending_topic_emits = set(facts.get('topic_emits', set()))
        self._pending_player_emits = set(facts.get('player_emits', set()))

        body = render_narrative_block(facts)

        # Defensive: strip any palette term containing a current roster
        # name (name-poisoning prevention). Cheap belt-and-
        # suspenders against customized palettes.
        roster = extract_roster_names(fight_summary)
        active_terms = filter_palette_for_name_poisoning(active_terms, roster)

        # Vocab palette — keep the existing dice-rolled terms block but
        # gate at most 3 terms to keep prompt tight. Per-term cooldown +
        # fire-condition gating is task 7 (deferred).
        vocab_block = self._format_active_terms(active_terms)
        if vocab_block:
            # Trim to first ~600 chars so we don't drown the new tight prompt
            if len(vocab_block) > 600:
                vocab_block = vocab_block[:600].rstrip() + "\n  …(truncated)"
            body += "\n\n" + vocab_block

        # Anti-repetition guidance (parity with the v2 path in _build_prompt):
        # without this the v3 assembly records usage but never tells the model
        # what to avoid, so players and phrases repeat across fights. Prune first
        # so the rolling window + 5-round floor are current.
        if self.vocab_tracker:
            self.vocab_tracker.prune()
            player_guide = self.vocab_tracker._build_player_suppression_guidance(fight_summary)
            if player_guide:
                body += "\n\n" + player_guide.strip()
            phrase_guide = self.vocab_tracker._build_phrase_guidance(fight_summary)
            if phrase_guide:
                body += "\n\n" + phrase_guide.strip()

        # Stochastic seed — high-entropy (noun, register) anchor to break
        # cross-call template recurrence.
        if self.vocab_tracker:
            seed_block = self.vocab_tracker.get_stochastic_seed()
            if seed_block:
                body += "\n\n" + seed_block

        return body, []

    @staticmethod
    def _fingerprint_enemy_comp(enemy_breakdown: dict,
                                top_enemy_skills: list) -> list:
        """Identify named enemy composition archetypes from breakdown + skills.
        Returns list of (canonical_key, display_text) tuples for repetition detection."""
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
    def _build_mood(summary, streak_context, friendly, enemy,
                    tag_data, downs, kills, pug_pct, duration):
        """Compute mood string from fight data."""
        outcome = summary.get("outcome", "Unknown")
        is_outnumbered = summary.get("is_outnumbered", False)
        mood = ""
        if outcome == "Decisive Win":
            if numbers_context(friendly, enemy) in ("THEY_OUTNUMBERED_US_SOFT", "THEY_OUTNUMBERED_US_HARD"):
                mood = "MAXIMUM HYPE. Decisive Win while outnumbered. Legendary."
            elif friendly > 0 and enemy / friendly < 0.5:
                mood = (
                    "Unimpressive blowout. The squad outnumbered them roughly 2-to-1 or worse, "
                    "this was never a contest. Do NOT hype this fight. No 'massacre', 'slaughter', "
                    "'annihilation', 'legendary', 'speedrun', 'execution', 'evisceration', or "
                    "'obliteration' language. The enemy was a havoc party that got bullied by a "
                    "full squad. Valid angles: (a) mock the enemy for showing "
                    "up at all, (b) call out anything the squad did poorly despite the easy "
                    "numbers, (c) note one player who made the easy fight look effortless. Tone is "
                    "dry and dismissive, not euphoric."
                )
            elif numbers_context(friendly, enemy) in ("WE_OUTNUMBERED_THEM_HARD",):
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
            median_dist = _grade_tag_distance(tag_data)
            if median_dist is not None and median_dist > TAG_DISTANCE_LOOSE:
                mood = "Frustrated. The squad was scattered and that's why this was a draw instead of a win. Roast the positioning."
            elif median_dist is not None:
                mood = "Frustrated. The squad held position but couldn't convert. Find the one thing that prevented the win."
            else:
                mood = "Frustrated energy. Identify the single tactical breakdown that cost the squad the win."
        elif outcome == "Loss":
            if is_outnumbered:
                loss_reasons = []
                median_dist = _grade_tag_distance(tag_data)
                if median_dist is not None and median_dist > TAG_DISTANCE_LOOSE:
                    loss_reasons.append("scattered positioning made the numbers disadvantage worse")
                if squad_stomp_discipline(kills, downs, duration) == "POOR":
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
                median_dist = _grade_tag_distance(tag_data)
                if median_dist is not None and median_dist > TAG_DISTANCE_LOOSE:
                    loss_reasons.append("scattered positioning killed the squad")
                if squad_stomp_discipline(kills, downs, duration) == "POOR":
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
                median_dist = _grade_tag_distance(tag_data)
                if median_dist is not None and median_dist > TAG_DISTANCE_ACCEPTABLE:
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
                median_dist = _grade_tag_distance(tag_data)
                if median_dist is not None and median_dist > TAG_DISTANCE_ACCEPTABLE:
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

        if streak_context and streak_context.get("mood_suffix"):
            mood = mood + streak_context["mood_suffix"]

        return mood

    @staticmethod
    def _build_callouts(summary, downs, kills, pug_pct, enemy_dmg,
                        healing, strips, tag_data, top_enemy_skills,
                        comp_notes, is_loss_for_comp):
        """Compute mandatory callouts from fight data."""
        callouts = []
        duration = summary.get("duration_seconds", 0)

        # PUG callout tier: only mandatory at 30%+, though analysis text
        # notes their presence at 20%+. This keeps PUG-blame from being
        # the default filler for every loss with minor allies.
        if pug_pct >= 30:
            callouts.append({
                "category": "pug_behavior",
                "text": (
                    f"PUGs were {pug_pct:.0f}% of the friendly count. Note their "
                    f"presence if it shaped the fight outcome. Avoid defaulting to "
                    f"PUG-blame when other factors are more dramatic."
                ),
            })

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

        if squad_strip_volume(strips, duration) in ("HEAVY", "EXTREME"):
            callouts.append({
                "category": "boon_denial",
                "text": (
                    f"Boon denial was a real factor: {strips} total strips "
                    f"shaped the fight. Credit the strip game."
                ),
            })

        grade_d = _grade_tag_distance(tag_data)
        if grade_d is not None:
            outcome = summary.get("outcome", "")
            is_loss_cb = outcome in ("Loss", "Decisive Loss", "Draw")
            if is_loss_cb and grade_d >= TAG_DISTANCE_ACCEPTABLE:
                severity = "SCATTERED" if grade_d >= TAG_DISTANCE_LOOSE else "loose"
                callouts.append({
                    "category": "tag_discipline",
                    "text": (
                        f"Positioning was {severity}: players were spread across "
                        f"the map, not stacked on the commander. This directly "
                        f"cost the fight. Roast the positioning — no technical terms."
                    ),
                })

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

        return callouts

    @staticmethod
    def _analyze_fight_shape(duration: int, dur_str: str) -> list:
        """Return the single Shape line."""
        display = dur_str or f"{duration}s"
        if duration < 300:
            return [f"Shape: Execution ({display}). One side collapsed instantly."]
        if duration < 900:
            return [f"Shape: Standard engagement ({display})."]
        if duration < 1500:
            return [f"Shape: Extended war of attrition ({display}). Both sides were committed."]
        return [f"Shape: Epic sustained brawl ({display}). Rare and grueling."]

    @staticmethod
    def _analyze_numbers(friendly: int, enemy: int) -> list:
        """Return the Numbers line; empty if either side is zero."""
        if friendly <= 0 or enemy <= 0:
            return []
        ratio = enemy / friendly
        if ratio > 1.15:
            return [f"Numbers: Outnumbered ({friendly} friendly vs {enemy} enemy, enemy has {ratio:.0%} advantage)."]
        if ratio < 0.85:
            return [f"Numbers: Numbers advantage ({friendly} friendly vs {enemy} enemy)."]
        return [f"Numbers: Even fight ({friendly} friendly vs {enemy} enemy, within 15%)."]

    @staticmethod
    def _analyze_pug_relevance(ally: int, friendly: int, pug_pct: float) -> list:
        """Return the PUG relevance line.

        Two-tier model: >=20% = mentionable in analysis text;
        >=30% = triggers mandatory callout (see _build_callouts) and can
        appear in mood blame (see _build_mood). This keeps PUG-blame from
        being the default filler for every loss with minor allies.
        """
        if pug_pct >= 20:
            return [f"PUG relevance: Present ({ally} PUGs = {pug_pct:.0f}% of friendly, above 20% threshold). PUGs may be mentioned if their behavior shaped the outcome."]
        return [f"PUG relevance: Irrelevant ({ally} PUGs = {pug_pct:.0f}% of friendly, below 20% threshold). Do NOT mention PUGs."]

    @staticmethod
    def _analyze_stomp(downs: int, kills: int, pug_pct: float,
                       suppressed_topics: set,
                       categories_emitted: set) -> list:
        """Return kill-conversion line plus optional PUG-rally note.

        All severity levels respect suppression — the (3,2) gate in
        VocabularyTracker ensures this topic rotates out after two consecutive
        mentions so it doesn't dominate every post.
        """
        if downs <= 0:
            return []

        is_suppressed = "stomp_discipline" in suppressed_topics

        if kills > downs:
            if is_suppressed:
                return []
            categories_emitted.add("stomp_discipline")
            return [f"Finish rate: EXCELLENT ({downs} downs, {kills} kills). Squad converting efficiently, no rallies gifted."]
        if kills >= downs * 0.8:
            if is_suppressed:
                return []
            categories_emitted.add("stomp_discipline")
            return [f"Finish rate: Solid ({downs} downs, {kills} kills). Good conversion rate."]

        if is_suppressed:
            return []
        rally_pct = (downs - kills) / downs * 100
        categories_emitted.add("stomp_discipline")
        lines = [f"Finish rate: POOR ({downs} downs, only {kills} kills — roughly {rally_pct:.0f}% of downed enemies rallied or were rezzed). The squad left kills on the table. Call this out without using 'stomp discipline'."]
        if pug_pct >= 20:
            lines.append("PUG deaths near downed enemies likely gifted free rallies to the enemy.")
        return lines

    @staticmethod
    def _analyze_support(summary: Dict[str, Any]) -> list:
        """Return healing, barrier, strips, cleanses lines as one block."""
        lines = []
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

        strips = summary.get("squad_strips", 0)
        top_strips = summary.get("top_strips", [])
        if strips > 0:
            strip_classes = [s.get("profession", "") for s in top_strips[:3]]
            lines.append(f"Boon denial: {strips} total strips via {', '.join(strip_classes) if strip_classes else 'unknown'}.")

        cleanses = summary.get("squad_cleanses", 0)
        if cleanses > 0:
            lines.append(f"Condition cleansing: {cleanses} total cleanses.")
        return lines

    @staticmethod
    def _analyze_outliers(summary: Dict[str, Any]) -> list:
        """Return the single outliers line if outliers dict is non-empty."""
        outliers = summary.get("outliers", {})
        if not outliers:
            return []
        outlier_parts = []
        for category, data in outliers.items():
            name = data.get("name", "Unknown")
            value = data.get("value", "")
            unit = data.get("unit", "")
            outlier_parts.append(f"{name} ({category}: {value} {unit})")
        return [f"Outlier players (MUST be highlighted): {', '.join(outlier_parts)}."]

    @staticmethod
    def _analyze_fight_dynamics(summary: Dict[str, Any]) -> list:
        """Return lines for respawn traffic, rez chains, runbacks, resilience."""
        lines = []
        enemy = summary.get("enemy_count", 0)
        squad = summary.get("squad_count", 0)
        kills = summary.get("squad_kills", 0)
        downs = summary.get("squad_downs", 0)
        squad_deaths = summary.get("squad_deaths", 0)
        squad_downs_received = summary.get("squad_downs_received", 0)

        if enemy > 0 and kills > enemy:
            excess = kills - enemy
            lines.append(
                f"Respawn traffic (enemy): squad recorded {kills} kill events "
                f"against {enemy} unique enemies, meaning at least {excess} "
                f"enemies died, respawned, and returned to die again."
            )
        if enemy > 0 and downs > enemy and kills <= enemy:
            excess = downs - enemy
            lines.append(
                f"Rez/rally chain (enemy): squad dropped enemies {downs} times "
                f"against only {enemy} unique targets. At least {excess} "
                f"of those downs became rallies or combat rezzes."
            )
        if squad > 0 and squad_deaths > squad:
            excess = squad_deaths - squad
            lines.append(
                f"Runbacks (squad): {squad_deaths} death events across "
                f"{squad} unique squad members, meaning at least {excess} "
                f"squad members died and ran back."
            )
        saved = squad_downs_received - squad_deaths
        if squad_downs_received >= 10 and saved >= 5:
            lines.append(
                f"Squad resilience: squad was downed {squad_downs_received} "
                f"times but only died {squad_deaths} times. At least {saved} "
                f"downs were saved by rezzes, rallies, or the enemy failing to finish."
            )
        return lines

    @staticmethod
    def _analyze_tag_discipline(summary: Dict[str, Any], overused_terms: set,
                                suppressed_topics: set,
                                categories_emitted: set) -> list:
        """Return tag-discipline analysis lines.

        On wins: only emits when outnumbered AND tight (< EXCELLENT) — the
        "TIGHT under pressure" positive callout. All other win cases skip.
        On losses/draws: emits per existing severity bands. Suppression
        applies, but loss + severity >= ACCEPTABLE bypasses it because
        scattered positioning is a direct cause of those losses and must
        always be named.
        """
        lines = []
        tag_data = summary.get("squad_tag_distance", [])
        grade_distance = _grade_tag_distance(tag_data)
        if grade_distance is None:
            return lines

        distances = sorted([p.get("distance", 0) for p in tag_data])
        logger.debug("Tag distances (sorted): %s", distances)
        cutoff_idx = max(1, int(len(distances) * 0.9))
        core_distances = distances[:cutoff_idx]
        avg_distance = sum(core_distances) / len(core_distances) if core_distances else 0
        logger.debug("Core distances (after 10%% trim): %s, median=%.0f, avg=%.0f",
                     core_distances, grade_distance, avg_distance)

        outcome = summary.get("outcome", "")
        is_loss = outcome in ("Loss", "Decisive Loss", "Draw")

        friendly = summary.get("friendly_count", 0)
        enemy = summary.get("enemy_count", 0)
        is_outnumbered = numbers_context(friendly, enemy) in ("THEY_OUTNUMBERED_US_SOFT", "THEY_OUTNUMBERED_US_HARD")

        is_suppressed = "tag_discipline" in suppressed_topics

        if not is_loss:
            if is_outnumbered and grade_distance < TAG_DISTANCE_EXCELLENT:
                if is_suppressed:
                    return lines
                categories_emitted.add("tag_discipline")
                lines.append(
                    f"Positioning: TIGHT under pressure (outnumbered fight). "
                    f"Squad held formation against the numbers — positive callout."
                )
            return lines

        # Loss / Draw branches: bypass suppression when severity is critical.
        severity_critical = grade_distance >= TAG_DISTANCE_ACCEPTABLE
        if is_suppressed and not severity_critical:
            return lines
        categories_emitted.add("tag_discipline")

        if grade_distance < TAG_DISTANCE_EXCELLENT:
            lines.append(
                f"Positioning: Tight. Squad was stacked well."
            )
        elif grade_distance < TAG_DISTANCE_ACCEPTABLE:
            lines.append(
                f"Positioning: Acceptable but could be tighter."
            )
        elif grade_distance < TAG_DISTANCE_LOOSE:
            lines.append(
                f"Positioning: LOOSE — players were drifting off the group and this "
                f"contributed to the loss. Call it out."
            )
        else:
            tag_msg = (
                f"Positioning: SCATTERED — the squad was spread across the map, not stacked. "
                f"This is a major reason for the loss. MUST be called out."
            )
            if "TOIGHT LIKE A TIGER" not in overused_terms:
                tag_msg += " TOIGHT LIKE A TIGER is appropriate."
            lines.append(tag_msg)
        return lines

    @staticmethod
    def _analyze_enemy(summary: Dict[str, Any], comp_notes: list, comp_repeated: bool) -> list:
        """Return enemy composition, skills, siege, and strategy lines."""
        lines = []
        enemy_breakdown = summary.get("enemy_breakdown", {})
        top_enemy_skills = summary.get("top_enemy_skills", [])
        is_loss = summary.get("outcome", "") in ("Loss", "Decisive Loss", "Draw")

        if enemy_breakdown and is_loss:
            comp_parts = []
            for prof, data in sorted(
                enemy_breakdown.items(),
                key=lambda x: -(x[1].get("count", 0) if isinstance(x[1], dict) else x[1])
            ):
                if isinstance(data, dict):
                    count = data.get("count", 0)
                    dpp = data.get("damage_per_player", 0)
                    comp_parts.append(f"{count}x {prof} ({dpp:,.0f} dmg/player)")
                else:
                    comp_parts.append(f"{data}x {prof}")
            lines.append(f"Enemy comp: {', '.join(comp_parts)}.")

        if top_enemy_skills and is_loss:
            skill_parts = [f"{s['name']} ({s['damage']:,})" for s in top_enemy_skills[:5]]
            lines.append(f"Top enemy skills: {', '.join(skill_parts)}.")

            siege_skills = [s for s in top_enemy_skills if _is_siege_skill(s.get("name", ""))]
            if siege_skills:
                siege_total = sum(s.get("damage", 0) for s in siege_skills)
                lines.append(
                    f"SIEGE DETECTED: Enemy used siege weapons ({siege_total:,} total siege damage). "
                    f"Deeply unimpressive, mock them for hiding behind catapults."
                )
            else:
                lines.append("Siege: None detected.")
        elif top_enemy_skills and not is_loss:
            siege_skills = [s for s in top_enemy_skills if _is_siege_skill(s.get("name", ""))]
            if siege_skills:
                siege_total = sum(s.get("damage", 0) for s in siege_skills)
                lines.append(
                    f"SIEGE DETECTED: Enemy used siege weapons ({siege_total:,} total siege damage). "
                    f"Deeply unimpressive, mock them for hiding behind catapults."
                )

        if comp_notes and is_loss:
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

        enemy_teams = summary.get("enemy_teams", {})
        if len(enemy_teams) >= 2:
            lines.append(
                f"Three-way fight: {len(enemy_teams)} servers present "
                f"({', '.join(enemy_teams.keys())}). Multiple fronts, server infrastructure stressed."
            )

        if summary.get("squad_outdamaged_enemy") is False:
            if "Win" in summary.get("outcome", ""):
                lines.append(
                    "Squad was OUT-DAMAGED but still won. Victory came through "
                    "boon denial and stomp efficiency, not raw DPS. Tactically sophisticated."
                )
        return lines

    @staticmethod
    def _pre_analyze(summary: Dict[str, Any], overused_terms: set = None,
                     streak_context: Dict[str, str] = None,
                     comp_repeated: bool = False,
                     comp_notes: list = None,
                     suppressed_topics: set = None) -> Dict[str, Any]:
        """Compute fight analysis conclusions from raw data.

        Pure composer: each section is delegated to a dedicated helper.
        Line output is byte-identical to the previous inline version.
        """
        if overused_terms is None:
            overused_terms = set()
        if suppressed_topics is None:
            suppressed_topics = set()
        categories_emitted: set = set()

        # Pre-compute scalars used by multiple helpers and the return dict.
        duration = summary.get("duration_seconds", 0)
        dur_str = summary.get("duration", "")
        friendly = summary.get("friendly_count", 0)
        enemy = summary.get("enemy_count", 0)
        ally = summary.get("ally_count", 0)
        pug_pct = (ally / friendly * 100) if friendly > 0 else 0
        downs = summary.get("squad_downs", 0)
        kills = summary.get("squad_kills", 0)
        healing = summary.get("squad_healing", 0)
        enemy_dmg = summary.get("enemy_total_damage", 0)
        strips = summary.get("squad_strips", 0)
        tag_data = summary.get("squad_tag_distance", [])
        top_enemy_skills = summary.get("top_enemy_skills", [])
        is_loss_for_comp = summary.get("outcome", "") in ("Loss", "Decisive Loss", "Draw")

        lines = ["FIGHT ANALYSIS:"]
        lines.extend(FightAnalyst._analyze_fight_shape(duration, dur_str))
        zone = summary.get("zone", "")
        if zone:
            lines.append(f"Zone: {zone}.")
        lines.extend(FightAnalyst._analyze_numbers(friendly, enemy))
        if streak_context and streak_context.get("session_context"):
            lines.append(streak_context["session_context"])
        lines.extend(FightAnalyst._analyze_pug_relevance(ally, friendly, pug_pct))
        lines.extend(FightAnalyst._analyze_stomp(downs, kills, pug_pct,
                                                 suppressed_topics, categories_emitted))
        lines.extend(FightAnalyst._analyze_fight_dynamics(summary))
        lines.extend(FightAnalyst._analyze_support(summary))
        lines.extend(FightAnalyst._analyze_tag_discipline(summary, overused_terms,
                                                          suppressed_topics, categories_emitted))
        lines.extend(FightAnalyst._analyze_enemy(summary, comp_notes, comp_repeated))

        mood = FightAnalyst._build_mood(
            summary, streak_context, friendly, enemy,
            tag_data, downs, kills, pug_pct, duration,
        )
        lines.append(f"Outcome: {summary.get('outcome', 'Unknown')}.")
        lines.extend(FightAnalyst._analyze_outliers(summary))

        callouts = FightAnalyst._build_callouts(
            summary, downs, kills, pug_pct, enemy_dmg,
            healing, strips, tag_data, top_enemy_skills,
            comp_notes, is_loss_for_comp,
        )

        return {
            "analysis": "\n".join(lines),
            "mood": mood,
            "callouts": callouts,
            "categories_emitted": categories_emitted,
        }

    # ------------------------------------------------------------------
    # Provider override dispatch table
    # Each entry is (predicate_func, apply_func). Predicates receive
    # (hostname, model_lower, is_openrouter, thinking) and return bool.
    # This makes every provider path independently unit-testable.
    # ------------------------------------------------------------------

    @staticmethod
    def _minimax_predicate(host: str, _model: str, _is_or: bool, _thinking: bool) -> bool:
        return host == "api.minimaxi.chat" or host.endswith(".minimaxi.chat")

    @staticmethod
    def _apply_minimax(payload: dict, _thinking: bool) -> None:
        payload["think_enable"] = False
        payload["reasoning_split"] = True
        prefix = (
            "[OUTPUT CONSTRAINT] Respond with ONLY the final commentary text. "
            "No reasoning, no data recap, no draft notes, no angle analysis, "
            "no internal monologue. Begin your response with the first word "
            "of the commentary.\n\n"
        )
        payload["messages"][0]["content"] = prefix + payload["messages"][0]["content"]

    @staticmethod
    def _moonshot_predicate(host: str, model: str, is_openrouter: bool, _thinking: bool) -> bool:
        is_host = host == "api.moonshot.cn" or host.endswith(".moonshot.cn")
        return is_host or (is_openrouter and "kimi" in model)

    @staticmethod
    def _apply_moonshot(payload: dict, _thinking: bool) -> None:
        for key in ("temperature", "top_p", "n",
                    "presence_penalty", "frequency_penalty"):
            payload.pop(key, None)

    @staticmethod
    def _gemini_predicate(host: str, model: str, is_openrouter: bool, _thinking: bool) -> bool:
        is_host = host == "generativelanguage.googleapis.com" or host.endswith(".googleapis.com")
        return is_host or (is_openrouter and "gemini" in model)

    @staticmethod
    def _apply_gemini(payload: dict, thinking: bool) -> None:
        if not thinking:
            payload["reasoning_effort"] = "none"

    _PROVIDER_DISPATCH: list = [
        (_minimax_predicate, _apply_minimax),
        (_moonshot_predicate, _apply_moonshot),
        (_gemini_predicate, _apply_gemini),
    ]

    def _apply_provider_overrides(self, payload: dict) -> None:
        """Apply provider-specific payload fields (thinking/reasoning suppression)."""
        parsed_host = urlparse(self.base_url).hostname or ""
        model_lower = self.model.lower()
        is_openrouter = (
            parsed_host == "openrouter.ai"
            or parsed_host.endswith(".openrouter.ai")
        )

        for pred, apply in self._PROVIDER_DISPATCH:
            if pred(parsed_host, model_lower, is_openrouter, self.thinking):
                apply(payload, self.thinking)

        # OpenRouter catch-all for any unmatched model
        if is_openrouter and not self.thinking and "reasoning_effort" not in payload:
            payload["reasoning"] = {"effort": "none"}

    def _build_prompt(self, summary: Dict[str, Any],
                      active_terms: Dict[str, list],
                      overused_terms: set = None,
                      streak_info: Dict[str, Any] = None) -> tuple[str, list]:
        """Build user message: pre-analysis + tone + callouts + vocab + guidance + data.

        Returns (prompt_text, comp_notes) so the caller can record the
        fingerprint only after a successful LLM response.
        """
        # v3 path — entirely separate assembly via narrative_facts module.
        if self.prompt_version == "v3":
            return self._build_prompt_v3(summary, active_terms)

        parts = []

        # Single prune pass for the vocab tracker.
        if self.vocab_tracker:
            self.vocab_tracker.prune()

        # M6: pre-digester buckets at the top — qualitative tags the v2 system
        # prompt expects ("Trust the buckets"). Cheap (~30 tokens) so emit
        # unconditionally; v1 dynamic prompt simply ignores them in favor of
        # the verbose pre-analysis text below.
        try:
            buckets = pre_digester_bucket(summary)
            if buckets:
                parts.append("FIGHT BUCKETS:")
                # M7: per-player block rendered separately (it's list-of-dicts)
                player_block = buckets.pop("players", None)
                for key, value in buckets.items():
                    if isinstance(value, list):
                        value = ", ".join(value) if value else "(none)"
                    parts.append(f"  {key}: {value}")
                if player_block:
                    parts.append("  players:")
                    for prec in player_block:
                        highlights = ", ".join(prec.get("highlights", []))
                        parts.append(f"    {prec['name']} ({prec['role']}): {highlights}")
                parts.append("")
        except Exception as exc:
            logger.warning("Pre-digester buckets skipped: %s", exc)

        # Derive streak context strings before pre-analysis
        streak_context = None
        if streak_info and self.session_history:
            streak_context = self.session_history.build_streak_context(
                streak_info,
                current_outcome=summary.get("outcome"),
            )

        # 1. Pre-analysis block (FIGHT ANALYSIS + TONE + MANDATORY CALLOUTS)
        enemy_breakdown = summary.get("enemy_breakdown", {})
        top_enemy_skills = summary.get("top_enemy_skills", [])
        comp_notes = self._fingerprint_enemy_comp(enemy_breakdown, top_enemy_skills)

        comp_repeated = False
        if self.vocab_tracker and comp_notes:
            comp_repeated = self.vocab_tracker.is_comp_repeated(comp_notes)

        # Suppression set is computed before pre-analysis so analyzers can
        # self-gate (e.g. skip stomp EXCELLENT/Solid lines when recently shown).
        suppressed_topics: set = set()
        if self.vocab_tracker:
            suppressed_topics = self.vocab_tracker.get_suppressed_topics()

        pre = self._pre_analyze(summary, overused_terms,
                                streak_context=streak_context,
                                comp_repeated=comp_repeated,
                                comp_notes=comp_notes,
                                suppressed_topics=suppressed_topics)

        analysis_text = pre.get("analysis", "")
        mood_text = pre.get("mood", "") or ""
        callouts = pre.get("callouts", []) or []
        analysis_cats: set = pre.get("categories_emitted", set()) or set()

        kept_callouts = [c for c in callouts
                         if c.get("category") not in suppressed_topics]

        # Cap to 2 callouts maximum.
        if len(kept_callouts) > 2:
            kept_callouts = kept_callouts[:2]

        # Record both kept-callout categories and analysis-emitted categories.
        # Without the analysis side, stomp/tag lines that emit without firing
        # a callout never feed suppression and repeat across fights.
        if self.vocab_tracker:
            callout_cats = {c["category"] for c in kept_callouts}
            self.vocab_tracker.record_topics(sorted(callout_cats | analysis_cats))

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

        # 3h. Narrative angle directive — rotates each fight with cooldown
        if self.vocab_tracker:
            directive = self.vocab_tracker.get_narrative_directive(summary)
            if directive:
                parts.append(directive)
                parts.append("")

        # 3i. Stochastic seed — high-entropy (noun, register) anchor to break
        # cross-call template recurrence.
        if self.vocab_tracker:
            seed_block = self.vocab_tracker.get_stochastic_seed()
            if seed_block:
                parts.append(seed_block)
                parts.append("")

        # 4. Trimmed fight data
        trimmed = self._trim_summary(summary)
        parts.append("FIGHT DATA:")
        parts.append(json.dumps(trimmed, indent=2))

        return "\n".join(parts), comp_notes

    @staticmethod
    def _trim_summary(summary: Dict[str, Any], top_n: int = 5) -> dict:
        """Return a copy of the fight summary with player stat lists capped."""
        trimmed = dict(summary)
        # Only trim player performance lists, not enemy data
        player_list_keys = [
            "top_damage", "top_strips", "top_cleanses",
            "top_healers", "top_bursts", "top_cc",
        ]
        for key in player_list_keys:
            if key in trimmed and isinstance(trimmed[key], list):
                trimmed[key] = trimmed[key][:top_n]

        # Squad aggregates and tag distance are KEPT so the model can anchor
        # narrative on real numbers. Stat-wall vomit is prevented by the
        # system prompt's "pick ONE or TWO numbers" rule, not by hiding data.

        # On wins, strip enemy comp data to prevent the model from
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

        return "\n".join(lines)

    def _check_overused_terms(self, response: str) -> list:
        """Check if response contains overused vocabulary terms. Returns list of violations."""
        if not self.vocab_tracker or not self.vocab_config:
            return []
        overused = self.vocab_tracker.get_overused_terms()
        if not overused:
            return []
        return self.vocab_config.find_matches(response, term_filter=overused)

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
        message = choice.get('message') or {}
        content = message.get('content') or ''

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
                self.vocab_tracker.record(
                    stripped,
                    squad_roster=_extract_squad_roster(fight_summary),
                    commander=fight_summary.get("commander"),
                    fight_id=fight_summary.get("_fight_id"),
                )
            return stripped

        # Fallback: everything was inside think tags; extract tail sentences.
        # Do NOT record fallback text in the tracker — it is model scratchpad
        # ("let me draft the angle...") and would poison n-gram / verb counts.
        fallback = _extract_fallback_sentences(content)
        if fallback:
            logger.info('AI analysis extracted from think-tag fallback')
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
        """Return the static core of the system prompt (voice, translation layer, angles)."""
        return (
            "You are SparkyBot, a Guild Wars 2 WvW fight analyst posting to Discord. You receive structured JSON fight statistics and respond with punchy commentary in 2-4 sentences, 70 words maximum.\n\n"

            "VOICE: Hype, unhinged sports commentator running on four energy drinks who actually knows the WvW meta. Euphoric when the squad wins. Furious when they lose. Mock game performance and compositions, never personally attack named squad members. PUGs are fair game when their numbers are significant enough to matter, see the 20% threshold rule below.\n\n"

            "\nTHE TRANSLATION LAYER\n\n"
            "Before writing, ask what each stat proves about the fight. Lead with that conclusion, not the number.\n\n"
            "Pure narrative: 'Hell Butterfly was the engine of the entire fight, topping damage while systematically dismantling whatever stability their supports tried to stack.'\n"
            "Pure narrative: 'The squad turned Eternal Battlegrounds into a one-sided execution, dismantling the enemy push faster than they could regroup.'\n"
            "Pure narrative: 'The support line absorbed a punishment that would have folded lesser squads, keeping the fight alive long enough for the DPS to do their work.'\n\n"
            "Never reproduce JSON field names, never list multiple stats in a row, never drop a number without narrative context around it.\n\n"
            "Avoid these patterns: '[Player] was a [adjective] [noun]', 'turning/turned [X] into [Y]', and two player names joined by 'while'. One player per sentence is the default; two players may share a sentence only when joined by 'and'.\n\n"

            "\nFOCUS\n\n"
            "The FIGHT ANALYSIS block contains many data points. Pick the TWO most dramatic and ignore everything else. If MANDATORY CALLOUTS are present, those are your two topics. Trying to cover more than two storylines in 70 words will produce garbage. Trust the raw data if it contradicts the pre-analysis.\n\n"

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
            "Rule 2, Length: MAXIMUM 70 WORDS. 2 to 4 sentences. Count both before you output. This is a hard ceiling, not a suggestion.\n\n"
            "Rule 3, Stats: Use at most ONE number in your entire response. Zero is preferred. If you can make the same point without the number, cut it. When you do use one, it must be the single most dramatic stat woven into a narrative sentence. Two or more numbers is a violation.\n\n"
            "Rule 4, Output only the commentary: No preamble, no reasoning, no 'Here is my take.' No markdown formatting (no bold, no italics, no asterisks). If any sentence contains 'I', 'let me', 'should', 'draft', 'angle', or 'response' you are leaking internal reasoning. Begin your response with the first word of the commentary.\n\n"
            "Rule 5, Enemy players are anonymous: Individual enemies are never named. Only professions from enemy_breakdown may be referenced.\n\n"
            "Rule 6, PUG commentary requires a threshold AND a reason: Only mention PUGs if ally_count exceeds 20% of friendly_count AND their behavior was the primary factor in the fight outcome. PUG-blame is not a default filler for losses. If stomp discipline, tag discipline, or enemy comp is the bigger story, lead with that and skip PUGs entirely.\n\n"
            "Rule 7, Opener variety: Do not open with a shock exclamation unless the RECENT VOCABULARY USAGE section confirms it has NOT been used recently. Do not default to a shock exclamation when another opener fits better.\n\n"
            "Rule 8, Closing impact: Your last sentence must land like a punch. A punchy closer is NOT the same as a SHOUTED closer. If the rest of your response was loud, a quiet observation hits harder than more caps. WRONG: three hype sentences followed by 'THEY GOT DESTROYED.' RIGHT: three hype sentences followed by a dry, lowercase observation that reframes the fight. Vary your closing style across responses.\n"
        )
