import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core.ai_analyst import FightAnalyst


def _analyst(strategy, base_url="https://api.minimaxi.chat/v1", thinking=True):
    return FightAnalyst(base_url=base_url, api_key="k", model="minimax-m3",
                        max_tokens=450, thinking=thinking, reasoning_strategy=strategy)


def test_configured_offswitch_applied_when_thinking_disabled():
    a = _analyst("openrouter_reasoning", base_url="https://openrouter.ai/api/v1", thinking=False)
    p = {"max_tokens": 450}
    handled = a._apply_configured_strategy(p)
    assert handled is True
    assert p["reasoning"] == {"enabled": False}


def test_configured_strategy_with_thinking_on_gets_headroom():
    a = _analyst("think_enable", thinking=True)
    p = {"max_tokens": 450}
    handled = a._apply_configured_strategy(p)
    assert handled is True
    assert p["max_tokens"] == 4000
    assert a._reasoning_headroom is True


def test_headroom_only_strategy():
    a = _analyst("headroom_only", thinking=True)
    p = {"max_tokens": 450}
    assert a._apply_configured_strategy(p) is True
    assert p["max_tokens"] == 4000


def test_no_strategy_falls_through_to_host_detection():
    a = _analyst("")           # empty -> not handled here
    assert a._apply_configured_strategy({"max_tokens": 450}) is False


def test_configured_strategy_suppresses_host_provider_reasoning_override():
    # minimax host would normally force think_enable via _apply_provider_overrides;
    # with an explicit strategy set, that host reasoning override must NOT fire.
    a = _analyst("openrouter_reasoning", base_url="https://api.minimaxi.chat/v1", thinking=False)
    p = {"messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]}
    a._apply_provider_overrides(p)
    assert "think_enable" not in p
