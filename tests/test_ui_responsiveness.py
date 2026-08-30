"""Ticket 6c8e08f9 — UI responsiveness regressions.

Every test here proves a GUI-thread entry point returns immediately while a
deliberately-blocked slow dependency (log-folder scan on a network share,
pip install) finishes on a worker thread and lands via signal.
"""

import os
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from core.raid_session import LogInfo


# A blocked scan holds this long; GUI calls must return way inside it.
BLOCK_SECS = 5.0
FAST_SECS = 1.0


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def _pump_until(qt_app, predicate, timeout=8.0):
    """Spin the event loop until predicate() or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _fake_logs(n=2):
    now = datetime.now()
    return [
        LogInfo(path=Path(f"/fake/log{i}.zevtc"),
                timestamp=now.replace(microsecond=i),
                source="filename")
        for i in range(n)
    ]


def _make_tab(discover):
    from core.raid_report_tab import RaidReportTab
    return RaidReportTab(
        discover=discover,
        select_session=lambda logs: logs,
        select_today=lambda logs: logs,
        select_recent=lambda logs: logs,
        runner_factory=lambda: None,
        publish=lambda result: None,
    )


def test_raid_report_tab_constructs_before_slow_discover_completes(qt_app):
    """Construction (and its initial refresh) must not wait for the scan."""
    release = threading.Event()
    logs = _fake_logs()

    def slow_discover():
        release.wait(BLOCK_SECS)
        return logs

    t0 = time.monotonic()
    tab = _make_tab(slow_discover)
    elapsed = time.monotonic() - t0
    try:
        assert elapsed < FAST_SECS, (
            f"RaidReportTab construction blocked {elapsed:.2f}s on discover")
        assert tab._table.rowCount() == 0   # nothing landed yet

        release.set()
        assert _pump_until(qt_app, lambda: tab._table.rowCount() == len(logs))
        # The initial "recent" quick-pick still applies once logs land.
        assert len(tab.selected_logs()) == len(logs)
    finally:
        release.set()
        tab.deleteLater()


def test_raid_report_tab_rescan_returns_before_scan_and_preserves_ticks(qt_app):
    logs = _fake_logs()
    release = threading.Event()
    calls = {"n": 0}

    def discover():
        calls["n"] += 1
        if calls["n"] > 1:          # only the rescan is slow
            release.wait(BLOCK_SECS)
        return logs

    tab = _make_tab(discover)
    try:
        assert _pump_until(qt_app, lambda: tab._table.rowCount() == len(logs))
        checked_before = {log.path for log in tab.selected_logs()}
        assert checked_before

        t0 = time.monotonic()
        tab.rescan()
        elapsed = time.monotonic() - t0
        assert elapsed < FAST_SECS, f"rescan blocked {elapsed:.2f}s"

        release.set()
        assert _pump_until(qt_app, lambda: calls["n"] == 2 and
                           {l.path for l in tab.selected_logs()} == checked_before)
    finally:
        release.set()
        tab.deleteLater()


def test_run_counter_refresh_returns_before_slow_folder_scan(qt_app, tmp_path):
    """_refresh_run_counter fires every 60s while a run is open; a network
    share hiccup in discover_logs must never freeze the GUI thread."""
    from core.config import Config
    from core.main_window import MainWindow

    config = Config(tmp_path / "config.properties")
    window = MainWindow(config)
    release = threading.Event()
    logs = _fake_logs(3)
    try:
        window._run_session = SimpleNamespace(
            is_open=True,
            started_at=datetime.now(),
            last_activity_at=None,
            recorded_logs=[],
            elapsed=lambda: datetime.now() - datetime.now(),
        )
        window._collect_run_logs = lambda logs=None, **kw: logs or []

        def slow_discover():
            release.wait(BLOCK_SECS)
            return logs

        window._discover_run_logs = slow_discover

        t0 = time.monotonic()
        window._refresh_run_counter()
        elapsed = time.monotonic() - t0
        assert elapsed < FAST_SECS, (
            f"_refresh_run_counter blocked {elapsed:.2f}s on the scan")
        assert window._run_fight_count == 0    # result not in yet

        release.set()
        assert _pump_until(
            qt_app, lambda: window._run_fight_count == len(logs))

        # Single-flight: dropped duplicate while a scan is out never wedges
        # the guard — a later refresh scans again.
        assert window._run_scan_inflight is False
    finally:
        release.set()
        window.deleteLater()


def test_wizard_pip_install_runs_off_gui_thread(qt_app, monkeypatch):
    import core.setup_wizard as setup_wizard
    from core.setup_wizard import DependenciesPage

    page = DependenciesPage()
    release = threading.Event()

    monkeypatch.setattr(page, "_get_requirements", lambda: ["fakepkg>=1.0"])
    check_results = [([], ["fakepkg>=1.0"]),          # pre-install check
                     (["fakepkg (1.0)"], [])]          # post-install re-check
    monkeypatch.setattr(
        page, "_check_installed", lambda reqs: check_results.pop(0))

    def fake_pip(cmd, **kwargs):
        release.wait(BLOCK_SECS)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(setup_wizard.subprocess, "run", fake_pip)

    try:
        t0 = time.monotonic()
        page._check_and_install(auto=False)
        elapsed = time.monotonic() - t0
        assert elapsed < FAST_SECS, f"pip install blocked GUI {elapsed:.2f}s"
        assert "Installing" in page.status_label.text()

        release.set()
        assert _pump_until(
            qt_app,
            lambda: "installed successfully" in page.status_label.text())
        assert "fakepkg" in page.details_label.text()
    finally:
        release.set()
        page.deleteLater()
