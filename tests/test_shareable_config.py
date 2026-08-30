import json
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import Config
import core.shareable_config as shareable_config
from core.shareable_config import (
    FORMAT_NAME,
    FORMAT_VERSION,
    GuildConfigError,
    apply_guild_config,
    bundle_from_config,
    load_guild_config,
    parse_guild_config,
    write_guild_config,
)


WEBHOOK_1 = "https://discord.com/api/webhooks/123456789012345678/secret-one"
WEBHOOK_2 = "https://ptb.discord.com/api/webhooks/223456789012345678/secret-two"


def configured(tmp_path):
    cfg = Config(tmp_path / "input.properties")
    values = {
        ("Discord", "discordWebhook"): WEBHOOK_1,
        ("Discord", "discordWebhook2"): WEBHOOK_2,
        ("Discord", "discordWebhook3"): "",
        ("Discord", "discordWebhookName1"): "Fight Reports",
        ("Discord", "discordWebhookName2"): "Raid Reports",
        ("Discord", "discordWebhookName3"): "Officers",
        ("Discord", "discordWebhookLabel"): "Guild Sparky",
        ("Discord", "activeDiscordWebhook"): "1",
        ("Discord", "raidReportDiscordWebhook"): "2",
        ("Discord", "enableDiscordBot"): "true",
        ("Discord", "embedColor"): "0x12ABEF",
        ("AI", "aiApiKey"): "SUPER-SECRET-AI-TOKEN",
        ("AI", "aiBaseUrl"): "https://private-ai.example",
        ("Twitch", "twitchBotToken"): "SUPER-SECRET-TWITCH-TOKEN",
        ("TTS", "ttsElevenLabsApiKey"): "SUPER-SECRET-VOICE-TOKEN",
        ("Paths", "logFolder"): "C:/Users/Private/Guild Wars 2/arcdps.cbtlogs",
    }
    for (section, key), value in values.items():
        cfg.update(section, key, value)
    cfg._load_values()
    return cfg


def test_export_contains_only_allowlisted_discord_data(tmp_path):
    cfg = configured(tmp_path)
    target = tmp_path / "guild.json"

    bundle = write_guild_config(cfg, target)
    text = target.read_text(encoding="utf-8")
    data = json.loads(text)

    assert bundle.configured_destination_count == 2
    assert set(data) == {"format", "version", "warning", "discord"}
    assert data["format"] == FORMAT_NAME
    assert data["version"] == FORMAT_VERSION
    assert data["discord"]["destinations"][0]["webhook_url"] == WEBHOOK_1
    assert "SUPER-SECRET-AI-TOKEN" not in text
    assert "SUPER-SECRET-TWITCH-TOKEN" not in text
    assert "SUPER-SECRET-VOICE-TOKEN" not in text
    assert "C:/Users/Private" not in text
    assert "api_key" not in text.lower()
    if sys.platform != "win32":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_round_trip_preserves_discord_and_leaves_other_secrets_untouched(tmp_path):
    source = configured(tmp_path)
    target = tmp_path / "guild.json"
    write_guild_config(source, target)

    destination = Config(tmp_path / "destination.properties")
    destination.update("AI", "aiApiKey", "KEEP-MY-AI-TOKEN")
    destination.update("Twitch", "twitchBotToken", "KEEP-MY-TWITCH-TOKEN")
    destination.update("Paths", "logFolder", "D:/keep/my/logs")

    bundle = load_guild_config(target)
    apply_guild_config(destination, bundle, persist=False)

    assert destination.discord_webhook == WEBHOOK_1
    assert destination.discord_webhook2 == WEBHOOK_2
    assert destination._config.get("Discord", "discordWebhook") == WEBHOOK_1
    assert destination._config.get("Discord", "discordWebhook2") == WEBHOOK_2
    assert destination._config.get("Discord", "discordWebhookName2") == "Raid Reports"
    assert destination._config.get("Discord", "embedColor") == "0x12ABEF"
    assert destination._config.get("AI", "aiApiKey") == "KEEP-MY-AI-TOKEN"
    assert destination._config.get("Twitch", "twitchBotToken") == "KEEP-MY-TWITCH-TOKEN"
    assert destination._config.get("Paths", "logFolder") == "D:/keep/my/logs"


def test_first_run_in_memory_import_does_not_create_partial_config(tmp_path):
    source = configured(tmp_path)
    bundle = bundle_from_config(source)
    config_path = tmp_path / "not-finished-yet.properties"
    destination = Config(config_path)

    apply_guild_config(destination, bundle, persist=False)

    assert destination.discord_webhook == WEBHOOK_1
    assert not config_path.exists()


