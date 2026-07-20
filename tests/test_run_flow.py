"""Run session UI (slice 6): Start/End Run over discover_logs.

Contract under test:
- Start Run persists run_state.json, starts the watcher only if stopped,
  and morphs the primary button (slice-1 state pattern) with the dim
  "Skipped fights still count for raid reports" hint;
- fights-so-far comes from discover_logs filtered to the run window (the
  report's own source of truth), refreshed on demand;
- End Run has exactly one confirm; zero fights posts NOTHING and says so
  quietly in the feed; a real end routes the window's fights into the
  existing report path with the remembered RaidReport/runAutoPost choice;
- inline stage-weighted progress reuses the Raid Report page's weights;
- resume is silent while the newest in-window log is < 6h old, otherwise a
  quiet finish-or-discard banner appears (no popup); stale ending pins
  ended_at to the last fight;
- quitting with an open run runs the 3-way confirm (button roles checked);
- manual mode has zero run UI and a dormant run_session (no state file).

All clocks are injected — no wall-clock sleeps.
"""

import configparser
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

pytest.importorskip("PySide6", reason="PySide6 not installed in this environment")

from PySide6.QtWidgets import QApplication, QMessageBox

from core.activity_feed import KIND_ROLE
from core.config import Config
from core.main_window import MainWindow
from core.run_session import RunSession, STATE_FILENAME

T_START = datetime(2026, 7, 19, 20, 0, 0)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _make_config(tmp_path):
    cfg = Config(tmp_path / "config.properties")
    cfg.home_dir = tmp_path
    logdir = tmp_path / "logs"
    logdir.mkdir()
    cfg.update('Paths', 'logFolder', str(logdir))
    assert cfg.save()
    return cfg, logdir


def _add_log(logdir, ts):
    path = logdir / f"{ts:%Y%m%d-%H%M%S}.zevtc"
    path.write_bytes(b"log")
    return path


def _window(qapp, tmp_path, now=T_START):
    cfg, logdir = _make_config(tmp_path)
    state = {"now": now}
    mw = MainWindow(cfg, clock=lambda: state["now"])
    return mw, cfg, logdir, state


def _feed_lines(mw):
    return [mw.activity_model.line(r)
            for r in range(mw.activity_model.rowCount())]


def _feed_kinds(mw):
    return [mw.activity_model.data(mw.activity_model.index(r), KIND_ROLE)
            for r in range(mw.activity_model.rowCount())]


# ---------------------------------------------------------------------------
# Start Run
# ---------------------------------------------------------------------------

def test_start_run_persists_state_and_starts_watcher(qapp, tmp_path):
    mw, cfg, logdir, state = _window(qapp, tmp_path)
    toggles = []
    mw.watcher_toggled.connect(lambda: toggles.append(1))

    mw.run_button.click()
    assert (tmp_path / STATE_FILENAME).exists()
    assert toggles == [1]                       # watcher was stopped
    assert mw.run_button.text() == "End Run…"
    assert mw.run_button.property("state") == "running"
    assert mw.run_hint_label.text() == \
        "Skipped fights still count for raid reports."
    assert "run" in _feed_kinds(mw)
    assert any("Run started" in line for line in _feed_lines(mw))


def test_start_run_leaves_running_watcher_alone(qapp, tmp_path):
    mw, cfg, logdir, state = _window(qapp, tmp_path)
    mw.set_watcher_state(True)
    toggles = []
    mw.watcher_toggled.connect(lambda: toggles.append(1))
    mw.run_button.click()
    assert toggles == []
    assert mw._run_session.is_open


def test_fights_so_far_from_discover_logs(qapp, tmp_path):
    mw, cfg, logdir, state = _window(qapp, tmp_path)
    mw.run_button.click()                       # run opens at 20:00
    _add_log(logdir, T_START - timedelta(hours=1))          # before the run
    _add_log(logdir, T_START + timedelta(minutes=10))
    _add_log(logdir, T_START + timedelta(minutes=30))
    state["now"] = T_START + timedelta(hours=1)
    mw._refresh_run_counter()
    assert "2 fights" in mw.run_status_label.text()
    assert "1 h 00 m" in mw.run_status_label.text()
    # A processed-file feed event also refreshes the counter
    _add_log(logdir, T_START + timedelta(minutes=45))
    mw.feed_file_event("x.zevtc", "skipped_threshold", "Too short (0:08 < 0:10 minimum)")
    assert "3 fights" in mw.run_status_label.text()


def test_long_open_run_gets_passive_warn_hint(qapp, tmp_path):
    mw, cfg, logdir, state = _window(qapp, tmp_path)
    mw.run_button.click()
    assert not mw.run_stale_hint.isVisibleTo(mw.run_panel)
    state["now"] = T_START + timedelta(hours=13)
    mw._update_run_panel_clock()
    assert mw.run_stale_hint.isVisibleTo(mw.run_panel)
    assert "forgot to end it?" in mw.run_stale_hint.text()


