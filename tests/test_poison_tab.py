"""Tests for core/poison_tab.py — poison coverage layer."""

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core.poison_tab import (
    find_session_tag,
    parse_applications,
    parse_poison_output,
    build_model,
    build_chart_tiddler,
    build_tab_tiddler,
    splice_menu,
    augment,
    augment_file,
    CLASS_COLORS,
    class_color,
)
from core.raid_report import (
    RaidReportRunner,
    RaidReportCancelled,
)
from core.raid_session import LogInfo

# ---------------------------------------------------------------------------
# Minimal fixture — mirrors topstats-poison-tab's topstats_mini.json
# ---------------------------------------------------------------------------

TAG = "2099-01-01-00:00:00"


def _fixture_tiddlers():
    return [
        {
            "title": f"{TAG}-Menu",
            "tags": TAG,
            "text": (
                '<<tabs "'
                f'[[{TAG}-Overview]] [[{TAG}-Damage]]"'
                ' "Overview">>'
            ),
        },
        {
            "title": f"{TAG}-Conditions-Out",
            "tags": TAG,
            "text": (
                "<div>\n"
                "|thead-dark sortable|k\n"
                "|!Party |!Name | !Prof | !{{FightTime}} |! {Bleeding} "
                "|! {Poison} |! {Torment} |h\n"
                "|Total Generated |<|<|<| 100| 300| 50|h\n"
                "| 1 |<span data-tooltip=\"Aay.1111\">Aay</span> | "
                "[img [Scourge|x.png]] Sco | 60.0| 10| 120| 5|\n"
                "| 1 |<span data-tooltip=\"Bee.2222\">Bee</span> | "
                "[img [Reaper|x.png]] Rea | 60.0| 0| 90| 0|\n"
                "| 1 |<span data-tooltip=\"Cee.3333\">Cee</span> | "
                "[img [Tempest|x.png]] Tem | 60.0| 4| 30| 1|\n"
            ),
        },
        {
            "title": f"{TAG}-Total-Condition-Output-Generation",
            "tags": TAG,
            "text": (
                "<$echarts $text=```\n"
                "option = {\n"
                "  dataset: [\n"
                "    {\n"
                "\t\tsource: [['Player', 'Bleeding', 'Poison', 'Profession'], "
                "['{{Scourge}} - Aay', 0.0, 0.5, 'Scourge'], "
                "['{{Reaper}} - Bee', 0.0, 0.3, 'Reaper'], "
                "['{{Tempest}} - Cee', 0.1, 0.1, 'Tempest']]\n"
                "    }\n"
                "  ]\n"
                "};\n"
                "```$height=\"400px\"/>\n"
            ),
        },
        {"title": "BubbleChart_Template", "text": "template-placeholder"},
    ]


def _fixture_no_poison():
    """A guild with zero poison applications (Conditions-Out exists but 0 poison hits)."""
    return [
        {
            "title": f"{TAG}-Menu",
            "tags": TAG,
            "text": f'<<tabs "[[{TAG}-Overview]]" "Overview">>',
        },
        {
            "title": f"{TAG}-Conditions-Out",
            "tags": TAG,
            "text": (
                "<div>\n"
                "|thead-dark sortable|k\n"
                "|!Party |!Name | !Prof | !{{FightTime}} |! {Poison} |h\n"
                "| 1 |<span data-tooltip=\"Nope.1234\">Nope</span> | "
                "[img [Warrior|x.png]] War | 60.0| 0|\n"
            ),
        },
        {"title": "BubbleChart_Template", "text": "template-placeholder"},
    ]


def _fixture_no_conditions_out():
    return [
        {
            "title": f"{TAG}-Menu",
            "tags": TAG,
            "text": f'<<tabs "[[{TAG}-Overview]]" "Overview">>',
        },
        {"title": "BubbleChart_Template", "text": "template-placeholder"},
    ]


# ---------------------------------------------------------------------------
# Core function tests
# ---------------------------------------------------------------------------

class TestFindSessionTag:
    def test_finds_tag(self):
        assert find_session_tag(_fixture_tiddlers()) == TAG

    def test_raises_when_missing(self):
        with pytest.raises(ValueError, match="no session tag"):
            find_session_tag([])


