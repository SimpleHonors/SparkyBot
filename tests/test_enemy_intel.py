"""Focused tests for deterministic Sparky Pro Enemy Intel extraction."""

from collections import Counter

from core.enemy_intel import PRO_NAVIGATION, build_enemy_intel, estimate_enemy_subgroups


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
        {
            "title": "night-Defenses-Summary",
            "text": "\n".join([
                '<$reveal stateTitle="$:/temp/detailed_state" default="Total" stateField="category_radio" type="match" text="Total">',
                "|thead-dark table-caption-top table-hover sortable|k",
                "|!Party |!Name |!Prof |!{{FightTime}} |!{{damageTaken}} |!{{damageTaken}}{Hits |!{{conditionDamageTaken}} |!{{conditionDamageTaken}}{Hits |!{{powerDamageTaken}} |!{{powerDamageTaken}}{Hits |!{{downedDamageTaken}} |!{{downedDamageTaken}}{Hits |!{{damageBarrier}} |!{{damageBarrier}}{Hits |!{{blockedCount}} |!{{evadedCount}} |!{{missedCount}} |!{{dodgeCount}} |!{{invulnedCount}} |!{{interruptedCount}} |!{{stunBreak}} |!{{downCount}} |!{{deadCount}} |!{{boonStrips}} |!{{conditionCleanses}} |!{{receivedCrowdControl}} |h",
                "|1|Alice|{{Firebrand}}|100|1,000|10|250|4|750|6|0|0|0|0|0|0|0|0|0|0|0|0|0|12|0|10|",
                "|1|Bob|{{Reaper}}|100|3,000|30|1,500|15|1,500|15|0|0|0|0|0|0|0|0|0|0|0|0|0|8|0|20|",
                "| Total - Defenses Table|c",
                "</$reveal>",
            ]),
        },
    ]


