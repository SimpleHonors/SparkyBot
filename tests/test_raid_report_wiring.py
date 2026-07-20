"""Tests for raid_report_wiring: viewer resolution, augment wiring, headless CLI, tab smoke."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))


# ---------------------------------------------------------------------------
# viewer resolution
# ---------------------------------------------------------------------------

def test_viewer_resolution_config_set(tmp_path):
    """Config with an existing raidreport_viewer_html path returns it."""
    from core.raid_report_wiring import _resolve_viewer

    viewer = tmp_path / "my_viewer.html"
    viewer.write_text("<html></html>")

    config = MagicMock()
    config.raidreport_viewer_html = str(viewer)

    result = _resolve_viewer(config)
    assert result == viewer


def test_viewer_resolution_combiner_fallback(tmp_path, monkeypatch):
    """When config has no viewer_html and combiner dir contains
    Top_Stats_Index.html, that file is returned."""
    from core.raid_report_wiring import _resolve_viewer
    import core.apppaths

    combiner_root = tmp_path / "RaidReportData" / "combiner"
    version_dir = combiner_root / "v1.2.3"
    version_dir.mkdir(parents=True)
    viewer_html = version_dir / "Top_Stats_Index.html"
    viewer_html.write_text("<html></html>")

    monkeypatch.setattr(core.apppaths, 'app_dir',
                        MagicMock(return_value=tmp_path))
    monkeypatch.setattr(core.apppaths, 'local_machine_dir',
                        MagicMock(return_value=tmp_path))

    config = MagicMock()
    config.raidreport_viewer_html = ""

    result = _resolve_viewer(config)
    assert result == viewer_html


def test_viewer_resolution_missing_raises_runtime_error(tmp_path, monkeypatch):
    """Neither config nor combiner fallback → RuntimeError mentioning Settings."""
    from core.raid_report_wiring import _resolve_viewer
    import core.apppaths

    empty_data = tmp_path / "RaidReportData"
    empty_data.mkdir()

    monkeypatch.setattr(core.apppaths, 'app_dir',
                        MagicMock(return_value=tmp_path))
    monkeypatch.setattr(core.apppaths, 'local_machine_dir',
                        MagicMock(return_value=tmp_path))

    config = MagicMock()
    config.raidreport_viewer_html = ""

    try:
        _resolve_viewer(config)
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "stats page" in str(e).lower()


def test_viewer_resolution_config_path_not_found_raises(tmp_path):
    """Config sets a path that doesn't exist → RuntimeError."""
    from core.raid_report_wiring import _resolve_viewer

    config = MagicMock()
    config.raidreport_viewer_html = str(tmp_path / "nope.html")

    try:
        _resolve_viewer(config)
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "could not find" in str(e).lower()


# ---------------------------------------------------------------------------
# selection safety — must never trigger viewer/combiner resolution
# ---------------------------------------------------------------------------

def test_select_recent_never_errors():
    """Selection functions are pure log filtering — no viewer/combiner resolution."""
    import pytest
    pytest.importorskip("PySide6")

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from core.raid_report_wiring import build_raid_report_tab
    from unittest.mock import MagicMock

    config = MagicMock()
    config.get_log_folders.return_value = []
    config.raidreport_cache_enabled = False
    config.raidreport_poison_tab = False

    tab = build_raid_report_tab(config)
    assert callable(tab._select_recent)
    result = tab._select_recent([])
    assert isinstance(result, list)
    assert len(result) == 0


def test_select_session_never_errors():
    import pytest
    pytest.importorskip("PySide6")

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from core.raid_report_wiring import build_raid_report_tab

    from unittest.mock import MagicMock

    config = MagicMock()
    config.get_log_folders.return_value = []
    config.raidreport_cache_enabled = False
    config.raidreport_poison_tab = False

    tab = build_raid_report_tab(config)
    assert callable(tab._select_session)
    result = tab._select_session([])
    assert isinstance(result, list)
    assert len(result) == 0


def test_select_today_never_errors():
    import pytest
    pytest.importorskip("PySide6")

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from core.raid_report_wiring import build_raid_report_tab
    from unittest.mock import MagicMock

    config = MagicMock()
    config.get_log_folders.return_value = []
    config.raidreport_cache_enabled = False
    config.raidreport_poison_tab = False

    tab = build_raid_report_tab(config)
    assert callable(tab._select_today)
    result = tab._select_today([])
    assert isinstance(result, list)
    assert len(result) == 0


