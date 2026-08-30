"""Settings theme picker + per-theme token/contrast guarantees.

(a) Every THEMES entry derives a complete token set (base + derived shades).
(b) Text/window contrast stays strong for every theme.
(c) The Settings Display-tab combo lists all themes and applying one changes
    the QApplication palette (and persists via the normal save path).
"""

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from core import theme
from core.config import Config
from core.gui_settings import SettingsWindow


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _luma(color: QColor) -> float:
    """Perceived luminance, same formula the theme module uses (0-255)."""
    return 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()


# (a) every original base key plus every derived shade _tokens must produce.
_POSSIBLE_KEYS = frozenset(theme._BASE_KEYS) | {
    "raised_hover", "raised_press", "accent_hover", "error_hover",
    "error_press", "on_accent", "disabled_text", "disabled_bg",
    "alt_base", "error_tint", "warn_tint", "scroll_handle", "window_shade",
}


def test_every_theme_derives_a_complete_token_set():
    assert len(theme.THEMES) >= 10  # full roster; additions welcome, removals suspicious
    for theme_id, spec in theme.THEMES.items():
        tokens = theme._tokens(spec)
        missing = _POSSIBLE_KEYS - set(tokens)
        assert not missing, f"{theme_id} missing tokens: {sorted(missing)}"
        for key, value in tokens.items():
            assert isinstance(value, str), (theme_id, key, value)
            assert value.startswith("#"), (theme_id, key, value)
            assert QColor(value).isValid(), (theme_id, key, value)


def test_every_theme_loads_qss_without_leftover_tokens(qt_app):
    saved = theme.current_theme_id()
    try:
        for theme_id in theme.THEMES:
            theme.set_theme(theme_id)
            qss = theme.load_stylesheet()
            assert theme._TOKEN_RE.search(qss) is None, (
                f"{theme_id}: QSS left unreplaced @tokens"
            )
    finally:
        theme.set_theme(saved)


# (b) light-on-dark AND dark-on-light: |luma(text) - luma(window)| audibly
# larger than the operator-rejected near-grey-on-grey (2026-08-30).
def test_every_theme_keeps_text_window_contrast_strong():
    for theme_id, spec in theme.THEMES.items():
        delta = abs(_luma(QColor(spec["text"])) - _luma(QColor(spec["window"])))
        assert delta > 150, f"{theme_id}: luma gap {delta:.0f}"


# (c) the settings combo mirrors the registry, applies live to the palette,
# and persists through the save path.
def test_settings_combo_lists_all_themes_and_applying_changes_palette(
    qt_app, tmp_path
):
    config = Config(tmp_path / "config.properties")
    window = SettingsWindow(config)
    qt_app.processEvents()
    try:
        combo = window.theme_combo
        ids = [combo.itemData(i) for i in range(combo.count())]
        labels = [combo.itemText(i) for i in range(combo.count())]

        assert set(ids) == set(theme.THEMES)
        assert labels == [label for _tid, label in theme.available_themes()]
        assert combo.currentData() == config.ui_theme

        target_id = next(tid for tid in ids if tid != config.ui_theme)
        saved_id = theme.current_theme_id()
        try:
            combo.setCurrentIndex(ids.index(target_id))

            assert theme.current_theme_id() == target_id
            # persisted through the normal save path: in-memory first, then
            # the file after save() — a fresh Config reads it back.
            assert config.ui_theme == target_id or not config.config_path.exists()
            assert config.save() is True
            assert Config(config.config_path).ui_theme == target_id

            app = QApplication.instance()
            window_color = app.palette().color(QPalette.ColorRole.Window)
            expected = QColor(theme.THEMES[target_id]["window"])
            assert window_color == expected
        finally:
            # Leave the shared offscreen app on its original theme.
            theme.set_theme(saved_id)
            theme.apply_theme(QApplication.instance())
            qt_app.processEvents()
    finally:
        config.close_to_tray = False
        window.close()


# (d) the REAL Settings UI: the modal SettingsDialog surfaces the engine's
# Interface Theme group on its Application page, the combo is reachable
# there, live-applies to the QApplication palette, and marks the dialog
# dirty so OK/Apply persist the choice.
def test_settings_dialog_surfaces_theme_picker_on_application_page(
    qt_app, tmp_path
):
    from core.settings_dialog import CAT_APPLICATION, SettingsDialog

    config = Config(tmp_path / "config.properties")
    engine = SettingsWindow(config)
    engine.run_update_checks_once = lambda: None
    dialog = SettingsDialog(engine)
    saved_id = theme.current_theme_id()
    try:
        dialog.open_dialog()
        qt_app.processEvents()

        dialog._select_category(CAT_APPLICATION)
        qt_app.processEvents()
        page = dialog._pages[CAT_APPLICATION]

        # The engine's group moved wholesale onto the Application page.
        assert page.isAncestorOf(engine.theme_group_box)
        assert page.isAncestorOf(engine.theme_combo)

        combo = engine.theme_combo
        ids = [combo.itemData(i) for i in range(combo.count())]
        assert set(ids) == set(theme.THEMES)

        target_id = next(tid for tid in ids if tid != combo.currentData())
        assert not dialog.is_dirty()
        combo.setCurrentIndex(ids.index(target_id))
        qt_app.processEvents()

        # Live-apply: the running app's palette now matches the new theme.
        window_color = qt_app.palette().color(QPalette.ColorRole.Window)
        assert window_color == QColor(theme.THEMES[target_id]["window"])
        assert theme.current_theme_id() == target_id

        # Dirty tracking: OK/Apply will persist via the engine's save path.
        assert dialog.is_dirty()
        assert dialog.apply_button.isEnabled()
    finally:
        theme.set_theme(saved_id)
        theme.apply_theme(QApplication.instance())
        qt_app.processEvents()
        dialog.reject()
        config.close_to_tray = False
        engine.close()
