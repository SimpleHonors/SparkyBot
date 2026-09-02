from types import SimpleNamespace

import core.discord_bot as discord_bot


def _embed_chars(embed):
    total = len(embed.get("description", "")) + len(embed.get("title", ""))
    total += len(embed.get("author", {}).get("name", ""))
    total += len(embed.get("footer", {}).get("text", ""))
    for field in embed.get("fields", []):
        total += len(field.get("name", "")) + len(field.get("value", ""))
    return total


def test_fight_report_can_be_condensed_to_one_discord_message(monkeypatch):
    sent = []

    class FakeDiscordBot:
        def __init__(self, webhook_url):
            self.webhook_url = webhook_url

        def send_message(self, content="", embeds=None, icon_path=None):
            sent.append((content, embeds, icon_path))
            return True

    monkeypatch.setattr(discord_bot, "DiscordBot", FakeDiscordBot)
    monkeypatch.setattr(discord_bot.time, "sleep", lambda _seconds: None)

    config = SimpleNamespace(
        active_discord_webhook=1,
        discord_webhook="https://discord.com/api/webhooks/123/token",
        discord_webhook2="",
        discord_webhook3="",
    )
    manager = discord_bot.DiscordWebhookManager(config)
    embeds = [
        {
            "title": "Full Report",
            "fields": [
                {
                    "name": f"Section {index}",
                    "value": "```\n" + "\n".join(
                        f"{row}. detailed player statistic" for row in range(30)
                    ) + "\n```",
                }
            ],
        }
        for index in range(8)
    ]
    assert sum(_embed_chars(embed) for embed in embeds) > discord_bot.MAX_TOTAL_CHARS

    result = manager.send_to_all(
        embeds=embeds,
        compact_single_message=True,
    )

    assert result == 1
    assert len(sent) == 1
    posted_embeds = sent[0][1]
    assert sum(_embed_chars(embed) for embed in posted_embeds) <= discord_bot.MAX_TOTAL_CHARS
    assert [field["name"] for embed in posted_embeds for field in embed["fields"]] == [
        f"Section {index}" for index in range(8)
    ]
    assert any("condensed" in field["value"] for embed in posted_embeds for field in embed["fields"])
