from test_report_refresh import render_js, sample_model


def test_rate_cards_use_full_width_ranked_rows_and_exact_values():
    model = sample_model()
    html = render_js(model, 'healingOverviewView()')
    assert 'highest first' not in html
    assert 'rate-ranking-card' in html
    assert 'rate-ranking-track' in html
    assert 'Healing / sec' in html
    assert 'Downed Healing / sec' in html
    assert 'data-drill-kind="session-player"' in html


def test_shared_rate_card_preserves_sort_precision_and_zero():
    board = {'stat': 'Healing', 'metric': {'rate_label': 'Healing / sec'}, 'rows': [
        {'name': 'Zero', 'rate': 0, 'total': 0},
        {'name': 'Second', 'rate': 2832.851, 'total': 100},
        {'name': 'First', 'rate': 2832.86, 'total': 101},
    ]}
    html = render_js({'stat_tables': [board]}, 'metricBoardBars(model.stat_tables[0],"heal")')
    assert html.index('First') < html.index('Second') < html.index('Zero')
    assert 'width:0%' in html
    assert '2,832.86' in html
    assert 'highest first' not in html
