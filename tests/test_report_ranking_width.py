from playwright.sync_api import sync_playwright
from core.report_viewer import build_switchable_report
from test_report_refresh import render_js


def test_ranking_columns_align_across_sparse_and_dense_boards(tmp_path):
    row = dict(name='A Full Readable Player', profession='Firebrand',total=123456,rate=12.34,participation_time=200,fight_count=3)
    boards = [dict(stat='Damage',rows=[row]),dict(stat='Healing',rows=[dict(row,rate=1234.56)]),dict(stat='Killing Blows',rows=[dict(row,rate=None)]),dict(stat='Pull-skill connected hits',rows=[dict(row,pull_skill_logged_hit_events=170,pull_skill_connection_rate=72.3,pull_skill_casts=14,pull_skill_connected_hits_per_cast=8.2)])]
    boards[-1]['source_key']='Pull-Skills'
    boards.append(dict(stat='Might Uptime',metric={'rate_label':'Uptime %','rate_unit':'percent'},rows=[dict(row,total=None,rate=93.67)]))
    model={'stat_tables':boards}
    cards=render_js(model,'model.stat_tables.map(b=>boardCard(b,10)).join("")')
    path=tmp_path/'rankings.html'
    path.write_text(build_switchable_report('Classic',model,default_view='sparky'))
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.goto(path.as_uri())
        page.locator('[data-view="sparky"]').click()
        frame=page.frame_locator('#report-frame')
        frame.locator('.wrap').wait_for()
        frame.locator('body').evaluate('(e,html)=>{const styles=Array.from(e.querySelectorAll("style")).map(s=>s.outerHTML).join("");e.innerHTML=styles+"<main class=wrap><div class=boards>"+html+"</div></main>"}',cards)
        for width in (1440,1024,390):
            page.set_viewport_size({'width':width,'height':1000})
            frame.locator('body').evaluate('e=>e.ownerDocument.defaultView.dispatchEvent(new Event("resize"))')
            geometry=frame.locator('.ranking-board table').evaluate_all('ts=>ts.map(t=>({width:t.getBoundingClientRect().width,player:t.rows[1].cells[1].getBoundingClientRect().width,left:t.rows[1].cells[1].getBoundingClientRect().left,overflow:t.scrollWidth>t.parentElement.clientWidth+1,condensed:t.classList.contains("responsive-condensed")}))')

            assert len(geometry)==5
            assert max(g['width'] for g in geometry)-min(g['width'] for g in geometry)<=1
            if width>=1024:
                assert max(g['player'] for g in geometry)-min(g['player'] for g in geometry)<=1
            assert not any(g['overflow'] for g in geometry)
            if width>=1024:
                assert all(not g['condensed'] for g in geometry)
                assert all(abs(g['player']-230)<=1 for g in geometry)
                for table in frame.locator('.ranking-board table').all()[:2]:
                    total = table.locator('tbody [data-value-kind="total"]').first.bounding_box()
                    rate = table.locator('tbody [data-value-kind="rate"]').first.bounding_box()
                    assert total is not None and rate is not None
                    assert abs(total['width']-112)<=1
                    assert rate['width']>total['width']*2
        browser.close()
