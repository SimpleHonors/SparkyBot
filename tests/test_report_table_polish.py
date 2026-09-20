"""Human table units and persistent sorting affordances."""
from test_report_refresh import render_js, sample_model


def test_grid_labels_match_units_without_repetitive_instructions():
    model = sample_model()
    for group, label in [('damage', 'Per second'), ('healing', 'Per second'),
                         ('utility', 'Per minute'), ('strips', 'Per minute')]:
        html = render_js(model, 'metricGrid('+repr(group)+')')
        if not html:
            continue
        assert 'data-sort-key="m0-total"' in html
        assert 'data-sort-key="m0-rate"' in html
        assert 'data-metric-mode' not in html
        assert 'How to read' not in html
        assert 'Click a column' not in html
    assert render_js(model, 'curatedBoards("utility").find(b=>b.source_key==="Stability-Generation").metric.rate_unit') == 'per_minute'
    original = render_js(model, 'tableBySource("Stability-Generation").rows[0].rate')
    converted = render_js(model, 'curatedBoards("utility").find(b=>b.source_key==="Stability-Generation").rows[0].rate')
    assert converted == original * 60
    assert render_js(model, 'tableBySource("Stability-Generation").metric.rate_unit || null') != 'per_minute'


def test_browser_headers_are_shaded_sortable_and_identity_compact(tmp_path):
    from playwright.sync_api import sync_playwright
    from core.report_viewer import build_switchable_report
    model = sample_model()
    path = tmp_path / 'headers.html'
    path.write_text(build_switchable_report('<p>Test</p>', model, default_view='simple'))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(path.as_uri())
        frame = page.frame_locator('#report-frame')
        for theme in ('blackout', 'graphite', 'midnight', 'studio-light'):
            page.locator('#theme-picker').select_option(theme)
            for table in frame.locator('[data-metric-grid]').all():
                buttons = table.locator('thead button[data-sort-key]')
                assert buttons.nth(1).evaluate("e=>getComputedStyle(e,'::after').content") == '"↓"'
                assert buttons.first.evaluate("e=>getComputedStyle(e,'::after').content") == '"↕"'
                assert table.locator('thead th').first.evaluate('e=>getComputedStyle(e).backgroundColor') != table.locator('tbody th').first.evaluate('e=>getComputedStyle(e).backgroundColor')
                identity = table.locator('tbody th').first
                assert identity.locator('.table-player .profession-glyph').count() == 1
                assert identity.locator('small').count() == 0
                assert identity.locator('b').evaluate('e=>parseFloat(getComputedStyle(e).fontSize)') >= 14
                buttons.first.click()
                assert buttons.first.locator('..').get_attribute('aria-sort') == 'descending'
                buttons.first.press('Enter')
                assert buttons.first.locator('..').get_attribute('aria-sort') == 'ascending'
                assert buttons.first.evaluate("e=>getComputedStyle(e,'::after').content") == '"↑"'
                assert buttons.first.evaluate('e=>getComputedStyle(e).outlineStyle') != 'none'
                # Restore initial state for the next theme.
                buttons.nth(1).click()
        browser.close()
