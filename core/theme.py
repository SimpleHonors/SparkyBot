"""SparkyBot themes — single source of truth for all widget styling.

Thirteen base palette tokens per theme plus one QSS file (assets/theme_dark.qss)
whose @tokens are substituted at load time; hover/pressed/disabled/tint shades
are derived from the base tokens so every theme stays a small, legible table.
Widgets never call setStyleSheet themselves: semantic looks are expressed as
dynamic properties (set_state / mark_hint / set_variant / set_widget_class)
that the QSS selects on, and legitimately data-driven colors go through the
QColor accessors here. The single sanctioned inline style in the whole app
is set_swatch_color() at the bottom — a user-picked color cannot live in a
static stylesheet.

Checkbox/radio indicator SVGs are rendered per theme from the templates in
assets/ (their baked-in colors are treated as placeholders) into a cache
folder, so indicators always match the active accent.
"""

import re
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette

# ---------------------------------------------------------------------------
# Theme registry: 13 base tokens each. "dark" flips the direction that hover
# and pressed shades move in. Keep text/window contrast strong — light grey
# on dark grey was explicitly rejected by the operator (2026-08-30).
# ---------------------------------------------------------------------------

_BASE_KEYS = (
    "window", "sunken", "raised", "border_strong", "border",
    "text", "dim", "accent", "accent_press", "warn", "error",
    "selection", "link",
)

THEMES = {
    "workbench-dark": {
        "label": "Workbench Dark", "dark": True,
        "window": "#2B2B2B", "sunken": "#222222", "raised": "#333333",
        "border_strong": "#1E1E1E", "border": "#3F3F3F",
        "text": "#F0F0F0", "dim": "#A8A8A8",
        "accent": "#4C9A52", "accent_press": "#3E7E44",
        "warn": "#D9A23C", "error": "#D9534F",
        "selection": "#2F4A33", "link": "#6CA9D8",
    },
    "jade": {
        # Matched to the JADE guild logo: flame-green lettering on pure black.
        "label": "JADE", "dark": True,
        "window": "#070B07", "sunken": "#040604", "raised": "#142014",
        "border_strong": "#020302", "border": "#2A402A",
        "text": "#EFF7EC", "dim": "#9CB59A",
        "accent": "#3CC916", "accent_press": "#2B9A12",
        "warn": "#D9A23C", "error": "#E05A55",
        "selection": "#164016", "link": "#6CC7D8",
    },
    "obsidian": {
        "label": "Obsidian (AMOLED)", "dark": True,
        "window": "#000000", "sunken": "#0C0C0C", "raised": "#181818",
        "border_strong": "#060606", "border": "#2E2E2E",
        "text": "#F5F5F5", "dim": "#9E9E9E",
        "accent": "#8B6BE0", "accent_press": "#6F51C4",
        "warn": "#D9A23C", "error": "#E05A55",
        "selection": "#2A2140", "link": "#8FB8E8",
    },
    "daylight": {
        "label": "Daylight", "dark": False,
        "window": "#F2F2F0", "sunken": "#FFFFFF", "raised": "#E4E4E2",
        "border_strong": "#C8C8C6", "border": "#C0C0BE",
        "text": "#1F1F1F", "dim": "#6A6A68",
        "accent": "#3E7E44", "accent_press": "#2F6234",
        "warn": "#A87616", "error": "#C23934",
        "selection": "#CFE5D2", "link": "#1F6FB2",
    },
    "midnight": {
        "label": "Midnight Blue", "dark": True,
        "window": "#101720", "sunken": "#0A0F16", "raised": "#1A2430",
        "border_strong": "#060B11", "border": "#2A3A4C",
        "text": "#E8EEF4", "dim": "#8CA0B3",
        "accent": "#4C8ED9", "accent_press": "#3B72B0",
        "warn": "#D9A23C", "error": "#E05A55",
        "selection": "#1D3A5F", "link": "#7FB3E8",
    },
    "mists": {
        "label": "Mist Teal", "dark": True,
        "window": "#16211F", "sunken": "#101917", "raised": "#22312E",
        "border_strong": "#0B1211", "border": "#32463F",
        "text": "#EAF2F0", "dim": "#93A8A3",
        "accent": "#3FA08F", "accent_press": "#2F8172",
        "warn": "#D9A23C", "error": "#E05A55",
        "selection": "#1F413C", "link": "#6CC7D8",
    },
    "crimson": {
        "label": "Crimson", "dark": True,
        "window": "#221A1C", "sunken": "#191315", "raised": "#2E2326",
        "border_strong": "#120E0F", "border": "#45333A",
        "text": "#F2E9EB", "dim": "#A89298",
        "accent": "#C94F63", "accent_press": "#A63C4F",
        "warn": "#D9A23C", "error": "#E0574F",
        "selection": "#4A2430", "link": "#E58BA0",
    },
    "sunset": {
        "label": "Sunset", "dark": True,
        "window": "#241C16", "sunken": "#1A1410", "raised": "#322721",
        "border_strong": "#130E0A", "border": "#4A392E",
        "text": "#F4ECE5", "dim": "#AE9D8E",
        "accent": "#E0813C", "accent_press": "#B96426",
        "warn": "#D9C23C", "error": "#D9534F",
        "selection": "#4A3117", "link": "#E8A56C",
    },
    "legendary": {
        "label": "Legendary Gold", "dark": True,
        "window": "#1E1B14", "sunken": "#16140E", "raised": "#2B271C",
        "border_strong": "#0F0D09", "border": "#453E2C",
        "text": "#F2EDDF", "dim": "#A89F88",
        "accent": "#C9A227", "accent_press": "#A3841B",
        "warn": "#D97C3C", "error": "#D9534F",
        "selection": "#453A16", "link": "#8FB8E8",
    },
    "bubblegum": {
        "label": "Bubblegum", "dark": False,
        "window": "#F7EFF2", "sunken": "#FFFFFF", "raised": "#EBDCE2",
        "border_strong": "#D4BCC6", "border": "#CDB4BE",
        "text": "#241A1E", "dim": "#7A646C",
        "accent": "#C94F8E", "accent_press": "#A63C74",
        "warn": "#A87616", "error": "#C23934",
        "selection": "#F0CADD", "link": "#8A5BB8",
    },
    "high-contrast": {
        "label": "High Contrast", "dark": True,
        "window": "#000000", "sunken": "#0A0A0A", "raised": "#202020",
        "border_strong": "#6A6A6A", "border": "#808080",
        "text": "#FFFFFF", "dim": "#D0D0D0",
        "accent": "#FFD34D", "accent_press": "#E0B62E",
        "warn": "#FFB454", "error": "#FF5C57",
        "selection": "#2B4EA0", "link": "#6EC1FF",
    },
}

