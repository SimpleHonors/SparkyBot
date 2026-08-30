import json
import sqlite3
from pathlib import Path

import pytest

from core.competitor_import import (
    CompetitorConfigError,
    MAX_CONFIG_BYTES,
    apply_competitor_import,
    build_import_plan,
    discover_competitor_configs,
    parse_competitor_config,
)
from core.config import Config


def webhook(number: int, token: str) -> str:
    return f"https://discord.com/api/webhooks/{number:018d}/{token}"


def test_mzfightreporter_imports_only_log_paths_and_named_webhooks(tmp_path):
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
                "activeDiscordWebhook=2",
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
    ]
    assert finding.webhooks[1].preferred is True
    assert "must-not-import" not in finding.summary()
    assert "fight-two" not in finding.summary()


def test_plenbot_pairs_app_settings_with_active_discord_webhooks(tmp_path):
    logs = tmp_path / "cbtlogs"
    gw2 = tmp_path / "Guild Wars 2"
    logs.mkdir()
    gw2.mkdir()
    settings = tmp_path / "app_settings.json"
    settings.write_text(
        json.dumps({"logsLocation": str(logs), "gw2Location": str(gw2)}),
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
    assert "do-not-touch" not in plan.summary()
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
        "[TopStats]\n"
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
    assert str(output) not in combiner_finding.summary()


def test_wvw_insights_pairs_settings_and_webhook_files(tmp_path):
    addon = tmp_path / "wvw-insights"
    addon.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()
    settings = addon / "settings.json"
    settings.write_text(json.dumps({"log_directory": str(logs)}), encoding="utf-8")
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


def test_manual_import_rejects_oversized_or_unrecognized_files(tmp_path):
    huge = tmp_path / "config.json"
    huge.write_bytes(b" " * (MAX_CONFIG_BYTES + 1))
    unrelated = tmp_path / "settings.json"
    unrelated.write_text(json.dumps({"api_key": "secret"}), encoding="utf-8")

    with pytest.raises(CompetitorConfigError, match="too large"):
        parse_competitor_config(huge)
    with pytest.raises(CompetitorConfigError, match="not a supported"):
        parse_competitor_config(unrelated)
