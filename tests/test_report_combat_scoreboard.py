"""Focused combat scoreboard semantics; source totals are never recomputed."""
import re
import pytest
from test_report_refresh import render_js


def scoreboard(totals):
    return render_js({'totals': totals}, 'totalsCards(false)')


def widths(html):
    return [float(x) for x in re.findall(r'<i style="width:([\d.]+)%;background:var\(--summary-', html)]


def test_real_scoreboard_hierarchy_and_exact_values():
    h = scoreboard(dict(enemy_kills=569, ally_deaths=360, kdr=1.58, enemy_downs=881, ally_downs=544, fights=37))
    assert widths(h) == pytest.approx([569/929*100, 360/929*100])
    assert 'aria-label="Combat scoreboard"' in h
    assert 'data-split-state="populated"' in h
    for cls, label, value in [('combat-kills','Our kills','569'),('combat-ratio','K/D','1.58'),('combat-deaths','Our deaths','360'),('combat-enemy-downs','Enemy downs','881'),('combat-our-downs','Our downs','544'),('combat-fights','Fights','37')]:
        assert re.search(r'class="[^"]*'+cls+r'"[^>]*><span>'+label+r'</span><strong>'+value+r'</strong>', h)
    assert h.count('data-drill-kind="summary-metric:') == 6
    assert 'title=' not in h.replace('data-drill-title=', '')


@pytest.mark.parametrize('kills,deaths,expected,state',[(0,0,[0,0],'zero'),(0,4,[0,100],'populated'),(4,0,[100,0],'populated'),(None,4,[0,0],'missing'),(4,None,[0,0],'missing'),(-1,4,[0,0],'missing'),('bad',4,[0,0],'missing')])
def test_zero_and_missing_safe(kills,deaths,expected,state):
    h=scoreboard(dict(enemy_kills=kills,ally_deaths=deaths))
    assert widths(h)==expected
    assert f'data-split-state="{state}"' in h
    assert '<span>K/D</span><strong>—</strong>' in h
    assert 'NaN' not in h and 'Infinity' not in h


def test_source_ratio_preserved_and_numeric_strings_safe():
    h=scoreboard(dict(enemy_kills='6',ally_deaths='3',kdr=1.97))
    assert '<span>K/D</span><strong>1.97</strong>' in h
    assert widths(h)==pytest.approx([200/3,100/3])


def test_all_missing_not_zero():
    h=scoreboard({})
    assert h.count('<strong>—</strong>')==6
    assert widths(h)==[0,0]