# ---------------------------------------------------------------------------
# End Run
# ---------------------------------------------------------------------------

def test_end_run_zero_fights_posts_nothing(qapp, tmp_path):
    mw, cfg, logdir, state = _window(qapp, tmp_path)
    mw.run_button.click()
    mw._exec_end_run_dialog = lambda count, elapsed: (True, False)
    reports = []
    mw._start_run_report = lambda *a, **k: reports.append(a)

    mw.run_button.click()                       # End Run
    assert not (tmp_path / STATE_FILENAME).exists()
    assert reports == []                        # nothing generated or posted
    assert any("nothing was posted" in line for line in _feed_lines(mw))
    assert mw.run_button.text() == "Start Run"


def test_end_run_routes_window_fights_into_report_path(qapp, tmp_path):
    mw, cfg, logdir, state = _window(qapp, tmp_path)
    mw.run_button.click()
    _add_log(logdir, T_START - timedelta(hours=1))          # out of window
    _add_log(logdir, T_START + timedelta(minutes=10))
    _add_log(logdir, T_START + timedelta(minutes=30))
    state["now"] = T_START + timedelta(hours=1)

    mw._exec_end_run_dialog = lambda count, elapsed: (True, False)
    calls = []
    mw._start_run_report = (
        lambda selected, name, auto_post, quit_after=False:
            calls.append((selected, name, auto_post)))
    mw.run_button.click()

    assert len(calls) == 1
    selected, name, auto_post = calls[0]
    assert [log.timestamp for log in selected] == [
        T_START + timedelta(minutes=10), T_START + timedelta(minutes=30)]
    assert name == "Raid Report 2026-07-19 (2 fights)"
    assert auto_post is False
    assert not (tmp_path / STATE_FILENAME).exists()
    # The unchecked box was remembered into RaidReport/runAutoPost
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(tmp_path / "config.properties")
    assert parser.get('RaidReport', 'runAutoPost') == 'false'
    assert cfg.run_auto_post is False


def test_end_run_confirm_cancel_keeps_run_open(qapp, tmp_path):
    mw, cfg, logdir, state = _window(qapp, tmp_path)
    mw.run_button.click()
    mw._exec_end_run_dialog = lambda count, elapsed: (False, True)
    mw.run_button.click()
    assert (tmp_path / STATE_FILENAME).exists()
    assert mw.run_button.text() == "End Run…"


def test_inline_progress_is_stage_weighted(qapp, tmp_path):
    mw, cfg, logdir, state = _window(qapp, tmp_path)
    mw._on_run_progress("parse", 1, 4, "")
    assert mw.run_progress.value() == 21        # 5 + 65 * 0.25
    assert "Reading fight 1 of 4" in mw.run_status_label.text()
    mw._on_run_progress("bake", 1, 1, "")
    assert mw.run_progress.value() == 99        # never 100 while running


def test_run_done_and_error_paths(qapp, tmp_path):
    mw, cfg, logdir, state = _window(qapp, tmp_path)
    result = SimpleNamespace(name="Raid Report 2026-07-19 (2 fights)")

    mw._run_report_busy = True
    mw._run_auto_posted = True
    mw._on_run_done(result)
    assert mw._run_report_busy is False
    assert any("Raid report posted" in line for line in _feed_lines(mw))
    assert mw.run_status_label.property("state") == "ok"
    assert "Last: report posted" in mw.status_last_label.text()

    dialogs = []
    mw._show_run_error_dialog = lambda message: dialogs.append(message)
    mw._run_report_busy = True
    mw._on_run_error("boom happened")
    assert mw._run_report_busy is False
    assert dialogs == ["boom happened"]         # plain dialog
    assert any("End Run report failed" in line for line in _feed_lines(mw))
    assert mw.run_status_label.property("state") == "error"


# ---------------------------------------------------------------------------
# resume / stale banner
# ---------------------------------------------------------------------------

def test_resume_silently_when_newest_log_is_fresh(qapp, tmp_path):
    cfg, logdir = _make_config(tmp_path)
    seed = RunSession(tmp_path, clock=lambda: T_START)
    seed.start()                                # run opened at 20:00
    _add_log(logdir, T_START + timedelta(minutes=30))

    now = T_START + timedelta(hours=4)          # newest log 3.5h old (< 6h)
    mw = MainWindow(cfg, clock=lambda: now)
    assert mw.run_button.text() == "End Run…"
    assert not mw.run_stale_banner.isVisibleTo(mw.run_panel)
    assert any("Run resumed" in line for line in _feed_lines(mw))


