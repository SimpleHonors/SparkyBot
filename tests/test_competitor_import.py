import json
import sqlite3
from pathlib import Path

import pytest

from core.competitor_import import (
    COMPETITOR_IMPORT_TARGETS,
    CompetitorConfigError,
    CompetitorFinding,
    CompetitorImportPlan,
    ImportedSetting,
    MAX_CONFIG_BYTES,
    apply_competitor_import,
    build_import_plan,
    discover_competitor_configs,
    expected_competitor_config_path,
    expected_competitor_config_paths,
    parse_competitor_config,
)
from core.config import Config


def webhook(number: int, token: str) -> str:
    return f"https://discord.com/api/webhooks/{number:018d}/{token}"


def test_every_advanced_tool_choice_has_a_specific_expected_file(tmp_path):
    home = tmp_path / "home"
    roaming = tmp_path / "roaming"
    local = tmp_path / "local"
    gw2 = tmp_path / "Guild Wars 2"

    for target in COMPETITOR_IMPORT_TARGETS:
        paths = expected_competitor_config_paths(
            target.key,
            home=home,
            appdata=roaming,
            local_appdata=local,
            gw2_dirs=(gw2,),
        )
        assert paths, target.name
        assert paths[0].name not in {"", "."}

    expected = gw2 / "addons" / "wvw-insights" / "settings.json"
    expected.parent.mkdir(parents=True)
    expected.write_text("{}", encoding="utf-8")
    assert expected_competitor_config_path(
        "wvw-insights",
        home=home,
        appdata=roaming,
        local_appdata=local,
        gw2_dirs=(gw2,),
    ) == expected


def test_mzfightreporter_imports_every_safe_matching_preference(tmp_path):
    logs = tmp_path / "logs" / "1"
    logs.mkdir(parents=True)
    config_file = tmp_path / "config.properties"
    config_file.write_text(
        "\n".join(
            (
                f"defaultLogFolder={logs}",
                "customLogFolder=",
                f"discordWebhook={webhook(1, 'fight-one')}",
                "discordWebhookLabel=Logspam",
                f"discordWebhook2={webhook(2, 'fight-two')}",
                "discordWebhookLabel2=Second Guild",
                f"discordWebhook3={webhook(3, 'fight-three')}",
                "discordWebhookLabel3=Third Guild",
                "activeDiscordWebhook=2",
                "minFightDuration=27",
                "minFightDowns=3",
                "minFightTotalDmg=765432",
                "maxUploadMegabytes=24",
                "largeUploadsAfterParse=true",
                "showDamage=false",
                "showHeals=false",
                "showQuickReport=false",
                "closeToTray=true",
                "minimizeToTray=false",
                "startMinimized=true",
                "maxParseMemory=8192",
                "embedColor=#A1B2C3",
                "twitchChannelName=quiet_commander",
                "twitchUseTLS=false",
                "twitchBotToken=must-not-import",
            )
        ),
        encoding="utf-8",
    )

    finding = parse_competitor_config(config_file)

    assert finding.app == "MzFightReporter"
    assert finding.log_folders == (logs,)
    assert [hook.display_name for hook in finding.webhooks] == [
        "Logspam",
        "Second Guild",
        "Third Guild",
    ]
    assert finding.webhooks[1].preferred is True
    imported = {
        (setting.section, setting.key): setting.value
        for setting in finding.settings
    }
    assert imported[("Thresholds", "minFightDuration")] == "27"
    assert imported[("Thresholds", "maxUploadSize")] == "24"
    assert imported[("UI", "showDamage")] == "false"
    assert imported[("Behavior", "closeToTray")] == "true"
    assert imported[("Behavior", "maxParseMemory")] == "8192"
    assert imported[("Discord", "embedColor")] == "0xA1B2C3"
    assert imported[("Twitch", "twitchChannelName")] == "quiet_commander"
    assert imported[("Twitch", "twitchUseTLS")] == "false"
    assert any("token" in warning.casefold() for warning in finding.warnings)
    assert "must-not-import" not in finding.summary()
    assert "fight-two" not in finding.summary()

    config = Config(tmp_path / "sparky.properties")
    apply_competitor_import(config, build_import_plan(finding), persist=False)
    assert config.min_fight_duration == 27
    assert config.show_damage is False
    assert config.close_to_tray is True
    assert config.embed_color == 0xA1B2C3
    assert config.twitch_channel == "quiet_commander"
    assert config.twitch_use_tls is False
    assert config.enable_twitch is False
    assert config.twitch_token == ""
    assert config.discord_webhook == webhook(2, "fight-two")
    assert config.discord_webhook_name1 == "Second Guild"
    assert config.discord_webhook2 == webhook(1, "fight-one")
    assert config.discord_webhook_name2 == "Logspam"
    assert config.discord_webhook3 == webhook(3, "fight-three")
    assert config.discord_webhook_name3 == "Third Guild"
    assert config.raid_report_discord_webhook == 1


