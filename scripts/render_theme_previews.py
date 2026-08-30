"""Render offscreen preview PNGs per theme of the REAL SparkyBot UI:

    theme-<id>-welcome.png   setup wizard welcome page (first-run UI)
    theme-<id>-main.png      MainWindow shell with the sidebar visible
    theme-<id>-settings.png  SettingsDialog on the Application page,
                             showing the Interface Theme row

Usage (run from a scratchable copy, not the noexec share):
    QT_QPA_PLATFORM=offscreen python scripts/render_theme_previews.py

Outputs land in /mnt/projects/ai_toolbox/SparkyBot-releases/previews/
(existing files with the same names are overwritten).
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

from core import theme
from core import setup_wizard as sw
from core.config import Config
from core.gui_settings import SettingsWindow
from core.main_window import MainWindow
from core.settings_dialog import CAT_APPLICATION, SettingsDialog

PREVIEW_DIR = Path("/mnt/projects/ai_toolbox/SparkyBot-releases/previews")
WIZARD_SIZE = (760, 620)
MAIN_SIZE = (1000, 680)
SETTINGS_SIZE = (780, 560)


def _scratch_config() -> Config:
    return Config(Path(tempfile.gettempdir()) / "sparkybot-theme-preview.properties")


def _render_welcome(app: QApplication, theme_id: str):
    wizard = sw.SetupWizard(_scratch_config())
    wizard.resize(*WIZARD_SIZE)
    wizard.show()
    wizard.welcome_page.initializePage()
    app.processEvents()
    wizard.grab().save(str(PREVIEW_DIR / f"theme-{theme_id}-welcome.png"))
    wizard.close()
    app.processEvents()


def _render_main(app: QApplication, theme_id: str):
    config = _scratch_config()
    window = MainWindow(config)
    window.resize(*MAIN_SIZE)
    window.show()
    app.processEvents()
    window.grab().save(str(PREVIEW_DIR / f"theme-{theme_id}-main.png"))
    config.close_to_tray = False
    window.close()
    app.processEvents()


def _render_settings(app: QApplication, theme_id: str):
    config = _scratch_config()
    # The theme row must show the theme being rendered, not the default.
    config.update('UI', 'theme', theme_id)
    config.ui_theme = theme_id
    engine = SettingsWindow(config)
    engine.run_update_checks_once = lambda: None  # no network from a preview
    dialog = SettingsDialog(engine)
    dialog.resize(*SETTINGS_SIZE)
    dialog.open_dialog()
    dialog._select_category(CAT_APPLICATION)  # the Interface Theme row's home
    app.processEvents()
    dialog.grab().save(str(PREVIEW_DIR / f"theme-{theme_id}-settings.png"))
    dialog.reject()
    config.close_to_tray = False
    engine.close()
    app.processEvents()


def main() -> int:
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
    # Deterministic welcome page: pretend no competitor log tool is installed.
    sw.discover_competitor_configs = lambda **kwargs: tuple()

    for theme_id in theme.THEMES:
        theme.set_theme(theme_id)
        theme.apply_theme(app)

        _render_welcome(app, theme_id)
        _render_main(app, theme_id)
        _render_settings(app, theme_id)
        print(f"rendered {theme_id}")

    print(f"done — PNGs in {PREVIEW_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
