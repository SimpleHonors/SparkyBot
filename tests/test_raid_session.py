"""Tests for raid_session: log discovery, clustering, cache, plan"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from core.raid_session import (
    LogInfo,
    RaidReportCache,
    current_session,
    discover_logs,
    plan_report,
    recent_logs,
    RECENT_WINDOW_HOURS,
    settings_fingerprint,
    today_logs,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _touch(path, mtime_dt=None):
    """Create an empty file, optionally setting a specific mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    if mtime_dt is not None:
        ts = mtime_dt.timestamp()
        os.utime(str(path), (ts, ts))


def _make_loginfo(timestamp, path=None):
    if path is None:
        path = Path(f"/fake/{timestamp.strftime('%Y%m%d-%H%M%S')}.zevtc")
    return LogInfo(path=path, timestamp=timestamp, source="filename")


# ---------------------------------------------------------------------------
# discover_logs
# ---------------------------------------------------------------------------

def test_filename_timestamp_parse(tmp_path):
    p = tmp_path / "20240715-220510.zevtc"
    _touch(p)
    logs = discover_logs(tmp_path)
    assert len(logs) == 1
    assert logs[0].timestamp == datetime(2024, 7, 15, 22, 5, 10)
    assert logs[0].source == "filename"
    assert logs[0].path == p


def test_mtime_fallback(tmp_path):
    p = tmp_path / "custom_name.evtc"
    mtime = datetime(2024, 10, 10, 14, 30, 0)
    _touch(p, mtime_dt=mtime)
    logs = discover_logs(tmp_path)
    assert len(logs) == 1
    assert logs[0].timestamp == mtime
    assert logs[0].source == "mtime"


def test_non_log_files_ignored(tmp_path):
    (tmp_path / "notes.txt").write_text("hello")
    (tmp_path / "archive.csv").write_text("a,b,c")
    logs = discover_logs(tmp_path)
    assert len(logs) == 0


def test_discover_logs_recursive(tmp_path):
    sub = tmp_path / "nested"
    p = sub / "20240715-220510.evtc"
    _touch(p)
    logs = discover_logs(tmp_path)
    assert len(logs) == 1
    assert logs[0].path == p


def test_discover_logs_sorted(tmp_path):
    _touch(tmp_path / "20240715-220510.zevtc")
    _touch(tmp_path / "20240715-200000.zevtc")
    _touch(tmp_path / "20240715-235959.zevtc")
    logs = discover_logs(tmp_path)
    assert [l.timestamp for l in logs] == [
        datetime(2024, 7, 15, 20, 0, 0),
        datetime(2024, 7, 15, 22, 5, 10),
        datetime(2024, 7, 15, 23, 59, 59),
    ]


def test_invalid_datetime_in_filename_falls_back_to_mtime(tmp_path):
    p = tmp_path / "20241332-999999.zevtc"
    mtime = datetime(2024, 1, 1, 12, 0, 0)
    _touch(p, mtime_dt=mtime)
    logs = discover_logs(tmp_path)
    assert len(logs) == 1
    assert logs[0].timestamp == mtime
    assert logs[0].source == "mtime"


# ---------------------------------------------------------------------------
# current_session
# ---------------------------------------------------------------------------

def test_mid_date_crossing_cluster():
    """22:10 / 23:40 on day N, 00:12 on day N+1: same session.
    14:00 earlier in day N excluded by >3h gap."""
    now = datetime(2026, 7, 19, 0, 15, 0)
    logs = [
        _make_loginfo(datetime(2026, 7, 18, 14, 0, 0)),
        _make_loginfo(datetime(2026, 7, 18, 22, 10, 0)),
        _make_loginfo(datetime(2026, 7, 18, 23, 40, 0)),
        _make_loginfo(datetime(2026, 7, 19, 0, 12, 0)),
    ]
    session = current_session(logs, now=now)
    assert len(session) == 3
    assert [l.timestamp for l in session] == [
        datetime(2026, 7, 18, 22, 10, 0),
        datetime(2026, 7, 18, 23, 40, 0),
        datetime(2026, 7, 19, 0, 12, 0),
    ]


def test_lookback_cutoff():
    """A log 30 h old is never selected with default 24 h lookback."""
    now = datetime(2026, 7, 19, 12, 0, 0)
    old = datetime(2026, 7, 18, 6, 0, 0)  # 30 h before now
    recent = datetime(2026, 7, 19, 11, 0, 0)  # 1 h before now
    logs = [
        _make_loginfo(old),
        _make_loginfo(recent),
    ]
    session = current_session(logs, now=now)
    assert len(session) == 1
    assert session[0].timestamp == recent


def test_empty_logs_current_session():
    assert current_session([], now=datetime.now()) == []


