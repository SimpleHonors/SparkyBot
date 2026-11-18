"""Public-existing slice of the accepted decode-reuse regressions; synthetic only."""
import os


import sys


from pathlib import Path


import json


from datetime import datetime


from types import SimpleNamespace


import pytest


from core import apppaths


from core import raid_report as rr


from core import enemy_role_evidence as evidence
from core import fight_outcomes as outcomes


@pytest.fixture(autouse=True)
def private_app_state(tmp_path, monkeypatch):
    # Scope every imported alias to this test and let monkeypatch restore it.
    from core import fight_links, sparky_wall
    state = tmp_path / 'appstate'
    state.mkdir()
    for module in (apppaths, fight_links, sparky_wall):
        monkeypatch.setattr(module, 'app_dir', lambda: state)


def document(index=1, **changes):
    data = {
        'synthetic_fixture': index, 'fightName': 'Synthetic map', 'durationMS': 20000,
        'timeStartStd': f'2026-01-01 00:0{index}:00 +00:00',
        'timeEnd': f'2026-01-01 00:0{index}:20 +00:00',
        'wvWMapData': {'redTeamID': 432, 'blueTeamID': 705, 'greenTeamID': 99},
        'players': [{'name': 'Synthetic Player', 'account': 'Synthetic.0001',
                     'profession': 'Firebrand', 'teamID': 705, 'instanceID': 1,
                     'group': 2, 'defenses': [{'deadCount': 0}]}],
        'targets': [{'name': 'Tempest pl-2', 'profession': 'Tempest', 'instanceID': 2,
                     'enemyPlayer': True, 'teamID': 432, 'activeTimes': [20000],
                     'dpsAll': [{'damage': 20000, 'dps': 1000}]}],
        'phases': [{'start': 0, 'end': 20000}],
    }
    data.update(changes)
    return data


