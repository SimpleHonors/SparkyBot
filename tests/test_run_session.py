"""core/run_session.py (slice 6): persisted Start/End Run state.

Pure-stdlib contract (no Qt):
- constructing a RunSession is dormant — it READS existing state but never
  creates run_state.json (manual mode must leave no trace);
- start() writes the file atomically ({version, started_at, ended_at:null});
- state survives a restart (a fresh instance sees the open run);
- end() returns the (started_at, ended_at) window and deletes the file;
- discard() deletes without reporting; malformed state never blocks launch;
- fights_in_window filters LogInfo records inclusively;
- all time comes from an injected clock — no wall-clock sleeps anywhere.
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core.raid_session import LogInfo
from core.run_session import (
    RESUME_WINDOW_HOURS, STALE_HINT_HOURS, STATE_FILENAME, RunSession,
    fights_in_window, format_elapsed,
)

T0 = datetime(2026, 7, 19, 20, 0, 0)


def _clocked(tmp_path, now):
    state = {"now": now}
    session = RunSession(tmp_path, clock=lambda: state["now"])
    return session, state


def test_construction_is_dormant(tmp_path):
    session, _ = _clocked(tmp_path, T0)
    assert not session.is_open
    assert session.started_at is None
    assert not (tmp_path / STATE_FILENAME).exists()
    assert list(tmp_path.iterdir()) == []      # no temp files either


def test_start_writes_atomic_state_file(tmp_path):
    session, _ = _clocked(tmp_path, T0)
    started = session.start()
    assert started == T0
    assert session.is_open
    path = tmp_path / STATE_FILENAME
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["started_at"] == "2026-07-19T20:00:00"
    assert raw["ended_at"] is None
    assert raw["version"] == 1
    # temp file from the atomic write is gone
    assert sorted(p.name for p in tmp_path.iterdir()) == [STATE_FILENAME]


def test_state_survives_restart(tmp_path):
    first, _ = _clocked(tmp_path, T0)
    first.start()
    second, _ = _clocked(tmp_path, T0 + timedelta(hours=2))
    assert second.is_open
    assert second.started_at == T0
    assert second.elapsed() == timedelta(hours=2)


def test_end_returns_window_and_deletes_file(tmp_path):
    session, state = _clocked(tmp_path, T0)
    session.start()
    state["now"] = T0 + timedelta(hours=2, minutes=14)
    window = session.end()
    assert window == (T0, T0 + timedelta(hours=2, minutes=14))
    assert not session.is_open
    assert not (tmp_path / STATE_FILENAME).exists()


def test_end_with_explicit_timestamp(tmp_path):
    """Stale-run ending pins ended_at to the last fight, not now."""
    session, state = _clocked(tmp_path, T0)
    session.start()
    state["now"] = T0 + timedelta(hours=30)
    last_fight = T0 + timedelta(hours=1)
    window = session.end(ended_at=last_fight)
    assert window == (T0, last_fight)


def test_discard_deletes_without_window(tmp_path):
    session, _ = _clocked(tmp_path, T0)
    session.start()
    session.discard()
    assert not session.is_open
    assert not (tmp_path / STATE_FILENAME).exists()
    session.discard()          # idempotent


def test_double_start_and_end_without_run_raise(tmp_path):
    session, _ = _clocked(tmp_path, T0)
    with pytest.raises(RuntimeError):
        session.end()
    session.start()
    with pytest.raises(RuntimeError):
        session.start()


def test_malformed_state_never_blocks_launch(tmp_path):
    (tmp_path / STATE_FILENAME).write_text("{not json", encoding="utf-8")
    session, _ = _clocked(tmp_path, T0)
    assert not session.is_open
    session.start()            # still usable afterwards
    assert session.is_open


def test_closed_state_on_disk_is_not_resumed(tmp_path):
    (tmp_path / STATE_FILENAME).write_text(json.dumps({
        "version": 1,
        "started_at": "2026-07-18T20:00:00",
        "ended_at": "2026-07-18T23:00:00",
    }), encoding="utf-8")
    session, _ = _clocked(tmp_path, T0)
    assert not session.is_open


def test_elapsed_uses_injected_clock(tmp_path):
    session, state = _clocked(tmp_path, T0)
    assert session.elapsed() == timedelta(0)     # closed -> zero
    session.start()
    state["now"] = T0 + timedelta(minutes=45)
    assert session.elapsed() == timedelta(minutes=45)


def _log(ts):
    return LogInfo(path=Path(f"/logs/{ts:%Y%m%d-%H%M%S}.zevtc"),
                   timestamp=ts, source="filename")


def test_fights_in_window_filters_inclusively():
    logs = [
        _log(T0 - timedelta(minutes=1)),          # before the run
        _log(T0),                                  # exactly at start
        _log(T0 + timedelta(minutes=30)),
        _log(T0 + timedelta(hours=2)),             # exactly at end
        _log(T0 + timedelta(hours=2, seconds=1)),  # after the end
    ]
    window = fights_in_window(logs, T0, T0 + timedelta(hours=2))
    assert [log.timestamp for log in window] == [
        T0, T0 + timedelta(minutes=30), T0 + timedelta(hours=2)]
    # Open run: window extends to now
    open_window = fights_in_window(logs, T0)
    assert len(open_window) == 4


def test_format_elapsed():
    assert format_elapsed(timedelta(0)) == "0 m"
    assert format_elapsed(timedelta(minutes=45)) == "45 m"
    assert format_elapsed(timedelta(hours=2, minutes=14)) == "2 h 14 m"
    assert format_elapsed(timedelta(hours=1, minutes=1)) == "1 h 01 m"
    assert format_elapsed(timedelta(seconds=-5)) == "0 m"


def test_thresholds_are_the_designed_ones():
    assert RESUME_WINDOW_HOURS == 6.0
    assert STALE_HINT_HOURS == 12.0
