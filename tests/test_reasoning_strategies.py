import core.reasoning_strategies as rs


def test_openai_effort_disable_sets_minimal():
    p = {}
    rs.apply_strategy(p, "openai_effort", disable=True)
    assert p["reasoning_effort"] == "minimal"


def test_openrouter_reasoning_disable():
    p = {}
    rs.apply_strategy(p, "openrouter_reasoning", disable=True)
    assert p["reasoning"] == {"enabled": False}


def test_template_kwargs_disable():
    p = {}
    rs.apply_strategy(p, "template_kwargs", disable=True)
    assert p["chat_template_kwargs"]["enable_thinking"] is False


def test_think_enable_disable():
    p = {}
    rs.apply_strategy(p, "think_enable", disable=True)
    assert p["think_enable"] is False
    assert p["reasoning_split"] is True


def test_gemini_budget_disable():
    p = {}
    rs.apply_strategy(p, "gemini_thinking_budget", disable=True)
    assert p["extra_body"]["thinkingConfig"]["thinkingBudget"] == 0


def test_anthropic_thinking_disable():
    p = {}
    rs.apply_strategy(p, "anthropic_thinking", disable=True)
    assert p["thinking"] == {"type": "disabled"}


def test_headroom_only_raises_budget_not_flags():
    p = {"max_tokens": 450}
    rs.apply_strategy(p, "headroom_only", disable=True, budget_key="max_tokens")
    assert p["max_tokens"] == 4000
    assert "reasoning_effort" not in p


def test_none_and_unknown_are_noops():
    p = {"max_tokens": 450}
    rs.apply_strategy(p, "none", disable=True)
    rs.apply_strategy(p, "does_not_exist", disable=True)
    assert p == {"max_tokens": 450}


def test_ensure_headroom_never_lowers():
    p = {"max_tokens": 8000}
    rs.ensure_headroom(p, "max_tokens")
    assert p["max_tokens"] == 8000


def test_ordered_for_prioritizes_matching_host():
    order = rs.ordered_for("https://api.minimaxi.chat/v1", "minimax-m3")
    assert order[0].id == "think_enable"
    order2 = rs.ordered_for("https://openrouter.ai/api/v1", "minimax/minimax-m3")
    assert order2[0].id == "openrouter_reasoning"
