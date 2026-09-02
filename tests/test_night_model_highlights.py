"""Night-model highlights: the eight headline support boards."""

import json
from pathlib import Path

import pytest

from core.night_model import (
    HIGHLIGHT_TOP_N,
    _HIGHLIGHTS_SPEC,
    build_night_model,
)

FIXTURES = Path(__file__).parent / "fixtures"
KEYS = [k for k, _, _ in _HIGHLIGHTS_SPEC]


def _load(name):
    with (FIXTURES / name).open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def aug10():
    return build_night_model(_load("aug10_summary_poison.json"))


@pytest.fixture(scope="module")
def jul18():
    return build_night_model(_load("jul18_night_with_leaderboards.json"))


@pytest.mark.parametrize('fixture', ['aug10', 'jul18'])
def test_all_8_boards_present_and_ordered(fixture, request):
    model = request.getfixturevalue(fixture)
    assert set(model["highlights"]) == set(KEYS)
    for key in KEYS:
        rows = model["highlights"][key]
        assert rows, f"{fixture}:{key} empty"
        assert len(rows) <= HIGHLIGHT_TOP_N
        assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
        values = [r["value"] for r in rows]
        assert values == sorted(values, reverse=True)
        for r in rows:
            assert r["name"]
            assert r["account"]
            assert r["profession"]
            assert isinstance(r["value"], (int, float))


@pytest.mark.parametrize('fixture', ['aug10', 'jul18'])
def test_highlights_add_no_warnings(fixture, request):
    model = request.getfixturevalue(fixture)
    assert [w for w in model["warnings"]
           if w.startswith("highlights")] == []


def test_stability_uptime_is_percentage_band(aug10, jul18):
    for model in (aug10, jul18):
        for r in model["highlights"]["stability_uptime"]:
            assert 0 < r["value"] <= 100


def test_known_tops(aug10):
    # Aug 10 night: healer damage/healing order and the top support
    # contributors are stable facts of this anonymized fixture.
    assert aug10["highlights"]["healing"][0]["account"] == "SampleAcct033.0033"
    assert aug10["highlights"]["cleanses"][0]["account"] == (
        "SampleAcct011.0011")
    assert aug10["highlights"]["strips"][0]["account"] == "SampleAcct065.0065"
    assert aug10["highlights"]["kills"][0]["account"] == "SampleAcct063.0063"


def test_missing_source_warns_and_empties_others_intact(aug10):
    tiddlers = _load("aug10_summary_poison.json")
    tiddlers = [t for t in tiddlers
                if not t.get("title", "").endswith("-Heal-Stats")]
    model = build_night_model(tiddlers)
    assert model["highlights"]["healing"] == []
    assert any(w.startswith("highlights.healing:")
               for w in model["warnings"])
    # every other board still parses
    assert all(model["highlights"][k] for k in KEYS if k != "healing")


def test_garbage_store_warns_for_every_board():
    model = build_night_model([{"nope": 1}])
    warns = {w.split(":")[0] for w in model["warnings"]}
    for key in KEYS:
        assert f"highlights.{key}" in warns
    assert all(model["highlights"][k] == [] for k in KEYS)


@pytest.mark.parametrize('fixture', ['aug10', 'jul18'])
def test_boards_have_unique_players(fixture, request):
    """Stat-tier rows (total / per-sec / per-engaged-sec) must collapse to
    one entry per account - visual defect found in review."""
    model = request.getfixturevalue(fixture)
    for key, rows in model["highlights"].items():
        keys = [r["account"] or r["name"] for r in rows]
        assert len(keys) == len(set(keys)), f"{fixture}:{key} dup players"


def test_jul18_classic_anchors(jul18):
    # Classic report headline values for the 2026-07-18 anonymized fixture.
    top = jul18["highlights"]["healing"][0]
    assert (top["account"], top["value"]) == ("SampleAcct071.0071", 10407038)
    dc = jul18["highlights"]["down_contribution"]
    assert dc[0]["value"] == 2307384
    # raw damage-scale column, never the 22.99%-style pct column: every
    # down-contribution row is damage-scale big.
    assert all(r["value"] >= 100_000 for r in dc)
    dupes = [r["account"] for r in dc
             if r["account"] in ("SampleAcct057.0057", "SampleAcct019.0019")]
    assert dupes == [] or all(dc.count(r) == 1 for r in dupes)


def test_simple_skin_targets_the_eight_boards():
    src = (Path(__file__).parents[1] / "scripts"
           / "spike_viewer_switcher_gen.py").read_text(encoding="utf-8")
    for suffix in ("-Support-Summary", "-Heal-Stats",
                   "-Offensive-Summary", "-Uptimes"):
        assert suffix in src, suffix
    for token in ("condicleanse", "boonstrips", "healing", "resurrects",
                  "downed", "downcontribution", "killed", "stability"):
        assert token in src, token
    # barrier board dropped per operator ruling
    assert "damage_barrier-Leaderboard" not in src
