# Reasoning-Model Auto-Detection Probe — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** At Test-Connection time, empirically detect which reasoning-control flag a live endpoint honors, persist the winning flag, and make the runtime honor it — replacing per-model host matching.

**Architecture:** A finite strategy table (`reasoning_strategies.py`) encodes the ~6 reasoning-control conventions. A probe (`reasoning_probe.py`) runs the test fight staged (baseline → headroom → off-switch discovery) via an injected analyst factory and a pure `diagnose()` decision function. `FightAnalyst` applies the persisted strategy in its request path (precedence over host detection), and the silent-failure guard stops shrinking the budget on reasoning retries.

**Tech Stack:** Python 3, PyQt (existing GUI), `requests`, `pytest`, `configparser` (existing `core/config.py`).

## Global Constraints

- Target repo: `/root/SparkyBot`, branch `main`. Do NOT push or create releases — local commits only unless the operator says "ship it".
- Sanitize every diff before any push (no private IPs/hosts/paths). N/A for local commits but keep test fixtures clean.
- Full suite lives in `tests/`; run with the session venv's `pytest`. Keep all existing tests green.
- Config section is `[AI]` in `core/config.py`; keys are camelCase (`aiMaxTokens`, `aiDisableThinking`). New key: `aiReasoningStrategy` (string, default `""`).
- Reasoning-safe budget floor is **4000** tokens (existing `_REASONING_MIN_BUDGET`); reuse this value as `HEADROOM_FLOOR`.
- Strategy IDs are stable strings persisted to config — never rename without a migration: `openai_effort`, `openrouter_reasoning`, `template_kwargs`, `think_enable`, `gemini_thinking_budget`, `anthropic_thinking`, `headroom_only`, `none`.
- Both `core/gui_settings.py` and `core/setup_wizard.py` must end with identical probe behavior.

---

### Task 1: Reasoning strategy table

**Files:**
- Create: `core/reasoning_strategies.py`
- Test: `tests/test_reasoning_strategies.py`

**Interfaces:**
- Produces:
  - `HEADROOM_FLOOR: int = 4000`
  - `class Strategy` with `id: str`, `label: str`, `is_off_switch: bool`, `apply(payload: dict, *, disable: bool) -> None`, `hint(base_url: str, model: str) -> int`
  - `ALL_STRATEGIES: list[Strategy]`, `BY_ID: dict[str, Strategy]`
  - `off_switch_strategies() -> list[Strategy]`
  - `ordered_for(base_url: str, model: str) -> list[Strategy]` (off-switch strategies, highest hint first)
  - `apply_strategy(payload: dict, strategy_id: str, *, disable: bool, budget_key: str = "max_tokens", headroom_floor: int = HEADROOM_FLOOR) -> None`
  - `ensure_headroom(payload: dict, budget_key: str, floor: int = HEADROOM_FLOOR) -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reasoning_strategies.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reasoning_strategies.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.reasoning_strategies'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/reasoning_strategies.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_reasoning_strategies.py -q`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add core/reasoning_strategies.py tests/test_reasoning_strategies.py
