"""Test DiscordBot.send_file embed support — mocked requests."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))


class TestDiscordSendFileEmbed:
    def test_no_embed_uses_content_data(self, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        from core.discord_bot import DiscordBot
        bot = DiscordBot("http://fake-webhook")

        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            bot.send_file(test_file, caption="just a file")

        _, kwargs = mock_post.call_args
        assert kwargs["data"] == {"content": "just a file"}
        assert "payload_json" not in kwargs["data"]

    def test_embed_adds_payload_json(self, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        embed = {"title": "Test Report", "color": 0x4CAF50}

        from core.discord_bot import DiscordBot
        bot = DiscordBot("http://fake-webhook")

        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            bot.send_file(test_file, caption="report", embed=embed)

        _, kwargs = mock_post.call_args
        assert "payload_json" in kwargs["data"]

        import json
        payload = json.loads(kwargs["data"]["payload_json"])
        assert payload["content"] == "report"
        assert payload["embeds"] == [embed]

    def test_embed_payload_json_not_present_when_none(self, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        from core.discord_bot import DiscordBot
        bot = DiscordBot("http://fake-webhook")

        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            bot.send_file(test_file, caption="no embed", embed=None)

        _, kwargs = mock_post.call_args
        assert "payload_json" not in kwargs["data"]
        assert kwargs["data"] == {"content": "no embed"}
