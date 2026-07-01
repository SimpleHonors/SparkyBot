from core.reasoning_probe import run_probe, format_report, ProbeOutcome


class FakeCall:
    def __init__(self, script, *, strategy_id, disable, budget):
        self.script = script
        self.strategy_id = strategy_id
        self.disable = disable
        self.budget = budget

    def run(self, test_summary, timeout):
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
