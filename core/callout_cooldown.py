"""M7 — Callout cooldown tracker (v3 expansion).

Originally tracked (player, axis) cooldowns to stop a single player
from monopolizing the same outlier callout. v3 adds three more ledgers:

  - global_player : every-other-fight cap on a player's NAME appearing
                    in NARRATIVE FACTS regardless of category. Stacks on
                    top of per-axis cooldown.
  - topic         : per-topic cooldown (stomp_discipline, tag_discipline,
                    support_quality, pug_blame, comp_drama, etc.) so the
                    pre-digester doesn't ship the same sentence type
                    every fight.
  - commander     : commander-name cooldown that gates the system-prompt
                    {commander_block} injection (1-in-3 fights).

State persists across SparkyBot restarts via JSON on disk. Old single-
ledger files migrate to the new schema on load — existing entries land
in player_axis and the new ledgers start empty.

USAGE
    cooldown = CalloutCooldown(state_path)             # load on construct

    # per-axis (existing)
    if not cooldown.is_on_cooldown(name, axis):
        ...
    cooldown.record(name, axis)

    # global player cap (new)
    if not cooldown.is_globally_on_cooldown(name):
        ...
    cooldown.record_global(name)

    # topic (new)
    if not cooldown.is_topic_on_cooldown('stomp_discipline'):
        ...
    cooldown.record_topic('stomp_discipline')

    # commander (new)
    if not cooldown.is_commander_on_cooldown(commander_name):
        prompt += f"COMMANDER: ..."
    cooldown.record_commander(commander_name)

    cooldown.tick()                                    # once per fight at end
    cooldown.save()                                    # persist
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Cooldown values are stored as fight-counters that tick down at end of fight.
# Value N means "N fights of lockout including this one"; record N=4 then
# tick() at end of fight → 3 → 2 → 1 → drops. Eligible on fight N+4.

DEFAULT_COOLDOWN = 4              # 3-fight lockout (legacy default)
DEFAULT_GLOBAL_PLAYER_COOLDOWN = 3  # 2-fight lockout = every-other-fight cap
DEFAULT_TOPIC_COOLDOWN = 4        # 3-fight lockout for topics
DEFAULT_COMMANDER_COOLDOWN = 4    # 3-fight lockout = 1-in-3 fights for commander


class CalloutCooldown:
    def __init__(self, state_path: Optional[Path | str] = None,
                 cooldown_fights: int = DEFAULT_COOLDOWN,
                 global_player_cooldown: int = DEFAULT_GLOBAL_PLAYER_COOLDOWN,
                 topic_cooldown: int = DEFAULT_TOPIC_COOLDOWN,
                 commander_cooldown: int = DEFAULT_COMMANDER_COOLDOWN):
        self._cooldown_fights = cooldown_fights
        self._global_player_cooldown = global_player_cooldown
        self._topic_cooldown = topic_cooldown
        self._commander_cooldown = commander_cooldown
        self._state_path = Path(state_path) if state_path else None
        # Four independent ledgers — each maps key -> fights_remaining.
        # _entries (the per-axis ledger) keeps the original name for
        # backward compat with any existing call sites.
        self._entries: dict[str, int] = {}
        self._global_player_entries: dict[str, int] = {}
        self._topic_entries: dict[str, int] = {}
        self._commander_entries: dict[str, int] = {}
        if self._state_path and self._state_path.exists():
            self._load()

    @staticmethod
    def _key(name: str, axis: str) -> str:
        return f"{name}||{axis}"

    # ---- per-axis player cooldown (existing, unchanged) ----

    def is_on_cooldown(self, name: str, axis: str) -> bool:
        return self._entries.get(self._key(name, axis), 0) > 0

    def record(self, name: str, axis: str, fights: Optional[int] = None) -> None:
        n = fights if fights is not None else self._cooldown_fights
        self._entries[self._key(name, axis)] = n

    # ---- global player cap (new — every-other-fight rule) ----

    def is_globally_on_cooldown(self, name: str) -> bool:
        return self._global_player_entries.get(name, 0) > 0

    def record_global(self, name: str, fights: Optional[int] = None) -> None:
        """Record a global cap — call when a name SHIPS in NARRATIVE FACTS,
        regardless of which axis or category sentence it appeared in."""
        n = fights if fights is not None else self._global_player_cooldown
        self._global_player_entries[name] = n

    # ---- topic cooldown (new) ----

    def is_topic_on_cooldown(self, topic: str) -> bool:
        return self._topic_entries.get(topic, 0) > 0

    def record_topic(self, topic: str, fights: Optional[int] = None) -> None:
        n = fights if fights is not None else self._topic_cooldown
        self._topic_entries[topic] = n

    # ---- commander cooldown (new — gates system-prompt block injection) ----

    def is_commander_on_cooldown(self, commander_name: str) -> bool:
        return self._commander_entries.get(commander_name, 0) > 0

    def record_commander(self, commander_name: str,
                         fights: Optional[int] = None) -> None:
        n = fights if fights is not None else self._commander_cooldown
        self._commander_entries[commander_name] = n

    # ---- tick / persistence ----

    def tick(self) -> None:
        """Decrement every ledger by 1; drop zero-or-below entries."""
        def _tick(ledger: dict[str, int]) -> dict[str, int]:
            return {k: n - 1 for k, n in ledger.items() if n > 1}
        self._entries = _tick(self._entries)
        self._global_player_entries = _tick(self._global_player_entries)
        self._topic_entries = _tick(self._topic_entries)
        self._commander_entries = _tick(self._commander_entries)

    def snapshot(self) -> dict[str, dict[str, int]]:
        """Read-only multi-ledger view (for tests / debug)."""
        return {
            "player_axis":    dict(self._entries),
            "global_player":  dict(self._global_player_entries),
            "topic":          dict(self._topic_entries),
            "commander":      dict(self._commander_entries),
        }

    # ---- persistence ----

    def _load(self) -> None:
        try:
            with self._state_path.open('r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("CalloutCooldown: failed to load state: %s", exc)
            return

        # Migration: detect old flat schema {"entries": {...}} and
        # graduate it into the new player_axis ledger. New ledgers stay empty.
        if "entries" in data and "player_axis" not in data:
            logger.info("CalloutCooldown: migrating flat schema -> v3 ledgers")
            self._entries = self._coerce_entries(data.get("entries", {}))
            return

        self._entries = self._coerce_entries(data.get("player_axis", {}))
        self._global_player_entries = self._coerce_entries(
            data.get("global_player", {}))
        self._topic_entries = self._coerce_entries(data.get("topic", {}))
        self._commander_entries = self._coerce_entries(data.get("commander", {}))

    @staticmethod
    def _coerce_entries(d) -> dict[str, int]:
        if not isinstance(d, dict):
            return {}
        out: dict[str, int] = {}
        for k, v in d.items():
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n > 0:
                out[str(k)] = n
        return out

    def save(self) -> None:
        if not self._state_path:
            return
        tmp = self._state_path.with_suffix(self._state_path.suffix + '.tmp')
        try:
            with tmp.open('w', encoding='utf-8') as f:
                json.dump({
                    'player_axis':   self._entries,
                    'global_player': self._global_player_entries,
                    'topic':         self._topic_entries,
                    'commander':     self._commander_entries,
                }, f, indent=2)
            tmp.replace(self._state_path)
        except OSError as exc:
            logger.warning("CalloutCooldown: failed to save state: %s", exc)
