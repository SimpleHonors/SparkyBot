"""Tests for voice wrap-up: zingers, recap script, multi-attach, toggles."""

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core.raid_wrapup import (
    CategoryLeader,
    build_zinger_prompt,
    apply_zingers_to_embed,
    compose_recap_script,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _sample_leaders():
    return [
        CategoryLeader("Damage", "\U0001F5E1\uFE0F", "Alpha", "Scourge",
                       2450000, "2.45M"),
        CategoryLeader("Healing", "\U0001F49A", "Healy", "Druid",
                       1800000, "1.8M"),
        CategoryLeader("Cleanses", "\u2728", "Healy", "Druid", 85, "85"),
    ]


def _sample_embed():
    return {
        "title": "Test Report",
        "fields": [
            {"name": "\U0001F5E1\uFE0F Damage", "value": "**Alpha** (Scourge) — 2.45M",
             "inline": True},
            {"name": "\U0001F49A Healing", "value": "**Healy** (Druid) — 1.8M",
             "inline": True},
            {"name": "\u2728 Cleanses", "value": "**Healy** (Druid) — 85",
             "inline": True},
        ],
        "footer": {"text": "SparkyBot Raid Report"},
        "color": 0x4CAF50,
    }


# ---------------------------------------------------------------------------
# Zinger prompt building
# ---------------------------------------------------------------------------

class TestBuildZingerPrompt:
    def test_builds_lines_from_leaders(self):
        prompt = build_zinger_prompt(_sample_leaders())
        lines = prompt.split("\n")
        assert len(lines) == 3
        assert "Damage: Alpha (Scourge) 2.45M" in lines[0]
        assert "Healing: Healy (Druid) 1.8M" in lines[1]
        assert "Cleanses: Healy (Druid) 85" in lines[2]


# ---------------------------------------------------------------------------
# Zinger application to embed
# ---------------------------------------------------------------------------

class TestApplyZingersToEmbed:
    def test_appends_zingers_to_fields(self):
        embed = _sample_embed()
        zingers = [
            "Alpha melted faces and took names.",
            "Healy was everywhere at once.",
            "85 cleanses. Healy doesn't believe in conditions.",
        ]
        result = apply_zingers_to_embed(embed, zingers)

        fields = result["fields"]
        assert "*Alpha melted faces and took names.*" in fields[0]["value"]
        assert "*Healy was everywhere at once.*" in fields[1]["value"]
        assert "*85 cleanses." in fields[2]["value"]

    def test_fewer_zingers_than_fields(self):
        embed = _sample_embed()
        result = apply_zingers_to_embed(embed, ["Only one."])
        assert "*Only one.*" in result["fields"][0]["value"]
        orig = _sample_embed()["fields"][1]["value"]
        assert result["fields"][1]["value"] == orig

    def test_no_zingers_does_not_modify(self):
        embed = _sample_embed()
        result = apply_zingers_to_embed(embed, [])
        assert result["fields"] == embed["fields"]


# ---------------------------------------------------------------------------
# Recap script composition
# ---------------------------------------------------------------------------

class TestComposeRecapScript:
    def test_contains_all_leaders(self):
        leaders = _sample_leaders()
        script = compose_recap_script("Raid Report 2026-07-18", leaders)

        assert "That's a wrap on Raid Report 2026-07-18." in script
        assert "Damage: Alpha" in script
        assert "Healing: Healy" in script
        assert "Cleanses: Healy" in script
        assert "SparkyBot out. See you next raid." in script

    def test_zingers_injected_into_script(self):
        leaders = _sample_leaders()
        zingers = ["hot fire", "big heals", "clean machine"]
        script = compose_recap_script("Raid", leaders, zingers)

        assert "Damage: Alpha. hot fire" in script
        assert "Healing: Healy. big heals" in script
        assert "Cleanses: Healy. clean machine" in script

    def test_fewer_zingers_than_leaders(self):
        leaders = _sample_leaders()
        script = compose_recap_script("Raid", leaders, ["just one"])

        assert "Damage: Alpha. just one" in script
        # Healing line should not have an extra ". " zinger appended
        lines = script.split("\n")
        heal_line = [l for l in lines if "Healing: Healy" in l][0]
        assert heal_line.strip() == "Healing: Healy"

    def test_missing_zingers_falls_back(self):
        leaders = _sample_leaders()
        script = compose_recap_script("Raid", leaders, None)
        # Each category line should NOT have a zinger appended
        lines = script.split("\n")
        dmg_line = [l for l in lines if "Damage: Alpha" in l][0]
        assert dmg_line.strip() == "Damage: Alpha"


# ---------------------------------------------------------------------------
# send_file multi-attach (mocked requests)
# ---------------------------------------------------------------------------

class TestSendFileMultiAttach:
    def test_extra_files_in_multipart(self, tmp_path):
        main = tmp_path / "report.html"
        main.write_text("main content")
        extra = tmp_path / "recap.mp3"
        extra.write_bytes(b"fake mp3 data")

        from core.discord_bot import DiscordBot
        bot = DiscordBot("http://fake")

        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            bot.send_file(main, caption="test", extra_files=[extra])

        _, kwargs = mock_post.call_args
        files = kwargs["files"]
        assert "file" in files
        assert "file1" in files
        assert files["file"][0] == "report.html"
        assert files["file1"][0] == "recap.mp3"

    def test_no_extra_files_unchanged(self, tmp_path):
        main = tmp_path / "report.html"
        main.write_text("main content")

        from core.discord_bot import DiscordBot
        bot = DiscordBot("http://fake")

        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            bot.send_file(main, caption="test")

        _, kwargs = mock_post.call_args
        files = kwargs["files"]
        assert "file" in files
        assert "file1" not in files


# ---------------------------------------------------------------------------
# _fetch_zingers response parsing
# ---------------------------------------------------------------------------

class TestFetchZingersParsing:
    def _patch_api(self, response_json, status=200):
        mock_resp = type("Response", (), {
            "status_code": status,
            "json": lambda self: response_json,
        })()
        return patch("core.raid_report_wiring._requests.post",
                     return_value=mock_resp)

    def test_parses_json_list(self):
        api_resp = {
            "choices": [{"message": {"content": '["A roast.", "A hype."]'}}],
        }
        with self._patch_api(api_resp):
            from core.raid_report_wiring import _fetch_zingers
            result = _fetch_zingers(_fake_config_ai(), _sample_leaders())
        assert result == ["A roast.", "A hype."]

    def test_strips_markdown_fences(self):
        api_resp = {
            "choices": [{"message": {"content": '```json\n["one", "two"]\n```'}}],
        }
        with self._patch_api(api_resp):
            from core.raid_report_wiring import _fetch_zingers
            result = _fetch_zingers(_fake_config_ai(), _sample_leaders())
        assert result == ["one", "two"]

    def test_non_list_json_returns_none(self):
        api_resp = {
            "choices": [{"message": {"content": '{"not": "a list"}'}}],
        }
        with self._patch_api(api_resp):
            from core.raid_report_wiring import _fetch_zingers
            result = _fetch_zingers(_fake_config_ai(), _sample_leaders())
        assert result is None

    def test_api_error_returns_none(self):
        with self._patch_api({}, status=500):
            from core.raid_report_wiring import _fetch_zingers
            result = _fetch_zingers(_fake_config_ai(), _sample_leaders())
        assert result is None

    def test_timeout_returns_none(self):
        import requests

        def slow_post(*args, **kwargs):
            raise requests.Timeout("too slow")

        with patch("core.raid_report_wiring._requests.post",
                   side_effect=slow_post):
            from core.raid_report_wiring import _fetch_zingers
            result = _fetch_zingers(
                _fake_config_ai(), _sample_leaders(), timeout=1
            )
        assert result is None

    def test_no_ai_config_returns_none(self):
        from core.raid_report_wiring import _fetch_zingers
        cfg = _fake_config_ai()
        cfg.ai_base_url = ""
        result = _fetch_zingers(cfg, _sample_leaders())
        assert result is None


# ---------------------------------------------------------------------------
# Helper config mock
# ---------------------------------------------------------------------------

def _fake_config_ai():
    class Cfg:
        ai_base_url = "http://fake-ai/v1"
        ai_api_key = "sk-fake"
        ai_model = "fake-model"
    return Cfg()
