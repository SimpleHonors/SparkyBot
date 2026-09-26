"""Hash-bound hosted links; fixtures never contact the upload service."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from core import dpsreport


def test_successful_upload_persists_exact_raw_and_ei_without_rewriting(tmp_path, monkeypatch):
    from core import fight_links
    monkeypatch.setattr(fight_links, 'app_dir', lambda: tmp_path)
    raw = tmp_path / 'fight.zevtc'
    ei = tmp_path / 'fight.json'
    raw.write_bytes(b'exact raw fixture')
    ei.write_bytes(b'{"uploadLinks":[""],"timeEnd":"same clock"}')
    before = [p.read_bytes() for p in (raw, ei)]
    post = Mock(return_value=Mock(status_code=200, json=lambda: {'permalink': 'https://dps.report/fixture-one'}))
    monkeypatch.setattr(dpsreport.requests, 'post', post)
    assert dpsreport.upload_log(raw, ei_json=ei) == 'https://dps.report/fixture-one'
    assert fight_links.report_url(ei) == 'https://dps.report/fixture-one'
    assert [p.read_bytes() for p in (raw, ei)] == before
    ei.write_bytes(b'{"uploadLinks":[""],"timeEnd":"same clock", "other":true}')
    assert fight_links.report_url(ei) is None
    assert post.call_count == 1


def test_staging_recovers_url_from_hash_metadata_only(tmp_path, monkeypatch):
    from core import fight_links
    from core.raid_report import _stage_combiner_json
    monkeypatch.setattr(fight_links, 'app_dir', lambda: tmp_path)
    raw = tmp_path / 'fight.zevtc'
    ei = tmp_path / 'fight.json'
    raw.write_bytes(b'raw')
    ei.write_text('{"uploadLinks":[""],"timeEnd":"same"}')
    before = ei.read_bytes()
    fight_links.remember(fight_links.digest(raw), fight_links.digest(ei), 'https://dps.report/fixture-one')
    staged = tmp_path / 'staged.json'
    _stage_combiner_json(ei, staged)
    assert json.loads(staged.read_text())['uploadLinks'][0] == 'https://dps.report/fixture-one'
    assert ei.read_bytes() == before
    ei.write_text('{"uploadLinks":[""],"timeEnd":"same","different":true}')
    _stage_combiner_json(ei, staged)
    assert json.loads(staged.read_text())['uploadLinks'] == ['']


def test_fresh_parse_recovers_by_raw_hash_not_clock_or_filename(tmp_path, monkeypatch):
    from datetime import datetime
    from core import fight_links
    from core.raid_report import RaidReportRunner
    from core.raid_session import RaidReportCache, LogInfo
    monkeypatch.setattr(fight_links, 'app_dir', lambda: tmp_path)
    raw = tmp_path / 'fight.zevtc'
    raw.write_bytes(b'raw exact bytes')
    fight_links.remember(fight_links.digest(raw), 'old-ei-hash', 'https://dps.report/fixture-one')
    ei = tmp_path / 'new.json'
    ei.write_text('{"uploadLinks":[""],"newParser":true}')
    runner = RaidReportRunner(log_folder=tmp_path, cache=RaidReportCache(tmp_path/'cache'),
        parse_log=lambda _: ei, ei_version='new', settings_fingerprint='new',
        combiner=None, viewer_html=tmp_path/'unused', output_dir=tmp_path/'out')
    paths, failures = runner._parse_missing_logs([LogInfo(raw, datetime.now(), 'mtime')], total_selected=1, done_already=0)
    assert not failures
    assert fight_links.report_url(paths[0]) == 'https://dps.report/fixture-one'


def test_default_visible_links_both_views_mobile_and_safe_urls(tmp_path):
    from core.night_model import build_night_model
    from core.report_viewer import build_switchable_report, unpack_classic_report
    from playwright.sync_api import sync_playwright
    model = build_night_model([{'title':'test-Overview', 'text':
        '|!#|!Fight Link|!Duration|h\n|1|2026-09-19 - 14:00:45|45s|\n|2|2026-09-19 - 14:01:45|45s|\n|3|2026-09-19 - 14:02:45|45s|'}])
    # Synthetic actual-format permalink; browser routes block all external requests.
    model['fights'][0]['report_url'] = 'https://dps.report/Ab12-20260919-140045_wvw?a=1&b=2'
    model['fights'][1]['report_url'] = 'javascript:alert(1)'
    import copy
    for bad in ('data:text/html,<script>alert(1)</script>',
                'https://example.invalid/" onclick="alert(1)',
                'https://user:pass@example.invalid/fight', '//example.invalid/fight'):
        extra=copy.deepcopy(model['fights'][2])
        extra.update(index=len(model['fights'])+1, report_url=bad)
        model['fights'].append(extra)
    classic = '<html><body>Exact Classic bytes</body></html>'
    html = build_switchable_report(classic, model)
    assert unpack_classic_report(html) == classic
    path = tmp_path/'report.html'
    path.write_text(html)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.route('http://**/*', lambda route: route.abort())
        page.route('https://**/*', lambda route: route.abort())
        page.goto(path.as_uri())
        for width in (1440, 390):
            page.set_viewport_size({'width':width,'height':900})
            for view in ('simple','sparky'):
                page.locator(f'[data-view="{view}"]').click()
                section = page.frame_locator('#report-frame').locator('[data-fight-links]')
                assert section.is_visible()
                assert section.locator('li').count() == 1
                assert 'No report link' not in section.inner_text()
                assert section.locator('img,script,[onclick],[onerror]').count() == 0
                link = section.get_by_role('link', name='Open report')
                assert link.count() == 1 and link.is_visible()
                assert link.get_attribute('href') == model['fights'][0]['report_url']
                assert 'Fight 1' in section.locator('li').first.inner_text()
                assert section.evaluate('(e)=>e.scrollWidth<=e.clientWidth')
                assert page.frame_locator('#report-frame').locator('a[href^="javascript:"]').count() == 0
        for enabled in (False, True):
            if enabled:
                model['fights'][0]['report_url'] = ''
            path.write_text(build_switchable_report(classic, model, dpsreport_links_enabled=enabled))
            page.goto(path.as_uri())
            for view in ('simple', 'sparky'):
                page.locator(f'[data-view="{view}"]').click()
                frame = page.frame_locator('#report-frame')
                frame.locator('body').wait_for()
                assert frame.locator('[data-fight-links]').count() == 0
                assert 'No report link' not in frame.locator('body').inner_text()
        browser.close()


@pytest.mark.parametrize('timing', dpsreport.VALID_TIMINGS)
def test_actual_watcher_upload_branches_persist_before_discord_delivery(timing):
    from test_sparky_wall_pipeline import ApplicationCallbackTests
    from core import fight_links
    from unittest.mock import patch
    case = ApplicationCallbackTests()
    case.setUp()
    try:
        case.enterContext(patch.object(fight_links, 'app_dir', return_value=case.home))
        case.config.enable_ai_analysis = False
        case.config.enable_twitch = False
        case.config.raidreport_cache_enabled = True
        case.config.dpsreport_links_enabled = True
        case.config.dpsreport_timing = timing
        case.enterContext(patch.object(case.config, 'get_raidreport_cache_dir', return_value=case.home/'cache'))
        case.invoker.cache_key.return_value = ('fixture', 'fixture')
        case.raw.write_bytes(b'exact callback raw')
        case.http.return_value.json.return_value = {'permalink':'https://dps.report/callback-fixture'}
        observed = []
        def send(**kw):
            if 'https://dps.report/callback-fixture' in json.dumps(kw):
                observed.append(fight_links.report_url(case.path))
            return 1
        case.discord.send_to_all.side_effect = send
        case.callback(case.raw)
        assert case.results[-1][1] == 'success'
        assert observed == ['https://dps.report/callback-fixture']
        cached = next((case.home/'cache').rglob('*.json'))
        assert json.loads(cached.read_text()) == case.data
        assert fight_links.report_url(cached) == 'https://dps.report/callback-fixture'
        assert case.raw.read_bytes() == b'exact callback raw'
        assert case.http.call_count == 1
    finally:
        case.doCleanups()


@pytest.mark.parametrize('url', ['javascript:alert(1)', '//dps.report/a', 'https://user:pass@dps.report/a',
    'https://dps.report/\nfoo', 'https://dps.report/" onclick="alert(1)', 'https://dps.report/\\foo',
    'https://dps.report:bad/a', 'https://dps.report/\x7f', 'https://dps.report/x]]|table-cell'])
def test_invalid_upload_url_is_not_persisted(tmp_path, monkeypatch, url):
    from core import fight_links
    monkeypatch.setattr(fight_links, 'app_dir', lambda: tmp_path)
    raw = tmp_path/'fight.zevtc'
    raw.write_bytes(b'raw')
    monkeypatch.setattr(dpsreport.requests, 'post', Mock(return_value=Mock(status_code=200, json=lambda:{'permalink':url})))
    assert dpsreport.upload_log(raw) is None
    assert not (tmp_path/'sparkybot_fight_links.sqlite3').exists()


def test_ambiguous_binding_changed_raw_failure_and_existing_ei_link(tmp_path, monkeypatch):
    from core import fight_links
    from core.raid_report import _stage_combiner_json
    monkeypatch.setattr(fight_links, 'app_dir', lambda: tmp_path)
    raw = tmp_path/'fight.zevtc'; raw.write_bytes(b'raw')
    ei = tmp_path/'fight.json'; ei.write_text('{"uploadLinks":["https://dps.report/original"]}')
    def changed(*a, **kw):
        raw.write_bytes(b'changed during upload')
        return Mock(status_code=200, json=lambda:{'permalink':'https://dps.report/changed'})
    monkeypatch.setattr(dpsreport.requests, 'post', changed)
    dpsreport.upload_log(raw, ei_json=ei)
    assert fight_links.report_url(ei) is None
    fight_links.remember('one', fight_links.digest(ei), 'https://dps.report/one')
    fight_links.remember('two', fight_links.digest(ei), 'https://dps.report/two')
    assert fight_links.report_url(ei) is None
    staged=tmp_path/'staged.json'; _stage_combiner_json(ei, staged)
    assert json.loads(staged.read_text())['uploadLinks'] == ['https://dps.report/original']


@pytest.mark.parametrize('cell', [
    '[[2026-09-19 - 14:00:45|https://example.invalid/fixture]]',
    '<a href="https://example.invalid/fixture">2026-09-19 - 14:00:45</a>',
    'https://example.invalid/fixture'])
def test_existing_combined_source_url_formats_are_preserved(cell):
    from core.night_model import build_night_model
    model = build_night_model([{'title':'test-Overview', 'text':
        '|!#|!Fight Link|!Duration|h\n|1|'+cell+'|45s|'}])
    assert model['fights'][0]['report_url'] == 'https://example.invalid/fixture'


def test_storage_failure_and_failed_upload_do_not_create_links(tmp_path, monkeypatch):
    from core import fight_links
    monkeypatch.setattr(fight_links, 'app_dir', lambda: tmp_path)
    raw=tmp_path/'raw.zevtc'; raw.write_bytes(b'raw')
    ei=tmp_path/'ei.json'; ei.write_bytes(b'{}')
    response=Mock(status_code=500)
    monkeypatch.setattr(dpsreport.requests, 'post', Mock(return_value=response))
    assert dpsreport.upload_log(raw, ei_json=ei) is None
    assert fight_links.report_url(ei) is None
    response.status_code=200
    response.json.return_value={'permalink':'https://dps.report/fixture'}
    (tmp_path/'sparkybot_fight_links.sqlite3').mkdir()
    assert dpsreport.upload_log(raw, ei_json=ei) == 'https://dps.report/fixture'
    assert fight_links.report_url(ei) is None


def test_link_survives_new_interpreter_and_renamed_identical_ei(tmp_path, monkeypatch):
    import subprocess, sys
    from core import fight_links
    monkeypatch.setattr(fight_links, 'app_dir', lambda: tmp_path)
    ei=tmp_path/'ei.json'; ei.write_bytes(b'{"unique":"restart"}')
    fight_links.remember('raw-hash',fight_links.digest(ei),'https://dps.report/restart')
    renamed=tmp_path/'renamed.json'; ei.rename(renamed)
    script=('from pathlib import Path; import sys; from core import fight_links; '
            'fight_links.app_dir=lambda:Path(sys.argv[1]); print(fight_links.report_url(Path(sys.argv[2])))')
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path),str(renamed)],
                          capture_output=True,text=True,check=True)
    assert result.stdout.strip() == 'https://dps.report/restart'
