"""Team-isolation regressions against the generated production JavaScript."""
import unittest

from tests.test_report_refresh import render_js


def trait(name, role, count=1, eligible=4, percent=25):
    return dict(trait=name, roles=[role], specialization='Firebrand',
                observed_skill=name + ' proc', actor_appearances=count,
                eligible_actor_appearances=eligible, observed_percent=percent)


def model_fixture():
    red = {'professions': {'Firebrand': {'traits': [trait('Red Healing', 'healing')]}}}
    blue = {'professions': {'Firebrand': {'traits': [trait('Blue Damage', 'damage', 2, 5, 40)]}}}
    global_evidence = {'professions': {'Firebrand': {'traits': [
        trait('Global Only', 'control'), *red['professions']['Firebrand']['traits'],
        *blue['professions']['Firebrand']['traits']]}}, 'teams': {'red': red, 'blue': blue}}
    scopes = [dict(id=color, color=color, label=color.title(), fight_indexes=[i],
                   role_validation=evidence)
              for i, (color, evidence) in enumerate([('red', red), ('blue', blue)])]
    fights = [dict(index=i, color=color, enemy_count=1,
                   professions=[dict(profession='Firebrand', count=1)])
              for i, color in enumerate(['red', 'blue'])]
    return {'enemy_intel': {'role_validation': global_evidence, 'scopes': scopes, 'fights': fights}}


