import re
from test_report_refresh import render_js, sample_model, board


def test_bubble_overview_retains_axis_near_axis_zero_and_missing():
    model = sample_model()
    support = next(b for b in model['stat_tables'] if b['source_key']=='Support-Summary')
    for name, metrics in [('Axis',dict(boonstrips=0,condicleanse=10,resurrects=1)),('Near',dict(boonstrips=.0001,condicleanse=10,resurrects=1)),('Zero',dict(boonstrips=0,condicleanse=0,resurrects=0)),('Missing',dict(boonstrips=3,resurrects=1))]:
        support['rows'].append(board('x',metrics,name)['rows'][0])
    html=render_js(model,'bubbleChart("support")')
    for name in ['Alice','Axis','Near','Missing']:
        assert html.count('data-overview-name="'+name+'"')==1
    assert 'data-overview-name="Zero"' not in html
    assert 'bubble-lane' not in html
    scatter = html.split('</svg>')[0]
    for name in ['Alice', 'Axis', 'Near']:
        assert 'data-overview-name="'+name+'"' in scatter
    assert 'data-overview-name="Missing"' not in scatter
    assert 'bubble-identity-table' in html
    import pytest
    near = re.search(r'data-overview-name="Near"[^>]*data-x="([^"]+)"',html)
    assert float(near[1]) == pytest.approx(.00006)
    assert 'data-y=""' in html
    assert 'data-x="0" data-y="0"' not in html
    for text in ['plotted','omitted','Independent linear','No jitter','Single-axis','half the radius','Focus or select','ranked comparison']:
        assert text not in html
    assert 'bubble-scatter' in html
    assert 'comparison-track' not in html


def test_missing_area_and_outlier_do_not_remove_observed_pairs():
    model = sample_model()
    support = next(b for b in model['stat_tables'] if b['source_key']=='Support-Summary')
    support['rows'] += [board('x', metrics, name)['rows'][0] for name, metrics in [
        ('No size', dict(boonstrips=1, condicleanse=1)),
        ('Threshold', dict(boonstrips=8, condicleanse=8, resurrects=0)),
        ('Outlier', dict(boonstrips=100, condicleanse=100, resurrects=4))]]
    for multiplier in [1, 100000]:
        support['rows'][-1]['metrics']['boonstrips'] = 100 * multiplier
        html = render_js(model, 'bubbleChart("support")')
        scatter = html.split('</svg>')[0]
        assert scatter.count('data-bubble data-point-index=') == 4
        assert 'data-overview-name="No size"' in scatter
        assert 'stroke-dasharray="2 2"' in scatter
        assert 'Incomplete data' not in html


def test_all_single_axis_coordinates_are_finite_and_professions_stay_separate():
    model = sample_model()
    damage = next(b for b in model['stat_tables'] if b['source_key']=='Damage')
    offense = next(b for b in model['stat_tables'] if b['source_key']=='Offensive-Summary')
    offense['rows'][0]['profession'] = 'Scourge'
    html = render_js(model, 'bubbleChart("dps")')
    assert 'bubble-scatter' not in html
    assert html.count('data-overview-name="Alice"') == 2
    support = next(b for b in model['stat_tables'] if b['source_key']=='Support-Summary')
    for axis in ['boonstrips', 'condicleanse']:
        support['rows'][0]['metrics'] = dict(boonstrips=0, condicleanse=0, resurrects=0)
        support['rows'][0]['metrics'][axis] = 10
        html = render_js(model, 'bubbleChart("support")')
        assert html.count('data-bubble data-point-index=') == 1
        assert 'NaN' not in html and 'Infinity' not in html


def test_chart_instructions_are_not_in_native_report():
    html=render_js(sample_model(),'renderSparky()+skillSharePie([{skill:"Example",damage:50}],"Skills","damage","damage")')
    for text in ['How to read','Select any labeled slice','half the radius','No jitter','Independent linear scales']:
        assert text not in html


def test_summary_is_kill_death_split_not_equal_tiles():
    model=sample_model();model['totals']={'enemy_kills':300,'ally_deaths':100,'fights':4,'enemy_downs':350,'ally_downs':120,'kdr':3}
    html=render_js(model,'totalsCards(false)')
    assert 'kill-comparison' in html
    assert 'width:75%' in html and 'width:25%' in html
    assert 'Our kills' in html and 'Our deaths' in html


def test_numeric_bars_keep_zero_missing_and_proportion():
    model=sample_model()
    assert '100%' in render_js(model,'numericBarStyle(100,100,"Firebrand")')
    assert '25%' in render_js(model,'numericBarStyle(25,100,"Firebrand")')
    assert render_js(model,'numericBarStyle(null,100,"Firebrand")')==''
    assert render_js(model,'numericBarStyle(0,100,"Firebrand")')==''
