import re

from playwright.sync_api import sync_playwright
from core.report_viewer import build_switchable_report
from test_report_refresh import render_js, sample_model


def test_dense_grid_allocates_spare_width_to_one_primary_comparison(tmp_path):
    model = sample_model()
    html = render_js(model, 'metricGrid("damage")')
    assert html.count('class="number pair-rate grid-primary-value"') == 1
    assert 'grid-primary-col' in html
    assert 'data-m0-rate="100"' in html
    path = tmp_path / 'dense.html'
    path.write_text(build_switchable_report('<p>Classic</p>', model, default_view='simple'))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width':1440, 'height':1000})
        page.goto(path.as_uri())
        frame = page.frame_locator('#report-frame')
        grid = frame.locator('[data-metric-grid=damage]')
        grid.wait_for()
        for width in (1440, 1024, 390):
            page.set_viewport_size({'width':width, 'height':1000})
            grid.evaluate("e=>e.ownerDocument.defaultView.dispatchEvent(new Event('resize'))")
            sizes = grid.evaluate('''e=>({
                overflow:e.scrollWidth>e.parentElement.clientWidth+1,
                primary:e.querySelector('.grid-primary-value').getBoundingClientRect().width,
                secondary:e.querySelector('[data-metric-cell="1"][data-value-kind=rate]').getBoundingClientRect().width,
                total:e.querySelector('[data-metric-cell="0"][data-value-kind=total]').getBoundingClientRect().width,
                font:parseFloat(getComputedStyle(e.querySelector('.grid-primary-value')).fontSize)
            })''')
            assert not sizes['overflow'], sizes
            assert sizes['font'] >= 14
            if width >= 1024:
                assert sizes['primary'] > sizes['secondary'] * 1.5, sizes
                assert sizes['total'] <= 100, sizes
            else:
                assert grid.locator('.grid-primary-value').is_visible()
        browser.close()


def test_pressure_chart_uses_full_plot_and_unscrolled_identity_key(tmp_path):
    from test_report_refresh import board
    model = sample_model()
    damage = next(b for b in model['stat_tables'] if b['source_key'] == 'Damage')
    offense = next(b for b in model['stat_tables'] if b['source_key'] == 'Offensive-Summary')
    for index in range(16):
        name = 'Full readable player name ' + str(index)
        damage['rows'].append(board('x', {'targetdamageps': index}, name)['rows'][0])
        offense['rows'].append(board('x', {'downcontribution': 10, 'againstdowneddamage': None if index == 0 else index}, name)['rows'][0])
    damage['rows'].append(board('x', {'targetdamageps': 30}, 'Incomplete identity')['rows'][0])
    html = render_js(model, 'bubbleChart("dps")')
    assert 'bubble-key-columns' in html
    assert 'Paired contributions' not in html
    assert html.count('data-bubble data-point-index=') == 17
    assert sorted(int(i) for i in re.findall('data-highlight-point="(\\d+)"', html)) == list(range(17))
    assert 'stroke-dasharray="2 2"' in html
    assert 'Incomplete identity' in html.split('Incomplete data')[1]
    assert 'cx="75.000"' in html
    css = render_js(model, 'refreshCss')
    path = tmp_path / 'pressure.html'
    path.write_text('<style>:root{--text:#eee;--panel:#171e28;--line:#333;--muted:#aab}body{margin:0;color:var(--text);background:var(--panel);font:14px Arial}*{box-sizing:border-box}</style><style>' + css + '</style>' + html)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width':1440, 'height':1000})
        page.goto(path.as_uri())
        for width in (1440, 1024, 390):
            page.set_viewport_size({'width':width, 'height':1000})
            values = page.locator('.bubble-card').evaluate('''e=>({
                overflow:e.scrollWidth>e.clientWidth+1,
                plot:e.querySelector('.bubble-scroll').getBoundingClientRect().width,
                width:e.clientWidth,
                keys:[...e.querySelectorAll('.bubble-key-scroll')].map(k=>({height:k.clientHeight,scroll:k.scrollHeight})),
                font:parseFloat(getComputedStyle(e.querySelector('.bubble-identity-table td')).fontSize)
            })''')
            assert not values['overflow'], values
            assert values['plot'] > values['width'] * .85
            assert all(k['scroll'] <= k['height'] + 1 for k in values['keys']), values
            assert values['font'] >= 14
        browser.close()