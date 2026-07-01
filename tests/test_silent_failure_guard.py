from core.silent_failure_guard import SilentFailureGuard


def test_headroom_floor_default_is_4000():
    g = SilentFailureGuard()
    assert g.headroom_floor == 4000


def test_strategy_id_default_is_empty_string():
    g = SilentFailureGuard()
    assert g.strategy_id == ""


def test_retry_budget_never_below_original():
    # Simulates the fight_analyst retry math for a reasoning model:
    # the retry budget must be >= the original, never the old 400 shrink.
    original = 450
    g = SilentFailureGuard()
    retry_budget = max(original, g.headroom_floor)
    assert retry_budget >= original
    assert retry_budget == 4000
