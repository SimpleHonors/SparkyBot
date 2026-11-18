"""Synthetic public consumable renderer regressions; Node executes actual JS."""
import ast
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def renderer_span():
    from core import report_viewer
    tree = ast.parse(Path(report_viewer.__file__).read_text(encoding='utf-8'))
    literal = max((n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)), key=len)
    start = literal.index('  function enemyConsumableFinding(')
    end = literal.index('  function enemyBuildEvidenceView(', start)
    return literal[start:end]


def execute(body):
    node = shutil.which('node')
    assert node, 'Installed Node is required for the actual renderer regression'
    script = ('var refreshCss=""; function fmt(x){return String(Math.round(x*100)/100);}'
              'function esc(x){return String(x).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/"/g,"&quot;");}'
              + renderer_span() + body)
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('count,eligible,measured', [
    (0, 10, True), (2, 10, True), (10, 10, True),
    (11, 10, False), (-1, 10, False), (1.5, 10, False),
    (None, 10, False), ('2', 10, False), (2, None, False),
    (2, 0, False), (2, -1, False), (2, 1.5, False),
    (2, '10', False), (0, 0, False), (2, float('inf'), False),
    (float('nan'), 10, False),
])
def test_consumable_coverage_validates_matching_count_pair(count, eligible, measured):
    body = ('var item={id:999,name:"SYNTHETIC <item>",actor_appearances:'
            + json.dumps(count) + '};console.log(JSON.stringify(enemyConsumableFinding(item,'
            + json.dumps(eligible) + ')));')
    html = execute(body)
    assert ('role="meter"' in html) is measured
    assert ('consumable-percent' in html) is measured
    assert '&lt;item>' in html
    if measured:
        assert 'width:' + str(count / eligible * 100).removesuffix('.0') + '%' in html
        assert str(count) + '/' + str(eligible) in html


def test_food_utility_and_other_items_remain_grouped_and_accessible():
    items = [{'id': i, 'name': 'SYNTHETIC Food ' + str(i),
              'classification': 'Nourishment', 'actor_appearances': i} for i in range(1, 6)]
    items += [{'id': 20, 'name': 'SYNTHETIC Utility', 'classification': 'Enhancement', 'actor_appearances': 2},
              {'id': 21, 'name': 'Malnourished', 'classification': 'Nourishment', 'actor_appearances': 1}]
    profile = {'consumables': items, 'meaningful_appearances': 10}
    html = execute('var p=' + json.dumps(profile) + ';var before=JSON.stringify(p);'
                   'var html=enemyConsumablesView(p);if(JSON.stringify(p)!==before)throw Error("input mutated");'
                   'console.log(JSON.stringify(html));')
    assert 'data-consumable-category="Food"' in html
    assert 'data-consumable-category="Utility"' in html
    assert '<details data-more-consumables>' in html
    assert '<details class="consumable-other" data-other-consumables>' in html
    assert html.count('data-consumable-id=') == len(items)
    assert html.index('data-consumable-id="5"') < html.index('data-consumable-id="4"')
