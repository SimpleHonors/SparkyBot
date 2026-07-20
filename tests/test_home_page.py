"""Home page (slice 5): run panel by mode, activity feed, status bar,
drag-drop routing, balloon suppression.

Contract under test:
- the run panel exists ONLY while RaidReport/runMode == "run-button"
  (absent in manual mode — not built, not hidden) and flips LIVE through
  the settings-changed seam;
- the activity feed is model-backed (time, kind, text), skip rows name the
  exact failed filter produced at the decision site in process_log_file,
  and right-click Copy line puts the formatted row on the clipboard;
- posted feed events echo into the status bar ("Last: fight posted ...");
- the status-bar update banner subscribes to UpdateFlow.sig_launch_available
  ONLY (never sig_available, the manual-check signal);
- tray balloons are suppressed while the main window is visible and stay
  when it is hidden or was never built;
- dropping .evtc/.zevtc anywhere on Home pre-fills the Process Files queue
  and navigates there.
"""

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

pytest.importorskip("PySide6", reason="PySide6 not installed in this environment")

from PySide6.QtCore import QMimeData, QObject, QUrl, Signal
from PySide6.QtWidgets import QApplication, QGroupBox

import main as main_module
from core.activity_feed import ActivityFeedModel, KIND_ROLE, format_clock
from core.main_window import (
    MainWindow, PAGE_PROCESS_FILES, _log_paths_from_mime,
)
from core.tray_manager import TrayManager


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture()
def config(tmp_path):
    from core.config import Config
    cfg = Config(tmp_path / "config.properties")
    cfg.home_dir = tmp_path
    return cfg


# ---------------------------------------------------------------------------
# run panel presence by mode (incl. live flip)
# ---------------------------------------------------------------------------

def test_run_panel_present_in_run_button_mode(qapp, config):
    mw = MainWindow(config)
    assert mw.run_panel is not None
    assert mw.run_button is not None
    assert mw.run_button.text() == "Start Run"
    # slice-1 state pattern: class=primary, state flipped at run open
    assert mw.run_button.property("class") == "primary"


def test_run_panel_absent_in_manual_mode(qapp, config):
    config.update('RaidReport', 'runMode', 'manual')
    assert config.save()
    mw = MainWindow(config)
    assert mw.run_panel is None
    assert mw.run_button is None
    # Absent means NOT BUILT — no run group box exists anywhere on Home
    assert [g for g in mw.home_page.findChildren(QGroupBox)
            if g.title() == "Run"] == []


def test_run_panel_flips_live_with_settings_change(qapp, config):
    mw = MainWindow(config)
    assert mw.run_panel is not None

    config.update('RaidReport', 'runMode', 'manual')
    assert config.save()
    mw.settings_changed.emit()      # the engine re-emits through the shell
    assert mw.run_panel is None
    assert mw.run_button is None
    assert [g for g in mw.home_page.findChildren(QGroupBox)
            if g.title() == "Run"] == []

    config.update('RaidReport', 'runMode', 'run-button')
    assert config.save()
    mw.settings_changed.emit()
    assert mw.run_panel is not None
    assert mw.run_button.property("class") == "primary"


# ---------------------------------------------------------------------------
# activity feed model + skip reasons + status bar
# ---------------------------------------------------------------------------

def test_feed_skip_row_names_exact_filter(qapp, config):
    mw = MainWindow(config)
    mw.feed_file_event("C:/logs/20260719-201400.zevtc", "skipped_threshold",
                       "Too short (0:08 < 0:10 minimum)")
    index = mw.activity_model.index(0)
    assert mw.activity_model.data(index, KIND_ROLE) == "skipped"
    line = mw.activity_model.line(0)
    assert "Too short (0:08 < 0:10 minimum)" in line
    assert "20260719-201400.zevtc" in line


def test_feed_posted_row_updates_status_bar_last(qapp, config):
    when = datetime(2026, 7, 19, 20, 14, 0)
    mw = MainWindow(config, clock=lambda: when)
    assert mw.status_last_label.text() == ""
    mw.feed_file_event("C:/logs/fight.zevtc", "success")
    assert mw.status_last_label.text() == "Last: fight posted 8:14 PM"
    index = mw.activity_model.index(0)
    assert mw.activity_model.data(index, KIND_ROLE) == "posted"
    # The empty-feed hint disappears once rows exist
    assert not mw.activity_hint.isVisibleTo(mw.home_page)


def test_feed_shows_processing_before_the_final_outcome(qapp, config):
    mw = MainWindow(config)
    mw.feed_event(
        "processing", "New fight detected — processing fight.zevtc")
    mw.feed_file_event("C:/logs/fight.zevtc", "success")
    assert "Posted" in mw.activity_model.line(0)
    assert "Processing" in mw.activity_model.line(1)
    assert "New fight detected" in mw.activity_model.line(1)


