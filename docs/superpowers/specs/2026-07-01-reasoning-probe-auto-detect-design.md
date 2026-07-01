# Reasoning-Model Auto-Detection Probe — Design

**Date:** 2026-07-01
**Status:** Approved design, pre-implementation
**Author:** claude (rova)

## Problem

Many LLMs run in a "thinking / reasoning" mode that spends the response's
token budget on hidden chain-of-thought, leaving **empty visible output**.
SparkyBot's current mitigation is a set of per-provider host checks
(`_is_deepseek_host`, `_is_gemini_pro_host`, `_is_minimax_reasoning_host`) that
key off the API base-URL hostname. This is whack-a-mole and it fails in the
exact case observed in production:

```
Requesting AI analysis from https://openrouter.ai/api/v1 using minimax/minimax-m3
silent_failure: no reasoning_content fallback, completion_tokens=450 max_tokens=450 content='\n'
```

OpenRouter proxies every model behind one hostname and renames the models, so
hostname-based detection never fires. Chasing individual models/hosts does not
scale.

## Core idea

Stop identifying **models**. Empirically detect the **reasoning-control
mechanism** at Test-Connection time by probing the live endpoint, then persist
the flag that actually worked so every real fight uses it. There are only ~6
reasoning-control conventions in the whole ecosystem — a finite, stable set —
versus thousands of models.

The machine determines automatically:
- whether a working off-switch exists on this endpoint, and **which flag** it is;
- whether the model is a *forced reasoner* (no off-switch — needs budget headroom);
- the plumbing quirks already handled (`max_tokens` vs `max_completion_tokens`, Responses API).

The only genuine human choice: when **both** "reasoning off (cheap)" and
"reasoning on (maybe smarter)" work — a cost/quality preference. Everything else
auto-applies.

## Reasoning-control strategy table (the finite universe)

| id | payload mutation to DISABLE thinking | used by |
|----|--------------------------------------|---------|
| `openai_effort` | `reasoning_effort: "minimal"` (fallback `"low"`) | OpenAI o-series, gpt-5.x |
| `openrouter_reasoning` | `reasoning: {"enabled": false}` (ON: `{"max_tokens": N}`) | OpenRouter unified |
| `template_kwargs` | `chat_template_kwargs: {"enable_thinking": false}` | DeepSeek, Qwen |
| `think_enable` | `think_enable: false` | MiniMax |
| `gemini_thinking_budget` | `extra_body.thinkingConfig: {"thinkingBudget": 0}` | Google Gemini |
| `anthropic_thinking` | `thinking: {"type": "disabled"}` | Anthropic |
| `headroom_only` | *(no flag — forced reasoner, rely on budget)* | forced reasoners |
| `none` | *(model is not a reasoning model — leave as-is)* | normal models |

## Architecture (isolated units)

### 1. `core/reasoning_strategies.py` (new — pure, no network, no Qt)
- `Strategy` objects with: `id`, `label`, `apply(payload, *, disable: bool)`,
  and `hint(base_url, model) -> int` (priority weight; this is where demoted
  host-detection lives — as ordering, not gospel).
- `ALL_STRATEGIES` ordered list; helpers `off_switch_strategies()` and
  `ordered_for(base_url, model)`.
- `apply_strategy(payload, strategy_id, *, disable, budget_key)` — the single
  entry point the request path uses to configure reasoning.

### 2. `core/reasoning_probe.py` (new — logic; network via injected factory)
- `ProbeOutcome` dataclass: `strategy_id, disable, budget, ok, empty, errored,
  error_msg, preview, completion_tokens (optional), elapsed_s`.
- `ProbeReport` dataclass: `recommended_strategy_id, recommended_disable,
  recommended_max_tokens, headline, detail, auto_applicable (bool),
  needs_choice (bool), alternatives (list), failure (bool)`.
- `diagnose(baseline, headroom, offswitch_outcomes, user_budget) -> ProbeReport`
  — **pure function**, the heart of the feature, fully unit-tested. Rules:
  - baseline ok (real text, not silent) → `none`, keep current, auto-apply.
  - baseline empty, an off-switch outcome ok → that strategy, disable=True,
    budget=user_budget. If headroom also ok → `needs_choice=True` (cheap vs smart).
  - baseline empty, no off-switch ok, headroom ok → `headroom_only`,
    disable=False, budget=max(user_budget, 4000). Forced reasoner. auto-apply.
  - nothing ok → `failure=True`, surface best error/preview.
