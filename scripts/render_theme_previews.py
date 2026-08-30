"""Render one offscreen preview PNG per theme: setup wizard welcome page
and the Settings window (Display tab).

Usage (run from a scratchable copy, not the noexec share):
    QT_QPA_PLATFORM=offscreen python scripts/render_theme_previews.py

Outputs:
    /mnt/projects/ai_toolbox/SparkyBot-releases/previews/theme-<id>-welcome.png
    /mnt/projects/ai_toolbox/SparkyBot-releases/previews/theme-<id>-settings.png
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

PREVIEW_DIR = Path("/mnt/projects/ai_toolbox/SparkyBot-releases/previews")
WIZARD_SIZE = (760, 620)
SETTINGS_SIZE = (1000, 700)
DISPLAY_TAB_INDEX = 3  # Messaging, Paths, Thresholds, Display


def _scratch_config() -> Config:
    return Config(Path(tempfile.gettempdir()) / "sparkybot-theme-preview.properties")


def main() -> int:
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
    # Deterministic welcome page: pretend no competitor log tool is installed.
    sw.discover_competitor_configs = lambda **kwargs: tuple()

    for theme_id in theme.THEMES:
        theme.set_theme(theme_id)
        theme.apply_theme(app)

        wizard = sw.SetupWizard(_scratch_config())
        wizard.resize(*WIZARD_SIZE)
        wizard.show()
        wizard.welcome_page.initializePage()
        app.processEvents()
        wizard.grab().save(str(PREVIEW_DIR / f"theme-{theme_id}-welcome.png"))
        wizard.close()
        app.processEvents()

        settings = SettingsWindow(_scratch_config())
        settings.resize(*SETTINGS_SIZE)
        settings.tab_widget.setCurrentIndex(DISPLAY_TAB_INDEX)
        settings.show()
        app.processEvents()
        settings.grab().save(str(PREVIEW_DIR / f"theme-{theme_id}-settings.png"))
        settings.close()
        app.processEvents()
        print(f"rendered {theme_id}")

    print(f"done — PNGs in {PREVIEW_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
