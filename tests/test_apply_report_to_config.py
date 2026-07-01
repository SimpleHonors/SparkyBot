from core.reasoning_probe import ProbeReport
from core.reasoning_settings_apply import apply_report_to_config


def test_auto_apply_maps_offswitch():
    r = ProbeReport("think_enable", True, 450, "h", "d", auto_applicable=True)
    out = apply_report_to_config(r)
    assert out == {"ai_disable_thinking": True, "ai_max_tokens": 450,
                   "ai_reasoning_strategy": "think_enable"}


def test_needs_choice_uses_selected_alternative():
    r = ProbeReport("think_enable", True, 450, "h", "d", needs_choice=True,
                    alternatives=[("off", "think_enable", True, 450, ""),
                                  ("on", "headroom_only", False, 4000, "")])
    out = apply_report_to_config(r, alt_key="on")
    assert out == {"ai_disable_thinking": False, "ai_max_tokens": 4000,
                   "ai_reasoning_strategy": "headroom_only"}
