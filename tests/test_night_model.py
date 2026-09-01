"""Night-model extractor tests against two REAL night stores.

aug10: poison night, combiner leaderboards off (db_update=false) ->
       leaderboards must be [] and stat_tables carry the per-night tables.
jul18: night with accumulated Top_Stats.db -> 8+ *-Leaderboard tiddlers.
"""

import json
from pathlib import Path

import pytest

from core.night_model import build_night_model

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    with (FIXTURES / name).open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def aug10():
    return build_night_model(_load("aug10_summary_poison.json"))


@pytest.fixture(scope="module")
def jul18():
    return build_night_model(_load("jul18_night_with_leaderboards.json"))


# ------------------------------------------------------------------ aug10


def test_aug10_warnings_empty(aug10):
    assert aug10["warnings"] == []


def test_aug10_totals(aug10):
    assert aug10["totals"]["fights"] == 8
    assert aug10["totals"]["kdr"] == pytest.approx(8.21)
    assert aug10["totals"]["enemy_downs"] == 417
    assert aug10["totals"]["enemy_kills"] == 320
    assert aug10["totals"]["ally_downs"] == 94
    assert aug10["totals"]["ally_deaths"] == 39


def test_aug10_session(aug10):
    assert aug10["session"]["tag"] == "2026-08-10-20:27:05"
    assert aug10["session"]["date"] == "2026-08-10"
    assert aug10["session"]["commander"] == "Mohr Shadows"
    assert aug10["session"]["commander_account"] == "Mohrr.8294"
    assert aug10["session"]["total_duration"] == "31m 00s 871ms"


def test_aug10_fights(aug10):
    assert len(aug10["fights"]) == 8
    first = aug10["fights"][0]
    assert first["index"] == 1
    assert first["time_label"] == "2026-08-10 - 19:07:49 - GAB"
    assert first["duration"] == "01m 24s 580ms"
    assert first["squad"] == 32
    assert first["allies"] == 1
    assert first["enemy"] == 10
    assert first["rgb"] == {"r": 0, "g": 10, "b": 0}
    assert first["downs"] == 9
    assert first["kills"] == 8
    assert first["damage_out"] == 597901
    assert first["damage_in"] == 284920
    assert first["barrier_out"] == 49317
    assert first["barrier_out_pct"] == pytest.approx(17.31)
    assert first["shield_out"] == 28355
    assert first["shield_out_pct"] == pytest.approx(4.74)
    assert first["chart"] == (
        "2026-08-10-20:27:05_Fight_01_Damage_Output_Review")
    assert aug10["fights"][-1]["index"] == 8


def test_aug10_leaderboards_empty_is_valid(aug10):
    # this night had no accumulated Top_Stats.db -> no boards, no warning
    assert aug10["leaderboards"] == []


def test_aug10_stat_tables_non_empty(aug10):
    assert aug10["stat_tables"]
    by_stat = {t["stat"]: t["rows"] for t in aug10["stat_tables"]}
    assert "Damage" in by_stat and len(by_stat["Damage"]) >= 10
    assert "Heal Stats" in by_stat and len(by_stat["Heal Stats"]) >= 10
    assert "Uptimes" in by_stat and len(by_stat["Uptimes"]) >= 10
    row = by_stat["Damage"][0]
    assert row["name"]
    assert row["profession"]
    assert row["value"] is not None
    assert row["value"] != pytest.approx(1848.4)
    damage = next(t for t in aug10["stat_tables"] if t["stat"] == "Damage")
    assert damage["value_label"] == "Damage / sec"
    assert damage["rows"] == sorted(
        damage["rows"], key=lambda item: item["value"], reverse=True
    )
    healing = next(t for t in aug10["stat_tables"] if t["stat"] == "Heal Stats")
    assert healing["value_label"] == "Healing / sec"
    healer_ids = [row["account"] or row["name"] for row in healing["rows"]]
    assert len(healer_ids) == len(set(healer_ids))
    pulls = next(t for t in aug10["stat_tables"] if t["stat"] == "Outgoing Pulls")
    assert pulls["value_label"] == "Outgoing pulls"
    tad = next(row for row in pulls["rows"] if row["name"] == "Tad Bit Blunt")
    assert tad["value"] == 28
    resurrect = next(
        t for t in aug10["stat_tables"] if t["stat"] == "Combat Resurrect"
    )
    assert resurrect["value_label"] == "Resurrection output"
    assert resurrect["rows"][0]["value"] > 0
    mechanics = next(t for t in aug10["stat_tables"] if t["stat"] == "Mechanics")
    thornzyz = next(row for row in mechanics["rows"] if row["account"] == "jreezy.3105")
    assert thornzyz["name"] == "Thornzyz"
    assert thornzyz["profession"] == "Amalgam"
    attendance = next(t for t in aug10["stat_tables"] if t["stat"] == "Attendance")
    distant = next(
        row for row in attendance["rows"] if row["account"] == "Mighty Schmoo.6018"
    )
    assert distant["name"] == "Distant Deadlights"
    assert distant["profession"] == "Luminary"


