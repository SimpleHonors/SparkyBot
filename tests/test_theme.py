"""Tests for core/theme.py + assets/theme_dark.qss — the Workbench Dark theme.

Covers: the 12-token palette values (binding spec, FINAL-DESIGN 2026-07-19),
QSS loading/substitution, the design rules (pt font sizes, max 2px radii),
the dynamic-property state helpers, app-level application, and a repo-wide
guard that no inline setStyleSheet/QPalette hacks creep back in outside
core/theme.py.
"""

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

pytest.importorskip("PySide6", reason="PySide6 not installed in this environment")

from PySide6.QtWidgets import QApplication, QLabel

from core import theme


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# palette tokens
# ---------------------------------------------------------------------------

# The 12 Workbench Dark tokens, verbatim from the design spec. If one of
# these fails, the palette drifted — fix the constant, not this test.
_EXPECTED_TOKENS = {
    "window": "#2B2B2B",
    "sunken": "#222222",
    "raised": "#333333",
    "border_strong": "#1E1E1E",
    "border": "#3F3F3F",
    "text": "#DADADA",
    "dim": "#9A9A9A",
    "accent": "#4C9A52",
    "accent_press": "#3E7E44",
    "warn": "#D9A23C",
    "error": "#D9534F",
    "selection": "#2F4A33",
}


def test_twelve_tokens_exact():
    assert theme.TOKENS == _EXPECTED_TOKENS


def test_every_token_reaches_the_stylesheet():
    qss = theme.load_stylesheet()
    for name, value in _EXPECTED_TOKENS.items():
        assert value in qss, f"token {name} ({value}) unused in theme_dark.qss"


def test_no_unsubstituted_placeholders():
    qss = theme.load_stylesheet()
    assert "@" not in qss, "theme_dark.qss has unsubstituted @token placeholders"


def test_indicator_images_use_launch_directory_independent_paths():
    qss = theme.load_stylesheet()
    assert 'url("assets/' not in qss
    assert str((_ROOT / "assets").as_posix()) in qss


def test_unknown_token_raises():
    # A typo in the QSS must fail loudly at load, not render as literal text.
    import core.theme as t
    bad = "QLabel { color: @nosuchtoken; }"
    with pytest.raises(KeyError):
        t._TOKEN_RE.sub(
            lambda m: t.TOKENS[m.group(1)], bad
        )


# ---------------------------------------------------------------------------
# design rules
# ---------------------------------------------------------------------------

def test_font_sizes_are_points_not_pixels():
    qss = theme.load_stylesheet()
    sizes = re.findall(r"font-size:\s*\d+(pt|px)", qss)
    assert sizes, "expected at least one font-size rule"
    assert all(unit == "pt" for unit in sizes), "px font sizes are a DPI hazard"


def test_border_radius_capped_at_2px():
    qss = theme.load_stylesheet()
    radii = [int(r) for r in re.findall(r"border-radius:\s*(\d+)px", qss)]
    assert radii, "expected border-radius rules"
    assert max(radii) <= 2


def test_semantic_state_selectors_present():
    qss = theme.load_stylesheet()
    for state in ("ok", "warn", "error", "busy"):
        assert f'QLabel[state="{state}"]' in qss
    assert 'QPushButton[class="primary"]' in qss
    assert 'QPushButton[class="primary"][state="running"]' in qss
    assert 'QTextEdit[readOnly="true"]' in qss
    assert 'QLabel[hint="true"]' in qss


def test_choice_controls_are_visibly_interactive():
    """Checks/radios must not fall back to tiny low-contrast native bubbles."""
    qss = theme.load_stylesheet()
    assert "QCheckBox::indicator" in qss
    assert "QRadioButton::indicator" in qss
    assert "QCheckBox::indicator:checked" in qss
    assert "QRadioButton::indicator:checked" in qss
    assert 'QRadioButton[class="option"]' in qss
    assert 'QRadioButton[class="option"]:hover' in qss
    assert 'QRadioButton[class="option"]:checked' in qss


