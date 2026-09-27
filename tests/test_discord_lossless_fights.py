"""Lossless individual-fight transport through real manager, HTTP mocked only."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.discord_bot import DiscordWebhookManager, _embed_char_count


def send(embeds, **kwargs):
    config = SimpleNamespace(active_discord_webhook=1,
                             discord_webhook='https://discord.com/api/webhooks/1/offline')
    payloads = []
    def http(url, **request):
        assert url == config.discord_webhook
        payloads.append(deepcopy(request['json']))
        return Mock(status_code=204)
    with patch('core.discord_bot.requests.post', side_effect=http), \
            patch('core.discord_bot.time.sleep'):
        result = DiscordWebhookManager(config).send_to_all(
            embeds=embeds, compact_single_message=True, **kwargs)
    assert result == 1
    return payloads


def fields(payloads):
    return [field for payload in payloads for embed in payload.get('embeds', [])
            for field in embed.get('fields', [])]


def assert_limits(payloads):
    for payload in payloads:
        assert len(payload.get('content', '')) <= 2000
        embeds = payload.get('embeds', [])
        assert len(embeds) <= 10
        assert sum(_embed_char_count(e) for e in embeds) <= 6000
        for embed in embeds:
            assert len(embed.get('title', '')) <= 256
            assert len(embed.get('description', '')) <= 4096
            assert len(embed.get('author', {}).get('name', '')) <= 256
            assert len(embed.get('footer', {}).get('text', '')) <= 2048
            assert len(embed.get('fields', [])) <= 25
            for field in embed.get('fields', []):
                assert len(field['name']) <= 256
                assert 0 < len(field['value']) <= 1024


def dense_embeds() -> list[dict]:
    # Synthetic already-summarized tables, not real player evidence.
    stats = [{'name': f'Stat {i}', 'value': '```\n' + '\n'.join(
        f'{i:02d}-{j:02d} Player contribution ' + 'x' * 43 for j in range(10)) + '\n```',
        'inline': False} for i in range(12)]
    stats.append({'name': 'Enemy Breakdown', 'value':
                  '```\n>>> Enemy: 51\n 5  Berserker  1.2M\n>>> Red: 46\n 9  Firebrand  900K\n```',
                  'inline': False})
    return [{'title': 'Full Report', 'description': 'Synthetic dense fight'}] + [
        {'fields': stats[i:i + 4]} for i in range(0, len(stats), 4)]


def test_dense_summarized_fight_survives_in_order_without_condensation():
    embeds = dense_embeds()
    original = deepcopy(embeds)
    payloads = send(embeds)
    assert fields(payloads) == [f for e in embeds for f in e.get('fields', [])]
    assert len(payloads) > 1
    assert 'condensed' not in str(payloads)
    assert 'linked report' not in str(payloads)
    assert embeds == original
    assert_limits(payloads)


def test_report_field_overflow_preserves_all_enemy_rows_and_fences():
    from core.fight_report import FightReport
    from test_sparky_wall import fight
    report = FightReport(fight())
    body = '\n'.join(['>>> Enemy: 51'] + [f'Enemy row {i:02d} ' + 'x' * 45 for i in range(51)]
                     + ['>>> Red: 46'] + [f'Red row {i:02d} ' + 'y' * 45 for i in range(46)])
    with patch.object(report, 'get_enemy_breakdown', return_value=body):
        embeds = report.get_discord_embeds({})
    payloads = send(embeds)
    parts = [f['value'] for f in fields(payloads) if f['name'].startswith('Enemy Breakdown')]
    assert all(p.startswith('```\n') and p.endswith('\n```') for p in parts)
    assert '\n'.join(p[4:-4] for p in parts) == body
    assert len(parts) > 1
    assert_limits(payloads)


def test_one_oversized_embed_is_split_at_field_and_message_limits():
    embeds = [{'title': 'Full Report', 'fields': [
        {'name': f'Field {i}', 'value': str(i) + 'x' * 900, 'inline': False}
        for i in range(29)]}]
    payloads = send(embeds)
    assert fields(payloads) == embeds[0]['fields']
    assert_limits(payloads)


def test_plain_field_text_and_clickable_link_survive_split_exactly():
    link = '[Open fight report](https://example.invalid/' + 'x' * 850 + ')'
    value = 'Summary row\n' * 70 + link + '\nFinal row'
    payloads = send([{'fields': [{'name': 'Notes', 'value': value}]}])
    assert ''.join(f['value'] for f in fields(payloads)) == value
    assert sum(link in f['value'] for f in fields(payloads)) == 1
    assert_limits(payloads)


def test_dense_report_keeps_long_link_in_first_message():
    embeds = dense_embeds()
    link = '[Open fight report](https://example.invalid/' + 'x' * 930 + ')'
    embeds[0]['fields'] = [{'name': 'dps.report', 'value': link}]
    payloads = send(embeds)
    assert fields(payloads)[0]['value'] == link
    assert payloads[0]['embeds'][0]['fields'][0]['value'] == link
    assert fields(payloads) == [f for e in embeds for f in e.get('fields', [])]
    assert_limits(payloads)


def test_short_report_is_unchanged_and_single_message():
    embeds = [{'title': 'Full Report', 'fields': [
        {'name': 'Enemy Breakdown', 'value': '```\n>>> Enemy: 51\n>>> Red: 46\n```'}]}]
    assert send(embeds, message='Fight') == [{'content': 'Fight', 'embeds': embeds}]


def test_field_count_and_embed_count_overflow():
    embeds = [{'fields': [{'name': str(i), 'value': 'x'} for i in range(30)]}]
    payloads = send(embeds)
    assert fields(payloads) == embeds[0]['fields']
    assert_limits(payloads)
    embeds = [{'description': str(i)} for i in range(12)]
    payloads = send(embeds)
    assert [e for p in payloads for e in p['embeds']] == embeds
    assert_limits(payloads)


def test_failed_continuation_stops_transport_and_does_not_send_audio():
    config = SimpleNamespace(active_discord_webhook=1,
                             discord_webhook='https://discord.com/api/webhooks/1/offline')
    with patch('core.discord_bot.requests.post', side_effect=[
            Mock(status_code=204), Mock(status_code=400, text='fixture failure')]) as http, \
            patch('core.discord_bot.time.sleep'):
        assert DiscordWebhookManager(config).send_to_all(
            embeds=dense_embeds(), audio_bytes=b'fixture audio') == 0
        assert http.call_count == 2
        assert all('json' in call.kwargs for call in http.call_args_list)


def test_link_larger_than_field_uses_description_without_breaking_target():
    link = '[Open fight report](https://example.invalid/' + 'x' * 1400 + ')'
    payloads = send([{'title': 'Full Report', 'color': 43115, 'fields': [
        {'name': 'dps.report', 'value': link}]}])
    assert len(payloads) == 1
    assert any(e.get('description') == link for e in payloads[0]['embeds'])
    assert all(e.get('title') or e.get('description') or e.get('fields')
               for e in payloads[0]['embeds'])
    assert_limits(payloads)


def test_long_field_name_is_preserved_on_continuations_within_limit():
    name = 'N' * 256
    payloads = send([{'fields': [{'name': name, 'value': 'A' * 2500}]}])
    assert ''.join(f['value'] for f in fields(payloads)) == 'A' * 2500
    assert all(f['name'] == name for f in fields(payloads))
    assert_limits(payloads)


def test_fenced_boundary_retains_trailing_blank_row_and_language():
    body = 'x' * 1012 + '\n'
    payloads = send([{'fields': [{'name': 'Table', 'value': '```text\n' + body + '\n```'}]}])
    values = [f['value'] for f in fields(payloads)]
    assert all(v.startswith('```text\n') and v.endswith('\n```') for v in values)
    assert '\n'.join(v[8:-4] for v in values) == body
    assert_limits(payloads)
