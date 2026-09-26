import json
from core.enemy_role_evidence import collect_report_evidence
from core.night_model import build_night_model


def test_normal_producer_consumer(tmp_path):
    data={'phases':[{'start':1000,'end':61000}], 'durationMS':90000,
          'wvWMapData':{'redTeamID':7}, 'targets':[]}
    for spec,count,damage in [('Warrior',25,6000),('Amalgam',2,7560)]:
        for _ in range(count):
            i=len(data['targets'])+1
            data['targets'].append({'enemyPlayer':True,'instanceID':i,'name':f'{spec} pl-{i}',
                'teamID':7,'firstAware':1000,'lastAware':2000,'dpsAll':[{'damage':damage}],
                'statsAll':[{'downed':0}], 'totalDamageDist':[[{'id':3,'totalDamage':5000}]]})
    path=tmp_path/'fight.json';path.write_text(json.dumps(data))
    enemy,_=collect_report_evidence([path])
    assert 'performance' in enemy
    model=build_night_model([],enemy_role_evidence=enemy)
    performance=model['enemy_intel']['performance']
    rows=performance['all']
    assert [r['spec'] for r in rows]==['Amalgam','Warrior']
    assert [r['metrics']['damage']['rate'] for r in rows]==[126,100]
    assert rows[0]['metrics']['downs']['rate']==0
    assert rows[0]['metrics']['cc']['rate'] is None
    assert rows[0]['skills'][0]['damage']==10000
    assert performance['teams']['blue']==[]
    data['wvwMapData']={'blueTeamID':7};path.write_text(json.dumps(data))
    enemy,_=collect_report_evidence([path]);assert enemy['performance']['teams']['red']==[]
    assert len(enemy['performance']['teams']['unknown'])==2


def test_renderer_visible_ranking_and_exact_skill_scope():
    from core.enemy_performance import summarize
    from test_report_refresh import render_js
    rows=[dict(spec='Amalgam',fight_id='a',team='red',seconds=60,
               metrics={'damage':7560,'downs':0},skills=[{'id':1,'name':'<Skill>','damage':123}])]
    model={'enemy_intel':{'performance':summarize(rows)}}
    page=render_js(model,'comparisonView()')
    assert 'enemy-performance' in page
    assert 'Damage / player-sec' in page
    assert 'data-damage="126"' in page
    assert 'data-cc=""' in page
    assert 'data-drill-kind="enemy-performance"' in page
    assert page.index('Enemy performance by spec') < page.index('All fights composition')
    assert 'Amalgam' not in render_js(model,'enemyPerformanceView({color:"blue"})')
    drill=render_js(model,'enemyPerformanceDrill("all|Amalgam")')
    assert '&lt;Skill&gt;' in drill and 'Actor-only' in drill


def test_per_metric_pools_missing_zero_invalid_phase_and_spec():
    from core.enemy_performance import extract, aggregate
    rows=[dict(spec='Reaper',team='blue',fight_id='f',seconds=10,metrics={'damage':1000,'downs':0}),
          dict(spec='Reaper',team='blue',fight_id='g',seconds=90,metrics={'damage':900,'kills':0})]
    r=aggregate(rows)[0]
    assert r['metrics']['damage']['rate']==19
    assert r['metrics']['downs']==dict(total=0,observed=1,player_phase_seconds=10,rate=0)
    assert r['metrics']['cc']['total'] is None and r['metrics']['cc']['rate'] is None
    d={'targets':[{'enemyPlayer':True,'name':'Reaper pl-5','instanceID':5,'dpsAll':[{'damage':1000}]}]}
    r=aggregate(extract(d,'f'))[0]
    assert r['appearances']==1 and r['metrics']['damage']['rate'] is None
    d['phases']=[{'start':0,'end':10000}];d['targets'][0]['name']='Reaper pl-999'
    r=aggregate(extract(d,'f'))[0];assert r['spec']=='Unknown' and r['metrics']['damage']['rate'] is None
    d['targets'][0]['enemyPlayer']=False;assert extract(d,'f')==[]
