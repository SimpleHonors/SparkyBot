"""Usability contracts for native report rendering."""
import re
from test_report_refresh import render_js, sample_model, board
from support_contract import audit_support


def test_bubble_origin_observations_are_listed_not_plotted():
    model = sample_model()
    support = next(b for b in model['stat_tables'] if b['source_key'] == 'Support-Summary')
    for name, metrics in [('Zero', {'boonstrips': 0, 'condicleanse': 0, 'resurrects': 1}),
                          ('Missing', {'boonstrips': 0, 'resurrects': 0}),
                          ('Overlap', {'boonstrips': 8, 'condicleanse': 11, 'resurrects': 2}),
                          ('Tiny', {'boonstrips': 0.001, 'condicleanse': 0.002}),
                          ('Single axis', {'boonstrips': 1, 'condicleanse': 0, 'resurrects': 0})]:
        support['rows'].append(board('x', metrics, name)['rows'][0])
    html = render_js(model, 'bubbleChart("support")')
    assert set(re.findall(r'data-point-name="([^"]+)"', html)) == {'Alice', 'Overlap', 'Tiny', 'Single axis'}
    assert 'data-overview-name="Missing"' in html
    assert 'data-overview-name="Zero"' not in html
    assert len(re.findall(r'data-bubble ', html)) == 4
    tiny = re.search(r'<circle[^>]*data-point-name="Tiny"[^>]*>', html)[0]
    assert 'stroke-dasharray="2 2"' in tiny
    assert 'data-size=""' in tiny
    assert '<b>Alice</b>' in html and '<b>Overlap</b>' in html
    assert 'Independent linear scales' not in html
    assert 'bubble-lane' not in html
    assert 'data-x="4.8" data-y="6.6" data-size="1.2"' in html


def test_bubble_all_zero_and_missing_are_different_states():
    model = sample_model()
    support = next(b for b in model['stat_tables'] if b['source_key'] == 'Support-Summary')
    support['rows'][0]['metrics'] = {'boonstrips': 0, 'condicleanse': 0, 'resurrects': 0}
    html = render_js(model, 'bubbleChart("support")')
    assert 'data-bubble ' not in html
    assert 'Incomplete data' not in html
    support['rows'][0]['metrics'].pop('condicleanse')
    missing = render_js(model, 'bubbleChart("support")')
    assert 'Incomplete data' in missing and '<b>Alice</b>' in missing
    assert 'data-x="0" data-y="" data-size="0"' in missing
    assert 'NaN' not in html


def test_simple_has_adjacent_total_rate_with_rate_sort_default():
    html = render_js(sample_model(), 'metricGrid("utility",true)')
    assert 'data-metric-mode=' not in html
    assert html.count('data-support-metric=') == 2
    assert html.count('data-sort-key="total"') == 2
    assert html.count('data-sort-key="rate" data-sort-direction="descending"') == 2
    assert 'metric-sort-pair' not in html
    assert 'Cleanses / min' in html and 'Strips / min' in html
    assert 'data-rate="6.6" data-total="11" data-participation="100"' in html
    assert 'data-rate="4.8" data-total="8" data-participation="100"' in html
    assert '>1m 40s</td>' in html
    assert '<b>11</b><small>6.6</small>' not in html
    assert re.search(r'<td title="Cleanses / min: 6.6">6.6<span', html)
    secondary = render_js(sample_model(), 'curatedBoards("utility")')
    stability = next(b for b in secondary if b['source_key'] == 'Stability-Generation')
    assert stability['rows'][0]['total'] == 150 and stability['rows'][0]['rate'] == 90
    for renderer in ['renderSimple()', 'renderSparky()', 'metricGrid("strips")']:
        native = render_js(sample_model(), renderer)
        assert 'metric-sort-pair' not in native
        assert 'data-metric-mode=' not in native
        assert 'data-value-kind="total"' in native and 'data-value-kind="rate"' in native


def test_wall_preview_explains_empty_reference_without_enabling_production():
    model = sample_model()
    model['sparky_wall'] = {'enabled': True, 'preview': True, 'players': []}
    html = render_js(model, 'renderWall()')
    assert 'Preview only' in html
    assert 'no recorded AI commentary' in html
    assert '<blockquote' not in html
    model['sparky_wall']['enabled'] = False
    assert render_js(model, 'wallEnabled()') is False


