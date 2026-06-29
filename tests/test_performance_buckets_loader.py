"""Tests for the runtime-loadable threshold layer in core/performance_buckets.py.

Override present -> active thresholds reflect it (and bucket_axis follows).
Override absent / corrupt / type-garbage -> fall back to the built-in defaults.

Regression coverage for the adversarial review:
  - HIGH: a freshly-written override is picked up by the LIVE read path
    (active_thresholds / bucket_axis) with no process restart and no reliance on
    reload_thresholds() having been called on a particular module instance —
    including the dual-module case main.py creates (performance_buckets imported
    both bare and as core.performance_buckets).
  - MEDIUM: a structurally-valid but type-garbage override does not poison the
    central scoring path; the bad axis falls back to its default instead of
    raising mid-fight.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core import performance_buckets as pb


@pytest.fixture(autouse=True)
def _isolate_thresholds(tmp_path, monkeypatch):
    """Every test starts on the built-in defaults with a guaranteed-absent
    override path, and the module cache is restored afterward."""
    monkeypatch.setattr(pb, "_OVERRIDE_PATH", tmp_path / "absent.json")
    pb.reload_thresholds()
    yield
    monkeypatch.undo()
    pb.reload_thresholds()


def test_defaults_active_when_no_override():
    assert pb.active_thresholds()["dps"] == pb._DEFAULT_THRESHOLDS["dps"]


def test_override_present_is_used(tmp_path, monkeypatch):
    override = tmp_path / "calibration_thresholds.json"
    override.write_text(json.dumps({"dps": [10, 20, 30, 40, 50]}))
    monkeypatch.setattr(pb, "_OVERRIDE_PATH", override)
    pb.reload_thresholds()
    assert list(pb.active_thresholds()["dps"]) == [10, 20, 30, 40, 50]


def test_bucket_axis_reflects_override(tmp_path, monkeypatch):
    override = tmp_path / "calibration_thresholds.json"
    override.write_text(json.dumps({"dps": [10, 20, 30, 40, 50]}))
    monkeypatch.setattr(pb, "_OVERRIDE_PATH", override)
    pb.reload_thresholds()
    assert pb.bucket_axis(25, "dps") == "strong"   # >= p50(20), < p75(30)
    assert pb.bucket_axis(9, "dps") is None         # below the new solid floor


def test_corrupt_override_falls_back_to_defaults(tmp_path, monkeypatch):
    override = tmp_path / "calibration_thresholds.json"
    override.write_text("{ this is not valid json ]")
    monkeypatch.setattr(pb, "_OVERRIDE_PATH", override)
    pb.reload_thresholds()
    assert pb.active_thresholds()["dps"] == pb._DEFAULT_THRESHOLDS["dps"]


def test_partial_override_keeps_defaults_for_other_axes(tmp_path, monkeypatch):
    override = tmp_path / "calibration_thresholds.json"
    override.write_text(json.dumps({"dps": [10, 20, 30, 40, 50]}))
    monkeypatch.setattr(pb, "_OVERRIDE_PATH", override)
    pb.reload_thresholds()
    active = pb.active_thresholds()
    assert list(active["dps"]) == [10, 20, 30, 40, 50]
    assert active["healing"] == pb._DEFAULT_THRESHOLDS["healing"]


def test_load_thresholds_returns_independent_copy(tmp_path):
    loaded = pb.load_thresholds(path=tmp_path / "absent.json")
    loaded["dps"] = [0, 0, 0, 0, 0]
    assert pb._DEFAULT_THRESHOLDS["dps"] != [0, 0, 0, 0, 0]


# ---------------------------------------------------------------------------
# MEDIUM regression: type-garbage override must not poison scoring.
# ---------------------------------------------------------------------------

def test_type_garbage_override_falls_back_per_axis(tmp_path, monkeypatch):
    # Structurally valid (length 5) but contains null + str -> would make
    # bucket_axis raise TypeError on '>=' if installed verbatim.
    override = tmp_path / "calibration_thresholds.json"
    override.write_text(json.dumps({
        "dps": [100, 200, None, 400, "x"],          # bad -> drop, keep default
        "healing": [1, 2, 3, 4, 5],                  # good -> apply
    }))
    monkeypatch.setattr(pb, "_OVERRIDE_PATH", override)
    pb.reload_thresholds()
    active = pb.active_thresholds()
    # Bad axis fell back to its built-in default...
    assert active["dps"] == pb._DEFAULT_THRESHOLDS["dps"]
    # ...the good axis was applied...
    assert list(active["healing"]) == [1, 2, 3, 4, 5]
    # ...and the central scoring call does NOT crash.
    assert pb.bucket_axis(3000, "dps") is not None  # 3000 clears the default solid floor


def test_nan_and_inf_override_rejected(tmp_path, monkeypatch):
    override = tmp_path / "calibration_thresholds.json"
    # Python's json emits NaN/Infinity tokens by default; ensure they're rejected.
    override.write_text('{"dps": [1, 2, NaN, 4, Infinity]}')
    monkeypatch.setattr(pb, "_OVERRIDE_PATH", override)
    pb.reload_thresholds()
    assert pb.active_thresholds()["dps"] == pb._DEFAULT_THRESHOLDS["dps"]


# ---------------------------------------------------------------------------
# HIGH regression: live read path auto-applies a fresh override without restart.
# ---------------------------------------------------------------------------

def test_active_thresholds_autorefreshes_on_external_write(tmp_path, monkeypatch):
    override = tmp_path / "calibration_thresholds.json"
    monkeypatch.setattr(pb, "_OVERRIDE_PATH", override)
    pb.reload_thresholds()  # primes cache with defaults (file absent)
    assert pb.active_thresholds()["dps"] == pb._DEFAULT_THRESHOLDS["dps"]

    # Simulate the GUI writing the override — WITHOUT calling reload here.
    override.write_text(json.dumps({"dps": [11, 22, 33, 44, 55]}))

    # The live read path must pick it up on its next call (mtime auto-refresh).
    assert list(pb.active_thresholds()["dps"]) == [11, 22, 33, 44, 55]
    assert pb.bucket_axis(60, "dps") == "legendary"   # >= p95(55)


def test_dual_module_copies_both_see_override(tmp_path, monkeypatch):
    """The exact trap main.py creates: performance_buckets loaded as two distinct
    module objects. A reload on ONE copy must not be required for the OTHER copy's
    live reads to reflect a freshly-written override."""
    override = tmp_path / "calibration_thresholds.json"

    # Load a second, independent copy of the module from the same source file.
    src = Path(pb.__file__)
    spec = importlib.util.spec_from_file_location("pb_second_copy", src)
    pb2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pb2)

    # Point both copies at the same override file and prime their caches.
    monkeypatch.setattr(pb, "_OVERRIDE_PATH", override)
    pb2._OVERRIDE_PATH = override
    pb.reload_thresholds()
    pb2.reload_thresholds()
    assert pb.active_thresholds()["dps"] == pb._DEFAULT_THRESHOLDS["dps"]
    assert pb2.active_thresholds()["dps"] == pb2._DEFAULT_THRESHOLDS["dps"]

    # Write the override and reload ONLY copy A (as the GUI would).
    override.write_text(json.dumps({"dps": [9, 19, 29, 39, 49]}))
    pb.reload_thresholds()

    # Copy A reflects it (it reloaded)...
    assert list(pb.active_thresholds()["dps"]) == [9, 19, 29, 39, 49]
    # ...and copy B reflects it too, WITHOUT a reload, via the mtime check.
    assert list(pb2.active_thresholds()["dps"]) == [9, 19, 29, 39, 49]
