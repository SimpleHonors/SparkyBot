import json
from pathlib import Path

from core.enemy_role_evidence import (
    collect_enemy_role_evidence,
    collect_player_skill_evidence,
    collect_report_evidence,
)


def _target(profession, dps, damage, rotation=None, damage_skill=None):
    target = {
        "name": f"{profession} anonymous",
        "profession": profession,
        "activeTimes": [60_000],
        "dpsAll": [{"dps": dps, "damage": damage}],
        "rotation": rotation or [],
        "totalDamageDist": [[]],
    }
    if damage_skill:
        target["totalDamageDist"] = [[{
            "id": damage_skill, "totalDamage": damage, "connectedHits": 20,
        }]]
    return target


def test_detailed_enemy_skills_plus_low_dps_validate_support_not_raw_healing(tmp_path):
    path = tmp_path / "fight.json"
    path.write_text(json.dumps({
        "durationMS": 60_000,
        "skillMap": {
            "s1": {"name": '"Wash the Pain Away!"'},
            "s2": {"name": '"Rebound!"'},
            "s3": {"name": "Soul Spiral"},
        },
        "targets": [
            _target("Tempest", 100, 6_000, rotation=[
                {"id": 1, "skills": [{}, {}, {}, {}]},
                {"id": 2, "skills": [{}, {}]},
            ]),
            _target("Tempest", 3_000, 180_000, damage_skill=3),
            _target("Reaper", 5_000, 300_000, damage_skill=3),
            _target("Druid", 150, 9_000),
        ],
    }), encoding="utf-8")

    result = collect_enemy_role_evidence([path])

    tempest = result["professions"]["Tempest"]
    assert tempest["low_damage_appearances"] == 1
    assert {row["role"] for row in tempest["roles"]} == {
        "Support / Healing", "Boon Support", "DPS",
    }
    assert all(row["level"] == "Likely" for row in tempest["roles"])
    assert tempest["profession_damage_reference_dps"] == 3_000
    assert result["professions"]["Druid"]["roles"] == []
    assert "healing totals are not measured" in result["limitations"]


def test_support_role_uses_same_profession_damage_baseline(tmp_path):
    path = tmp_path / "fight.json"
    path.write_text(json.dumps({
        "durationMS": 60_000,
        "skillMap": {
            "s1": {"name": "Healing Mist"},
            "s2": {"name": "Flame Jet"},
        },
        "targets": [
            _target("Amalgam", 3_000, 180_000, rotation=[
                {"id": 1, "skills": [{}, {}, {}, {}]},
            ], damage_skill=2),
            _target("Amalgam", 2_500, 150_000, damage_skill=2),
            _target("Reaper", 12_000, 720_000, damage_skill=2),
        ],
    }), encoding="utf-8")

    result = collect_enemy_role_evidence([path])
    amalgam = result["professions"]["Amalgam"]

    assert amalgam["profession_damage_reference_dps"] == 3_000
    assert "Support / Healing" not in {row["role"] for row in amalgam["roles"]}


def test_single_profession_sample_does_not_create_damage_relative_role(tmp_path):
    path = tmp_path / "fight.json"
    path.write_text(json.dumps({
        "durationMS": 60_000,
        "skillMap": {"s1": {"name": "Healing Mist"}},
        "targets": [
            _target("Amalgam", 200, 12_000, rotation=[
                {"id": 1, "skills": [{}, {}, {}, {}]},
            ]),
        ],
    }), encoding="utf-8")

    amalgam = collect_enemy_role_evidence([path])["professions"]["Amalgam"]

    assert amalgam["roles"] == []
    assert amalgam["profession_baseline_status"] == "insufficient_sample"


def test_bad_or_missing_json_is_ignored(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not-json", encoding="utf-8")
    non_object = tmp_path / "non-object.json"
    non_object.write_text("[]", encoding="utf-8")
    paths = [bad, non_object, tmp_path / "missing.json"]

    result = collect_enemy_role_evidence(paths)
    assert result["enemy_actor_appearances"] == 0
    assert result["professions"] == {}
    assert collect_player_skill_evidence(paths)["players"] == []
    enemy, players = collect_report_evidence(paths)
    assert enemy["enemy_actor_appearances"] == 0
    assert players["players"] == []


def test_combined_report_evidence_reads_each_json_once(tmp_path, monkeypatch):
    path = tmp_path / "fight.json"
    path.write_text(json.dumps({
        "durationMS": 60_000,
        "skillMap": {"s10": {"name": "Flux State"}},
        "players": [{
            "name": "Squad Player", "account": ":squad.1234",
            "profession": "Amalgam",
            "rotation": [{"id": 10, "skills": [{"castTime": 100}]}],
        }],
        "targets": [_target("Reaper", 2_000, 120_000, damage_skill=10)],
    }), encoding="utf-8")
    expected_enemy = collect_enemy_role_evidence([path])
    expected_player = collect_player_skill_evidence([path])
    real_read_text = Path.read_text
    reads = []

    def counted_read_text(self, *args, **kwargs):
        reads.append(self)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read_text)

    enemy, player = collect_report_evidence([path])

    assert reads == [path]
    assert enemy == expected_enemy
    assert player == expected_player