def test_custom_max_gap(tmp_path):
    """With max_gap_hours=1.0, a 90-minute gap breaks the cluster."""
    now = datetime(2026, 7, 19, 0, 15, 0)
    logs = [
        _make_loginfo(datetime(2026, 7, 18, 22, 10, 0)),
        _make_loginfo(datetime(2026, 7, 18, 23, 40, 0)),
    ]
    session = current_session(logs, now=now, max_gap_hours=1.0)
    # From 23:40 back to 22:10 = 90 min > 1 h → break, only 23:40 included
    assert len(session) == 1
    assert session[0].timestamp == datetime(2026, 7, 18, 23, 40, 0)


def test_default_now_works():
    """current_session with now=None uses datetime.now()."""
    logs = [_make_loginfo(datetime.now() - timedelta(minutes=5))]
    assert len(current_session(logs)) == 1


# ---------------------------------------------------------------------------
# today_logs
# ---------------------------------------------------------------------------

def test_today_logs_same_day():
    now = datetime(2026, 7, 19, 12, 0, 0)
    logs = [
        _make_loginfo(datetime(2026, 7, 19, 9, 0, 0)),
        _make_loginfo(datetime(2026, 7, 18, 23, 0, 0)),
        _make_loginfo(datetime(2026, 7, 19, 14, 0, 0)),
    ]
    result = today_logs(logs, now=now)
    assert len(result) == 2
    assert all(l.timestamp.date() == now.date() for l in result)


def test_today_logs_empty():
    now = datetime(2026, 7, 19, 12, 0, 0)
    logs = [_make_loginfo(datetime(2026, 7, 18, 9, 0, 0))]
    assert today_logs(logs, now=now) == []


# ---------------------------------------------------------------------------
# recent_logs
# ---------------------------------------------------------------------------

def test_recent_logs_includes_recent_only():
    now = datetime(2026, 7, 19, 12, 0, 0)
    logs = [
        _make_loginfo(datetime(2026, 7, 19, 11, 30, 0)),  # 30 min ago
        _make_loginfo(datetime(2026, 7, 19,  0,  1, 0)),  # ~12 h ago — included
        _make_loginfo(datetime(2026, 7, 18, 23, 59, 0)),  # 12 h 1 m ago — excluded
        _make_loginfo(datetime(2026, 7, 18, 22,  0, 0)),  # 14 h ago — excluded
    ]
    result = recent_logs(logs, hours=12.0, now=now)
    assert len(result) == 2
    names = {log.timestamp.strftime("%H:%M") for log in result}
    assert names == {"11:30", "00:01"}


def test_recent_logs_chronological():
    now = datetime(2026, 7, 19, 12, 0, 0)
    logs = [
        _make_loginfo(datetime(2026, 7, 19, 10, 0, 0)),
        _make_loginfo(datetime(2026, 7, 19, 11, 0, 0)),
        _make_loginfo(datetime(2026, 7, 19, 11, 30, 0)),
    ]
    result = recent_logs(logs, hours=12.0, now=now)
    assert result == sorted(result, key=lambda l: l.timestamp)


def test_recent_logs_empty_when_none_recent():
    now = datetime(2026, 7, 19, 12, 0, 0)
    logs = [
        _make_loginfo(datetime(2026, 7, 18, 12, 0, 0)),  # 24 h ago
    ]
    result = recent_logs(logs, hours=1.0, now=now)
    assert result == []


def test_recent_logs_uses_default_window():
    assert RECENT_WINDOW_HOURS == 12.0


# ---------------------------------------------------------------------------
# settings_fingerprint
# ---------------------------------------------------------------------------

def test_settings_fingerprint_length():
    fp = settings_fingerprint("some settings text")
    assert len(fp) == 12


def test_settings_fingerprint_is_hex():
    fp = settings_fingerprint("data")
    assert all(c in "0123456789abcdef" for c in fp)


def test_settings_fingerprint_deterministic():
    assert settings_fingerprint("abc") == settings_fingerprint("abc")


def test_settings_fingerprint_different_for_different_input():
    assert settings_fingerprint("abc") != settings_fingerprint("def")


# ---------------------------------------------------------------------------
# RaidReportCache
# ---------------------------------------------------------------------------

def test_cache_store_and_lookup_roundtrip(tmp_path):
    log_path = Path("/fake/path/20240601-180000.zevtc")
    cache_root = tmp_path / "cache"
    cache = RaidReportCache(cache_root)

    json_src = tmp_path / "parsed.json"
    json_src.write_text('{"data": 1}')

    ei_ver = "2.50.0"
    fp = "abc123def456"

    dest = cache.store(log_path, json_src, ei_ver, fp)
    assert dest.exists()
    assert dest.read_text() == '{"data": 1}'
    assert not json_src.exists()  # moved, not copied

    found = cache.lookup(log_path, ei_ver, fp)
    assert found == dest


def test_cache_wrong_ei_version_returns_none(tmp_path):
    log_path = Path("/fake/path/20240601-180000.zevtc")
    cache = RaidReportCache(tmp_path / "cache")
    json_src = tmp_path / "parsed.json"
    json_src.write_text("{}")
    cache.store(log_path, json_src, "2.50.0", "abc123def456")
    assert cache.lookup(log_path, "2.49.0", "abc123def456") is None


