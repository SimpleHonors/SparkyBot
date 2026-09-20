"""Actual EI schema and raw-composition regressions (no static world palette)."""
import json

from core.enemy_role_evidence import _enemy_team, collect_report_evidence
from core.enemy_intel import build_enemy_intel


def test_actual_ei_casing_and_conflicting_legacy_alias():
    mapping = {"redTeamID": 707, "greenTeamID": 2778, "blueTeamID": 433}
    for team, color in [(707, "red"), (2778, "green"), (9999, "unknown")]:
        assert _enemy_team({"teamID": team}, {"wvWMapData": mapping}) == (team, color)
        assert _enemy_team({"teamID": team}, {"wvwMapData": mapping}) == (team, color)
    assert _enemy_team({"teamID": 2778}, {"wvWMapData": mapping,
        "wvwMapData": {"redTeamID": 2778}}) == (2778, "unknown")


def test_fallback_unknown_and_unrecognized_caption_boundaries():
    tiddlers = [{"title": "night-Squad-Composition", "text": (
        "| Fight - 1: Red Composition |c\n| {{Holosmith}}: 2 |\n"
        "| Fight - 1: Unk Composition |c\n| {{Tempest}}: 3 |\n"
        "| Fight - 2: Unk Composition |c\n| {{Druid}}: 4 |\n"
        "| Fight - 2: Squad Composition |c\n| {{Firebrand}}: 40 |\n"
        "| Fight - 3: Red Composition |c\n| {{Holosmith}}: 1 |\n"
        "</$reveal>\n| {{Firebrand}}: 90 |\n"
    )}]
    intel = build_enemy_intel(tiddlers, [{"index": i} for i in (1, 2, 3)])
    counts = {(s['index'], s['color']): s['observed_profession_count'] for s in intel['fights']}
    assert counts == {(1, 'red'): 2, (1, 'unknown'): 3, (2, 'unknown'): 4, (3, 'red'): 1}


def test_raw_composition_overrides_captions_by_end_clock_and_shares_team_evidence(tmp_path):
    def target(team, instance, active=30000, **extra):
        return dict({'name': 'Holosmith invader', 'profession': 'Holosmith', 'enemyPlayer': True,
                     'teamID': team, 'instanceID': instance, 'activeTimes': [active]}, **extra)
    path = tmp_path / 'fight.json'
    path.write_text(json.dumps({
        'timeStart': '2026-09-19 02:00:00 +00', 'timeEnd': '2026-09-19 02:01:00 +00',
        'durationMS': 60000,
        'wvWMapData': {'redTeamID': 707, 'greenTeamID': 2778, 'blueTeamID': 433},
        'targets': [target(707, 1, rotation=[{'id': 41684, 'skills': [{}]}]),
                    target(2778, 2), target(2778, 3, 100), target(9999, 4),
                    target(2778, 2), target(433, 5, enemyPlayer=False), target(433, 6)],
        'players': [{'name': 'Squad', 'instanceID': 6}],
        'skillMap': {'s41684': {'isTraitProc': True}},
    }))
    evidence, _ = collect_report_evidence([path, path])
    assert evidence['enemy_actor_appearances'] == 4  # duplicate file/actor and squad excluded
    fights = [{'index': 9, 'time_label': '2026-09-19 - 02:01:00 - BAB',
               'rgb': {'r': 99, 'g': 0, 'b': 0}}, {'index': 10}]
    captions = [{'title': 'night-Squad-Composition', 'text':
        '| Fight - 9: Red Composition |c\n| {{Holosmith}}: 99 |\n'
        '| Fight - 10: Blue Composition |c\n| {{Druid}}: 1 |'}]
    intel = build_enemy_intel(captions, fights, actor_role_evidence=evidence)
    rows = {(s['index'], s['color']): s for s in intel['fights']}
    assert {k: v['enemy_count'] for k, v in rows.items()} == {
        (9, 'red'): 1, (9, 'green'): 2, (9, 'unknown'): 1, (10, 'blue'): 1}
    assert rows[(9, 'unknown')]['team_ids'] == [9999]
    scopes = {s['id']: s for s in intel['scopes']}
    green = scopes['green']['role_validation']['professions']['Holosmith']
    assert green['actor_appearances'] == 2 and green['trait_eligible_appearances'] == 1
    assert green['traits'] == []
    red = scopes['red']['role_validation']['professions']['Holosmith']
    assert red['traits'][0]['observed_percent'] == 100
    assert rows[(9, 'green')]['estimated_subgroups'][0]['members'][0]['role_inference']['build_evidence'] == []