class TeamTraitViewerTests(unittest.TestCase):
    def test_selected_team_isolates_traits_and_role_candidates(self):
        model = model_fixture()
        for index, own, other, role in [(0, 'Red Healing', 'Blue Damage', 'Support / Healing'),
                                         (1, 'Blue Damage', 'Red Healing', 'DPS')]:
            page = render_js(model, f'allFightsCompositionView(model.enemy_intel.scopes[{index}])')
            self.assertIn(own, page)
            self.assertNotIn(other, page)
            self.assertNotIn('Global Only', page)
            self.assertIn(role, page)
            if index == 1:
                self.assertNotIn('Support / Healing', page)

    def test_trait_rates_show_eligible_appearances_without_disclaimers(self):
        page = render_js(model_fixture(), 'allFightsCompositionView(model.enemy_intel.scopes[0])')
        self.assertIn('1 / 4 eligible appearances', page)
        self.assertIn('25% observed', page)
        self.assertIn('&gt;=15 seconds active', page)
        self.assertNotIn('Repeated players count once per fight', page)
        self.assertNotIn('not exact equip prevalence', page)
        self.assertIn('role="meter"', page)
        self.assertIn('aria-valuemin="0" aria-valuemax="100" aria-valuenow="25"', page)
        self.assertNotIn('color are not retained', page)

    def test_legacy_team_evidence_is_unavailable_not_global(self):
        model = model_fixture()
        for evidence in [None, {'status': 'team_attribution_unavailable', 'professions': {}}]:
            scope = model['enemy_intel']['scopes'][0]
            if evidence is None:
                scope.pop('role_validation', None)
            else:
                scope['role_validation'] = evidence
            page = render_js(model, 'allFightsCompositionView(model.enemy_intel.scopes[0])')
            self.assertIn('Team-attributed evidence unavailable', page)
            for forbidden in ['Red Healing', 'Blue Damage', 'Global Only', 'Likely Support / Healing']:
                self.assertNotIn(forbidden, page)

    def test_global_view_is_explicitly_all_opponents(self):
        page = render_js(model_fixture(), 'allFightsCompositionView()')
        for name in ['Red Healing', 'Blue Damage', 'Global Only', 'All opponents', '40% observed']:
            self.assertIn(name, page)

    def test_legacy_trait_rate_is_not_invented(self):
        model = model_fixture()
        item = model['enemy_intel']['scopes'][0]['role_validation']['professions']['Firebrand']['traits'][0]
        item.pop('eligible_actor_appearances')
        item.pop('observed_percent')
        page = render_js(model, 'allFightsCompositionView(model.enemy_intel.scopes[0])')
        self.assertIn('eligible denominator / rate unavailable', page)
        self.assertNotIn('0% observed', page)

    def test_trait_meter_retains_source_percentage_and_spec_filter(self):
        model = model_fixture()
        evidence = model['enemy_intel']['scopes'][0]['role_validation']
        item = evidence['professions']['Firebrand']['traits'][0]
        item.update(trait="Smiter's Boon", specialization='Valor',
                    observed_skill='Lesser Smite Condition', actor_appearances=51,
                    eligible_actor_appearances=127, observed_percent=40.16,
                    trait_description='Secondary mechanics text')
        evidence['professions']['Druid'] = {'traits': [trait('Other spec', 'healing')]}
        page = render_js(model, 'enemyBuildEvidenceView(model.enemy_intel.scopes[0].role_validation,["Firebrand"],"Red")')
        for text in ['40.16% observed', '51 / 127 eligible appearances',
                     'width:40.16%', 'Valor', 'Lesser Smite Condition',
                     '<details class="trait-mechanics"><summary>Mechanics</summary>']:
            self.assertIn(text, page)
        self.assertNotIn('Other spec', page)
        for percent in [0, 100]:
            item['observed_percent'] = percent
            page = render_js(model, 'enemyBuildEvidenceView(model.enemy_intel.scopes[0].role_validation,["Firebrand"],"Red")')
            self.assertIn(f'width:{percent}%', page)

    def test_browser_switches_teams_without_trait_or_role_leakage(self):
        import tempfile
        from pathlib import Path
        from playwright.sync_api import sync_playwright, expect
        from core.report_viewer import build_switchable_report
        model = model_fixture()
        model['enemy_intel']['scopes'].append(dict(id='green', color='green', label='Green', fight_indexes=[2]))
        model['enemy_intel']['fights'].append(dict(index=2, color='green', enemy_count=1,
            professions=[dict(profession='Firebrand', count=1)]))
        with tempfile.TemporaryDirectory() as temp, sync_playwright() as pw:
            path = Path(temp) / 'teams.html'
            path.write_text(build_switchable_report('Classic', model, default_view='sparky'))
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(path.as_uri())
            frame = page.frame_locator('#report-frame')
            frame.locator('[data-tab="enemy"]').click()
            panel = frame.locator('#enemy-panel')
            for color, own, other, role, rate in [
                ('red', 'Red Healing', 'Blue Damage', 'Likely Support / Healing', '25% observed'),
                ('blue', 'Blue Damage', 'Red Healing', 'Likely DPS', '40% observed')]:
                frame.locator(f'[data-enemy-scope="{color}"]').click()
                expect(panel).to_contain_text(own)
                expect(panel).not_to_contain_text(other)
                expect(panel).not_to_contain_text('Global Only')
                expect(panel).to_contain_text(role)
                expect(panel).to_contain_text(rate)
                finding = panel.locator('.enemy-trait').filter(has_text=own)
                expect(finding.locator('.trait-name')).to_be_visible()
                expect(finding.locator('.trait-rate')).to_be_visible()
                expect(finding.locator('.trait-count')).to_be_visible()
                expect(finding.locator('[role="meter"]')).to_be_visible()
                self.assertGreaterEqual(finding.locator('.trait-name').evaluate('(e)=>parseFloat(getComputedStyle(e).fontSize)'), 14)
                self.assertEqual(finding.evaluate('(e)=>!!e.closest("details:not([open])")'), False)
                for width in [1440, 390]:
                    page.set_viewport_size({'width': width, 'height': 900})
                    expect(finding.locator('.trait-name')).to_be_visible()
                    meter = finding.locator('[role="meter"]')
                    geometry = meter.evaluate('(e)=>({width:e.clientWidth,fill:e.firstElementChild.getBoundingClientRect().width,right:e.getBoundingClientRect().right,rate:Number(e.getAttribute("aria-valuenow"))})')
                    self.assertLessEqual(geometry['right'], width)
                    self.assertAlmostEqual(geometry['fill'] / geometry['width'], geometry['rate'] / 100, places=2)
            frame.locator('[data-enemy-scope="green"]').click()
            expect(panel).to_contain_text('Team-attributed evidence unavailable')
            expect(panel).not_to_contain_text('Red Healing')
            frame.locator('[data-enemy-scope="all"]').click()
            for name in ['Red Healing', 'Blue Damage', 'Global Only']:
                expect(panel).to_contain_text(name)
            self.assertEqual(errors, [])
            browser.close()


