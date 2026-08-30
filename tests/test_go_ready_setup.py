import json
import os
from pathlib import Path

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QFileDialog

from core import apppaths
from core.config import Config
from core.setup_wizard import (
    PAGE_COMPLETE,
    PAGE_GW2EI,
    GW2EIPage,
    LogFolderPage,
    SetupWizard,
)
from core.shareable_config import GuildConfigError, parse_guild_config


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def guild_bundle(*, enabled=True):
    return parse_guild_config(
        {
            "format": "sparkybot-guild-config",
            "version": 1,
            "discord": {
                "enabled": enabled,
                "destinations": [
                    {
                        "name": "Logspam",
                        "webhook_url": "123456789012345678/logspam-token",
                    },
                    {
                        "name": "Nightly Debrief & Logs",
                        "webhook_url": "223456789012345678/nightly-token",
                    },
                    {"name": "Officers", "webhook_url": ""},
                ],
                "active_destination": 1,
                "raid_report_destination": 2,
                "bot_name": "Guild Sparky",
                "embed_color": "#12ABEF",
            },
        }
    )


def build_wizard(tmp_path, monkeypatch, *, parser_ready=False):
    parser_dir = tmp_path / "parser"
    if parser_ready:
        parser_dir.mkdir()
        (parser_dir / "GuildWars2EliteInsights-CLI.exe").write_bytes(b"test")
    log_base = tmp_path / "arcdps.cbtlogs"
    wvw_logs = log_base / "1"
    wvw_logs.mkdir(parents=True)
    monkeypatch.setattr(apppaths, "is_frozen", lambda: True)
    monkeypatch.setattr(apppaths, "gw2ei_dir", lambda: parser_dir)
    monkeypatch.setattr(GW2EIPage, "_check_version_worker", lambda _self: None)
    monkeypatch.setattr(
        LogFolderPage,
        "_detect_default_log_path",
        lambda _self: str(log_base),
    )
    config = Config(tmp_path / "config.properties")
    wizard = SetupWizard(config)
    return wizard, config, wvw_logs


def test_imported_setup_has_two_required_steps_then_ready(
    tmp_path, monkeypatch, qt_app
):
    wizard, config, wvw_logs = build_wizard(tmp_path, monkeypatch)

    wizard.use_imported_guild_config(guild_bundle())
    wizard.complete_page.initializePage()

    assert wizard.welcome_page.nextId() == PAGE_GW2EI
    assert wizard.log_folder_page.nextId() == PAGE_COMPLETE
    assert wizard.log_folder_page.folder_edit.text() == str(wvw_logs)
    assert "Logspam" in wizard.complete_page.summary_label.text()
    assert "Nightly Debrief & Logs" in wizard.complete_page.summary_label.text()
    assert "AI, voice, and Twitch are off" in wizard.complete_page.summary_label.text()
    assert config.enable_discord_bot is True
    assert config.enable_ai_analysis is False
    assert config.tts_enabled is False
    assert config.enable_twitch is False


def test_fully_detected_computer_can_go_straight_to_ready(
    tmp_path, monkeypatch, qt_app
):
    wizard, _config, _wvw_logs = build_wizard(
        tmp_path, monkeypatch, parser_ready=True
    )

    wizard.use_imported_guild_config(guild_bundle())

    assert wizard.welcome_page.nextId() == PAGE_COMPLETE


def test_imported_setup_finish_persists_go_ready_defaults(
    tmp_path, monkeypatch, qt_app
):
    wizard, config, _wvw_logs = build_wizard(tmp_path, monkeypatch)
    wizard.use_imported_guild_config(guild_bundle())

    wizard.accept()

    assert config.config_path.is_file()
    assert config.discord_webhook_name1 == "Logspam"
    assert config.discord_webhook_name2 == "Nightly Debrief & Logs"
    assert config.active_discord_webhook == 1
    assert config.raid_report_discord_webhook == 2
    assert config.enable_discord_bot is True
    assert config.enable_ai_analysis is False
    assert config.tts_enabled is False
    assert config.enable_twitch is False
    assert config.raidreport_run_mode == "run-button"


def test_first_run_rejects_a_setup_file_with_posting_disabled(
    tmp_path, monkeypatch, qt_app
):
    wizard, config, _wvw_logs = build_wizard(tmp_path, monkeypatch)

    with pytest.raises(GuildConfigError, match="posting is turned off"):
        wizard.use_imported_guild_config(guild_bundle(enabled=False))

    assert wizard.imported_guild_config is None
    assert not config.config_path.exists()


def test_machine_local_pages_block_a_fake_ready_state(
    tmp_path, monkeypatch, qt_app
):
    wizard, _config, _wvw_logs = build_wizard(tmp_path, monkeypatch)

    assert wizard.gw2ei_page.validatePage() is False
    assert wizard.log_folder_page.validatePage() is True


def test_choose_file_to_ready_is_one_bounded_flow(
    tmp_path, monkeypatch, qt_app
):
    wizard, config, _wvw_logs = build_wizard(tmp_path, monkeypatch)
    setup_file = tmp_path / "guild-setup.json"
    setup_file.write_text(
        json.dumps(guild_bundle().as_dict()), encoding="utf-8"
    )
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(setup_file), ""),
    )
    monkeypatch.setattr(
        wizard.welcome_page, "_confirm_import", lambda _bundle: True
    )
    wizard.show()
    qt_app.processEvents()

    wizard.welcome_page._import_guild_config()

    assert wizard.currentId() == PAGE_GW2EI
    wizard.gw2ei_page._install_success = True
    wizard.next()
    assert wizard.currentId() == PAGE_COMPLETE
    wizard.accept()
    reloaded = Config(config.config_path)
    assert reloaded.discord_webhook_name1 == "Logspam"
    assert reloaded.discord_webhook_name2 == "Nightly Debrief & Logs"
    assert reloaded.enable_ai_analysis is False
    assert reloaded.tts_enabled is False
    assert reloaded.enable_twitch is False
    wizard.close()
