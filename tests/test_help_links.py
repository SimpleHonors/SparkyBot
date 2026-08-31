"""Per-screen Help control (ticket ef95d7e3).

The Help app-bar button on the main window and the Help button on the setup
wizard must exist, carry tooltips (tooltip law), and open the help page for
the CURRENT screen via QDesktopServices.openUrl — never any other network
path. The slug map must cover every sidebar section, every settings
category and every wizard page, and every mapped slug must be a real file
under docs/help/.
"""

import os
import gc
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

import core.setup_wizard as sw
from core import helplinks
from core.config import Config
from core.main_window import NAV_ENTRIES
from core.settings_dialog import AI_CATEGORIES, BASE_CATEGORIES


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _live_help(monkeypatch):
    """The behavior tests below exercise the wired help buttons, so they
    run with the live-gate open. The gate tests at the bottom close it
    again explicitly. Shipping default stays False until docs/help is
    actually published (v2.2.0 P0: a shipped ? that 404'd)."""
    monkeypatch.setattr(helplinks, "HELP_LINKS_LIVE", True)


def _make(app, holder, factory, *args, **kwargs):
    holder["w"] = factory(*args, **kwargs)
    return holder["w"]


def _dispose(app, holder):
    """Drop a disposable widget completely (reject/hide + drop every test
    reference + gc, no deleteLater, no closeEvent fallthrough): a
    rejected-but-alive QWizard poisons later global apply_theme passes
    in-process (offscreen segfault in this Qt variant — 2026-08-30), and
    deleteLater leaves the same dangling C++ object once the deferred
    delete fires. hide() instead of close() keeps MainWindow's
    closeToTray-off quit path out of tests."""
    widget = holder.pop("w", None)
    if widget is None:
        return
    reject = getattr(widget, "reject", None)
    if reject is not None:
        reject()
    widget.hide()
    del widget
    gc.collect()
    app.processEvents()


# ----------------------------------------------------------------------
# the slug map
# ----------------------------------------------------------------------

def test_slug_map_covers_every_sidebar_section():
    for key, _label in NAV_ENTRIES:
        assert key in helplinks.NAV_HELP_SLUGS, f"sidebar {key!r} unmapped"


def test_slug_map_covers_every_settings_category():
    for name in (*BASE_CATEGORIES, *AI_CATEGORIES):
        assert name in helplinks.SETTINGS_HELP_SLUGS, (
            f"settings category {name!r} unmapped")


def test_slug_map_covers_every_wizard_page():
    expected = {getattr(sw, name) for name in dir(sw)
                if name.startswith("PAGE_")}
    assert expected, "wizard page-id constants not found"
    assert set(helplinks.WIZARD_HELP_SLUGS) == expected


def test_every_mapped_slug_is_a_real_help_page():
    mappings = (helplinks.NAV_HELP_SLUGS, helplinks.SETTINGS_HELP_SLUGS,
                helplinks.WIZARD_HELP_SLUGS)
    missing = sorted({slug for mapping in mappings for slug in mapping.values()
                      if not (helplinks.HELP_DOCS_DIR / slug).exists()})
    assert not missing, f"docs/help pages missing: {missing}"


def test_unknown_screen_falls_back_to_readme_index():
    assert helplinks.help_slug("no-such-screen") == helplinks.README_SLUG
    assert helplinks.help_slug(None) == helplinks.README_SLUG
    assert helplinks.help_url(999) == helplinks.HELP_BASE + "README.md"


def test_help_url_is_the_github_docs_base_plus_slug(app):
    assert helplinks.HELP_BASE == (
        "https://github.com/SimpleHonors/SparkyBot/blob/main/docs/help/")
    assert helplinks.help_url("home") == helplinks.HELP_BASE + "home.md"


# ----------------------------------------------------------------------
# main window Help button
# ----------------------------------------------------------------------

def _make_main_window(app, tmp_path, ai_enabled=False):
    from core.main_window import MainWindow

    config = Config(tmp_path / "mw-config.properties")
    config.enable_ai_analysis = ai_enabled  # gates the Calibration entry
    window = MainWindow(config)
    app.processEvents()
    return window


def test_main_window_help_button_exists_with_tooltip(app, tmp_path, monkeypatch):
    from PySide6.QtGui import QDesktopServices

    monkeypatch.setattr(QDesktopServices, "openUrl",
                        staticmethod(lambda url: None))
    holder = {}
    window = _make(app, holder, _make_main_window, app, tmp_path)
    try:
        assert window.help_button is not None
        assert window.help_button.toolTip().strip(), (
            "Help button needs a tooltip (tooltip law)")
    finally:
        _dispose(app, holder)


@pytest.mark.parametrize(
    "page_key,expected_slug",
    [
        ("home", "home.md"),
        ("raid-report", "fight-log-summary.md"),
        ("process-files", "process-files.md"),
        ("calibration", "calibration.md"),
        ("settings", "README.md"),
    ],
)
def test_main_window_help_opens_current_section_page(
    app, tmp_path, monkeypatch, page_key, expected_slug
):
    from PySide6.QtGui import QDesktopServices

    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl",
                        staticmethod(lambda url: opened.append(url)))
    holder = {}
    window = _make(
        app, holder, _make_main_window, app, tmp_path,
        ai_enabled=(page_key == "calibration"))
    try:
        window.navigate(page_key)
        app.processEvents()
        assert window.current_page == page_key
        window.help_button.click()
        assert opened == [helplinks.HELP_BASE + expected_slug]
    finally:
        _dispose(app, holder)