def test_import_accepts_bom_and_id_token_shorthand(tmp_path):
    data = bundle_from_config(configured(tmp_path)).as_dict()
    data["discord"]["destinations"][0]["webhook_url"] = "123456789012345678/short-token"
    target = tmp_path / "guild.json"
    target.write_text(json.dumps(data), encoding="utf-8-sig")

    bundle = load_guild_config(target)

    assert bundle.destinations[0].webhook_url == (
        "https://discord.com/api/webhooks/123456789012345678/short-token"
    )


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.update({"ai_api_key": "steal-me"}), "top-level"),
        (lambda d: d["discord"].update({"twitch_token": "steal-me"}), "discord"),
        (lambda d: d["discord"]["destinations"][0].update({"command": "calc.exe"}), "destination 1"),
        (lambda d: d.update({"version": 999}), "version"),
        (lambda d: d["discord"]["destinations"][0].update({"webhook_url": "token-only"}), "Destination 1"),
        (lambda d: d["discord"].update({"embed_color": "red"}), "embed_color"),
    ],
)
def test_import_rejects_unknown_or_invalid_fields_without_applying(tmp_path, mutate, message):
    cfg = configured(tmp_path)
    data = bundle_from_config(cfg).as_dict()
    mutate(data)
    before = cfg._config.get("Discord", "discordWebhook")

    with pytest.raises(GuildConfigError, match=message):
        parse_guild_config(data)

    assert cfg._config.get("Discord", "discordWebhook") == before


def test_enabled_bundle_requires_a_webhook_in_the_active_slot(tmp_path):
    data = bundle_from_config(configured(tmp_path)).as_dict()
    data["discord"]["active_destination"] = 3

    with pytest.raises(GuildConfigError, match="active destination 3"):
        parse_guild_config(data)


def test_import_file_size_is_bounded(tmp_path):
    target = tmp_path / "huge.json"
    target.write_text("x" * (64 * 1024 + 1), encoding="utf-8")

    with pytest.raises(GuildConfigError, match="too large"):
        load_guild_config(target)


def test_routing_summary_uses_the_two_jobs_people_recognize(tmp_path):
    bundle = bundle_from_config(configured(tmp_path))

    assert bundle.routing_summary() == (
        "Individual fight reports → Fight Reports\n"
        "End-of-night debrief and logs → Raid Reports"
    )


def test_enabled_bundle_requires_the_nightly_destination_webhook(tmp_path):
    data = bundle_from_config(configured(tmp_path)).as_dict()
    data["discord"]["destinations"][1]["webhook_url"] = ""

    with pytest.raises(GuildConfigError, match="end-of-night destination 2"):
        parse_guild_config(data)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.update({"version": True}), "version"),
        (lambda d: d.update({"warning": {"not": "text"}}), "warning"),
        (
            lambda d: d["discord"]["destinations"][0].update(
                {"name": "\ud800"}
            ),
            "invalid text",
        ),
    ],
)
def test_strict_parser_rejects_ambiguous_or_unencodable_values(
    tmp_path, mutate, message
):
    data = bundle_from_config(configured(tmp_path)).as_dict()
    mutate(data)

    with pytest.raises(GuildConfigError, match=message):
        parse_guild_config(data)


def test_export_works_when_platform_has_no_fchmod(tmp_path, monkeypatch):
    monkeypatch.delattr(shareable_config.os, "fchmod", raising=False)
    target = tmp_path / "guild.json"

    write_guild_config(configured(tmp_path), target)

    assert target.is_file()
    assert json.loads(target.read_text(encoding="utf-8"))["format"] == FORMAT_NAME


def test_export_wraps_parent_directory_failure(tmp_path, monkeypatch):
    def deny_mkdir(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "mkdir", deny_mkdir)

    with pytest.raises(GuildConfigError, match="Could not export guild config"):
        write_guild_config(configured(tmp_path), tmp_path / "blocked" / "guild.json")


def test_failed_import_rolls_back_in_memory_values(tmp_path, monkeypatch):
    destination = configured(tmp_path)
    data = bundle_from_config(destination).as_dict()
    data["discord"]["destinations"][0]["webhook_url"] = (
        "https://discord.com/api/webhooks/999999999999999999/new-token"
    )
    bundle = parse_guild_config(data)
    before_parser = destination._config.get("Discord", "discordWebhook")
    before_attribute = destination.discord_webhook
    monkeypatch.setattr(destination, "save", lambda: False)

    with pytest.raises(GuildConfigError, match="could not save"):
        apply_guild_config(destination, bundle)

    assert destination._config.get("Discord", "discordWebhook") == before_parser
    assert destination.discord_webhook == before_attribute


def test_config_saves_back_to_the_path_it_loaded(tmp_path):
    custom_path = tmp_path / "custom.properties"
    cfg = Config(custom_path)
    cfg.update("AI", "aiApiKey", "keep-this-path")

    assert cfg.save()

    assert custom_path.is_file()
    assert "keep-this-path" in custom_path.read_text(encoding="utf-8")


def test_atomic_config_save_preserves_original_on_encoding_failure(tmp_path):
    config_path = tmp_path / "config.properties"
    cfg = Config(config_path)
    assert cfg.save()
    before = config_path.read_bytes()
    cfg.update("Discord", "discordWebhookName1", "\ud800")

    assert not cfg.save()

    assert config_path.read_bytes() == before
