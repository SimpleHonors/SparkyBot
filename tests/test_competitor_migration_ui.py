import os
from pathlib import Path

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QScrollArea,
)

from core import apppaths
import core.competitor_migration_ui as migration_ui
from core.competitor_import import (
    CompetitorFinding,
    ImportedSetting,
    ImportedWebhook,
    build_import_plan,
)
from core.competitor_migration_ui import (
    CompetitorExportDoneDialog,
    CompetitorExportConfirmDialog,
    CompetitorImportDialog,
    InteropCatalogDialog,
    choose_manual_competitor_import,
)
from core.config import Config
from core.setup_wizard import (
    PAGE_COMPLETE,
    PAGE_DISCORD,
    GW2EIPage,
    LogFolderPage,
    SetupWizard,
)
from core.shareable_config import parse_guild_config


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def webhook(number: int, token: str) -> str:
    return f"https://discord.com/api/webhooks/{number:018d}/{token}"


def build_wizard(tmp_path, monkeypatch):
    parser_dir = tmp_path / "parser"
    parser_dir.mkdir()
    parser = parser_dir / "GuildWars2EliteInsights-CLI.exe"
    parser.write_bytes(b"parser")
    monkeypatch.setattr(apppaths, "is_frozen", lambda: True)
    monkeypatch.setattr(apppaths, "gw2ei_dir", lambda: parser_dir)
    monkeypatch.setattr(GW2EIPage, "_check_version_worker", lambda _self: None)
    monkeypatch.setattr(LogFolderPage, "_detect_gw2_installations", lambda _self: ())
    monkeypatch.setattr(LogFolderPage, "_detect_arcdps_setups", lambda _self: ())
    config = Config(tmp_path / "sparky.properties")
    return SetupWizard(config), config, parser


def finding(tmp_path, *, with_routes=True):
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    source = tmp_path / "AxiBridge" / "config.json"
    source.parent.mkdir(exist_ok=True)
    source.write_text("{}", encoding="utf-8")
    hooks = ()
    if with_routes:
        hooks = (
            ImportedWebhook("Logspam", webhook(1, "fight-hidden"), "fight"),
            ImportedWebhook(
                "Nightly Debrief & Logs",
                webhook(2, "nightly-hidden"),
                "nightly",
            ),
            ImportedWebhook(
                "Backup Guild",
                webhook(3, "backup-hidden"),
                "fight",
                preferred=False,
            ),
        )
    return CompetitorFinding(
        app="AxiBridge",
        source_files=(source,),
        log_folders=(logs,),
        webhooks=hooks,
        settings=(
            ImportedSetting(
                "Thresholds",
                "minFightDuration",
                "27",
                "Minimum fight duration (seconds)",
            ),
            ImportedSetting(
                "Behavior", "closeToTray", "true", "Close to tray"
            ),
        ),
        tier=1,
    )


def test_import_dialog_shows_named_choices_but_never_webhook_secrets(
    tmp_path, qt_app
):
    dialog = CompetitorImportDialog(finding(tmp_path))
    visible = "\n".join(label.text() for label in dialog.findChildren(QLabel))
    visible += "\n" + "\n".join(
        check.text() for check in dialog.findChildren(QCheckBox)
    )
    visible += "\n" + "\n".join(
        dialog.fight_combo.itemText(index)
        for index in range(dialog.fight_combo.count())
    )

    assert "Logspam" in visible
    assert "Nightly Debrief & Logs" in "\n".join(
        dialog.nightly_combo.itemText(index)
        for index in range(dialog.nightly_combo.count())
    )
    assert "fight-hidden" not in visible
    assert "nightly-hidden" not in visible
    assert "backup-hidden" not in visible
    assert "Backup Guild" in visible
    assert dialog.selected_plan().has_discord_routing
    assert dialog.selected_plan().settings == dialog.finding.settings
    assert {group.title() for group in dialog.findChildren(QGroupBox)} >= {
        "Thresholds",
        "Behavior",
    }
    assert "Minimum fight duration (seconds)" in visible
    assert "Fight log folder" in visible
    assert "Elite Insights parser" in visible
    assert "Other settings found" in visible
    assert dialog.use_button.text() == "Use This Setup"
    assert "27" in visible
    selectable = [
        check
        for check in dialog.findChildren(QCheckBox)
        if check.property("importItem")
    ]
    assert selectable
    assert all(check.isChecked() for check in selectable if check.isEnabled())