def test_plenbot_pairs_app_settings_with_active_discord_webhooks(tmp_path):
    logs = tmp_path / "cbtlogs"
    gw2 = tmp_path / "Guild Wars 2"
    logs.mkdir()
    gw2.mkdir()
    settings = tmp_path / "app_settings.json"
    settings.write_text(
        json.dumps(
            {
                "logsLocation": str(logs),
                "gw2Location": str(gw2),
                "closeToTry": True,
                "minimiseToTry": False,
                "gw2APIKeys": [{"apiKey": "must-not-import"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "discord_webhooks.json").write_text(
        json.dumps(
            [
                {"isActive": False, "name": "Old", "url": webhook(3, "old")},
                {"isActive": True, "name": "Current", "url": webhook(4, "new")},
            ]
        ),
        encoding="utf-8",
    )

    finding = parse_competitor_config(settings)
    plan = build_import_plan(finding)

    assert finding.app == "PlenBot Log Uploader"
    assert finding.log_folders == (logs,)
    assert finding.gw2_directories == (gw2,)
    assert len(finding.source_files) == 2
    assert {
        (setting.section, setting.key, setting.value)
        for setting in finding.settings
    } == {
        ("Behavior", "closeToTray", "true"),
        ("Behavior", "minimizeToTray", "false"),
    }
    assert any("credentials" in warning.casefold() for warning in finding.warnings)
    assert "must-not-import" not in finding.summary()
    assert plan.fight_webhook.display_name == "Current"
    assert plan.nightly_webhook == plan.fight_webhook


def test_axibridge_assigns_fight_and_nightly_routes_without_exposing_tokens(tmp_path):
    app_dir = tmp_path / "AxiBridge"
    app_dir.mkdir()
    logs = tmp_path / "raw-logs"
    logs.mkdir()
    config_file = app_dir / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "logDirectory": str(logs),
                "discordWebhookUrl": webhook(5, "legacy"),
                "webhooks": [
                    {"id": "fight", "name": "Logspam", "url": webhook(6, "fight")}
                ],
                "reportWebhooks": [
                    {
                        "id": "nightly",
                        "name": "Nightly Debrief",
                        "url": webhook(7, "nightly"),
                        "enabled": True,
                    }
                ],
                "embedStatSettings": {
                    "showDamage": False,
                    "showHealing": False,
                    "showCleanses": True,
                    "showBoonStrips": False,
                    "showCC": True,
                    "showDowns": False,
                    "showKills": False,
                },
                "closeBehavior": "minimize",
                "dpsReportToken": "do-not-touch-either",
                "githubToken": "do-not-touch",
            }
        ),
        encoding="utf-8",
    )

    finding = parse_competitor_config(config_file)
    plan = build_import_plan(finding)

    assert finding.app == "AxiBridge"
    assert plan.fight_webhook.role == "fight"
    assert plan.nightly_webhook.role == "nightly"
    assert plan.nightly_webhook.display_name == "Nightly Debrief"
    assert {
        (setting.section, setting.key): setting.value
        for setting in finding.settings
    } == {
        ("UI", "showDamage"): "false",
        ("UI", "showHeals"): "false",
        ("UI", "showCleanses"): "true",
        ("UI", "showStrips"): "false",
        ("UI", "showCCs"): "true",
        ("UI", "showDownsKills"): "false",
        ("Behavior", "closeToTray"): "true",
    }
    assert any("credentials" in warning.casefold() for warning in finding.warnings)
    assert "do-not-touch" not in plan.summary()
    assert "do-not-touch-either" not in finding.summary()
    assert "nightly" not in finding.summary().split("Nightly debrief: ", 1)[1]


def test_topstats_imports_raw_last_folder_but_not_combiner_output_folder(tmp_path):
    raw_logs = tmp_path / "raw"
    output = tmp_path / "generated-ei-json"
    raw_logs.mkdir()
    output.mkdir()
    state = tmp_path / "ui-state.json"
    state.write_text(json.dumps({"lastFolder": str(raw_logs)}), encoding="utf-8")
    combiner = tmp_path / "top_stats_config.ini"
    combiner.write_text(
        "[TopStatsCfg]\n"
        "guild_name = Friendly Guild\n"
        f"input_directory = {output}\n"
        "[DiscordCfg]\n"
        f"webhook_url = {webhook(8, 'nightly')}\n",
        encoding="utf-8",
    )

    state_finding = parse_competitor_config(state)
    combiner_finding = parse_competitor_config(combiner)

    assert state_finding.app == "TopStatsAIO"
    assert state_finding.log_folders == (raw_logs,)
    assert combiner_finding.log_folders == ()
    assert combiner_finding.webhooks[0].role == "nightly"
    assert combiner_finding.settings[0].value == "Friendly Guild"
    assert str(output) not in combiner_finding.summary()


def test_wvw_insights_pairs_settings_and_webhook_files(tmp_path):
    addon = tmp_path / "wvw-insights"
    addon.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()
    settings = addon / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "log_directory": str(logs),
                "guild_name": "Reset Fight Club",
                "history_token": "must-not-import",
            }
        ),
        encoding="utf-8",
    )
    (addon / "webhooks.json").write_text(
        json.dumps(
            {
                "saved_webhooks": [
                    {"name": "Guild fights", "url": webhook(9, "saved")}
                ],
                "last_webhook_url": webhook(10, "last"),
            }
        ),
        encoding="utf-8",
    )

    finding = parse_competitor_config(settings)

    assert finding.app == "WvW Insights"
    assert finding.log_folders == (logs,)
    assert len(finding.webhooks) == 2
    assert all(item.role == "fight" for item in finding.webhooks)
    assert finding.settings[0].section == "Discord"
    assert finding.settings[0].key == "discordWebhookLabel"
    assert finding.settings[0].value == "Reset Fight Club"
    assert any("tokens" in warning.casefold() for warning in finding.warnings)
    assert "must-not-import" not in finding.summary()


