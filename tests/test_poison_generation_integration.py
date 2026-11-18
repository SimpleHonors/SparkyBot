"""Synthetic generation availability; no real logs/providers are invoked."""
import sys
from types import ModuleType

import pytest

from core.night_model import build_night_model

TAG = "2000-01-01-00:00:00"


def source_rows(*, output="0.0", name="Availability Test", prof="Scourge"):
    tiddlers = [{"title": TAG + "-Conditions-Out", "text": (
        "|!Party |!Name | !Prof | !{{FightTime}} |!Poison |h\n"
        f'|1 |<span data-tooltip="Synthetic.0001">{name}</span> '
        f'|{{{{{prof}}}}} |120 |12 |')}]
    if output is not None:
        tiddlers.append({"title": TAG + "-Total-Condition-Output-Generation",
                        "text": ("option = {dataset: {source: [[\"Player\", \"Poison\"], "
                                 f'["{{{{{prof}}}}} - {name}", {output}]]}}}}')})
    return tiddlers


@pytest.fixture(autouse=True)
def offline_review_import(monkeypatch):
    # AI import only; the real model and poison parser execute unchanged.
    review = ModuleType("core.run_analysis")
    review.read_review = lambda source: None
    monkeypatch.setitem(sys.modules, "core.run_analysis", review)


@pytest.mark.parametrize(("output", "available"), [
    ("0.0", True), ("0.000001", True), (None, False),
    ('"not measured"', False), ("NaN", False), ("Infinity", False),
])
def test_normal_model_distinguishes_measured_output_from_unavailable(output, available):
    row, = build_night_model(source_rows(output=output))["poison"]
    assert row["output_available"] is available
    assert (row["account"], row["name"], row["prof"], row["apps"], row["fight_time"]) == (
        "Synthetic.0001", "Availability Test", "Scourge", 12, 120.0)
    assert row["apps_per_min"] == 6.0
    if output in ("0.0", "0.000001"):
        assert row["output"] == float(output)


@pytest.mark.parametrize("output", ["-0.5", "-0.000001"])
def test_negative_output_remains_numeric_but_unavailable(output):
    from dataclasses import asdict
    from core.poison_tab import build_model
    tiddlers = source_rows(output=output)
    legacy, = build_model(tiddlers)
    actual, = build_night_model(tiddlers)["poison"]
    assert actual["output"] == float(output)
    assert {k: v for k, v in actual.items() if k != "output_available"} == dict(
        asdict(legacy), apps_per_min=legacy.apps_per_min)
    assert actual["output_available"] is False


@pytest.mark.parametrize("case", ["wrong-profession", "same-name-accounts", "duplicate-generation"])
def test_ambiguous_generation_never_becomes_a_measured_coordinate(case):
    from dataclasses import asdict
    from core.poison_tab import build_model
    tiddlers = source_rows(output="0.125")
    if case == "wrong-profession":
        tiddlers[1]["text"] = tiddlers[1]["text"].replace("{{Scourge}}", "{{Reaper}}")
    elif case == "same-name-accounts":
        line = tiddlers[0]["text"].splitlines()[1]
        tiddlers[0]["text"] += "\n" + line.replace("Synthetic.0001", "Synthetic.0002")
    else:
        tiddlers[1]["text"] = tiddlers[1]["text"].replace(
            '0.125]]', '0.125], ["{{Scourge}} - Availability Test", 0.25]]')
    original = [dict(asdict(r), apps_per_min=r.apps_per_min) for r in build_model(tiddlers)]
    actual = build_night_model(tiddlers)["poison"]
    assert all(r["output_available"] is False for r in actual)
    assert [{k: v for k, v in r.items() if k != "output_available"} for r in actual] == original


@pytest.mark.parametrize("output", ["0.0", "NaN", "Infinity", "-Infinity"])
def test_normal_generator_includes_native_poison_plot_in_dps(output):
    from playwright.sync_api import sync_playwright, expect
    from core.report_viewer import build_switchable_report, unpack_night_model, unpack_classic_report
    model = build_night_model(source_rows(output=output))
    classic = "<!doctype html><html><title>Synthetic generator proof</title><body>Unchanged Classic</body></html>"
    rendered = build_switchable_report(classic, model, default_view="pro")
    assert "function poisonBubbleChart()" in rendered
    assert 'poisonContext() + poisonBubbleChart() + poisonDetailsTable()' in rendered
    assert unpack_classic_report(rendered) == classic
    # Exercise the shell's gzip inflate + JSON.parse, not Python's lenient loads.
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.route("**/*", lambda route: route.abort())
        page.set_content(rendered)
        expect(page.frame_locator("#report-frame").locator('[data-tab="dps"]')).to_be_visible()
        browser.close()
    packed = unpack_night_model(rendered)
    assert packed == model
    row, = packed["poison"]
    assert row["output_available"] is (output == "0.0")
    assert row["output"] == (0.0 if output == "0.0" else None)
