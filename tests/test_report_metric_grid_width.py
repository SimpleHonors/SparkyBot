from playwright.sync_api import sync_playwright
from core.report_viewer import build_switchable_report
from test_report_refresh import sample_model


def test_adjacent_metric_grids_use_available_width_and_shared_identity(tmp_path):
    path = tmp_path / 'grids.html'
    path.write_text(build_switchable_report('<p>Classic</p>', sample_model(), default_view='simple'))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        page.goto(path.as_uri())
        frame = page.frame_locator('#report-frame')
        frame.locator('.metric-grid').first.wait_for()
        for width in (1440, 1024, 768, 390):
            page.set_viewport_size({'width': width, 'height': 1100})
            frame.locator('main').evaluate("e=>e.ownerDocument.defaultView.dispatchEvent(new Event('resize'))")
            grids = frame.locator('.metric-grid').evaluate_all('''tables => tables.map(t=>({
                width:t.getBoundingClientRect().width,
                available:t.closest('.boards').clientWidth-2,
                identity:t.tHead.rows[0].cells[0].getBoundingClientRect().width,
                overflow:t.scrollWidth>t.parentElement.clientWidth+1,
                condensed:t.classList.contains('responsive-condensed'),
                height:t.tHead.getBoundingClientRect().height,
                time:t.tHead.rows[0].cells[t.tHead.rows[0].cells.length-1].getBoundingClientRect().right,
                rateWidths:Array.from(t.tHead.querySelectorAll('.pair-rate')).map(c=>c.getBoundingClientRect().width)
            }))''')
            assert len(grids) == 3
            for grid in grids:
                assert abs(grid['width']-grid['available']) <= 2, grids
                assert not grid['overflow'], grids
                assert grid['height'] <= 32
            assert max(g['identity'] for g in grids)-min(g['identity'] for g in grids) <= 1
            if width >= 1024:
                assert not any(g['condensed'] for g in grids)
                assert max(g['time'] for g in grids)-min(g['time'] for g in grids) <= 1
                # Spare width belongs to the primary rate, not every numeric column.
                assert all(45 <= w <= 78 for g in grids for w in g['rateWidths'][1:]), grids
                assert all(g['rateWidths'][0] > max(g['rateWidths'][1:]) for g in grids), grids
                assert all(g['identity'] >= 180 for g in grids), grids
        summary = frame.locator('.row-metric-details summary').first
        summary.focus()
        summary.press('Enter')
        assert summary.evaluate('e=>e.parentElement.open')
        assert frame.locator('[data-metric-grid=damage] [data-value-kind=total]').first.is_visible()
        assert frame.locator('[data-metric-grid=damage] [data-value-kind=rate]').first.is_visible()
        browser.close()
