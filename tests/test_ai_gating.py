"""LAW #2 AI gating (slice 7): one master switch, AI surfaces ABSENT when off.

Contract under test:
- the master switch is the EXISTING AI/enableAiAnalysis key behind the
  "Enable AI features" checkbox on Settings > Application — no new keys;
- AI-off leaves ZERO AI-labeled surfaces in the running UI: no Calibration
  sidebar entry, no Tools > Calibration action, no AI settings categories,
  and no AI-flavored word in any reachable text/tooltip — EXCEPT the master
  switch's own group on the Application page (design-A LAW-2b: "the only
  AI-related pixel");
- flipping the switch + Apply/OK takes effect LIVE: AI categories
  insert/remove in the OPEN dialog, and the main window's Calibration
  entry + Tools action gate through the settings-changed seam — the nav is
  key-addressed now, never positional;
- the run flow stays mode-gated and the feed stays pipeline-gated (out of
  scope here by design — see slices 5/6).
"""

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

pytest.importorskip("PySide6", reason="PySide6 not installed in this environment")

from PySide6.QtWidgets import QApplication, QMenu, QWidget

from core.main_window import (
    MainWindow, NAV_ENTRIES,
    PAGE_HOME, PAGE_RAID_REPORT, PAGE_PROCESS_FILES, PAGE_CALIBRATION,
    PAGE_SETTINGS,
)
from core.settings_dialog import (
    SettingsDialog, BASE_CATEGORIES, AI_CATEGORIES,
    CAT_APPLICATION, CAT_VOICE, _ROLE_CATEGORY,
)

# Any of these words in visible UI text marks an AI surface. \b keeps
# "RAID" (which contains "ai") and friends out; the match is otherwise
# case-insensitive.
_AI_WORDS = re.compile(
    r"\b(AI|calibration|commentary|voice|vocabulary|TTS)\b", re.IGNORECASE)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture()
def config(tmp_path):
    """Real Config isolated to tmp_path (Config.home_dir is app_dir() —
    the repo — unless redirected; save() and run_state.json must never
    touch the repo root)."""
    from core.config import Config
    cfg = Config(tmp_path / "config.properties")
    cfg.home_dir = tmp_path
    return cfg


@pytest.fixture()
def stub_checks(monkeypatch):
    """Keep the settings engine's GitHub checks off the network."""
    from core.gui_settings import SettingsWindow
    monkeypatch.setattr(SettingsWindow, "_check_sparkybot_status",
                        lambda self: None)
    monkeypatch.setattr(SettingsWindow, "_check_ei_status",
                        lambda self: None)


@pytest.fixture()
def engine(qapp, config, stub_checks):
    from core.gui_settings import SettingsWindow
    return SettingsWindow(config)


@pytest.fixture()
def dialog(engine):
    dlg = SettingsDialog(engine)
    yield dlg
    if dlg.isVisible():
        dlg.reject()


def _ai_on(config):
    config.update('AI', 'enableAiAnalysis', 'true')
    assert config.save()


def _ai_off(config):
    config.update('AI', 'enableAiAnalysis', 'false')
    assert config.save()


def _texts_of(root) -> list:
    """(widget, string) pairs for every text-ish property under `root` —
    labels, titles, placeholders and tooltips."""
    out = []
    for w in [root] + root.findChildren(QWidget):
        for getter in ("text", "title", "placeholderText"):
            fn = getattr(w, getter, None)
            if callable(fn):
                try:
                    value = fn()
                except TypeError:
                    continue
                if isinstance(value, str) and value:
                    out.append((w, value))
        tip = w.toolTip()
        if tip:
            out.append((w, tip))
    return out


def _visible_menu_texts(mw) -> list:
    """Texts of every VISIBLE menu action (submenus recursed). An action
    made invisible is absent from the rendered menu — that is the gating
    mechanism for Tools > Calibration."""
    texts = []

    def _walk(actions):
        for action in actions:
            if not action.isVisible():
                continue
            if action.text():
                texts.append(action.text())
            menu = action.menu()
            if isinstance(menu, QMenu):
                _walk(menu.actions())

    _walk(mw.menuBar().actions())
    return texts


# ---------------------------------------------------------------------------
# main window: nav walk + menu gating
# ---------------------------------------------------------------------------

def test_ai_off_nav_and_menus_have_no_ai_surfaces(qapp, config, stub_checks):
    mw = MainWindow(config)
    # Nav walk: the Calibration entry is ABSENT, not grayed
    assert mw.nav_keys() == [PAGE_HOME, PAGE_RAID_REPORT,
                             PAGE_PROCESS_FILES, PAGE_SETTINGS]
    # Tools menu: the action exists but is invisible = absent from the menu
    assert not mw.action_calibration.isVisible()
    offenders = [t for t in _visible_menu_texts(mw) if _AI_WORDS.search(t)]
    assert offenders == [], offenders


def test_ai_on_nav_has_calibration_in_order(qapp, config, stub_checks):
    _ai_on(config)
    mw = MainWindow(config)
    assert mw.nav_keys() == [key for key, _label in NAV_ENTRIES]
    assert mw.action_calibration.isVisible()
    assert "&Calibration" in _visible_menu_texts(mw)


def test_navigate_to_absent_calibration_is_noop(qapp, config, stub_checks):
    mw = MainWindow(config)
    mw.navigate(PAGE_CALIBRATION)
    assert mw.current_page == PAGE_HOME
    # An absent entry never builds the settings engine either
    assert mw._settings is None


