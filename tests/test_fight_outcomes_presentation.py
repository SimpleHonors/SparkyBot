"""Generated-browser regressions; sample values are NOT a real raid fixture."""
from copy import deepcopy
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from core.fight_outcomes import PAST_FIGHTS
from core.night_model import build_night_model
from core.report_viewer import build_switchable_report, unpack_classic_report, unpack_night_model

SAMPLE_LABEL = "Synthetic comparison fixture"
CLASSIC_SAMPLE = "<!doctype html><html><title>Layout sample only</title><body>Fictional layout fixture; no real combat report.</body></html>"


def sample_model():
    model = build_night_model([])
    model["session"]["report_title"] = SAMPLE_LABEL
    wins = dict(stab_at_worst_hit=32, opening_trade=2.4, enemy_down_first_pct=55,
                cc_received_per_member_min=0.6, stuns_broken_pct=28, downs_to_kills_pct=100)
    losses = dict(stab_at_worst_hit=49, opening_trade=0.7, enemy_down_first_pct=64,
                  cc_received_per_member_min=1.1, stuns_broken_pct=25, downs_to_kills_pct=48)
    # Isolate rendering with explicit synthetic summary values.
    fights = [dict(result=result, time_start=None, first_down=None, **values)
              for result, values in [("win", wins)] * 2 + [("loss", losses)] * 2]
    model["fight_outcomes"] = dict(version=1, fights=fights, parties=[],
        summary=dict(fights=4, wins=2, losses=2, wins_avg=wins, losses_avg=losses),
        past_fights=deepcopy(PAST_FIGHTS))
    return model


