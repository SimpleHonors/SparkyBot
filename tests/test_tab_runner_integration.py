"""Integration test: real RaidReportRunner driven by RaidReportTab's thread target.

If either side's generate(progress=) signature changes, THIS test fails.
"""

import sys
import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

pytest.importorskip("PySide6", reason="PySide6 not installed in this environment")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from core.raid_session import LogInfo, RaidReportCache
from core.raid_report import RaidReportRunner, ReportResult


# ---------------------------------------------------------------------------
# QApplication fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _log(year, month, day, hour, minute, fname=None):
    ts = datetime(year, month, day, hour, minute)
    if fname:
        path = Path(f"/fake/{fname}")
    else:
        path = Path(f"/fake/{ts.strftime('%Y%m%d-%H%M%S')}.zevtc")
    return LogInfo(path=path, timestamp=ts, source="filename")


class _FakeCache:
    def __init__(self):
        self._store = {}

    def lookup(self, log_path, ei_version, fingerprint):
        return self._store.get(log_path)

    def store(self, log_path, json_path, ei_version, fingerprint):
        self._store[log_path] = json_path
        return json_path

    def preload(self, log_path, json_path):
        self._store[log_path] = json_path


class _FakeCombiner:
    def __init__(self, dragdrop_json):
        self._output = dragdrop_json

    def ensure_installed(self, progress_callback=None):
        pass

    def write_run_config(self, run_dir, input_dir,
                         guild_name="", guild_id="", api_key=""):
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir / "top_stats_config.ini"

    def run(self, input_dir, run_dir, timeout=900, progress_callback=None):
        return self._output


class _InlineThread:
    def __init__(self, target=None, daemon=None, args=(), kwargs=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


# ---------------------------------------------------------------------------
# the integration test
# ---------------------------------------------------------------------------

def test_tab_thread_drives_real_runner_with_progress_kwarg(qapp, tmp_path):
    """Build a real RaidReportRunner, wrap it in runner_factory, pass it to
    the tab, click the big button, assert sig_done fires.

    If generate() ever loses the progress= kwarg, this test raises TypeError.
    """
    from core.raid_report_tab import RaidReportTab

    # --- prepare fakes ---
    log_a = _log(2026, 7, 18, 22, 0, "fight_a.zevtc")
    log_b = _log(2026, 7, 18, 23, 0, "fight_b.zevtc")

    cache = _FakeCache()
    parsed_a = tmp_path / "parsed_a.json"
    parsed_a.write_text('{"title":"Fight_01"}')
    parsed_b = tmp_path / "parsed_b.json"
    parsed_b.write_text('{"title":"Fight_02"}')

    combiner_json = tmp_path / "combiner_out.json"
    combiner_json.write_text(
        '[{"title":"Fight_01","tags":"2026-07-18-22:00:00"},'
        '{"title":"Fight_02","tags":"2026-07-18-23:00:00"}]'
    )
    combiner = _FakeCombiner(combiner_json)

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    log_folder = tmp_path / "logs"
    log_folder.mkdir()

    viewer_path = tmp_path / "viewer.html"
    viewer_path.write_text(
        '<html><head><title>Viewer</title></head><body>\n'
        '<script class="tiddlywiki-tiddler-store" type="application/json">'
        '[{"title":"$:/boot","text":"hi"}]'
        '</script>\n</body></html>'
    )

    # --- monkeypatch bake_report and summarize_tiddlers ---
    import core.raid_report as raid_report_mod

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        raid_report_mod, "bake_report",
        lambda viewer, json, out, report_title=None: out.write_text("<html>report</html>") or out)
    monkeypatch.setattr(
        raid_report_mod, "summarize_tiddlers",
        lambda json: {"fight_count": 2, "span": "22:00\u201323:00"})

    try:
        tab = RaidReportTab(
            discover=lambda: [log_a, log_b],
            select_session=lambda logs: logs,
            select_recent=lambda logs: logs,
            select_today=lambda logs: logs,
            runner_factory=lambda: RaidReportRunner(
                log_folder=log_folder,
                cache=cache,
                parse_log=lambda p: parsed_a if "fight_a" in str(p)
                         else parsed_b if "fight_b" in str(p) else None,
                ei_version="v2.50.0",
                settings_fingerprint="abc123def456",
                combiner=combiner,
                viewer_html=viewer_path,
                output_dir=output_dir,
            ),
            publish=lambda r: None,
        )

        tab._apply_chip("today")

        with patch("core.raid_report_tab.threading.Thread",
                   new=_InlineThread):
            tab.big_btn.click()

        assert tab.last_result is not None
        assert tab.last_result.fight_count == 2
        assert "Done!" in tab.status_label.text()
        assert tab.open_report_btn.isEnabled()
    finally:
        monkeypatch.undo()


def test_progress_override_vs_constructor(qapp, tmp_path):
    """Generate's per-call progress takes precedence over constructor progress."""
    calls = []

    def ctor_progress(stage, done, total, msg):
        calls.append(("ctor", stage))

    def call_progress(stage, done, total, msg):
        calls.append(("call", stage))

    p1 = tmp_path / "p1.json"
    p1.write_text('{"title":"Fight_01"}')
    p2 = tmp_path / "p2.json"
    p2.write_text('{"title":"Fight_02"}')

    combiner_json = tmp_path / "combiner.json"
    combiner_json.write_text(
        '[{"title":"Fight_01","tags":"2026-07-18-22:00:00"},'
        '{"title":"Fight_02","tags":"2026-07-18-23:00:00"}]'
    )

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    viewer = tmp_path / "v.html"
    viewer.write_text(
        '<html><head><title>Viewer</title></head><body>\n'
        '<script class="tiddlywiki-tiddler-store" type="application/json">'
        '[{"title":"$:/boot","text":"hi"}]'
        '</script>\n</body></html>'
    )

    cache = _FakeCache()
    cache.preload(Path("/fake/a.zevtc"), p1)
    cache.preload(Path("/fake/b.zevtc"), p2)

    runner = RaidReportRunner(
        log_folder=tmp_path,
        cache=cache,
        parse_log=lambda p: None,
        ei_version="v1",
        settings_fingerprint="a" * 12,
        combiner=_FakeCombiner(combiner_json),
        viewer_html=viewer,
        output_dir=output_dir,
        progress=ctor_progress,
    )

    selected = [_log(2026, 7, 18, 22, 0, "a.zevtc"),
                _log(2026, 7, 18, 23, 0, "b.zevtc")]

    result = runner.generate(selected, progress=call_progress)
    assert result.fight_count == 2
    assert any(stage == "plan" for _, stage in calls)
    assert all(source == "call" for source, _ in calls)
    assert len(calls) >= 4
