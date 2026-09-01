"""Focused tests for deterministic Sparky Pro Enemy Intel extraction."""

from collections import Counter

from core.enemy_intel import PRO_NAVIGATION, build_enemy_intel


def _composition_text():
    return "\n".join([
        "| Enemy Composition |h",
        "|Fight - 1 : Green Composition |c",
        "|{{Firebrand}} : 5 ||{{Druid}} : 5 ||{{Reaper}} : 15 |",
        "|Fight - 2 : Green Composition |c",
        "|{{Troubadour}} : 2 ||{{Necromancer}} : 8 |",
        "|Fight - 2 : Red Composition |c",
        "|{{Firebrand}} : 1 ||{{Reaper}} : 4 |",
        "|Fight - 3 : Green Composition |c",
        "|{{Firebrand}} : 2 ||{{Reaper}} : 8 |",
    ])


def _pressure_tiddlers():
    return [
        {
            "title": "night-Top-Damage-By-Skill",
            "text": "\n".join([
                "|!Skill Name | !Damage | !Total Casts | !Connected Hits | !% of Total|h",
                "|{{Soul Spiral}}-Soul Spiral | 1,800,000 | 39 | 2,100 | 5.2% |",
                "|{{Burning}}-Burning | 570,000 | 0 | 3,300 | 1.6% |",
                "| Enemy Damage Output |c",
            ]),
        },
        {
            "title": "night-Conditions-In",
            "text": "\n".join([
                "|!Party |!Name |!Prof |!{{FightTime}} |!{{Bleeding}} |!{{Poison}} |h",
                "|Squad Average Uptime |<|<|<|8.5%|2.25%|f",
            ]),
        },
        {
            "title": "night-Debuffs-In",
            "text": "\n".join([
                "|!Party |!Name |!Prof |!{{FightTime}} |!{{Daze}} |!{{Stun}} |!{{Boon Strip}} |h",
                "|Squad Average Uptime |<|<|<|0.7%|0.2%|0.4%|f",
            ]),
        },
        {
            "title": "night-Pull-Skills",
            "text": "\n".join([
                "| Incoming Pulls |c",
                "|!Player |!Prof |!{{FightTime}} |!{{Gravity Well}} |!{{Grasping Darkness}} |h",
                "|One |{{Chronomancer}} |100 |3 |1 |",
                "|Two |{{Reaper}} |100 |2 |4 |",
                "| Outgoing Pulls |c",
            ]),
        },
    ]


def _model():
    fights = [
        {"index": 1, "rgb": {"r": 0, "g": 25, "b": 0}},
        {"index": 2, "rgb": {"r": 5, "g": 10, "b": 0}},
        # Ten professions identified out of twelve enemies.
        {"index": 3, "rgb": {"r": 0, "g": 12, "b": 0}},
    ]
    tiddlers = [{"title": "night-Squad-Composition", "text": _composition_text()}]
    tiddlers.extend(_pressure_tiddlers())
    return build_enemy_intel(tiddlers, fights, selected_fights=4)


def test_pro_navigation_has_required_views_and_subviews():
    by_id = {view["id"]: view for view in PRO_NAVIGATION}
    assert {"overview", "dps", "support", "healing", "high-scores", "enemy-intel", "details"} <= set(by_id)
    assert "conditions" in by_id["dps"]["subviews"]
    assert "estimated-subgroups" in by_id["enemy-intel"]["subviews"]


def test_enemy_scopes_never_blend_world_colours_into_a_party_grid():
    model = _model()
    assert model["coverage"]["selected_fights"] == 4
    assert model["coverage"]["reported_fights"] == 3
    assert model["coverage"]["composition_fights"] == 3
    assert model["coverage"]["composition_snapshots"] == 4
    assert model["coverage"]["colors"] == ["green", "red"]
    assert model["all"]["mode"] == "comparison_only"
    assert model["all"]["estimated_subgroups"] is None

    scopes = {scope["id"]: scope for scope in model["scopes"]}
    assert scopes["green"]["fight_indexes"] == [1, 2, 3]
    assert scopes["red"]["fight_indexes"] == [2]
    assert scopes["green"]["groups"][0]["grouping_method"] == "color_only"
    assert scopes["green"]["groups"][0]["cohort_status"] == "future"


def test_estimated_parties_preserve_counts_and_spread_support_anchors():
    fight = next(
        row for row in _model()["fights"]
        if row["index"] == 1 and row["color"] == "green"
    )
    assert fight["enemy_count"] == 25
    assert fight["observed_profession_count"] == 25
    assert len(fight["estimated_subgroups"]) == 5
    assert fight["party_placement_evidence"] == "inferred"

    members = [member for party in fight["estimated_subgroups"] for member in party["members"]]
    counts = Counter(member["profession"] for member in members)
    assert counts == {"Firebrand": 5, "Druid": 5, "Reaper": 15}
    assert all(len(party["members"]) == 5 for party in fight["estimated_subgroups"])
    assert all(
        sum(member["profession"] == "Firebrand" for member in party["members"]) == 1
        for party in fight["estimated_subgroups"]
    )
    assert all(member["evidence"] == "inferred" for member in members)


def test_unknown_enemies_are_distinct_from_partial_party_open_slots():
    fight = next(
        row for row in _model()["fights"]
        if row["index"] == 3 and row["color"] == "green"
    )
    assert len(fight["estimated_subgroups"]) == 3
    assert fight["unknown_slots"] == 2
    assert sum(party["unknown_slots"] for party in fight["estimated_subgroups"]) == 2
    assert sum(party["open_slots"] for party in fight["estimated_subgroups"]) == 3
    assert fight["confidence"]["level"] == "medium"


def test_session_pressure_is_observed_but_not_falsely_color_attributed():
    model = _model()
    pressure = model["session_pressure"]
    assert pressure["source_scope"] == "session_all_opponents"
    assert pressure["top_damage_skills"][0]["skill"] == "Soul Spiral"
    assert pressure["top_damage_skills"][0]["damage"] == 1800000
    assert pressure["conditions_in"][0] == {
        "effect": "Bleeding",
        "uptime_percent": 8.5,
        "evidence": "observed",
        "source_scope": "session",
    }
    assert {row["effect"] for row in pressure["cc"]} == {"Daze", "Stun"}
    assert pressure["incoming_strips"][0]["effect"] == "Boon Strip"
    pull_counts = {row["skill"]: row["count"] for row in pressure["pulls"]}
    assert pull_counts["Gravity Well"] == 5
    assert pull_counts["Grasping Darkness"] == 5
    assert all(
        not scope["aggregate"]["top_damage_skills"]
        for scope in model["scopes"]
    )
    assert pressure["damage_profile"]["status"] == "not_available_from_combiner_summary"


def test_methodology_does_not_claim_exact_builds():
    model = _model()
    methodology = model["methodology"]
    assert methodology["profession_counts"] == "observed"
    assert methodology["party_placement"] == "inferred"
    assert methodology["exact_builds"] == "not_available"
    assert methodology["all_scope"] == "comparison_only_never_blended"
    assert model["ai_analysis"] is None
