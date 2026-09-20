"""Display rounding must not leak into data, chart coordinates or sort keys."""
import re
from test_report_refresh import sample_model, render_js


def test_display_formatter_precision_and_literal_text():
    values = render_js(sample_model(), '[1.23456789,0.1+0.2,0,null,1234567.891234,-1.23456789].map(fmt)')
    assert values == ['1.23','0.3','0','—','1,234,567.89','-1.23']
    assert render_js(sample_model(), 'fmt("version 1.23456789")') == 'version 1.23456789'


def test_accessible_percentage_and_boon_tooltip_round_only_display():
    model=sample_model()
    model['boon_generation']={'scope':'session','unit':'weighted_generation','rows':[{'name':'Test','profession':'Guardian','boons':{'Might':1.23456789,'Fury':0.1+0.2},'source_total':1.53456789}]}
    html=render_js(model, 'accessibleBar("Uptime",1.23456789,"%",1.23456789,"")+multiboonGenerationChart(false)')
    assert 'Uptime: 1.23 %' in html and 'Test · Might: 1.23' in html
    for text in re.findall(r'(?:title|aria-label|data-tooltip)="([^"]*)"',html):
        assert not re.search(r'\d+\.\d{3,}',text),text
    assert render_js(model,'(multiboonGenerationChart(false),model.boon_generation)')==model['boon_generation']
