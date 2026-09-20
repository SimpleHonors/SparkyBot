"""Optional missing metrics disappear; real zeros and timestamp identity survive."""
from test_report_refresh import render_js, sample_model


def test_missing_boards_and_pressure_are_omitted_not_zeroed():
    m=sample_model()
    for rows in [[],[{'name':'Missing'}],[{'name':'Missing','damage':None}]]:
        assert render_js(m,'pressureBars('+__import__('json').dumps(rows)+',"Unused","enemy")')==''
    assert '>0</b>' in render_js(m,'pressureBars([{name:"Observed",damage:0}],"Observed","enemy")')
    for rows in [[],[{'name':'Missing','rate':None,'total':None}]]:
        assert render_js(m,'boardCard('+__import__('json').dumps({'stat':'Test','rows':rows})+')')==''
    assert 'Observed' in render_js(m,'boardCard({stat:"Test",rows:[{name:"Observed",total:0,rate:0}]})')


def test_local_clock_date_rollover_dst_and_explicit_offsets():
    m=sample_model()
    assert render_js(m,'fightClock("2026-09-19 - 02:17:00 - BAB")')=='9:17 PM'
    assert 'Sep 18' in render_js(m,'reportTimestamp("2026-09-19T02:17:00Z",true)')
    assert render_js(m,'fightClock("2026-01-19T02:17:00Z")')=='8:17 PM'
    assert render_js(m,'fightClock("2026-09-18 21:17:00 -05")')=='9:17 PM'
    assert render_js(m,'fightClock("2026-03-08T07:59:00Z")')=='1:59 AM'
    assert render_js(m,'fightClock("2026-03-08T08:01:00Z")')=='3:01 AM'
    assert render_js(m,'reportInstant("2026-09-18T23:59:00Z").getTime()<reportInstant("2026-09-19T00:01:00Z").getTime()')
    m['fights']=[{'index':1,'time_label':'2026-09-19T00:01:00Z'},{'index':2,'time_label':'2026-09-18T23:59:00Z'}]
    instant=render_js(m,'reportInstant(model.fights[0].time_label).getTime()')
    assert f'data-time="{instant}"' in render_js(m,'fightsTable(true)')
    assert render_js(m,'(fightsTable(true),model.fights)')==m['fights']
