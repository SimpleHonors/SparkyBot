"""Actual callback -> Discord manager -> serialized HTTP; no network requests."""
import copy
import json
from unittest.mock import Mock, patch

import pytest

from core import dpsreport, fight_links
from core.discord_bot import DiscordWebhookManager


@pytest.mark.parametrize('timing', dpsreport.VALID_TIMINGS)
@pytest.mark.parametrize('enabled', [True, False])
def test_each_fight_sends_its_own_clickable_permalink(timing, enabled):
    from test_sparky_wall_pipeline import ApplicationCallbackTests
    case = ApplicationCallbackTests()
    case.setUp()
    try:
        case.enterContext(patch.object(fight_links, 'app_dir', return_value=case.home))
        case.config.enable_ai_analysis = False
        case.config.enable_twitch = False
        case.config.dpsreport_links_enabled = enabled
        case.config.dpsreport_timing = timing
        case.config.guild_icon = ''
        case.config.discord_webhook = 'https://discord.com/api/webhooks/1/offline-fixture'
        case.config.active_discord_webhook = 1
        manager = DiscordWebhookManager(case.config)
        case.discord.send_to_all.side_effect = manager.send_to_all
        payloads, upload_files = [], []
        current = {}
        def http(url, **kw):
            if url == dpsreport.UPLOAD_URL:
                upload_files.append(kw['files']['file'][0])
                return Mock(status_code=200, json=lambda: {'permalink': current['url']})
            assert url == case.config.discord_webhook
            assert 'json' in kw
            payloads.append(copy.deepcopy(kw['json']))
            if enabled and current['url'] in json.dumps(kw['json']):
                assert fight_links.report_url(case.path) == current['url']
            return Mock(status_code=204)
        case.http.side_effect = http
        for index in (1, 2):
            raw = case.home / f'fight-{index}.zevtc'
            raw.write_bytes(f'offline raw fixture {index}'.encode())
            case.data['timeEnd'] = f'2026-09-19 20:0{index}:00 +00:00'
            # A cached/source URL must not leak when the option is disabled.
            case.data['uploadLinks'] = ['https://example.invalid/previously-saved']
            current['url'] = f'https://example.invalid/individual-fixture-{index}'
            start = len(payloads)
            case.callback(raw)
            assert case.results[-1][1] == 'success'
            emitted = payloads[start:]
            if enabled:
                assert len(emitted) == (2 if timing == dpsreport.TIMING_LINK_LATER else 1)
                markdown = f"[Open fight report]({current['url']})"
                assert json.dumps(emitted).count(markdown) == 1
                assert 'previously-saved' not in json.dumps(emitted)
                if timing == dpsreport.TIMING_LINK_LATER:
                    assert markdown in emitted[1]['content']
                    assert current['url'] not in json.dumps(emitted[0])
                else:
                    assert any(field['value'] == markdown
                               for field in emitted[0]['embeds'][0]['fields'])
            else:
                assert len(emitted) == 1
                assert 'example.invalid' not in json.dumps(emitted)
                assert 'Open fight report' not in json.dumps(emitted)
        assert upload_files == (['fight-1.zevtc', 'fight-2.zevtc'] if enabled else [])
    finally:
        case.doCleanups()
