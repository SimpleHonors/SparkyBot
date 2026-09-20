from test_report_refresh import render_js, sample_model

def test_grid_pairs_remain_in_one_header_row_without_modes():
    html=render_js(sample_model(),'metricGrid("damage")')
    assert 'data-metric-mode' not in html
    assert 'data-sort-key="m0-total"' in html
    assert 'data-sort-key="m0-rate"' in html
    assert 'data-value-kind="total"' in html
    assert 'data-value-kind="rate"' in html
    assert html.split('</thead>')[0].count('<tr>') == 1