def test_invalid_neighbor_preferences_are_ignored_instead_of_poisoning_config(
    tmp_path,
):
    logs = tmp_path / "logs"
    logs.mkdir()
    config_file = tmp_path / "config.properties"
    config_file.write_text(
        f"defaultLogFolder={logs}\n"
        "minFightDuration=999999\n"
        "showDamage=maybe\n"
        "embedColor=definitely-purple\n"
        "maxParseMemory=-1\n",
        encoding="utf-8",
    )

    finding = parse_competitor_config(config_file)

    assert finding.settings == ()


@pytest.mark.parametrize(
    ("filename", "payload", "expected_app", "path_key"),
    [
        (
            "config.json",
            {"logPath": "{logs}", "webhookURL": webhook(11, "hax")},
            "WvW Log Uploader",
            "logs",
        ),
        (
            "l0g-101086-config.json",
            {
                "arcdps_logs": "{logs}",
                "guilds": [{"name": "Guild", "webhook_url": webhook(12, "l0g")}],
            },
            "L0G-101086",
            "logs",
        ),
        (
            "settings.json",
            {"schema_version": 1, "general": {"log_directory": "{logs}"}},
            "GW2 Manny Uploader",
            "logs",
        ),
        (
            "Settings.json",
            {"LogRootPaths": ["{logs}"]},
            "GW2Scratch Log Manager",
            "logs",
        ),
        (
            "config.json",
            {"watch_folder": "{logs}"},
            "GW2 Commanders Watch",
            "logs",
        ),
    ],
)
def test_json_secondary_adapters(
    tmp_path, filename, payload, expected_app, path_key
):
    logs = tmp_path / path_key
    logs.mkdir()
    target_dir = tmp_path / expected_app.replace(" ", "-")
    target_dir.mkdir()
    target = target_dir / filename
    target.write_text(
        json.dumps(payload).replace("{logs}", str(logs)), encoding="utf-8"
    )

    finding = parse_competitor_config(target)

    assert finding.app == expected_app
    assert finding.log_folders == (logs,)


