# GUI Threshold Recalibration — "Calibrate to Your Guild"

**Status:** approved design, ready for implementation
**Date:** 2026-06-29

## Goal

Let a SparkyBot operator recalibrate the per-player performance thresholds
(`_PERFORMANCE_THRESHOLDS` in `core/performance_buckets.py`) from their own
guild's fights, entirely through the Settings GUI — no CLI, no manual file
edits, no redeploy. Today this is a dev-only CLI (`tools/recalc_thresholds.py`)
that prints a dict to paste by hand.

## Background (current state)

- `core/performance_buckets.py` hardcodes `_PERFORMANCE_THRESHOLDS`: a dict of
  `axis -> (p25, p50, p75, p90, p95)`, used by `bucket_axis()` to tier each
  player stat as solid/strong/dominant/exceptional/legendary.
- `tools/recalc_thresholds.py` (NOT in the repo — dev-only on the NAS) pools
  every player-fight observation per axis from a `corpus.jsonl` of
  `{"summary": <fight_summary>}` lines, scales (`per_sec`/`per_min`/`absolute`),
  and computes the percentiles. Its `AXES` table is the source of truth for
  which `top_*` array and field feeds each axis, and the scaling.
- `core/gw2ei_invoker.py` already runs `.evtc`/`.zevtc` → Elite Insights → JSON,
  and the live pipeline already turns that JSON into the fight summary
  (the same `get_ai_summary` shape with `top_damage`, `top_healers`, etc.).
- Settings UI is a PyQt6 `QTabWidget` in `core/gui_settings.py`.

## Components

### 1. Runtime-loadable thresholds — `core/performance_buckets.py`
- Keep the current hardcoded dict, renamed to `_DEFAULT_THRESHOLDS`.
- On import (or first use), if `<app_dir>/calibration_thresholds.json` exists and
  parses, load it as the active thresholds; otherwise use `_DEFAULT_THRESHOLDS`.
- Provide `load_thresholds()`, `active_thresholds()`, and a way to reload after
  the GUI writes the override (so a recalibrate takes effect without restart, or
  at minimum on next launch — restart-to-apply is acceptable, document which).
- The override file schema = exactly the dict shape `{axis: [p25,p50,p75,p90,p95]}`.

### 2. Calibration module — `core/calibration.py` (new)
- Port the pooling + percentile logic from `tools/recalc_thresholds.py` into the
  shipped app. Reuse its `AXES` table verbatim (axis, top-array, field, scaling).
- `compute_thresholds(summaries) -> (thresholds: dict, obs_counts: dict)`:
  pure function over a list of fight summaries. `obs_counts[axis]` = number of
  pooled observations (for soft warnings). Honor the `min_duration` skip (30s)
  and the `<5 obs => axis omitted` rule the CLI already uses.
- This module must have NO PyQt import — pure, unit-testable.

### 3. Corpus store — `<app_dir>/calibration_corpus.jsonl`
- One `{"summary": <summary>}` per line.
- **Auto-accumulate:** after each live fight is analyzed, append its summary.
  (Hook the existing analyze path; the summary already exists there — no EI re-run.)
- **Manual import:** given user-selected `.evtc`/`.zevtc` files, run each through
  the existing `gw2ei_invoker` → EI → summary pipeline and append. Reuse live code.
- Provide a `corpus_count()` and an append helper. Keep it simple; no dedup
  required for v1 (note it as a known limitation).

### 4. GUI panel — new "Calibration" section in `core/gui_settings.py`
- Shows **"N fights collected"** (from `corpus_count()`).
- **Import logs…** button → file dialog (`.evtc;*.zevtc`) → runs them through EI
  with a **progress bar / status** (EI runs per file, can be slow) → appends to
  corpus, updates the count.
- **Recalibrate** button → `compute_thresholds(corpus)` → shows a **side-by-side
  old-vs-new table** (axis | current | proposed, per tier), with a **soft
  "thin data" warning** on any axis below a confidence floor (e.g. < ~30 obs) —
  warn, never block. **Confirm** writes `calibration_thresholds.json`; **Deny**
  discards.
- **Reset to Defaults** button → delete `calibration_thresholds.json`, revert to
  built-in `_DEFAULT_THRESHOLDS`.
- **No backups / no history** of prior threshold sets are kept.

## Data flow
fights (live OR imported→EI) → summaries → `calibration_corpus.jsonl` → `compute_thresholds()` → side-by-side preview → Confirm → `calibration_thresholds.json` → loaded by `performance_buckets` at startup.

## Testing (TDD)
Pure-logic units first:
- `compute_thresholds`: percentile correctness, per_sec/per_min/absolute scaling,
  `<5 obs` axis omission, `min_duration` skip, obs_counts.
- `performance_buckets` loader: override present → uses override; absent/corrupt
  → falls back to defaults; `bucket_axis` reflects the active set.
- corpus append/count round-trips.
GUI wiring kept thin; not unit-tested beyond smoke.

## Constraints / decisions (locked)
- Corpus source: BOTH auto-accumulate + manual import.
- Apply: side-by-side preview → confirm/deny. No backups. Reset-to-defaults button.
- Quality guard: **soft warning only** (never block recalibration).
- Manual import accepts **raw `.evtc`/`.zevtc`** (run through EI), not pre-parsed JSON.
- All new runtime files (`calibration_corpus.jsonl`, `calibration_thresholds.json`)
  live in the app dir and are gitignored (runtime data, like `config.properties`).

## Out of scope (v1)
- Corpus dedup / cap management.
- Per-axis manual threshold editing.
- Sharing/exporting calibrations between guilds.

## Appendix — source logic to port (from the dev-only `tools/recalc_thresholds.py`)

Percentile helper:
```python
def _percentiles(vals, ps=(0.25, 0.50, 0.75, 0.90, 0.95)):
    vs = sorted(v for v in vals if v is not None and v > 0)
    n = len(vs)
    if n < 5:
        return None
    return tuple(vs[int(n * p)] for p in ps), n
```

AXES table — `(axis_name, corpus_top_array, field, scaling)`; scaling is
`per_sec` (raw/duration), `per_min` (raw/duration*60), or `absolute`. A field
prefixed `+` means sum the `+`-joined keys (e.g. `+hard_cc+interrupts`):
```python
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
```
Default `min_duration` skip = 30s. Note: the `bucket_axis()` consumer in
`performance_buckets.py` keys thresholds by the SHORT axis names it already uses
(`dps`, `healing`, `strips`, `cc`, `burst`, `downs_dealt`, `kills`, `stab_uptime`,
`damage_taken`, `downed_damage`, `downed_healing`, `resurrects`, `*_gen`) — the
implementer must map the calibration AXES names to the consumer's keys (verify
against the existing `_DEFAULT_THRESHOLDS` keys, don't assume).
