"""Raid session: log discovery, session clustering, fight-data cache"""

import hashlib
import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_STEM_RE = re.compile(r'^(\d{8})-(\d{6})')


@dataclass(frozen=True)
class LogInfo:
    path: Path
    timestamp: datetime          # local, naive
    source: str                  # "filename" or "mtime"
    size_bytes: int | None = None


def log_info_for_path(path: Path, stat_result=None) -> LogInfo:
    """Build LogInfo for one file using the same rules as discovery."""
    path = Path(path)
    size_bytes = stat_result.st_size if stat_result is not None else None
    m = _LOG_STEM_RE.match(path.stem)
    if m:
        ts_str = m.group(1) + m.group(2)
        try:
            return LogInfo(
                path=path,
                timestamp=datetime.strptime(ts_str, '%Y%m%d%H%M%S'),
                source='filename',
                size_bytes=size_bytes,
            )
        except ValueError:
            pass
    if stat_result is None:
        stat_result = path.stat()
        size_bytes = stat_result.st_size
    return LogInfo(
        path=path,
        timestamp=datetime.fromtimestamp(stat_result.st_mtime),
        source='mtime',
        size_bytes=size_bytes,
    )


def discover_logs(log_folder: Path, *, include_size: bool = False) -> list[LogInfo]:
    """Discover .zevtc + .evtc logs in one metadata-efficient walk.

    Timestamp from YYYYMMDD-HHMMSS filename prefix; falls back to mtime
    if the name doesn't match.  ``include_size`` captures file size from the
    same scandir entry for UIs that display it, avoiding a second SMB stat per
    row. Sorted by timestamp ascending.
    """
    results: list[LogInfo] = []
    pending = [Path(log_folder)]
    while pending:
        current = pending.pop()
        try:
            entries = os.scandir(current)
        except OSError:
            logger.debug("Skipping unreadable folder: %s", current)
            continue

        with entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                        continue
                    if not entry.name.lower().endswith(('.zevtc', '.evtc')):
                        continue
                    path = Path(entry.path)
                    needs_mtime = _LOG_STEM_RE.match(path.stem) is None
                    stat_result = (
                        entry.stat(follow_symlinks=False)
                        if include_size or needs_mtime else None
                    )
                    results.append(log_info_for_path(path, stat_result))
                except OSError:
                    logger.debug("Skipping unreadable file: %s", entry.path)
                    continue

    results.sort(key=lambda x: x.timestamp)
    return results


def current_session(logs: list[LogInfo], now: datetime | None = None,
                    max_gap_hours: float = 3.0,
                    lookback_hours: float = 24.0) -> list[LogInfo]:
    """Gap-based session clustering (newest -> oldest).

    Starts from the newest log within lookback_hours of now; extends the
    cluster while consecutive gap <= max_gap_hours. Returns chronological.
    Empty list if no log within lookback.
    """
    if now is None:
        now = datetime.now()
    if not logs:
        return []

    cutoff = now - timedelta(hours=lookback_hours)
    max_gap = timedelta(hours=max_gap_hours)

    cluster: list[LogInfo] = []
    for log in reversed(logs):
        if log.timestamp <= cutoff:
            if not cluster:
                continue
            break
        if not cluster or (cluster[0].timestamp - log.timestamp) <= max_gap:
            cluster.insert(0, log)
        else:
            break

    return cluster


def today_logs(logs: list[LogInfo], now: datetime | None = None) -> list[LogInfo]:
    """Calendar-day filter: logs whose local date == now.date()."""
    if now is None:
        now = datetime.now()
    target_date = now.date()
    return [log for log in logs if log.timestamp.date() == target_date]


RECENT_WINDOW_HOURS = 12.0