def test_feed_error_rows(qapp, config):
    mw = MainWindow(config)
    mw.feed_file_event("a.zevtc", "error_discord")
    mw.feed_file_event("b.zevtc", "error_parse")
    assert mw.activity_model.data(mw.activity_model.index(0), KIND_ROLE) == "error"
    assert "Discord post failed" in mw.activity_model.line(1)
    assert "failed to process" in mw.activity_model.line(0)


def test_feed_model_caps_rows(qapp):
    model = ActivityFeedModel(max_rows=5)
    for i in range(8):
        model.add("info", f"row {i}", datetime(2026, 7, 19, 8, 0, i))
    assert model.rowCount() == 5
    assert "row 7" in model.line(0)      # newest kept, oldest trimmed
    assert "row 3" in model.line(4)


def test_copy_feed_line_sets_clipboard(qapp, config):
    when = datetime(2026, 7, 19, 21, 5, 44)
    mw = MainWindow(config, clock=lambda: when)
    mw.feed_event("skipped", "x.zevtc — Too few downs (2 < 5 minimum)")
    mw.copy_feed_line(0)
    text = QApplication.clipboard().text()
    assert "Too few downs (2 < 5 minimum)" in text
    assert format_clock(when) in text


def test_ai_feed_rows_route_through_feed_event(qapp, config):
    """Commentary/voice rows arrive via the pipeline_event path — only
    emitted while AI runs, so the feed API itself never gates them."""
    mw = MainWindow(config)
    mw.feed_event("commentary", "Commentary posted")
    mw.feed_event("voice", "Voice clip attached")
    assert mw.activity_model.data(mw.activity_model.index(1), KIND_ROLE) == "commentary"
    assert mw.activity_model.data(mw.activity_model.index(0), KIND_ROLE) == "voice"
    # AI rows never claim the "Last: fight posted" slot
    assert mw.status_last_label.text() == ""


# ---------------------------------------------------------------------------
# skip reasons are produced AT THE DECISION SITE (process_log_file)
# ---------------------------------------------------------------------------

def _pipeline_stubs(tmp_path, monkeypatch, *, duration_ms, downs, damage):
    json_file = tmp_path / "fight.json"
    json_file.write_text("{}", encoding="utf-8")

    class _StubReport:
        def __init__(self, data):
            self.duration_ms = duration_ms
            self.total_downs = downs
            self.total_damage = damage

        def set_embed_color(self, color):
            pass

    monkeypatch.setattr(main_module, "FightReport", _StubReport)
    monkeypatch.setattr(main_module, "_callout_cooldown", object())

    config = SimpleNamespace(
        min_fight_duration=10, min_fight_downs=5, min_fight_total_dmg=50000,
        embed_color=0x00A86B, raidreport_cache_enabled=False,
    )
    gw2ei = SimpleNamespace(parse_file=lambda p: json_file)
    return config, gw2ei


def test_skip_reason_too_short(tmp_path, monkeypatch):
    config, gw2ei = _pipeline_stubs(
        tmp_path, monkeypatch, duration_ms=8000, downs=9, damage=99999)
    events = []
    result = main_module.process_log_file(
        tmp_path / "a.zevtc", config, gw2ei, None,
        events=lambda kind, text: events.append((kind, text)))
    assert result is main_module.ProcessResult.SKIPPED_THRESHOLD
    assert events == [("skipped", "Too short (0:08 < 0:10 minimum)")]


def test_skip_reason_too_few_downs(tmp_path, monkeypatch):
    config, gw2ei = _pipeline_stubs(
        tmp_path, monkeypatch, duration_ms=60000, downs=2, damage=99999)
    events = []
    result = main_module.process_log_file(
        tmp_path / "a.zevtc", config, gw2ei, None,
        events=lambda kind, text: events.append((kind, text)))
    assert result is main_module.ProcessResult.SKIPPED_THRESHOLD
    assert events == [("skipped", "Too few downs (2 < 5 minimum)")]


def test_skip_reason_too_little_damage(tmp_path, monkeypatch):
    config, gw2ei = _pipeline_stubs(
        tmp_path, monkeypatch, duration_ms=60000, downs=6, damage=32100)
    events = []
    result = main_module.process_log_file(
        tmp_path / "a.zevtc", config, gw2ei, None,
        events=lambda kind, text: events.append((kind, text)))
    assert result is main_module.ProcessResult.SKIPPED_THRESHOLD
    assert events == [("skipped", "Too little damage (32,100 < 50,000 minimum)")]


def test_format_mmss():
    assert main_module._format_mmss(8) == "0:08"
    assert main_module._format_mmss(10) == "0:10"
    assert main_module._format_mmss(75) == "1:15"
    assert main_module._format_mmss(-3) == "0:00"


