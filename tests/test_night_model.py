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


def _commander_selection_tiddlers(tag_rows, attendance_rows):
    return [
        {
            "title": "2026-08-31-20:43:58-Log-Summary",
            "text": "",
        },
        {
            "title": "2026-08-31-20:43:58-Tag_Stats",
            "caption": "Tag Summary",
            "text": "\n".join([
                "| Summary by Command Tag |c",
                "|Name | Prof | Fights | DownedEnemy | killed | DownedAlly | DeadAlly | KDR |h",
                *tag_rows,
                "|Totals |<| 10 | 0 | 0 | 0 | 0 | 0|f",
            ]),
        },
        {
            "title": "2026-08-31-20:43:58-Attendance",
            "caption": "Attendance",
            "text": "\n".join([
                "| Attendance Review |c",
                "|Account|Name|Profession| Num Fights| Active Time| Status |h",
                *attendance_rows,
            ]),
        },
    ]


def test_session_commander_uses_most_commanded_fights_not_first_row():
    model = build_night_model(_commander_selection_tiddlers(
        [
            '|<span data-tooltip="Alpha.1111">Alpha Tag</span>|{{Druid}}|3|0|0|0|0|0|',
            '|<span data-tooltip="Beta.2222">Beta Tag</span>|{{Reaper}}|7|0|0|0|0|0|',
        ],
        [
            "|Alpha.1111|Alpha Tag|{{Druid}}|3|900| |",
            "|Beta.2222|Beta Tag|{{Reaper}}|7|700| |",
        ],
    ))

    assert model["session"]["commander"] == "Beta Tag"
    assert model["session"]["commander_account"] == "Beta.2222"


def test_session_commander_tie_uses_most_combat_time():
    model = build_night_model(_commander_selection_tiddlers(
        [
            '|<span data-tooltip="Alpha.1111">Alpha Tag</span>|{{Druid}}|5|0|0|0|0|0|',
            '|<span data-tooltip="Beta.2222">Beta Tag</span>|{{Reaper}}|5|0|0|0|0|0|',
        ],
        [
            "|Alpha.1111|Alpha Tag|{{Druid}}|5|700| |",
            "|Beta.2222|Beta Tag|{{Reaper}}|5|900| |",
        ],
    ))

    assert model["session"]["commander"] == "Beta Tag"
    assert model["session"]["commander_account"] == "Beta.2222"


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
    assert first["ally_downs"] == 0
    assert first["ally_deaths"] == 0
    assert first["damage_out"] == 597901
    assert first["damage_in"] == 284920
    assert first["barrier_out"] == 49317
    assert first["barrier_out_pct"] == pytest.approx(17.31)
    assert first["shield_out"] == 28355
    assert first["shield_out_pct"] == pytest.approx(4.74)
    assert first["chart"] == (
        "2026-08-10-20:27:05_Fight_01_Damage_Output_Review")
    assert aug10["fights"][-1]["index"] == 8


def test_fight_report_url_is_preserved_only_when_posted():
    tiddlers = _load("aug10_summary_poison.json")
    overview = next(t for t in tiddlers if t.get("title", "").endswith("-Overview"))
    overview["text"] = overview["text"].replace(
        "2026-08-10 - 19:07:49 - GAB",
        '<a href="https://dps.report/posted-fight">Fight 1</a>',
        1,
    )

    fights = build_night_model(tiddlers)["fights"]

    assert fights[0]["report_url"] == "https://dps.report/posted-fight"
    assert all(fight["report_url"] is None for fight in fights[1:])


def test_aug10_leaderboards_empty_is_valid(aug10):
    # this night had no accumulated Top_Stats.db -> no boards, no warning
    assert aug10["leaderboards"] == []