def save_report(path, model, *, default_view="simple", classic=CLASSIC_SAMPLE):
    original = deepcopy(model)
    report = build_switchable_report(classic, model, default_view=default_view)
    assert model == original, "Renderer mutated its input model"
    assert unpack_night_model(report) == original
    assert unpack_classic_report(report) == classic
    path.write_text(report, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        yield browser
        browser.close()


def open_report(browser, path):
    page = browser.new_page(viewport=dict(width=1440, height=1100))
    page.errors = []
    page.requests_seen = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    page.on("request", lambda request: page.requests_seen.append(request.url)
            if request.url.startswith(("http:", "https:")) else None)
    page.route("http://**/*", lambda route: route.abort())
    page.route("https://**/*", lambda route: route.abort())
    page.goto(path.as_uri())
    page.wait_for_function("Boolean(document.querySelector('#report-frame').contentDocument?.querySelector('main'))")
    return page, page.frames[1]


def test_generated_simple_shows_measured_comparisons_before_details(browser, tmp_path):
    page, frame = open_report(browser, save_report(tmp_path / "sample.html", sample_model()))
    section = frame.locator("[data-fight-outcomes]")
    assert section.locator("h2").inner_text() == "How wins and losses differed"
    assert section.locator("[data-fo-comparison]").count() == 3
    paragraph = section.locator("[data-fo-assessment]").inner_text()
    assert "higher in wins (2.4× vs 0.7×)" in paragraph
    assert "fewer CC events" in paragraph and "0.6 vs 1.1" in paragraph
    assert "32% in wins and 49% in losses" in paragraph
    assert section.locator("[data-fo-measures]").get_attribute("open") is None
    assert section.locator("[data-fo-history]").get_attribute("open") is None
    assert section.locator("[data-fo-comparison='opening_trade']").inner_text().count("1× means equal damage") == 1
    for view in ("simple", "sparky"):
        if view != "simple":
            page.locator('[data-view="sparky"]').click()
            frame = page.frames[1]
            frame.locator('[data-subnav="overview"]').wait_for()
            frame.locator('[data-fight-outcomes]').wait_for()
            section = frame.locator('[data-fight-outcomes]')
        measures = section.locator('[data-fo-measures]')
        measures.locator('summary').focus()
        page.keyboard.press('Enter')
        assert measures.evaluate('(el) => el.open')
        rows = measures.locator('tbody tr')
        assert rows.count() == 6
        assert rows.nth(0).locator('td').all_text_contents() == ['32%', '49%']
        assert rows.nth(1).locator('td').all_text_contents() == ['2.4×', '0.7×']
        assert rows.nth(2).locator('td').all_text_contents() == ['55%', '64%']
        assert rows.nth(3).locator('td').all_text_contents() == ['0.6', '1.1']
        assert rows.nth(4).locator('td').all_text_contents() == ['28%', '25%']
        assert rows.nth(5).locator('td').all_text_contents() == ['1', '0.48']
        assert [rows.nth(i).locator('th').evaluate('(el) => el.firstChild.textContent') for i in range(6)] == [
            'Stability when hit hardest', 'Opening damage: dealt vs taken',
            'Enemy went down first', 'CC received per player / min',
            'Stun breaks vs CC received', 'Enemy deaths for each down',
        ]
        # Methodology belongs in the private audit, not a relocated UI footnote.
        for phrase in ('geometric mean', 'all-target', 'all-source', 'denominator'):
            assert phrase not in measures.inner_text().lower()
        assert 'skill presses' not in measures.inner_text()
        history = section.locator('[data-fo-history]')
        history.locator('summary').focus()
        page.keyboard.press('Space')
        assert history.evaluate('(el) => el.open')
        assert '33% or lower: 28% wins' in history.inner_text()
        assert history.locator('[data-fo-comparison]').count() == 0
        assert 'downs finished' not in section.inner_text().lower()
        assert not section.locator('td[title*="past fights"]').count()
    assert not page.errors
    assert not page.requests_seen
    page.close()


def test_no_outcomes_omits_section_in_both_views(browser, tmp_path):
    model = build_night_model([])
    assert model['fight_outcomes'] is None
    page, frame = open_report(browser, save_report(tmp_path / 'absent.html', model))
    assert frame.locator('[data-fight-outcomes]').count() == 0
    page.locator('[data-view="sparky"]').click()
    frame = page.frames[1]
    frame.locator('[data-subnav="overview"]').wait_for()
    assert frame.locator('[data-fight-outcomes]').count() == 0
    assert not page.errors and not page.requests_seen
    page.close()


def test_generated_missing_values_are_distinct_from_observed_zero(browser, tmp_path):
    model = sample_model()
    summary = model['fight_outcomes']['summary']
    for key in summary['wins_avg']:
        summary['wins_avg'][key] = 0
        summary['losses_avg'][key] = None
    summary['wins_avg']['downs_to_kills_pct'] = 175
    page, frame = open_report(browser, save_report(tmp_path / 'missing-zero.html', model))
    section = frame.locator('[data-fight-outcomes]')
    assert section.locator('[data-fo-assessment]').inner_text() == 'No measured win/loss pairs are available.'
    for key, zero in [('opening_trade', '0×'), ('cc_received_per_member_min', '0'), ('stab_at_worst_hit', '0%')]:
        card = section.locator('[data-fo-comparison="' + key + '"]')
        assert card.locator('[data-fo-group="wins"] b').inner_text() == zero
        assert card.locator('[data-fo-group="wins"] .fo-fill').evaluate('(el) => el.getBoundingClientRect().width') == 0
        assert card.locator('[data-fo-group="losses"] b').inner_text() == '—'
        assert card.locator('[data-fo-group="losses"] b').get_attribute('aria-label') == 'No measured value'
        assert card.locator('[data-fo-group="losses"] .fo-fill').count() == 0
    measures = section.locator('[data-fo-measures]')
    measures.locator('summary').focus()
    page.keyboard.press('Enter')
    assert measures.locator('tbody tr').nth(5).locator('td').all_text_contents() == ['1.75', '—']
    assert '— = not recorded' in measures.inner_text()
    assert not page.errors and not page.requests_seen
    page.close()
