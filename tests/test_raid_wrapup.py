"""Tests for core/raid_wrapup.py — Raid Wrap-Up embed builder."""

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core.raid_wrapup import (
    build_wrapup,
    CategoryLeader,
    _format_number,
    _parse_table_rows,
)

# ---------------------------------------------------------------------------
# Test fixtures — extend poison_tab fixtures with wrap-up tables
# ---------------------------------------------------------------------------

TAG = "2099-01-01-00:00:00"

_BASE_TIDDLERS = [
    {
        "title": f"{TAG}-Menu",
        "tags": TAG,
        "text": f'<<tabs "[[{TAG}-Overview]]" "Overview">>',
    },
    {
        "title": "BubbleChart_Template",
        "text": "template-placeholder",
    },
]

_OFFENSIVE = {
    "title": f"{TAG}-Offensive",
    "tags": TAG,
    "text": (
        "<div>\n"
        "|thead-dark sortable|k\n"
        "|!Party |!Name | !Prof |! Damage |! Power DPS |! Condi DPS |h\n"
        "| 1 |<span data-tooltip=\"Alpha.1111\">Alpha</span> | "
        "[img [Scourge|x.png]] Sco | 2450000| 15000| 8000|\n"
        "| 1 |<span data-tooltip=\"Bravo.2222\">Bravo</span> | "
        "[img [Reaper|x.png]] Rea | 1800000| 12000| 5000|\n"
    ),
}

_DEFENSES = {
    "title": f"{TAG}-Defenses",
    "tags": TAG,
    "text": (
        "<div>\n"
        "|thead-dark sortable|k\n"
        "|!Party |!Name | !Prof |! Barrier |! Dodge |h\n"
        "| 1 |<span data-tooltip=\"Gamma.3333\">Gamma</span> | "
        "[img [Firebrand|x.png]] Fir | 450000| 25|\n"
        "| 1 |<span data-tooltip=\"Delta.4444\">Delta</span> | "
        "[img [Herald|x.png]] Her | 320000| 18|\n"
    ),
}

_SUPPORT = {
    "title": f"{TAG}-Support",
    "tags": TAG,
    "text": (
        "<div>\n"
        "|thead-dark sortable|k\n"
        "|!Party |!Name | !Prof |! Healing |! Condi Cleanses |! Boon Strips |h\n"
        "| 1 |<span data-tooltip=\"Healer.5555\">Healy</span> | "
        "[img [Druid|x.png]] Dru | 1800000| 85| 12|\n"
        "| 1 |<span data-tooltip=\"Stripper.6666\">Strip</span> | "
        "[img [Spellbreaker|x.png]] Spe | 900000| 40| 35|\n"
    ),
}

_CONDITIONS_OUT = {
    "title": f"{TAG}-Conditions-Out",
    "tags": TAG,
    "text": (
        "<div>\n"
        "|thead-dark sortable|k\n"
        "|!Party |!Name | !Prof | !{{FightTime}} |! {Bleeding} "
        "|! {Poison} |! {Torment} |h\n"
        "| 1 |<span data-tooltip=\"Poison.7777\">Poisy</span> | "
        "[img [Scourge|x.png]] Sco | 60.0| 10| 120| 5|\n"
        "| 1 |<span data-tooltip=\"Bee.8888\">Bee</span> | "
        "[img [Reaper|x.png]] Rea | 60.0| 0| 90| 0|\n"
    ),
}

_TOTAL_COND_OUT = {
    "title": f"{TAG}-Total-Condition-Output-Generation",
    "tags": TAG,
    "text": (
        "<$echarts $text=```\n"
        "option = {\n"
        "  dataset: [\n"
        "    {\n"
        "\t\tsource: [['Player', 'Poison', 'Profession'], "
        "['{{Scourge}} - Poisy', 0.5, 'Scourge'], "
        "['{{Reaper}} - Bee', 0.3, 'Reaper']]\n"
        "    }\n"
        "  ]\n"
        "};\n"
        "```$height=\"400px\"/>\n"
    ),
}


def _full_fixture():
    return [
        *_BASE_TIDDLERS,
        _OFFENSIVE,
        _DEFENSES,
        _SUPPORT,
        _CONDITIONS_OUT,
        _TOTAL_COND_OUT,
    ]


