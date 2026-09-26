"""Observed enemy output. Phase-0 numerators pooled over matching phase time.

All-target output, not enemy-to-squad attribution. Appearances are actor-fights,
not unique people; phase exposure is not active/alive time. No network or writes.
"""
import math
import re
from collections import defaultdict

METRICS = {'damage': ('dpsAll', 'damage', 1),
           'downs': ('statsAll', 'downed', 60),
           'cc': ('statsAll', 'appliedCrowdControl', 60),
           'interrupts': ('statsAll', 'interrupts', 60),
           'kills': ('statsAll', 'killed', 60)}


def number(x):
    return type(x) in (int, float) and math.isfinite(x) and x >= 0


def extract(data, fight_id):
    from core.enemy_role_evidence import _enemy_team
    phases = data.get('phases') or []
    phase = phases[0] if phases and isinstance(phases[0], dict) else {}
    start, end = phase.get('start'), phase.get('end')
    seconds = (end-start)/1000 if number(start) and number(end) and end > start else None
    rows = []
    for target in data.get('targets') or []:
        if not isinstance(target, dict) or target.get('enemyPlayer') is not True:
            continue
        match = re.fullmatch(r'([A-Za-z][A-Za-z ]*) pl-(\d+)', str(target.get('name', '')))
        spec = match[1] if match and str(target.get('instanceID')) == match[2] else 'Unknown'
        metrics = {}
        for key, (array, field, _) in METRICS.items():
            values = target.get(array) or []
            value = values[0].get(field) if values and isinstance(values[0], dict) else None
            metrics[key] = value if number(value) else None
        skills = []
        dist = target.get('totalDamageDist') or []
        for skill in dist[0] if dist and isinstance(dist[0], list) else []:
            if not isinstance(skill, dict) or not number(skill.get('totalDamage')):
                continue
            sid = skill.get('id')
            source = (data.get('skillMap') or {}).get(f's{sid}') or (data.get('buffMap') or {}).get(f'b{sid}') or {}
            skills.append({'id': sid, 'name': source.get('name') or f'Skill {sid}',
                           'damage': skill['totalDamage']})
        rows.append(dict(spec=spec, team=_enemy_team(target, data)[1], fight_id=fight_id,
                         seconds=seconds, metrics=metrics, skills=skills))
    return rows


def aggregate(rows, team='all'):
    groups = defaultdict(list)
    for row in rows:
        if team == 'all' or row['team'] == team:
            groups[row['spec']].append(row)
    result = []
    for spec, actors in groups.items():
        metrics = {}
        for metric, (_, _, scale) in METRICS.items():
            observed = [a for a in actors if spec != 'Unknown' and number(a['metrics'].get(metric))
                        and number(a['seconds']) and a['seconds'] > 0]
            seconds = math.fsum(a['seconds'] for a in observed)
            total = sum(a['metrics'][metric] for a in observed) if observed else None
            metrics[metric] = dict(total=total, player_phase_seconds=seconds,
                                   observed=len(observed), rate=total*scale/seconds if seconds else None)
        skills = {}
        for actor in actors:
            for skill in actor.get('skills') or []:
                key = (skill['id'], skill['name'])
                skills[key] = skills.get(key, 0) + skill['damage']
        result.append(dict(spec=spec, appearances=len(actors),
                           fight_count=len({a['fight_id'] for a in actors}), metrics=metrics,
                           skills=[dict(id=k[0], name=k[1], damage=v) for k,v in
                                   sorted(skills.items(), key=lambda item: -item[1])[:15]]))
    return sorted(result, key=lambda r: (r['metrics']['damage']['rate'] is None,
                  -(r['metrics']['damage']['rate'] or 0), r['spec']))


def summarize(rows):
    return {'scope': 'observed_all_targets', 'denominator': 'matching_phase_0_player_seconds',
            'appearances': len(rows), 'fight_count': len({r['fight_id'] for r in rows}),
            'all': aggregate(rows),
            'teams': {team: aggregate(rows, team) for team in ('red','blue','green','unknown')}}
