"""Execute the generated offline viewer's real renderers in Node (no JS mocks)."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from core.report_viewer import build_switchable_report, unpack_classic_report


def render_js(model, expression):
    report = build_switchable_report('<title>Original</title><p>Classic</p>', model)
    script = re.findall(r'<script>(.*?)</script>', report, re.S)[-1]
    # Stop before startup; retain every production renderer and event handler.
    script = script.rsplit('  Array.from(document.querySelectorAll("[data-view]"))', 1)[0]
    prelude = 'const document={getElementById:()=>({textContent:"{}"})};\n'
    script += '\nmodel=' + json.dumps(model) + ';\nconsole.log(JSON.stringify(' + expression + '));})();'
    result = subprocess.run([shutil.which('node') or 'node', '-'], input=prelude + script,
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def board(source, metrics, name='Alice', **extra):
    row = dict(name=name, account=name + '.1234', profession='Firebrand',
               participation_time=100, metrics=metrics, **extra)
    return dict(source_key=source, stat=source, rows=[row])


def sample_model() -> dict[str, Any]:
    return {'stat_tables': [
        board('Conditions-Out', {'applications': 99}),
        board('Mechanics', {'kills': 4}), board('Attendance', {'fights': 3}),
        board('Pull-Skills', {'hits': 7}),
        board('Damage', {'targetdamage': 10000, 'targetdamageps': 100, 'targetpowerps': 80, 'targetconditionps': 20}),
        board('Offensive-Summary', {'downcontribution': 3000, 'againstdowneddamage': 900, 'appliedcrowdcontrol': 12}),
        board('Heal-Stats', {'healing': 8000, 'healingps': 80, 'downedhealing': 900, 'downedhealingps': 9, 'barrier': 100}),
        board('Support-Summary', {'resurrects': 2, 'condicleanse': 11, 'boonstrips': 8}),
        dict(board('Stability-Generation', {'totalgen': 150}, total=150, rate=1.5),
             stat='Stability Generation', metric={'rate_label': 'Stability Generation / sec'}),
    ]}


class ReportRefreshTests(unittest.TestCase):
    def test_simple_is_curated_not_source_order(self):
        model = sample_model()
        page = render_js(model, 'renderSimple()')
        for label in ['Damage to Enemy Players', 'Down-Contribution Damage', 'Downed-Ally Healing',
                      'Resurrects', 'Condition Cleanses', 'Boons Removed', 'Stability Generation', 'Outgoing Crowd Control']:
            self.assertIn(label, page)
        for label in ['Conditions-Out', 'Mechanics', 'Attendance', 'Pull-Skills']:
            self.assertNotIn('<h3>' + label, page)
        self.assertIn('<details class="secondary-support" open><summary>Barrier</summary>', page)
        self.assertEqual(unpack_classic_report(build_switchable_report('unaltered', model)), 'unaltered')

    def test_grouped_tables_join_identity_preserve_missing_and_sort_metrics(self):
        model = sample_model()
        support = next(b for b in model['stat_tables'] if b['source_key'] == 'Support-Summary')
        support['rows'] += [board('x', {'condicleanse': 0}, 'Bob')['rows'][0],
                            board('x', {'boonstrips': 4}, 'Missing')['rows'][0]]
        page = render_js(model, 'renderSimple()')
        self.assertIn('data-metric-grid="utility"', page)
        self.assertIn('data-metric-grid="damage"', page)
        self.assertIn('data-metric-grid="healing"', page)
        self.assertNotIn('data-metric-mode="total"', page)
        self.assertIn('data-sort-key="m0-total"', page)
        self.assertIn('data-value-kind="total"', page)
        self.assertIn('data-value-kind="rate"', page)
        self.assertIn('data-sort-key="m0-rate"', page)
        self.assertRegex(page, r'data-m0-total="0"')
        self.assertRegex(page, r'data-m0-total=""')
        self.assertNotIn('Missing values are —, not zero', page)
        result = render_js(model, '''[1,null,0,5].sort(function(a,b){return compareMetricValues(a,b,"ascending");})''')
        self.assertEqual(result, [0, 1, 5, None])
        result = render_js(model, '''[1,null,0,5].sort(function(a,b){return compareMetricValues(a,b,"descending");})''')
        self.assertEqual(result, [5, 1, 0, None])

    def test_boon_bars_have_exact_accessible_units_and_honest_scales(self):
        model = sample_model()
        model['stat_tables'].append(board('Uptimes', {'stability': 42.12345, 'might': 18.5, 'fury': 0},
                                            metric_units={'stability': 'percent', 'fury': 'percent'}))
        page = render_js(model, 'boonUptimeCharts(["stability","might","fury","alacrity"],"Boons")')
        self.assertIn('Stability Uptime: 42.12 %', page)
        self.assertNotIn('42.12345', page)
        self.assertEqual(model['stat_tables'][-1]['rows'][0]['metrics']['stability'], 42.12345)
        self.assertIn('width:42.12%', page)
        self.assertIn('Might Average Stacks: 18.5 stacks', page)
        self.assertNotIn('Might Uptime', page)
        self.assertNotIn('Alacrity Uptime', page)
        self.assertIn('width:0.00%', page)
        self.assertIn('aria-label=', page)
        self.assertIn('chart-tooltip', page)
        generation = render_js(model, 'boonGenerationCharts()')
        self.assertIn('Stability Generation / sec: 1.5', generation)
        self.assertIn('--boon-color:', generation)
        simple = render_js(model, 'renderSimple()')
        self.assertIn('boon-generation', simple)

    def test_bubbles_encode_third_metric_by_area_and_keep_zero_distinct(self):
        model = sample_model()
        damage = next(b for b in model['stat_tables'] if b['source_key'] == 'Damage')
        offense = next(b for b in model['stat_tables'] if b['source_key'] == 'Offensive-Summary')
        for name, size in [('Bob', 3600), ('Zero', 0), ('Missing', None)]:
            damage['rows'].append(board('x', {'targetdamageps': 50}, name)['rows'][0])
            offense['rows'].append(board('x', {'downcontribution': 100, 'againstdowneddamage': size}, name)['rows'][0])
        page = render_js(model, 'bubbleChart("dps")')
        radii = [float(v) for v in re.findall(r'data-bubble[^>]* r="([\d.]+)"', page)]
        self.assertEqual(page.count('data-overview-name='), 4)
        self.assertAlmostEqual(max(radii) / sorted(set(radii))[1], 2, places=2)
        self.assertIn('Area: damage to downed enemies / sec', page)
        self.assertIn('Down contribution / sec', page)
        self.assertNotIn('Incomplete data', page)  # Missing size is still valid XY.
        self.assertRegex(page, r'data-point-name="Missing"[^>]*data-size=""[^>]*stroke-dasharray="2 2"')
        self.assertRegex(page, r'data-point-name="Zero"[^>]*data-size="0"[^>]*fill="none"')
        self.assertIn('data-size="0"', page)
        self.assertIn('DPS: 100', page)
        pro = render_js(model, 'renderSparky()')
        self.assertIn('data-metric-grid="damage"', pro)
        self.assertIn('data-bubble-chart="dps"', pro)
        self.assertNotIn('data-bubble-chart="support"', pro)
        self.assertIn('data-support-metric="condicleanse"', pro)
        self.assertIn('data-support-metric="boonstrips"', pro)

    def test_ai_wall_independent_original_comments_and_provenance(self):
        model = sample_model()
        self.assertEqual(render_js(model, 'wallEnabled()'), False)
        model['sparky_wall'] = {'enabled': False, 'players': []}
        self.assertEqual(render_js(model, 'wallEnabled()'), False)
        model['sparky_wall'] = {'enabled': True, 'players': [{
            'id': 'alice.1234', 'name': 'Alice', 'mentions': 2, 'categories': {'damage': 2, 'healing': 1},
            'comments': [{'text': 'Alice did <brilliant> damage.\nKeep it up!', 'fight_id': 'fight-abc',
                          'timestamp': '2026-09-09T03:26:55Z', 'categories': ['damage']},
                         {'text': 'Alice healed the squad.', 'fight_id': 'fight-def',
                          'timestamp': '2026-09-09T04:00:00Z', 'categories': ['damage', 'healing']}]}]}
        page = render_js(model, 'documentFor("wall")')
        self.assertIn('Wall of Fame', page)
        self.assertIn('<details class="wall-player"', page)
        self.assertIn('Alice did &lt;brilliant&gt; damage.\nKeep it up!', page)
        self.assertIn('fight-abc', page)
        self.assertIn('Sep 8, 10:26 PM CDT', page)
        self.assertEqual(render_js(model, '(renderWall(), model.sparky_wall.players[0].comments[0].timestamp)'),
                         '2026-09-09T03:26:55Z')
        self.assertIn('Damage <b>2</b>', page)
        self.assertNotIn('data-section="dps"', page)
        self.assertNotIn('Historical Leaderboards', page)
        model['sparky_wall']['players'] = []
        self.assertIn('No recorded player mentions', render_js(model, 'renderWall()'))

    def test_missing_metrics_are_not_coerced_and_zero_size_legend_is_real(self):
        model = sample_model()
        damage = next(b for b in model['stat_tables'] if b['source_key'] == 'Damage')
        damage['rows'].append(board('x', {'targetdamage': '', 'targetdamageps': 'not recorded'}, 'Unknown')['rows'][0])
        rows = render_js(model, 'derivedDamageBoard("Damage","targetdamage","targetdamageps","DPS").rows')
        self.assertEqual([row['name'] for row in rows], ['Alice'])
        offense = next(b for b in model['stat_tables'] if b['source_key'] == 'Offensive-Summary')
        offense['rows'][0]['metrics']['againstdowneddamage'] = 0
        chart = render_js(model, 'bubbleChart("dps")')
        self.assertIn('Area: damage to downed enemies / sec · ○ 0', chart)
        self.assertNotIn('NaN', chart)

    def test_strips_summary_keeps_duration_and_event_counts_separate(self):
        model = sample_model()
        support = next(b for b in model['stat_tables'] if b['source_key'] == 'Support-Summary')
        support['rows'][0]['metrics'].update(boonstripstime=23, boonstripsdowned=2, boonstripstimedowned=8)
        page = render_js(model, 'stripsAndControlView()')
        self.assertIn('data-metric-grid="strips"', page)
        self.assertIn('Boon Duration Removed (seconds)', page)
        self.assertIn('Boon Duration Removed / min (seconds)', page)
        self.assertIn('Boons Removed from Downed Enemies', page)
        self.assertIn('data-m1-total="23"', page)
        self.assertIn('data-m2-total="2"', page)
        self.assertIn('data-detail-label="Removed seconds/min"', page)
        self.assertIn('data-detail-label="Duration removed (s)"', page)
        self.assertIn('data-detail-label="Downed strips/min"', page)
        self.assertIn('data-detail-label="Downed seconds/min"', page)
        rows = render_js(model, 'curatedBoards("strips").map(b=>[b.rows[0].total,b.rows[0].rate])')
        self.assertEqual(rows[:4], [[8, 4.8], [23, 13.8], [2, 1.2], [8, 4.8]])


class ReportRefreshBrowserTests(unittest.TestCase):
    def test_bubble_dom_keeps_all_observed_xy_and_missing_distinct(self):
        from playwright.sync_api import sync_playwright
        # Retained DPS scatter: exported DPS and offense totals / time -> SVG/key.
        inputs = [('Large', 100, 100, 4), ('Tiny', .00001, .00002, 1),
                  ('X only', 1, 0, 0), ('Y only', 0, 1, 0),
                  ('Missing size', 2, 2, None), ('Origin', 0, 0, 2),
                  ('Missing axis', 3, None, 0)]
        model = {'stat_tables': [
            {'source_key': 'Damage', 'rows': [
                board('x', {'targetdamageps': x}, name)['rows'][0]
                for name, x, y, size in inputs]},
            {'source_key': 'Offensive-Summary', 'rows': [
                board('x', {'downcontribution': y, 'againstdowneddamage': size}, name)['rows'][0]
                for name, x, y, size in inputs]}]}
        with tempfile.TemporaryDirectory() as directory, sync_playwright() as pw:
            path = Path(directory) / 'bubble.html'
            path.write_text(build_switchable_report('<p>Classic</p>', model, default_view='sparky'))
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.goto(path.as_uri())
            frame = page.frame_locator('#report-frame')
            frame.locator('[data-tab="dps"]').click()
            chart = frame.locator('[data-bubble-chart="dps"]')
            points = chart.locator('svg [data-overview-name]').evaluate_all('''es=>es.map(e=>({
                name:e.dataset.overviewName,x:Number(e.dataset.x),y:Number(e.dataset.y),
                size:e.dataset.size, cx:Number(e.querySelector('circle').getAttribute('cx')),
                cy:Number(e.querySelector('circle').getAttribute('cy')),
                dashed:e.querySelector('circle').hasAttribute('stroke-dasharray')}))''')
            expected = {name: (x, y / 100) for name, x, y, size in inputs
                        if x is not None and y is not None and (x or y)}
            self.assertEqual({p['name'] for p in points}, set(expected))
            for point in points:
                x, y = expected[point['name']]
                self.assertAlmostEqual(point['x'], x)
                self.assertAlmostEqual(point['y'], y)
                self.assertAlmostEqual(point['cx'], 75 + x / 100 * 760, places=3)
                self.assertAlmostEqual(point['cy'], 410 - y * 360, places=3)
                self.assertEqual(point['dashed'], point['name'] == 'Missing size')
            self.assertEqual(chart.locator('[data-overview-name="Missing axis"]').get_attribute('data-y'), '')
            self.assertEqual(chart.locator('[data-overview-name="Origin"]').count(), 0)
            self.assertEqual(chart.locator('[data-overview-name="X only"]').get_attribute('data-size'), '0')
            self.assertEqual(chart.locator('[data-highlight-point]').count(), len(expected))
            for rejected in ['No jitter', 'plotted', 'threshold']:
                self.assertNotIn(rejected, chart.inner_text())
            browser.close()

    def test_every_native_tab_has_simultaneous_metric_pairs_and_sorting(self):
        from playwright.sync_api import sync_playwright
        from core.night_model import build_night_model
        source = Path('/opt/data/sparkybot-reference/WvW_Combat_Summary.json')
        if not source.exists():
            self.skipTest('Real reference unavailable')
        model = build_night_model(json.loads(source.read_text()))
        model['sparky_wall'] = {'enabled': True, 'preview': True, 'players': []}
        with tempfile.TemporaryDirectory() as temp, sync_playwright() as pw:
            directory = Path(os.environ.get('REPORT_REFRESH_ARTIFACT_DIR', temp))
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / 'all-native-metrics.html'
            classic = '<p>Classic must remain untouched</p>'
            report = build_switchable_report(classic, model, default_view='simple')
            self.assertEqual(unpack_classic_report(report), classic)
            path.write_text(report)
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            errors, coverage = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(path.as_uri())
            frame = page.frame_locator('#report-frame')

            def audit(context):
                self.assertEqual(frame.locator('.metric-sort-pair').count(), 0)
                # Audit all native table classes, not only the grouped renderer.
                self.assertEqual(frame.locator('table:visible').evaluate_all('''tables=>tables.flatMap(table=>{
                    const issues=[];
                    if(table.querySelectorAll('thead tr').length>1) issues.push('multiple header rows');
                    table.querySelectorAll('thead th').forEach(th=>{
                        if(th.querySelectorAll('[data-sort-key]').length>1 || th.querySelector('small,br')) issues.push(th.textContent);
                    });
                    return issues;
                })'''), [])
                for grid in frame.locator('[data-metric-grid]:visible').all():
                    group = grid.get_attribute('data-metric-grid')
                    board_element = grid.locator('xpath=ancestor::article')
                    self.assertEqual(board_element.locator('[data-metric-mode]').count(), 0)
                    self.assertEqual(grid.locator('[data-metric-cell] > *').count(), 0)
                    expand = board_element.locator('[data-expand-board]')
                    if expand.count():
                        expand.click()
                    headers = grid.locator('thead [data-sort-key^="m"]')
                    keys = headers.evaluate_all('(elements)=>elements.map(e=>e.dataset.sortKey)')
                    self.assertGreater(len(keys), 0)
                    self.assertEqual(keys, [f'm{i}-{kind}' for i in range(len(keys)//2) for kind in ['total', 'rate']])
                    for header in headers.all():
                        self.assertTrue(header.is_visible())
                        self.assertTrue(header.get_attribute('aria-label'))
                        key = header.get_attribute('data-sort-key')
                        directions = set()
                        for _ in range(2):
                            header.evaluate('(element)=>element.click()')
                            direction = header.get_attribute('data-sort-direction')
                            directions.add(direction)
                            values = grid.locator('tbody tr').evaluate_all('(rows,key)=>rows.map(r=>r.getAttribute("data-"+key))', key)
                            numbers = [float(v) for v in values if v != '']
                            self.assertEqual(numbers, sorted(numbers, reverse=direction == 'descending'))
                            self.assertEqual(values[len(numbers):], [''] * (len(values) - len(numbers)))
                        self.assertEqual(directions, {'ascending', 'descending'})
                    self.assertTrue(grid.locator('tbody tr').evaluate_all('''rows=>rows.every(row=>Array.from(row.querySelectorAll('[data-metric-cell]')).every(cell=>{
                        const value=row.getAttribute('data-m'+cell.dataset.metricCell+'-'+cell.dataset.valueKind);
                        const n=Number(value), magnitude=Math.abs(n);
                        const divisor=magnitude>=1e6?1e6:magnitude>=1e4?1e3:1;
                        const suffix=divisor===1e6?'M':divisor===1e3?'k':'';
                        const expected=value===''?'—':(n/divisor).toLocaleString(undefined,{maximumFractionDigits:2})+suffix;
                        return cell.textContent===expected && cell.getBoundingClientRect().width>0 && !/\\.\\d{3}/.test(cell.title);
                    }))'''))
                    if expand.count():
                        self.assertEqual(grid.locator('tbody tr:visible').count(), grid.locator('tbody tr').count())
                    if os.environ.get('REPORT_REFRESH_ARTIFACT_DIR'):
                        page.mouse.move(0, 0)
                        board_element.screenshot(path=str(directory / (context + '-' + group + '-pairs.png')))
                    coverage.append({'context': context, 'group': group, 'mode': 'simultaneous'})
                    if expand.count():
                        expand.click()
                        self.assertEqual(grid.locator('tbody tr:visible').count(), 5)

            frame.locator('[data-metric-grid]').first.wait_for()
            audit('simple')
            page.locator('[data-view="sparky"]').click()
            visited = []
            for tab in frame.locator('[data-tab]').all():
                name = tab.get_attribute('data-tab')
                tab.click()
                section = frame.locator('[data-section="' + name + '"]')
                subtabs = section.locator('[data-subtab]').all()
                if subtabs:
                    for subtab in subtabs:
                        subname = subtab.get_attribute('data-subtab')
                        subtab.click()
                        audit('pro-' + name + '-' + subname)
                        visited.append(name + '/' + subname)
                else:
                    audit('pro-' + name)
                    visited.append(name)
            self.assertEqual({(c['context'], c['group']) for c in coverage}, {
                ('simple', 'damage'), ('simple', 'healing'), ('simple', 'utility'),
                ('pro-dps-overview', 'damage'), ('pro-support-overview', 'utility'),
                ('pro-support-strips', 'strips'), ('pro-healing-overview', 'healing')})
            page.locator('[data-view="wall"]').click()
            audit('wall')
            page.locator('[data-view="classic"]').click()
            self.assertEqual(frame.locator('body').inner_text(), 'Classic must remain untouched')
            self.assertEqual(errors, [])
            (directory / 'native-metric-audit.json').write_text(json.dumps({'visited': visited, 'coverage': coverage, 'errors': errors}, indent=2))
            browser.close()

    def test_real_reference_rendering(self):
        source = Path('/opt/data/sparkybot-reference/WvW_Combat_Summary.json')
        if not source.exists():
            self.skipTest('Downloaded real report fixture unavailable')
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('Playwright not installed')
        from core.night_model import build_night_model
        model = build_night_model(json.loads(source.read_text()))
        self.assertGreater(len(model['fights']), 0)
        with tempfile.TemporaryDirectory() as temp, sync_playwright() as pw:
            directory = Path(os.environ.get('REPORT_REFRESH_ARTIFACT_DIR', temp))
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / 'real-report.html'
            path.write_text(build_switchable_report('<title>Fixture Classic</title>', model, default_view='simple'))
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(path.as_uri())
            frame = page.frame_locator('#report-frame')
            grid = frame.locator('[data-metric-grid="damage"]')
            grid.wait_for()
            identities = {(r.get('account', '').lstrip(':'), r.get('name', ''), r.get('profession', ''))
                          for b in model['stat_tables'] if b['source_key'] in ('Damage', 'Offensive-Summary')
                          for r in b['rows'] if b['source_key'] == 'Damage' or r.get('metrics', {}).get('downcontribution') is not None}
            self.assertEqual(grid.locator('tbody tr').count(), len(identities))
            if os.environ.get('REPORT_REFRESH_ARTIFACT_DIR'):
                page.screenshot(path=str(directory / 'simple-desktop.png'))
            page.locator('[data-view="sparky"]').click()
            frame.locator('[data-tab="dps"]').click()
            bubbles = frame.locator('[data-bubble-chart="dps"]')
            self.assertGreater(bubbles.locator('circle').count(), 0)
            if os.environ.get('REPORT_REFRESH_ARTIFACT_DIR'):
                bubbles.screenshot(path=str(directory / 'dps-bubble.png'))
            frame.locator('[data-tab="support"]').click()
            frame.locator('[data-subtab="boons"]').click()
            self.assertGreater(frame.locator('.boon-card').count(), 0)
            if os.environ.get('REPORT_REFRESH_ARTIFACT_DIR'):
                frame.locator('.boon-uptimes').first.screenshot(path=str(directory / 'boons.png'))
            page.locator('[data-view="simple"]').click()
            page.set_viewport_size({'width': 390, 'height': 844})
            if os.environ.get('REPORT_REFRESH_ARTIFACT_DIR'):
                page.screenshot(path=str(directory / 'simple-mobile.png'))
            self.assertEqual(errors, [])
            browser.close()

    def test_rendered_sorting_tabs_tooltips_themes_and_offline(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('Playwright not installed; Node renderer tests still run')
        model = sample_model()
        support = next(b for b in model['stat_tables'] if b['source_key'] == 'Support-Summary')
        support['rows'] += [board('x', {'condicleanse': n}, 'Player' + str(n))['rows'][0] for n in range(6)]
        support['rows'].append(board('x', {'boonstrips': 0}, 'Missing')['rows'][0])
        model['stat_tables'].append(board('Uptimes', {'stability': 42.12345, 'might': 18.5}))
        model['sparky_wall'] = {'enabled': True, 'players': [{
            'id': 'alice.1234', 'name': 'Alice', 'mentions': 1, 'categories': {'damage': 1},
            'comments': [{'text': 'Alice did <brilliant> damage.', 'fight_id': 'fight-abc',
                          'timestamp': '2026-09-09T03:26:55Z', 'categories': ['damage']}]}]}
        with tempfile.TemporaryDirectory() as directory, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors, requests = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: requests.append(request.url) if request.url.startswith('http') else None)
            path = Path(directory) / 'report.html'
            path.write_text(build_switchable_report('<title>Original</title><p>Classic</p>', model, default_view='simple'))
            page.goto(path.as_uri())
            frame = page.frame_locator('#report-frame')
            grid = frame.locator('[data-metric-grid="utility"]')
            grid.wait_for()
            button = grid.locator('[data-sort-key="m0-rate"]')
            for _ in range(2):
                button.click()
                direction = button.get_attribute('data-sort-direction')
                values = grid.locator('tbody tr').evaluate_all('(rows)=>rows.map(r=>r.getAttribute("data-m0-rate"))')
                self.assertEqual(values[-1], '')
                numbers = [float(v) for v in values if v != '']
                self.assertEqual(numbers, sorted(numbers, reverse=direction == 'descending'))
            grid.locator('[data-sort-key="m1-rate"]').click()
            self.assertIsNone(button.get_attribute('data-sort-direction'))
            grid.locator('xpath=ancestor::article').locator('[data-expand-board]').click()
            self.assertEqual(grid.locator('tbody tr:visible').count(), 8)
            page.locator('[data-view="sparky"]').click()
            frame.locator('[data-tab="dps"]').click()
            frame.locator('[data-bubble-chart="dps"] [data-highlight-point]').first.focus()
            page.keyboard.press('Enter')
            self.assertTrue(frame.locator('#drilldown').is_visible())
            frame.locator('#drill-close').click()
            frame.locator('[data-tab="support"]').click()
            frame.locator('[data-subtab="boons"]').click()
            bar = frame.locator('.boon-card .sparky-boon-bar').first
            bar.focus()
            self.assertIn('42.12', frame.locator('#chart-popup').inner_text())
            self.assertNotIn('42.12345', frame.locator('#chart-popup').inner_text())
            self.assertTrue(frame.locator('#chart-popup').is_visible())
            for theme in ['studio-light', 'midnight', 'graphite', 'blackout']:
                page.locator('#theme-picker').select_option(theme)
                self.assertEqual(frame.locator('html').get_attribute('data-theme'), theme)
            page.locator('[data-view="wall"]').click()
            frame.locator('.wall-player summary').click()
            self.assertEqual(frame.locator('blockquote').inner_text(), 'Alice did <brilliant> damage.')
            self.assertIn('fight-abc', frame.locator('.wall-comment footer').inner_text())
            self.assertIn('Sep 8, 10:26 PM CDT', frame.locator('.wall-comment footer').inner_text())
            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('[data-view="simple"]').click()
            self.assertTrue(frame.locator('[data-metric-grid="utility"]').is_visible())
            self.assertTrue(frame.locator('.metric-grid-scroll').first.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'))
            mobile_grid = frame.locator('[data-metric-grid="utility"]')
            self.assertTrue(mobile_grid.locator('tbody tr:visible').first.locator('[data-metric-cell="0"][data-value-kind="total"]').is_visible())
            self.assertTrue(mobile_grid.locator('tbody tr:visible').first.locator('[data-metric-cell="0"][data-value-kind="rate"]').is_visible())
            scoreboard = frame.locator('.kill-comparison')
            self.assertTrue(scoreboard.is_visible())
            self.assertTrue(scoreboard.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'))
            self.assertEqual(scoreboard.locator('.combat-duel button:visible').count(), 3)
            page.locator('[data-view="classic"]').click()
            self.assertEqual(frame.locator('body').inner_text(), 'Classic')
            self.assertEqual(errors, [])
            self.assertEqual(requests, [])
            # A disabled report must hide the wall even when localStorage remembers it.
            page.locator('[data-view="wall"]').click()
            model['sparky_wall']['enabled'] = False
            path.write_text(build_switchable_report('<p>Classic</p>', model))
            page.reload()
            frame.locator('.tabs').wait_for()
            self.assertTrue(page.locator('[data-view="wall"]').is_hidden())
            browser.close()


if __name__ == '__main__':
    unittest.main()