import json

import pytest

from core.competitor_export import (
    COMPETITOR_EXPORT_TARGETS,
    export_competitor_config,
    export_preview,
)
from core.competitor_import import CompetitorConfigError, parse_competitor_config
from core.config import Config


def webhook(number: int, token: str) -> str:
    return f"https://discord.com/api/webhooks/{number:018d}/{token}"


def configured_sparky(tmp_path):
    logs = tmp_path / "arcdps.cbtlogs" / "1"
    logs.mkdir(parents=True)
    config = Config(tmp_path / "sparky.properties")
    updates = (
        ("Paths", "logFolder", str(logs)),
        ("Discord", "discordWebhook", webhook(1, "fight-secret")),
        ("Discord", "discordWebhookName1", "Logspam"),
        ("Discord", "discordWebhook2", webhook(2, "nightly-secret")),
        ("Discord", "discordWebhookName2", "Nightly Debrief & Logs"),
        ("Discord", "activeDiscordWebhook", "1"),
        ("Discord", "raidReportDiscordWebhook", "2"),
    )
    for section, key, value in updates:
        config.update(section, key, value)
    config._load_values()
    return config, logs


@pytest.mark.parametrize(
    ("target", "main_file", "expected_app"),
    [
        ("axibridge", "config.json", "AxiBridge"),
        ("topstatsaio", "ui-state.json", "TopStatsAIO"),
        ("plenbot", "app_settings.json", "PlenBot Log Uploader"),
        ("mzfightreporter", "config.properties", "MzFightReporter"),
        ("wvw-insights", "settings.json", "WvW Insights"),
        ("evtc-parser", "config.ini", "EVTC_parser"),
    ],
)
def test_every_supported_export_is_readable_by_its_import_adapter(
    tmp_path, target, main_file, expected_app
):
    config, logs = configured_sparky(tmp_path)
    destination = tmp_path / target

    result = export_competitor_config(config, target, destination)
    round_trip = parse_competitor_config(destination / main_file)

    assert result.target.key == target
    assert round_trip.app == expected_app
    assert round_trip.log_folders == (logs,)
    assert all(path.is_file() for path in result.written_files)


def test_axibridge_round_trip_keeps_fight_and_nightly_roles(tmp_path):
    config, _logs = configured_sparky(tmp_path)
    for section, key, value in (
        ("UI", "showDamage", "false"),
        ("UI", "showHeals", "false"),
        ("UI", "showStrips", "false"),
        ("UI", "showDownsKills", "false"),
        ("Behavior", "closeToTray", "true"),
    ):
        config.update(section, key, value)
    config._load_values()
    destination = tmp_path / "AxiBridge"

    export_competitor_config(config, "axibridge", destination)
    finding = parse_competitor_config(destination / "config.json")

    assert any(hook.display_name == "Logspam" and hook.role == "fight" for hook in finding.webhooks)
    assert any(
        hook.display_name == "Nightly Debrief & Logs" and hook.role == "nightly"
        for hook in finding.webhooks
    )
    settings = {
        (setting.section, setting.key): setting.value
        for setting in finding.settings
    }
    assert settings[("UI", "showDamage")] == "false"
    assert settings[("UI", "showHeals")] == "false"
    assert settings[("UI", "showStrips")] == "false"
    assert settings[("UI", "showDownsKills")] == "false"
    assert settings[("Behavior", "closeToTray")] == "true"


