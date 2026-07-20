from core.reasoning_probe import (
    run_probe, format_report, make_real_factory, ProbeOutcome,
)


class FakeCall:
    def __init__(self, script, *, strategy_id, disable, budget):
        self.script = script
        self.strategy_id = strategy_id
        self.disable = disable
        self.budget = budget

    def run(self, test_summary, timeout):
        self.script.setdefault("_timeouts", []).append(timeout)
        ok = self.script.get(self.strategy_id, False)
        return ProbeOutcome(self.strategy_id, self.disable, self.budget,
                            ok=ok, empty=not ok, preview="hi" if ok else "")


def _factory(script):
    calls = []

    def factory(*, strategy_id, disable, budget):
        calls.append(strategy_id)
        return FakeCall(script, strategy_id=strategy_id, disable=disable, budget=budget)

    factory.calls = calls
    return factory


def test_healthy_model_is_one_call():
    f = _factory({"none": True})
    r = run_probe(f, {}, user_budget=450, base_url="https://api.openai.com/v1", model="gpt-4o")
    assert f.calls == ["none"]
    assert r.recommended_strategy_id == "none"


def test_offswitch_discovery_stops_at_first_hit():
    # baseline empty; think_enable is the winner for a minimax host
    f = _factory({"none": False, "headroom_only": True, "think_enable": True})
    r = run_probe(f, {}, user_budget=450,
                  base_url="https://api.minimaxi.chat/v1", model="minimax-m3")
    assert f.calls[0] == "none"
    assert f.calls[1] == "headroom_only"
    assert f.calls[2] == "think_enable"     # highest hint for this host, tried first
    assert r.recommended_strategy_id == "think_enable"


def test_format_report_is_plain_text():
    f = _factory({"none": True})
    r = run_probe(f, {}, user_budget=450, base_url="https://x/v1", model="m")
    text = format_report(r)
    assert "no reasoning fix" in text.lower()


def test_failed_probe_is_bounded_to_four_short_single_calls():
    script = {}
    f = _factory(script)
    run_probe(
        f, {}, user_budget=450, base_url="https://unknown.example/v1",
        model="mystery", timeout=30,
    )
    assert len(f.calls) <= 4
    assert script["_timeouts"]
    assert max(script["_timeouts"]) <= 10


def test_real_probe_call_disables_nested_runtime_retries(monkeypatch):
    seen = {}

    class StubAnalyst:
        last_completion_tokens = 12

        def __init__(self, **kwargs):
            seen["init"] = kwargs

        def analyze(self, summary, **kwargs):
            seen["analyze"] = kwargs
            return "working"

    monkeypatch.setattr("core.ai_analyst.FightAnalyst", StubAnalyst)
    call = make_real_factory(
        "https://api.example.test", "key", "model"
    )(strategy_id="none", disable=False, budget=450)
    outcome = call.run({}, timeout=10)

    assert outcome.ok
    assert seen["analyze"]["max_retries"] == 0
    assert seen["analyze"]["retry_delay"] == 0