def test_import_dialog_allows_every_detected_item_to_be_excluded(
    tmp_path, qt_app
):
    dialog = CompetitorImportDialog(finding(tmp_path))

    dialog.log_check.setChecked(False)
    dialog.setting_checks[0][1].setChecked(False)
    dialog.webhook_checks[0][1].setChecked(False)
    plan = dialog.selected_plan()

    assert plan.log_folder is None
    assert plan.settings == (dialog.finding.settings[1],)
    assert {hook.display_name for hook in plan.saved_webhooks} == {
        "Logspam",
        "Nightly Debrief & Logs",
    }

    dialog.select_none_button.click()
    assert all(
        not check.isChecked()
        for check in dialog.findChildren(QCheckBox)
        if check.property("importItem")
    )
    assert dialog.use_button.isEnabled() is False

    dialog.select_all_button.click()
    assert all(
        check.isChecked()
        for check in dialog.findChildren(QCheckBox)
        if check.property("importItem") and check.isEnabled()
    )
    assert dialog.use_button.isEnabled() is True


def test_import_dialog_never_silently_selects_more_than_three_channels(
    tmp_path, qt_app
):
    base = finding(tmp_path)
    hooks = base.webhooks + (
        ImportedWebhook("Fourth Guild", webhook(4, "fourth-hidden"), "unknown"),
        ImportedWebhook("Fifth Guild", webhook(5, "fifth-hidden"), "unknown"),
    )
    item = CompetitorFinding(
        app=base.app,
        source_files=base.source_files,
        log_folders=base.log_folders,
        webhooks=hooks,
        settings=base.settings,
    )
    dialog = CompetitorImportDialog(item)

    assert len(dialog.selected_plan().saved_webhooks) == 3
    assert dialog.use_button.isEnabled()
    assert "can keep 3 Discord channels" in dialog.webhook_limit_label.text()

    unchecked = [check for _hook, check in dialog.webhook_checks if not check.isChecked()]
    assert unchecked
    unchecked[0].setChecked(True)
    assert dialog.use_button.isEnabled() is False


def test_changing_a_route_combo_rechecks_the_three_channel_limit(
    tmp_path, qt_app
):
    hooks = tuple(
        ImportedWebhook(
            f"Channel {index}",
            webhook(10 + index, f"hidden-{index}"),
            "fight",
        )
        for index in range(1, 5)
    )
    item = CompetitorFinding(
        app="Neighbor",
        source_files=(tmp_path / "neighbor.json",),
        webhooks=hooks,
    )
    dialog = CompetitorImportDialog(item)
    assert dialog.use_button.isEnabled()
    assert len(dialog.selected_plan().saved_webhooks) == 3

    fourth_index = next(
        index
        for index in range(dialog.nightly_combo.count())
        if getattr(dialog.nightly_combo.itemData(index), "url", None)
        == hooks[3].url
    )
    dialog.nightly_combo.setCurrentIndex(fourth_index)
    qt_app.processEvents()

    assert dialog.use_button.isEnabled() is False


def test_import_dialog_counts_existing_routes_the_user_leaves_unchanged(
    tmp_path, qt_app
):
    config = Config(tmp_path / "sparky.properties")
    config.update("Discord", "discordWebhook", webhook(20, "existing"))
    config.update("Discord", "activeDiscordWebhook", "1")
    config.update("Discord", "raidReportDiscordWebhook", "0")
    config._load_values()
    hooks = (
        ImportedWebhook("New fight", webhook(21, "fight"), "fight"),
        ImportedWebhook("New nightly", webhook(22, "nightly"), "nightly"),
        ImportedWebhook("Saved 1", webhook(23, "saved-1"), "unknown"),
        ImportedWebhook("Saved 2", webhook(24, "saved-2"), "unknown"),
        ImportedWebhook("Saved 3", webhook(25, "saved-3"), "unknown"),
    )
    item = CompetitorFinding(
        app="Neighbor",
        source_files=(tmp_path / "neighbor.json",),
        webhooks=hooks,
    )
    dialog = CompetitorImportDialog(item, existing_config=config)

    dialog.fight_check.setChecked(False)
    dialog.nightly_check.setChecked(False)
    for _hook, check in dialog.webhook_checks:
        check.setChecked(True)
    qt_app.processEvents()

    assert len(dialog.selected_plan().saved_webhooks) == 3
    assert dialog.use_button.isEnabled() is False
    assert not dialog.webhook_limit_label.isHidden()

    dialog.webhook_checks[-1][1].setChecked(False)
    qt_app.processEvents()
    assert dialog.use_button.isEnabled()


