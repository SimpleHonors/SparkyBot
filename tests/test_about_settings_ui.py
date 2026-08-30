import os
from pathlib import Path

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QScrollArea

from core.config import Config
from core.gui_settings import SettingsWindow
from core.interop_catalog import INTEROP_PROJECTS
from core.settings_dialog import (
    CAT_ABOUT,
    CAT_APPLICATION,
    CAT_UPDATES,
    SettingsDialog,
)


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings_dialog(tmp_path, qt_app):
    engine = SettingsWindow(Config(tmp_path / "config.properties"))
    engine.run_update_checks_once = lambda: None
    dialog = SettingsDialog(engine)
    dialog.open_dialog()
    qt_app.processEvents()
    yield dialog, engine
    dialog.reject()
    engine.config.close_to_tray = False
    engine.close()


def _render_category(dialog, qt_app, name):
    dialog._select_category(name)
    qt_app.processEvents()
    return dialog._pages[name]


def test_about_is_its_own_settings_entry_and_application_fits_without_scroll(
    settings_dialog, qt_app
):
    dialog, engine = settings_dialog

    assert CAT_ABOUT in dialog.visible_categories()
    assert CAT_UPDATES in dialog.visible_categories()

    application = _render_category(dialog, qt_app, CAT_APPLICATION)
    assert isinstance(application, QScrollArea)
    assert not application.isAncestorOf(engine.about_widget)
    assert application.verticalScrollBar().maximum() == 0

    about = _render_category(dialog, qt_app, CAT_ABOUT)
    assert about.isAncestorOf(engine.about_widget)


def test_about_uses_plain_credits_and_links_every_named_project(
    settings_dialog, qt_app
):
    dialog, _engine = settings_dialog
    about = _render_category(dialog, qt_app, CAT_ABOUT)
    labels = about.findChildren(QLabel)
    rendered = "\n".join(label.text() for label in labels)

    assert "Built on the shoulders of giants" not in rendered
    assert "Tools SparkyBot uses" in rendered
    assert "Independent neighboring tools" in rendered
    assert "How these tools differ" in rendered

    for project in INTEROP_PROJECTS:
        matching = [label for label in labels if project.url in label.text()]
        assert matching, f"About is missing a link for {project.name}"
        assert any(label.openExternalLinks() for label in matching)


def test_readme_credits_do_not_claim_every_neighbor_built_sparkybot():
    readme = Path(__file__).parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")

    assert "Built on the Shoulders of Giants" not in text
    assert "directly relies on" in text
    assert "interoperability guide" in text