def test_safe_preferences_round_trip_through_reciprocal_neighbor_formats(tmp_path):
    config, _logs = configured_sparky(tmp_path)
    updates = (
        ("Discord", "discordWebhookLabel", "Reset Fight Club"),
        ("Discord", "embedColor", "0xA1B2C3"),
        ("Thresholds", "minFightDuration", "27"),
        ("Thresholds", "minFightDowns", "3"),
        ("Thresholds", "minFightTotalDmg", "765432"),
        ("Thresholds", "maxUploadSize", "24"),
        ("Thresholds", "uploadLargeAfterParse", "true"),
        ("UI", "showDamage", "false"),
        ("UI", "showQuickReport", "false"),
        ("Behavior", "closeToTray", "true"),
        ("Behavior", "minimizeToTray", "false"),
        ("Behavior", "startMinimized", "true"),
        ("Behavior", "maxParseMemory", "8192"),
        ("Twitch", "twitchChannelName", "quiet_commander"),
        ("Twitch", "twitchUseTLS", "false"),
    )
    for section, key, value in updates:
        config.update(section, key, value)
    config._load_values()

    preview = export_preview(config, "mzfightreporter")
    assert "Matching preferences:" in preview
    assert "Thresholds:" in preview
    assert "Minimum fight duration (seconds): 27" in preview
    assert "UI:" in preview
    assert "Show damage: Off" in preview
    assert "Twitch:" in preview
    assert "Twitch channel: quiet_commander" in preview
    assert "twitchBotToken" not in preview

    mz_dir = tmp_path / "mz-round-trip"
    plen_dir = tmp_path / "plen-round-trip"
    insights_dir = tmp_path / "insights-round-trip"
    combiner_dir = tmp_path / "combiner-round-trip"
    export_competitor_config(config, "mzfightreporter", mz_dir)
    export_competitor_config(config, "plenbot", plen_dir)
    export_competitor_config(config, "wvw-insights", insights_dir)
    export_competitor_config(config, "gw2-ei-combiner", combiner_dir)

    mz = parse_competitor_config(mz_dir / "config.properties")
    plen = parse_competitor_config(plen_dir / "app_settings.json")
    insights = parse_competitor_config(insights_dir / "settings.json")
    combiner = parse_competitor_config(combiner_dir / "top_stats_config.ini")
    mz_settings = {
        (setting.section, setting.key): setting.value for setting in mz.settings
    }
    plen_settings = {
        (setting.section, setting.key): setting.value for setting in plen.settings
    }

    assert mz_settings[("Discord", "embedColor")] == "0xA1B2C3"
    assert mz_settings[("Thresholds", "minFightDuration")] == "27"
    assert mz_settings[("Thresholds", "maxUploadSize")] == "24"
    assert mz_settings[("UI", "showDamage")] == "false"
    assert mz_settings[("UI", "showQuickReport")] == "false"
    assert mz_settings[("Behavior", "maxParseMemory")] == "8192"
    assert mz_settings[("Twitch", "twitchChannelName")] == "quiet_commander"
    assert mz_settings[("Twitch", "twitchUseTLS")] == "false"
    assert plen_settings[("Behavior", "closeToTray")] == "true"
    assert plen_settings[("Behavior", "minimizeToTray")] == "false"
    assert insights.settings[0].value == "Reset Fight Club"
    assert combiner.settings[0].value == "Reset Fight Club"

    rendered = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            mz_dir / "config.properties",
            plen_dir / "app_settings.json",
            insights_dir / "settings.json",
            combiner_dir / "top_stats_config.ini",
        )
    )
    assert "twitchBotToken" not in rendered
    assert "api_key" not in rendered


def test_existing_target_settings_are_preserved_and_backed_up(tmp_path):
    config, _logs = configured_sparky(tmp_path)
    destination = tmp_path / "AxiBridge"
    destination.mkdir()
    target = destination / "config.json"
    target.write_text(
        json.dumps(
            {
                "githubToken": "competitor-secret-stays-in-competitor-file",
                "colorPalette": "ember",
                "logDirectory": "old",
            }
        ),
        encoding="utf-8",
    )

    result = export_competitor_config(config, "axibridge", destination)
    updated = json.loads(target.read_text(encoding="utf-8"))
    original = json.loads(result.backup_files[0].read_text(encoding="utf-8"))

    assert updated["githubToken"] == "competitor-secret-stays-in-competitor-file"
    assert updated["colorPalette"] == "ember"
    assert updated["logDirectory"] == config.log_folder
    assert original["logDirectory"] == "old"
    assert result.backup_files[0].name == "config.json.before-sparkybot"


