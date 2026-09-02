"""Public contract tests for honest, sortable Pro report metrics."""

from core.night_model import build_night_model


TAG = "2026-08-31-20:43:58"


def _model(*tiddlers):
    return build_night_model(list(tiddlers), selected_fights=31)


def test_historical_rating_board_uses_the_actual_average_metric_not_glicko():
    model = _model(
        {
            "title": f"{TAG}-damage-Leaderboard",
            "caption": "DPS",
            "text": (
                "| Rank |Name|Profession| Glicko Rating| Trend| Raids |"
                " Guild Member | Avg Damage|h\n"
                "| 1 |Taeyeon Fognus |{{Evoker}} Evoker | 2305.6 |"
                " 37.8 up| 6 | yes | 3,133.73/sec|"
            ),
        }
    )

    board = model["leaderboards"][0]
    row = board["rows"][0]
    assert board["scope"] == "historical"
    assert board["source_kind"] == "historical_rating"
    assert board["metric"]["rate_label"] == "DPS"
    assert board["sort"]["default_key"] == "rate"
    assert row["value"] == 3133.73
    assert row["rate"] == 3133.73
    assert row["rating"] == 2305.6
    assert row["raids"] == 6
    assert row["total"] is None
    assert row["participation_time"] is None
    assert "score" not in board["metric"]


def test_session_damage_exposes_total_dps_fight_time_and_fights_for_sorting():
    model = _model(
        {
            "title": f"{TAG}-Damage",
            "caption": "Damage",
            "text": (
                "|Party|Name|Prof|FightTime|Target_Damage|Target_Damage_PS|h\n"
                "|1|Player Example|{{Paragon}} Par|700.5|1,400,000|1,998.57|"
            ),
        },
        {
            "title": f"{TAG}-Attendance",
            "caption": "Attendance",
            "text": (
                "|Account|Name|Profession|Num Fights|Active Time|Status|h\n"
                "|Example.1234|Player Example|{{Paragon}}|12|701||"
            ),
        },
    )

    table = next(t for t in model["stat_tables"] if t["stat"] == "Damage")
    row = table["rows"][0]
    assert table["scope"] == "session"
    assert table["metric"]["total_label"] == "Damage"
    assert table["metric"]["rate_label"] == "DPS"
    assert table["sort"]["default_key"] == "total"
    assert row["total"] == 1_400_000
    assert row["rate"] == 1998.57
    assert row["participation_time"] == 700.5
    assert row["fight_count"] == 12
    # Legacy consumers keep their previous rate-oriented primary value.
    assert row["value"] == 1998.57
    assert table["value_label"] == "Damage / sec"


def test_night_mvps_are_session_totals_with_rates_and_drilldown_evidence():
    model = _model(
        {
            "title": f"{TAG}-Support-Summary",
            "caption": "Support - Summary",
            "text": (
                "|Party|Name|Prof|FightTime|condiCleanse|boonStrips|"
                "resurrects|h\n"
                "|1|Support One|{{Druid}} Dru|600|200|40|8|\n"
                "|2|Burst Guest|{{Tempest}} Tem|60|50|10|2|"
            ),
        },
        {
            "title": f"{TAG}-Offensive-Summary",
            "caption": "Offensive - Summary",
            "text": (
                "|Party|Name|Prof|FightTime|totalDmg|killed|downed|"
                "downContribution|appliedCrowdControl|h\n"
                "|1|Impact One|{{Renegade}} Ren|500|900000|9|14|120000|80|"
            ),
        },
    )

    awards = {award["category"]: award for award in model["night_mvps"]}
    assert awards["Cleanses"]["name"] == "Support One"
    assert awards["Cleanses"]["total"] == 200
    assert awards["Cleanses"]["rate"] == 20
    assert awards["Boon Strips"]["total"] == 40
    assert awards["Resurrection"]["total"] == 8
    assert awards["Crowd Control"]["total"] == 80
    assert awards["Fight Impact"]["metric_label"] == "Down Contribution"
    assert awards["Fight Impact"]["total"] == 120000
    assert awards["Fight Impact"]["source"]["scope"] == "session"
    assert awards["Fight Impact"]["evidence"]["metrics"]["downcontribution"] == 120000
    assert all("score" not in award for award in model["night_mvps"])


def test_stability_generation_uses_the_dedicated_total_and_gen_per_sec_tables():
    model = _model(
        {
            "title": f"{TAG}-Boon-Generation-Detailed",
            "caption": "Boons - Detailed",
            "text": (
                "! {{Stability}} Stability\n"
                "|Party|Name|Prof|FightTime|Self Gen|Group Gen|Squad Gen|Total Gen|h\n"
                "|1|Stable One|{{Luminary}} Lum|600|100|200|300|400|\n"
                "|Total Gen - Stability Table|c\n"
                "|Party|Name|Prof|FightTime|Self Gen|Group Gen|Squad Gen|Total Gen|h\n"
                "|1|Stable One|{{Luminary}} Lum|600|0.17|0.33|0.50|0.67|\n"
                "|Gen/Sec - Stability Table|c\n"
            ),
        }
    )

    table = next(t for t in model["stat_tables"] if t["source_key"] == "Stability-Generation")
    row = table["rows"][0]
    assert row["total"] == 400
    assert row["rate"] == 0.67
    assert row["participation_time"] == 600
    award = next(a for a in model["night_mvps"] if a["category"] == "Stability")
    assert award["total"] == 400
    assert award["rate"] == 0.67
