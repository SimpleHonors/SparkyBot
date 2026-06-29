"""Tests for the runtime-loadable threshold layer in core/performance_buckets.py.

Override present -> active thresholds reflect it (and bucket_axis follows).
Override absent or corrupt -> fall back to the built-in defaults.
"""
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core import performance_buckets as pb


@pytest.fixture(autouse=True)
def _restore_defaults():
    """Every test starts and ends on the built-in defaults (no override file)."""
    pb.reload_thresholds(path=Path("/nonexistent/calibration_thresholds.json"))
    yield
    pb.reload_thresholds(path=Path("/nonexistent/calibration_thresholds.json"))


def test_defaults_active_when_no_override():
    active = pb.active_thresholds()
    assert active["dps"] == pb._DEFAULT_THRESHOLDS["dps"]


def test_override_present_is_used(tmp_path):
    override = tmp_path / "calibration_thresholds.json"
    override.write_text(json.dumps({"dps": [10, 20, 30, 40, 50]}))
    pb.reload_thresholds(path=override)
    assert list(pb.active_thresholds()["dps"]) == [10, 20, 30, 40, 50]


def test_bucket_axis_reflects_override(tmp_path):
    override = tmp_path / "calibration_thresholds.json"
    override.write_text(json.dumps({"dps": [10, 20, 30, 40, 50]}))
    pb.reload_thresholds(path=override)
    # value 25 clears p50(20) but not p75(30) -> "strong"
    assert pb.bucket_axis(25, "dps") == "strong"
    # value 9 is below the new solid floor (10) -> None
    assert pb.bucket_axis(9, "dps") is None


def test_corrupt_override_falls_back_to_defaults(tmp_path):
    override = tmp_path / "calibration_thresholds.json"
    override.write_text("{ this is not valid json ]")
    pb.reload_thresholds(path=override)
    assert pb.active_thresholds()["dps"] == pb._DEFAULT_THRESHOLDS["dps"]


def test_partial_override_keeps_defaults_for_other_axes(tmp_path):
    # An override that only recalibrated dps must not wipe other axes — the
    # remaining axes keep their built-in defaults (merge semantics).
    override = tmp_path / "calibration_thresholds.json"
    override.write_text(json.dumps({"dps": [10, 20, 30, 40, 50]}))
    pb.reload_thresholds(path=override)
    active = pb.active_thresholds()
    assert list(active["dps"]) == [10, 20, 30, 40, 50]
    assert active["healing"] == pb._DEFAULT_THRESHOLDS["healing"]


def test_load_thresholds_returns_independent_copy(tmp_path):
    # Mutating a returned dict must not corrupt the defaults.
    loaded = pb.load_thresholds(path=Path("/nonexistent/x.json"))
    loaded["dps"] = [0, 0, 0, 0, 0]
    assert pb._DEFAULT_THRESHOLDS["dps"] != [0, 0, 0, 0, 0]
