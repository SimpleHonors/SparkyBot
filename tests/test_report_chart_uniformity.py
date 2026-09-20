from test_report_refresh import render_js, sample_model


def test_damage_composition_uses_same_ranked_card_structure():
    html=render_js(sample_model(),'damageCompositionView()')
    assert '<article class="board damage-composition-card">' in html
    assert '<h3>Total DPS · Power + Condition</h3>' in html
    assert 'Sorted by total DPS' not in html
    assert 'class="damage-rank"' in html
    assert 'class="power"' in html and 'class="condition"' in html
    assert '100 DPS' in html
