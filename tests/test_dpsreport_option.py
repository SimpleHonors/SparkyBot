"""One upload/link preference, exercised offline through production entry points."""
import json

from unittest.mock import Mock

import pytest

from core.config import Config
from core import dpsreport


@pytest.mark.parametrize('saved', [None, '', '[DpsReport]\ndpsReportTiming =\n'])
def test_new_and_unset_option_defaults_on_without_writing(tmp_path, saved):
    path = tmp_path / 'settings.properties'
    if saved is not None:
        path.write_text(saved)
    cfg = Config(path)
    assert cfg.dpsreport_links_enabled is True
    assert cfg.dpsreport_timing == dpsreport.TIMING_TOGETHER
    assert dpsreport.links_active(cfg)
    assert path.read_text() == saved if saved is not None else not path.exists()


@pytest.mark.parametrize('timing', ['', *dpsreport.VALID_TIMINGS])
def test_saved_false_survives_load_save_reload(tmp_path, timing):
    path = tmp_path / 'settings.properties'
    path.write_text('[DpsReport]\ndpsReportLinks = false\ndpsReportTiming = '+timing+'\n')
    cfg = Config(path)
    assert not cfg.dpsreport_links_enabled
    assert not dpsreport.links_active(cfg)
    assert cfg.save()
    loaded = Config(path)
    assert not loaded.dpsreport_links_enabled
    assert not dpsreport.links_active(loaded)
    assert loaded.dpsreport_timing == dpsreport.TIMING_TOGETHER


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('entry', ['gui', 'headless'])
def test_normal_factory_cached_links_visibility_and_preservation(tmp_path, monkeypatch, enabled, entry):
    from datetime import datetime
    from core import fight_links, raid_report_wiring, raid_report
    from core.raid_session import LogInfo, RaidReportCache
    from core.report_viewer import unpack_night_model, unpack_classic_report
    from playwright.sync_api import sync_playwright
    from core.gw2ei_invoker import GW2EIInvoker
    from core.combiner_manager import CombinerManager

    cfg = Config(tmp_path/'settings.properties')
    cfg.dpsreport_links_enabled = enabled
    cfg.enable_ai_analysis = False
    cfg.guild_icon = ''
    monkeypatch.setattr(cfg, 'get_raidreport_cache_dir', lambda: tmp_path/'cache')
    monkeypatch.setattr(cfg, 'get_raidreport_output_dir', lambda: tmp_path/'out')
    monkeypatch.setattr(cfg, 'get_log_folders', lambda: [tmp_path])
    monkeypatch.setattr(GW2EIInvoker, 'cache_key', lambda self: ('fixture', 'fixture'))
    monkeypatch.setattr(GW2EIInvoker, 'parse_file', lambda *a, **kw: pytest.fail('cache miss'))
    monkeypatch.setattr(CombinerManager, 'ensure_installed', lambda self: None)
    monkeypatch.setattr(raid_report_wiring, '_resolve_viewer', lambda cfg: tmp_path/'unused')
    monkeypatch.setattr(fight_links, 'app_dir', lambda: tmp_path)
    monkeypatch.setattr(dpsreport, 'upload_log', lambda *a, **kw: pytest.fail('report must not upload'))
    raw, ei = tmp_path/'raw.zevtc', tmp_path/'parsed.json'
    raw.write_bytes(b'synthetic raw fixture, not an actual log')
    data = {'uploadLinks': [''], 'players': [], 'targets': [], 'durationMS': 45000}
    ei.write_text(json.dumps(data))
    before = (raw.read_bytes(), ei.read_bytes())
    url = 'https://example.invalid/offline-link-fixture'
    fight_links.remember(fight_links.digest(raw), fight_links.digest(ei), url)
    cache = RaidReportCache(tmp_path/'cache')
    cached = cache.store(raw, ei, 'fixture', 'fixture')
    classic = '<html><head></head><body>Unchanged Classic fixture</body></html>'
    def combine(self, input_dir, run_dir, **kw):
        staged = json.loads(next(input_dir.glob('*.json')).read_text())
        assert staged['uploadLinks'] == [url]  # Mapping retained even when hidden.
        out = input_dir/'combined.json'
        out.write_text(json.dumps([{'title':'fixture-Overview', 'text':
            '|!#|!Fight Link|!Duration|!Squad|!Enemy|!DownedEnemy|!killed|h\n'
            '|1|[[2026-09-19 - 14:00:45|'+url+']]|45s|10|20|8|7|'}]))
        out.with_suffix('.html').write_text(classic)
        return out
    monkeypatch.setattr(CombinerManager, 'run', combine)
    convert = raid_report.convert_report_file
    classic_inputs = []
    def capture_convert(path, *args, **kwargs):
        classic_inputs.append(path.read_text())
        return convert(path, *args, **kwargs)
    monkeypatch.setattr(raid_report, 'convert_report_file', capture_convert)
    selected = [LogInfo(raw, datetime(2026, 9, 19), 'filename')]
    if entry == 'gui':
        runner = raid_report_wiring.make_runner(cfg)
        report_path = runner.generate(selected).html_path
    else:
        monkeypatch.setattr(raid_report_wiring, 'discover_logs', lambda _: selected)
        monkeypatch.setattr(raid_report_wiring, 'recent_logs', lambda logs: logs)
        report_path = raid_report_wiring.run_headless_raid_report(cfg)
    html = report_path.read_text()
    assert unpack_classic_report(html) == classic_inputs[0]
    model = unpack_night_model(html)
    assert model['fights'][0]['report_url'] == url
    assert model['fights'][0]['kills'] == 7
    assert (raw.read_bytes(), cached.read_bytes()) == before
    assert fight_links.report_url(cached) == url
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.route('https://**/*', lambda route: route.abort())
        page.route('http://**/*', lambda route: route.abort())
        page.goto(report_path.as_uri())
        for width in (1440, 390):
            page.set_viewport_size({'width': width, 'height': 900})
            for view in ('simple', 'sparky'):
                page.locator(f'[data-view="{view}"]').click()
                frame = page.frame_locator('#report-frame')
                assert frame.locator('[data-fight-links]').count() == int(enabled)
                assert frame.locator(f'a[href="{url}"]').count() > 0 if enabled else frame.locator(f'a[href="{url}"]').count() == 0
                if view == 'sparky':
                    # Ordinary fight rows and statistical drill stay intact.
                    row = frame.locator('.fights-table tbody tr').first
                    assert row.count() == 1
                    assert row.locator('[data-label="Kills"]').inner_text() == '7'
                    assert bool(frame.locator('.fight-col-log').count()) == enabled
                    row.evaluate('(e)=>e.click()')
                    drill = frame.locator('#drilldown')
                    assert drill.is_visible()
                    assert drill.locator('.drill-kpis').count() == 1
                    assert drill.locator(f'a[href="{url}"]').count() == int(enabled)
                    frame.locator('#drill-close').click()
        browser.close()