- `run_probe(analyst_factory, test_summary, *, user_budget, headroom_budget=4000,
  timeout, base_url, model) -> ProbeReport` — staged, stop-early:
  1. **Baseline:** 1 call at user_budget, no suppression. If healthy → return (common case, 1 call).
  2. **Headroom:** 1 call reasoning-on at headroom_budget (can the model produce text with room?).
  3. **Off-switch discovery:** iterate off-switch strategies in `hint` order,
     1 call each at user_budget, **stop at first success**.
  Worst case ~4–5 calls, only when a model is reasoning-troubled.
- `format_report(report) -> str` — plain-language rendering shared by both UIs.

### 3. `core/fight_analyst.py` (generalize)
- Accept an explicit `reasoning_strategy` id + `disable_thinking` and apply it in
  the request path via `apply_strategy(...)`. An explicit configured strategy
  **takes precedence** over the host-detection defaults.
- Keep host-detection functions, but demote them to feeding `Strategy.hint`
  ordering only.
- Expose per-call diagnostics for the probe: `last_completion_tokens`,
  `last_finish_reason`, `last_empty` set after each request (best-effort;
  report degrades gracefully if usage is absent).

### 4. `core/config.py`
- Add `ai_reasoning_strategy = self._config.get('AI', 'aiReasoningStrategy', fallback='')`.
  Empty string = unset → request path falls back to host-detection defaults
  (backward compatible). Written by the probe's auto-apply/Apply.

### 5. UI — `core/gui_settings.py` AND `core/setup_wizard.py` (identical behavior)
- Replace single-shot `_test_ai_connection` with a worker-thread `run_probe`,
  with staged status text ("Testing… reasoning off / on / detecting flag…").
- Render `format_report(report)`:
  - `auto_applicable` → set `ai_disable_thinking`, `ai_max_tokens`,
    `ai_reasoning_strategy` (widgets in gui; config + minimal widgets in wizard)
    automatically; show "Applied: …".
  - `needs_choice` → show cheap-vs-smart options + an Apply button; user picks.
  - `failure` → show error + preview.
- **Wizard parity:** the wizard AI page currently lacks disable-thinking and
  max-tokens widgets. Add minimal ones (or write config directly) so the probe
  behaves identically on both surfaces.

### 6. Tests
- `tests/test_reasoning_strategies.py` — each strategy's `apply()` payload
  mutation (disable + on); `ordered_for` priority given a host/model.
- `tests/test_reasoning_probe.py` — `diagnose` matrix: baseline-ok /
  baseline-empty+offswitch-found / forced-reasoner(headroom-only) /
  nothing-works / both-work(needs_choice) / off-switch ordering. `run_probe`
  with a fake `analyst_factory` returning scripted outcomes — assert staging &
  call counts (healthy = 1 call; troubled stops at first off-switch hit).
- `tests/test_fight_analyst_strategy.py` — explicit configured strategy beats
  host-detection in the request path; `last_*` diagnostics populated.

## Data flow

```
Test Connection click
  → run_probe(analyst_factory, test_summary)
      → baseline call ── healthy? ─→ ProbeReport(none, auto-apply) ─┐
      → headroom call                                               │
      → off-switch strategies (ordered, stop-early)                 │
      → diagnose(...) ─→ ProbeReport ──────────────────────────────┤
  → format_report → UI renders                                     │
  → auto_applicable: write config (disable, max_tokens, strategy) ─┘
  → needs_choice: user picks cheap|smart → Apply → write config
Real fight → FightAnalyst reads ai_reasoning_strategy → apply_strategy()
```

## Error handling
- Every probe call wrapped; an errored call becomes `ProbeOutcome(errored=True)`,
  never crashes the probe. `diagnose` treats errored ≠ empty.
- Silent/empty detection reuses the existing `SilentFailureGuard.is_silent_failure`
  criterion so "empty" means the same thing everywhere.
- Nondeterminism: single-sample risk is acknowledged; the human Apply/confirm
  step (and re-run Test Connection) is the safety valve.

## Scope / YAGNI
- **No** per-model persistent database — one active `ai_reasoning_strategy` per
  connection is enough (changing model/provider re-probes).
- **No** probing on launch — only on Test Connection.
- Token-burn read is best-effort; the report works without it.
- Do **not** rip out `SilentFailureGuard` here; the probe's headroom budget just
  avoids tripping it. (Its shrink-on-retry misbehavior for reasoning models is a
  separate follow-up.)

## Non-goals
- Auto-switching providers/models.
- Changing the runtime retry pipeline beyond honoring the configured strategy.
