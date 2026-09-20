"""Metric header cues keep real values and compact paired columns intact."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright
from core.report_viewer import build_switchable_report
from test_report_refresh import sample_model


def test_metric_cues_real_browser(tmp_path):
    source = os.environ.get('METRIC_CUES_MODEL')
    model = json.loads(Path(source).read_text()) if source else sample_model()
    before = json.dumps(model, sort_keys=True)
    path = tmp_path / 'metric-cues.html'
    path.write_text(build_switchable_report('<p>Classic diagnostic placeholder</p>', model, default_view='simple'))
    assert json.dumps(model, sort_keys=True) == before
    evidence = os.environ.get('METRIC_CUES_EVIDENCE')
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1500})
        page.goto(path.as_uri())
        frame = page.frame_locator('#report-frame')
        frame.locator('.metric-grid').first.wait_for()
        baseline = frame.locator('.metric-grid tbody tr').evaluate_all('(rows)=>rows.map(r=>Object.fromEntries(Array.from(r.attributes).filter(a=>/^data-m/.test(a.name)).map(a=>[a.name,a.value])))')
        for width in (1440, 1024, 390):
            page.set_viewport_size({'width': width, 'height': 1900})
            frame.locator('main').evaluate("e=>e.ownerDocument.defaultView.dispatchEvent(new Event('resize'))")
            page.wait_for_timeout(150)
            for group in ('damage', 'healing', 'utility'):
                table = frame.locator('[data-metric-grid='+group+']')
                assert table.locator('thead tr').count() == 1
                assert table.locator('thead .pair-total svg.metric-cue-icon').count() == table.locator('thead .pair-total').count()
                assert table.locator('thead .pair-total .metric-cue-label').evaluate_all('(es)=>es.every(e=>e.textContent.trim())')
                checks = table.evaluate('''t=>({overflow:t.scrollWidth>t.parentElement.clientWidth+1,height:t.tHead.getBoundingClientRect().height, bad:Array.from(t.tHead.querySelectorAll('button')).filter(e=>e.getBoundingClientRect().width && e.scrollWidth>e.clientWidth+1).map(e=>e.textContent),font:getComputedStyle(t.querySelector('td.number')).fontSize,condensed:t.classList.contains('responsive-condensed')})''')
                assert not checks['overflow'], (width, group, checks)
                assert not checks['bad'], (width, group, checks)
                assert checks['height'] <= 32, checks
                assert checks['font'] == '14px'
                if width >= 1024:
                    assert not checks['condensed']
                if evidence:
                    Path(evidence).mkdir(parents=True, exist_ok=True)
                    table.locator('xpath=ancestor::article[1]').screenshot(path=str(Path(evidence)/f'{group}-{width}.png'))
        assert frame.locator('.metric-grid tbody tr').evaluate_all('(rows)=>rows.map(r=>Object.fromEntries(Array.from(r.attributes).filter(a=>/^data-m/.test(a.name)).map(a=>[a.name,a.value])))') == baseline
        browser.close()