def _model():
    fights = [
        {"index": 1, "duration": "02m 00s 000ms", "rgb": {"r": 0, "g": 25, "b": 0}},
        {"index": 2, "duration": "03m 00s 000ms", "rgb": {"r": 5, "g": 10, "b": 0}},
        # Ten professions identified out of twelve enemies.
        {"index": 3, "duration": "05m 00s 000ms", "rgb": {"r": 0, "g": 12, "b": 0}},
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


def test_slots_include_metric_backed_tactical_role_inference():
    fight = next(
        row for row in _model()["fights"]
        if row["index"] == 1 and row["color"] == "green"
    )
    members = [member for party in fight["estimated_subgroups"] for member in party["members"]]
    by_profession = {member["profession"]: member for member in members}

    reaper = by_profession["Reaper"]
    assert reaper["role"] == "Unknown"
    assert reaper["role_tags"] == ["DPS", "Crowd Control", "Boon Strip"]
    assert reaper["role_family"] == "DPS"
    assert reaper["role_inference"]["qualifier"] == "Unresolved"
    assert reaper["role_inference"]["confidence"] == "unresolved"
    assert reaper["role_inference"]["candidate_label"] == "DPS"
    assert reaper["role_inference"]["candidate_qualifier"] == "Likely"
    levels = {row["role"]: row["level"] for row in reaper["role_inference"]["roles"]}
    assert levels == {"DPS": "Likely", "Crowd Control": "Likely", "Boon Strip": "Estimated"}
    assert "scores" not in reaper["role_inference"]
    assert any("Soul Spiral" in item for item in reaper["role_inference"]["evidence"])
    assert any("Grasping Darkness" in item for item in reaper["role_inference"]["evidence"])

    assert by_profession["Druid"]["role"] == "Unknown"
    assert by_profession["Druid"]["role_inference"]["qualifier"] == "Unresolved"
    assert by_profession["Firebrand"]["role"] == "Unknown"
    assert by_profession["Firebrand"]["role_tags"] == []
    assert by_profession["Firebrand"]["role_inference"]["qualifier"] == "Unresolved"
    assert all(
        any("observed in this fight/color" in item for item in member["role_inference"]["evidence"])
        for member in members
    )


def test_tempest_role_signals_remain_candidates_not_per_slot_claims():
    parties, _, _ = estimate_enemy_subgroups(
        [{"profession": "Tempest", "count": 1}], 1, role_context={}
    )
    tempest = parties[0]["members"][0]
    assert tempest["role"] == "Unknown"
    assert tempest["role_tags"] == []
    assert tempest["role_inference"]["qualifier"] == "Unresolved"
    assert tempest["role_inference"]["roles"] == []

    parties, _, _ = estimate_enemy_subgroups(
        [{"profession": "Tempest", "count": 1}], 1,
        role_context={"actor_role_evidence": {"professions": {"Tempest": {
            "roles": [{
                "role": "Support / Healing", "level": "Likely",
                "evidence": "low DPS plus Overload Water and Wash the Pain Away casts",
                "source_scope": "detailed_wvw_enemy_targets_across_selected_fights",
            }],
        }}}},
    )
    tempest = parties[0]["members"][0]
    assert tempest["role"] == "Unknown"
    assert tempest["role_inference"]["qualifier"] == "Unresolved"
    assert tempest["role_inference"]["candidate_label"] == "Support / Healing"
    assert tempest["role_inference"]["candidate_qualifier"] == "Likely"
    assert any("Overload Water" in item for item in tempest["role_inference"]["evidence"])


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
        "uptime_unit": "percent_of_squad_active_time",
        "evidence": "observed",
        "source_scope": "session",
    }
    assert {row["effect"] for row in pressure["cc"]} == {"Daze", "Stun"}
    assert pressure["incoming_strips"] == [{
        "effect": "Boon Strip",
        "count": 20,
        "count_unit": "boons_removed_from_squad",
        "rate_per_combat_second": 0.03,
        "rate_per_combat_minute": 2.0,
        "rate_unit": "aggregate_squad_boon_strips_per_combat_time",
        "evidence": "observed",
        "source_scope": "session_all_opponents",
        "measurement": "boons_removed_from_squad",
        "source": "Defenses-Summary",
        "attribution": "enemy_profession_not_attributed",
    }]
    pull_counts = {row["skill"]: row["count"] for row in pressure["pulls"]}
    assert pull_counts["Gravity Well"] == 5
    assert pull_counts["Grasping Darkness"] == 5
    assert all(
        not scope["aggregate"]["top_damage_skills"]
        for scope in model["scopes"]
    )
    assert pressure["damage_profile"] == {
        "total_incoming_damage": 4000,
        "direct_damage": 2250,
        "condition_damage": 1750,
        "combat_seconds": 600.0,
        "total_damage_per_second": 6.67,
        "direct_damage_per_second": 3.75,
        "condition_damage_per_second": 2.92,
        "direct_percent": 56.25,
        "condition_percent": 43.75,
        "classification": "Mixed Damage",
        "status": "observed",
        "evidence": "observed",
        "damage_unit": "hit_point_damage",
        "rate_unit": "aggregate_squad_damage_per_combat_second",
        "combat_time_unit": "seconds",
        "source_scope": "session_all_opponents",
        "source": "Defenses-Summary",
        "attribution": "enemy_profession_not_attributed",
    }
    assert pressure["control_profile"] == [{
        "effect": "Incoming Crowd Control",
        "count": 30,
        "count_unit": "received_crowd_control_events",
        "rate_per_combat_second": 0.05,
        "rate_per_combat_minute": 3.0,
        "rate_unit": "aggregate_squad_events_per_combat_time",
        "evidence": "observed",
        "source_scope": "session_all_opponents",
        "measurement": "received_crowd_control_events",
        "source": "Defenses-Summary",
        "attribution": "enemy_profession_not_attributed",
    }]
    condition_profile = pressure["condition_profile"]
    assert condition_profile["status"] == "observed"
    assert condition_profile["dominant_condition"] == "Bleeding"
    assert condition_profile["damaging_condition_uptime_index"] == 10.75
    assert condition_profile["normalized"][0] == {
        "effect": "Bleeding",
        "uptime_percent": 8.5,
        "uptime_unit": "percent_of_squad_active_time",
        "pressure_share_percent": 79.07,
        "evidence": "observed",
    }


def test_methodology_does_not_claim_exact_builds():
    model = _model()
    methodology = model["methodology"]
    assert methodology["profession_counts"] == "observed"
    assert methodology["party_placement"] == "inferred"
    assert methodology["exact_builds"] == "not_available"
    assert methodology["all_scope"] == "representative_average_across_snapshots"
    assert model["ai_analysis"] is None