def write_doc(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return path


@pytest.fixture
def run_case(tmp_path, monkeypatch):
    events, rendered = [], []

    class Combiner:
        def ensure_installed(self):
            events.append('ensure')

        def write_run_config(self, *args):
            events.append('config')

        def run(self, input_dir, run_dir, **kwargs):
            if self.inspect_inputs:
                self.inspect_inputs([(p.name, json.loads(p.read_bytes()))
                                     for p in sorted(input_dir.glob('*.json'))])
            events.append('combine')
            run_dir.mkdir(parents=True, exist_ok=True)
            output = run_dir / 'synthetic.json'
            output.write_text('[]', encoding='utf-8')
            output.with_suffix('.html').write_text('<html><head></head><body>Synthetic</body></html>', encoding='utf-8')
            return output

    def execute(paths, *, ai=False, hits=None, parsed=None, **options):
        paths = list(paths)
        logs = [rr.LogInfo(tmp_path / f'synthetic-{i}.zevtc', datetime(2026, 1, 1), 'synthetic')
                for i in range(len(paths))]
        mapping = dict(zip((log.path for log in logs), paths))
        combiner = Combiner()
        combiner.inspect_inputs = options.pop('inspect_inputs', None)
        logs = options.pop('logs', logs)
        options.setdefault('cache', SimpleNamespace(lookup=lambda path, *args: mapping[path]))
        options.setdefault('parse_log', lambda path: pytest.fail('parser invoked'))
        runner = rr.RaidReportRunner(
            log_folder=tmp_path,
            ei_version='synthetic', settings_fingerprint='synthetic', combiner=combiner,
            viewer_html=tmp_path / 'unused.html', output_dir=tmp_path / 'output',
            ai_enabled=ai,
            progress=lambda stage, done, total, msg: events.append((stage, done, total)), **options)
        if hits is not None:
            monkeypatch.setattr(rr, 'plan_report', lambda *args: ([(logs[i], paths[i]) for i in hits], [logs[i] for i in parsed]))
            monkeypatch.setattr(runner, '_parse_missing_logs', lambda *args, **kw: ([paths[i] for i in parsed], []))
        return runner.generate(logs, 'Synthetic report', work_dir=tmp_path / 'staging')

    monkeypatch.setattr(rr, 'convert_report_file', lambda *args, **kwargs: rendered.append((args, kwargs)))
    return execute, events, rendered


def test_runner_decodes_each_staging_occurrence_once(tmp_path, monkeypatch, run_case):
    paths = [write_doc(tmp_path, f'input-{i}.json', document(i)) for i in (1, 2)]
    original = [p.read_bytes() for p in paths]
    expected_evidence = evidence.collect_report_evidence(paths)
    expected_outcomes = outcomes.collect_fight_outcomes(paths)
    original_ids = []
    feed = outcomes._FightOutcomesAccumulator.feed

    def observe(accumulator, data, path):
        original_ids.append(data['targets'][0]['teamID'])
        return feed(accumulator, data, path)

    monkeypatch.setattr(outcomes._FightOutcomesAccumulator, 'feed', observe)
    loads = json.loads
    decoded = []
    texts = {p.read_text(encoding='utf-8') for p in paths}

    def counted(text, *args, **kwargs):
        if text in texts:
            decoded.append(text)
        return loads(text, *args, **kwargs)

    monkeypatch.setattr(json, 'loads', counted)
    execute, events, rendered = run_case
    staged = []
    result = execute(paths, inspect_inputs=staged.extend)
    assert len(decoded) == len(paths), 'native evidence/outcomes must reuse each original staging decode'
    assert original_ids == [432, 432]
    assert [d['targets'][0]['teamID'] for _, d in staged] == [705, 705]
    assert [p.read_bytes() for p in paths] == original
    assert rendered[0][1]['enemy_role_evidence'] == expected_evidence[0]
    assert rendered[0][1]['player_skill_evidence'] == expected_evidence[1]
    assert rendered[0][1]['fight_outcomes'] == expected_outcomes
    assert result.html_path.is_file()
    assert events.index('combine') < events.index(('view', 0, 1))


def test_runner_releases_original_metadata_before_next_decode(tmp_path, monkeypatch, run_case):
    import weakref

    class Tracked(dict):
        pass

    paths = [write_doc(tmp_path, f'metadata-{i}.json', document(i, timeEnd={'literal': i}))
             for i in (1, 2)]
    refs = []
    loads = json.loads

    def tracked(text, *args, **kwargs):
        result = loads(text, *args, **kwargs)
        if isinstance(result, dict) and 'synthetic_fixture' in result:
            assert all(ref() is None for ref in refs), 'previous original EI subtree retained'
            result = Tracked(result)
            result['timeEnd'] = Tracked(result['timeEnd'])
            refs.extend((weakref.ref(result), weakref.ref(result['timeEnd'])))
        return result

    monkeypatch.setattr(json, 'loads', tracked)
    run_case[0](paths)
    assert all(ref() is None for ref in refs)


def test_runner_embeds_current_and_historical_measures_in_synthetic_report(tmp_path, monkeypatch, run_case):
    from core.report_viewer import convert_report_file, unpack_night_model, unpack_classic_report
    monkeypatch.setattr(rr, 'convert_report_file', convert_report_file)
    data = document()
    data['players'][0]['defenses'] = [{'deadCount': 1, 'receivedCrowdControl': 4, 'stunBreak': 1}]
    path = write_doc(tmp_path, 'loss.json', data)
    result = run_case[0]([path], dpsreport_links_enabled=False)
    html = result.html_path.read_text(encoding='utf-8')
    payload = unpack_night_model(html)['fight_outcomes']
    assert payload['summary']['losses_avg']['stuns_broken_pct'] == 25
    assert payload['past_fights']['stuns_broken_pct']['low_won_pct'] == 28
    assert 'Synthetic' in unpack_classic_report(html)


@pytest.mark.parametrize('durations,events,expected', [
    ([0], [4], None), ([-1000], [4], None), ([60000], [0], 0),
    ([0, 60000, 120000], [4, 0, 8], 2),
])
def test_group_cc_rate_requires_positive_exposure(tmp_path, durations, events, expected):
    paths = []
    for i, (duration, cc) in enumerate(zip(durations, events), 1):
        data = document(i, durationMS=duration)
        data['players'][0]['defenses'] = [{'deadCount': 1, 'receivedCrowdControl': cc}]
        paths.append(write_doc(tmp_path, f'loss-{i}.json', data))
    payload = outcomes.collect_fight_outcomes(paths)
    key = 'cc_received_per_member_min'
    assert [f[key] for f in payload['fights']] == [
        cc * 60000 / ms if ms > 0 else None for cc, ms in zip(events, durations)]
    assert payload['summary']['losses_avg'][key] == expected
    assert payload['summary']['losses'] == len(paths)
    # Party rate is pooled over measurable member-minutes, not a mean of rates.
    assert payload['parties'][0][key] == (8 / 3 if len(paths) == 3 else expected)


def test_unpacked_outcomes_keep_only_typed_metadata(tmp_path, monkeypatch, run_case):
    from core.report_viewer import convert_report_file, unpack_night_model
    monkeypatch.setattr(rr, 'convert_report_file', convert_report_file)
    sentinel = 'synthetic-private-metadata'
    nested = {'unused': [sentinel]}
    data = document(timeStartStd=nested, timeStart='2026-01-01 00:01:00 -05',
                    timeEnd=nested, fightName=sentinel)
    player = data['players'][0]
    player['damageTaken1S'] = [[0, 500]]
    data['players'] += [dict(player, group=value) for value in
                        (nested, [sentinel], sentinel, True, 2.5, None, -1)]
    other = document(2, timeStartStd=sentinel, timeEnd=sentinel, fightName=nested)
    paths = [write_doc(tmp_path, f'meta-{i}.json', d) for i, d in enumerate((data, other))]
    html = run_case[0](paths).html_path.read_text()
    payload = unpack_night_model(html)['fight_outcomes']
    assert sentinel not in json.dumps(payload)
    assert len(payload['fights']) == 2
    unknown, valid = payload['fights']
    assert not {'time_start', 'time_end', 'map'} & unknown.keys()
    assert valid['time_start'] == data['timeStart']
    assert 'time_end' not in valid and 'map' not in valid
    assert valid['squad'] == len(data['players'])
    assert list(valid['party_stab_at_worst_hit']) == ['2']
    assert [p['group'] for p in payload['parties']] == [2]


def test_evidence_helper_failure_is_deferred_until_after_combine(tmp_path, monkeypatch, run_case):
    paths = [write_doc(tmp_path, f'failure-{i}.json', document(i)) for i in (1, 2)]
    seen = []

    def fail(data, actors):
        seen.append(data['synthetic_fixture'])
        raise ValueError('synthetic extraction failure')

    monkeypatch.setattr(evidence, '_accumulate_enemy_role_evidence', fail)
    with pytest.raises(RuntimeError, match='report views') as caught:
        run_case[0](paths)
    assert isinstance(caught.value.__cause__, ValueError)
    assert str(caught.value.__cause__) == 'synthetic extraction failure'
    assert seen == [1]
    assert 'combine' in run_case[1]
    assert ('collect', 2, 2) in run_case[1]
    assert ('view', 0, 1) in run_case[1]
    assert not run_case[2]


def test_outcomes_analyse_failure_skips_only_one_file(tmp_path, monkeypatch, run_case):
    paths = [write_doc(tmp_path, f'outcome-{i}.json', document(i)) for i in (1, 2)]
    expected = outcomes.collect_fight_outcomes(paths[1:])
    analyse = outcomes._analyse

    def partial(data):
        if data['synthetic_fixture'] == 1:
            raise ValueError('synthetic per-file outcome failure')
        return analyse(data)

    monkeypatch.setattr(outcomes, '_analyse', partial)
    run_case[0](paths)
    assert run_case[2][0][1]['fight_outcomes'] == expected


@pytest.mark.parametrize('failure_at', ['aggregate', 'finish'])
def test_other_outcomes_failure_is_optional(tmp_path, monkeypatch, run_case, failure_at):
    path = write_doc(tmp_path, 'optional.json', document())
    if failure_at == 'finish':
        def fail(*args):
            raise ValueError('synthetic finalization failure')
        monkeypatch.setattr(outcomes, '_finish_fight_outcomes', fail)
    else:
        analyse = outcomes._analyse
        monkeypatch.setattr(outcomes, '_analyse', lambda data: (analyse(data)[0], {'2': None}))
    result = run_case[0]([path])
    assert result.html_path.is_file()
    assert run_case[2][0][1]['fight_outcomes'] is None


def assert_stage_agreement(run_case, paths, unique, *, evidence_paths=None, **options):
    before = {p: p.read_bytes() for p in unique}
    staged = []
    run_case[0](paths, inspect_inputs=staged.extend, **options)
    documents = [d for _, d in staged]
    expected = [json.loads(before[p]) for p in unique]
    assert sorted(d['synthetic_fixture'] for d in documents) == sorted(
        d['synthetic_fixture'] for d in expected)
    assert len({name.casefold() for name, _ in staged}) == len(unique)
    assert all(d['targets'][0]['teamID'] == 705 for d in documents)
    native = run_case[2][-1][1]
    assert [f['time_start'] for f in native['fight_outcomes']['fights']] == sorted(
        d['timeStartStd'] for d in expected)
    assert native['fight_outcomes'] == outcomes.collect_fight_outcomes(unique)
    assert (native['enemy_role_evidence'], native['player_skill_evidence']) == evidence.collect_report_evidence(evidence_paths or unique)
    assert {p: p.read_bytes() for p in unique} == before


def test_same_named_raw_cache_hit_miss_and_alias(tmp_path, run_case):
    from core.raid_session import RaidReportCache
    name = '20260101-000000.zevtc'
    a, b = [tmp_path / folder / name for folder in ('a', 'b')]
    for raw in (a, b):
        raw.parent.mkdir()
        raw.write_bytes(raw.parent.name.encode())
    alias = tmp_path / 'alias.zevtc'
    alias.symlink_to(b)
    cache = RaidReportCache(tmp_path / 'cache')
    keys = ('synthetic', 'synthetic')
    legacy = cache.cache_root / '20260101' / f'{a.stem}__synthetic__synthetic.json'
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b'legacy, unbound to any raw')
    assert cache.lookup(a, *keys) is None
    sources = [write_doc(tmp_path, f'original-{i}.json', document(i)) for i in (1, 2)]
    old_bytes = [p.read_bytes() for p in sources]
    claim = tmp_path / 'claim.json'
    claim.write_bytes(old_bytes[0])
    cached_a = cache.store(a, claim, *keys)
    assert cache.lookup(b, *keys) is None
    parsed = []

    def parse(path):
        parsed.append(path)
        assert path.resolve() == b
        claim.write_bytes(old_bytes[1])
        return claim

    logs = [rr.LogInfo(p, datetime(2026, 1, 1), 'synthetic') for p in (b, a, alias, b)]
    cached_b = cache.path_for(b, *keys)
    for order in ([cached_a, cached_b], [cached_b, cached_a]):  # hits precede parses
        assert_stage_agreement(run_case, sources, sources, logs=logs, cache=cache,
                               parse_log=parse, evidence_paths=order)
        assert parsed == [b]
        assert cache.lookup(b, *keys) == cache.lookup(alias, *keys) != cached_a
        assert [cache.lookup(p, *keys).read_bytes() for p in (a, b)] == old_bytes
        assert [p.read_bytes() for p in (a, b)] == [b'a', b'b']
        assert legacy.read_bytes() == b'legacy, unbound to any raw'


