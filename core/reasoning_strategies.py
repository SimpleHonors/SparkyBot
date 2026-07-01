"""Finite table of reasoning-control conventions.

There are only a handful of ways an OpenAI-compatible endpoint lets you turn a
model's hidden "thinking" down or off. This module encodes each as a Strategy
whose apply() mutates a /chat/completions payload. The probe
(core/reasoning_probe.py) discovers which one an endpoint actually honors; the
runtime (core/fight_analyst.py) applies the persisted winner. This replaces
per-model host matching with per-mechanism detection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List
from urllib.parse import urlparse

HEADROOM_FLOOR = 4000  # reasoning-safe budget floor (matches _REASONING_MIN_BUDGET)


@dataclass(frozen=True)
class Strategy:
    id: str
    label: str
    is_off_switch: bool
    _apply: Callable[[dict, bool], None]
    _hint: Callable[[str, str], int]

    def apply(self, payload: dict, *, disable: bool) -> None:
        self._apply(payload, disable)

    def hint(self, base_url: str, model: str) -> int:
        host = urlparse(base_url).hostname or ""
        return self._hint(host, (model or "").lower())


# --- apply functions (disable=True suppresses thinking) --------------------

def _apply_openai_effort(p: dict, disable: bool) -> None:
    if disable:
        p["reasoning_effort"] = "minimal"


def _apply_openrouter(p: dict, disable: bool) -> None:
    if disable:
        p["reasoning"] = {"enabled": False}


def _apply_template_kwargs(p: dict, disable: bool) -> None:
    if disable:
        p.setdefault("chat_template_kwargs", {})["enable_thinking"] = False


def _apply_think_enable(p: dict, disable: bool) -> None:
    if disable:
        p["think_enable"] = False
        p["reasoning_split"] = True


def _apply_gemini_budget(p: dict, disable: bool) -> None:
    if disable:
        p.setdefault("extra_body", {}).setdefault("thinkingConfig", {})["thinkingBudget"] = 0


def _apply_anthropic(p: dict, disable: bool) -> None:
    if disable:
        p["thinking"] = {"type": "disabled"}


def _noop(p: dict, disable: bool) -> None:
    pass


# --- hint functions (higher = try first for this host/model) ---------------

def _hint_openrouter(host: str, model: str) -> int:
    return 10 if host == "openrouter.ai" or host.endswith(".openrouter.ai") else 1


def _hint_think_enable(host: str, model: str) -> int:
    if host == "api.minimaxi.chat" or host.endswith(".minimaxi.chat"):
        return 10
    return 3 if "minimax" in model else 1


def _hint_template(host: str, model: str) -> int:
    if host.endswith(".deepseek.com") or host == "api.deepseek.com":
        return 10
    return 3 if ("deepseek" in model or "qwen" in model) else 1


def _hint_gemini(host: str, model: str) -> int:
    if host.endswith(".googleapis.com"):
        return 10
    return 3 if "gemini" in model else 1


def _hint_anthropic(host: str, model: str) -> int:
    if host.endswith(".anthropic.com"):
        return 10
    return 3 if "claude" in model else 1


def _hint_openai(host: str, model: str) -> int:
    if host.endswith(".openai.com") or host.endswith(".azure.com"):
        return 8
    return 2 if any(t in model for t in ("gpt", "o1", "o3", "o4")) else 1


ALL_STRATEGIES: List[Strategy] = [
    Strategy("openai_effort", "OpenAI reasoning_effort", True, _apply_openai_effort, _hint_openai),
    Strategy("openrouter_reasoning", "OpenRouter reasoning flag", True, _apply_openrouter, _hint_openrouter),
    Strategy("template_kwargs", "Template enable_thinking flag", True, _apply_template_kwargs, _hint_template),
    Strategy("think_enable", "MiniMax think_enable flag", True, _apply_think_enable, _hint_think_enable),
    Strategy("gemini_thinking_budget", "Gemini thinkingBudget", True, _apply_gemini_budget, _hint_gemini),
    Strategy("anthropic_thinking", "Anthropic thinking disabled", True, _apply_anthropic, _hint_anthropic),
    Strategy("headroom_only", "No off-switch — add headroom", False, _noop, lambda h, m: 0),
    Strategy("none", "Not a reasoning model", False, _noop, lambda h, m: 0),
]

BY_ID: Dict[str, Strategy] = {s.id: s for s in ALL_STRATEGIES}


def off_switch_strategies() -> List[Strategy]:
    return [s for s in ALL_STRATEGIES if s.is_off_switch]


def ordered_for(base_url: str, model: str) -> List[Strategy]:
    return sorted(off_switch_strategies(), key=lambda s: s.hint(base_url, model), reverse=True)


def ensure_headroom(payload: dict, budget_key: str, floor: int = HEADROOM_FLOOR) -> None:
    payload[budget_key] = max(int(payload.get(budget_key) or 0), floor)


def apply_strategy(payload: dict, strategy_id: str, *, disable: bool,
                   budget_key: str = "max_tokens", headroom_floor: int = HEADROOM_FLOOR) -> None:
    s = BY_ID.get(strategy_id)
    if s is None or s.id == "none":
        return
    if s.id == "headroom_only":
        ensure_headroom(payload, budget_key, headroom_floor)
        return
    s.apply(payload, disable=disable)