def test_wingman_requires_its_addon_directory_signature(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    addon = tmp_path / "wingman-uploader"
    addon.mkdir()
    target = addon / "settings.json"
    target.write_text(json.dumps({"logpath": str(logs)}), encoding="utf-8")

    assert parse_competitor_config(target).app == "Nexus Wingman Uploader"


def test_neighbor_parser_detection_accepts_cli_but_not_elite_insights_gui(tmp_path):
    app = tmp_path / "AxiBridge"
    parser_dir = app / "elite-insights"
    parser_dir.mkdir(parents=True)
    logs = tmp_path / "logs"
    logs.mkdir()
    config = app / "config.json"
    config.write_text(json.dumps({"logDirectory": str(logs)}), encoding="utf-8")
    gui = parser_dir / "GuildWars2EliteInsights.exe"
    gui.write_bytes(b"gui")

    without_cli = parse_competitor_config(config)
    assert without_cli.parser_executables == ()

    cli = parser_dir / "GuildWars2EliteInsights-CLI.exe"
    cli.write_bytes(b"cli")
    with_cli = parse_competitor_config(config)
    assert with_cli.parser_executables == (cli,)


def test_text_and_xml_adapters_only_read_allowlisted_path_fields(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    evtc = tmp_path / "config.ini"
    evtc.write_text(
        f"[Settings]\nARCDPS_LOG_DIR = {logs}\nWEBHOOK_URL = {webhook(13, 'evtc')}\n",
        encoding="utf-8",
    )
    arclog = tmp_path / "config.toml"
    arclog.write_text(f'logpath = "{logs}"\nusertoken = "secret"\n', encoding="utf-8")
    toxic = tmp_path / "config.yml"
    toxic.write_text(f'arcdps_logs: "{logs}"\nbot_token: secret\n', encoding="utf-8")
    xml = tmp_path / "user.config"
    xml.write_text(
        "<configuration><userSettings><Settings>"
        f'<setting name="ArcLogsPath"><value>{logs}</value></setting>'
        '<setting name="Webhook"><value>encrypted-secret</value></setting>'
        "</Settings></userSettings></configuration>",
        encoding="utf-8",
    )

    assert parse_competitor_config(evtc).app == "EVTC_parser"
    assert parse_competitor_config(arclog).app == "arclog"
    assert parse_competitor_config(toxic).app == "toxic-elitist"
    xml_finding = parse_competitor_config(xml)
    assert xml_finding.app == "LogUploader2"
    assert "encrypted-secret" not in xml_finding.summary()


def test_drevarr_wvw_teams_roster_webhook_is_not_misread_as_evtc_logspam(tmp_path):
    roster = tmp_path / "config.ini"
    roster.write_text(
        "[Settings]\n"
        f"WEBHOOK_URL = {webhook(99, 'roster-channel')}\n"
        "GUILD_ID = 123456789012345678\n"
        "ALLIANCES_REMOTE_SHEET_URL = https://example.invalid/alliances.csv\n",
        encoding="utf-8",
    )

    with pytest.raises(CompetitorConfigError, match="not an EVTC_parser"):
        parse_competitor_config(roster)


def test_import_plan_selects_arcdps_wvw_child_from_base_log_folder(tmp_path):
    base = tmp_path / "arcdps.cbtlogs"
    (base / "2").mkdir(parents=True)
    wvw = base / "1"
    wvw.mkdir()
    source = tmp_path / "config.ini"
    source.write_text(
        "[Settings]\n"
        f"ARCDPS_LOG_DIR = {base}\n"
        f"WEBHOOK_URL = {webhook(100, 'fight')}\n",
        encoding="utf-8",
    )

    plan = build_import_plan(parse_competitor_config(source))

    assert plan.log_folder == wvw


def test_import_plan_never_mislabels_a_different_encounter_folder_as_wvw(tmp_path):
    base = tmp_path / "arcdps.cbtlogs"
    (base / "2").mkdir(parents=True)
    source = tmp_path / "config.ini"
    source.write_text(
        "[Settings]\n"
        f"ARCDPS_LOG_DIR = {base}\n"
        f"WEBHOOK_URL = {webhook(101, 'fight')}\n",
        encoding="utf-8",
    )

    plan = build_import_plan(parse_competitor_config(source))

    assert plan.log_folder == base / "1"
    assert not plan.log_folder.exists()


def test_arcdps_uploader_database_reads_only_wvw_webhooks(tmp_path):
    database = tmp_path / "uploader.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE webhooks (id INTEGER, name TEXT, url TEXT, wvw INTEGER)"
    )
    connection.executemany(
        "INSERT INTO webhooks VALUES (?, ?, ?, ?)",
        [
            (1, "WvW", webhook(14, "wvw"), 1),
            (2, "PvE", webhook(15, "pve"), 0),
        ],
    )
    connection.commit()
    connection.close()

    finding = parse_competitor_config(database)

    assert finding.app == "arcdps-uploader"
    assert [hook.display_name for hook in finding.webhooks] == ["WvW"]


def test_discovery_checks_known_app_addon_and_bounded_portable_locations(tmp_path):
    appdata = tmp_path / "AppData" / "Roaming"
    local = tmp_path / "AppData" / "Local"
    downloads = tmp_path / "Downloads"
    gw2 = tmp_path / "custom-steam-library" / "Guild Wars 2"
    for directory in (appdata / "AxiBridge", local, downloads / "MzFightReporter"):
        directory.mkdir(parents=True)
    logs = tmp_path / "logs"
    logs.mkdir()
    (appdata / "AxiBridge" / "config.json").write_text(
        json.dumps({"logDirectory": str(logs), "webhooks": []}), encoding="utf-8"
    )
    (downloads / "MzFightReporter" / "config.properties").write_text(
        f"defaultLogFolder={logs}\n", encoding="utf-8"
    )
    addon = gw2 / "addons" / "wingman-uploader"
    addon.mkdir(parents=True)
    (addon / "settings.json").write_text(
        json.dumps({"logpath": str(logs)}), encoding="utf-8"
    )
    # A generic config is deliberately not guessed from filename alone.
    (downloads / "config.json").write_text(json.dumps({"password": "nope"}))

    findings = discover_competitor_configs(
        home=tmp_path,
        appdata=appdata,
        local_appdata=local,
        portable_roots=(downloads,),
        gw2_dirs=(gw2,),
    )

    assert {item.app for item in findings} == {
        "AxiBridge",
        "MzFightReporter",
        "Nexus Wingman Uploader",
    }


def test_apply_plan_sets_basic_routing_and_turns_optional_features_off(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    source = tmp_path / "config.json"
    source.write_text(
        json.dumps(
            {
                "logPath": str(logs),
                "webhookURL": webhook(16, "one-destination"),
            }
        ),
        encoding="utf-8",
    )
    config = Config(tmp_path / "sparky.properties")
    config.update("AI", "enableAiAnalysis", "true")
    config.update("TTS", "enableTts", "true")
    config.update("Twitch", "enableTwitchBot", "true")

    plan = build_import_plan(parse_competitor_config(source))
    apply_competitor_import(config, plan, persist=False)

    assert config.log_folder == str(logs)
    assert config.enable_discord_bot is True
    assert config.discord_webhook == webhook(16, "one-destination")
    assert config.raid_report_discord_webhook == 1
    assert config.enable_ai_analysis is False
    assert config.tts_enabled is False
    assert config.enable_twitch is False
    assert not (tmp_path / "sparky.properties").exists()


def test_existing_user_can_import_without_disabling_their_optional_features(tmp_path):
    source = tmp_path / "config.yml"
    logs = tmp_path / "logs"
    logs.mkdir()
    source.write_text(f"arcdps_logs: {logs}\n", encoding="utf-8")
    config = Config(tmp_path / "sparky.properties")
    config.update("AI", "enableAiAnalysis", "true")
    config._load_values()

    apply_competitor_import(
        config,
        build_import_plan(parse_competitor_config(source)),
        persist=False,
        turn_off_optional=False,
    )

    assert config.enable_ai_analysis is True
    assert config.log_folder == str(logs)


def test_apply_rejects_unallowlisted_preferences_and_rolls_back(tmp_path):
    config = Config(tmp_path / "sparky.properties")
    original_folder = config.log_folder
    candidate_folder = tmp_path / "logs"
    candidate_folder.mkdir()
    source = tmp_path / "neighbor.json"
    source.write_text("{}", encoding="utf-8")
    finding = CompetitorFinding(app="Neighbor", source_files=(source,))
    plan = CompetitorImportPlan(
        finding=finding,
        log_folder=candidate_folder,
        parser_executable=None,
        fight_webhook=None,
        nightly_webhook=None,
        settings=(ImportedSetting("AI", "aiApiKey", "stolen", "API key"),),
    )

    with pytest.raises(CompetitorConfigError, match="not a safe"):
        apply_competitor_import(config, plan, persist=False)

    assert config.log_folder == original_folder
    assert config.ai_api_key == ""


def test_manual_import_rejects_oversized_or_unrecognized_files(tmp_path):
    huge = tmp_path / "config.json"
    huge.write_bytes(b" " * (MAX_CONFIG_BYTES + 1))
    unrelated = tmp_path / "settings.json"
    unrelated.write_text(json.dumps({"api_key": "secret"}), encoding="utf-8")

    with pytest.raises(CompetitorConfigError, match="too large"):
        parse_competitor_config(huge)
    with pytest.raises(CompetitorConfigError, match="not a supported"):
        parse_competitor_config(unrelated)