git commit -m "feat: reasoning-control strategy table (finite mechanism set)"
```

---

### Task 2: Probe outcome types + pure diagnose()

**Files:**
- Create: `core/reasoning_probe.py`
- Test: `tests/test_reasoning_probe_diagnose.py`

**Interfaces:**
- Consumes: `core.reasoning_strategies.HEADROOM_FLOOR`
- Produces:
  - `@dataclass ProbeOutcome` fields: `strategy_id: str, disable: bool, budget: int, ok: bool=False, empty: bool=False, errored: bool=False, error_msg: str="", preview: str="", completion_tokens: Optional[int]=None, elapsed_s: float=0.0`
  - `@dataclass ProbeReport` fields: `recommended_strategy_id: str, recommended_disable: bool, recommended_max_tokens: int, headline: str, detail: str, auto_applicable: bool=False, needs_choice: bool=False, failure: bool=False, alternatives: list=[]`
    where each alternative is a tuple `(key: str, strategy_id: str, disable: bool, max_tokens: int, blurb: str)`
  - `diagnose(baseline: ProbeOutcome, headroom: Optional[ProbeOutcome], offswitch_outcomes: list[ProbeOutcome], user_budget: int, headroom_floor: int = HEADROOM_FLOOR) -> ProbeReport`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reasoning_probe_diagnose.py
from core.reasoning_probe import ProbeOutcome, diagnose


def _oc(strategy_id, ok, **kw):
    return ProbeOutcome(strategy_id=strategy_id, disable=kw.get("disable", False),
                        budget=kw.get("budget", 450), ok=ok, empty=not ok,
                        error_msg=kw.get("error_msg", ""))


def test_baseline_ok_means_no_fix():
    r = diagnose(_oc("none", True), None, [], 450)
    assert r.recommended_strategy_id == "none"
    assert r.auto_applicable and not r.needs_choice and not r.failure


def test_offswitch_found_auto_applies():
    base = _oc("none", False)
    offs = [_oc("openrouter_reasoning", False, disable=True), _oc("think_enable", True, disable=True)]
    r = diagnose(base, _oc("headroom_only", False), offs, 450)
    assert r.recommended_strategy_id == "think_enable"
    assert r.recommended_disable is True
    assert r.recommended_max_tokens == 450
    assert r.auto_applicable and not r.needs_choice


def test_offswitch_and_headroom_both_work_needs_choice():
    base = _oc("none", False)
    offs = [_oc("think_enable", True, disable=True)]
    r = diagnose(base, _oc("headroom_only", True), offs, 450)
    assert r.needs_choice is True
    assert len(r.alternatives) == 2
    assert {a[0] for a in r.alternatives} == {"off", "on"}


def test_forced_reasoner_headroom_only():
    base = _oc("none", False)
    offs = [_oc("openai_effort", False, disable=True)]
    r = diagnose(base, _oc("headroom_only", True), offs, 450)
    assert r.recommended_strategy_id == "headroom_only"
    assert r.recommended_disable is False
    assert r.recommended_max_tokens == 4000
    assert r.auto_applicable


def test_nothing_works_is_failure():
    base = ProbeOutcome("none", False, 450, ok=False, errored=True, error_msg="401 unauthorized")
    r = diagnose(base, _oc("headroom_only", False), [_oc("think_enable", False, disable=True)], 450)
    assert r.failure is True
    assert "401" in r.detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reasoning_probe_diagnose.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.reasoning_probe'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/reasoning_probe.py
"""Empirical reasoning-mode probe.

Runs the AI test call a few times against a live endpoint to learn which
reasoning-control flag it actually honors, then recommends (and usually
auto-applies) the winning configuration. Model-agnostic: it watches behaviour
instead of matching model names.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from core.reasoning_strategies import HEADROOM_FLOOR


@dataclass
class ProbeOutcome:
    strategy_id: str
    disable: bool
    budget: int
    ok: bool = False
    empty: bool = False
    errored: bool = False
    error_msg: str = ""
    preview: str = ""
    completion_tokens: Optional[int] = None
    elapsed_s: float = 0.0


@dataclass
class ProbeReport:
    recommended_strategy_id: str
    recommended_disable: bool
    recommended_max_tokens: int
    headline: str
    detail: str
    auto_applicable: bool = False
    needs_choice: bool = False
    failure: bool = False
    alternatives: list = field(default_factory=list)


def diagnose(baseline: ProbeOutcome, headroom: Optional[ProbeOutcome],
             offswitch_outcomes: List[ProbeOutcome], user_budget: int,
             headroom_floor: int = HEADROOM_FLOOR) -> ProbeReport:
    off_ok = next((o for o in offswitch_outcomes if o.ok), None)
    headroom_ok = bool(headroom and headroom.ok)

    if baseline.ok:
        return ProbeReport(
            recommended_strategy_id="none",
            recommended_disable=False,
            recommended_max_tokens=user_budget,
            headline="Connection works — no reasoning fix needed.",
            detail=f"The model returned usable text at your current settings ({user_budget} max tokens).",
            auto_applicable=True,
        )

    if off_ok is not None:
        report = ProbeReport(
            recommended_strategy_id=off_ok.strategy_id,
            recommended_disable=True,
            recommended_max_tokens=user_budget,
            headline="Found the off-switch — this model was burying its answer in hidden reasoning.",
            detail=(f"With reasoning left on, the model returned nothing at {user_budget} tokens. "
                    f"Turning it off ('{off_ok.strategy_id}') produced real text."),
        )
        if headroom_ok:
            report.needs_choice = True
            report.headline = "This model works both ways — pick cheap or smart."
            report.alternatives = [
                ("off", off_ok.strategy_id, True, user_budget,
                 "Faster & cheaper — reasoning off."),
                ("on", "headroom_only", False, max(user_budget, headroom_floor),
                 "Possibly smarter — reasoning on, with room to think."),
            ]
        else:
            report.auto_applicable = True
        return report

    if headroom_ok:
        budget = max(user_budget, headroom_floor)
        return ProbeReport(
            recommended_strategy_id="headroom_only",
            recommended_disable=False,
            recommended_max_tokens=budget,
            headline="This model can't stop reasoning — gave it room instead.",
            detail=(f"No off-switch worked, but with a larger budget ({budget} tokens) it produced "
                    f"text. Recommending reasoning ON with headroom."),
            auto_applicable=True,
        )

    best = baseline if baseline.error_msg else (headroom or baseline)
    return ProbeReport(
        recommended_strategy_id="none",
        recommended_disable=False,
        recommended_max_tokens=user_budget,
        headline="Couldn't get usable text from this model.",
        detail=(best.error_msg or "Every attempt came back empty. Check the model name, key, and base URL.")[:400],
        failure=True,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_reasoning_probe_diagnose.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add core/reasoning_probe.py tests/test_reasoning_probe_diagnose.py
git commit -m "feat: probe outcome types + pure diagnose decision function"
```