class TestParseApplications:
    def test_extracts_appliers(self):
        apps = parse_applications(_fixture_tiddlers())
        assert set(apps) == {"Aay.1111", "Bee.2222", "Cee.3333"}
        assert apps["Aay.1111"].apps == 120
        assert apps["Aay.1111"].prof == "Scourge"
        assert apps["Aay.1111"].fight_time == 60.0
        assert round(apps["Aay.1111"].apps_per_min, 1) == 120.0
        assert apps["Bee.2222"].apps == 90

    def test_excludes_zero_poison(self):
        apps = parse_applications(_fixture_tiddlers())
        assert all(r.apps > 0 for r in apps.values())

    def test_empty_when_no_conditions_out(self):
        assert parse_applications([]) == {}

    def test_comma_formatted_fight_time_and_apps(self):
        """Real 34-fight nights format FightTime as "3,038.4" — the comma
        must not zero the fight time (which zeroed Apps/min for 23/35
        players on the 2026-07-18 report)."""
        tiddlers = [
            {
                "title": f"{TAG}-Conditions-Out",
                "tags": TAG,
                "text": (
                    "<div>\n"
                    "|thead-dark sortable|k\n"
                    "|!Party |!Name | !Prof | !{{FightTime}} |! {Poison} |h\n"
                    "| 1 |<span data-tooltip=\"Dee.4444\">Dee</span> | "
                    "[img [Scourge|x.png]] Sco | 2,735.2| 2,119|\n"
                ),
            },
        ]
        apps = parse_applications(tiddlers)
        row = apps["Dee.4444"]
        assert row.apps == 2119
        assert row.fight_time == 2735.2
        assert round(row.apps_per_min, 1) == round(2119 / (2735.2 / 60), 1)


class TestParsePoisonOutput:
    def test_extracts_output(self):
        out = parse_poison_output(_fixture_tiddlers())
        assert out["Aay"] == 0.5
        assert out["Bee"] == 0.3
        assert out["Cee"] == 0.1

    def test_empty_when_missing(self):
        assert parse_poison_output([]) == {}


class TestBuildModel:
    def test_merges_and_sorts_by_hits(self):
        rows = build_model(_fixture_tiddlers())
        assert [r.account for r in rows] == ["Aay.1111", "Bee.2222", "Cee.3333"]
        aay = next(r for r in rows if r.account == "Aay.1111")
        assert aay.output == 0.5
        assert aay.has_output
        assert aay.apps == 120


class TestColors:
    def test_known_class(self):
        assert class_color("Scourge").startswith("#")

    def test_default_for_unknown(self):
        assert class_color("TotallyMadeUpClass") == CLASS_COLORS["_default"]


class TestBuildChartTiddler:
    def test_structure(self):
        rows = build_model(_fixture_tiddlers())
        ch = build_chart_tiddler("TAG", rows)
        assert ch["title"] == "TAG-SparkyBot-Poison-Chart"
        assert ch["data"].count("[") == 4  # header + 3 rows
        for nm in ("Aay", "Bee", "Cee"):
            assert nm in ch["data"]
        assert ch["xAxis"] == "Poison Hits"
        assert ch["yAxis"] == "Poison Output/sec"


class TestBuildTabTiddler:
    def test_structure(self):
        rows = build_model(_fixture_tiddlers())
        tab = build_tab_tiddler("TAG", rows)
        assert tab["title"] == "TAG-SparkyBot"
        assert tab["caption"] == "Poison Coverage"
        assert "TAG-SparkyBot-Poison-Chart||BubbleChart_Template" in tab["text"]
        for nm in ("Aay", "Bee", "Cee"):
            assert nm in tab["text"]


class TestSpliceMenu:
    def test_inserts_entry(self):
        d = _fixture_tiddlers()
        assert splice_menu(d, TAG) is True
        menu = next(t for t in d if t["title"].endswith("-Menu"))
        assert f"[[{TAG}-SparkyBot]]" in menu["text"]

    def test_idempotent(self):
        d = _fixture_tiddlers()
        assert splice_menu(d, TAG) is True
        menu = next(t for t in d if t["title"].endswith("-Menu"))
        count_before = menu["text"].count(f"[[{TAG}-SparkyBot]]")
        assert splice_menu(d, TAG) is False
        assert menu["text"].count(f"[[{TAG}-SparkyBot]]") == count_before


class TestAugment:
    def test_adds_two_tiddlers_and_menu(self):
        out = augment(_fixture_tiddlers())
        titles = [t["title"] for t in out]
        assert f"{TAG}-SparkyBot" in titles
        assert f"{TAG}-SparkyBot-Poison-Chart" in titles
        menu = next(t for t in out if t["title"].endswith("-Menu"))
        assert f"[[{TAG}-SparkyBot]]" in menu["text"]

    def test_idempotent(self):
        out = augment(_fixture_tiddlers())
        out2 = augment(out)
        assert [t["title"] for t in out2].count(f"{TAG}-SparkyBot") == 1
        assert [t["title"] for t in out2].count(f"{TAG}-SparkyBot-Poison-Chart") == 1