def test_import_dialog_scrolls_without_covering_fixed_actions_on_short_screen(
    tmp_path, qt_app
):
    base = finding(tmp_path)
    sources = tuple(
        tmp_path / f"Tool {index}" / "config.json" for index in range(6)
    )
    item = CompetitorFinding(
        app="AxiBridge + MzFightReporter + TopStatsAIO + PlenBot + WvW Insights",
        source_files=sources,
        log_folders=base.log_folders,
        webhooks=base.webhooks,
        settings=base.settings,
        warnings=(
            "Fight folders differed; prioritizing AxiBridge.",
            "Individual fight channels differed; using AxiBridge: Logspam.",
            "One optional destination was not copied.",
        ),
    )
    dialog = CompetitorImportDialog(item)
    dialog.resize(620, 400)
    dialog.show()
    qt_app.processEvents()

    body_scroll = dialog.findChild(QScrollArea)
    buttons = dialog.findChild(QDialogButtonBox)
    assert body_scroll is not None and buttons is not None
    assert body_scroll.geometry().bottom() < buttons.geometry().top()
    assert body_scroll.verticalScrollBar().maximum() > 0
    assert dialog.use_button.isVisible() and dialog.use_button.isEnabled()
    visible = "\n".join(label.text() for label in dialog.findChildren(QLabel))
    assert all(str(path) in visible for path in sources)
    dialog.close()


def test_advanced_import_asks_for_tool_then_seeds_its_expected_file(
    tmp_path, monkeypatch, qt_app
):
    expected = tmp_path / "Guild Wars 2" / "addons" / "wvw-insights" / "settings.json"
    seen = {}
    monkeypatch.setattr(
        migration_ui.QInputDialog,
        "getItem",
        lambda *_args, **_kwargs: ("WvW Insights", True),
    )
    def expected_path(key, **_kwargs):
        seen["key"] = key
        return expected

    monkeypatch.setattr(
        migration_ui, "expected_competitor_config_path", expected_path
    )

    def choose_file(_parent, _title, start, _filter):
        seen["start"] = start
        return "", ""

    monkeypatch.setattr(migration_ui.QFileDialog, "getOpenFileName", choose_file)

    assert choose_manual_competitor_import(None) is None
    assert seen["key"] == "wvw-insights"
    assert seen["start"] == str(expected)


def test_export_confirmation_is_readable_and_says_backup_or_separate_folder(
    tmp_path, qt_app
):
    dialog = CompetitorExportConfirmDialog(
        "AxiBridge",
        "Fight logs: C:/Guild Wars 2/arcdps.cbtlogs/1\n"
        "Individual fights: Logspam\nNightly report: Nightly Debrief & Logs",
        tmp_path / "SparkyBot Export - AxiBridge",
        patches_existing=False,
    )
    visible = "\n".join(label.text() for label in dialog.findChildren(QLabel))

    assert dialog.minimumWidth() >= 640
    assert "separate migration folder" in visible
    assert "Create Setup" == dialog.create_button.text()


def test_long_export_preference_preview_scrolls_instead_of_growing_offscreen(
    tmp_path, qt_app
):
    preview = "\n".join(f"Preference {index}: On" for index in range(30))
    dialog = CompetitorExportConfirmDialog(
        "MzFightReporter",
        preview,
        tmp_path / "export",
        patches_existing=True,
    )

    scrolls = dialog.findChildren(QScrollArea)
    assert scrolls
    assert scrolls[0].maximumHeight() == 300
    assert "Preference 29: On" in "\n".join(
        label.text() for label in dialog.findChildren(QLabel)
    )


def test_export_success_is_wide_and_keeps_paths_selectable(tmp_path, qt_app):
    from core.competitor_export import export_competitor_config

    config = Config(tmp_path / "sparky.properties")
    logs = tmp_path / "arcdps.cbtlogs" / "1"
    logs.mkdir(parents=True)
    config.update("Paths", "logFolder", str(logs))
    config._load_values()
    result = export_competitor_config(config, "topstatsaio", tmp_path / "out")

    dialog = CompetitorExportDoneDialog(result)
    visible = "\n".join(label.text() for label in dialog.findChildren(QLabel))

    assert dialog.minimumWidth() >= 640
    assert "setup is ready" in visible
    assert "Your SparkyBot setup was not changed" in visible


def test_full_competitor_import_skips_optional_setup_and_is_go_ready(
    tmp_path, monkeypatch, qt_app
):
    wizard, config, parser = build_wizard(tmp_path, monkeypatch)
    item = finding(tmp_path)
    plan = build_import_plan(
        CompetitorFinding(
            app=item.app,
            source_files=item.source_files,
            log_folders=item.log_folders,
            parser_executables=(parser,),
            webhooks=item.webhooks,
            settings=item.settings,
            tier=1,
        )
    )

    wizard.use_competitor_import(plan)
    wizard.complete_page.initializePage()

    assert wizard.welcome_page.nextId() == PAGE_COMPLETE
    assert wizard.log_folder_page.folder_edit.text() == str(item.log_folders[0])
    assert wizard.gw2ei_page.path_edit.text() == str(parser)
    assert config.discord_webhook == webhook(1, "fight-hidden")
    assert config.discord_webhook2 == webhook(2, "nightly-hidden")
    assert config.discord_webhook3 == webhook(3, "backup-hidden")
    assert config.min_fight_duration == 27
    assert config.close_to_tray is True
    assert "Setup reused from AxiBridge" in wizard.complete_page.summary_label.text()
    assert "other log tool and its credentials were not changed" in wizard.complete_page.summary_label.text()


