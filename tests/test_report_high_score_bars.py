"""High Scores must be real, sortable full-width ranking bars."""
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from core.report_viewer import build_switchable_report
from test_report_refresh import render_js


def high_scores_model():
    return {'high_scores': {'blocks': [{'caption': 'Highest 1s Burst Damage', 'rows': [
        {'name': name, 'profession': profession, 'account': 'test.1234',
         'score': score, 'fight': i + 1, 'details': ['Riposte'] if i == 0 else []}
        for i, (name, profession, score) in enumerate([
            ('Alice Long Player Name', 'Firebrand', 15000), ('Bob', 'Revenant', 9000),
            ('Cara', 'Guardian', 98.123456), ('Dora', 'Guardian', 98.123455),
            ('Eve', 'Guardian', 42.5), ('Finn', 'Guardian', 1), ('Zero', 'Guardian', 0)])]}]}}


@pytest.mark.parametrize('width', [1440, 1024, 390])
def test_browser_ranking_geometry_sort_drill_expand(tmp_path, width):
    report = tmp_path / 'report.html'
    report.write_text(build_switchable_report('<p>Test Classic</p>', high_scores_model()))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(report.as_uri())
        page.locator('[data-view="sparky"]').click()
        frame = page.frame_locator('#report-frame')
        frame.locator('[data-tab="scores"]').click()
        frame.locator('[data-subtab="all"]').click()
        board = frame.locator('.high-score-grid .board:visible').first
        rows = board.locator('tbody tr')
        assert rows.count() == 7
        geometry = rows.first.evaluate('''r => ({row:r.getBoundingClientRect().width,
            fill:parseFloat(getComputedStyle(r,'::after').width),
            color:getComputedStyle(r,'::after').backgroundColor})''')
        assert geometry['fill'] > geometry['row'] * .75, geometry
        second = rows.nth(1).evaluate("r=>parseFloat(getComputedStyle(r,'::after').width)")
        assert second / geometry['fill'] == pytest.approx(.6, abs=.005)
        assert rows.first.locator('[data-drill]').count() == 1
        assert 'Fight 1' in rows.first.inner_text() and 'Riposte' in rows.first.inner_text()
        assert 'Riposte' not in rows.nth(1).inner_text()
        assert rows.nth(2).get_attribute('data-score') == '98.123456'
        assert '98.123456' not in rows.nth(2).inner_text()
        assert '98.12' in rows.nth(2).inner_text()
        assert board.locator('tbody tr:visible').count() == 5
        assert board.locator('[data-sort-key="score"]').count() == 0
        assert rows.evaluate_all("rs=>rs.map(r=>Number(r.dataset.score))") == [15000, 9000, 98.123456, 98.123455, 42.5, 1, 0]
        assert rows.evaluate_all("rs=>rs.map(r=>r.querySelector('[data-rank-cell]').textContent)") == ['1','2','3','4','5','6','7']
        assert rows.first.get_attribute('data-score') == '15000'
        for key in ['Enter', 'Space']:
            drill = rows.first.locator('[data-drill]')
            drill.focus()
            drill.press(key)
            assert frame.locator('#drilldown').is_visible()
            assert '15,000' in frame.locator('#drilldown').inner_text()
            frame.locator('#drill-close').click()
        board.locator('[data-expand-board]').click()
        assert board.locator('tbody tr:visible').count() == 7
        assert rows.last.evaluate("r=>parseFloat(getComputedStyle(r,'::after').width)") == 0
        overflow = board.evaluate('''b=>({board:b.scrollWidth-b.clientWidth,
            document:b.ownerDocument.documentElement.scrollWidth-b.ownerDocument.documentElement.clientWidth,
            rows:[...b.querySelectorAll('tbody tr')].map(r=>r.scrollWidth-r.clientWidth)})''')
        assert overflow['board'] <= 1 and overflow['document'] <= 1, overflow
        assert max(overflow['rows']) <= 1, overflow
        evidence = os.environ.get('HIGH_SCORE_EVIDENCE_DIR')
        if evidence:
            Path(evidence).mkdir(parents=True, exist_ok=True)
            board.screenshot(path=str(Path(evidence) / f'fixture-{width}.png'))
        board.locator('[data-expand-board]').click()
        assert board.locator('tbody tr:visible').count() == 5
        assert errors == []
        browser.close()


def test_filter_empty_and_profession_color():
    model = high_scores_model()
    html = render_js(model, 'highScoreGrid([])')
    assert 'data-score="98.123456"' in html
    assert '--score-color:' + render_js(model, 'professionColor("Firebrand")') in html
    assert render_js(model, 'highScoreGridExact(["Not a metric"])') == ''
    assert render_js({}, 'highScoreGrid([])') == ''
