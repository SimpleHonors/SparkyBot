from test_report_refresh import render_js, sample_model


def test_estimated_subgroups_are_visible_without_repeating_estimate():
    html = render_js({}, 'partyGrid({estimated_subgroups:[{members:[{profession:"Firebrand",observed:true}]}]})')
    assert '<details class="estimated-layout" open>' in html
    assert 'Estimated subgroup layout' in html
    assert 'Subgroup placement estimated' not in html
    assert 'Observed profession' in html
    assert 'Inferred placement' in html


def test_barrier_summary_is_visible_by_default():
    model = sample_model()
    board = dict(model['stat_tables'][0])
    board.update(source_key='Barrier', stat='Barrier')
    model['stat_tables'].append(board)
    html = render_js(model, 'renderSimple()')
    assert '<details class="secondary-support" open>' in html