def test_path_only_import_requires_one_discord_route_before_finishing(
    tmp_path, monkeypatch, qt_app
):
    wizard, _config, parser = build_wizard(tmp_path, monkeypatch)
    item = finding(tmp_path, with_routes=False)
    plan = build_import_plan(
        CompetitorFinding(
            app="TopStatsAIO",
            source_files=item.source_files,
            log_folders=item.log_folders,
            parser_executables=(parser,),
        )
    )

    wizard.use_competitor_import(plan)

    assert wizard.welcome_page.nextId() == PAGE_DISCORD
    wizard.discord_page.webhook_edit.setText(webhook(3, "single-route"))
    wizard.discord_page.skip_check.setChecked(False)
    assert wizard.discord_page.validatePage() is True
    assert wizard.discord_page.nextId() == PAGE_COMPLETE


def test_guild_file_routes_win_when_competitor_import_adds_local_paths(
    tmp_path, monkeypatch, qt_app
):
    wizard, config, parser = build_wizard(tmp_path, monkeypatch)
    guild = parse_guild_config(
        {
            "format": "sparkybot-guild-config",
            "version": 1,
            "discord": {
                "enabled": True,
                "destinations": [
                    {"name": "Guild Logspam", "webhook_url": "4/guild-fight"},
                    {"name": "Guild Nightly", "webhook_url": "5/guild-nightly"},
                    {"name": "Third", "webhook_url": ""},
                ],
                "active_destination": 1,
                "raid_report_destination": 2,
                "bot_name": "SparkyBot",
                "embed_color": "#5865F2",
            },
        }
    )
    wizard.use_imported_guild_config(guild)
    item = finding(tmp_path)
    wizard.use_competitor_import(
        build_import_plan(
            CompetitorFinding(
                app=item.app,
                source_files=item.source_files,
                log_folders=item.log_folders,
                parser_executables=(parser,),
                webhooks=item.webhooks,
            )
        )
    )

    assert config.discord_webhook.endswith("/guild-fight")
    assert config.discord_webhook2.endswith("/guild-nightly")
    assert config.log_folder == str(item.log_folders[0])


def test_tool_base_then_guild_override_keeps_non_overlapping_preferences(
    tmp_path, monkeypatch, qt_app
):
    wizard, config, parser = build_wizard(tmp_path, monkeypatch)
    item = finding(tmp_path)
    wizard.use_competitor_import(
        build_import_plan(
            CompetitorFinding(
                app=item.app,
                source_files=item.source_files,
                log_folders=item.log_folders,
                parser_executables=(parser,),
                webhooks=item.webhooks,
                settings=item.settings,
            )
        )
    )
    assert config.discord_webhook == webhook(1, "fight-hidden")
    assert config.min_fight_duration == 27

    guild = parse_guild_config(
        {
            "format": "sparkybot-guild-config",
            "version": 1,
            "discord": {
                "enabled": True,
                "destinations": [
                    {"name": "Guild Logspam", "webhook_url": "21/guild-fight"},
                    {"name": "Guild Nightly", "webhook_url": "22/guild-night"},
                    {"name": "Third", "webhook_url": ""},
                ],
                "active_destination": 1,
                "raid_report_destination": 2,
                "bot_name": "Guild Sparky",
                "embed_color": "#123456",
            },
        }
    )
    wizard.use_imported_guild_config(guild)

    assert config.discord_webhook.endswith("/guild-fight")
    assert config.discord_webhook2.endswith("/guild-night")
    assert config.discord_webhook_label == "Guild Sparky"
    assert config.embed_color == 0x123456
    assert config.min_fight_duration == 27
    assert config.close_to_tray is True
    assert config.log_folder == str(item.log_folders[0])


def test_other_tools_dialog_is_plain_and_excludes_unrelated_roster_dashboards(
    qt_app,
):
    dialog = InteropCatalogDialog()
    rendered = "\n".join(label.text() for label in dialog.findChildren(QLabel))

    assert "What it does well" in rendered
    assert "SparkyBot connection" in rendered
    assert "MzFightReporter" in rendered
    assert "AxiBridge" in rendered
    assert "TopStatsAIO" in rendered
    assert "WvW Insights" in rendered
    assert "GW2-WVW-Teams" not in rendered
    assert "TopStatsDash" not in rendered
    assert "No competitor code" in rendered