---

### Task 3: Staged run_probe + format_report

**Files:**
- Modify: `core/reasoning_probe.py`
- Test: `tests/test_reasoning_probe_run.py`

**Interfaces:**
- Consumes: `core.reasoning_strategies.ordered_for`, `diagnose`, `ProbeOutcome`, `ProbeReport`
- Produces:
  - `run_probe(analyst_factory, test_summary, *, user_budget, base_url, model, headroom_floor=HEADROOM_FLOOR, timeout=30, progress=None) -> ProbeReport`
    where `analyst_factory(*, strategy_id, disable, budget)` returns an object with `.run(test_summary, timeout) -> ProbeOutcome`, and `progress` is an optional `Callable[[str], None]`.
  - `format_report(report: ProbeReport) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reasoning_probe_run.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reasoning_probe_run.py -q`
Expected: FAIL with `ImportError: cannot import name 'run_probe'`

- [ ] **Step 3: Write minimal implementation** (append to `core/reasoning_probe.py`)

```python
from core.reasoning_strategies import ordered_for


def run_probe(analyst_factory, test_summary, *, user_budget, base_url, model,
              headroom_floor: int = HEADROOM_FLOOR, timeout: int = 30,
              progress: Optional[Callable[[str], None]] = None) -> ProbeReport:
    def _emit(msg: str) -> None:
        if progress:
            progress(msg)

    _emit("Testing connection…")
    baseline = analyst_factory(strategy_id="none", disable=False, budget=user_budget).run(test_summary, timeout)
    if baseline.ok:
        return diagnose(baseline, None, [], user_budget, headroom_floor)

    _emit("Empty response — checking whether the model just needs more room…")
    headroom = analyst_factory(strategy_id="headroom_only", disable=False,
                               budget=max(user_budget, headroom_floor)).run(test_summary, timeout)

    _emit("Detecting the reasoning off-switch…")
    outcomes: List[ProbeOutcome] = []
    for s in ordered_for(base_url, model):
        oc = analyst_factory(strategy_id=s.id, disable=True, budget=user_budget).run(test_summary, timeout)
        outcomes.append(oc)
        if oc.ok:
            break

    return diagnose(baseline, headroom, outcomes, user_budget, headroom_floor)


def format_report(report: ProbeReport) -> str:
    lines = [report.headline, "", report.detail]
    if report.needs_choice:
        lines.append("")
        for key, _sid, _dis, tokens, blurb in report.alternatives:
            lines.append(f"  • {key.upper()} ({tokens} max tokens): {blurb}")
        lines.append("")
        lines.append("Pick one below, then click Apply.")
    elif report.auto_applicable and not report.failure:
        strat = report.recommended_strategy_id
        state = "OFF" if report.recommended_disable else "ON"
        lines.append("")
        lines.append(f"Applied: reasoning {state}, {report.recommended_max_tokens} max tokens"
                     + (f" (via {strat})" if strat not in ("none", "headroom_only") else "") + ".")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_reasoning_probe_run.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add core/reasoning_probe.py tests/test_reasoning_probe_run.py
git commit -m "feat: staged run_probe orchestration + plain-text report"
```

---

### Task 4: Config field `aiReasoningStrategy`

**Files:**
- Modify: `core/config.py:211` (after `ai_disable_thinking`), plus the save/write path
- Test: `tests/test_config_reasoning_strategy.py`

**Interfaces:**
- Produces: `Config.ai_reasoning_strategy: str` (default `""`), persisted under `[AI] aiReasoningStrategy`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_reasoning_strategy.py
import configparser
from core.config import Config