def _no_data_fixture():
    return _BASE_TIDDLERS


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestFormatNumber:
    def test_millions(self):
        assert _format_number(1_240_000) == "1.24M"
        assert _format_number(2_000_000) == "2M"
        assert _format_number(1_500_000) == "1.5M"

    def test_thousands(self):
        assert _format_number(96_500) == "96.5K"
        assert _format_number(45_000) == "45K"
        assert _format_number(1_200) == "1.2K"

    def test_small(self):
        assert _format_number(42) == "42"
        assert _format_number(0) == "0"


class TestParseTableRows:
    def test_parses_offensive(self):
        rows = _parse_table_rows(_OFFENSIVE["text"])
        assert len(rows) == 2
        assert rows[0]["account"] == "Alpha.1111"
        assert rows[0]["name"] == "Alpha"
        assert rows[0]["prof"] == "Scourge"
        assert rows[0]["Damage"] == 2_450_000

    def test_parses_support(self):
        rows = _parse_table_rows(_SUPPORT["text"])
        assert len(rows) == 2
        heal_row = max(rows, key=lambda r: r.get("Healing", 0))
        assert heal_row["name"] == "Healy"
        assert heal_row["Healing"] == 1_800_000
        assert heal_row["Condi Cleanses"] == 85

    def test_empty_on_no_header(self):
        rows = _parse_table_rows("just some text\nno table here")
        assert rows == []


class TestBuildWrapup:
    def test_all_categories_found(self):
        result = build_wrapup(_full_fixture())
        leaders = result["leaders"]
        categories = {l.category for l in leaders}
        assert "Damage" in categories
        assert "Healing" in categories
        assert "Barrier" in categories
        assert "Cleanses" in categories
        assert "Strips" in categories
        assert "Poison Coverage" in categories

    def test_top_damage_correct(self):
        result = build_wrapup(_full_fixture())
        dmg = next(l for l in result["leaders"] if l.category == "Damage")
        assert dmg.name == "Alpha"
        assert dmg.profession == "Scourge"
        assert dmg.display == "2.45M"
        assert dmg.value == 2_450_000

    def test_top_healing_correct(self):
        result = build_wrapup(_full_fixture())
        heal = next(l for l in result["leaders"] if l.category == "Healing")
        assert heal.name == "Healy"
        assert heal.display == "1.8M"

    def test_top_cleanses_correct(self):
        result = build_wrapup(_full_fixture())
        clean = next(l for l in result["leaders"] if l.category == "Cleanses")
        assert clean.name == "Healy"
        assert clean.value == 85
        assert clean.display == "85"

    def test_top_strips_correct(self):
        result = build_wrapup(_full_fixture())
        strip = next(l for l in result["leaders"] if l.category == "Strips")
        assert strip.name == "Strip"
        assert strip.value == 35

    def test_poison_coverage(self):
        result = build_wrapup(_full_fixture())
        poison = next(l for l in result["leaders"] if l.category == "Poison Coverage")
        assert poison.name == "Poisy"
        assert poison.value == 120

    def test_missing_categories_skipped(self):
        result = build_wrapup(_no_data_fixture())
        assert result["leaders"] == []
        assert result["text"] == "No data available."

    def test_embed_structure(self):
        result = build_wrapup(_full_fixture())
        embed = result["embed"]
        assert embed["title"] is None
        assert embed["color"] == 0x4CAF50
        assert embed["footer"]["text"] == "SparkyBot Raid Report"
        assert len(embed["fields"]) == len(result["leaders"])
        for field in embed["fields"]:
            assert "name" in field
            assert "value" in field
            assert field["inline"] is True

    def test_text_fallback(self):
        result = build_wrapup(_full_fixture())
        text = result["text"]
        assert "Alpha" in text
        assert "Healy" in text
        assert "Poisy" in text
        assert "2.45M" in text
        assert "1.8M" in text

    def test_embed_title_injectable(self):
        result = build_wrapup(_full_fixture())
        embed = result["embed"]
        embed["title"] = "Test Raid Report"
        assert embed["title"] == "Test Raid Report"