# ---------------------------------------------------------------------------
# run_headless_raid_report
# ---------------------------------------------------------------------------

def test_run_headless_returns_html_path(tmp_path, monkeypatch):
    """run_headless_raid_report with faked pipeline returns HTML path.
    Uses recent_logs (12h rolling window), the product default."""
    from datetime import datetime
    from core.raid_report_wiring import run_headless_raid_report
    from unittest.mock import MagicMock

    # Log timestamps RELATIVE to now so they sit inside the 12h rolling
    # window at any time of day (fixed 10:00/11:00 stamps failed the suite
    # every evening after 22:00).
    from datetime import timedelta
    now = datetime.now()
    log_names = [f"{now - timedelta(hours=h):%Y%m%d-%H%M%S}.zevtc"
                 for h in (2, 1)]

    config = MagicMock()
    config.get_log_folders.return_value = [tmp_path]
    config.raidreport_cache_enabled = False
    config.get_raidreport_cache_dir.return_value = tmp_path / "cache"
    config.raidreport_viewer_html = ""
    config.get_raidreport_output_dir.return_value = tmp_path
    config.raidreport_poison_tab = False

    for name in log_names:
        (tmp_path / name).write_text("")

    combiner_root = tmp_path / "RaidReportData" / "combiner" / "v1"
    combiner_root.mkdir(parents=True)
    (combiner_root / "Top_Stats_Index.html").write_text("<html></html>")

    from core import raid_report
    monkeypatch.setattr(raid_report, 'bake_report',
                        lambda *a, **kw: None)
    monkeypatch.setattr(raid_report, 'summarize_tiddlers',
                        lambda _: {"fight_count": 2, "span": "12:00\u201313:00"})

    from core.combiner_manager import CombinerManager
    monkeypatch.setattr(CombinerManager, 'ensure_installed', lambda self: None)
    monkeypatch.setattr(CombinerManager, 'write_run_config', lambda *a, **kw: None)
    combiner_json = tmp_path / "combiner_out.json"
    combiner_json.write_text('[{"title":"test"}]')
    monkeypatch.setattr(CombinerManager, 'run',
                        lambda *a, **kw: combiner_json)

    from core.gw2ei_invoker import GW2EIInvoker
    monkeypatch.setattr(GW2EIInvoker, 'cache_key',
                        lambda self: ("2.50.0", "abc123def456"))
    # Pre-populate the cache so plan_report finds all logs as hits
    from core.raid_session import RaidReportCache
    import shutil
    cache = RaidReportCache(tmp_path / "cache")
    for name in log_names:
        log_file = tmp_path / name
        src = tmp_path / f"parsed_{log_file.stem}.json"
        src.write_text('{"tiddlers":[]}')
        cache.store(log_file, src, "2.50.0", "abc123def456")
    import core.apppaths
    monkeypatch.setattr(core.apppaths, 'app_dir',
                        MagicMock(return_value=tmp_path))
    monkeypatch.setattr(core.apppaths, 'local_machine_dir',
                        MagicMock(return_value=tmp_path))

    result = run_headless_raid_report(config)
    assert isinstance(result, Path)
    assert result.suffix == ".html"


# ---------------------------------------------------------------------------
# fresh-config end-to-end — no viewer configured, no combiner installed
# ---------------------------------------------------------------------------