def test_reasoning_strategy_defaults_empty(tmp_path):
    p = tmp_path / "settings.ini"
    p.write_text("[AI]\naiProvider = custom\n")
    cfg = Config(str(p))
    assert cfg.ai_reasoning_strategy == ""


def test_reasoning_strategy_reads_value(tmp_path):
    p = tmp_path / "settings.ini"
    p.write_text("[AI]\naiProvider = custom\naiReasoningStrategy = think_enable\n")
    cfg = Config(str(p))
    assert cfg.ai_reasoning_strategy == "think_enable"
```

> NOTE: If `Config.__init__` does not accept a path argument, adapt the test to
> the project's existing construction pattern (check the top of `core/config.py`
> and mirror how `tests/` already instantiate `Config`). The assertion on
> `cfg.ai_reasoning_strategy` is the invariant that must hold.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_reasoning_strategy.py -q`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'ai_reasoning_strategy'`

- [ ] **Step 3: Write minimal implementation**

In `core/config.py`, immediately after line 211 (`self.ai_disable_thinking = ...`):

```python
        self.ai_reasoning_strategy = self._config.get('AI', 'aiReasoningStrategy', fallback='')
```

Then find the corresponding save method (grep for `aiDisableThinking` writes — likely a `save()` / `_config.set('AI', 'aiDisableThinking', ...)`). Add alongside it:

```python
        self._config.set('AI', 'aiReasoningStrategy', str(self.ai_reasoning_strategy or ''))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config_reasoning_strategy.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add core/config.py tests/test_config_reasoning_strategy.py
git commit -m "feat: persist aiReasoningStrategy in config"
```

---

### Task 5: FightAnalyst applies the configured strategy (precedence over host detection)

**Files:**
- Modify: `core/fight_analyst.py` — `__init__` (add param + diagnostics), `analyze()` reasoning block (`~463-484`), `_apply_provider_overrides` (`~1552`), and `_handle_success` (populate diagnostics)
- Test: `tests/test_fight_analyst_strategy.py`

**Interfaces:**
- Consumes: `core.reasoning_strategies.apply_strategy`, `ensure_headroom`
- Produces:
  - `FightAnalyst(..., reasoning_strategy: str = "")` → stored as `self.reasoning_strategy`
  - `self.last_completion_tokens: Optional[int]`, `self.last_finish_reason: Optional[str]`, `self.last_empty: Optional[bool]`
  - `FightAnalyst._apply_configured_strategy(payload: dict) -> bool` (True when a configured strategy handled reasoning)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fight_analyst_strategy.py
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
    a = _analyst("")           # empty → not handled here
    assert a._apply_configured_strategy({"max_tokens": 450}) is False


def test_configured_strategy_suppresses_host_provider_reasoning_override():
    # minimax host would normally force think_enable via _apply_provider_overrides;
    # with an explicit strategy set, that host reasoning override must NOT fire.
    a = _analyst("openrouter_reasoning", base_url="https://api.minimaxi.chat/v1", thinking=False)
    p = {"messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]}
    a._apply_provider_overrides(p)
    assert "think_enable" not in p
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fight_analyst_strategy.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'reasoning_strategy'`

- [ ] **Step 3: Write minimal implementation**

3a. In `__init__`, add the parameter to the signature (after `thinking: bool = True,`):

```python
                 reasoning_strategy: str = "",
```

3b. In `__init__` body, near the other reasoning flags (after the `self._reasoning_headroom = False` line ~210):

```python
        # Probe-configured reasoning strategy id (from Test Connection). When
        # set, it takes precedence over the legacy host-detection below and
        # over the provider-override reasoning suppression.
        self.reasoning_strategy = reasoning_strategy or ""
        # Per-call diagnostics, read by the reasoning probe.
        self.last_completion_tokens: "Optional[int]" = None
        self.last_finish_reason: "Optional[str]" = None
        self.last_empty: "Optional[bool]" = None
```

3c. Add the helper method (place it right after `_apply_reasoning_model_params`, ~line 238):

```python
    def _apply_configured_strategy(self, payload: dict) -> bool:
        """Apply the probe-configured reasoning strategy, if any.

        Returns True when a configured strategy handled reasoning (so the
        legacy host-detection block should be skipped).
        """
        sid = self.reasoning_strategy
        if not sid:
            return False
        from core import reasoning_strategies as rs
        key = self._token_budget_key()
        if sid == "none":
            return True
        if sid == "headroom_only":
            rs.ensure_headroom(payload, key)
            self._reasoning_headroom = True
            return True
        # An off-switch strategy.
        if not self.thinking:
            rs.apply_strategy(payload, sid, disable=True, budget_key=key)
        else:
            rs.ensure_headroom(payload, key)
            self._reasoning_headroom = True
        return True
```