def test_cache_wrong_fingerprint_returns_none(tmp_path):
    log_path = Path("/fake/path/20240601-180000.zevtc")
    cache = RaidReportCache(tmp_path / "cache")
    json_src = tmp_path / "parsed.json"
    json_src.write_text("{}")
    cache.store(log_path, json_src, "2.50.0", "abc123def456")
    assert cache.lookup(log_path, "2.50.0", "xyz999000111") is None


def test_cache_mtime_fallback_date_dir(tmp_path):
    """Log path without date prefix uses file mtime for date directory."""
    log_path = tmp_path / "custom_log.evtc"
    mtime = datetime(2025, 3, 15, 8, 0, 0)
    _touch(log_path, mtime_dt=mtime)

    cache = RaidReportCache(tmp_path / "cache")
    json_src = tmp_path / "parsed.json"
    json_src.write_text("x")

    dest = cache.store(log_path, json_src, "v1", "ffffffffffff")
    assert "20250315" in str(dest)
    assert dest.exists()


def test_lookup_nonexistent_returns_none(tmp_path):
    cache = RaidReportCache(tmp_path / "cache")
    log_path = Path("/fake/20240601-180000.zevtc")
    assert cache.lookup(log_path, "v1", "ffffffffffff") is None


def test_prune_removes_old_files(tmp_path):
    now = datetime(2026, 7, 19, 12, 0, 0)
    cache_root = tmp_path / "cache"
    cache = RaidReportCache(cache_root)

    log_path = Path("/fake/20240601-180000.zevtc")

    # File with mtime = now - 72h (older than 48h retention)
    old_json = tmp_path / "old.json"
    old_json.write_text("old")
    old_dest = cache.path_for(log_path, "v1", "aaabbbcccddd")
    old_dest.parent.mkdir(parents=True, exist_ok=True)
    old_json.rename(old_dest)
    old_ts = (now - timedelta(hours=72)).timestamp()
    os.utime(str(old_dest), (old_ts, old_ts))

    # File with mtime = now - 1h (fresh)
    fresh_json = tmp_path / "fresh.json"
    fresh_json.write_text("fresh")
    fresh_dest = cache.path_for(log_path, "v1", "ddddeeeeffff")
    fresh_dest.parent.mkdir(parents=True, exist_ok=True)
    fresh_json.rename(fresh_dest)
    fresh_ts = (now - timedelta(hours=1)).timestamp()
    os.utime(str(fresh_dest), (fresh_ts, fresh_ts))

    removed = cache.prune(retention_hours=48.0, now=now)
    assert removed == 1
    assert not old_dest.exists()
    assert fresh_dest.exists()


def test_prune_cleans_empty_day_dirs(tmp_path):
    now = datetime(2026, 7, 19, 12, 0, 0)
    cache_root = tmp_path / "cache"
    cache = RaidReportCache(cache_root)

    day_dir = cache_root / "20240601"
    day_dir.mkdir(parents=True, exist_ok=True)
    old_file = day_dir / "dummy.json"
    old_file.write_text("stale")
    old_ts = (now - timedelta(hours=72)).timestamp()
    os.utime(str(old_file), (old_ts, old_ts))

    removed = cache.prune(retention_hours=48.0, now=now)
    assert removed == 1
    assert not old_file.exists()
    assert not day_dir.exists()


def test_prune_non_existent_root(tmp_path):
    cache = RaidReportCache(tmp_path / "nonexistent")
    assert cache.prune() == 0


# ---------------------------------------------------------------------------
# plan_report
# ---------------------------------------------------------------------------

def test_plan_report_split(tmp_path):
    log1 = _make_loginfo(datetime(2026, 7, 19, 1, 0, 0),
                         path=Path("/fake/20240719-010000.zevtc"))
    log2 = _make_loginfo(datetime(2026, 7, 19, 1, 5, 0),
                         path=Path("/fake/20240719-010500.zevtc"))

    cache = RaidReportCache(tmp_path / "cache")
    json_src = tmp_path / "parsed.json"
    json_src.write_text('{"result": "ok"}')
    cache.store(log1.path, json_src, "v2", "cccccccccccc")

    hits, needs = plan_report([log1, log2], cache, "v2", "cccccccccccc")
    assert len(hits) == 1
    assert hits[0][0] is log1
    assert len(needs) == 1
    assert needs[0] is log2


def test_plan_report_all_needs(tmp_path):
    log = _make_loginfo(datetime(2026, 7, 19, 1, 0, 0))
    cache = RaidReportCache(tmp_path / "cache")
    hits, needs = plan_report([log], cache, "v1", "aaaaaaaaaaaa")
    assert hits == []
    assert len(needs) == 1


def test_plan_report_empty(tmp_path):
    cache = RaidReportCache(tmp_path / "cache")
    hits, needs = plan_report([], cache, "v1", "aaaaaaaaaaaa")
    assert hits == []
    assert needs == []
