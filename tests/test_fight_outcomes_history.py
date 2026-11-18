"""Historical win rate and this report's loss-group measurement are independent."""


def test_historical_rate_is_not_the_current_loss_measurement(tmp_path):
    import json
    from core.fight_outcomes import collect_fight_outcomes

    path = tmp_path / "loss.json"
    path.write_text(json.dumps({"durationMS": 60000, "targets": [], "players": [
        {"defenses": [{"deadCount": 1, "receivedCrowdControl": 4, "stunBreak": 1}]}]}))
    result = collect_fight_outcomes([path])
    history = result["past_fights"]["stuns_broken_pct"]
    assert history == {"low": 33, "low_won_pct": 28, "high": 61, "high_won_pct": 81}
    assert result["summary"]["losses"] == 1
    assert result["summary"]["losses_avg"]["stuns_broken_pct"] == 25
    assert result["summary"]["wins_avg"]["stuns_broken_pct"] is None
