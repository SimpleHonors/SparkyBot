"""Team-attributed trait rates count observed actor-fight appearances."""
import json

import pytest

from core.enemy_intel import build_enemy_intel
from core.enemy_role_evidence import collect_enemy_role_evidence
from core.enemy_role_evidence import _enemy_team


def target(team=9001, skills=(41684,), active=30000, **extra):
    return {
        "name": "Holosmith repeated name", "profession": "Holosmith",
        "enemyPlayer": True, "teamID": team, "activeTimes": [active],
        "rotation": [{"id": skill, "skills": [{"castTime": 1}]} for skill in skills],
        **extra,
    }


def collect(tmp_path, *fights):
    paths = []
    for index, targets in enumerate(fights):
        path = tmp_path / f"fight-{index}.json"
        path.write_text(json.dumps({
            "durationMS": 30000, "targets": targets,
            "wvwMapData": {"redTeamID": 9001, "blueTeamID": 9002, "greenTeamID": 9003},
            "skillMap": {f"s{skill}": {"isTraitProc": True} for skill in (41684, 42475)},
        }))
        paths.append(path)
    return collect_enemy_role_evidence(paths)


def test_night_rates_are_separate_for_actual_teams(tmp_path):
    result = collect(tmp_path, [target(), target(skills=()), target(9002, skills=())], [target()])
    red = result["teams"]["red"]["professions"]["Holosmith"]
    blue = result["teams"]["blue"]["professions"]["Holosmith"]
    assert red["actor_appearances"] == 3
    assert red["trait_eligible_appearances"] == 3
    assert red["traits"][0]["actor_appearances"] == 2
    assert red["traits"][0]["eligible_actor_appearances"] == 3
    assert red["traits"][0]["observed_percent"] == 66.67
    assert blue["traits"] == []  # No proc means unknown, not unequipped.
    assert result["professions"]["Holosmith"]["actor_appearances"] == 4
    assert result["professions"]["Holosmith"]["traits"][0]["observed_percent"] == 50.0


def test_multiple_proc_skills_count_the_trait_once_per_actor_fight(tmp_path):
    result = collect(tmp_path, [target(skills=(41684, 42475, 41684)), target(skills=())])
    traits = result["teams"]["red"]["professions"]["Holosmith"]["traits"]
    assert len(traits) == 1
    assert traits[0]["actor_appearances"] == 1
    assert traits[0]["observed_percent"] == 50
    assert traits[0]["observed_skill_ids"] == [41684, 42475]


def composition():
    return [{"title": "night-Squad-Composition", "text": (
        "| Fight - 1: Red Composition |c\n| {{Holosmith}}: 1 |\n"
        "| Fight - 1: Blue Composition |c\n| {{Holosmith}}: 1 |"
    )}]


def test_team_scope_carries_only_its_own_night_evidence(tmp_path):
    evidence = collect(tmp_path, [target(), target(9002, skills=()), target(0)])
    intel = build_enemy_intel(composition(), [{"index": 1}], actor_role_evidence=evidence)
    scopes = {row["id"]: row for row in intel["scopes"]}
    assert scopes["red"]["role_validation"] == evidence["teams"]["red"]
    assert scopes["blue"]["role_validation"]["professions"]["Holosmith"]["traits"] == []
    for fight in intel["fights"]:
        inference = fight["estimated_subgroups"][0]["members"][0]["role_inference"]
        assert bool(inference["build_evidence"]) == (fight["color"] == "red")


def test_legacy_profession_evidence_is_never_assigned_to_a_color():
    legacy = {"enemy_actor_appearances": 1, "professions": {"Holosmith": {
        "traits": [{"trait": "legacy proc", "actor_appearances": 1}],
    }}}
    intel = build_enemy_intel(composition(), [{"index": 1}], actor_role_evidence=legacy)
    assert intel["role_validation"]["professions"] == legacy["professions"]
    for scope in intel["scopes"]:
        assert scope["role_validation"]["professions"] == {}
        assert scope["role_validation"]["status"] == "team_attribution_unavailable"
    for fight in intel["fights"]:
        assert fight["estimated_subgroups"][0]["members"][0]["role_inference"]["build_evidence"] == []


@pytest.mark.parametrize("team", [None, 0, -1, True, "9001", 9001.0, 99999])
def test_unknown_team_is_not_assigned_using_name_or_composition(tmp_path, team):
    result = collect(tmp_path, [target(team, name="Holosmith Red Invader")])
    assert set(result["teams"]) == {"unknown"}
    assert result["unknown_team_actor_appearances"] == 1
    assert result["teams"]["unknown"]["professions"]["Holosmith"]["traits"][0]["actor_appearances"] == 1


def test_short_appearances_are_excluded_from_both_rate_sides(tmp_path):
    result = collect(tmp_path, [target(active=14999), target(active=15000),
                                target(active=15000, skills=()), target(active=0)])
    profile = result["teams"]["red"]["professions"]["Holosmith"]
    assert profile["actor_appearances"] == 4
    assert profile["trait_eligible_appearances"] == 2
    assert profile["traits"][0]["actor_appearances"] == 1
    assert profile["traits"][0]["eligible_actor_appearances"] == 2
    assert profile["traits"][0]["observed_percent"] == 50
    assert "not exact equip prevalence" in result["trait_measurement"]["limitations"]


def test_zero_eligible_appearances_has_no_rate(tmp_path):
    profile = collect(tmp_path, [target(active=1)])["teams"]["red"]["professions"]["Holosmith"]
    assert profile["trait_eligible_appearances"] == 0
    assert profile["traits"] == []


def test_explicit_nonplayer_targets_do_not_enter_enemy_trait_denominators(tmp_path):
    result = collect(tmp_path, [target(enemyPlayer=False), target()])
    assert result["enemy_actor_appearances"] == 1
    assert result["teams"]["red"]["professions"]["Holosmith"]["trait_eligible_appearances"] == 1


@pytest.mark.parametrize("mapping", [None, [], [9001], "red", {"redTeamID": 9001, "blueTeamID": 9001}])
def test_missing_or_ambiguous_color_mapping_remains_unknown(mapping):
    assert _enemy_team(target(), {"wvwMapData": mapping}) == (9001, "unknown")


def test_do_not_guess_colors_from_legacy_numeric_team_id():
    assert _enemy_team(target(705), {}) == (705, "unknown")


def test_unique_major_trait_and_ei_proc_proof_are_both_required(tmp_path):
    path = tmp_path / "proof.json"
    path.write_text(json.dumps({"targets": [target(skills=(41684, 42475, 999999))],
        "skillMap": {"s41684": {"isTraitProc": False}, "s999999": {"isTraitProc": True}}}))
    assert collect_enemy_role_evidence([path])["professions"]["Holosmith"]["traits"] == []


def test_damage_distribution_and_rotation_share_a_single_trait_count(tmp_path):
    evidence = collect(tmp_path, [target(totalDamageDist=[[{"id": 42475}, {"id": 41684}]])])
    traits = evidence["teams"]["red"]["professions"]["Holosmith"]["traits"]
    assert len(traits) == 1
    assert traits[0]["actor_appearances"] == 1
    assert traits[0]["observed_percent"] == 100
    assert traits[0]["observed_skill_ids"] == [41684, 42475]
    json.dumps(evidence)  # No accumulator sets escape into report payloads.


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_malformed_map_team_ids_cannot_attribute_a_color(value):
    assert _enemy_team(target(1), {"wvwMapData": {"redTeamID": value}}) == (1, "unknown")