3d. In `analyze()`, wrap the legacy host-detection block. Replace lines ~463-484 (the three `if self._is_*_host():` blocks) so they only run when no strategy is configured:

```python
        # Probe-configured strategy wins; otherwise fall back to host detection.
        if not self._apply_configured_strategy(payload):
            if self._is_deepseek_host():
                if not self.thinking:
                    payload["thinking"] = {"type": "disabled"}
                else:
                    self._apply_reasoning_model_params(payload)

            if self._is_gemini_pro_host():
                self._apply_reasoning_model_params(payload)

            if self._is_minimax_reasoning_host():
                self._apply_reasoning_model_params(payload)
```

(Leave the subsequent `if self._use_max_completion_tokens:` and `if self._use_responses_api:` blocks unchanged.)

3e. Split the provider dispatch so host-based *reasoning* suppression is skipped when a strategy is configured, but non-reasoning cleanups (moonshot param strip) still run. Replace the `_PROVIDER_DISPATCH` list and `_apply_provider_overrides` (~1549-1573):

```python
    # Reasoning-suppression providers (skipped when an explicit strategy is set)
    _PROVIDER_DISPATCH_REASONING: list = [
        (_minimax_predicate, _apply_minimax),
        (_gemini_predicate, _apply_gemini),
    ]
    # Non-reasoning provider cleanups (always run)
    _PROVIDER_DISPATCH_CLEANUP: list = [
        (_moonshot_predicate, _apply_moonshot),
    ]

    def _apply_provider_overrides(self, payload: dict) -> None:
        """Apply provider-specific payload fields.

        Non-reasoning cleanups always run. Host-based reasoning suppression is
        skipped when an explicit probe strategy is configured (that strategy
        owns reasoning control instead).
        """
        parsed_host = urlparse(self.base_url).hostname or ""
        model_lower = self.model.lower()
        is_openrouter = (
            parsed_host == "openrouter.ai"
            or parsed_host.endswith(".openrouter.ai")
        )

        for pred, apply in self._PROVIDER_DISPATCH_CLEANUP:
            if pred(parsed_host, model_lower, is_openrouter, self.thinking):
                apply(payload, self.thinking, model_lower)

        if self.reasoning_strategy:
            return  # strategy owns reasoning control

        for pred, apply in self._PROVIDER_DISPATCH_REASONING:
            if pred(parsed_host, model_lower, is_openrouter, self.thinking):
                apply(payload, self.thinking, model_lower)

        # OpenRouter catch-all for any unmatched model
        if is_openrouter and not self.thinking and "reasoning_effort" not in payload:
            payload["reasoning"] = {"effort": "none"}
```

3f. Populate diagnostics in `_handle_success` (find `def _handle_success` and set these near the top, using the `data` dict it receives):

```python
        _choice = data.get("choices", [{}])[0]
        self.last_finish_reason = _choice.get("finish_reason")
        self.last_completion_tokens = data.get("usage", {}).get("completion_tokens")
        _content = _choice.get("message", {}).get("content", "") or ""
        self.last_empty = not _content.strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_fight_analyst_strategy.py -q`
Expected: PASS (5 passed)

Then the full suite to confirm no regression:
Run: `python -m pytest tests/ -q`
Expected: all previously-passing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add core/fight_analyst.py tests/test_fight_analyst_strategy.py
git commit -m "feat: FightAnalyst honors probe-configured reasoning strategy over host detection"
```

---

### Task 6: SilentFailureGuard — stop shrinking the budget on reasoning retries

**Files:**
- Modify: `core/silent_failure_guard.py` (rename `fallback_token_limit` → `headroom_floor`, default 4000; add optional `strategy_id`)
- Modify: `core/fight_analyst.py` retry `else` branch (~547-548) so it never shrinks below the original budget
- Test: `tests/test_silent_failure_guard.py` (extend if present, else create)

**Interfaces:**
- Consumes: `SilentFailureGuard`
- Produces: `SilentFailureGuard.headroom_floor: int = 4000`, `SilentFailureGuard.strategy_id: str = ""`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_silent_failure_guard.py  (add these; keep existing tests)
from core.silent_failure_guard import SilentFailureGuard


def test_headroom_floor_default_is_4000():
    g = SilentFailureGuard()
    assert g.headroom_floor == 4000


def test_retry_budget_never_below_original():
    # Simulates the fight_analyst retry math for a reasoning model:
    # the retry budget must be >= the original, never the old 400 shrink.
    original = 450
    g = SilentFailureGuard()
    retry_budget = max(original, g.headroom_floor)
    assert retry_budget >= original
    assert retry_budget == 4000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_silent_failure_guard.py -q`