def test_demo_wall_is_explicitly_fictional_only_in_preview():
    model = sample_model()
    model['sparky_wall'] = {'enabled': True, 'preview': True, 'demo': True, 'players': []}
    html = render_js(model, 'renderWall()')
    assert 'Demo content — fictional players and sample callouts for reviewing this interface. Not historical commentary from this raid.' in html
    assert 'No quotes have been invented' not in html
    assert 'This empty Wall demonstrates' not in html
    assert 'Actual saved fight comments' not in html
    assert 'recorded AI commentary' not in html
    model['sparky_wall']['preview'] = False
    assert 'Demo content' not in render_js(model, 'renderWall()')
    model['sparky_wall']['enabled'] = False
    assert render_js(model, 'wallEnabled()') is False


def test_compact_labels_follow_metric_not_optional_column_index():
    model = sample_model()
    model['stat_tables'] = [b for b in model['stat_tables'] if b['source_key'] != 'Stability-Generation']
    html = render_js(model, 'metricGrid("utility",true)')
    assert '>Stab/sec</button>' not in html
    assert 'Cleanses / min' in html and 'Strips / min' in html
    assert html.count('data-support-metric=') == 2
    secondary = render_js(model, 'curatedBoards("utility")')
    resurrects = next(b for b in secondary if b['stat'] == 'Resurrects')
    cc = next(b for b in secondary if b['stat'] == 'Outgoing Crowd Control')
    assert resurrects['rows'][0]['total'] == 2 and resurrects['rows'][0]['rate'] == 1.2
    assert cc['rows'][0]['total'] == 12 and abs(cc['rows'][0]['rate'] - 7.2) < 1e-12


def test_browser_simultaneous_metrics_and_named_comparisons(tmp_path):
    from playwright.sync_api import sync_playwright
    from core.report_viewer import build_switchable_report
    path = tmp_path / 'report.html'
    path.write_text(build_switchable_report('<p>Classic</p>', sample_model(), default_view='simple'))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(path.as_uri())
        frame = page.frame_locator('#report-frame')
        grid = frame.locator('[data-support-metric="condicleanse"] table')
        audit_support(frame, sample_model(), keyboard=True)
        assert grid.locator('tbody td').nth(0).inner_text() == '11'
        assert grid.locator('tbody td').nth(1).inner_text() == '6.6'
        for kind in ('total', 'rate'):
            header = grid.locator('[data-sort-key="'+kind+'"]')
            header.click()
            assert header.locator('..').get_attribute('aria-sort') in ('ascending', 'descending')
        assert grid.locator('thead tr').count() == 1
        assert grid.locator('td small').count() == 0
        page.locator('[data-view="sparky"]').click()
        frame.locator('[data-tab="dps"]').click()
        chart = frame.locator('[data-bubble-chart="dps"]')
        row = chart.locator('.bubble-identity').first
        assert row.locator('.table-player b').is_visible()
        assert chart.locator('.bubble-identity-table').is_visible()
        icon = row.locator('.profession-glyph')
        assert icon.evaluate('(e)=>getComputedStyle(e).marginLeft') == '0px'
        page.set_viewport_size({'width': 390, 'height': 844})
        assert chart.locator('thead th').nth(1).get_attribute('title') == 'DPS'
        assert chart.locator('thead th').nth(3).get_attribute('title') == 'damage to downed enemies / sec'
        assert chart.locator('tbody td').all_text_contents() == ['100', '30', '9']
        assert row.evaluate('(e)=>e.scrollWidth <= e.clientWidth')
        page.set_viewport_size({'width': 1440, 'height': 1100})
        row.focus()
        assert frame.locator('#chart-popup').is_visible()
        page.keyboard.press('Enter')
        assert frame.locator('#drilldown').is_visible()
        browser.close()