def test_calibration_gates_live_through_settings_seam(qapp, config,
                                                      stub_checks):
    """The engine emits settings_changed on every save; the shell re-gates
    the Calibration entry + Tools action from the freshly saved config."""
    mw = MainWindow(config)
    assert PAGE_CALIBRATION not in mw.nav_keys()

    _ai_on(config)
    mw.settings_changed.emit()
    assert mw.nav_keys() == [key for key, _label in NAV_ENTRIES]
    assert mw.action_calibration.isVisible()
    # The inserted entry is fully functional
    mw.navigate(PAGE_CALIBRATION)
    assert mw.current_page == PAGE_CALIBRATION
    assert mw.stack.currentWidget() is mw._containers[PAGE_CALIBRATION]

    # Flip off while SITTING on Calibration: bounced Home, entry removed
    _ai_off(config)
    mw.settings_changed.emit()
    assert PAGE_CALIBRATION not in mw.nav_keys()
    assert not mw.action_calibration.isVisible()
    assert mw.current_page == PAGE_HOME

    # And back on: re-inserted at its NAV_ENTRIES position, page intact
    _ai_on(config)
    mw.settings_changed.emit()
    assert mw.nav_keys()[3] == PAGE_CALIBRATION
    mw.navigate(PAGE_CALIBRATION)
    assert mw.stack.currentWidget() is mw._containers[PAGE_CALIBRATION]


def test_ai_off_main_window_walk_zero_ai_strings(qapp, config, stub_checks):
    """Every REACHABLE page (nav-walk), the chrome and the sidebar carry
    no AI-flavored word — including tooltips."""
    mw = MainWindow(config)
    mw.navigate(PAGE_RAID_REPORT)   # mount the engine's promoted page too
    mw.navigate(PAGE_HOME)
    texts = []
    for key in mw.nav_keys():
        texts += _texts_of(mw._containers[key])
    texts += _texts_of(mw.statusBar())
    texts += [(mw.sidebar, mw.sidebar.item(i).text())
              for i in range(mw.sidebar.count())]
    texts.append((mw, mw.windowTitle()))
    offenders = [(w.__class__.__name__, t) for w, t in texts
                 if _AI_WORDS.search(t)]
    assert offenders == [], offenders


# ---------------------------------------------------------------------------
# settings dialog: live insert/remove on Apply
# ---------------------------------------------------------------------------

def test_dialog_ai_categories_insert_live_on_apply(dialog, engine, config):
    dialog.open_dialog()
    assert dialog.visible_categories() == list(BASE_CATEGORIES)

    engine.enable_ai.setChecked(True)
    assert dialog.apply_button.isEnabled()
    dialog.apply_button.click()
    # No reopen: the saved flip re-gated the OPEN dialog
    assert config.enable_ai_analysis is True
    assert dialog.visible_categories() == \
        list(BASE_CATEGORIES) + list(AI_CATEGORIES)
    sep_row = dialog.category_list.row(dialog._separator_item)
    assert not dialog.category_list.isRowHidden(sep_row)

    engine.enable_ai.setChecked(False)
    dialog.apply_button.click()
    assert config.enable_ai_analysis is False
    assert dialog.visible_categories() == list(BASE_CATEGORIES)
    assert dialog.category_list.isRowHidden(sep_row)


def test_dialog_lands_on_application_when_current_page_vanishes(
        dialog, engine, config):
    _ai_on(config)
    dialog.open_dialog()
    dialog._select_category(CAT_VOICE)
    row = dialog.category_list.currentRow()
    assert dialog.category_list.item(row).data(_ROLE_CATEGORY) == CAT_VOICE

    engine.enable_ai.setChecked(False)
    dialog.apply_button.click()
    row = dialog.category_list.currentRow()
    assert dialog.category_list.item(row).data(_ROLE_CATEGORY) \
        == CAT_APPLICATION
    assert dialog.visible_categories() == list(BASE_CATEGORIES)


def test_twitch_tooltip_wording_follows_ai_mode(dialog, engine, config):
    """Twitch is NOT AI-gated — only its promise mentions commentary, and
    only while AI is on (design-A LAW-2 table)."""
    dialog.open_dialog()
    assert engine.enable_twitch.toolTip() \
        == "Posts fight summaries to a Twitch chat channel."
    engine.enable_ai.setChecked(True)
    dialog.apply_button.click()
    assert "AI commentary" in engine.enable_twitch.toolTip()
    engine.enable_ai.setChecked(False)
    dialog.apply_button.click()
    assert "AI" not in engine.enable_twitch.toolTip()


def test_ai_off_dialog_walk_only_master_switch_mentions_ai(dialog, config):
    """Build every VISIBLE category AI-off and sweep all text/tooltips:
    the only AI-flavored words live inside the master switch's own group
    on the Application page."""
    dialog.open_dialog()
    for row in range(dialog.category_list.count()):
        item = dialog.category_list.item(row)
        if item.data(_ROLE_CATEGORY) and \
                not dialog.category_list.isRowHidden(row):
            dialog.category_list.setCurrentRow(row)

    group = dialog._ai_switch_group
    assert group is not None
    texts = _texts_of(dialog)
    offenders = [(w.__class__.__name__, t) for w, t in texts
                 if _AI_WORDS.search(t)
                 and w is not group and not group.isAncestorOf(w)]
    assert offenders == [], offenders
    # The switch itself is present and correctly labeled
    assert any("Enable AI features" in t for _w, t in texts)
    # The stale "pages appear the next time you open Settings" hint is
    # gone — gating is live now
    assert not any("next time you open" in t for _w, t in texts)