Expected: FAIL with `AttributeError: 'SilentFailureGuard' object has no attribute 'headroom_floor'`

- [ ] **Step 3: Write minimal implementation**

3a. In `core/silent_failure_guard.py`, replace the `fallback_token_limit` field (~line 24) and add `strategy_id`:

```python
    headroom_floor: int = 4000        # retry budget floor for reasoning models (never shrink below this)
    strategy_id: str = ""             # probe-configured off-switch to engage on retry
```

3b. In `core/fight_analyst.py`, fix the retry `else` branch (~547-548) so it never shrinks. Replace:

```python
                            else:
                                payload["messages"][1]["content"] += hot_take
                                payload[self._token_budget_key()] = self._silent_guard.fallback_token_limit
```

with:

```python
                            else:
                                # Never shrink the budget on retry: a model that
                                # ate its budget on hidden reasoning needs MORE
                                # room, not less. Engage the configured
                                # off-switch if we have one, then floor the
                                # budget at the reasoning-safe headroom.
                                payload["messages"][1]["content"] += hot_take
                                if self._silent_guard.strategy_id and self.thinking is False:
                                    from core import reasoning_strategies as rs
                                    rs.apply_strategy(payload, self._silent_guard.strategy_id,
                                                      disable=True, budget_key=self._token_budget_key())
                                key = self._token_budget_key()
                                payload[key] = max(int(payload.get(key) or 0),
                                                   self._silent_guard.headroom_floor)
```

3c. Wire the guard's `strategy_id` from the analyst in `__init__` (right after `self._silent_guard = SilentFailureGuard()` ~line 177 — but note `reasoning_strategy` is set later, so set it at end of `__init__` instead):

At the end of `__init__`, after `self.last_empty = None`:

```python
        self._silent_guard.strategy_id = self.reasoning_strategy
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_silent_failure_guard.py -q`
Expected: PASS

Full suite:
Run: `python -m pytest tests/ -q`
Expected: all green. (If any test referenced `fallback_token_limit`, update it to `headroom_floor` — grep: `grep -rn fallback_token_limit tests/ core/`.)

- [ ] **Step 5: Commit**

```bash
git add core/silent_failure_guard.py core/fight_analyst.py tests/test_silent_failure_guard.py
git commit -m "fix: reasoning silent-failure retry raises budget instead of shrinking it"
```

---

### Task 7: Real analyst factory for the probe

**Files:**
- Modify: `core/reasoning_probe.py` (add real factory glue)
- Test: `tests/test_reasoning_probe_factory.py`

**Interfaces:**
- Consumes: `core.ai_analyst.FightAnalyst` (lazy import), `ProbeOutcome`
- Produces: `make_real_factory(base_url, api_key, model, system_prompt=None) -> factory` where `factory(*, strategy_id, disable, budget)` returns a `_RealProbeCall` with `.run(test_summary, timeout) -> ProbeOutcome`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reasoning_probe_factory.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reasoning_probe_factory.py -q`
Expected: FAIL with `AttributeError: module 'core.reasoning_probe' has no attribute 'make_real_factory'`

- [ ] **Step 3: Write minimal implementation** (append to `core/reasoning_probe.py`)

```python
import time