def test_real_source_desktop_mobile_comparison_and_simple(tmp_path):
    import json
    import os
    from pathlib import Path
    from playwright.sync_api import sync_playwright
    from core.night_model import build_night_model
    from core.report_viewer import build_switchable_report
    source = Path('/opt/data/sparkybot-reference/WvW_Combat_Summary.json')
    if not source.exists():
        import pytest
        pytest.skip('Real reference unavailable')
    data = json.loads(source.read_text())
    model = build_night_model(data)
    model['sparky_wall'] = {'preview': True, 'enabled': True, 'players': []}
    classic = source.with_name('LogCombiner.html').read_text().replace('<head>', '<head><script>window.combatData=' + json.dumps(data).replace('<', '\\u003c') + ';</script>', 1)
    artifact = Path(os.environ.get('REPORT_USABILITY_ARTIFACT_DIR') or tmp_path)
    artifact.mkdir(parents=True, exist_ok=True)
    path = artifact / 'real-usability-preview.html'
    path.write_text(build_switchable_report(classic, model, default_view='simple'))
    source_rows = next(b for b in model['stat_tables'] if b['source_key'] == 'Support-Summary')['rows']
    # Independent rankings include observed zero totals, but never invent missing metrics.
    expected = {}
    for metric in ('condicleanse', 'boonstrips'):
        expected[metric] = {
            (r.get('account', '').removeprefix(':'), r['name'], r['profession']):
            (r['metrics'][metric], r['metrics'][metric] / r['participation_time'] * 60
             if r.get('participation_time', 0) > 0 else None)
            for r in source_rows if r['metrics'].get(metric) is not None}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(path.as_uri())
        frame = page.frame_locator('#report-frame')
        frame.locator('[data-metric-grid="damage"]').wait_for()
        page.screenshot(path=str(artifact / 'simple-desktop.png'))
        grid = frame.locator('[data-support-metric="condicleanse"] table')
        grid.scroll_into_view_if_needed()
        grid.locator('xpath=ancestor::article').screenshot(path=str(artifact / 'simple-utility-desktop.png'))
        page.locator('[data-view="sparky"]').click()
        frame.locator('[data-tab="support"]').click()
        audit_support(frame, model, keyboard=True)
        chart = frame.locator('[data-support-rankings]')
        assert frame.locator('[data-bubble-chart="support"]').count() == 0
        assert chart.locator('[data-support-metric]').count() == 2
        for metric, source_values in expected.items():
            ranking = chart.locator('[data-support-metric="'+metric+'"]')
            actual = ranking.locator('[data-support-player]').evaluate_all('''rows=>rows.map(r=>({
                identity:JSON.parse(r.dataset.supportPlayer),name:r.querySelector('.table-player b').textContent,
                total:Number(r.dataset.total),rate:r.dataset.rate===''?null:Number(r.dataset.rate),
                width:parseFloat(r.querySelector('.support-ranking-track i').style.width)}))''')
            assert len(actual) == len(source_values)
            assert {tuple(r['identity']) for r in actual} == set(source_values)
            maximum = max(rate or 0 for total, rate in source_values.values())
            for row in actual:
                total, rate = source_values[tuple(row['identity'])]
                assert row['name'] == row['identity'][1]
                assert row['total'] == total
                assert row['rate'] == rate
                expected_width = rate / maximum * 100 if rate is not None and maximum else 0
                assert abs(row['width'] - expected_width) < .001
            rates = [r['rate'] for r in actual if r['rate'] is not None]
            assert rates == sorted(rates, reverse=True)
            expand = ranking.locator('[data-expand-board]')
            expand.focus()
            expand.press('Enter')
            assert ranking.locator('tbody tr:visible').count() == len(source_values)
            expand.press('Space')
            assert ranking.locator('tbody tr:visible').count() == 5
        assert chart.evaluate('(e)=>e.scrollWidth <= e.clientWidth')
        page.mouse.move(0, 0)
        chart.screenshot(path=str(artifact / 'support-desktop.png'))
        assert chart.locator('.bubble-lane, .bubble-comparison-row').count() == 0
        frame.locator('[data-tab="dps"]').click()
        damage = frame.locator('[data-bubble-chart="dps"]')
        page.mouse.move(0, 0)
        damage.screenshot(path=str(artifact / 'damage-desktop.png'))
        damage.locator('[data-highlight-point]').first.focus()
        assert damage.locator('.point-highlight').count() == 1
        assert frame.locator('#chart-popup').is_visible()
        page.set_viewport_size({'width': 390, 'height': 844})
        frame.locator('[data-tab="support"]').click()
        chart.evaluate('(e)=>e.scrollIntoView({block:"start"})')
        page.mouse.move(0, 0)
        page.screenshot(path=str(artifact / 'support-mobile.png'))
        assert chart.evaluate('(e)=>e.scrollWidth <= e.clientWidth')
        assert chart.locator('tbody tr:visible').count() == 10
        audit_support(frame, model, keyboard=True)
        assert frame.locator('body').evaluate('(e)=>e.scrollWidth <= innerWidth')
        page.locator('[data-view="simple"]').click()
        frame.locator('[data-metric-grid="damage"]').wait_for()
        page.screenshot(path=str(artifact / 'simple-mobile.png'))
        grid = frame.locator('[data-support-metric="condicleanse"] table')
        grid.locator('xpath=ancestor::article').evaluate('(e)=>e.scrollIntoView({block:"start"})')
        page.screenshot(path=str(artifact / 'simple-utility-mobile.png'))
        page.locator('[data-view="wall"]').click()
        assert frame.locator('.wall-preview').is_visible()
        page.screenshot(path=str(artifact / 'wall-mobile.png'))
        assert not errors
        browser.close()
