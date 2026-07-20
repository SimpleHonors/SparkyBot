"""MainWindow shell (slices 3-4): sidebar + stack + menu bar + status bar.

The contract under test:
- the shell is a QMainWindow with File/Tools/Help menus (mnemonics), a
  QListWidget sidebar driving a QStackedWidget, and a status-bar watcher
  indicator styled via theme dynamic properties;
- the legacy SettingsWindow survives as a headless engine, built lazily on
  first need — never at window open — with its promoted ACTION tabs
  (Raid Report, Calibration) re-mounted as sidebar pages and everything
  else re-homed by the modal SettingsDialog (see test_settings_dialog.py);
- the "Settings" sidebar entry, Tools menu, and File > Settings...
  (Ctrl+,) all open the modal dialog; the sidebar page itself is a stub
  hosting a reopen button;
- the SettingsWindow GitHub status checks fire on first VIEW of the
  dialog's Application page, never at construction;
- main.py keeps the `settings_window` attribute contract but constructs
  the shell, with quit-on-last-window-closed handled explicitly.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

pytest.importorskip("PySide6", reason="PySide6 not installed in this environment")

from PySide6.QtWidgets import QApplication, QListWidget, QMainWindow, QStackedWidget

from core import theme
from core.main_window import (
    MainWindow, NAV_ENTRIES,
    PAGE_HOME, PAGE_RAID_REPORT, PAGE_PROCESS_FILES, PAGE_CALIBRATION,
    PAGE_SETTINGS,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _stub_config(**overrides):
    """Cheap config double for shell-only tests (no settings host built)."""
    values = dict(close_to_tray=False, minimize_to_tray=False,
                  get_log_folders=lambda: [])
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture()
def real_config(tmp_path):
    """Real Config on a throwaway path — needed once the settings host is
    actually constructed (its tabs read typed config attributes).
    home_dir is redirected because Config.home_dir is app_dir() (the
    repo!) regardless of the config file path — save() and run_state.json
    must never touch the repo root."""
    from core.config import Config
    cfg = Config(tmp_path / "config.properties")
    cfg.home_dir = tmp_path
    return cfg


# ---------------------------------------------------------------------------
# shell structure
# ---------------------------------------------------------------------------

def test_shell_structure(qapp):
    # The stub config has no enable_ai_analysis -> AI off -> the
    # Calibration entry is ABSENT (LAW #2; see test_ai_gating.py).
    mw = MainWindow(_stub_config())
    assert isinstance(mw, QMainWindow)
    assert mw.windowTitle() == "SparkyBot"
    # Menu bar: classic three menus with mnemonics
    titles = [a.text() for a in mw.menuBar().actions()]
    assert titles == ["&File", "&Tools", "&Help"]
    # Sidebar drives the stack; rows are dynamic (keys, not indexes)
    assert isinstance(mw.sidebar, QListWidget)
    assert [mw.sidebar.item(i).text() for i in range(mw.sidebar.count())] \
        == ["Home", "Raid Report", "Process Files", "Settings"]
    assert mw.nav_keys() == [PAGE_HOME, PAGE_RAID_REPORT,
                             PAGE_PROCESS_FILES, PAGE_SETTINGS]
    assert isinstance(mw.stack, QStackedWidget)
    # Containers exist for every page key, gated entries included
    assert mw.stack.count() == len(NAV_ENTRIES)
    assert mw.current_page == PAGE_HOME
    # Status bar watcher indicator
    assert mw.status_dot.property("class") == "status-dot"
    assert mw.status_text.text() == "Watcher stopped"
    # Shell target size — the old 13-tab-label width computation is gone
    assert (mw.minimumWidth(), mw.minimumHeight()) == (600, 460)


def test_shell_structure_ai_on(qapp):
    mw = MainWindow(_stub_config(enable_ai_analysis=True))
    assert [label for _key, label in NAV_ENTRIES] \
        == [mw.sidebar.item(i).text() for i in range(mw.sidebar.count())]
    assert mw.nav_keys() == [key for key, _label in NAV_ENTRIES]


def test_settings_engine_is_lazy(qapp):
    mw = MainWindow(_stub_config())
    # Opening the window must not construct the legacy settings engine
    # (nor the modal dialog over it)
    assert mw._settings is None
    assert mw._settings_dialog is None
    assert PAGE_RAID_REPORT not in mw._built
    assert PAGE_CALIBRATION not in mw._built
    # ...but the Process Files queue is eager: the app controller connects
    # its process_requested signal immediately after construction.
    from core.gui_settings import ProcessFilesWidget
    assert isinstance(mw.process_files_widget, ProcessFilesWidget)
    assert PAGE_PROCESS_FILES in mw._built
    # The Settings stub page (title + reopen button) is eager and cheap
    assert PAGE_SETTINGS in mw._built
    assert mw.open_settings_button.text() == "Open Settings..."


def test_file_menu_has_settings_shortcut(qapp):
    mw = MainWindow(_stub_config())
    assert mw.action_settings_dialog.text() == "&Settings..."
    assert mw.action_settings_dialog.shortcut().toString() == "Ctrl+,"


def test_action_pages_have_keyboard_shortcuts(qapp):
    mw = MainWindow(_stub_config())
    assert mw._nav_actions[PAGE_RAID_REPORT].shortcut().toString() == "Ctrl+R"
    assert mw._nav_actions[PAGE_PROCESS_FILES].shortcut().toString() == "Ctrl+E"


def test_watcher_button_is_quiet_secondary_action(qapp):
    mw = MainWindow(_stub_config())
    assert mw.start_button.property("class") == "secondary"


def test_watcher_state_morphs_all_surfaces(qapp):
    mw = MainWindow(_stub_config())
    mw.set_watcher_state(True)
    assert mw.start_button.text() == "Stop Watcher"
    assert mw.start_button.property("state") == "running"
    assert mw.action_watcher.text() == "Stop &Watcher"
    assert mw.status_dot.property("state") == "running"
    assert mw.status_text.text() == "Watcher running"
    mw.set_watcher_state(False)
    assert mw.start_button.text() == "Start Watcher"
    assert mw.start_button.property("state") == "stopped"
    assert mw.action_watcher.text() == "Start &Watcher"
    assert mw.status_dot.property("state") is None
    assert mw.status_text.text() == "Watcher stopped"


def test_start_button_keeps_signal_contract(qapp):
    mw = MainWindow(_stub_config())
    fired = []
    mw.watcher_toggled.connect(lambda: fired.append(True))
    mw.start_button.click()
    mw.action_watcher.trigger()
    assert len(fired) == 2


# ---------------------------------------------------------------------------
# close semantics (quitOnLastWindowClosed is off app-wide)
# ---------------------------------------------------------------------------

def test_close_with_close_to_tray_hides(qapp):
    mw = MainWindow(_stub_config(close_to_tray=True))
    quits = []
    mw._quit_app = lambda: quits.append(True)
    mw.show()
    assert mw.close() is False   # event ignored -> not closed
    assert not mw.isVisible()    # ...but hidden to tray
    assert quits == []


def test_close_without_close_to_tray_quits(qapp):
    mw = MainWindow(_stub_config(close_to_tray=False))
    quits = []
    mw._quit_app = lambda: quits.append(True)
    mw.show()
    assert mw.close() is True
    assert quits == [True]


# ---------------------------------------------------------------------------
# lazy settings host + promoted tabs
# ---------------------------------------------------------------------------

def test_settings_entry_opens_dialog_and_strips_promoted_tabs(
        qapp, real_config, monkeypatch):
    from core.gui_settings import SettingsWindow
    checks = []
    monkeypatch.setattr(SettingsWindow, "_check_sparkybot_status",
                        lambda self: checks.append("sparkybot"))
    monkeypatch.setattr(SettingsWindow, "_check_ei_status",
                        lambda self: checks.append("ei"))

    mw = MainWindow(real_config)
    sentinel = object()
    mw._tts_client = sentinel

    mw.action_settings.trigger()   # Tools > Settings
    settings = mw._settings
    assert settings is not None
    dialog = mw._settings_dialog
    assert dialog is not None
    assert dialog.isVisible()
    assert mw.current_page == PAGE_SETTINGS
    assert mw.stack.currentWidget() is mw._containers[PAGE_SETTINGS]
    # Building the engine and dialog must NOT hit GitHub — the checks wait
    # for the dialog's Application page
    assert checks == []

    # Promoted ACTION tabs are gone from the engine's tab widget...
    remaining = [settings.tab_widget.tabText(i)
                 for i in range(settings.tab_widget.count())]
    for title in ("Raid Report", "Calibration"):
        assert title not in remaining
    # ...and their pages are mounted under the sidebar
    assert PAGE_RAID_REPORT in mw._built
    assert PAGE_CALIBRATION in mw._built

    # The engine builds no duplicate queue anymore — the shell's is the one
    assert not hasattr(settings, "process_files_widget")
    assert "Process Files" not in remaining
    from core.gui_settings import ProcessFilesWidget
    assert isinstance(mw.process_files_widget, ProcessFilesWidget)

    # Legacy contracts forwarded through the shell
    assert settings._tts_client is sentinel
    fired = []
    mw.settings_changed.connect(lambda: fired.append("changed"))
    mw.watcher_toggled.connect(lambda: fired.append("toggled"))
    settings.settings_changed.emit()
    settings.watcher_toggled.emit()
    assert fired == ["changed", "toggled"]
    mw.set_watcher_state(True)
    assert settings.start_button.property("state") == "running"
    mw.set_watcher_state(False)

    # First VIEW of the dialog's Application page fires both checks once
    from core.settings_dialog import CAT_APPLICATION, _ROLE_CATEGORY
    app_row = next(
        r for r in range(dialog.category_list.count())
        if dialog.category_list.item(r).data(_ROLE_CATEGORY) == CAT_APPLICATION)
    dialog.category_list.setCurrentRow(app_row)
    assert sorted(checks) == ["ei", "sparkybot"]
    dialog.category_list.setCurrentRow(0)
    dialog.category_list.setCurrentRow(app_row)
    assert sorted(checks) == ["ei", "sparkybot"]
    dialog.reject()


def test_reopening_settings_reuses_one_dialog(qapp, real_config, monkeypatch):
    from core.gui_settings import SettingsWindow
    monkeypatch.setattr(SettingsWindow, "_check_sparkybot_status",
                        lambda self: None)
    monkeypatch.setattr(SettingsWindow, "_check_ei_status", lambda self: None)
    mw = MainWindow(real_config)
    first = mw.open_settings_dialog()
    assert first.isVisible()
    # Re-invoking while open raises the same dialog instead of stacking
    assert mw.open_settings_dialog() is first
    first.reject()
    # The stub page's button reopens it
    mw.open_settings_button.click()
    assert mw._settings_dialog is first
    assert first.isVisible()
    first.reject()


def test_raid_report_page_rescans_on_every_open(qapp, real_config,
                                                monkeypatch):
    from core.gui_settings import SettingsWindow
    monkeypatch.setattr(SettingsWindow, "_check_sparkybot_status",
                        lambda self: None)
    monkeypatch.setattr(SettingsWindow, "_check_ei_status", lambda self: None)
    mw = MainWindow(real_config)
    mw.navigate(PAGE_RAID_REPORT)
    assert mw._raid_report_page is not None
    calls = []
    mw._raid_report_page.rescan = lambda: calls.append(1)
    mw.navigate(PAGE_HOME)
    mw.navigate(PAGE_RAID_REPORT)
    assert calls == [1]


def test_promoted_raid_report_is_visible_after_navigation(qapp, real_config,
                                                          monkeypatch):
    """Regression: QTabWidget leaves a non-current removed tab hidden."""
    from core.gui_settings import SettingsWindow
    monkeypatch.setattr(SettingsWindow, "_check_sparkybot_status",
                        lambda self: None)
    monkeypatch.setattr(SettingsWindow, "_check_ei_status", lambda self: None)
    mw = MainWindow(real_config)
    try:
        mw.show()
        mw.navigate(PAGE_RAID_REPORT)
        qapp.processEvents()
        holder = mw._containers[PAGE_RAID_REPORT]
        assert mw._raid_report_page.isVisibleTo(holder)
    finally:
        mw.close()


def test_raid_report_empty_action_opens_process_files(qapp, real_config,
                                                      monkeypatch):
    from core.gui_settings import SettingsWindow
    monkeypatch.setattr(SettingsWindow, "_check_sparkybot_status",
                        lambda self: None)
    monkeypatch.setattr(SettingsWindow, "_check_ei_status", lambda self: None)
    mw = MainWindow(real_config)
    mw.navigate(PAGE_RAID_REPORT)
    mw._raid_report_page.sig_process_files_requested.emit()
    assert mw.current_page == PAGE_PROCESS_FILES


def test_calibration_entry_builds_settings_too(qapp, real_config, monkeypatch):
    from core.gui_settings import SettingsWindow
    monkeypatch.setattr(SettingsWindow, "_check_sparkybot_status",
                        lambda self: None)
    monkeypatch.setattr(SettingsWindow, "_check_ei_status",
                        lambda self: None)
    # Calibration is an AI-only page — it needs AI on to exist at all
    real_config.update('AI', 'enableAiAnalysis', 'true')
    assert real_config.save()
    mw = MainWindow(real_config)
    mw.navigate(PAGE_CALIBRATION)
    assert mw._settings is not None
    assert mw.current_page == PAGE_CALIBRATION
    assert mw.stack.currentWidget() is mw._containers[PAGE_CALIBRATION]
    # The calibration page container actually holds the promoted widget
    holder = mw._containers[PAGE_CALIBRATION]
    assert holder.layout().count() == 1
    try:
        mw.show()
        qapp.processEvents()
        calibration = holder.layout().itemAt(0).widget()
        assert calibration.isVisibleTo(holder)
    finally:
        mw.close()


def test_updates_checks_not_scheduled_at_construction(qapp, real_config,
                                                      monkeypatch):
    """Standalone SettingsWindow (tests, direct use): construction must not
    schedule the GitHub checks — only showing the Updates tab may."""
    from core.gui_settings import SettingsWindow
    checks = []
    monkeypatch.setattr(SettingsWindow, "_check_sparkybot_status",
                        lambda self: checks.append("sparkybot"))
    monkeypatch.setattr(SettingsWindow, "_check_ei_status",
                        lambda self: checks.append("ei"))
    win = SettingsWindow(real_config)
    assert win._updates_checked is False
    # Drain any single-shot timers the old code would have queued
    qapp.processEvents()
    assert checks == []


# ---------------------------------------------------------------------------
# main.py wiring + theme selectors
# ---------------------------------------------------------------------------

def test_main_py_uses_shell_and_explicit_quit_policy():
    src = (_ROOT / "main.py").read_text(encoding="utf-8")
    assert "from core.main_window import MainWindow" in src
    assert "SettingsWindow(" not in src, \
        "main.py must construct the MainWindow shell, not the old window"
    assert "setQuitOnLastWindowClosed(False)" in src, \
        "quit-on-last-window-close must be handled explicitly"


def test_theme_styles_shell_surfaces():
    qss = theme.load_stylesheet()
    assert 'QListWidget[class="nav"]' in qss
    assert 'QLabel[class="status-dot"]' in qss
    assert 'QLabel[class="status-dot"][state="running"]' in qss
