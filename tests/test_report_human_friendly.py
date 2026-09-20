"""Human-facing unit, scope and progressive-disclosure regressions."""
import json
from pathlib import Path
from test_report_refresh import render_js, sample_model
from core.night_model import build_night_model

SOURCE = Path('/opt/data/sparkybot-reference/WvW_Combat_Summary.json')

def real_model():
    return build_night_model(json.loads(SOURCE.read_text()))

def test_might_metadata_and_all_consumers_use_stacks():
    model = real_model()
    boon = next(b for b in model['stat_tables'] if b['source_key'] == 'Uptimes')
    assert boon['metric']['rate_unit'] == 'stacks'
    assert boon['value_label'] == 'Might average stacks'
    assert all(r['metric_units']['might'] == 'stacks' for r in boon['rows'])
    assert 'stacks' in render_js(model, 'boardCard(model.stat_tables.find(b=>b.source_key==="Uptimes"))')
    html = render_js(model, 'comparisonMetricHtml(comparisonPlayers()[0],comparisonPlayers()[1])')
    assert 'Might average stacks' in html
    assert 'Might uptime' not in html


def test_comparison_zero_baseline_and_count_differences():
    html = render_js({}, '''comparisonMetricHtml({name:"A",tables:{Damage:{metrics:{targetdamageps:125}},"Support-Summary":{metrics:{resurrects:3,condicleanse:8}}}},{name:"B",tables:{Damage:{metrics:{targetdamageps:0}},"Support-Summary":{metrics:{resurrects:0,condicleanse:2}}}})''')
    assert '125 vs 0' in html
    assert 'A +3' in html and 'A +6' in html
    assert '+100%' not in html and '+300%' not in html


def test_skill_share_explains_retained_coverage_and_denominators():
    html = render_js({}, '''comparisonSkillPanel({name:"A",skillDamage:{skills:[{skill:"Hit",damage:40,percent_of_total:20},{skill:"Zap",damage:60,percent_of_total:30}]}})''')
    assert 'Share of listed damage' in html
    assert '30% of all damage' in html
    assert '20% of all damage' in html
    assert '40%' in html
    assert 'data-chart-total="100"' in html


def test_profession_boons_are_visible_and_times_are_human():
    model = real_model()
    html = render_js(model, 'renderSimple()')
    assert '<details class="boon-more"><summary>Show all' in html
    assert '<details class="boon-methodology"' not in html
    assert '<details class="boon-professions"' not in html
    assert '<h2>Boon generation by profession</h2>' in html
    assert 'What We Did Well · By Profession' not in html
    assert 'Fight time (s)' not in html
    grid = render_js(sample_model(), 'metricGrid("utility")')
    assert 'data-participation="100"' in grid and '>1m 40s</td>' in grid
    assert 'Modeled fights' not in html


def test_unknown_enemy_roles_are_one_explanation_not_role_cards():
    html = render_js(real_model(), 'allFightsCompositionView()')
    assert 'No reliable enemy role evidence' not in html
    assert 'Representative Role Mix' not in html
    assert '<details class="enemy-methodology"' not in html
    assert 'Profession Frequency' in html
    assert '<details class="estimated-layout" open><summary>Estimated subgroup layout' in html
    assert 'class="role-badge role-unknown"' not in html


def test_bubbles_are_true_scatter_with_named_key_and_real_coordinates():
    model = sample_model()
    support = next(b for b in model['stat_tables'] if b['source_key'] == 'Support-Summary')
    support['rows'] += [dict(support['rows'][0], name='Bob', account='Bob', metrics={'boonstrips':16,'condicleanse':22,'resurrects':8})]
    html = render_js(model, 'bubbleChart("support")')
    assert 'class="bubble-scatter"' in html
    assert 'data-point-name="Alice"' in html and 'data-point-name="Bob"' in html
    assert 'No jitter' not in html
    assert 'class="bubble-identity-table"' in html
    assert 'bubble-lane' not in html
    import re
    circles = re.findall(r'<circle data-bubble[^>]+>', html)
    assert len(circles) == 2
    assert len(set(re.findall(r'cx="([\d.]+)"', ''.join(circles)))) == 2
    assert len(set(re.findall(r'cy="([\d.]+)"', ''.join(circles)))) == 2
    coordinates = {re.search(r'data-point-name="([^"]+)"', c)[1]:
                   tuple(float(re.search(key+r'="([\d.]+)"', c)[1]) for key in ('cx', 'cy', 'r'))
                   for c in circles}
    assert coordinates['Bob'] == (835.0, 50.0, 18.0)
    assert coordinates['Alice'] == (455.0, 230.0, 9.0)


