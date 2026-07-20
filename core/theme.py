"""Workbench Dark theme — single source of truth for all widget styling.

Twelve palette tokens plus one QSS file (assets/theme_dark.qss). Widgets
never call setStyleSheet themselves: semantic looks are expressed as
dynamic properties (set_state / mark_hint / set_variant / set_widget_class)
that the QSS selects on, and legitimately data-driven colors go through the
QColor accessors here. The single sanctioned inline style in the whole app
is set_swatch_color() at the bottom — a user-picked color cannot live in a
static stylesheet.
"""

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette

# ---------------------------------------------------------------------------
# The 12 "Workbench Dark" tokens (FINAL-DESIGN 2026-07-19, verbatim)
# ---------------------------------------------------------------------------

WINDOW = "#2B2B2B"         # window and page background
SUNKEN = "#222222"         # inputs, lists, tables, scroll tracks
RAISED = "#333333"         # buttons, chips
BORDER_STRONG = "#1E1E1E"  # window-edge separators, button outlines
BORDER = "#3F3F3F"         # control borders, group-box outlines, table grid
TEXT = "#DADADA"           # primary text
TEXT_DIM = "#9A9A9A"       # hints, secondary labels, placeholders
ACCENT = "#4C9A52"         # THE green: primary actions, success, focus
ACCENT_PRESS = "#3E7E44"   # accent pressed
WARN = "#D9A23C"           # skip/partial states, soft warnings, thin data
ERROR = "#D9534F"          # error text, failed states, destructive actions
SELECTION = "#2F4A33"      # list/table selection fill

# Link blue lives in the palette (QPalette.Link) so rich-text <a> tags never
# need inline color attributes.
LINK = "#6CA9D8"

TOKENS = {
    "window": WINDOW,
    "sunken": SUNKEN,
    "raised": RAISED,
    "border_strong": BORDER_STRONG,
    "border": BORDER,
    "text": TEXT,
    "dim": TEXT_DIM,
    "accent": ACCENT,
    "accent_press": ACCENT_PRESS,
    "warn": WARN,
    "error": ERROR,
    "selection": SELECTION,
}

# Semantic QColor names for the few legitimately data-driven paint sites
# (calibration delta cells, tray dot). Everything else goes through the QSS.
_SEMANTIC_COLORS = {
    "ok": ACCENT,
    "warn": WARN,
    "error": ERROR,
    "neutral": TEXT_DIM,
    "text": TEXT,
}

_QSS_PATH = Path(__file__).parent.parent / "assets" / "theme_dark.qss"
_TOKEN_RE = re.compile(r"@([a-z_]+)")


def load_stylesheet() -> str:
    """Read theme_dark.qss and substitute the palette tokens into it."""
    raw = _QSS_PATH.read_text(encoding="utf-8")

    def _sub(match):
        name = match.group(1)
        if name not in TOKENS:
            raise KeyError(f"theme_dark.qss references unknown token {name!r}")
        return TOKENS[name]

    rendered = _TOKEN_RE.sub(_sub, raw)
    # QSS image URLs otherwise resolve against whatever folder launched the
    # app. Point them at the bundled/source assets folder explicitly.
    asset_dir = _QSS_PATH.parent.as_posix()
    return rendered.replace('url("assets/', f'url("{asset_dir}/')


def color(name: str) -> QColor:
    """Semantic QColor for data-driven painting (ok/warn/error/neutral/text)."""
    return QColor(_SEMANTIC_COLORS[name])


def _build_palette() -> QPalette:
    """Token-derived palette so native primitives (check/radio indicators,
    combo arrows, spin buttons) come out dark without QSS overrides."""
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(WINDOW))
    p.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    p.setColor(QPalette.ColorRole.Base, QColor(SUNKEN))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor("#262626"))
    p.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_DIM))
    p.setColor(QPalette.ColorRole.Button, QColor(RAISED))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    p.setColor(QPalette.ColorRole.Highlight, QColor(SELECTION))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(TEXT))
    p.setColor(QPalette.ColorRole.Link, QColor(LINK))
    p.setColor(QPalette.ColorRole.BrightText, QColor(ERROR))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(RAISED))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("#666666"))
    return p


def apply_theme(app):
    """Apply Workbench Dark to the whole QApplication: Fusion base style for
    consistent cross-platform primitives, the token palette, Segoe UI 9pt
    (points, not px — the old UI's px sizing was a DPI hazard), and the QSS.
    Every window — settings, dialogs, the setup wizard — inherits this."""
    app.setStyle("Fusion")
    app.setPalette(_build_palette())
    app.setFont(QFont("Segoe UI", 9))
    app.setStyleSheet(load_stylesheet())


# ---------------------------------------------------------------------------
# Dynamic-property helpers — the ONLY way widgets change their look at runtime
# ---------------------------------------------------------------------------

def repolish(widget):
    """Re-evaluate QSS property selectors after a dynamic property change."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_state(widget, state):
    """Set the semantic `state` property ("ok", "warn", "error", "busy",
    "running", "stopped", "drag", ...) or clear it with None, and repolish."""
    widget.setProperty("state", state if state else None)
    repolish(widget)


def set_widget_class(widget, name: str):
    """Assign a QSS `class` variant ("primary", "chip", "error-outline", ...)."""
    widget.setProperty("class", name)
    repolish(widget)


def mark_option(widget):
    """Make an important choice read as a clickable option card."""
    set_widget_class(widget, "option")
    widget.setCursor(Qt.CursorShape.PointingHandCursor)


def set_variant(widget, variant: str):
    """Assign a QSS `variant` ("status", "title", "heading")."""
    widget.setProperty("variant", variant)
    repolish(widget)


def mark_hint(label):
    """Style a label as muted fine print (replaces the old #888/#aaa notes)."""
    label.setProperty("hint", True)
    repolish(label)


def set_read_only(text_edit, read_only: bool):
    """Toggle read-only AND repolish so QTextEdit[readOnly="true"] re-applies
    (Qt does not re-evaluate QSS selectors on plain property changes)."""
    text_edit.setReadOnly(read_only)
    repolish(text_edit)


def set_swatch_color(button, qcolor: QColor):
    """The one sanctioned inline style: paint a data-driven color swatch
    (user-picked embed color). Chrome comes from QPushButton[class="swatch"]
    in the QSS; only the background is data."""
    button.setProperty("class", "swatch")
    button.setStyleSheet(f"background-color: {qcolor.name()};")
    repolish(button)
