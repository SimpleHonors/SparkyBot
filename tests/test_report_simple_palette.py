from core.report_viewer import build_switchable_report
from test_report_refresh import sample_model
from playwright.sync_api import sync_playwright


def test_simple_and_pro_share_actual_table_palette(tmp_path):
    model=sample_model()
    for board in model['stat_tables']:
        for row in board['rows']:
            row['profession']='Druid'
    path=tmp_path/'parity.html'
    path.write_text(build_switchable_report('<p>Classic</p>',model))
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page();page.goto(path.as_uri())
        for theme in ['blackout','graphite','midnight','studio-light']:
            page.locator('#theme-picker').select_option(theme)
            views={}
            for view in ['simple','sparky']:
                page.locator('[data-view='+view+']').click()
                views[view]=page.frame_locator('#report-frame').locator('body').evaluate('''e=>['damage','healing','utility'].map(g=>{let t=e.querySelector('[data-metric-grid='+g+']');return [t.closest('.board'),t.querySelector('h3'),t.querySelector('thead th'),t.querySelector('tbody tr'),t.querySelector('tbody th'),t.querySelector('.grid-primary-value')].filter(Boolean).map(n=>{let s=getComputedStyle(n);return {color:s.color,bg:s.backgroundColor,image:s.backgroundImage}})})''')
            assert views['simple']==views['sparky'],(theme,views)
        browser.close()
