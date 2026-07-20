"""Named Discord destinations and independent Raid Report routing."""

from core.config import Config


def test_existing_config_uses_active_fight_destination_for_raid_reports(
        tmp_path):
    path = tmp_path / "config.properties"
    cfg = Config(path)
    cfg.update("Discord", "activeDiscordWebhook", "2")
    cfg.save(path)

    loaded = Config(path)
    assert loaded.active_discord_webhook == 2
    assert loaded.raid_report_discord_webhook == 0
    assert loaded.get_raid_report_discord_webhook_index() == 2


def test_raid_reports_can_use_a_separate_named_destination(tmp_path):
    path = tmp_path / "config.properties"
    cfg = Config(path)
    cfg.update("Discord", "activeDiscordWebhook", "1")
    cfg.update("Discord", "raidReportDiscordWebhook", "3")
    cfg.update("Discord", "discordWebhookName1", "Main WvW")
    cfg.update("Discord", "discordWebhookName3", "Raid Reports")
    cfg.save(path)

    loaded = Config(path)
    assert loaded.get_discord_destination_name(1) == "Main WvW"
    assert loaded.get_discord_destination_name(2) == "Destination 2"
    assert loaded.get_discord_destination_name(3) == "Raid Reports"
    assert loaded.get_raid_report_discord_webhook_index() == 3
