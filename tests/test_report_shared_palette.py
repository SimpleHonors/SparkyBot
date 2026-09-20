from core.report_viewer import build_switchable_report
from test_report_refresh import sample_model
from playwright.sync_api import sync_playwright


def test_shared_palette_across_table_families_and_themes(tmp_path):
    path=tmp_path/'palette.html'
    path.write_text(build_switchable_report('<p>Classic</p>',sample_model(),default_view='sparky'))
    with sync_playwright() as p:
        b=p.chromium.launch();page=b.new_page();page.goto(path.as_uri())
        f=page.frame_locator('#report-frame')
        for theme in ['blackout','graphite','midnight','studio-light']:
            page.locator('#theme-picker').select_option(theme)
            values=f.locator('body').evaluate('''e=>{let sels=['.metric-grid-board','.ranking-board','.rate-ranking-card','.support-ranking'];return sels.map(s=>{let n=e.querySelector(s),c=getComputedStyle(n);return [c.backgroundColor,c.borderTopColor,c.borderTopWidth]})}''')
            assert all(v==values[0] for v in values),values
            headers=f.locator('body').evaluate('''e=>['.metric-grid thead th:first-child','.rate-ranking-head','.support-ranking-labels'].map(s=>getComputedStyle(e.querySelector(s)).backgroundColor)''')
            assert len(set(headers))==1,headers
            values=f.locator('body').evaluate('''e=>{let g=e.querySelector('.metric-grid');return [...g.querySelectorAll('tbody tr:first-child td')].map(n=>getComputedStyle(n).color)}''')
            assert len(set(values))==1,values
            bar=f.locator('.rate-ranking-row').first.evaluate('''e=>({color:getComputedStyle(e).getPropertyValue('--comparison-color').trim(),fill:getComputedStyle(e.querySelector('em')).backgroundColor,height:getComputedStyle(e.querySelector('i')).height})''')
            assert bar['color'] and bar['height']=='9px'
            cell=f.locator('.grid-primary-value').first.evaluate('e=>getComputedStyle(e).backgroundImage')
            fill=cell.split('), linear-gradient')[0]
            assert 'color(srgb' not in fill and 'rgba(' not in fill.replace('rgba(0, 0, 0, 0)','transparent')
            assert f.locator('.grid-primary-value').first.evaluate('e=>getComputedStyle(e).backgroundSize')=='100% 9px, 100% 9px'
            assert f.locator('.rate-ranking-track em').first.evaluate('e=>getComputedStyle(e).opacity')=='1'
            assert f.locator('.support-ranking-track i').first.evaluate('e=>getComputedStyle(e).opacity')=='1'
        b.close()