def test_fresh_config_generate_with_mocked_download_succeeds(tmp_path, monkeypatch):
    """Fresh setup (no viewer_html, no combiner) — generate succeeds end-to-end
    with mocked download, no errors."""
    from datetime import datetime
    from core.raid_report_wiring import run_headless_raid_report
    from unittest.mock import MagicMock
    import core.apppaths
    import core.raid_report

    # Relative timestamps — see test_run_headless_returns_html_path.
    from datetime import timedelta
    now = datetime.now()
    log_names = [f"{now - timedelta(hours=h):%Y%m%d-%H%M%S}.zevtc"
                 for h in (2, 1)]

    config = MagicMock()
    config.get_log_folders.return_value = [tmp_path]
    config.raidreport_viewer_html = ""
    config.get_raidreport_cache_dir.return_value = tmp_path / "cache"
    config.get_raidreport_output_dir.return_value = tmp_path
    config.raidreport_poison_tab = False

    for name in log_names:
        (tmp_path / name).write_text("")

    from core.raid_session import RaidReportCache
    cache = RaidReportCache(tmp_path / "cache")
    for name in log_names:
        log_file = tmp_path / name
        src = tmp_path / f"parsed_{log_file.stem}.json"
        src.write_text('{"tiddlers":[]}')
        cache.store(log_file, src, "2.50.0", "abc123def456")

    combiner_install_root = tmp_path / "RaidReportData" / "combiner" / "v9.9.9"
    combiner_install_root.mkdir(parents=True)
    (combiner_install_root / "Top_Stats_Index.html").write_text("<html></html>")

    monkeypatch.setattr(core.apppaths, 'app_dir',
                        MagicMock(return_value=tmp_path))
    monkeypatch.setattr(core.apppaths, 'local_machine_dir',
                        MagicMock(return_value=tmp_path))

    monkeypatch.setattr(core.raid_report, 'bake_report',
                        lambda *a, **kw: None)
    monkeypatch.setattr(core.raid_report, 'summarize_tiddlers',
                        lambda _: {"fight_count": 2, "span": "10:00\u201311:00"})

    from core.combiner_manager import CombinerManager
    monkeypatch.setattr(CombinerManager, 'ensure_installed', lambda self: None)
    monkeypatch.setattr(CombinerManager, 'write_run_config', lambda *a, **kw: None)
    combiner_json = tmp_path / "combiner_out.json"
    combiner_json.write_text('[{"title":"test"}]')
    monkeypatch.setattr(CombinerManager, 'run',
                        lambda *a, **kw: combiner_json)

    from core.gw2ei_invoker import GW2EIInvoker
    monkeypatch.setattr(GW2EIInvoker, 'cache_key',
                        lambda self: ("2.50.0", "abc123def456"))

    result = run_headless_raid_report(config)
    assert isinstance(result, Path)
    assert result.suffix == ".html"


# ---------------------------------------------------------------------------
# tab-build smoke
# ---------------------------------------------------------------------------

def test_build_raid_report_tab_smoke():
    """build_raid_report_tab with stub config returns a QWidget.
    Skip gracefully if PySide6 is not installed."""
    import pytest
    pytest.importorskip("PySide6")

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from unittest.mock import MagicMock

    config = MagicMock()
    config.get_log_folders.return_value = []
    config.raidreport_cache_enabled = False
    config.raidreport_poison_tab = False

    from core.raid_report_wiring import build_raid_report_tab

    widget = build_raid_report_tab(config)
    assert widget is not None

    # Verify select_recent is a working callable
    assert callable(widget._select_recent)
    result = widget._select_recent([])
    assert isinstance(result, list)
    assert len(result) == 0


def test_publish_never_ships_recap_even_with_legacy_config_keys(tmp_path):
    """Configs written by <=1.8.12 have raidreportWrapup* = true baked in
    (save() persists every key). The recap is parked (operator,
    2026-07-19), so publish must ignore those keys entirely — no embed,
    no extra files, no mp3."""
    import inspect
    from core import raid_report_wiring

    src = "\n".join(
        inspect.getsource(fn)
        for fn in (raid_report_wiring.build_raid_report_tab,
                   raid_report_wiring.make_runner,
                   raid_report_wiring.publish_result))
    for banned in ("raidreport_wrapup", "build_wrapup",
                   "_fetch_zingers", "_generate_recap_mp3",
                   "embed=", "extra_files="):
        assert banned not in src, (
            f"parked recap resurfaced in publish wiring: {banned}"
        )


def test_publish_uses_dedicated_raid_report_destination(tmp_path, monkeypatch):
    from datetime import date
    from core.raid_report import ReportResult
    from core.raid_report_wiring import publish_result
    import core.discord_bot
    import core.report_publisher

    selected = []
    sent = []

    class FakeManager:
        def __init__(self, config):
            self.config = config

        def get_webhook(self, index):
            selected.append(index)
            return MagicMock(send_file=MagicMock())

    monkeypatch.setattr(
        core.discord_bot, "DiscordWebhookManager", FakeManager)
    monkeypatch.setattr(
        core.report_publisher, "publish_report",
        lambda *args, **kwargs: sent.append((args, kwargs)))

    html = tmp_path / "report.html"
    html.write_text("<html></html>")
    result = ReportResult(
        name="Fellas",
        html_path=html,
        json_path=tmp_path / "report.json",
        fight_count=18,
        span="20:26\u201320:26",
        generated_date=date(2026, 7, 20),
    )
    config = MagicMock()
    config.get_raid_report_discord_webhook_index.return_value = 2
    config.raidreport_always_zip = False

    publish_result(config, result)

    assert selected == [2]
    assert sent[0][1]["caption"] == "Fellas — 18 fights · 07/20/2026"
