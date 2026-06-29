"""Calibration math + corpus store for "Calibrate to Your Guild".

Pools per-player observations from a list of fight summaries (the same shape
get_ai_summary() emits), scales each axis, and computes percentile thresholds
that drop straight into performance_buckets' active threshold dict.

This module is PURE: no PyQt, no Elite Insights, no disk side effects beyond
the small corpus/threshold file helpers (which take explicit paths). It is the
shipped-app port of the dev-only tools/recalc_thresholds.py.

AXES is the source of truth for which top_* array and field feeds each axis and
how it scales. The axis_name column matches the keys in
performance_buckets._DEFAULT_THRESHOLDS one-to-one (verified by
test_axes_names_match_default_threshold_keys), so compute_thresholds output can
be written as the override file with no key remapping.

Scaling:
    per_sec   -> raw / duration_seconds
    per_min   -> raw / duration_seconds * 60
    absolute  -> raw (already a rate or an intrinsic count)

A field prefixed with '+' sums the '+'-joined keys, e.g. '+hard_cc+interrupts'.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Iterable

# (axis_name, corpus_top_array, field, scaling)
AXES = [
    ('dps',              'top_damage',         'damage',              'per_sec'),
    ('healing',          'top_healers',        'healing',             'per_sec'),
    ('cleanses_pm',      'top_cleanses',       'cleanses',            'per_min'),
    ('strips_pm',        'top_strips',         'strips',              'per_min'),
    ('cc_pm',            'top_cc',             '+hard_cc+interrupts', 'per_min'),
    ('burst_4s',         'top_bursts',         'dmg_4s',              'absolute'),
    ('downs_dealt_pm',   'top_damage',         'downs',               'per_min'),
    ('kills_pm',         'top_damage',         'kills',               'per_min'),
    ('stability_uptime', 'top_stability',      'stab_uptime',         'absolute'),
    ('downed_damage',    'top_downed_damage',  'downed_damage',       'absolute'),
    ('downed_healing',   'top_downed_healing', 'downed_healing',      'absolute'),
    ('resurrects',       'top_resurrects',     'resurrects',          'absolute'),
    ('damage_taken',     'top_damage_taken',   'damage_taken',        'absolute'),
    ('might_gen',        'top_might_gen',      'might_gen',           'absolute'),
    ('quickness_gen',    'top_quickness_gen',  'quickness_gen',       'absolute'),
    ('alacrity_gen',     'top_alacrity_gen',   'alacrity_gen',        'absolute'),
    ('protection_gen',   'top_protection_gen', 'protection_gen',      'absolute'),
    ('stability_gen',    'top_stability_gen',  'stability_gen',       'absolute'),
]

DEFAULT_MIN_DURATION = 30  # seconds; short fights skew rates and are skipped
_PCTS = (0.25, 0.50, 0.75, 0.90, 0.95)

# Default runtime corpus / override paths (app dir, gitignored).
# WITHOUT .resolve() to match config.home_dir (Path(__file__).parent.parent),
# so the auto-accumulate hook and the GUI agree on the same file even under a
# symlinked install.
_APP_DIR = Path(__file__).parent.parent
CORPUS_PATH = _APP_DIR / "calibration_corpus.jsonl"
THRESHOLDS_PATH = _APP_DIR / "calibration_thresholds.json"


def _percentiles(vals, ps=_PCTS):
    """Return (percentile_tuple, n) over strictly-positive values, or None if
    fewer than 5 usable observations. Ported verbatim from recalc_thresholds.py.
    """
    vs = sorted(v for v in vals if v is not None and v > 0)
    n = len(vs)
    if n < 5:
        return None
    return tuple(vs[int(n * p)] for p in ps), n


def _extract(player: dict, field: str):
    """Pull the raw value for a field, summing '+'-joined keys when prefixed."""
    if field.startswith('+'):
        keys = [k for k in field.split('+') if k]
        return sum((player.get(k, 0) or 0) for k in keys)
    return player.get(field)


def compute_thresholds(summaries: Iterable[dict], min_duration: int = DEFAULT_MIN_DURATION):
    """Pool observations across fights and compute per-axis percentile thresholds.

    Args:
        summaries: iterable of fight summaries (get_ai_summary() shape).
        min_duration: fights shorter than this (seconds) are skipped entirely.

    Returns:
        (thresholds, obs_counts) where
          thresholds[axis] = [p25, p50, p75, p90, p95]  (axes with >=5 obs only)
          obs_counts[axis] = number of pooled strictly-positive observations
                             (present even for omitted thin-data axes, so the
                             GUI can surface a soft warning).
    """
    pools: dict[str, list] = {axis: [] for axis, _, _, _ in AXES}

    for s in summaries:
        try:
            dur = float(s.get('duration_seconds') or 0)
        except (TypeError, ValueError):
            dur = 0
        if dur < min_duration:
            continue
        for axis, arr_key, field, scaling in AXES:
            for player in (s.get(arr_key) or []):
                if not isinstance(player, dict):
                    continue
                raw = _extract(player, field)
                if raw is None:
                    continue
                if scaling == 'per_sec':
                    val = raw / dur
                elif scaling == 'per_min':
                    val = raw / dur * 60
                else:  # absolute
                    val = raw
                pools[axis].append(val)

    thresholds: dict[str, list] = {}
    obs_counts: dict[str, int] = {}
    for axis, vals in pools.items():
        result = _percentiles(vals)
        if result is None:
            # Axis omitted from thresholds, but still report how thin it was.
            obs_counts[axis] = len([v for v in vals if v is not None and v > 0])
            continue
        pct, n = result
        thresholds[axis] = list(pct)
        obs_counts[axis] = n
    return thresholds, obs_counts


# ---------------------------------------------------------------------------
# Corpus store — one {"summary": <summary>} JSON object per line.
#
# There are genuinely concurrent writers (the watcher spawns a thread per
# detected log, and the GUI import runs on its own daemon thread), so a
# process-wide lock serializes corpus access. The lock makes appends — and the
# count/load reads that may run alongside them — atomic with respect to one
# another, preventing interleaved/partial lines.
# ---------------------------------------------------------------------------
_CORPUS_LOCK = threading.Lock()


def append_summary(summary: dict, path: Path = CORPUS_PATH) -> None:
    """Append a single fight summary to the corpus (best-effort, creates dirs)."""
    path = Path(path)
    line = json.dumps({"summary": summary}, ensure_ascii=False) + "\n"
    with _CORPUS_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(line)


def corpus_count(path: Path = CORPUS_PATH) -> int:
    """Number of fights collected in the corpus (0 if the file is absent)."""
    path = Path(path)
    with _CORPUS_LOCK:
        if not path.exists():
            return 0
        count = 0
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    count += 1
        return count


def load_corpus(path: Path = CORPUS_PATH) -> list[dict]:
    """Load every stored fight summary. Corrupt lines are skipped, not fatal."""
    path = Path(path)
    with _CORPUS_LOCK:
        if not path.exists():
            return []
        summaries: list[dict] = []
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                summary = obj.get("summary") if isinstance(obj, dict) else None
                if isinstance(summary, dict):
                    summaries.append(summary)
        return summaries


def write_thresholds(thresholds: dict, path: Path = THRESHOLDS_PATH) -> None:
    """Persist a threshold override dict ({axis: [p25..p95]}) as JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(thresholds, indent=2, ensure_ascii=False), encoding='utf-8')
