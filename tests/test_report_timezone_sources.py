"""Original EI end offsets survive the external combiner's lossy labels."""
import json
from core.enemy_role_evidence import collect_report_evidence
from core.night_model import build_night_model
from test_report_refresh import render_js


def pipeline(tmp_path, sources, labels):
    paths = []
    for i, source in enumerate(sources):
        p = tmp_path / f'{i}.json'
        p.write_text(json.dumps({'timeStart': '2026-09-25 20:00:00 +00', **source}))
        paths.append(p)
    evidence, skills = collect_report_evidence(paths)
    tiddlers = [{'title': 'test-Overview', 'text': '|!#|!Fight Link|!Duration|h\n' + '\n'.join(
        f'|{i}|{label}|34s|' for i, label in enumerate(labels, 1))}]
    return build_night_model(tiddlers, enemy_role_evidence=evidence, player_skill_evidence=skills)


def test_original_end_offset_reaches_shared_renderer(tmp_path):
    label = '2026-09-25 - 21:12:00 - GAB'
    model = pipeline(tmp_path, [{'timeEnd': '2026-09-25 21:12:00 -05'}], [label])
    assert model['fights'][0]['time_label'] == label
    assert render_js(model, 'fightClock(model.fights[0].time_label)') == '9:12 PM'


def test_shuffled_mixed_sources_join_end_not_start(tmp_path):
    labels = ['2026-09-25 - 21:12:00 - GAB', '2026-09-26 - 02:15:00 - EBG']
    sources = [{'timeStart': '2026-09-25 21:12:00 -05', 'timeEnd': '2026-09-26 02:15:00 +00'},
               {'timeStart': '2026-09-25 21:11:26 -05', 'timeEnd': '2026-09-25 21:12:00 -05'}]
    model = pipeline(tmp_path, sources, labels)
    reverse = pipeline(tmp_path, list(reversed(sources)), labels)
    assert model['timestamp_sources'] == reverse['timestamp_sources']
    assert render_js(model, 'model.fights.map(f=>fightClock(f.time_label))') == ['9:12 PM', '9:15 PM']
    model['session'] = {'display_timezone': 'UTC'}
    assert render_js(model, 'model.fights.map(f=>fightClock(f.time_label))') == ['2:12 AM', '2:15 AM']


def test_missing_ambiguous_and_offsetless_sources_remain_literal(tmp_path):
    label = '2026-09-25 - 21:12:00 - GAB'
    for sources in ([], [{'timeEnd': '2026-09-25 21:12:00'}],
                    [{'timeEnd': '2026-09-25 21:12:00 -05'}, {'timeEnd': '2026-09-25 21:12:00 +00'}],
                    [{'timeStart': '2026-09-25 21:12:00 -05', 'timeEnd': '2026-09-25 21:13:00 -05'}]):
        model = pipeline(tmp_path, sources, [label])
        assert model['timestamp_sources'] == {}
        assert render_js(model, '[fightClock(model.fights[0].time_label),reportInstant(model.fights[0].time_label)]') == [label, None]
    model = pipeline(tmp_path, [{'timeEnd': '2026-09-25 21:12:00 -05'}], [label, label.replace('GAB', 'EBG')])
    assert model['timestamp_sources'] == {}


def test_explicit_offsets_win_and_dst_is_display_zone_only(tmp_path):
    values = ['2026-09-26T02:12:00Z', '2026-09-26 02:12:00 +00',
              '2026-09-25 21:12:00 -05', '2026-09-25T21:12:00-05:00',
              '2026-09-25T21:12:00-0500']
    model = pipeline(tmp_path, [{'timeEnd': '2026-09-25 21:12:00 +00'}], values)
    assert model['timestamp_sources'] == {}
    assert render_js(model, 'model.fights.map(f=>fightClock(f.time_label))') == ['9:12 PM'] * 5
    assert render_js(model, '[fightClock("2026-03-08T07:59:00Z"),fightClock("2026-03-08T08:01:00Z")]') == ['1:59 AM', '3:01 AM']
    assert render_js(model, '[fightClock("2026-11-01T06:59:00Z"),fightClock("2026-11-01T07:01:00Z")]') == ['1:59 AM', '1:01 AM']


def test_all_native_consumers_share_resolved_instant(tmp_path):
    label = '2026-09-25 - 21:12:00 - GAB'
    model = pipeline(tmp_path, [{'timeEnd': '2026-09-25 21:12:00 -05'}], [label])
    model['fights'][0]['report_url'] = 'https://example.invalid/fixture'
    model['sparky_wall'] = {'enabled': False, 'players': []}
    for expression in ['renderSimple()', 'renderSparky()', 'fightsTable(true)',
                       'fightDrillHtml(model.fights[0])', 'summaryDrillHtml("fights")']:
        rendered = render_js(model, expression)
        assert '9:12 PM' in rendered
        assert '4:12 PM' not in rendered
    instant = render_js(model, 'reportInstant(model.fights[0].time_label).getTime()')
    assert f'data-time="{instant}"' in render_js(model, 'fightsTable(true)')