def test_browser_real_scatter_alignment_hover_and_mobile(tmp_path):
    from playwright.sync_api import sync_playwright
    from core.report_viewer import build_switchable_report
    artifact = tmp_path / 'human-friendly'
    artifact.mkdir(parents=True, exist_ok=True)
    path = artifact / 'native-real-data.html'
    path.write_text(build_switchable_report('<p>Native UX test; Classic preview owned separately.</p>', real_model(), default_view='simple'))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width':1440,'height':1100})
        errors=[]
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(path.as_uri())
        frame = page.frame_locator('#report-frame')
        grid = frame.locator('[data-metric-grid="damage"]')
        grid.wait_for()
        assert grid.locator('thead th').nth(1).evaluate('(e)=>getComputedStyle(e).textAlign') == 'right'
        assert grid.locator('thead th button').first.evaluate('(e)=>getComputedStyle(e).textAlign') == 'right'
        chart = frame.locator('[data-multiboon-chart]')
        assert chart.locator('.multiboon-row:visible').count() == 10
        assert frame.locator('.boon-generation').is_visible()
        assert frame.locator('.boon-generation').evaluate('e=>!e.closest("details:not([open])")')
        chart.screenshot(path=str(artifact/'simple-boons-desktop.png'))
        segment = chart.locator('[data-boon]').first
        segment.hover()
        popup=frame.locator('#chart-popup')
        assert popup.is_visible()
        assert popup.inner_text() == segment.get_attribute('data-tooltip')
        assert segment.get_attribute('data-boon') in popup.inner_text()
        assert chart.locator('.multiboon-row').first.get_attribute('data-multiboon-player') in popup.inner_text()
        import re
        assert not re.search(r'\d+\.\d{3,}', popup.inner_text())
        assert len(popup.inner_text()) < 200
        assert segment.get_attribute('title') is None
        popup.screenshot(path=str(artifact/'boon-tooltip.png'))
        page.mouse.move(0,0)
        chart.locator('.boon-more summary').click()
        assert chart.locator('.multiboon-row:visible').count() > 10
        chart.locator('.boon-more summary').click()
        grid.locator('xpath=ancestor::article').screenshot(path=str(artifact/'simple-aligned-grid.png'))
        page.locator('[data-view="sparky"]').click()
        frame.locator('[data-tab="support"]').click()
        rankings = frame.locator('[data-support-rankings]')
        assert rankings.locator('[data-support-metric]').count() == 2
        assert frame.locator('[data-bubble-chart="support"]').count() == 0
        for ranking in rankings.locator('[data-support-metric]').all():
            assert ranking.locator('.table-player b').first.is_visible()
            expand = ranking.locator('[data-expand-board]')
            expand.focus()
            expand.press('Enter')
            assert ranking.locator('tbody tr:visible').count() == ranking.locator('tbody tr').count()
            expand.press('Space')
            assert ranking.locator('tbody tr:visible').count() == 5
        rankings.screenshot(path=str(artifact/'support-rankings-desktop.png'))
        for tab,group in [('dps','dps')]:
            frame.locator('[data-tab="'+tab+'"]').click()
            bubbles=frame.locator('[data-bubble-chart="'+group+'"]')
            assert bubbles.locator('.bubble-scatter').is_visible()
            bubbles.locator('[data-highlight-point]').first.focus()
            assert bubbles.locator('circle.point-highlight').count() == 1, errors
            assert bubbles.locator('.bubble-identity-table').first.is_visible()
            assert bubbles.locator('.bubble-lane, .bubble-comparison-row').count() == 0
            bubbles.locator('[data-highlight-point]').first.evaluate('e=>e.blur()')
            assert bubbles.locator('.has-highlight').count() == 0
            page.mouse.move(0,0)
            bubbles.screenshot(path=str(artifact/(group+'-bubbles-desktop.png')))
        for width in (1440,390):
            page.set_viewport_size({'width':width,'height':1100})
            damage=frame.locator('.damage-composition-card')
            assert damage.evaluate('(e)=>e.scrollWidth<=e.clientWidth')
            if width == 390:
                assert damage.locator('.damage-player').first.evaluate('(e)=>e.getBoundingClientRect().width>200')
            damage.screenshot(path=str(artifact/('damage-composition-'+str(width)+'.png')))
            bubbles.screenshot(path=str(artifact/('dps-bubbles-'+str(width)+'.png')))
        page.set_viewport_size({'width':1440,'height':1100})
        page.locator('[data-view="simple"]').click()
        for width in (1440,390):
            page.set_viewport_size({'width':width,'height':844})
            for view,tab,group in [('simple',None,'damage'),('simple',None,'utility'),('sparky','dps','damage'),('sparky','support','utility'),('sparky','support','strips')]:
                page.locator('[data-view="'+view+'"]').click()
                if tab: frame.locator('[data-tab="'+tab+'"]').click()
                if group=='strips': frame.locator('[data-subtab="strips"]').click()
                table=frame.locator('[data-metric-grid="'+group+'"]')
                table.wait_for(state='visible')
                article=table.locator('xpath=ancestor::article')
                assert article.locator('[data-metric-mode]').count() == 0
                assert table.locator('thead tr').count() == 1
                assert table.locator('thead th').nth(1).evaluate('(e)=>getComputedStyle(e).textAlign')=='right'
                for kind in ('total', 'rate'):
                    assert table.locator('[data-metric-cell="0"][data-value-kind="'+kind+'"]').first.is_visible()
                assert table.locator('tbody tr:visible').first.evaluate('e=>Array.from(e.children).slice(1).every((cell,i)=>!cell.getBoundingClientRect().width || Math.abs(cell.getBoundingClientRect().right-e.closest("table").querySelectorAll("thead th")[i+1].getBoundingClientRect().right)<1)')
                article.screenshot(path=str(artifact/(view+'-'+group+'-aligned-'+str(width)+'.png')))
                if width==390:
                    scroll=table.locator('xpath=parent::*')
                    scroll.evaluate('e=>e.scrollLeft=e.scrollWidth')
                    assert scroll.evaluate('e=>e.scrollWidth<=e.clientWidth+1')
                    details=table.locator('tbody tr:visible').first.locator('.row-metric-details summary')
                    assert details.is_visible()
                    details.focus()
                    page.keyboard.press('Enter')
                    assert details.locator('..').get_attribute('open') is not None
            page.locator('[data-view="simple"]').click()
            chart=frame.locator('[data-multiboon-chart]')
            for index,block in [(0,'start'),(5,'center'),(9,'end')]:
                row=chart.locator('.multiboon-row').nth(index)
                row.evaluate('(e,b)=>e.scrollIntoView({block:b})',block)
                segment=row.locator('[data-boon]').first
                segment.hover()
                assert popup.is_visible()
                assert row.get_attribute('data-multiboon-player') in popup.inner_text()
                assert popup.evaluate('(e)=>{const r=e.getBoundingClientRect();return r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight}')
                segment.focus()
                assert popup.is_visible()
                assert popup.evaluate('(e)=>{const r=e.getBoundingClientRect();return r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight}')
            page.screenshot(path=str(artifact/('boon-hover-context-'+str(width)+'.png')))
        assert not errors
        browser.close()