def test_alias_and_repeated_path_reach_combiner_once(tmp_path, run_case):
    first = write_doc(tmp_path, 'first.json', document())
    alias = tmp_path / 'alias.json'
    alias.symlink_to(first)
    assert_stage_agreement(run_case, [first, alias, first], [first])


@pytest.mark.parametrize('name', ['same.json', 'SAME.json'])
def test_colliding_basenames_keep_both_fights(tmp_path, run_case, name):
    first = write_doc(tmp_path, 'first/same.json', document(1))
    second = write_doc(tmp_path, 'second/' + name, document(2))
    assert_stage_agreement(run_case, [second, first], [first, second], hits=[1], parsed=[0])
    assert [e for e in run_case[1] if isinstance(e, tuple) and e[0] == 'collect'] == [
        ('collect', i, 2) for i in range(3)]


@pytest.mark.parametrize('raw,kind', [
    (b'not-json', json.JSONDecodeError), (b'\xff', UnicodeDecodeError),
    (b'\xef\xbb\xbf{}', json.JSONDecodeError), (b'[]', AttributeError), (b'null', AttributeError),
])
def test_invalid_staging_inputs_abort_before_combiner(tmp_path, run_case, raw, kind):
    path = tmp_path / 'invalid.json'
    path.write_bytes(raw)
    with pytest.raises(kind):
        run_case[0]([path])
    assert 'combine' not in run_case[1]
    assert not run_case[2]


@pytest.mark.parametrize('prepared', [False, True])
def test_source_equals_destination_fails_before_read(tmp_path, monkeypatch, prepared):
    path = tmp_path / 'not-even-created.json'
    monkeypatch.setattr(Path, 'read_text', lambda *args, **kwargs: pytest.fail('read before guard'))
    stage = rr._ReportPreparation().stage if prepared else rr._stage_combiner_json
    with pytest.raises(ValueError, match='overwrite'):
        stage(path, path)


def test_ai_off_keeps_saved_wall_payload_disabled(tmp_path, run_case):
    from core.sparky_wall import WALL_TIDDLER
    path = write_doc(tmp_path, 'disabled.json', document())
    result = run_case[0]([path], ai=False)
    saved = json.loads(result.json_path.read_text())
    payload = json.loads(next(t['text'] for t in saved if t['title'] == WALL_TIDDLER))
    assert payload == {'enabled': False, 'coverage_basis': 'ei.timeEnd', 'fights': [], 'entries': []}