def test_axibridge_export_keeps_unrelated_fight_and_nightly_routes(tmp_path):
    config, _logs = configured_sparky(tmp_path)
    destination = tmp_path / "AxiBridge"
    destination.mkdir()
    target = destination / "config.json"
    target.write_text(
        json.dumps(
            {
                "webhooks": [
                    {"id": "neighbor-fight", "name": "Other Guild", "url": webhook(8, "neighbor")}
                ],
                "reportWebhooks": [
                    {
                        "id": "neighbor-nightly",
                        "name": "Other Night",
                        "url": webhook(9, "neighbor-night"),
                        "enabled": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    export_competitor_config(config, "axibridge", destination)
    updated = json.loads(target.read_text(encoding="utf-8"))

    assert {item["url"] for item in updated["webhooks"]} == {
        webhook(8, "neighbor"),
        webhook(1, "fight-secret"),
    }
    assert {item["url"] for item in updated["reportWebhooks"]} == {
        webhook(9, "neighbor-night"),
        webhook(2, "nightly-secret"),
    }


def test_plenbot_and_wvw_insights_exports_append_without_erasing_routes(tmp_path):
    config, _logs = configured_sparky(tmp_path)
    plen = tmp_path / "PlenBot"
    plen.mkdir()
    (plen / "discord_webhooks.json").write_text(
        json.dumps(
            [
                {
                    "isActive": True,
                    "name": "Neighbor",
                    "url": webhook(10, "plen-neighbor"),
                    "customFilter": "keep-me",
                }
            ]
        ),
        encoding="utf-8",
    )
    insights = tmp_path / "wvw-insights"
    insights.mkdir()
    (insights / "webhooks.json").write_text(
        json.dumps(
            {
                "saved_webhooks": [
                    {"name": "Neighbor", "url": webhook(11, "insights-neighbor")}
                ]
            }
        ),
        encoding="utf-8",
    )

    export_competitor_config(config, "plenbot", plen)
    export_competitor_config(config, "wvw-insights", insights)

    plen_hooks = json.loads((plen / "discord_webhooks.json").read_text(encoding="utf-8"))
    insight_hooks = json.loads((insights / "webhooks.json").read_text(encoding="utf-8"))[
        "saved_webhooks"
    ]
    assert {item["url"] for item in plen_hooks} == {
        webhook(10, "plen-neighbor"),
        webhook(1, "fight-secret"),
        webhook(2, "nightly-secret"),
    }
    assert plen_hooks[0]["customFilter"] == "keep-me"
    assert {item["url"] for item in insight_hooks} == {
        webhook(11, "insights-neighbor"),
        webhook(1, "fight-secret"),
        webhook(2, "nightly-secret"),
    }


def test_mz_export_uses_empty_slots_but_never_replaces_a_full_config(tmp_path):
    config, _logs = configured_sparky(tmp_path)
    destination = tmp_path / "MzFightReporter"
    destination.mkdir()
    target = destination / "config.properties"
    target.write_text(
        "defaultLogFolder=old\n"
        f"discordWebhook={webhook(12, 'one')}\n"
        "discordWebhookLabel=Neighbor One\n",
        encoding="utf-8",
    )

    export_competitor_config(config, "mzfightreporter", destination)
    rendered = target.read_text(encoding="utf-8")
    assert webhook(12, "one") in rendered
    assert webhook(1, "fight-secret") in rendered
    assert webhook(2, "nightly-secret") in rendered

    target.write_text(
        "defaultLogFolder=old\n"
        f"discordWebhook={webhook(12, 'one')}\n"
        f"discordWebhook2={webhook(13, 'two')}\n"
        f"discordWebhook3={webhook(14, 'three')}\n",
        encoding="utf-8",
    )
    original = target.read_text(encoding="utf-8")

    with pytest.raises(CompetitorConfigError, match="all three Discord slots"):
        export_competitor_config(config, "mzfightreporter", destination)
    assert target.read_text(encoding="utf-8") == original


def test_evtc_export_refuses_gw2_wvw_teams_roster_config(tmp_path):
    config, _logs = configured_sparky(tmp_path)
    destination = tmp_path / "GW2-WVW-Teams"
    destination.mkdir()
    roster = destination / "config.ini"
    original = (
        "[Settings]\n"
        f"WEBHOOK_URL = {webhook(15, 'roster')}\n"
        "GUILD_ID = 123456789012345678\n"
    )
    roster.write_text(original, encoding="utf-8")

    with pytest.raises(CompetitorConfigError, match="not an existing EVTC_parser"):
        export_competitor_config(config, "evtc-parser", destination)

    assert roster.read_text(encoding="utf-8") == original


def test_evtc_export_preserves_comments_and_unrelated_ini_sections(tmp_path):
    config, _logs = configured_sparky(tmp_path)
    destination = tmp_path / "EVTC_parser"
    destination.mkdir()
    target = destination / "config.ini"
    target.write_text(
        "# neighbor comment\n"
        "[Settings]\n"
        "ARCDPS_LOG_DIR = old\n"
        "WEBHOOK_URL = old-hook\n"
        "KEEP_ME = yes\n\n"
        "[Colors]\n"
        "theme = purple\n",
        encoding="utf-8",
    )

    export_competitor_config(config, "evtc-parser", destination)
    rendered = target.read_text(encoding="utf-8")

    assert "# neighbor comment" in rendered
    assert "KEEP_ME = yes" in rendered
    assert "[Colors]\ntheme = purple" in rendered
    assert f"ARCDPS_LOG_DIR = {config.log_folder}" in rendered
    assert webhook(1, "fight-secret") in rendered


def test_combiner_export_changes_only_the_nightly_webhook(tmp_path):
    config, _logs = configured_sparky(tmp_path)
    destination = tmp_path / "GW2_EI_log_combiner"
    destination.mkdir()
    target = destination / "top_stats_config.ini"
    original_input = r"D:\generated-elite-insights-json"
    target.write_text(
        "# keep this comment\n"
        "[TopStatsCfg]\n"
        f"input_directory = {original_input}\n"
        "compress_standalone_html = false\n\n"
        "[DiscordCfg]\n"
        "webhook_url = false\n"
        "discord_additional_notes = Keep this too\n",
        encoding="utf-8",
    )

    result = export_competitor_config(config, "gw2-ei-combiner", destination)
    rendered = target.read_text(encoding="utf-8")
    finding = parse_competitor_config(target)

    assert result.target.name == "GW2 EI Log Combiner"
    assert finding.app == "TopStats / GW2 EI Log Combiner"
    assert finding.log_folders == ()
    assert finding.webhooks[0].url == webhook(2, "nightly-secret")
    assert "# keep this comment" in rendered
    assert f"input_directory = {original_input}" in rendered
    assert "compress_standalone_html = false" in rendered
    assert "discord_additional_notes = Keep this too" in rendered
    assert result.backup_files


def test_combiner_export_can_create_a_minimal_discord_handoff(tmp_path):
    config, _logs = configured_sparky(tmp_path)
    destination = tmp_path / "new-combiner"

    export_competitor_config(config, "gw2-ei-combiner", destination)

    rendered = (destination / "top_stats_config.ini").read_text(encoding="utf-8")
    assert "[DiscordCfg]" in rendered
    assert f"webhook_url = {webhook(2, 'nightly-secret')}" in rendered
    assert "input_directory" not in rendered


def test_mz_properties_preserve_unknown_lines_and_comments(tmp_path):
    config, _logs = configured_sparky(tmp_path)
    destination = tmp_path / "MzFightReporter"
    destination.mkdir()
    target = destination / "config.properties"
    target.write_text(
        "# user's comment\ncustomTheme=purple\ndefaultLogFolder=old\n",
        encoding="utf-8",
    )

    export_competitor_config(config, "mzfightreporter", destination)
    rendered = target.read_text(encoding="utf-8")

    assert "# user's comment" in rendered
    assert "customTheme=purple" in rendered
    assert f"defaultLogFolder={config.log_folder}" in rendered


def test_preview_names_routes_but_never_displays_webhook_urls(tmp_path):
    config, _logs = configured_sparky(tmp_path)

    preview = export_preview(config, "axibridge")

    assert "Logspam" in preview
    assert "Nightly Debrief & Logs" in preview
    assert "fight-secret" not in preview
    assert "nightly-secret" not in preview
    assert "discord.com" not in preview


def test_export_needs_a_log_folder_and_rejects_unknown_target(tmp_path):
    config = Config(tmp_path / "sparky.properties")

    with pytest.raises(CompetitorConfigError, match="fight-log folder"):
        export_competitor_config(config, "axibridge", tmp_path / "out")
    with pytest.raises(CompetitorConfigError, match="Unknown export target"):
        export_competitor_config(config, "made-up-tool", tmp_path / "out")


def test_target_list_leads_with_the_four_major_competitor_crowds():
    assert [target.key for target in COMPETITOR_EXPORT_TARGETS[:4]] == [
        "axibridge",
        "topstatsaio",
        "plenbot",
        "mzfightreporter",
    ]
