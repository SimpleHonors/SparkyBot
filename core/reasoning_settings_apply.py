"""Map a ProbeReport onto the three config knobs, shared by both UIs."""
from __future__ import annotations


def apply_report_to_config(report, *, alt_key=None) -> dict:
    if report.needs_choice and alt_key is not None:
        for key, strategy_id, disable, tokens, _blurb in report.alternatives:
            if key == alt_key:
                return {"ai_disable_thinking": disable, "ai_max_tokens": tokens,
                        "ai_reasoning_strategy": strategy_id}
    return {"ai_disable_thinking": report.recommended_disable,
            "ai_max_tokens": report.recommended_max_tokens,
            "ai_reasoning_strategy": report.recommended_strategy_id}
