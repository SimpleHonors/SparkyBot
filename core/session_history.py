"""SessionHistoryTracker: streak-aware mood escalation over recent fights.

Depends only on ai_helpers.
"""
import json
import logging
import random
import time
from pathlib import Path
from typing import Any, Dict

from ai_helpers import _atomic_write_json

logger = logging.getLogger(__name__)


class SessionHistoryTracker:
    """Tracks recent fight outcomes for streak-aware mood escalation."""

    MAX_ENTRIES = 50

    # Win streak: 2-3 ease off, 4-5 roll hard, 6+ bored
    WIN_STREAK_EASE = 2
    WIN_STREAK_ROLL = 4
    WIN_STREAK_BORED = 6

    # Loss streak: 2-3 pattern, 4+ full tilt
    LOSS_STREAK_PATTERN = 2
    LOSS_STREAK_TILT = 4

    # Session gap hours: streak resets after this gap between fights
    SESSION_GAP_HOURS = 4

    WIN_OUTCOMES = {"Win", "Decisive Win"}
    LOSS_OUTCOMES = {"Loss", "Decisive Loss"}

    def __init__(self, store_path: Path = None):
        self.store_path = store_path or Path(__file__).parent.parent / "sparkybot_session_history.json"
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
        """Walk history newest-to-oldest, return streak info."""
        if not self._entries:
            return {"type": "none", "length": 0, "shapes": []}

        newest = self._entries[-1]

        # Stale-session guard: the inter-entry gap check below only fires
        # between consecutive entries. Without this, a fresh session after
        # a multi-day break inherits the old streak as if it were current.
        if (time.time() - newest.get("ts", 0)) > self.SESSION_GAP_HOURS * 3600:
            return {"type": "none", "length": 0, "shapes": []}

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
        """Convert get_streak() result into prompt strings."""
        stype = streak.get("type", "none")
        length = streak.get("length", 0)
        shapes = streak.get("shapes", [])

        if stype in ("none", "fresh") or length < 2:
            return {"session_context": "", "mood_suffix": ""}

        # Determine whether the current fight continues or breaks the streak.
        streak_continues = True
        if current_outcome is not None:
            if stype == "win" and current_outcome not in self.WIN_OUTCOMES:
                streak_continues = False
            elif stype == "loss" and current_outcome not in self.LOSS_OUTCOMES:
                streak_continues = False

        if not streak_continues:
            # Streak-break case. Emit a different context line
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

        # Shape flavor for the session context line.
        shape_flavor = ""
        if shapes and all(s == "blowout" for s in shapes):
            shape_flavor = " (all blowouts, unimpressive)"
        elif shapes and all(s in ("legendary_outnumbered", "comparable")
                            for s in shapes):
            shape_flavor = " (all comparable or outnumbered, real quality)"

        # Transition-only emission: only fire on the exact length that
        # crosses a tier. Avoids re-saying "on a win streak" every fight.
        if stype == "win":
            if length == self.WIN_STREAK_BORED:
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
                        "badly despite the easy night and roast that instead of "
                        "praising the win. Constructive contempt."
                    ),
                ])
            elif length == self.WIN_STREAK_ROLL:
                mood = (
                    " Session note: the squad is rolling hard. Do not "
                    "celebrate the result itself, mock the enemy for walking "
                    "into yet another loss."
                )
            elif length == self.WIN_STREAK_EASE:
                mood = (
                    " Session note: the squad is on a win streak. Ease off "
                    "the euphoria slightly, this is not the first win of "
                    "the night."
                )
            else:
                return {"session_context": "", "mood_suffix": ""}
            context = f"Session context: on a {length}-fight win streak{shape_flavor}."
            return {"session_context": context, "mood_suffix": mood}

        if stype == "loss":
            if length == self.LOSS_STREAK_TILT:
                mood = (
                    " Session note: this is a sustained loss streak. Full "
                    "tilt. Something is structurally wrong tonight, the "
                    "squad should hear it."
                )
            elif length == self.LOSS_STREAK_PATTERN:
                mood = (
                    " Session note: this is becoming a pattern. The anger "
                    "should feel less like a surprise and more like a "
                    "repeated warning."
                )
            else:
                return {"session_context": "", "mood_suffix": ""}
            context = f"Session context: on a {length}-fight loss streak{shape_flavor}."
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
            _atomic_write_json(self.store_path, {"entries": self._entries})
        except Exception as exc:
            logger.warning("SessionHistoryTracker: could not save store: %s", exc)
