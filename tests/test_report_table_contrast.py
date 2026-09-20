"""Shared table surfaces: readable identities, real hover, concise boon output."""
from pathlib import Path
from playwright.sync_api import sync_playwright
from core.report_viewer import build_switchable_report
from test_report_refresh import sample_model, render_js

# Resolve CSS Color 4 through canvas rather than assuming computed rgb() syntax.
CONTRAST_JS = """e=>{const s=getComputedStyle(e),c=document.createElement('canvas');c.width=c.height=1;const x=c.getContext('2d');function rgb(v){x.clearRect(0,0,1,1);x.fillStyle=v;x.fillRect(0,0,1,1);return [...x.getImageData(0,0,1,1).data].slice(0,3).map(v=>v/255)}function lum(v){v=rgb(v).map(n=>n<=.04045?n/12.92:((n+.055)/1.055)**2.4);return v[0]*.2126+v[1]*.7152+v[2]*.0722}let n=e,bg=s.backgroundColor;while(bg==='rgba(0, 0, 0, 0)'&&n.parentElement){n=n.parentElement;bg=getComputedStyle(n).backgroundColor}let a=lum(s.color),b=lum(bg);return {contrast:(Math.max(a,b)+.05)/(Math.min(a,b)+.05),color:s.color,bg,rgb:rgb(bg)}}"""


def test_shared_table_contrast_and_states(tmp_path):
    path=tmp_path/'contrast.html'
    path.write_text(build_switchable_report('<p>Test</p>',sample_model(),default_view='simple'))
    with sync_playwright() as pw:
        browser=pw.chromium.launch();page=browser.new_page();page.goto(path.as_uri())
        f=page.frame_locator('#report-frame');table=f.locator('[data-metric-grid]').first
        for theme in ['blackout','graphite','midnight','studio-light']:
            page.locator('#theme-picker').select_option(theme)
            page.mouse.move(0,0)
            header=table.locator('thead th').nth(3)
            normal=header.evaluate(CONTRAST_JS)
            header.hover();hover=header.evaluate(CONTRAST_JS)
            assert normal['bg']!=hover['bg']
            for cell in [header,table.locator('tbody th').first,table.locator('tbody td').first]:
                assert cell.evaluate(CONTRAST_JS)['contrast']>=4.5
            assert hover['contrast']>=4.5
            button=header.locator('button');button.click();page.mouse.move(0,0);button.evaluate('e=>e.blur()')
            selected=header.evaluate(CONTRAST_JS)
            assert selected['bg']!=normal['bg']
            assert selected['contrast']>=4.5
            assert button.evaluate("e=>getComputedStyle(e,'::after').content")=='"↓"'
            button.press('Enter')
            assert button.evaluate("e=>getComputedStyle(e,'::after').content")=='"↑"'
            assert button.evaluate('e=>getComputedStyle(e).outlineStyle')!='none'
            table.locator('thead button').first.click()
            table.locator('tbody th').first.hover()
            assert table.locator('tbody th').first.evaluate(CONTRAST_JS)['contrast']>=4.5
            assert table.locator('tbody td').first.evaluate(CONTRAST_JS)['contrast']>=4.5
        browser.close()


def test_boon_labels_are_concise_without_changing_values():
    model=sample_model()
    model['boon_generation']={'scope':'session','unit':'weighted_generation','rows':[{'name':'Test Player','profession':'Guardian','boons':{'Might':12.345,'Fury':2.5},'source_total':14.845}]}
    html=render_js(model,'multiboonGenerationChart(false)')
    assert '<h2>Squad boons</h2><span>Boon output</span>' in html
    assert 'Test Player · Might: 12.35' in html
    assert 'Test Player · Boon output: 14.85' in html
    assert 'weighted' not in html.lower()
    assert ' / sec' not in html
    assert render_js(model,'(multiboonGenerationChart(false),model.boon_generation)')==model['boon_generation']