# ----------------------------------------------------------------------
# setup wizard Help button
# ----------------------------------------------------------------------

def _make_wizard(app, tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "discover_competitor_configs",
                        lambda **kwargs: ())
    wizard = sw.SetupWizard(Config(tmp_path / "w-config.properties"))
    wizard.show()  # the wizard only enters its page flow once shown
    app.processEvents()
    return wizard


def test_wizard_help_button_exists_with_tooltip(app, tmp_path, monkeypatch):
    monkeypatch.setattr(sw.QDesktopServices, "openUrl",
                        staticmethod(lambda url: None))
    holder = {}
    wizard = _make(app, holder, _make_wizard, app, tmp_path, monkeypatch)
    try:
        assert wizard.help_button is not None
        assert wizard.help_button.toolTip().strip(), (
            "wizard Help button needs a tooltip (tooltip law)")
    finally:
        _dispose(app, holder)


@pytest.mark.parametrize(
    "page_id,expected_slug",
    [
        (sw.PAGE_WELCOME, "welcome-setup.md"),
        (sw.PAGE_AI_OPTIN, "setup-ai-optin.md"),
        (sw.PAGE_USAGE_MODE, "setup-usage-mode.md"),
        (sw.PAGE_DEPENDENCIES, "setup-dependencies.md"),
        (sw.PAGE_GW2EI, "setup-parser.md"),
        (sw.PAGE_LOG_FOLDER, "setup-log-folder.md"),
        (sw.PAGE_DISCORD, "setup-discord.md"),
        (sw.PAGE_TWITCH, "setup-twitch.md"),
        (sw.PAGE_AI_SETUP, "setup-ai-commentary.md"),
        (sw.PAGE_TTS_VOICE, "setup-voice.md"),
        (sw.PAGE_BEHAVIOR, "setup-behavior.md"),
        (sw.PAGE_COMPLETE, "setup-complete.md"),
    ],
)
def test_wizard_help_opens_current_page(
    app, tmp_path, monkeypatch, page_id, expected_slug
):
    from PySide6.QtGui import QDesktopServices

    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl",
                        staticmethod(lambda url: opened.append(url)))
    holder = {}
    wizard = _make(app, holder, _make_wizard, app, tmp_path, monkeypatch)
    try:
        wizard.setCurrentId(page_id)
        app.processEvents()
        assert wizard.currentId() == page_id
        wizard.help_button.click()
        assert opened == [helplinks.HELP_BASE + expected_slug]
    finally:
        _dispose(app, holder)


# ----------------------------------------------------------------------
# the live gate (operator rule: URLs resolve today, or the ? is ABSENT)
# ----------------------------------------------------------------------

def test_shipping_default_is_gated_off_until_docs_are_published():
    """Pin of the 2026-08-30 P0 ruling: docs/help/ has never been pushed,
    so shipped builds must not show a "?" that 404s. Flip HELP_LINKS_LIVE
    only after the pages are live — and update this test in the same
    commit, as proof the flip was deliberate."""
    import importlib
    fresh = importlib.reload(helplinks)
    try:
        assert fresh.HELP_LINKS_LIVE is False
    finally:
        importlib.reload(helplinks)


def test_main_window_has_no_help_button_when_gate_closed(
    app, tmp_path, monkeypatch
):
    monkeypatch.setattr(helplinks, "HELP_LINKS_LIVE", False)
    holder = {}
    window = _make(app, holder, _make_main_window, app, tmp_path)
    try:
        assert not hasattr(window, "help_button")
    finally:
        _dispose(app, holder)


def test_wizard_has_no_help_button_and_no_custom_button_when_gate_closed(
    app, tmp_path, monkeypatch
):
    from PySide6.QtWidgets import QWizard

    monkeypatch.setattr(helplinks, "HELP_LINKS_LIVE", False)
    holder = {}
    wizard = _make(app, holder, _make_wizard, app, tmp_path, monkeypatch)
    try:
        assert not hasattr(wizard, "help_button")
        assert not wizard.testOption(QWizard.WizardOption.HaveCustomButton1)
    finally:
        _dispose(app, holder)


def test_wizard_gate_open_places_button_in_the_button_row(
    app, tmp_path, monkeypatch
):
    """The v2.2.0 defect was setButton() without HaveCustomButton1: the
    button painted as a loose child mid-page. With the option enabled the
    wizard owns and places it."""
    from PySide6.QtWidgets import QWizard

    holder = {}
    wizard = _make(app, holder, _make_wizard, app, tmp_path, monkeypatch)
    try:
        assert wizard.testOption(QWizard.WizardOption.HaveCustomButton1)
        assert wizard.button(QWizard.WizardButton.CustomButton1) \
            is wizard.help_button
    finally:
        _dispose(app, holder)