def test_widget_class_coverage():
    """Every widget class the app uses has a rule in the theme."""
    qss = theme.load_stylesheet()
    for widget in (
        "QPushButton", "QLineEdit", "QTextEdit", "QComboBox", "QSpinBox",
        "QDoubleSpinBox", "QCheckBox", "QRadioButton", "QGroupBox",
        "QTabBar", "QListWidget", "QTableWidget", "QHeaderView",
        "QScrollBar", "QToolTip", "QMenu", "QMenuBar", "QStatusBar",
        "QProgressBar",
    ):
        assert widget in qss, f"{widget} not styled by theme_dark.qss"


# ---------------------------------------------------------------------------
# dynamic-property helpers
# ---------------------------------------------------------------------------

def test_set_state_roundtrip(qapp):
    label = QLabel("x")
    theme.set_state(label, "error")
    assert label.property("state") == "error"
    theme.set_state(label, "ok")
    assert label.property("state") == "ok"
    theme.set_state(label, None)
    assert label.property("state") is None


def test_mark_hint_and_variants(qapp):
    label = QLabel("x")
    theme.mark_hint(label)
    assert label.property("hint") is True
    theme.set_variant(label, "status")
    assert label.property("variant") == "status"


def test_swatch_color_is_only_background(qapp):
    from PySide6.QtWidgets import QPushButton
    from PySide6.QtGui import QColor
    btn = QPushButton()
    theme.set_swatch_color(btn, QColor("#123456"))
    assert btn.property("class") == "swatch"
    # Only the data-driven background lives inline; chrome comes from QSS.
    assert btn.styleSheet() == "background-color: #123456;"


def test_semantic_color_accessors():
    assert theme.color("ok").name().upper() == theme.ACCENT
    assert theme.color("error").name().upper() == theme.ERROR
    assert theme.color("warn").name().upper() == theme.WARN
    assert theme.color("neutral").name().upper() == theme.TEXT_DIM


# ---------------------------------------------------------------------------
# app-level application
# ---------------------------------------------------------------------------

def test_apply_theme_sets_style_font_and_stylesheet(qapp):
    theme.apply_theme(qapp)
    assert qapp.font().pointSize() == 9
    assert qapp.styleSheet() == theme.load_stylesheet()
    # An app stylesheet wraps the base style in QStyleSheetStyle (empty
    # objectName); drop it to observe the Fusion base underneath.
    qapp.setStyleSheet("")
    assert qapp.style().objectName().lower() == "fusion"
    theme.apply_theme(qapp)  # restore for any tests that run after this one


# ---------------------------------------------------------------------------
# repo-wide inline-style guard
# ---------------------------------------------------------------------------

def test_no_inline_styles_outside_theme_module():
    """The theme is the single source of truth: no widget code may call
    setStyleSheet or build a QPalette. (theme.py itself is exempt — it owns
    apply_theme and the data-driven swatch helper.)"""
    offenders = []
    sources = [_ROOT / "main.py"] + sorted((_ROOT / "core").glob("*.py"))
    for path in sources:
        if path.name == "theme.py":
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in ("setStyleSheet", "QPalette", "setPalette"):
            if pattern in text:
                offenders.append(f"{path.name}: {pattern}")
    assert not offenders, f"inline styling outside core/theme.py: {offenders}"


def test_ui_sources_have_no_decorative_emoji_or_status_glyphs():
    banned = ("⚡", "⚠", "\U0001F4CA", "✓", "✗", "✅", "⬇", "↻")
    offenders = []
    sources = [_ROOT / "main.py"] + sorted((_ROOT / "core").glob("*.py"))
    for path in sources:
        text = path.read_text(encoding="utf-8")
        found = [glyph for glyph in banned if glyph in text]
        if found:
            offenders.append(f"{path.name}: {''.join(found)}")
    assert not offenders, f"decorative glyphs leaked into UI source: {offenders}"


def test_wizard_inherits_app_theme():
    """The setup wizard must not force its own palette/stylesheet — it
    inherits the app-wide theme applied in main.py (audit-app-shell)."""
    src = (_ROOT / "core" / "setup_wizard.py").read_text(encoding="utf-8")
    assert "setPalette" not in src
    assert "setStyleSheet" not in src
    src_main = (_ROOT / "main.py").read_text(encoding="utf-8")
    assert "apply_theme" in src_main
