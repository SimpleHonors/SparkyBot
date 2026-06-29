"""Unit tests for core/calibration.py — the pure pooling + percentile logic
that powers GUI threshold recalibration, plus the corpus append/count store.

Pure-logic only: no PyQt, no Elite Insights. Summaries are hand-built dicts in
the same shape get_ai_summary() emits (top_* arrays + duration_seconds).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core.calibration import (
    compute_thresholds,
    append_summary,
    corpus_count,
    load_corpus,
    write_thresholds,
    AXES,
)


def _fight(duration_seconds=100, **arrays):
    """Build a minimal fight summary with the given top_* arrays."""
    s = {"duration_seconds": duration_seconds}
    s.update(arrays)
    return s


# ---------------------------------------------------------------------------
# Percentile correctness (absolute scaling, no division)
# ---------------------------------------------------------------------------

def test_percentile_correctness_absolute():
    # damage_taken is 'absolute' scaling: value passes through untouched.
    # Pool 1..20 across one fight; ps indices = int(20*p) for
    # (.25,.5,.75,.9,.95) -> 5,10,15,18,19 -> sorted[idx] = 6,11,16,19,20.
    fight = _fight(top_damage_taken=[{"damage_taken": v} for v in range(1, 21)])
    thresholds, obs = compute_thresholds([fight])
    assert thresholds["damage_taken"] == [6, 11, 16, 19, 20]
    assert obs["damage_taken"] == 20


# ---------------------------------------------------------------------------
# Scaling: per_sec / per_min
# ---------------------------------------------------------------------------

def test_per_sec_scaling_dps():
    # dps from top_damage 'damage', per_sec = damage / duration.
    # duration=100, damage 100..500 -> per_sec 1..5. n=5,
    # indices int(5*p)=1,2,3,4,4 -> [2,3,4,5,5].
    fight = _fight(
        duration_seconds=100,
        top_damage=[{"damage": d} for d in (100, 200, 300, 400, 500)],
    )
    thresholds, obs = compute_thresholds([fight])
    assert thresholds["dps"] == [2, 3, 4, 5, 5]
    assert obs["dps"] == 5


def test_per_min_scaling_cleanses():
    # cleanses_pm from top_cleanses 'cleanses', per_min = cleanses/dur*60.
    # duration=60 makes per_min == raw cleanses. 1..5 -> [2,3,4,5,5].
    fight = _fight(
        duration_seconds=60,
        top_cleanses=[{"cleanses": c} for c in (1, 2, 3, 4, 5)],
    )
    thresholds, _ = compute_thresholds([fight])
    assert thresholds["cleanses_pm"] == [2, 3, 4, 5, 5]


def test_summed_field_cc_pm():
    # cc_pm field is '+hard_cc+interrupts' -> sum the two keys, then per_min.
    # duration=60 -> per_min == raw sum. sums 1..5 -> [2,3,4,5,5].
    fight = _fight(
        duration_seconds=60,
        top_cc=[
            {"hard_cc": 1, "interrupts": 0},
            {"hard_cc": 1, "interrupts": 1},
            {"hard_cc": 2, "interrupts": 1},
            {"hard_cc": 2, "interrupts": 2},
            {"hard_cc": 3, "interrupts": 2},
        ],
    )
    thresholds, _ = compute_thresholds([fight])
    assert thresholds["cc_pm"] == [2, 3, 4, 5, 5]


# ---------------------------------------------------------------------------
# <5 obs => axis omitted, but obs_counts still records the thin count
# ---------------------------------------------------------------------------

def test_axis_with_fewer_than_five_obs_is_omitted():
    fight = _fight(top_resurrects=[{"resurrects": v} for v in (1, 2, 3, 4)])
    thresholds, obs = compute_thresholds([fight])
    assert "resurrects" not in thresholds
    assert obs["resurrects"] == 4  # surfaced for the thin-data soft warning


def test_zero_and_none_values_filtered():
    # Only strictly-positive observations count toward the pool.
    fight = _fight(
        top_damage_taken=[{"damage_taken": v} for v in (0, 0, None, 10, 20, 30, 40, 50)]
    )
    thresholds, obs = compute_thresholds([fight])
    assert obs["damage_taken"] == 5  # the five positives only
    assert thresholds["damage_taken"][0] == 20  # int(5*.25)=1 -> sorted[1]=20


# ---------------------------------------------------------------------------
# min_duration skip
# ---------------------------------------------------------------------------

def test_min_duration_skip():
    short = _fight(duration_seconds=10, top_damage_taken=[{"damage_taken": 9999}])
    long_fights = [
        _fight(duration_seconds=100, top_damage_taken=[{"damage_taken": v}])
        for v in (10, 20, 30, 40, 50)
    ]
    thresholds, obs = compute_thresholds([short] + long_fights)
    # The 9999 from the <30s fight must not pollute the pool.
    assert obs["damage_taken"] == 5
    assert 9999 not in thresholds["damage_taken"]


def test_custom_min_duration():
    f = _fight(duration_seconds=45, top_damage_taken=[{"damage_taken": v} for v in range(1, 6)])
    # default 30s keeps it; a 60s floor drops it.
    keep, _ = compute_thresholds([f])
    assert "damage_taken" in keep
    drop, obs = compute_thresholds([f], min_duration=60)
    assert "damage_taken" not in drop
    assert obs.get("damage_taken", 0) == 0


# ---------------------------------------------------------------------------
# AXES integrity — names match the consumer threshold dict keys
# ---------------------------------------------------------------------------

def test_axes_names_match_default_threshold_keys():
    from core.performance_buckets import _DEFAULT_THRESHOLDS
    axis_names = {a[0] for a in AXES}
    assert axis_names == set(_DEFAULT_THRESHOLDS.keys())


# ---------------------------------------------------------------------------
# Corpus append / count / load round-trips
# ---------------------------------------------------------------------------

def test_corpus_append_count_roundtrip(tmp_path):
    corpus = tmp_path / "calibration_corpus.jsonl"
    assert corpus_count(corpus) == 0
    append_summary({"duration_seconds": 100, "top_damage": []}, corpus)
    append_summary({"duration_seconds": 50, "top_damage": [{"name": "TestPlayer"}]}, corpus)
    assert corpus_count(corpus) == 2
    loaded = load_corpus(corpus)
    assert len(loaded) == 2
    assert loaded[1]["top_damage"][0]["name"] == "TestPlayer"


def test_corpus_count_missing_file(tmp_path):
    assert corpus_count(tmp_path / "nope.jsonl") == 0


def test_compute_from_loaded_corpus(tmp_path):
    corpus = tmp_path / "calibration_corpus.jsonl"
    for v in (10, 20, 30, 40, 50):
        append_summary(_fight(top_damage_taken=[{"damage_taken": v}]), corpus)
    thresholds, obs = compute_thresholds(load_corpus(corpus))
    assert obs["damage_taken"] == 5
    assert thresholds["damage_taken"][0] == 20


def test_write_thresholds_roundtrip(tmp_path):
    out = tmp_path / "calibration_thresholds.json"
    payload = {"dps": [1, 2, 3, 4, 5]}
    write_thresholds(payload, out)
    import json
    assert json.loads(out.read_text()) == payload


def test_concurrent_appends_do_not_corrupt_corpus(tmp_path):
    # Multiple writers (watcher thread-per-fight + GUI import thread) append to
    # the same file; the lock must keep every line intact (no interleaving).
    import threading
    corpus = tmp_path / "calibration_corpus.jsonl"
    n_threads, per_thread = 8, 25
    # Large-ish summaries so a write could exceed the OS buffer if unlocked.
    big = [{"name": "TestPlayer", "damage": 123456, "blob": "x" * 2000} for _ in range(20)]

    def worker(tid):
        for j in range(per_thread):
            append_summary(_fight(top_damage=list(big), tag=f"{tid}-{j}"), corpus)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert corpus_count(corpus) == n_threads * per_thread
    # Every line must still parse — no corruption from interleaved writes.
    assert len(load_corpus(corpus)) == n_threads * per_thread


def test_corpus_and_override_paths_consistent_across_modules(tmp_path):
    # Finding #6: the auto-accumulate path (calibration) and the threshold
    # override path (performance_buckets) must resolve to the same app dir, and
    # neither may diverge via an inconsistent .resolve().
    import core.calibration as cal
    from core import performance_buckets as pb
    assert cal._APP_DIR == pb._OVERRIDE_PATH.parent
    assert cal.CORPUS_PATH.parent == pb._OVERRIDE_PATH.parent
