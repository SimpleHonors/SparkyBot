"""Combiner transport regression: real EI colors, never its stale ID palette."""
import json
from pathlib import Path

from core import raid_report


def test_staged_ei_colors_follow_each_fights_map_without_changing_source(tmp_path):
    source = tmp_path / 'fight.json'
    data = {'wvWMapData': {'redTeamID': 707, 'blueTeamID': 433,
                          'greenTeamID': 2778, 'mapId': 38},
            'targets': [{'teamID': 2778, 'name': 'Warrior one'},
                        {'teamID': 707, 'name': 'Guardian two'}],
            'players': [{'teamID': 433}], 'timeEnd': '2026-09-19 02:00:00 +00:00'}
    source.write_text(json.dumps(data))
    original = source.read_bytes()
    dest = tmp_path / 'staged' / source.name
    dest.parent.mkdir()
    # Upstream transport IDs, exercised against v1.9 in the real pipeline.
    # Exercise the same staging helper used by generate().
    stage = raid_report._stage_combiner_json
    stage(source, dest)
    staged = json.loads(dest.read_text())
    assert staged['targets'][0]['teamID'] == 2739
    assert staged['targets'][1]['teamID'] == 705
    assert staged['players'][0]['teamID'] == 432
    assert staged['wvWMapData'] == {'redTeamID': 705, 'blueTeamID': 432,
                                    'greenTeamID': 2739, 'mapId': 38}
    assert staged['timeEnd'] == data['timeEnd']
    assert source.read_bytes() == original
    # The very same ID can change color next fight; no report-global override.
    data['wvWMapData']['redTeamID'], data['wvWMapData']['greenTeamID'] = 2778, 707
    source.write_text(json.dumps(data))
    stage(source, dest)
    assert json.loads(dest.read_text())['targets'][0]['teamID'] == 705


def test_unknown_or_conflicting_ids_never_inherit_static_colors(tmp_path):
    import pytest
    source, dest = tmp_path / 'source.json', tmp_path / 'staged.json'
    mapping = {'redTeamID': 707, 'greenTeamID': 2778, 'blueTeamID': 433}
    for maps, team_id in [
        ({'wvWMapData': mapping}, 705),  # static Red, but not this fight's Red
        ({'wvWMapData': mapping}, '707'),
        ({'wvWMapData': mapping}, True),
        ({'wvWMapData': mapping}, 0),
        ({'wvWMapData': mapping}, -1),
        ({}, 707),
        ({'wvWMapData': {**mapping, 'greenTeamID': 707}}, 707),
        ({'wvWMapData': mapping, 'wvwMapData': {**mapping, 'redTeamID': 2778,
                                               'greenTeamID': 707}}, 707),
    ]:
        source.write_text(json.dumps({**maps, 'targets': [{'teamID': team_id}]}))
        original = source.read_bytes()
        raid_report._stage_combiner_json(source, dest)
        assert json.loads(dest.read_text())['targets'][0]['teamID'] == 0
        assert source.read_bytes() == original  # original unknown ID preserved
    with pytest.raises(ValueError, match='original EI'):
        raid_report._stage_combiner_json(source, source)
    assert source.read_bytes() == original


def test_legacy_mapping_agreement_and_non_team_metrics_preserved(tmp_path):
    mapping = {'redTeamID': 707, 'greenTeamID': 2778, 'blueTeamID': 433}
    source, dest = tmp_path / 'source.json', tmp_path / 'staged.json'
    for maps in [{'wvwMapData': mapping}, {'wvWMapData': mapping, 'wvwMapData': mapping}]:
        data = {**maps, 'targets': [{'teamID': 2778, 'totalDamageDist': [[{'id': 707,
                    'totalDamage': 12345}]], 'name': 'Warrior x'}], 'players': [],
                'durationMS': 100000, 'skillMap': {'s707': {'name': 'literal'}}}
        source.write_text(json.dumps(data))
        raid_report._stage_combiner_json(source, dest)
        staged = json.loads(dest.read_text())
        assert staged['targets'][0]['teamID'] == 2739
        assert staged['targets'][0]['totalDamageDist'] == data['targets'][0]['totalDamageDist']
        assert staged['skillMap'] == data['skillMap']
        assert staged['durationMS'] == data['durationMS']
        assert staged['wvWMapData']['greenTeamID'] == 2739


def test_generate_sends_transport_copy_but_evidence_reads_original(tmp_path, monkeypatch):
    from datetime import datetime
    from unittest.mock import Mock
    from core.raid_session import LogInfo
    from core.report_viewer import unpack_night_model
    source = tmp_path / 'original.json'
    source.write_text(json.dumps({'wvWMapData': {'greenTeamID': 2778,
                     'redTeamID': 707, 'blueTeamID': 433},
                     'targets': [{'teamID': 2778}], 'players': []}))
    original = source.read_bytes()
    log = LogInfo(tmp_path / 'log.zevtc', datetime(2026, 9, 19), 'filename')
    monkeypatch.setattr(raid_report, 'plan_report', lambda *a: ([(log, source)], []))
    evidence = raid_report.collect_report_evidence
    seen = []
    def collect(paths):
        assert paths == [source]
        assert json.loads(paths[0].read_text())['targets'][0]['teamID'] == 2778
        seen.append(True)
        return evidence(paths)
    monkeypatch.setattr(raid_report, 'collect_report_evidence', collect)
    def combine(input_dir, run_dir, **kwargs):
        assert json.loads((input_dir / source.name).read_text())['targets'][0]['teamID'] == 2739
        out = input_dir / 'combined.json'
        out.write_text(json.dumps([{'title': 'fixture-Overview', 'text': '|!#|!Time|h\n|1|2026-09-19 - 20:01:00|'}]))
        out.with_suffix('.html').write_text('<html><head></head><body>Classic fixture</body></html>')
        return out
    combiner = Mock()
    combiner.run.side_effect = combine
    runner = raid_report.RaidReportRunner(log_folder=tmp_path, cache=Mock(),
        parse_log=Mock(), ei_version='x', settings_fingerprint='x',
        combiner=combiner, viewer_html=tmp_path / 'viewer.html', output_dir=tmp_path / 'out')
    result = runner.generate([log])
    assert seen == [True]
    assert source.read_bytes() == original
    assert unpack_night_model(result.html_path.read_text())['sparky_wall']['enabled'] is False
