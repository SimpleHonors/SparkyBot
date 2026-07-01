"""Empirical reasoning-mode probe.

Runs the AI test call a few times against a live endpoint to learn which
reasoning-control flag it actually honors, then recommends (and usually
auto-applies) the winning configuration. Model-agnostic: it watches behaviour
instead of matching model names.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from core.reasoning_strategies import HEADROOM_FLOOR, ordered_for


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

    best = next((o for o in [baseline, headroom, *offswitch_outcomes] if o and o.error_msg), baseline)
    return ProbeReport(
        recommended_strategy_id="none",
        recommended_disable=False,
        recommended_max_tokens=user_budget,
        headline="Couldn't get usable text from this model.",
        detail=(best.error_msg or "Every attempt came back empty. Check the model name, key, and base URL.")[:400],
        failure=True,
    )


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