def test_stale_run_shows_quiet_banner(qapp, tmp_path):
    cfg, logdir = _make_config(tmp_path)
    seed = RunSession(tmp_path, clock=lambda: T_START)
    seed.start()
    _add_log(logdir, T_START + timedelta(hours=1))

    now = T_START + timedelta(hours=30)         # newest log 29h old
    mw = MainWindow(cfg, clock=lambda: now)
    assert mw.run_stale_banner.isVisibleTo(mw.run_panel)
    assert "still open" in mw.run_stale_label.text()
    assert "1 fight" in mw.run_stale_label.text()
    # The run itself is still open on the panel meanwhile
    assert mw.run_button.text() == "End Run…"

    mw.run_stale_discard_button.click()
    assert not (tmp_path / STATE_FILENAME).exists()
    assert not mw.run_stale_banner.isVisibleTo(mw.run_panel)
    assert mw.run_button.text() == "Start Run"
    assert any("Run discarded" in line for line in _feed_lines(mw))


def test_stale_end_pins_window_to_last_fight(qapp, tmp_path):
    cfg, logdir = _make_config(tmp_path)
    seed = RunSession(tmp_path, clock=lambda: T_START)
    seed.start()
    _add_log(logdir, T_START + timedelta(hours=1))
    _add_log(logdir, T_START + timedelta(hours=2))

    now = T_START + timedelta(hours=30)
    mw = MainWindow(cfg, clock=lambda: now)
    calls = []
    mw._start_run_report = (
        lambda selected, name, auto_post, quit_after=False:
            calls.append((selected, name, auto_post)))
    mw.run_stale_end_button.click()

    assert len(calls) == 1
    selected, name, auto_post = calls[0]
    assert [log.timestamp for log in selected] == [
        T_START + timedelta(hours=1), T_START + timedelta(hours=2)]
    assert name == "Raid Report 2026-07-19 (2 fights)"
    assert auto_post is True                    # remembered default
    assert not (tmp_path / STATE_FILENAME).exists()


def test_stale_end_with_zero_fights_discards_quietly(qapp, tmp_path):
    cfg, logdir = _make_config(tmp_path)
    seed = RunSession(tmp_path, clock=lambda: T_START)
    seed.start()
    now = T_START + timedelta(hours=30)
    mw = MainWindow(cfg, clock=lambda: now)
    reports = []
    mw._start_run_report = lambda *a, **k: reports.append(a)
    mw.run_stale_end_button.click()
    assert reports == []
    assert not (tmp_path / STATE_FILENAME).exists()
    assert any("nothing was posted" in line for line in _feed_lines(mw))


# ---------------------------------------------------------------------------
# quit with an open run (3-way confirm)
# ---------------------------------------------------------------------------

def test_quit_confirm_button_roles(qapp, tmp_path):
    mw, cfg, logdir, state = _window(qapp, tmp_path)
    box, buttons = mw._build_quit_confirm()
    assert box.buttonRole(buttons["tray"]) == QMessageBox.ButtonRole.AcceptRole
    assert box.buttonRole(buttons["end"]) == \
        QMessageBox.ButtonRole.DestructiveRole
    assert box.buttonRole(buttons["cancel"]) == \
        QMessageBox.ButtonRole.RejectRole
    assert buttons["tray"].text() == "Keep running in tray"
    assert buttons["end"].text().replace("&&", "&") == "End run & quit"


def test_close_with_open_run_offers_three_ways(qapp, tmp_path):
    mw, cfg, logdir, state = _window(qapp, tmp_path)
    mw.run_button.click()                       # open a run
    quits, ends = [], []
    mw._quit_app_now = lambda: quits.append(1)
    mw._end_run_and_quit = lambda: ends.append(1)
    mw.show()

    mw._confirm_quit_with_run = lambda: "cancel"
    assert mw.close() is False                  # event ignored
    assert mw.isVisible()
    assert quits == [] and ends == []

    mw._confirm_quit_with_run = lambda: "tray"
    assert mw.close() is False
    assert not mw.isVisible()                   # kept running, hidden
    assert quits == [] and ends == []

    mw.show()
    mw._confirm_quit_with_run = lambda: "end"
    assert mw.close() is False
    assert ends == [1]
    assert quits == []


def test_file_exit_with_open_run_confirms_too(qapp, tmp_path):
    mw, cfg, logdir, state = _window(qapp, tmp_path)
    mw.run_button.click()
    quits, ends = [], []
    mw._quit_app_now = lambda: quits.append(1)
    mw._end_run_and_quit = lambda: ends.append(1)

    mw._confirm_quit_with_run = lambda: "cancel"
    mw._quit_app()
    assert quits == [] and ends == []

    mw._confirm_quit_with_run = lambda: "end"
    mw._quit_app()
    assert ends == [1] and quits == []