class _RealProbeCall:
    def __init__(self, base_url, api_key, model, system_prompt, strategy_id, disable, budget):
        self._args = (base_url, api_key, model, system_prompt)
        self.strategy_id = strategy_id
        self.disable = disable
        self.budget = budget

    def run(self, test_summary, timeout) -> ProbeOutcome:
        from core.ai_analyst import FightAnalyst  # lazy — avoids import cost/cycle
        base_url, api_key, model, system_prompt = self._args
        t0 = time.monotonic()
        try:
            analyst = FightAnalyst(
                base_url=base_url, api_key=api_key, model=model,
                system_prompt=system_prompt, max_tokens=self.budget,
                thinking=not self.disable, reasoning_strategy=self.strategy_id,
            )
            text = analyst.analyze(test_summary, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — probe must never crash the UI
            return ProbeOutcome(self.strategy_id, self.disable, self.budget,
                                errored=True, error_msg=str(exc),
                                elapsed_s=time.monotonic() - t0)
        empty = not (text and text.strip())
        return ProbeOutcome(
            self.strategy_id, self.disable, self.budget,
            ok=not empty, empty=empty,
            preview=(text or "")[:200],
            completion_tokens=getattr(analyst, "last_completion_tokens", None),
            elapsed_s=time.monotonic() - t0,
        )


def make_real_factory(base_url, api_key, model, system_prompt=None):
    def factory(*, strategy_id, disable, budget):
        return _RealProbeCall(base_url, api_key, model, system_prompt, strategy_id, disable, budget)
    return factory
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_reasoning_probe_factory.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add core/reasoning_probe.py tests/test_reasoning_probe_factory.py
git commit -m "feat: real FightAnalyst factory for the probe"
```

---

### Task 8: Wire the probe into gui_settings Test Connection

**Files:**
- Modify: `core/gui_settings.py` — `_test_ai_connection` (~1122), `_on_ai_test_done` (~1193), add an Apply button + a mapping helper
- Test: `tests/test_apply_report_to_config.py` (pure mapping helper; Qt itself is verified manually)

**Interfaces:**
- Consumes: `core.reasoning_probe.run_probe`, `make_real_factory`, `format_report`, `ProbeReport`
- Produces: module-level `apply_report_to_config(report, *, alt_key=None) -> dict` returning `{"ai_disable_thinking": bool, "ai_max_tokens": int, "ai_reasoning_strategy": str}` — shared by both UIs.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_apply_report_to_config.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_apply_report_to_config.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.reasoning_settings_apply'`

- [ ] **Step 3: Write minimal implementation**

3a. Create `core/reasoning_settings_apply.py`:

```python
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
```

3b. In `core/gui_settings.py`, replace the body of `_test_ai_connection` (~1122) with a probe run. Keep the existing `test_summary` dict; swap the worker:

```python
    def _test_ai_connection(self):
        """Probe the AI connection both ways and auto-apply the reasoning fix."""
        from core.reasoning_probe import run_probe, make_real_factory, format_report
        from core.reasoning_settings_apply import apply_report_to_config

        test_summary = { ... }   # UNCHANGED — keep the existing dict verbatim

        base_url = self.ai_base_url.text()
        api_key = self.ai_api_key.text()
        model = self.ai_model.currentText()
        system_prompt = self.ai_system_prompt.toPlainText() or None
        user_budget = self.ai_max_tokens.value()
        timeout = self.ai_timeout.value()

        self.ai_test_status.setText("Testing…")
        self.ai_test_btn.setEnabled(False)
        self._last_report = None

        import threading

        def _run_test():
            try:
                factory = make_real_factory(base_url, api_key, model, system_prompt)
                report = run_probe(
                    factory, test_summary, user_budget=user_budget,
                    base_url=base_url, model=model, timeout=timeout,
                    progress=lambda m: self._sig_ai_test_progress.emit(m),
                )
                self._last_report = report
                if report.auto_applicable and not report.failure:
                    applied = apply_report_to_config(report)
                    self._sig_ai_apply.emit(applied)
                self._sig_ai_test_done.emit(format_report(report), not report.failure)
            except Exception as exc:  # noqa: BLE001
                self._sig_ai_test_done.emit(f"Test failed: {exc}", False)

        threading.Thread(target=_run_test, daemon=True).start()
```

3c. Add the signals + slots. Near the other `_sig_ai_*` signal declarations in the class, add:

```python
    _sig_ai_test_progress = pyqtSignal(str)
    _sig_ai_apply = pyqtSignal(dict)
```

Connect them in `_setup_ui` (or wherever `_sig_ai_test_done` is connected):

```python
        self._sig_ai_test_progress.connect(lambda m: self.ai_test_status.setText(m))
        self._sig_ai_apply.connect(self._apply_reasoning_settings)
```

Add the apply slot (main thread — safe to touch widgets):

```python
    def _apply_reasoning_settings(self, applied: dict):
        self.ai_disable_thinking.setChecked(applied["ai_disable_thinking"])
        self.ai_max_tokens.setValue(applied["ai_max_tokens"])
        self._reasoning_strategy = applied["ai_reasoning_strategy"]  # saved on Save
```

3d. Persist `self._reasoning_strategy` in `_on_save_clicked` (find where `aiDisableThinking` is written, ~2497) and write it to config:

```python
        cfg('AI', 'aiReasoningStrategy', getattr(self, '_reasoning_strategy', ''))
```

(Match the exact `cfg(...)` / `self._config.set(...)` idiom already used for `aiDisableThinking` at that location.)

3e. For the `needs_choice` case, add an Apply button that reads a small combo of the alternatives. Minimal implementation: when `_on_ai_test_done` receives a report with `needs_choice`, show two radio buttons (OFF/ON) and an Apply button that calls `_apply_reasoning_settings(apply_report_to_config(self._last_report, alt_key=<selected>))`. If wiring radios is heavy, ship the simpler default now: auto-apply the **OFF** alternative (cheaper) and tell the user they can flip the checkbox — but the button path is preferred to honor the "let them choose" requirement.

- [ ] **Step 4: Verify**

Run the pure helper test:
Run: `python -m pytest tests/test_apply_report_to_config.py -q`
Expected: PASS (2 passed)

Manual GUI check (no automated Qt test):
1. Launch settings, set base_url `https://openrouter.ai/api/v1`, model `minimax/minimax-m3`, a valid key.
2. Click Test Connection.
3. Expected: staged status text, then a report saying the off-switch was found (or headroom applied), and the Disable-Thinking checkbox + Max Tokens auto-updated.

- [ ] **Step 5: Commit**

```bash
git add core/gui_settings.py core/reasoning_settings_apply.py tests/test_apply_report_to_config.py
git commit -m "feat: gui Test Connection runs the reasoning probe and auto-applies"
```

---

### Task 9: Setup wizard parity

**Files:**
- Modify: `core/setup_wizard.py` — AI config page (`~853-1187`): add Disable-Thinking checkbox + Max-Tokens spinner, replace `_test_ai_connection` (~1105) with the same probe flow, persist strategy in `validatePage`
- Test: none automated (shares the tested modules); manual parity check

**Interfaces:**
- Consumes: `core.reasoning_probe.run_probe`, `make_real_factory`, `format_report`, `core.reasoning_settings_apply.apply_report_to_config` (all already tested)

- [ ] **Step 1: Add the missing widgets**

In the AI page `__init__` (`~853`), after the model row (`~91`), add:

```python
        self.ai_max_tokens = QSpinBox()
        self.ai_max_tokens.setRange(100, 8000)
        self.ai_max_tokens.setValue(self.config.ai_max_tokens or 450)
        _make_row("Max Tokens:", self.ai_max_tokens)

        self.ai_disable_thinking = QCheckBox("Disable Thinking / Reasoning Mode")
        _make_row("", self.ai_disable_thinking)

        self._reasoning_strategy = self.config.ai_reasoning_strategy or ""
```

- [ ] **Step 2: Replace `_test_ai_connection` (~1105)**

Use the identical probe flow as Task 8 §3b, adapted to the wizard's signal/slot pattern (it uses `QMetaObject.invokeMethod(self, "_set_ai_test_result", ...)`). On an auto-applicable report, call an apply slot that sets `self.ai_disable_thinking`, `self.ai_max_tokens`, and `self._reasoning_strategy`:

```python
    def _apply_reasoning_settings(self, applied: dict):
        self.ai_disable_thinking.setChecked(applied["ai_disable_thinking"])
        self.ai_max_tokens.setValue(applied["ai_max_tokens"])
        self._reasoning_strategy = applied["ai_reasoning_strategy"]
```

- [ ] **Step 3: Persist in `validatePage` (~1187)**

Where the page writes AI settings to `self.config`, add:

```python
        self.config.ai_max_tokens = self.ai_max_tokens.value()
        self.config.ai_disable_thinking = self.ai_disable_thinking.isChecked()
        self.config.ai_reasoning_strategy = getattr(self, "_reasoning_strategy", "")
```

- [ ] **Step 4: Verify (manual parity)**

1. Run the setup wizard, reach the AI page.
2. Confirm the Disable-Thinking checkbox + Max-Tokens spinner are present.
3. Enter the OpenRouter/minimax config, click Test Connection.
4. Expected: same staged report + auto-apply behavior as the settings dialog.

Run the full suite once more to confirm nothing regressed:
Run: `python -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add core/setup_wizard.py
git commit -m "feat: setup wizard reasoning probe parity with settings dialog"
```

---

## Final verification

- [ ] Run the entire suite: `python -m pytest tests/ -q` — all green.
- [ ] `grep -rn fallback_token_limit core/ tests/` returns nothing (fully renamed).
- [ ] `grep -rn "_is_deepseek_host\|_is_gemini_pro_host\|_is_minimax_reasoning_host" core/fight_analyst.py` — still present, but only reached when `reasoning_strategy` is empty (legacy fallback).
- [ ] Manual end-to-end: OpenRouter + `minimax/minimax-m3` through Test Connection auto-detects and applies a working config; a real fight then returns non-empty commentary.
- [ ] Do NOT bump version / push / release — report completion and wait for the operator's "ship it".