DEFAULT_THEME = "workbench-dark"
_active_id = DEFAULT_THEME


def available_themes():
    """(theme_id, display label) pairs, registry order."""
    return [(tid, spec["label"]) for tid, spec in THEMES.items()]


def current_theme_id() -> str:
    return _active_id


def set_theme(theme_id: str) -> str:
    """Select the active theme; unknown ids fall back to the default.
    Call apply_theme() afterwards to restyle a running app."""
    global _active_id
    _active_id = theme_id if theme_id in THEMES else DEFAULT_THEME
    return _active_id


# ---------------------------------------------------------------------------
# Derived shades — computed so themes never hand-maintain hover/tint tables
# ---------------------------------------------------------------------------

def _mix(a: str, b: str, t: float) -> str:
    """Blend color a toward color b by t (0..1)."""
    ca, cb = QColor(a), QColor(b)
    return QColor(
        round(ca.red() + (cb.red() - ca.red()) * t),
        round(ca.green() + (cb.green() - ca.green()) * t),
        round(ca.blue() + (cb.blue() - ca.blue()) * t),
    ).name().upper()


def _hover(spec, color: str) -> str:
    return _mix(color, "#FFFFFF" if spec["dark"] else "#000000", 0.10)


def _press(spec, color: str) -> str:
    return _mix(color, "#000000" if spec["dark"] else "#FFFFFF", 0.14)


def _on_accent(accent: str) -> str:
    c = QColor(accent)
    # Perceived luminance; gold/yellow accents need dark text on the button.
    lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return "#1A1A1A" if lum > 170 else "#FFFFFF"


def _tokens(spec) -> dict:
    t = {key: spec[key] for key in _BASE_KEYS}
    t["dim"] = spec["dim"]
    t["raised_hover"] = _hover(spec, spec["raised"])
    t["raised_press"] = _press(spec, spec["raised"])
    t["accent_hover"] = _hover(spec, spec["accent"])
    t["error_hover"] = _hover(spec, spec["error"])
    t["error_press"] = _press(spec, spec["error"])
    t["on_accent"] = _on_accent(spec["accent"])
    t["disabled_text"] = _mix(spec["dim"], spec["window"], 0.45)
    t["disabled_bg"] = _mix(spec["raised"], spec["window"], 0.50)
    t["alt_base"] = _mix(spec["sunken"], spec["window"], 0.50)
    t["error_tint"] = _mix(spec["window"], spec["error"], 0.12)
    t["warn_tint"] = _mix(spec["window"], spec["warn"], 0.14)
    t["scroll_handle"] = _mix(spec["border"], spec["text"], 0.18)
    t["window_shade"] = _mix(
        spec["window"], "#000000" if spec["dark"] else "#FFFFFF", 0.12
    )
    return t


