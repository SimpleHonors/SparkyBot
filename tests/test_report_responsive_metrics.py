from playwright.sync_api import sync_playwright
from core.report_viewer import build_switchable_report
from test_report_refresh import sample_model, render_js


def test_pull_details_fit_and_preserve_values_keyboard_and_sort(tmp_path):
    model=sample_model()
    board=render_js(model,'boardCard({stat:"Pull-skill connected hits",source_key:"Pull-Skills",rows:[{name:"Full Readable Player Name",profession:"Guardian",total:716,rate:13.88,participation_time:3095,fights:25,pull_skill_logged_hit_events:999,pull_skill_connection_rate:71.67,pull_skill_casts:32,pull_skill_connected_hits_per_cast:22.38}]},100)')
    path=tmp_path/'responsive.html';path.write_text(build_switchable_report('<p>Classic</p>',model,default_view='simple'))
    with sync_playwright() as pw:
        browser=pw.chromium.launch();page=browser.new_page();page.goto(path.as_uri())
        frame=page.frame_locator('#report-frame');frame.locator('main').wait_for()
        frame.locator('main').evaluate('(e,html)=>e.innerHTML=html',board)
        for width in [1440,1024,768,390]:
            page.set_viewport_size({'width':width,'height':1000})
            for theme in ['blackout','graphite','midnight','studio-light']:
                page.locator('#theme-picker').select_option(theme)
                frame.locator('table').evaluate("e=>e.ownerDocument.defaultView.dispatchEvent(new Event('resize'))")
                result=frame.locator('table').evaluate("e=>({width:e.clientWidth,scroll:e.scrollWidth,container:e.parentElement.clientWidth,identity:e.querySelector('.responsive-identity').getBoundingClientRect().width})")
                assert result['scroll']<=result['width']+1 and result['scroll']<=result['container']+1
                assert result['identity']>150
        summary=frame.locator('.row-metric-details summary');summary.focus();summary.press('Enter')
        assert summary.evaluate('e=>e.parentElement.open')
        details=frame.locator('.row-metric-details').inner_text()
        for value in ['999','71.67%','32','22.38','51m 35s','25']:
            assert value in details
        assert frame.locator('[data-value-kind=total]').inner_text()=='716'
        assert frame.locator('[data-value-kind=rate]').inner_text()=='13.88'
        assert frame.locator('[data-value-kind=total]').is_visible()
        assert frame.locator('[data-value-kind=rate]').is_visible()
        frame.locator('[data-sort-key=rate]').click()
        assert frame.locator('[data-sort-key=rate]').evaluate("e=>e.closest('th').getAttribute('aria-sort')")=='ascending'
        browser.close()