def test_aug10_poison_non_empty(aug10):
    assert aug10["poison"]
    top = aug10["poison"][0]
    assert top["name"] == "Simple Deathly"
    assert top["prof"] == "Scourge"
    assert top["apps"] == 1014
    assert top["apps_per_min"] > 0


def test_aug10_high_scores(aug10):
    blocks = aug10["high_scores"]["blocks"]
    assert blocks
    assert blocks[0]["caption"] == "Highest 1s Burst Damage"
    assert blocks[0]["rows"]
    first = blocks[0]["rows"][0]
    assert first["score"] == 53153
    assert first["name"] == "Dont Nerf My Mech"
    assert first["profession"] == "Holosmith"
    assert first["fight"] == 3
    assert first["cells"] == ["Dont Nerf My Mech", "Holosmith", "Fight 3"]
    skill = blocks[1]["rows"][0]
    assert skill["details"] == ["Rocket"]


def test_aug10_squad_composition(aug10):
    squads = aug10["squad_composition"]["squads"]
    assert len(squads) == 8
    assert squads[0]["fight"] == 1
    assert squads[0]["players"]
    assert all(p["name"] for p in squads[0]["players"])


# ------------------------------------------------------------------ jul18


def test_jul18_warnings_empty(jul18):
    assert jul18["warnings"] == []


def test_jul18_totals(jul18):
    assert jul18["totals"]["fights"] == 34
    assert jul18["totals"]["kdr"] == pytest.approx(4.27)


def test_jul18_leaderboards(jul18):
    big = [b for b in jul18["leaderboards"] if len(b["rows"]) >= 10]
    assert len(big) >= 3
    stats = {b["stat"] for b in jul18["leaderboards"]}
    assert {"damage", "kills", "downs"} <= stats
    board = next(b for b in jul18["leaderboards"] if b["stat"] == "damage")
    assert len(board["rows"]) >= 10
    row = board["rows"][0]
    assert row["rank"] == 1
    assert row["name"]
    assert row["account"]
    assert row["profession"]
    assert row["value"] is not None


def test_jul18_leaderboard_menu_excluded(jul18):
    # the "{tag}-Leaderboard" menu tiddler must not become a board
    assert all(b["stat"] not in ("", "21:05:14", "07-18-21:05:14")
               for b in jul18["leaderboards"])


def test_jul18_fights_and_session(jul18):
    assert len(jul18["fights"]) == 34
    assert jul18["session"]["tag"] == "2026-07-18-21:05:14"
    assert jul18["session"]["total_duration"] == "01h 11m 26s 164ms"


def test_jul18_poison_non_empty(jul18):
    assert len(jul18["poison"]) >= 10


# ------------------------------------------------------------ robustness


def test_bad_section_warns_but_does_not_raise():
    tiddlers = _load("aug10_summary_poison.json")
    for t in tiddlers:
        if "Tag_Stats" in t.get("title", ""):
            t["text"] = "this is not a table at all"
    model = build_night_model(tiddlers)
    assert any(w.startswith("totals:") for w in model["warnings"])
    assert model["totals"] is None
    # the rest of the night still comes through
    assert len(model["fights"]) == 8
    assert model["poison"]


def test_garbage_input_is_tolerated():
    model = build_night_model(["not a dict", {"no_title": 1}, None])
    assert model["schema_version"] == 2
    assert model["fights"] == []
    assert model["leaderboards"] == []
    assert model["stat_tables"] == []
    assert model["poison"] == []
    # unparseable sections are recorded; absent-is-valid sections are not
    failed = {w.split(":")[0] for w in model["warnings"]}
    assert failed == {"totals", "fights", "high_scores", "squad_composition"}


def test_selected_fight_coverage_can_be_supplied_by_the_report_pipeline():
    model = build_night_model(
        _load("aug10_summary_poison.json"), selected_fights=11
    )
    coverage = model["enemy_intel"]["coverage"]
    assert coverage["selected_fights"] == 11
    assert coverage["reported_fights"] == 8
    assert coverage["modeled_fights"] == 8
