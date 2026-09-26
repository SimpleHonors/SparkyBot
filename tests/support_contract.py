"""Shared acceptance checks for the approved two-table/two-chart support UI."""
import math
import re

METRICS = ('condicleanse', 'boonstrips')


def audit_support(frame, model, *, keyboard=False):
    source = next(b for b in model['stat_tables'] if b['source_key'] == 'Support-Summary')['rows']
    rankings = frame.locator('[data-support-rankings]:visible')
    assert rankings.count() == 1
    assert rankings.locator('[data-support-metric]').count() == 2
    for metric in METRICS:
        card = rankings.locator('[data-support-metric="'+metric+'"]')
        table = card.locator('table')
        assert table.locator('thead tr').count() == 1
        assert table.locator('thead th').all_text_contents() == ['Player', 'Total', '/min', 'Time']
        expected = {(r.get('account', '').removeprefix(':'), r['name'], r['profession']): r for r in source}
        rows = table.locator('tbody tr').evaluate_all('''rs=>rs.map(r=>({id:JSON.parse(r.dataset.supportPlayer),
            total:r.dataset.total,rate:r.dataset.rate,time:r.dataset.participation,
            titles:[...r.querySelectorAll('td')].map(c=>c.title)}))''')
        assert len(rows) == len(expected) > 0
        assert {tuple(r['id']) for r in rows} == set(expected)
        for row in rows:
            original = expected[tuple(row['id'])]
            total = original['metrics'].get(metric)
            time = original.get('participation_time')
            rate = total / time * 60 if total is not None and time and time > 0 else None
            for key, value in [('total', total), ('rate', rate), ('time', time)]:
                assert row[key] == '' if value is None else math.isclose(float(row[key]), value, rel_tol=1e-12)
            assert all(not re.search(r'\d+\.\d{3,}', title) for title in row['titles'])
        expand = card.locator('[data-expand-board]')
        if expand.count():
            expand.focus()
            expand.press('Enter')
            assert table.locator('tbody tr:visible').count() == len(expected)
        for key in ('total', 'rate', 'participation'):
            header = table.locator('[data-sort-key="'+key+'"]')
            directions = set()
            for _ in range(2):
                if keyboard:
                    header.focus()
                    header.press('Enter')
                else:
                    header.evaluate('e=>e.click()')
                direction = header.get_attribute('data-sort-direction')
                directions.add(direction)
                assert header.locator('..').get_attribute('aria-sort') == direction
                values = table.locator('tbody tr').evaluate_all('(rs,k)=>rs.map(r=>r.getAttribute("data-"+k))', key)
                numbers = [float(v) for v in values if v != '']
                assert numbers == sorted(numbers, reverse=direction == 'descending')
                assert values[len(numbers):] == [''] * (len(values)-len(numbers))
            assert directions == {'ascending', 'descending'}
        rate_header = table.locator('[data-sort-key="rate"]')
        rate_header.evaluate('e=>e.click()')
        if rate_header.get_attribute('data-sort-direction') != 'descending':
            rate_header.evaluate('e=>e.click()')
        if expand.count():
            expand.focus()
            expand.press('Space')
            assert table.locator('tbody tr:visible').count() == min(5, len(expected))
        geometry = table.evaluate('''t=>({overflow:t.scrollWidth>t.parentElement.clientWidth+1,
            aligned:[...t.querySelector('tbody tr').children].every((c,i)=>Math.abs(c.getBoundingClientRect().right-t.tHead.rows[0].cells[i].getBoundingClientRect().right)<1),
            visible:[...t.querySelector('tbody tr').children].every(c=>c.getBoundingClientRect().width>0)})''')
        assert not geometry['overflow'] and geometry['aligned'] and geometry['visible'], geometry
        chart = frame.locator('[data-bubble-chart="'+metric+'"]:visible')
        assert chart.count() == 1
        assert 'Equal circles · no size encoding · Color: profession' in chart.inner_text()
        assert 'Participation time (min)' in chart.inner_text()
        points = chart.locator('circle[data-bubble]').evaluate_all('''es=>es.map(e=>({id:JSON.parse(e.parentElement.dataset.identity),
            x:+e.parentElement.dataset.x,y:+e.parentElement.dataset.y,cx:+e.getAttribute('cx'),cy:+e.getAttribute('cy'),r:+e.getAttribute('r'),fill:getComputedStyle(e).fill}))''')
        eligible = {k:r for k,r in expected.items() if r['metrics'].get(metric) is not None and (r.get('participation_time') or 0)>0}
        assert len(points) == len(eligible) > 0
        assert {tuple(p['id']) for p in points} == set(eligible)
        max_x = max(r['participation_time']/60 for r in eligible.values())
        max_y = max([1]+[r['metrics'][metric]/r['participation_time']*60 for r in eligible.values()])
        for point in points:
            row = eligible[tuple(point['id'])]
            assert math.isclose(point['x'], row['participation_time']/60)
            assert math.isclose(point['y'], row['metrics'][metric]/row['participation_time']*60)
            assert math.isclose(point['cx'], 75+point['x']/max_x*760)
            assert math.isclose(point['cy'], 410-point['y']/max_y*360, abs_tol=1e-9)
            assert point['r'] == 5
        summary = chart.locator('.support-point-key summary')
        summary.focus()
        summary.press('Enter')
        keys = chart.locator('[data-highlight-point]')
        assert keys.count() == len(points)
        for index in sorted({0, len(points)-1}):
            key = keys.nth(index)
            key.focus()
            highlighted = chart.locator('circle.point-highlight')
            assert highlighted.count() == 1
            assert highlighted.get_attribute('data-point-index') == str(index)
            popup = frame.locator('#chart-popup')
            assert popup.is_visible()
            assert popup.inner_text() == key.get_attribute('data-tooltip')
            assert highlighted.evaluate('e=>getComputedStyle(e).getPropertyValue("--point-color").trim()') == key.evaluate('e=>getComputedStyle(e).getPropertyValue("--point-color").trim()')
            assert highlighted.evaluate('e=>getComputedStyle(e).opacity') == '1'
        summary.focus()
        summary.press('Enter')
        assert chart.evaluate('e=>e.scrollWidth<=e.clientWidth+1')
