"""The full hosted-report list belongs only to Simple and Pro Night Summary."""
import pytest
from core.night_model import build_night_model
from core.report_viewer import build_switchable_report
from playwright.sync_api import sync_playwright


@pytest.mark.parametrize('enabled', [True, False])
def test_fight_list_overview_navigation(tmp_path, enabled):
    model = build_night_model([{'title': 'test-Overview', 'text':
        '|!#|!Fight Link|!Duration|h\n|1|2026-09-19 - 14:00:45|45s|\n|2|2026-09-19 - 14:01:45|45s|'}])
    model['fights'][0]['report_url'] = 'https://example.invalid/fight-one'
    path = tmp_path / 'report.html'
    path.write_text(build_switchable_report('<html>Classic</html>', model,
                                           dpsreport_links_enabled=enabled))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.route('https://**/*', lambda route: route.abort())
        page.goto(path.as_uri())
        for view in ('simple', 'sparky'):
            page.locator(f'[data-view={view}]').click()
            frame = page.frame_locator('#report-frame')
            assert frame.locator('[data-fight-links]:visible').count() == int(enabled)
            assert frame.locator('[data-fight-links] li').count() == int(enabled)
        tabs = frame.locator('[data-tab]').evaluate_all('(es)=>es.map(e=>e.dataset.tab)')
        for tab in tabs:
            frame.locator(f'[data-tab="{tab}"]').click()
            subs = frame.locator(f'[data-subnav="{tab}"] [data-subtab]').evaluate_all('(es)=>es.map(e=>e.dataset.subtab)')
            for sub in subs or [None]:
                if sub:
                    frame.locator(f'[data-subnav="{tab}"] [data-subtab="{sub}"]').click()
                expected = enabled and tab == 'overview' and sub == 'summary'
                assert frame.locator('[data-fight-links]:visible').count() == int(expected), (tab, sub)
                if not enabled:
                    assert frame.locator('a[href="https://example.invalid/fight-one"]').count() == 0
        frame.locator('[data-tab=details]').click()
        frame.locator('[data-subnav=details] [data-subtab=fights]').click()
        assert frame.locator('a[href="https://example.invalid/fight-one"]:visible').count() == int(enabled)
        frame.locator('[data-tab=overview]').click()
        frame.locator('[data-subnav=overview] [data-subtab=summary]').click()
        assert frame.locator('[data-fight-links]:visible').count() == int(enabled)
        browser.close()
