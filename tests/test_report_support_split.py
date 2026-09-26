import re
from test_report_refresh import render_js, sample_model, board


def test_two_independent_support_tables_replace_combined_grid():
    model = sample_model()
    source = next(b for b in model['stat_tables'] if b['source_key'] == 'Support-Summary')
    source['rows'] += [board('x', {'condicleanse': 0}, 'Zero')['rows'][0], board('x', {'boonstrips': 20}, 'Stripper')['rows'][0]]
    for expression in ['metricGrid("utility")', 'supportOverviewView()']:
        page = render_js(model, expression)
        assert 'Utility &amp; support' not in page and 'Utility & support' not in page
        tables = re.findall(r'<article class="board support-ranking".*?</article>', page)
        assert len(tables) == 2
        assert 'Condition cleanses' in tables[0] and 'Boon strips' in tables[1]
        for table in tables:
            assert all('data-sort-key="'+key+'"' in table for key in ['total','rate','participation'])
            assert table.count('data-support-player=') == 3
            assert 'data-total=""' in table
            assert 'Time' in table and 'Total' in table and '/min' in table
        assert 'data-total="0"' in tables[0]
        assert tables[1].index('Stripper') < tables[1].index('Alice')


def test_support_charts_use_time_and_rate_equal_circles_and_exact_population():
    model = sample_model()
    source = next(b for b in model['stat_tables'] if b['source_key'] == 'Support-Summary')
    source['rows'] += [board('x', {'condicleanse': n}, name)['rows'][0] for name,n in [('Zero',0),('Tiny',0.0001),('Missing',None)]]
    page = render_js(model, 'supportCharts()')
    assert page.count('data-bubble-chart=') == 2
    assert 'Participation time (min)' in page
    assert 'Cleanses / min' in page and 'Strips / min' in page
    assert 'Equal circles' in page and 'no size encoding' in page
    assert 'resurrect' not in page.lower()
    first = page.split('data-bubble-chart="boonstrips"')[0]
    assert first.count('data-bubble ') == 3  # observed zero remains plotted
    assert 'data-point-name="Missing"' not in first
    assert 'data-point-name="Tiny"' in first
    assert set(re.findall(r' r="([\d.]+)"', page)) == {'5'}
    assert 'data-x="1.6666666666666667"' in first
    assert 'data-y="0"' in first
    assert 'data-highlight-point=' in first
    assert 'Incomplete data' not in page  # missing remains in the primary table

