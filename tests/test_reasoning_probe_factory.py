import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

import core.reasoning_probe as rp


class _FakeAnalyst:
    def __init__(self, *a, **kw):
        self.kw = kw
        self.last_completion_tokens = 123

    def analyze(self, summary, timeout=30):
        return "" if self.kw.get("thinking") else "HOT TAKE: nice fight"


def test_real_factory_maps_empty_to_not_ok(monkeypatch):
    monkeypatch.setattr("core.ai_analyst.FightAnalyst", _FakeAnalyst)
    factory = rp.make_real_factory("https://x/v1", "k", "m")
    oc = factory(strategy_id="none", disable=False, budget=450).run({}, 5)
    assert oc.ok is False and oc.empty is True


def test_real_factory_maps_text_to_ok(monkeypatch):
    monkeypatch.setattr("core.ai_analyst.FightAnalyst", _FakeAnalyst)
    factory = rp.make_real_factory("https://x/v1", "k", "m")
    oc = factory(strategy_id="think_enable", disable=True, budget=450).run({}, 5)
    assert oc.ok is True
    assert "HOT TAKE" in oc.preview
    assert oc.completion_tokens == 123