def test_end_run_and_quit_zero_fights_quits_directly(qapp, tmp_path):
    mw, cfg, logdir, state = _window(qapp, tmp_path)
    mw.run_button.click()
    quits = []
    mw._quit_app_now = lambda: quits.append(1)
    mw._end_run_and_quit()
    assert quits == [1]
    assert not (tmp_path / STATE_FILENAME).exists()
    assert any("nothing was posted" in line for line in _feed_lines(mw))


def test_quit_without_run_never_confirms(qapp, tmp_path):
    mw, cfg, logdir, state = _window(qapp, tmp_path)
    confirms = []
    mw._confirm_quit_with_run = lambda: confirms.append(1) or "cancel"
    quits = []
    mw._quit_app_now = lambda: quits.append(1)
    mw._quit_app()
    assert confirms == []
    assert quits == [1]


# ---------------------------------------------------------------------------
# manual mode: zero run UI, dormant module
# ---------------------------------------------------------------------------

def test_manual_mode_is_fully_dormant(qapp, tmp_path):
    cfg, logdir = _make_config(tmp_path)
    cfg.update('RaidReport', 'runMode', 'manual')
    assert cfg.save()
    mw = MainWindow(cfg)
    assert mw.run_panel is None
    assert mw.run_button is None
    assert mw._run_session is None
    assert not (tmp_path / STATE_FILENAME).exists()   # no state file created
    assert mw._run_confirm_needed() is False


def test_live_flip_to_run_button_revives_open_run(qapp, tmp_path):
    cfg, logdir = _make_config(tmp_path)
    cfg.update('RaidReport', 'runMode', 'manual')
    assert cfg.save()
    seed = RunSession(tmp_path, clock=lambda: T_START)
    seed.start()                                # leftover open run on disk

    now = T_START + timedelta(hours=1)
    mw = MainWindow(cfg, clock=lambda: now)
    assert mw._run_session is None              # manual: never reads it

    cfg.update('RaidReport', 'runMode', 'run-button')
    assert cfg.save()
    mw.settings_changed.emit()
    assert mw.run_panel is not None
    assert mw._run_session.is_open
    assert mw.run_button.text() == "End Run…"

    # ...and flipping back to manual removes the UI, leaving state on disk
    cfg.update('RaidReport', 'runMode', 'manual')
    assert cfg.save()
    mw.settings_changed.emit()
    assert mw.run_panel is None
    assert mw._run_session is None
    assert (tmp_path / STATE_FILENAME).exists()  # untouched, just dormant


# ---------------------------------------------------------------------------
# RaidReport/runAutoPost in the settings engine + dialog
# ---------------------------------------------------------------------------

def test_run_autopost_defaults_true(tmp_path):
    cfg = Config(tmp_path / "config.properties")
    assert cfg.run_auto_post is True


def test_run_autopost_round_trip_and_dirty_tracking(qapp, tmp_path,
                                                    monkeypatch):
    from core.gui_settings import SettingsWindow
    from core.settings_dialog import SettingsDialog
    monkeypatch.setattr(SettingsWindow, "_check_sparkybot_status",
                        lambda self: None)
    monkeypatch.setattr(SettingsWindow, "_check_ei_status", lambda self: None)

    cfg = Config(tmp_path / "config.properties")
    cfg.home_dir = tmp_path
    engine = SettingsWindow(cfg)
    dialog = SettingsDialog(engine)
    dialog.open_dialog()

    assert engine.run_autopost.isChecked()      # default true
    engine.run_autopost.setChecked(False)
    assert dialog.apply_button.isEnabled()      # registered in _TRACKED_
    assert dialog._apply()
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(tmp_path / "config.properties")
    assert parser.get('RaidReport', 'runAutoPost') == 'false'
    assert cfg.run_auto_post is False
    assert Config(tmp_path / "config.properties").run_auto_post is False

    # Reload-on-open reflects the stored value; reverting round-trips
    dialog.reject()
    dialog.open_dialog()
    assert not engine.run_autopost.isChecked()
    assert not dialog.apply_button.isEnabled()
    engine.run_autopost.setChecked(True)
    assert dialog._apply()
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(tmp_path / "config.properties")
    assert parser.get('RaidReport', 'runAutoPost') == 'true'
    dialog.reject()


def test_run_autopost_grays_in_manual_mode(qapp, tmp_path, monkeypatch):
    from core.gui_settings import SettingsWindow
    monkeypatch.setattr(SettingsWindow, "_check_sparkybot_status",
                        lambda self: None)
    monkeypatch.setattr(SettingsWindow, "_check_ei_status", lambda self: None)
    cfg = Config(tmp_path / "config.properties")
    cfg.home_dir = tmp_path
    engine = SettingsWindow(cfg)
    assert engine.run_autopost.isEnabled()
    engine.runmode_manual.setChecked(True)
    assert not engine.run_autopost.isEnabled()
    engine.runmode_run_button.setChecked(True)
    assert engine.run_autopost.isEnabled()
