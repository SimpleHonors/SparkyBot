import re
from test_report_refresh import render_js, sample_model, board


def test_support_overview_uses_independent_expandable_rate_rankings():
    model = sample_model()
    support = next(b for b in model['stat_tables'] if b['source_key'] == 'Support-Summary')
    support['rows'] = [board('x', {'condicleanse': n, 'boonstrips': 20-n}, name)['rows'][0]
                       for name, n in [('Close low', 10.001), ('Close high', 10.002), ('Zero', 0), ('Four', 4), ('Five', 5), ('Six', 6), ('Seven', 7)]]
    page = render_js(model, 'supportOverviewView()')
    assert 'data-support-rankings' in page
    assert 'data-bubble-chart="support"' not in page
    charts = re.findall(r'<article class="board support-ranking".*?</article>', page)
    assert len(charts) == 2
    assert 'Cleansing' in charts[0] and 'Boon removal' in charts[1]
    assert charts[0].index('Close high') < charts[0].index('Close low')
    assert charts[1].index('Zero') < charts[1].index('Four')
    for chart in charts:
        assert chart.count('data-support-player=') == 7
        assert chart.count(' hidden') == 2
        assert 'data-expand-board' in chart
        assert 'width:100%' in chart
        assert 'Total' in chart
        assert 'table-player' in chart
    assert 'Cleanses / min' in charts[0] and 'Strips / min' in charts[1]
    assert 'bubble-scatter' in render_js(sample_model(), 'bubbleChart("dps")')
