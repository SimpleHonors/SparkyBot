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
    destination = tmp_path / "AxiBridge"

    export_competitor_config(config, "axibridge", destination)
    finding = parse_competitor_config(destination / "config.json")

    assert any(hook.display_name == "Logspam" and hook.role == "fight" for hook in finding.webhooks)
    assert any(
        hook.display_name == "Nightly Debrief & Logs" and hook.role == "nightly"
        for hook in finding.webhooks
    )


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
