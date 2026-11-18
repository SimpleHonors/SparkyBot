"""Poison hit compatibility through the public model seam (synthetic cases)."""
import pytest

from core.poison_tab import build_model


TAG = "2000-01-01-00:00:00"
HEADER = "|!Party |!Name | !Prof | !{{FightTime}} |!Poison |h"


def condition_row(cell, *, account="Synthetic.0001", name="Synthetic Applier",
                  prof="Scourge", fight_time="2,762.4"):
    return (f'|1 |<span data-tooltip="{account}">{name}</span> '
            f'|{{{{{prof}}}}} |{fight_time} |{cell} |')


def tiddlers_for(*rows, output=True):
    result = [{"title": TAG + "-Conditions-Out",
               "text": HEADER + "\n" + "\n".join(rows)}]
    if output:
        result.append({
            "title": TAG + "-Total-Condition-Output-Generation",
            "text": ("option = {dataset: {source: [[\"Player\", \"Poison\"], "
                     "[\"{{Scourge}} - Synthetic Applier\", 0.125]]}}"),
        })
    return result


def test_formatted_hits_use_tooltip_count_not_visible_generation():
    rows = build_model(tiddlers_for(condition_row(
        '<span data-tooltip="Hits Applied: 373">1,508.0</span>')))
    assert len(rows) == 1
    row = rows[0]
    assert (row.account, row.name, row.prof, row.apps, row.fight_time, row.output) == (
        "Synthetic.0001", "Synthetic Applier", "Scourge", 373, 2762.4, 0.125)
    assert row.apps_per_min == pytest.approx(373 / (2762.4 / 60))


@pytest.mark.parametrize(("cell", "expected"), [
    ("373", 373),
    ("1,234", 1234),
    ('<span data-tooltip="Hits Applied: 1,234">9,876.5</span>', 1234),
    ("<b class='value' data-tooltip='Hits Applied: 17'><i>99.9</i></b>", 17),
    ('<SPAN DATA-TOOLTIP="Hits Applied:&#32;9">123.4</SPAN>', 9),
    ('<span title="unrelated"><em data-tooltip="Hits Applied: 6">3.0</em></span>', 6),
    ('<span data-tooltip="Hits Applied: 8"/>999', 8),
    ("0", 0),
    ('<span data-tooltip="Hits Applied: 0">1,508.0</span>', 0),
    ("", 0),
    ("not a count", 0),
    ("373.0", 0),
    ('<span>373</span>', 0),
    ('<span data-tooltip="Other: 373">373</span>', 0),
    ('<span data-tooltip="Hits Applied: many">373</span>', 0),
    ('<span data-tooltip="Hits Applied: 373.5">373</span>', 0),
    ('<span data-tooltip="Hits Applied: -3">373</span>', 0),
    ('<span data-tooltip="Hits Applied: 1,23">373</span>', 0),
    ('<span data-tooltip="Hits Applied: 3 extra">373</span>', 0),
    ('<span data-tooltip>373</span>', 0),
])
def test_exact_hit_values_and_omission_policy(cell, expected):
    rows = build_model(tiddlers_for(condition_row(cell)))
    if not expected:
        assert rows == []
        return
    assert len(rows) == 1
    row = rows[0]
    assert (row.account, row.name, row.prof, row.apps, row.fight_time, row.output) == (
        "Synthetic.0001", "Synthetic Applier", "Scourge", expected, 2762.4, 0.125)


@pytest.mark.parametrize(("fight_time", "expected"), [
    ("0", 0.0), ("", 0.0), ("invalid", 0.0), ("1,200.5", 1200.5),
])
def test_fight_time_semantics_are_unchanged(fight_time, expected):
    row, = build_model(tiddlers_for(condition_row(
        '<span data-tooltip="Hits Applied: 12">99.5</span>', fight_time=fight_time)))
    assert row.fight_time == expected
    assert row.apps_per_min == (12 / (expected / 60) if expected else 0.0)


def test_missing_poison_cell_remains_omitted():
    row = '|1 |<span data-tooltip="Synthetic.0001">Synthetic Applier</span> |{{Scourge}} |120'
    assert build_model(tiddlers_for(row)) == []
    assert build_model([]) == []


def test_last_positive_account_row_and_generation_name_join_are_unchanged():
    rows = build_model(tiddlers_for(
        condition_row('12', prof='Necromancer'),
        condition_row('<span data-tooltip="Hits Applied: 5">99.0</span>',
                      name='Other Character', prof='Harbinger', fight_time='90'),
        condition_row('0', name='Ignored Zero', prof='Reaper'),
        condition_row('9', account='Synthetic.0002'),
    ))
    assert [(r.account, r.name, r.prof, r.apps, r.fight_time, r.output)
            for r in rows] == [
        ('Synthetic.0002', 'Synthetic Applier', 'Scourge', 9, 2762.4, 0.125),
        ('Synthetic.0001', 'Other Character', 'Harbinger', 5, 90.0, 0.0),
    ]


def test_observed_zero_generation_keeps_legacy_value():
    tiddlers = tiddlers_for(condition_row('12'))
    tiddlers[1]['text'] = tiddlers[1]['text'].replace('0.125', '0.0')
    row, = build_model(tiddlers)
    assert row.output == 0.0
    assert not row.has_output


def test_missing_generation_keeps_legacy_default_without_inferring_measurement():
    row, = build_model(tiddlers_for(condition_row('12'), output=False))
    # Compatibility only: absence is audited independently, not called observed zero.
    assert row.output == 0.0


def test_classic_augmentation_keeps_existing_tiddlers_and_menu_location():
    import copy
    from core.poison_tab import augment

    original = tiddlers_for(condition_row(
        '<span data-tooltip="Hits Applied: 373">1,508.0</span>'))
    original.append({'title': TAG + '-Menu',
                     'text': f'<<tabs "[[{TAG}-DPS-Stats]]" "{TAG}-DPS-Stats">>'})
    preimage = copy.deepcopy(original)
    augmented = augment(original)
    assert original == preimage
    by_title = {t['title']: t for t in augmented}
    for t in preimage:
        if not t['title'].endswith('-Menu'):
            assert by_title[t['title']] == t
    assert len(augmented) == len(original) + 2
    assert '|Synthetic Applier | Scourge | 373 | 8.1 | 0.125|' in by_title[TAG + '-SparkyBot']['text']
    assert "0.125, 373," in by_title[TAG + '-SparkyBot-Poison-Chart']['data']
    assert by_title[TAG + '-Menu']['text'] == (
        f'<<tabs "[[{TAG}-DPS-Stats]] [[{TAG}-SparkyBot]]" "{TAG}-DPS-Stats">>')


def test_normal_native_model_receives_exact_poison_rows(monkeypatch):
    import sys
    from types import ModuleType
    from core.night_model import build_night_model

    # Isolate the unrelated AI import boundary, not the poison/model producer.
    review = ModuleType('core.run_analysis')
    review.read_review = lambda source: None
    monkeypatch.setitem(sys.modules, 'core.run_analysis', review)
    model = build_night_model(tiddlers_for(condition_row(
        '<span data-tooltip="Hits Applied: 373">1,508.0</span>')))
    assert model['poison'] == [{
        'account': 'Synthetic.0001', 'name': 'Synthetic Applier', 'prof': 'Scourge',
        'apps': 373, 'fight_time': 2762.4, 'output': 0.125,
        'apps_per_min': pytest.approx(373 / (2762.4 / 60)),
        'output_available': True,
    }]
    assert not [w for w in model['warnings'] if w.startswith('poison:')]