def _spec():
    return THEMES[_active_id]


# ---------------------------------------------------------------------------
# Stylesheet + indicator assets
# ---------------------------------------------------------------------------

_ASSET_DIR = Path(__file__).parent.parent / "assets"
_QSS_PATH = _ASSET_DIR / "theme_dark.qss"
_TOKEN_RE = re.compile(r"@([a-z_]+)")
# //@token placeholders must never be mistaken for substitution targets — the
# stylesheet's own header comment documents the token contract, so comments
# are stripped before substitution runs.
_STYLE_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# The template SVGs are authored in Workbench Dark colors; these literals are
# rewritten to the active theme's tokens when the indicators are rendered.
_SVG_TEMPLATE_COLORS = {
    "#222222": "sunken",
    "#9A9A9A": "dim",
    "#4C9A52": "accent",
    "#72BE78": "accent_hover",
    "#FFFFFF": "on_accent",
}
_INDICATOR_SVGS = (
    "checkbox-unchecked.svg", "checkbox-checked.svg",
    "radio-unchecked.svg", "radio-checked.svg",
)


def _render_indicator_assets(tokens: dict) -> Path:
    """Write theme-tinted copies of the indicator SVGs; return their folder."""
    out_dir = Path(tempfile.gettempdir()) / f"sparkybot-theme-{_active_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in _INDICATOR_SVGS:
        svg = (_ASSET_DIR / name).read_text(encoding="utf-8")
        for literal, token in _SVG_TEMPLATE_COLORS.items():
            svg = svg.replace(literal, tokens[token])
            svg = svg.replace(literal.lower(), tokens[token])
        (out_dir / name).write_text(svg, encoding="utf-8")
    return out_dir


def load_stylesheet() -> str:
    """Read the QSS and substitute the active theme's tokens into it."""
    raw = _QSS_PATH.read_text(encoding="utf-8")
    tokens = _tokens(_spec())

    def _sub(match):
        name = match.group(1)
        if name not in tokens:
            raise KeyError(f"theme_dark.qss references unknown token {name!r}")
        return tokens[name]

    rendered = _TOKEN_RE.sub(_sub, _STYLE_COMMENT_RE.sub("", raw))
    # QSS image URLs otherwise resolve against whatever folder launched the
    # app. Point them at the per-theme rendered indicator assets.
    try:
        asset_dir = _render_indicator_assets(tokens).as_posix()
    except OSError:
        asset_dir = _ASSET_DIR.as_posix()
    return rendered.replace('url("assets/', f'url("{asset_dir}/')


def color(name: str) -> QColor:
    """Semantic QColor for data-driven painting (ok/warn/error/neutral/text)."""
    spec = _spec()
    semantic = {
        "ok": spec["accent"],
        "warn": spec["warn"],
        "error": spec["error"],
        "neutral": spec["dim"],
        "text": spec["text"],
    }
    return QColor(semantic[name])


def _build_palette() -> QPalette:
    """Token-derived palette so native primitives (check/radio indicators,
    combo arrows, spin buttons) match the theme without QSS overrides."""
    spec = _spec()
    tokens = _tokens(spec)
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(spec["window"]))
    p.setColor(QPalette.ColorRole.WindowText, QColor(spec["text"]))
    p.setColor(QPalette.ColorRole.Base, QColor(spec["sunken"]))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(tokens["alt_base"]))
    p.setColor(QPalette.ColorRole.Text, QColor(spec["text"]))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(spec["dim"]))
    p.setColor(QPalette.ColorRole.Button, QColor(spec["raised"]))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(spec["text"]))
    p.setColor(QPalette.ColorRole.Highlight, QColor(spec["selection"]))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(spec["text"]))
    p.setColor(QPalette.ColorRole.Link, QColor(spec["link"]))
    p.setColor(QPalette.ColorRole.BrightText, QColor(spec["error"]))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(spec["raised"]))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(spec["text"]))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role,
                   QColor(tokens["disabled_text"]))
    return p


def apply_theme(app):
    """Apply the active theme to the whole QApplication: Fusion base style for
    consistent cross-platform primitives, the token palette, Segoe UI 9pt
    (points, not px — the old UI's px sizing was a DPI hazard), and the QSS.
    Every window — settings, dialogs, the setup wizard — inherits this.
    Safe to call again after set_theme() to restyle a running app."""
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
