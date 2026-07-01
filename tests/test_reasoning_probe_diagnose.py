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


def test_failure_surfaces_offswitch_error():
    base = ProbeOutcome("none", False, 450, ok=False, empty=True)
    headroom = ProbeOutcome("headroom_only", False, 4000, ok=False, empty=True)
    offs = [ProbeOutcome("think_enable", True, 450, ok=False, errored=True, error_msg="429 rate limited")]
    r = diagnose(base, headroom, offs, 450)
    assert r.failure is True
    assert "429" in r.detail