def recent_logs(logs: list[LogInfo],
                hours: float = RECENT_WINDOW_HOURS,
                now: datetime | None = None) -> list[LogInfo]:
    """Rolling-window filter: logs from now-hours through now, chronological."""
    if now is None:
        now = datetime.now()
    cutoff = now - timedelta(hours=hours)
    result = [log for log in logs if log.timestamp >= cutoff]
    result.sort(key=lambda x: x.timestamp)
    return result


def settings_fingerprint(settings_text: str) -> str:
    """SHA-256 hex of settings text, first 12 chars."""
    return hashlib.sha256(settings_text.encode('utf-8')).hexdigest()[:12]


class RaidReportCache:
    """Cache for GW2EI-parsed fight JSON files.

    Layout: cache_root/YYYYMMDD/<logstem>__<ei_version>__<fingerprint>.json
    """

    def __init__(self, cache_root: Path):
        self.cache_root = Path(cache_root)

    def _date_dir(self, log_path: Path) -> Path:
        m = _LOG_STEM_RE.match(log_path.stem)
        if m:
            date_str = m.group(1)
        else:
            try:
                mtime = datetime.fromtimestamp(log_path.stat().st_mtime)
                date_str = mtime.strftime('%Y%m%d')
            except OSError:
                date_str = datetime.now().strftime('%Y%m%d')
        return self.cache_root / date_str

    def path_for(self, log_path: Path, ei_version: str,
                 fingerprint: str) -> Path:
        return self._date_dir(log_path) / (
            f"{log_path.stem}__{ei_version}__{fingerprint}.json"
        )

    def store(self, log_path: Path, json_path: Path,
              ei_version: str, fingerprint: str) -> Path:
        dest = self.path_for(log_path, ei_version, fingerprint)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(json_path), str(dest))
        return dest

    def lookup(self, log_path: Path, ei_version: str,
               fingerprint: str) -> Path | None:
        p = self.path_for(log_path, ei_version, fingerprint)
        return p if p.exists() else None

    def prune(self, retention_hours: float = 48.0,
              now: datetime | None = None) -> int:
        if now is None:
            now = datetime.now()
        if not self.cache_root.exists():
            return 0

        cutoff = now - timedelta(hours=retention_hours)
        removed = 0

        for day_dir in list(self.cache_root.iterdir()):
            if not day_dir.is_dir():
                continue
            for f in list(day_dir.iterdir()):
                if f.is_file() and datetime.fromtimestamp(
                        f.stat().st_mtime) < cutoff:
                    f.unlink()
                    removed += 1
                    logger.debug("Pruned cached file: %s", f)
            if not list(day_dir.iterdir()):
                day_dir.rmdir()
                logger.debug("Removed empty day dir: %s", day_dir)

        return removed


def prune_raidreport_output(retention_hours: float = 48.0,
                            now: datetime | None = None,
                            root: Path | None = None) -> int:
    """Sweep the DEFAULT (temp-dir) report output folder.

    Only ever touches Config.default_raidreport_output_dir() (or the
    explicit root, used by tests); a folder the user configured is
    never pruned. Returns files removed.
    """
    if root is None:
        from core.config import Config
        root = Config.default_raidreport_output_dir()
    if not root.exists():
        return 0
    if now is None:
        now = datetime.now()
    cutoff = now - timedelta(hours=retention_hours)
    removed = 0
    for f in list(root.iterdir()):
        if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
            try:
                f.unlink()
                removed += 1
                logger.debug("Pruned old raid report artifact: %s", f)
            except OSError:
                logger.debug("Could not prune %s", f, exc_info=True)
    return removed


def plan_report(selected: list[LogInfo], cache: RaidReportCache,
                ei_version: str, fingerprint: str
                ) -> tuple[list[tuple[LogInfo, Path]], list[LogInfo]]:
    hits: list[tuple[LogInfo, Path]] = []
    needs: list[LogInfo] = []
    for log in selected:
        found = cache.lookup(log.path, ei_version, fingerprint)
        if found is not None:
            hits.append((log, found))
        else:
            needs.append(log)
    return hits, needs
