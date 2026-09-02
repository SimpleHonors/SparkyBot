import os
from pathlib import Path

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QScrollArea

from core.config import Config
from core.gui_settings import SettingsWindow
from core.interop_catalog import CREDIT_PROJECTS
from core.settings_dialog import (
    CAT_ABOUT,
    CAT_APPLICATION,
    CAT_DISCORD,
    CAT_RAID_REPORTS,
    CAT_UPDATES,
    SettingsDialog,
)


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings_dialog(tmp_path, qt_app):
    config = Config(tmp_path / "config.properties")
    config.update("Discord", "enableDiscordBot", "false")
    config._load_values()
    engine = SettingsWindow(config)
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


def test_about_credits_only_software_sparkybot_actually_uses_or_learned_from(
    settings_dialog, qt_app
):
    dialog, _engine = settings_dialog
    about = _render_category(dialog, qt_app, CAT_ABOUT)
    labels = about.findChildren(QLabel)
    rendered = "\n".join(label.text() for label in labels)

    assert "Built on the shoulders of giants" not in rendered
    assert "Software we use and credit" in rendered
    assert "Independent neighboring tools" not in rendered
    assert "Other WvW log tools" in rendered

    for project in CREDIT_PROJECTS:
        matching = [label for label in labels if project.url in label.text()]
        assert matching, f"About is missing a link for {project.name}"
        assert any(label.openExternalLinks() for label in matching)
    assert "MzFightReporter" in rendered
    assert "AxiBridge" not in rendered
    assert "TopStatsAIO" not in rendered
    assert "GW2-WVW-Teams" not in rendered
    assert "TopStatsDash" not in rendered


def test_discord_page_removes_dead_50mb_controls_and_explains_real_delivery(
    settings_dialog, qt_app
):
    dialog, engine = settings_dialog
    discord = _render_category(dialog, qt_app, CAT_DISCORD)
    rendered = "\n".join(
        label.text() for label in discord.findChildren(QLabel)
    )

    assert not discord.isAncestorOf(engine.max_upload)
    assert not discord.isAncestorOf(engine.large_upload_after)
    assert "does not attach raw fight-log files" in rendered
    assert "quick run summary" in rendered
    assert "shrinks or zips" in rendered
    assert "10 MB" in rendered


def test_report_default_view_marks_settings_dirty_and_saves(
    settings_dialog, qt_app
):
    dialog, engine = settings_dialog
    _render_category(dialog, qt_app, CAT_RAID_REPORTS)

    simple_index = engine.raidreport_default_view.findData("simple")
    engine.raidreport_default_view.setCurrentIndex(simple_index)
    qt_app.processEvents()

    assert dialog.apply_button.isEnabled()
    dialog._on_apply()
    assert engine.config.raidreport_default_view == "simple"


def test_readme_credits_do_not_claim_every_neighbor_built_sparkybot():
    readme = Path(__file__).parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")

    assert "Built on the Shoulders of Giants" not in text
    assert "directly relies on" in text
    assert "interoperability guide" in text