def test_settings_modal_single_option_defaults_and_false_roundtrip(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from core.gui_settings import SettingsWindow
    from core.settings_dialog import SettingsDialog
    app = QApplication.instance() or QApplication([])
    cfg = Config(tmp_path/'settings.properties')
    load = SettingsWindow._load_settings
    monkeypatch.setattr(SettingsWindow, '_load_settings',
                        lambda self, **kw: load(self, prompt_updates=False))
    engine = SettingsWindow(cfg)
    dialog = SettingsDialog(engine)
    page = dialog._build_fight_reports_page()
    try:
        assert page.isAncestorOf(engine.dpsreport_enabled)
        assert engine.dpsreport_enabled.isChecked()
        from PySide6.QtWidgets import QRadioButton
        assert not engine.dpsreport_group_box.findChildren(QRadioButton)
        assert 'report links' in engine.dpsreport_enabled.text().lower()
        assert 'unchecked' in engine.dpsreport_enabled.toolTip().lower()
        dialog._take_snapshot()
        engine.dpsreport_enabled.setChecked(False)
        assert dialog.is_dirty() and dialog.apply_button.isEnabled()
        engine.enable_discord.setChecked(False)  # No configured destination in this fixture.
        saved, _ = engine._save_settings()
        assert saved, getattr(engine, '_last_save_error', '')
        loaded = Config(cfg.config_path)
        assert not loaded.dpsreport_links_enabled
        assert loaded.dpsreport_timing == dpsreport.TIMING_TOGETHER
        engine._load_settings(prompt_updates=False)
        assert not engine.dpsreport_enabled.isChecked()
        engine.dpsreport_enabled.setChecked(True)
        assert engine._save_settings()[0]
        assert dpsreport.links_active(Config(cfg.config_path))
    finally:
        dialog.deleteLater()
        engine.deleteLater()
        app.processEvents()


@pytest.mark.parametrize('timing', dpsreport.VALID_TIMINGS)
@pytest.mark.parametrize('enabled', [False, True])
def test_actual_watcher_upload_waits_only_when_enabled(timing, enabled):
    from threading import Event, Thread
    from unittest.mock import patch
    from test_sparky_wall_pipeline import ApplicationCallbackTests
    from core import fight_links
    case = ApplicationCallbackTests()
    case.setUp()
    release, entered, done = Event(), Event(), Event()
    thread = None
    try:
        case.enterContext(patch.object(fight_links, 'app_dir', return_value=case.home))
        case.config.enable_ai_analysis = False
        case.config.enable_twitch = False
        case.config.dpsreport_links_enabled = enabled
        case.config.dpsreport_timing = timing
        case.raw.write_bytes(b'offline synthetic callback fixture')
        order, failures, sent = [], [], []
        def http(*a, **kw):
            order.append('upload')
            entered.set()
            assert release.wait(5), 'test upload was not released'
            return Mock(status_code=200, json=lambda: {'permalink':'https://example.invalid/callback-fixture'})
        case.http.side_effect = http
        def send(**kw):
            sent.append(kw)
            order.append('link' if 'message' in kw else 'fight')
            if 'example.invalid' in json.dumps(kw):
                assert fight_links.report_url(case.path) == 'https://example.invalid/callback-fixture'
            return 1
        case.discord.send_to_all.side_effect = send
        def callback():
            try:
                case.callback(case.raw)
            except BaseException as error:
                failures.append(error)
            finally:
                done.set()
        thread = Thread(target=callback)
        thread.start()
        if enabled:
            assert entered.wait(2)
            assert not done.is_set()
            assert order == ['upload']
        else:
            # Upload response deliberately remains blocked. OFF must finish
            # without reaching it or any upload-specific timeout/wait.
            assert done.wait(2)
            assert not entered.is_set()
            case.http.assert_not_called()
            assert order == ['fight']
        release.set()
        assert done.wait(2)
        thread.join(2)
        assert not failures
        if enabled:
            assert order == ['upload', 'fight']
            assert case.http.call_count == 1
            link = '[Open fight report](https://example.invalid/callback-fixture)'
            fields = sent[0]['embeds'][0]['fields']
            assert [field['value'] for field in fields if field['name'] == 'dps.report'] == [link]
        else:
            assert 'Open fight report' not in json.dumps(sent)
            assert 'example.invalid' not in json.dumps(sent)
    finally:
        release.set()
        if thread is not None:
            thread.join(5)
        case.doCleanups()


@pytest.mark.parametrize('timing', dpsreport.VALID_TIMINGS)
def test_failed_discord_still_reports_failure_after_upload(timing):
    from unittest.mock import patch
    from test_sparky_wall_pipeline import ApplicationCallbackTests
    case = ApplicationCallbackTests()
    case.setUp()
    try:
        case.config.enable_ai_analysis = False
        case.config.enable_twitch = False
        case.config.dpsreport_links_enabled = True
        case.config.dpsreport_timing = timing
        case.discord.send_to_all.return_value = 0
        with patch.object(dpsreport, 'upload_log', return_value=None) as upload:
            case.callback(case.raw)
        assert upload.call_count == 1
        assert case.results[-1][1] == 'error_discord'
    finally:
        case.doCleanups()
