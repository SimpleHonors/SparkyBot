import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from core.raid_report import ReportResult, make_publish_embed
from core.raid_report_wiring import publish_result


FIXTURES = Path(__file__).parent / "fixtures"


def test_nightly_discord_embed_is_brief_but_answers_the_run_basics(tmp_path):
    source = FIXTURES / "aug10_summary_poison.json"
    json_path = tmp_path / "night.json"
    json_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    result = ReportResult(
        name="Raid Report 2026-08-10 (8 fights)",
        html_path=tmp_path / "night.html",
        json_path=json_path,
        fight_count=8,
        span="19:07\u201320:28",
        generated_date=date(2026, 8, 10),
    )

    embed = make_publish_embed(result)
    rendered = json.dumps(embed)

    assert embed["title"] == "\U0001f4ca Raid Report 2026-08-10"
    assert "Mohr Shadows" in rendered
    assert "8" in rendered and "fights" in rendered
    assert "320" in rendered and "417" in rendered
    assert "8.21" in rendered
    assert "39" in rendered and "94" in rendered
    assert "7:07 PM" in rendered and "8:28 PM" in rendered
    assert "1h 20m elapsed" in rendered
    assert "31m in combat" in rendered
    assert len(embed["fields"]) == 3


def test_missing_nightly_json_still_produces_an_attachment_summary(tmp_path):
    result = ReportResult(
        name="Wolf Wednesday (3 fights)",
        html_path=tmp_path / "night.html",
        json_path=tmp_path / "missing.json",
        fight_count=3,
        span="",
    )

    embed = make_publish_embed(result)

    assert embed["title"] == "\U0001f4ca Wolf Wednesday"
    assert embed["fields"] == [
        {"name": "Run", "value": "**3** fights", "inline": True}
    ]


def test_real_publish_path_sends_summary_before_separate_attachment(
    tmp_path, monkeypatch
):
    json_path = tmp_path / "night.json"
    json_path.write_text(
        (FIXTURES / "aug10_summary_poison.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    html_path = tmp_path / "night.html"
    html_path.write_text("<html>night</html>", encoding="utf-8")
    result = ReportResult(
        name="Raid Report 2026-08-10 (8 fights)",
        html_path=html_path,
        json_path=json_path,
        fight_count=8,
        span="",
    )
    sent = {}
    events = []

    def send_message(content="", embeds=None):
        events.append(("summary", content, embeds))
        return True

    def send_file(*_args, **_kwargs):
        return True

    bot = SimpleNamespace(send_message=send_message, send_file=send_file)

    class Manager:
        def __init__(self, _config):
            pass

        def get_webhook(self, _destination):
            return bot

    def capture(path, **kwargs):
        events.append(("report", path, kwargs))
        sent["path"] = path
        sent.update(kwargs)

    monkeypatch.setattr("core.discord_bot.DiscordWebhookManager", Manager)
    monkeypatch.setattr("core.report_publisher.publish_report", capture)
    config = SimpleNamespace(
        get_raid_report_discord_webhook_index=lambda: 2,
        raidreport_always_zip=False,
        embed_color=0x123456,
    )

    publish_result(config, result)

    assert [event[0] for event in events] == ["summary", "report"]
    summary_embed = events[0][2][0]
    assert summary_embed["description"] == "Commanded by **Mohr Shadows**"
    assert summary_embed["color"] == 0x123456
    assert sent["path"] == html_path
    assert sent["send_file"] is bot.send_file
    assert sent["embed"] is None
    assert sent["caption"] == ""
