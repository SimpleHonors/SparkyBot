import re
from test_report_refresh import render_js, sample_model


def test_ranking_has_simultaneous_total_and_rate_with_one_bar():
    expr='boardCard({stat:"damage",metric:{rate_label:"DPS"},rows:[{name:"A",total:100,rate:10,participation_time:10,fights:1},{name:"B",total:200,rate:5,participation_time:40,fights:2}]},100)'
    html=render_js(sample_model(),expr)
    assert 'data-ranking-mode' not in html
    assert 'data-sort-key="total"' in html and 'data-sort-key="rate"' in html
    assert re.search(r'data-value-kind="total"[^>]*>200</td>', html)
    assert re.search(r'data-value-kind="rate"[^>]*>5</td>', html)
    assert html.count('background-image:') == 2
    assert 'data-ranking-cell' in html
    assert 'Fight Time' in html and 'Fights' in html
    assert 'data-total="200"' in html and 'data-rate="5"' in html
    assert 'data-rank-cell' in html


def test_summary_fills_are_scoped_muted_colors():
    html=render_js(sample_model(),'totalsCards(false)')
    assert 'background:var(--summary-kills)' in html
    assert 'background:var(--summary-deaths)' in html


def test_profession_ranking_labels_actual_player_count():
    html=render_js(sample_model(),'healingProfilesView()')
    assert '<th>Profession</th>' in html
    assert '>Players</button>' in html
    assert '>Fights</button>' not in html


def test_native_report_has_no_classic_promotion():
    html=render_js(sample_model(),'renderSparky()+renderSimple()')
    assert 'Need the untouched upstream report?' not in html
    assert 'id="open-classic"' not in html


def test_ranking_mobile_preserves_collapsed_rows_and_responsive_details():
    html=render_js(sample_model(),'renderSparky()')
    assert '.ranking-board tr[hidden]{display:none!important}' in html
    assert 'responsive-condensed' in html and 'row-metric-details' in html
