"""Every user-visible input field carries a real hover tooltip.

Operator ruling (ticket a0b116a2): "any input field the user sees needs a
good tooltip". These tests instantiate the settings surfaces (the
SettingsWindow engine with every legacy tab, and the modal SettingsDialog
with every category page built) plus every setup-wizard page, then walk all
input widgets — QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
QRadioButton, QSlider — asserting a non-empty toolTip().

Qt-internal child editors (the QLineEdit inside an editable QComboBox or a
spin box) are excluded mechanically: the tooltip belongs on the composite
widget the user perceives, not its private parts.
"""

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QLineEdit, QRadioButton, QSlider, QSpinBox, QWidget,
)

from core.config import Config
from core.gui_settings import SettingsWindow

INPUT_CLASSES = (
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QRadioButton, QSlider,
)

# Allowlist of genuinely self-explanatory widgets, keyed by objectName or
# attribute identity. Keep this SMALL and justify every entry. Currently
# empty on purpose: every real input field earned a tooltip in the sweep,
# and the walk mechanically skips Qt-internal child editors instead of
# allowlisting them.
ALLOWLIST: frozenset[str] = frozenset()


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _is_internal_child(widget) -> bool:
    """True for Qt-private editors nested inside a composite input widget
    (an editable combo's line edit, a spin box's line edit): the user hovers
    the composite, whose tooltip Qt shows for the whole area."""
    parent = widget.parent()
    while parent is not None:
        if isinstance(parent, (QComboBox, QAbstractSpinBox)):
            return True
        parent = parent.parent()
    return False


def _describe(widget) -> str:
    text = ""
    for getter in ("text", "currentText", "placeholderText"):
        fn = getattr(widget, getter, None)
        if fn:
            text = text or (fn() or "")
    return f"{type(widget).__name__}(objectName={widget.objectName()!r}, text={text!r})"


def _assert_all_inputs_have_tooltips(root: QWidget, surface: str):
    missing = []
    seen = set()
    for cls in INPUT_CLASSES:
        for widget in root.findChildren(cls):
            if id(widget) in seen:
                continue  # QSpinBox is also a QAbstractSpinBox etc.
            seen.add(id(widget))
            if _is_internal_child(widget):
                continue
            if widget.objectName() in ALLOWLIST and widget.objectName():
                continue
            if not widget.toolTip().strip():
                missing.append(_describe(widget))
    assert not missing, (
        f"{surface}: {len(missing)} input widget(s) without a tooltip:\n  "
        + "\n  ".join(missing)
    )


def test_settings_engine_inputs_all_have_tooltips(qt_app, tmp_path):
    """The legacy SettingsWindow engine owns nearly every settings control
    (the modal dialog re-homes these same widgets), including the promoted
    Fight Summary and Calibration action tabs."""
    config = Config(tmp_path / "config.properties")
    window = SettingsWindow(config)
    qt_app.processEvents()
    try:
        _assert_all_inputs_have_tooltips(window, "SettingsWindow engine")
    finally:
        config.close_to_tray = False
        window.close()


def test_settings_dialog_pages_inputs_all_have_tooltips(qt_app, tmp_path):
    """The real v2.0 Settings surface: build EVERY category page (AI
    categories included — the master switch is forced on so they exist)
    and walk the integrated result."""
    from core.settings_dialog import SettingsDialog

    config = Config(tmp_path / "config.properties")
    config.enable_ai_analysis = True  # surface the AI/Voice/Vocabulary pages
    engine = SettingsWindow(config)
    engine.run_update_checks_once = lambda: None  # no GitHub in tests
    dialog = SettingsDialog(engine)
    try:
        dialog.open_dialog()
        qt_app.processEvents()
        for name in dialog.visible_categories():
            dialog._select_category(name)
            qt_app.processEvents()
        # Sanity: the lazy pages actually got built before the walk.
        assert len(dialog._pages) == len(dialog.visible_categories())
        _assert_all_inputs_have_tooltips(dialog, "SettingsDialog")
    finally:
        dialog.reject()
        config.close_to_tray = False
        engine.close()


def test_setup_wizard_pages_inputs_all_have_tooltips(qt_app, tmp_path, monkeypatch):
    """Every wizard page a new user can meet, walked in one sweep. The
    competitor scan is stubbed out so the test never probes this machine."""
    import core.setup_wizard as sw

    monkeypatch.setattr(
        sw, "discover_competitor_configs", lambda **kwargs: ()
    )
    config = Config(tmp_path / "config.properties")
    wizard = sw.SetupWizard(config)
    qt_app.processEvents()
    try:
        for page_id in wizard.pageIds():
            _assert_all_inputs_have_tooltips(
                wizard.page(page_id), f"wizard page {page_id}"
            )
    finally:
        wizard.reject()
        wizard.deleteLater()