class TestAugmentFile:
    def test_modifies_json_in_place(self, tmp_path):
        src = tmp_path / "report.json"
        src.write_text(json.dumps(_fixture_tiddlers()), encoding="utf-8")

        result = augment_file(src)
        assert not result["skipped"]
        assert result["appliers"] == 3
        assert result["with_output"] == 3

        data = json.loads(src.read_text(encoding="utf-8"))
        titles = [t["title"] for t in data]
        assert f"{TAG}-SparkyBot" in titles

    def test_no_poison_skips(self, tmp_path):
        src = tmp_path / "report.json"
        original = _fixture_no_poison()
        src.write_text(json.dumps(original), encoding="utf-8")
        original_text = src.read_text(encoding="utf-8")

        result = augment_file(src)
        assert result["skipped"] is True
        assert result["appliers"] == 0
        assert src.read_text(encoding="utf-8") == original_text

    def test_missing_conditions_out_skips(self, tmp_path):
        src = tmp_path / "report.json"
        original = _fixture_no_conditions_out()
        src.write_text(json.dumps(original), encoding="utf-8")
        original_text = src.read_text(encoding="utf-8")

        result = augment_file(src)
        assert result["skipped"] is True
        assert src.read_text(encoding="utf-8") == original_text

    def test_bad_json_skips(self, tmp_path):
        src = tmp_path / "report.json"
        src.write_text("not valid json", encoding="utf-8")

        result = augment_file(src)
        assert result["skipped"] is True


# ---------------------------------------------------------------------------
# Runner integration tests
# ---------------------------------------------------------------------------

class FakeCombiner:
    def __init__(self, dragdrop_json):
        self._output = dragdrop_json
        self.ensure_calls = 0
        self.run_calls = []

    def ensure_installed(self, progress_callback=None):
        self.ensure_calls += 1

    def write_run_config(self, run_dir, input_dir,
                         guild_name="", guild_id="", api_key=""):
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir / "top_stats_config.ini"

    def run(self, input_dir, run_dir, timeout=900, progress_callback=None):
        self.run_calls.append((input_dir, run_dir))
        return self._output


class FakeRaidReportCache:
    def __init__(self):
        self._store = {}

    def lookup(self, log_path, ei_version, fingerprint):
        return self._store.get(log_path)

    def store(self, log_path, json_path, ei_version, fingerprint):
        self._store[log_path] = json_path
        return json_path

    def preload(self, log_path, json_path):
        self._store[log_path] = json_path


def _make_viewer(tmp_path):
    p = tmp_path / "viewer.html"
    p.write_text(
        '<html><head><title>Viewer</title></head><body>\n'
        '<script class="tiddlywiki-tiddler-store" type="application/json">'
        '[{"title":"$:/boot","text":"hi"}]'
        '</script>\n'
        '</body></html>',
        encoding="utf-8",
    )
    return p


class TestRunnerAugmentIntegration:
    def test_augment_json_called_between_combine_and_bake(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        log_a = log_dir / "20260718-200001.zevtc"
        log_a.write_text("")

        info = LogInfo(path=log_a, timestamp=datetime(2026, 7, 18, 20, 0, 1),
                       source="filename")

        cached = tmp_path / "cached.json"
        cached.write_text("{}")

        cache = FakeRaidReportCache()
        cache.preload(log_a, cached)

        dragdrop = tmp_path / "dragdrop.json"
        dragdrop.write_text(json.dumps(_fixture_tiddlers()), encoding="utf-8")

        combiner = FakeCombiner(dragdrop)

        augment_calls = []
        progress_calls = []

        def fake_augment(path):
            augment_calls.append(path)
            return {"appliers": 1, "with_output": 1, "skipped": False}

        def progress(stage, done, total, msg):
            progress_calls.append(stage)

        runner = RaidReportRunner(
            log_folder=log_dir,
            cache=cache,
            parse_log=lambda p: None,
            ei_version="v1",
            settings_fingerprint="abc",
            combiner=combiner,
            viewer_html=_make_viewer(tmp_path),
            output_dir=out_dir,
            augment_json=fake_augment,
            progress=progress,
        )

        runner.generate([info])

        stages = list(progress_calls)
        assert "augment" in stages
        combine_idx = stages.index("combine")
        augment_idx = stages.index("augment")
        bake_idx = stages.index("bake")
        assert combine_idx < augment_idx < bake_idx
        assert len(augment_calls) == 1
        assert augment_calls[0] == dragdrop

    def test_augment_raising_still_produces_report(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        log_a = log_dir / "20260718-200001.zevtc"
        log_a.write_text("")

        info = LogInfo(path=log_a, timestamp=datetime(2026, 7, 18, 20, 0, 1),
                       source="filename")

        cached = tmp_path / "cached.json"
        cached.write_text("{}")

        cache = FakeRaidReportCache()
        cache.preload(log_a, cached)

        dragdrop = tmp_path / "dragdrop.json"
        dragdrop.write_text(json.dumps([{"title": "Fight_01"}]), encoding="utf-8")

        combiner = FakeCombiner(dragdrop)

        def bad_augment(path):
            raise RuntimeError("kaboom")

        runner = RaidReportRunner(
            log_folder=log_dir,
            cache=cache,
            parse_log=lambda p: None,
            ei_version="v1",
            settings_fingerprint="abc",
            combiner=combiner,
            viewer_html=_make_viewer(tmp_path),
            output_dir=out_dir,
            augment_json=bad_augment,
        )

        result = runner.generate([info])
        assert result.html_path.exists()
        assert result.json_path.exists()