class CompositeBoonViewerTests(unittest.TestCase):
    @staticmethod
    def model():
        return {'boon_generation': {'scope': 'session', 'unit': 'weighted_generation',
                'methodology': 'Source-exported weighted generation per second; no raw units combined.',
                'rows': [dict(name='Alice', profession='Firebrand', boons={'Might': 3, 'Stability': 2, 'Fury': 0.000123}),
                         dict(name='Bob', profession='Druid', boons={'Might': 1, 'Stability': 0, 'Fury': 0})]}}

    def test_native_multiboon_stack_ranks_players_and_retains_exact_tooltips(self):
        model = self.model()
        html = render_js(model, 'boonGenerationCharts()')
        self.assertIn('data-multiboon-chart', html)
        self.assertLess(html.index('Alice'), html.index('Bob'))
        for label in ['Might: 3', 'Stability: 2', 'Other: 0', 'Squad boons', 'Boon output']:
            self.assertIn(label, html)
        # Display rounding and minor-boon grouping must not round source geometry.
        import re
        match = re.search(r'data-boon="Other"[^>]*width:([\d.]+)%', html)
        assert match is not None
        other_width = float(match.group(1))
        self.assertAlmostEqual(other_width, 0.000123 / 5.000123 * 100, places=12)
        self.assertEqual(model['boon_generation']['rows'][0]['boons']['Fury'], 0.000123)
        self.assertNotIn('below 1%', html)
        for view in ['renderSimple()', 'renderSparky()']:
            self.assertIn('data-multiboon-chart', render_js(model, view))

    def test_browser_multiboon_hover_focus_and_mobile(self):
        import tempfile
        from pathlib import Path
        from playwright.sync_api import sync_playwright, expect
        from core.report_viewer import build_switchable_report
        with tempfile.TemporaryDirectory() as temp, sync_playwright() as pw:
            path = Path(temp) / 'boons.html'
            path.write_text(build_switchable_report('Classic', self.model(), default_view='simple'))
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1100, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(path.as_uri())
            frame = page.frame_locator('#report-frame')
            chart = frame.locator('[data-multiboon-chart]')
            expect(chart).to_be_visible()
            bar = chart.locator('[data-multiboon-player="Alice"] .multiboon-bar')
            tooltip = frame.locator('#chart-popup')
            bar.locator('[data-boon="Might"]').hover()
            expect(tooltip).to_be_visible()
            expect(tooltip).to_have_text('Alice · Might: 3')
            bar.locator('[data-boon="Other"]').focus()
            expect(tooltip).to_have_text('Alice · Other: 0')
            widths = bar.locator('.multiboon-track i').evaluate_all('(els)=>els.map(e=>e.getBoundingClientRect().width)')
            self.assertGreater(widths[0], widths[1])
            self.assertGreater(widths[1], widths[2])
            page.mouse.move(0, 0)
            bar.focus()
            expect(tooltip).to_be_visible()
            page.set_viewport_size({'width': 390, 'height': 844})
            expect(chart).to_be_visible()
            self.assertLessEqual(chart.evaluate('(e)=>e.getBoundingClientRect().right'), 390)
            page.locator('[data-view="sparky"]').click()
            frame.locator('[data-tab="support"]').click()
            frame.locator('[data-subtab="boons"]').click()
            expect(frame.locator('[data-multiboon-chart]')).to_be_visible()
            self.assertEqual(errors, [])
            browser.close()

    def test_missing_or_incompatible_generation_never_gets_summed(self):
        for model in [{}, {'boon_generation': {'scope': 'session', 'unit': 'raw',
                      'rows': [dict(name='Alice', boons={'Might': 20, 'Fury': 80})]}}]:
            html = render_js(model, 'boonGenerationCharts()')
            self.assertEqual(html, '')
            self.assertNotIn('data-multiboon-player', html)


if __name__ == '__main__':
    unittest.main()
