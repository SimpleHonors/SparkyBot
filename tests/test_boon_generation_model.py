"""Weighted chart data is parsed as data, never executed as JavaScript."""
import pytest
from core.night_model import build_night_model

TAG = '2026-09-09-03:26:55'


def tiddler(source):
    return {'title': TAG + '-Total-Squad-Boon-Generation',
            'text': "option = {title:{text:'Weighted Total Squad Boon Generation'}, dataset:[{source: " + source + "}], series:[]};"}


def test_exported_comparable_boon_values_reach_model_without_reweighting():
    model = build_night_model([tiddler("[['Player','Might','Stability','Total','Profession'], ['{{Firebrand}} - Alice', 1.25, 2.5, 3.75, 'Firebrand']]")])
    generation = model.get('boon_generation')
    assert generation is not None, 'The viewer must receive real exported multiboon data'
    assert generation['scope'] == 'session'
    assert generation['unit'] == 'weighted_generation'
    assert generation['rows'] == [{'name': 'Alice', 'profession': 'Firebrand',
                                    'boons': {'Might': 1.25, 'Stability': 2.5},
                                    'source_total': 3.75}]
    assert 'not uptime' in generation['methodology']


@pytest.mark.parametrize('value', ['None', 'True', '-1', '1e999', "'unavailable'", "__import__('os').getcwd()"])
def test_unsafe_or_missing_values_never_execute_or_become_zero(value):
    source = "[['Player','Might','Total','Profession'], ['Alice', " + value + ", 0, 'Firebrand']]"
    model = build_night_model([tiddler(source)])
    assert model['boon_generation'] is None
    assert 'boon_generation: exported weighted chart unavailable' in model['warnings']


def test_explicit_zero_and_distinct_professions_survive_with_brackets_in_name():
    source = '''[["Player","Might","Total","Profession"],
                 ["{{Firebrand}} - Alice [One]",0,0,"Firebrand"],
                 ["{{Luminary}} - Alice [One]",2,2,"Luminary"]]'''
    rows = build_night_model([tiddler(source)])['boon_generation']['rows']
    assert len(rows) == 2
    assert rows[0]['name'] == 'Alice [One]'
    assert rows[0]['boons']['Might'] == 0
    assert rows[1]['profession'] == 'Luminary'


def test_does_not_mix_another_session_or_per_fight_dataset():
    main = tiddler("[['Player','Might','Total','Profession'], ['Alice', 1, 1, 'Firebrand']]")
    main['text'] += "\nvar fightData = {Fight_01: [['Player','Might','Total','Profession'], ['Alice',999,999,'Firebrand']]};"
    other = dict(main, title='2026-09-10-03:26:55-Total-Squad-Boon-Generation')
    summary = {'title': TAG + '-Log-Summary', 'text': ''}
    model = build_night_model([summary, main, other])
    assert model['boon_generation']['rows'][0]['boons'] == {'Might': 1}
