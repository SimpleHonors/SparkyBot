"""Focus scrolling must not let a stationary pointer select a different player."""
import pytest
from playwright.sync_api import sync_playwright, expect
from core.report_viewer import build_switchable_report
from test_report_refresh import sample_model, board


def test_support_keyboard_focus_wins_over_scroll_pointer_events(tmp_path):
    model = sample_model()
    support = next(b for b in model['stat_tables'] if b['source_key'] == 'Support-Summary')
    support['rows'] += [board('x', {'condicleanse': n, 'boonstrips': n}, 'Player'+str(n))['rows'][0]
                        for n in range(60)]
    path = tmp_path/'keyboard.html'
    path.write_text(build_switchable_report('<p>Classic</p>', model, default_view='simple'))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        page.route('http://**/*', lambda route: route.abort())
        page.route('https://**/*', lambda route: route.abort())
        page.goto(path.as_uri())
        frame = page.frame_locator('#report-frame')
        for metric in ('condicleanse', 'boonstrips'):
            chart = frame.locator('[data-bubble-chart="'+metric+'"]')
            summary = chart.locator('summary')
            summary.focus()
            summary.press('Enter')
            keys = chart.locator('[data-highlight-point]')
            keys.first.hover()
            for index in (0, 60, 0, 60):
                key = keys.nth(index)
                key.focus()
                # Allow browser-generated scroll/enter/leave events to settle.
                key.evaluate('e=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                expect(key).to_be_focused()
                expect(chart.locator('circle.point-highlight')).to_have_attribute('data-point-index', str(index))
                expect(frame.locator('#chart-popup')).to_be_visible()
                expect(frame.locator('#chart-popup')).to_have_text(key.get_attribute('data-tooltip'))
            keys.first.focus()
            page.keyboard.press('Tab')
            expect(keys.nth(1)).to_be_focused()
            expect(chart.locator('circle.point-highlight')).to_have_attribute('data-point-index', '1')
            keys.nth(2).hover()
            expect(chart.locator('circle.point-highlight')).to_have_attribute('data-point-index', '2')
            expect(frame.locator('#chart-popup')).to_have_text(keys.nth(2).get_attribute('data-tooltip'))
            summary.focus()
            summary.press('Enter')
        browser.close()


@pytest.mark.parametrize('metric', ['condicleanse', 'boonstrips'])
def test_support_pointer_owner_survives_wheel_after_focus(tmp_path, metric):
    model = sample_model()
    support = next(b for b in model['stat_tables'] if b['source_key'] == 'Support-Summary')
    support['rows'] = [board('x', {'participation_time': 100, 'condicleanse': n,
                                 'boonstrips': n}, 'Player'+str(n))['rows'][0]
                       for n in range(5)]
    path = tmp_path/'pointer-scroll.html'
    path.write_text(build_switchable_report('<p>Classic</p>', model, default_view='simple'))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.route('http://**/*', lambda route: route.abort())
        page.route('https://**/*', lambda route: route.abort())
        page.goto(path.as_uri())
        frame = page.frame_locator('#report-frame')
        chart = frame.locator('[data-bubble-chart="'+metric+'"]')
        chart.locator('summary').click()
        keys = chart.locator('[data-highlight-point]')
        keys.nth(0).focus()
        keys.nth(1).hover()
        popup = frame.locator('#chart-popup')
        expected = keys.nth(1).get_attribute('data-tooltip')
        assert 'Player3' in expected
        expect(popup).to_have_text(expected)
        expect(chart.locator('circle.point-highlight')).to_have_attribute('data-point-index', '1')
        # Real wheel input, not a synthetic scroll; wait for actual scroll delivery
        # and its deferred popup callback while B remains under the pointer.
        keys.nth(1).evaluate('''e=>{window.__wheelScrollSeen=false;
            document.addEventListener('scroll',()=>{window.__wheelScrollSeen=true},
                                      {capture:true,once:true});}''')
        page.mouse.wheel(0, 1)
        page.wait_for_function("document.querySelector('#report-frame').contentWindow.__wheelScrollSeen")
        keys.nth(1).evaluate('e=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
        expect(keys.nth(0)).to_be_focused()
        assert keys.nth(1).evaluate("e=>e.matches(':hover')")
        expect(chart.locator('circle.point-highlight')).to_have_attribute('data-point-index', '1')
        expect(popup).to_be_visible()
        expect(popup).to_have_text(expected)
        browser.close()
