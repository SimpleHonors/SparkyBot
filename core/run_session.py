"""Run session state — the persisted Start Run / End Run window.

A run is nothing more than {started_at, ended_at} persisted as an atomic
run_state.json in the config home dir. Fight collection is defined by the
time window over core.raid_session.discover_logs — the exact same source
of truth the raid report reads — so restart-safety is free and fights the
watcher skipped by posting filters still count for the report.

Pure stdlib, no Qt: headless modes and tests can drive it directly.
Naming note: core/session_history.py is the AI streak tracker — unrelated;
hence the distinct run_session name.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_FILENAME = "run_state.json"
STATE_VERSION = 1

# Resume silently at launch while the newest in-window log is fresher than
# this; older means the operator forgot to end the run — quiet banner.
RESUME_WINDOW_HOURS = 6.0
# Passive warn-tint hint on the run panel once a run has been open this long.
STALE_HINT_HOURS = 12.0


def fights_in_window(logs, started_at: datetime,
                     ended_at: datetime | None = None) -> list:
    """Filter LogInfo records to the run window (inclusive both ends).
    ended_at None means the run is still open (window extends to now)."""
    return [log for log in logs
            if log.timestamp >= started_at
            and (ended_at is None or log.timestamp <= ended_at)]


def collect_run_logs(logs, started_at: datetime,
                     ended_at: datetime | None = None,
                     recorded_paths=()) -> list:
    """Collect timestamp-window fights plus files processed during the run.

    A copied/replayed log keeps its original filename timestamp. Recording
    the processing event prevents that old timestamp from hiding a fight the
    user deliberately processed after pressing Start Run.
    """
    from core.raid_session import log_info_for_path

    selected = fights_in_window(logs, started_at, ended_at)
    by_path = {str(log.path.resolve()): log for log in logs}
    included = {str(log.path.resolve()) for log in selected}
    for raw_path in recorded_paths:
        path = Path(raw_path)
        key = str(path.resolve())
        if key in included:
            continue
        info = by_path.get(key)
        if info is None:
            try:
                info = log_info_for_path(path)
            except OSError:
                logger.warning("Recorded run log is no longer readable: %s", path)
                continue
        selected.append(info)
        included.add(key)
    selected.sort(key=lambda log: (log.timestamp, str(log.path)))
    return selected


def format_elapsed(delta: timedelta) -> str:
    """Human elapsed time for the run panel: '45 m', '2 h 14 m'."""
    total_minutes = int(max(delta.total_seconds(), 0) // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours} h {minutes:02d} m"
    return f"{minutes} m"


class RunSession:
    """Persisted open-run state over run_state.json.

    Dormant until start(): constructing one only READS existing state and
    never creates the file (manual mode must leave no trace on disk).
    """

    def __init__(self, home_dir, clock=None):
        self._path = Path(home_dir) / STATE_FILENAME
        self._clock = clock or datetime.now
        self._started_at: datetime | None = None
        self._last_activity_at: datetime | None = None
        self._recorded_logs: list[Path] = []
        self._load()

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def is_open(self) -> bool:
        return self._started_at is not None

    @property
    def started_at(self) -> datetime | None:
        return self._started_at

    @property
    def recorded_logs(self) -> tuple[Path, ...]:
        return tuple(self._recorded_logs)

    @property
    def last_activity_at(self) -> datetime | None:
        return self._last_activity_at

    def elapsed(self, now: datetime | None = None) -> timedelta:
        if not self.is_open:
            return timedelta(0)
        return (now or self._clock()) - self._started_at

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------

    def _load(self):
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            started = raw.get("started_at")
            if started and raw.get("ended_at") is None:
                self._started_at = datetime.fromisoformat(started)
                activity = raw.get("last_activity_at")
                self._last_activity_at = (
                    datetime.fromisoformat(activity) if activity
                    else self._started_at)
                recorded = raw.get("recorded_logs", [])
                if not isinstance(recorded, list):
                    recorded = []
                self._recorded_logs = [
                    Path(path) for path in recorded
                    if isinstance(path, str) and path
                ]
        except (OSError, ValueError, TypeError) as exc:
            # A corrupt state file must never block launch; the run is
            # simply not resumed (the logs on disk are the real record).
            logger.warning("Ignoring unreadable %s: %s", self._path.name, exc)

    def _write(self):
        payload = {
            "version": STATE_VERSION,
            "started_at": self._started_at.isoformat(timespec="seconds"),
            "ended_at": None,
            "last_activity_at": self._last_activity_at.isoformat(
                timespec="seconds"),
            "recorded_logs": [str(path) for path in self._recorded_logs],
        }
        tmp = self._path.parent / (self._path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self, now: datetime | None = None) -> datetime:
        """Open a run and persist it (atomic temp-then-replace write)."""
        if self.is_open:
            raise RuntimeError("a run is already open")
        self._started_at = now or self._clock()
        self._last_activity_at = self._started_at
        self._recorded_logs = []
        self._write()
        return self._started_at

    def record_log(self, path) -> bool:
        """Persist a log processed while this run is open; deduplicate paths."""
        if not self.is_open:
            return False
        path = Path(path)
        key = str(path.resolve())
        if any(str(existing.resolve()) == key
               for existing in self._recorded_logs):
            return False
        self._recorded_logs.append(path)
        self._last_activity_at = self._clock()
        self._write()
        return True

    def end(self, ended_at: datetime | None = None):
        """Close the run: returns the (started_at, ended_at) window and
        removes the state file — a run only exists while open."""
        if not self.is_open:
            raise RuntimeError("no run is open")
        window = (self._started_at, ended_at or self._clock())
        self.discard()
        return window

    def discard(self):
        """Drop the open run without reporting anything."""
        self._started_at = None
        self._last_activity_at = None
        self._recorded_logs = []
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
