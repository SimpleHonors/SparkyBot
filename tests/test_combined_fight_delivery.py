"""Legacy settings and real manual/watcher/CLI pipeline: combined, lossless HTTP."""
import copy
import json
from unittest.mock import Mock, patch

import pytest

from core import dpsreport, fight_links
from core.config import Config
from core.discord_bot import DiscordWebhookManager
from core.fight_report import FightReport
from test_discord_lossless_fights import assert_limits, fields


@pytest.mark.parametrize('timing', ['link_later', 'together', '', 'obsolete'])
@pytest.mark.parametrize('enabled', [True, False])
def test_legacy_settings_normalize_without_enabling_saved_off(tmp_path, timing, enabled):
    path = tmp_path / 'old.properties'
    original = f'[DpsReport]\ndpsReportLinks = {enabled}\ndpsReportTiming = {timing}\n'
    path.write_text(original)
    config = Config(path)
    assert config.dpsreport_links_enabled is enabled
    assert config.dpsreport_timing == 'together'
    assert dpsreport.links_active(config) is enabled
    assert path.read_text() == original  # Loading never writes the user's file.
    assert config.save()
    assert Config(path).dpsreport_links_enabled is enabled
    assert Config(path).dpsreport_timing == 'together'


@pytest.mark.parametrize('entry', ['watcher', 'manual', 'cli'])
@pytest.mark.parametrize('timing', ['link_later', 'together'])
@pytest.mark.parametrize('enabled,upload_ok', [(False, True), (True, True), (True, False)])
def test_dense_original_fight_contains_link_without_separate_link_message(entry, timing, enabled, upload_ok):
    from test_sparky_wall_pipeline import ApplicationCallbackTests
    case = ApplicationCallbackTests()
    case.setUp()
    try:
        case.enterContext(patch.object(fight_links, 'app_dir', return_value=case.home))
        case.enterContext(patch('core.discord_bot.time.sleep'))
        case.config.enable_ai_analysis = False
        case.config.enable_twitch = False
        # Load actual old saved values, not just an invented timing property.
        old = case.home / 'legacy.properties'
        old.write_text(f'[DpsReport]\ndpsReportLinks = {enabled}\ndpsReportTiming = {timing}\n')
        loaded = Config(old)
        case.config.dpsreport_links_enabled = loaded.dpsreport_links_enabled
        case.config.dpsreport_timing = loaded.dpsreport_timing
        case.config.guild_icon = ''
        case.config.discord_webhook = 'https://discord.com/api/webhooks/1/offline-fixture'
        case.config.active_discord_webhook = 1
        case.raw.write_bytes(b'synthetic dense fight fixture')
        manager = DiscordWebhookManager(case.config)
        case.discord.send_to_all.side_effect = manager.send_to_all
        body = '\n'.join(['>>> Enemy: 51'] + [f'Enemy row {i:02d} ' + 'x' * 45 for i in range(51)]
                         + ['>>> Red: 46'] + [f'Red row {i:02d} ' + 'y' * 45 for i in range(46)])
        case.enterContext(patch.object(FightReport, 'get_enemy_breakdown', return_value=body))
        # Fill other already-summarized sections beyond the per-message budget.
        summary = '\n'.join(f'Stat row {i:02d} ' + 'z' * 42 for i in range(12))
        for getter in ('get_damage', 'get_bursters', 'get_strips', 'get_cleanses',
                       'get_healers', 'get_defense', 'get_ccs', 'get_downs_kills'):
            case.enterContext(patch.object(FightReport, getter, return_value=summary))
        payloads, order = [], []
        link = 'https://example.invalid/synthetic-combined-fight'
        def http(url, **kw):
            if url == dpsreport.UPLOAD_URL:
                order.append('upload')
                return Mock(status_code=200 if upload_ok else 500,
                            json=lambda: {'permalink': link}, text='offline fixture')
            assert url == case.config.discord_webhook  # No real HTTP/provider.
            order.append('discord')
            payloads.append(copy.deepcopy(kw['json']))
            return Mock(status_code=204)
        case.http.side_effect = http
        if entry == 'watcher':
            case.callback(case.raw)
            assert case.results[-1][1] == 'success'
        elif entry == 'manual':
            worker = case.main.FileProcessorWorker([case.raw], case.config)
            results = []
            worker.file_finished.connect(lambda *args: results.append(args))
            worker.run()
            assert results[0][1] == 'success'
        else:
            # Same entry function invoked by the CLI watcher's on_new_file.
            result = case.main.process_log_file(case.raw, case.config, case.invoker, manager)
            assert result.value == 'success'
        assert len(payloads) > 1
        assert all(p.get('embeds') and not p['content'] for p in payloads)
        assert_limits(payloads)
        parts = [f['value'] for f in fields(payloads) if f['name'].startswith('Enemy Breakdown')]
        assert '\n'.join(p[4:-4] for p in parts) == body
        for title in ('Damage & Down Contribution', 'Burst Damage', 'Strips', 'Cleanses',
                      'Heals', 'Defense', 'Outgoing CCs & Interrupts', 'Outgoing Downs & Kills'):
            values = [f['value'] for f in fields(payloads) if f['name'].startswith(title)]
            assert '\n'.join(v[4:-4] for v in values) == summary
        assert 'condensed' not in json.dumps(payloads)
        assert 'unavailable' not in json.dumps(payloads)
        assert order.count('upload') == int(enabled)
        if enabled:
            assert order[0] == 'upload'
        if enabled and upload_ok:
            assert payloads[0]['embeds'][0]['fields'][0]['value'] == f'[Open fight report]({link})'
            assert json.dumps(payloads).count(link) == 1
        else:
            assert 'Open fight report' not in json.dumps(payloads)
        assert not case.rows()  # AI-off gate remains unchanged.
    finally:
        case.doCleanups()


@pytest.mark.parametrize('enabled', [True, False])
def test_full_settings_import_cannot_restore_separate_posts(tmp_path, enabled):
    from core.dev_settings_transfer import import_full_settings
    incoming = tmp_path / 'legacy-full.ini'
    incoming.write_text('[SparkyBotFullExport]\ncontainsSecrets=true\n'
                        f'[DpsReport]\ndpsReportLinks={enabled}\ndpsReportTiming=link_later\n')
    cfg = Config(tmp_path / 'settings.properties')
    assert import_full_settings(cfg, incoming) == 2
    assert cfg.dpsreport_links_enabled is enabled
    assert cfg.dpsreport_timing == 'together'
    assert dpsreport.links_active(cfg) is enabled
    reloaded = Config(cfg.config_path)
    assert reloaded.dpsreport_timing == 'together'
    assert reloaded.dpsreport_links_enabled is enabled
