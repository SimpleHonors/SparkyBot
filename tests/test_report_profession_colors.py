"""Browser regressions for source profession colors, not semantic boon colors."""
import copy
import json

from core.report_viewer import build_switchable_report, unpack_classic_report
from test_report_refresh import sample_model

FAMILIES = {
    'Guardian': 'Guardian Dragonhunter Firebrand Willbender Luminary',
    'Warrior': 'Warrior Berserker Spellbreaker Bladesworn Paragon',
    'Revenant': 'Revenant Herald Renegade Vindicator Conduit',
    'Ranger': 'Ranger Druid Soulbeast Untamed Galeshot',
    'Thief': 'Thief Daredevil Deadeye Specter Antiquary',
    'Engineer': 'Engineer Scrapper Holosmith Mechanist Amalgam',
    'Elementalist': 'Elementalist Tempest Weaver Catalyst Evoker',
    'Mesmer': 'Mesmer Chronomancer Mirage Virtuoso Troubadour',
    'Necromancer': 'Necromancer Reaper Scourge Harbinger Ritualist',
}
# Existing Classic source palette; absent elites must inherit their base.
PALETTE = dict(zip(FAMILIES, ['#3399cc', '#FF9933', '#CC6342', '#66CC33',
                            '#CC6666', '#996633', '#EC5752', '#993399', '#339966']))
PALETTE.update(Firebrand='#6DB6DA', Conduit='#E7B7AE', Unknown='#FFFFFF')


# Native family anchors compared with React's actual bubble palette; Elementalist
# keeps Classic's stronger red rather than its progressively white elite tints.
ANCHORS = dict(zip(FAMILIES, ['#72C1C1', '#FFD166', '#D12705', '#8EEB2E',
                            '#C08F95', '#D09C59', '#EC5752', '#B679D5', '#52A76F']))


def native_color(base, spec):
    anchor = ANCHORS[base]
    shade = PALETTE.get(spec, PALETTE[base])
    return '#' + ''.join(f'{int(int(anchor[i:i+2], 16)*.85 + int(shade[i:i+2], 16)*.15 + .5):02x}' for i in (1,3,5))


def test_browser_all_professions_use_source_palette_in_both_bubbles_and_keys(tmp_path):
    from playwright.sync_api import sync_playwright
    expected = {spec: native_color(base, spec)
                for base, specs in FAMILIES.items() for spec in specs.split()}
    expected.update(Unknown='#FFFFFF', FutureSpec='#FFFFFF')
    model = sample_model()
    for board in model['stat_tables']:
        row = board['rows'][0]
        board['rows'] = [dict(copy.deepcopy(row), name=spec, account=spec, profession=spec)
                         for spec in expected]
    original = copy.deepcopy(model)
    classic = '<script class="tiddlywiki-tiddler-store" type="application/json">' + json.dumps([
        {'title': 'session-boxplot', 'text': 'const ProfessionColor = '+json.dumps(PALETTE)+';'}
    ]) + '</script>'
    path = tmp_path/'colors.html'
    report = build_switchable_report(classic, model)
    assert unpack_classic_report(report) == classic
    assert model == original
    path.write_text(report)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(path.as_uri())
        frame = page.frame_locator('#report-frame')
        for theme in ('blackout', 'graphite', 'midnight', 'studio-light'):
            page.locator('#theme-picker').select_option(theme)
            for group in ('dps',):
                frame.locator('[data-tab="'+group+'"]').click()
                chart = frame.locator('[data-bubble-chart="'+group+'"]')
                actual = chart.evaluate('''e=>Array.from(e.querySelectorAll('[data-bubble]')).map(c=>{
                    const key=e.querySelector('[data-highlight-point="'+c.dataset.pointIndex+'"]');
                    const row=key.closest('.bubble-identity-table tr');
                    return {name:c.dataset.pointName,fill:getComputedStyle(c).fill,opacity:getComputedStyle(c).fillOpacity,
                        key:getComputedStyle(key,'::before').backgroundColor,
                        identity:row.querySelector('.table-player b').textContent};
                })''')
                assert len(actual) == len(expected)
                for point in actual:
                    value = expected[point['name']].lstrip('#')
                    rgb = 'rgb('+', '.join(str(int(value[i:i+2],16)) for i in (0,2,4))+')'
                    assert point['opacity'] == '1', point
                    assert point['fill'] == rgb, point
                    assert point['key'] == rgb, point
                    assert point['identity'] == point['name'], point
            frame.locator('[data-tab="support"]').click()
            assert frame.locator('[data-bubble-chart="support"]').count() == 0
            rankings = frame.locator('[data-support-metric]')
            assert rankings.count() == 2
            for ranking in rankings.all():
                actual = ranking.locator('[data-support-player]').evaluate_all('''rows=>rows.map(r=>({
                    name:r.querySelector('.table-player b').textContent,
                    identity:r.dataset.supportPlayer,
                    fill:getComputedStyle(r.querySelector('.support-ranking-track i')).backgroundColor,
                    opacity:getComputedStyle(r.querySelector('.support-ranking-track i')).opacity
                }))''')
                assert len(actual) == len(expected)
                for point in actual:
                    value = expected[point['name']].lstrip('#')
                    rgb = 'rgb('+', '.join(str(int(value[i:i+2],16)) for i in (0,2,4))+')'
                    assert point['fill'] == rgb and point['opacity'] == '1', point
                    assert json.loads(point['identity']) == [point['name']] * 3, point
        assert not errors
        browser.close()


def test_palette_missing_invalid_unknown_and_new_spec_fallbacks():
    from test_report_refresh import render_js
    from core.report_viewer import _source_profession_colors
    assert render_js({}, 'professionColor("FutureSpec")') == 'var(--faint)'
    for base, specs in FAMILIES.items():
        for spec in specs.split():
            assert render_js({}, 'professionColor('+json.dumps(spec)+')') == 'var(--'+base.lower()+')'
    bad = '<script type="application/json">' + json.dumps([
        {'text': 'const ProfessionColor = {"Guardian":"url(https://invalid)","Firebrand":"#6DB6DA"};'},
        {'text': 'const ProfessionColor = {"Mesmer":alert(1)};'},
    ]) + '</script>'
    assert _source_profession_colors(bad) == {'firebrand': '#6DB6DA'}
    assert render_js({'profession_colors': {'guardian': 'url(https://invalid)'}},
                     'professionColor("Luminary")') == 'var(--guardian)'