def test_player_skill_casts_are_preserved_separately_from_hit_counts(tmp_path):
    path = tmp_path / "fight.json"
    path.write_text(json.dumps({
        "skillMap": {
            "s10": {"name": "Flux State"},
            "s11": {"name": "Abyssal Blot"},
        },
        "players": [{
            "name": "Sample Hero ACK",
            "account": ":SampleAcct063.0063",
            "profession": "Amalgam",
            "weapons": ["Hammer", "2Hand", "Pistol", "Shield", "Unknown"],
            "rotation": [
                {"id": 10, "skills": [
                    {"castTime": 100}, {"castTime": 300}, {"castTime": 500},
                ]},
                {"id": 11, "skills": [
                    {"castTime": 200}, {"castTime": 400},
                ]},
            ],
        }],
    }), encoding="utf-8")

    result = collect_player_skill_evidence([path])

    assert result["source"] == "detailed_gw2ei_json_player_rotations"
    assert result["players"] == [{
        "account": "SampleAcct063.0063",
        "names": ["Sample Hero ACK"],
        "professions": ["Amalgam"],
        "fight_appearances": 1,
        "skill_casts": {"Flux State": 3, "Abyssal Blot": 2},
        "weapons": ["Hammer", "Pistol", "Shield"],
        "rotation_links": {
            "Flux State → Abyssal Blot": 2,
            "Abyssal Blot → Flux State": 2,
        },
        "consumables": [],
        "consumable_role_signal": None,
        "traits": [],
    }]
    assert "actual pull events and distinct enemies moved are not measured" in result["limitations"]


def test_player_skill_evidence_excludes_non_squad_friendlies_and_friendly_npcs(tmp_path):
    path = tmp_path / "fight.json"
    path.write_text(json.dumps({
        "skillMap": {"s10": {"name": "Flux State"}},
        "players": [
            {
                "name": "Squad Player", "account": ":squad.1234",
                "profession": "Amalgam", "group": 1,
                "rotation": [{"id": 10, "skills": [{"castTime": 100}]}],
            },
            {
                "name": "Nearby Friendly", "account": ":nearby.1234",
                "profession": "Amalgam", "group": 0, "notInSquad": True,
                "rotation": [{"id": 10, "skills": [{"castTime": 100}]}],
            },
            {
                "name": "Friendly NPC", "profession": "Amalgam",
                "group": 1, "friendlyNPC": True,
                "rotation": [{"id": 10, "skills": [{"castTime": 100}]}],
            },
        ],
    }), encoding="utf-8")

    result = collect_player_skill_evidence([path])

    assert [player["account"] for player in result["players"]] == ["squad.1234"]


def test_exact_player_food_utility_and_unique_trait_proc_are_retained(tmp_path):
    path = tmp_path / "fight.json"
    path.write_text(json.dumps({
        "skillMap": {"s24356": {"name": "Poison Nova", "isTraitProc": True}},
        "buffMap": {
            "b100": {"name": "Healer Food", "classification": "Nourishment", "descriptions": ["+100 Healing Power"]},
            "b101": {"name": "Bountiful Maintenance Oil", "classification": "Enhancement", "descriptions": ["+100 Healing Power"]},
        },
        "players": [{
            "name": "Example", "account": ":example.1234", "profession": "Reaper",
            "consumables": [{"id": 100}, {"id": 101}],
            "rotation": [{"id": 24356, "skills": [{"castTime": 100}]}],
        }],
    }), encoding="utf-8")

    player = collect_player_skill_evidence([path])["players"][0]

    assert [row["classification"] for row in player["consumables"]] == ["Enhancement", "Nourishment"]
    assert player["consumable_role_signal"]["role"] == "Healing"
    assert player["consumable_role_signal"]["confidence"] == "strong"
    assert player["traits"][0]["trait"] == "Death Nova"
    assert player["traits"][0]["specialization"] == "Death Magic"
    assert player["traits"][0]["roles"] == ["Damage"]


def test_enemy_food_and_traits_require_observed_buff_and_trait_proc(tmp_path):
    path = tmp_path / "fight.json"
    target = _target("Reaper", 2_000, 120_000, rotation=[
        {"id": 24356, "skills": [{"castTime": 100}]},
        {"id": 99999, "skills": [{"castTime": 200}]},
    ])
    target["buffs"] = [{"id": 100, "buffData": []}, {"id": 101, "buffData": []}]
    path.write_text(json.dumps({
        "durationMS": 60_000,
        "skillMap": {
            "s24356": {"name": "Poison Nova", "isTraitProc": True},
            "s99999": {"name": "Pretend Trait", "isTraitProc": True},
        },
        "buffMap": {
            "b100": {"name": "Damage Food", "classification": "Nourishment", "descriptions": ["+100 Power"]},
            "b101": {"name": "Sharpening Stone", "classification": "Enhancement", "descriptions": ["+100 Power"]},
        },
        "targets": [target],
    }), encoding="utf-8")

    reaper = collect_enemy_role_evidence([path])["professions"]["Reaper"]

    assert {row["name"] for row in reaper["consumables"]} == {"Damage Food", "Sharpening Stone"}
    assert [row["trait"] for row in reaper["traits"]] == ["Death Nova"]
    assert reaper["traits"][0]["actor_appearances"] == 1


def test_generated_trait_catalog_contains_only_unique_major_trait_proofs():
    catalog = json.loads(
        (Path(__file__).parents[1] / "core" / "trait_evidence_catalog.json").read_text(encoding="utf-8")
    )

    assert catalog["counts"]["specializations_scanned"] >= 81
    assert catalog["counts"]["major_traits_scanned"] >= 729
    assert catalog["counts"]["unique_observable_trait_skills"] == len(catalog["skills"])
    assert all(row["proof_rule"] == "unique_major_trait_skill_and_elite_insights_trait_proc" for row in catalog["skills"].values())
    assert catalog["skills"]["24356"]["trait"] == "Death Nova"