def test_aug10_stat_tables_non_empty(aug10):
    assert aug10["stat_tables"]
    by_stat = {t["stat"]: t["rows"] for t in aug10["stat_tables"]}
    assert "Damage" in by_stat and len(by_stat["Damage"]) >= 10
    assert "Healing" in by_stat and len(by_stat["Healing"]) >= 10
    assert "Might Uptime" in by_stat and len(by_stat["Might Uptime"]) >= 10
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
    healing = next(t for t in aug10["stat_tables"] if t["source_key"] == "Heal-Stats")
    assert healing["value_label"] == "Healing / sec"
    healer_ids = [row["account"] or row["name"] for row in healing["rows"]]
    assert len(healer_ids) == len(set(healer_ids))
    uptime = next(
        t for t in aug10["stat_tables"] if t["source_key"] == "Uptimes"
    )
    assert uptime["stat"] == "Might Uptime"
    assert uptime["metric"]["rate_label"] == "Might Uptime (%)"
    assert uptime["metric"]["rate_unit"] == "percent"
    assert all(row["total"] is None for row in uptime["rows"])
    assert all(row["rate"] == row["metrics"]["might"] for row in uptime["rows"])
    pulls = next(t for t in aug10["stat_tables"] if t["stat"] == "Outgoing Pulls")
    assert pulls["value_label"] == "Pull-skill connected hits"
    tad = next(row for row in pulls["rows"] if row["name"] == "Tad Bit Blunt")
    assert tad["value"] == 28
    assert tad["pull_skill_connected_hits"] == 28
    assert tad["pull_skill_logged_hit_events"] == 40
    assert tad["pull_skill_connection_rate"] == pytest.approx(70.0)
    thornzyz_pull = next(row for row in pulls["rows"] if row["name"] == "Thornzyz")
    assert thornzyz_pull["total"] == 246
    assert thornzyz_pull["pull_skill_connected_hits"] == 246
    assert thornzyz_pull["pull_skill_logged_hit_events"] == 340
    assert thornzyz_pull["pull_skill_connection_rate"] == pytest.approx(72.3529)
    assert all(
        "pull" not in award["category"].casefold()
        for award in aug10["night_mvps"]
    )
    resurrect = next(
        t for t in aug10["stat_tables"] if t["source_key"] == "Combat-Resurrect"
    )
    assert resurrect["stat"] == "Combat-Resurrection Healing"
    assert resurrect["value_label"] == "Resurrection output"
    assert resurrect["rows"][0]["value"] > 0
    mechanics = next(t for t in aug10["stat_tables"] if t["source_key"] == "Mechanics")
    thornzyz = next(row for row in mechanics["rows"] if row["account"] == "jreezy.3105")
    assert thornzyz["name"] == "Thornzyz"
    assert thornzyz["profession"] == "Amalgam"
    attendance = next(t for t in aug10["stat_tables"] if t["stat"] == "Attendance")
    assert attendance["show_fights_column"] is False
    distant = next(
        row for row in attendance["rows"] if row["account"] == "Mighty Schmoo.6018"
    )
    assert distant["name"] == "Distant Deadlights"
    assert distant["profession"] == "Luminary"
    hidden_sources = {"Conditions-In", "Debuffs-In", "Support-Summary", "Offensive-Summary"}
    assert all(
        table["show_as_board"] is False
        for table in aug10["stat_tables"]
        if table["source_key"] in hidden_sources
    )
    assert next(
        table for table in aug10["stat_tables"] if table["source_key"] == "Heal-Stats"
    )["stat"] == "Healing"
    assert next(
        table for table in aug10["stat_tables"] if table["source_key"] == "Mechanics"
    )["stat"] == "Killing Blows"


def test_pull_table_combines_connected_hits_logged_hits_and_detailed_casts():
    evidence = {
            "players": [{
                "account": "jreezy.3105",
                "names": ["Thornzyz"],
                "weapons": ["Hammer"],
                "skill_casts": {"Flux State": 12},
            }],
        }
    model = build_night_model(
        _load("aug10_summary_poison.json"),
        player_skill_evidence=evidence,
    )
    pulls = next(
        table for table in model["stat_tables"]
        if table["source_key"] == "Pull-Skills"
    )
    thornzyz = next(row for row in pulls["rows"] if row["name"] == "Thornzyz")

    assert thornzyz["pull_skill_casts"] == 12
    assert thornzyz["pull_skill_connected_hits_per_cast"] == pytest.approx(20.5)
    assert model["player_skill_evidence"] == evidence


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
    assert all(p["party"] >= 1 for p in squads[0]["players"])
    assert all(1 <= p["slot"] <= 5 for p in squads[0]["players"])


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


def test_player_damage_by_skill_tables_are_modeled_and_duplicate_names_combine():
    tiddlers = [
        {
            "title": "2026-08-31-20:43:58-Log-Summary",
            "text": "",
        },
        {
            "title": (
                "2026-08-31-20:43:58-Damage-By-Skill-Amalgam-"
                "Simple Gadget-SimpleHonors.6320"
            ),
            "text": "\n".join(
                [
                    "|{{Amalgam}} - Simple Gadget - SimpleHonors.6320|c",
                    "|!Skill Name | !Damage| !Down Contrib| !Hits| !Dmg/Hit| !Max Hit| !% of Total|h",
                    "|[img width=24 [Thunderclap|https://example/1.png]]-Thunderclap | 100| 20| 4| 25| 60| 10%|",
                    "|[img width=24 [Thunderclap|https://example/2.png]]-Thunderclap | 50| 5| 2| 25| 30| 5%|",
                    "|[img width=24 [Napalm|https://example/3.png]]-Napalm | 75| 0| 3| 25| 40| 7.5%|",
                ]
            ),
        },
    ]

    model = build_night_model(tiddlers)

    player = model["player_skill_damage"][0]
    assert player["name"] == "Simple Gadget"
    assert player["profession"] == "Amalgam"
    assert player["account"] == "SimpleHonors.6320"
    assert player["total_damage"] == 225
    assert player["skills"][0] == {
        "skill": "Thunderclap",
        "damage": 150,
        "down_contribution": 25,
        "hits": 6,
        "damage_per_hit": 25,
        "max_hit": 60,
        "percent_of_total": 15,
    }
    assert "not zeroes" in player["coverage_note"]


def test_older_damage_by_skill_export_keeps_down_damage_without_fake_max_hit(aug10):
    thornzyz = next(
        player for player in aug10["player_skill_damage"]
        if player["name"] == "Thornzyz"
    )
    skills = {row["skill"]: row for row in thornzyz["skills"]}

    assert skills["Offensive Protocol: Obliterate"]["down_contribution"] == 44_116
    assert skills["Napalm"]["down_contribution"] == 62_696
    assert skills["Offensive Protocol: Obliterate"]["max_hit"] is None
    assert skills["Napalm"]["max_hit"] is None