# ---------------------------------------------------------------------------
# balloon suppression while the window is visible
# ---------------------------------------------------------------------------

def _fake_window(visible: bool):
    feed = []
    return SimpleNamespace(
        feed=feed,
        feed_file_event=lambda f, r, d="": feed.append((f, r, d)),
        isVisible=lambda: visible,
    )


def _fake_tray():
    messages = []
    return SimpleNamespace(
        messages=messages,
        show_message=lambda title, msg, icon=None, timeout=3000:
            messages.append((title, msg)),
        MessageIcon=TrayManager.MessageIcon,
    )


def test_balloons_suppressed_while_window_visible():
    window, tray = _fake_window(True), _fake_tray()
    app = SimpleNamespace(settings_window=window, tray_manager=tray)
    main_module.SparkyBotApp._on_file_processed(app, "a.zevtc", "success", "")
    assert window.feed == [("a.zevtc", "success", "")]   # feed always fed
    assert tray.messages == []                           # balloon suppressed


def test_balloons_stay_when_window_hidden():
    window, tray = _fake_window(False), _fake_tray()
    app = SimpleNamespace(settings_window=window, tray_manager=tray)
    main_module.SparkyBotApp._on_file_processed(
        app, "a.zevtc", "skipped_threshold", "Too short (0:08 < 0:10 minimum)")
    assert window.feed == [("a.zevtc", "skipped_threshold",
                            "Too short (0:08 < 0:10 minimum)")]
    assert len(tray.messages) == 1
    assert "Too short (0:08 < 0:10 minimum)" in tray.messages[0][1]


def test_balloons_stay_when_window_never_built():
    tray = _fake_tray()
    app = SimpleNamespace(settings_window=None, tray_manager=tray)
    main_module.SparkyBotApp._on_file_processed(app, "a.zevtc", "success", "")
    assert [t for t, _ in tray.messages] == ["Fight Report Sent"]


# ---------------------------------------------------------------------------
# status-bar update banner (sig_launch_available ONLY)
# ---------------------------------------------------------------------------

class _FakeFlow(QObject):
    sig_launch_available = Signal(str, dict)
    sig_available = Signal(str, dict)     # manual check — must stay unhooked

    def __init__(self):
        super().__init__()
        self.started = []

    def start_update(self, release_data, version):
        self.started.append((version, release_data))


def test_update_banner_from_launch_check_only(qapp, config):
    flow = _FakeFlow()
    mw = MainWindow(config, update_flow=flow)
    assert not mw.update_banner.isVisibleTo(mw)

    # The manual-check signal must not surface the banner
    flow.sig_available.emit("8.8.8", {})
    assert not mw.update_banner.isVisibleTo(mw)

    release = {"tag_name": "v9.9.9"}
    flow.sig_launch_available.emit("9.9.9", release)
    assert mw.update_banner.isVisibleTo(mw)
    assert "9.9.9" in mw.update_banner.text()

    mw.update_banner.click()
    assert flow.started == [("9.9.9", release)]
    assert not mw.update_banner.isVisibleTo(mw)


def test_banner_never_subscribes_manual_check_signal():
    src = (_ROOT / "core" / "main_window.py").read_text(encoding="utf-8")
    assert ".sig_launch_available.connect" in src
    assert ".sig_available.connect" not in src, \
        "the banner must subscribe to sig_launch_available ONLY"


# ---------------------------------------------------------------------------
# drag-drop routing to Process Files
# ---------------------------------------------------------------------------

def test_home_page_accepts_drops(qapp, config):
    mw = MainWindow(config)
    assert mw.home_page.acceptDrops()


def test_mime_filter_takes_only_log_files(qapp):
    mime = QMimeData()
    mime.setUrls([
        QUrl.fromLocalFile("/tmp/a.zevtc"),
        QUrl.fromLocalFile("/tmp/b.EVTC"),
        QUrl.fromLocalFile("/tmp/c.txt"),
    ])
    paths = _log_paths_from_mime(mime)
    assert [Path(p).name for p in paths] == ["a.zevtc", "b.EVTC"]
    assert _log_paths_from_mime(QMimeData()) == []


def test_drop_prefills_queue_and_navigates(qapp, config):
    mw = MainWindow(config)
    mw.home_page.sig_logs_dropped.emit(["/tmp/a.zevtc", "/tmp/b.evtc"])
    assert mw.current_page == PAGE_PROCESS_FILES
    queue = mw.process_files_widget.file_list
    assert queue.count() == 2
    role = mw.process_files_widget.PATH_ROLE
    assert queue.item(0).data(role) == "/tmp/a.zevtc"
    # Duplicates are ignored, same as the drop zone
    mw.home_page.sig_logs_dropped.emit(["/tmp/a.zevtc"])
    assert queue.count() == 2
