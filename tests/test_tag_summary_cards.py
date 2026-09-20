"""Synthetic modern combiner cards; exercise the actual model boundary."""
from html import escape

import pytest

from core.night_model import _parse_session

from core.night_model import build_night_model


LABELS = ('Fights', 'Enemy Downed', 'Enemy Killed', 'Squad Downed', 'Squad Deaths')


def card(name, account, counts, profession='Chronomancer'):
    metrics = ''.join(
        f'<div class="tag-summary-metric"><div class="tag-summary-label">{label}</div>'
        f'<div class="tag-summary-value"><strong>{value:,}</strong></div></div>'
        for label, value in zip(LABELS, counts)
    )
    kills, deaths = counts[2], counts[4]
    metrics += ('<div class="tag-summary-metric"><div class="tag-summary-label">KDR</div>'
                f'<div class="tag-summary-value">{kills / deaths if deaths else kills:.2f}</div></div>')
    return (f'<div class="tag-summary-card"><div class="tag-summary-header">'
            f'<div class="tag-summary-name"><span>{{{{{profession}}}}}</span>'
            f'<span>{escape(name)}</span></div><div class="tag-summary-account" '
            f'title="{escape(account)}">{escape(account)}</div></div>'
            f'<div class="tag-summary-metrics">{metrics}</div></div>')


def model(*cards, text=None):
    if text is None:
        text = ('<div class="composition-section"><h2>Summary by Command Tag</h2>'
                '<div class="tag-summary-list">' + ''.join(cards) + '</div></div>')
    return build_night_model([{'title': '2026-09-19-21:01:41-Tag_Stats',
                              'caption': 'Tag Summary', 'text': text}])


def test_modern_cards_aggregate_all_commanders_and_no_tag_once():
    result = model(card('First', 'First.1234', (10, 100, 40, 80, 20)),
                   card('Second & Co', 'Second.1234', (19, 357, 181, 471, 406)),
                   card('No Tag', 'No Tag', (1, 3, 2, 0, 0), profession=''))
    assert result['totals'] == {'fights': 30, 'enemy_downs': 460, 'enemy_kills': 223,
                                'ally_downs': 551, 'ally_deaths': 426,
                                'kdr': 223 / 426}
    assert result['session']['commander'] == 'Second & Co'
    assert result['session']['commander_account'] == 'Second.1234'
    assert result['session']['report_title'] == 'Second & Co’s night'


def test_unnamed_no_tag_card_is_counted_but_never_selected():
    result = model(card('Named', 'Named.1234', (1, 3, 1, 0, 0)),
                   card('', 'No Tag', (20, 40, 20, 0, 0), profession=''))
    assert result['totals']['fights'] == 21
    assert result['totals']['enemy_kills'] == 21
    assert result['totals']['kdr'] == 21.0
    assert result['session']['commander'] == 'Named'


LEGACY = '''|Name | Prof | Fights | {{DownedEnemy}} | {{killed}} | {{DownedAlly}} | {{DeadAlly}} | KDR |h
|<span data-tooltip="Legacy.1234">Legacy</span>|{{Firebrand}}|2|10|3|8|7|0.43|
|Totals |<|2|10|3|8|7|0.43|f'''


def test_legacy_totals_remain_authoritative_not_added_to_cards():
    result = model(text=LEGACY + '\n<div class="tag-summary-list">' +
                   card('Other', 'Other.1234', (9, 90, 30, 20, 70)) + '</div>')
    assert result['totals'] == dict(fights=2, enemy_downs=10, enemy_kills=3,
                                    ally_downs=8, ally_deaths=7, kdr=0.43)
    assert result['session']['commander'] == 'Legacy'


@pytest.mark.parametrize('bad', ['', '—', '-1', '1.5', 'NaN', '1%'])
def test_missing_or_invalid_counts_never_become_zero(bad):
    source = card('Named', 'Named.1234', (2, 10, 3, 8, 7))
    source = source.replace('<strong>7</strong>', bad)
    result = model(source)
    assert result['totals'] is None
    assert result['session']['commander'] is None
    assert any(w.startswith('totals:') for w in result['warnings'])


@pytest.mark.parametrize('change', [
    lambda s: s.replace('Squad Deaths', 'Unrecognized'),
    lambda s: s + s,
    lambda s: s[:-6],
    lambda s: s.replace('</strong>', '</span>'),
    lambda s: s + '<div class="future-total">unknown</div>',
    lambda s: s.replace('tag-summary-card', 'future-card'),
    lambda s: s.replace('tag-summary-metrics', 'tag-summary-card'),
    lambda s: s.replace('<div class="tag-summary-label">Fights</div>',
                        '<div class="tag-summary-label">Fights</div>' * 2),
])
def test_incomplete_duplicate_or_unknown_card_structure_is_unavailable(change):
    result = model(change(card('Named', 'Named.1234', (2, 10, 3, 8, 7))))
    assert result['totals'] is None


def test_commander_tie_uses_existing_combat_time_then_source_order():
    text = '<div class="tag-summary-list">' + ''.join([
        card('First', 'First.1234', (2, 10, 3, 8, 7)),
        card('Second', 'Second.1234', (2, 10, 3, 8, 7)),
    ]) + '</div>'
    tiddlers = [{'title': '2026-09-19-21:01:41-Tag_Stats', 'text': text}]
    assert _parse_session(tiddlers)['commander'] == 'First'
    stats = [{'rows': [{'name': 'Second', 'account': 'Second.1234',
                        'participation_time': 120}]}]
    assert _parse_session(tiddlers, stats)['commander'] == 'Second'


def test_duplicate_no_tag_aliases_cannot_be_counted_twice():
    result = model(card('', 'No Tag', (1, 2, 3, 0, 0), profession=''),
                   card('No Tag', 'No Tag', (1, 2, 3, 0, 0), profession=''))
    assert result['totals'] is None


def test_all_zero_untagged_session_preserves_observed_zeros():
    result = model(card('', 'No Tag', (1, 0, 0, 0, 0), profession=''))
    assert result['totals'] == dict(fights=1, enemy_downs=0, enemy_kills=0,
                                    ally_downs=0, ally_deaths=0, kdr=0.0)
    assert result['session']['commander'] is None


def test_legacy_missing_count_is_unavailable_not_fabricated_zero():
    assert model(text=LEGACY.replace('|8|7|0.43|f', '|8||0.43|f'))['totals'] is None
