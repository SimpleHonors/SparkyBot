"""M7 — Calibrated performance buckets + build-role inference.

Translates a player's raw activity numbers into qualitative tier labels
("solid" / "strong" / "dominant" / "exceptional" / "legendary") using
percentile thresholds derived empirically from a corpus of historical
fights (see tools/batch_calibrate.py + analysis script).

NOTE (2026-05-09): the 'dominant' label was previously named 'carried'.
The rename was for clarity (the old name read as either "this player
carried the team" or "this player got carried" depending on the reader).
The corpus JSONL on disk may still contain the old label until next
regeneration.

Also infers a likely build role from the combination of {profession,
condi_share, active_buckets} — turning class plus behavior into tags
like "rez druid", "condi scourge", "power reaper", "heal evoker",
"boon firebrand".

The pre_digester (M1) calls bucket_player() and infer_build() for each
player in get_ai_summary's top_X arrays, exposing the results to the v2
system prompt so Sparky can reference *behavior tags* instead of numbers.

Recalibration: run tools/recalc_thresholds.py against an updated corpus
to regenerate _PERFORMANCE_THRESHOLDS.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Calibrated thresholds — derived from 802-fight corpus 2026-05-09.
# Each axis: (p25_solid, p50_strong, p75_dominant, p90_exceptional, p95_legendary)
# Pool = top-N performers per fight (top-5 for DPS, top-3 for the rest).
# ---------------------------------------------------------------------------
_PERFORMANCE_THRESHOLDS: dict[str, tuple[float, float, float, float, float]] = {
    # Computed from 802-fight corpus (2026-02-06 .. 2026-05-08), 766 fights
    # used after short-fight (<30s) filter. Pool obs counts vary per axis.
    'dps':              (1225,    2003,    3121,    4552,    5509),     # n=3811
    'healing':          (736,     1348,    2098,    2949,    3479),     # n=2282
    'cleanses_pm':      (27.6,    52.2,    79.3,    113,     134),      # n=2280
    'strips_pm':        (12.7,    26.1,    42.5,    58.3,    67.6),     # n=2219
    'cc_pm':            (2.857,   4.444,   6.585,   8.889,   10.5),     # n=2258
    'burst_4s':         (18231,   29703,   40717,   52083,   60964),    # n=2297
    'downs_dealt_pm':   (0.594,   0.976,   1.784,   2.830,   3.717),    # n=2567
    'kills_pm':         (0.531,   0.876,   1.500,   2.353,   3.077),    # n=2486
    'stability_uptime': (3.849,   4.968,   6.222,   7.624,   8.442),    # n=2293
    'downed_damage':    (19892,   44666,   81897,   131318,  174927),   # n=2155
    'downed_healing':   (10776,   28414,   55445,   98768,   134935),   # n=1567
    'resurrects':       (1,       1,       2,       4,       5),        # n=1408
    'damage_taken':     (83160,   158955,  242628,  374724,  492465),   # n=2292
    'might_gen':        (0.419,   0.590,   0.817,   1.085,   1.282),    # n=2280
    'quickness_gen':    (0.825,   1.497,   2.756,   4.613,   6.066),    # n=2098
    'alacrity_gen':     (0.077,   0.152,   0.330,   0.953,   1.403),    # n=901
    'protection_gen':   (3.576,   4.829,   6.484,   8.882,   11.4),     # n=2273
    'stability_gen':    (0.210,   0.275,   0.365,   0.472,   0.546),    # n=2279
}

_BUCKET_LABELS = ('solid', 'strong', 'dominant', 'exceptional', 'legendary')


def bucket_axis(value: float, axis: str) -> str | None:
    """Return tier label for `value` on `axis`, or None if below the solid floor."""
    thresholds = _PERFORMANCE_THRESHOLDS.get(axis)
    if thresholds is None or value is None:
        return None
    label = None
    for thr, name in zip(thresholds, _BUCKET_LABELS):
        if value >= thr:
            label = name
        else:
            break
    return label


# ---------------------------------------------------------------------------
# Per-player bucket emission
# ---------------------------------------------------------------------------

def bucket_player(player: dict, duration_s: float) -> dict:
    """Compute bucket labels for every axis applicable to one player record.

    Returns:
        {axis_name: tier_label}  (only axes where player cleared 'solid')

    `player` is one entry from a top_X array (or a synthesized merged record).
    `duration_s` is the fight's duration_seconds, used to compute per-second
    or per-minute rates from absolute counts.
    """
    if duration_s <= 0:
        return {}
    out: dict = {}

    # Raw rate computations from fields that may or may not be present
    if 'damage' in player and player['damage'] > 0:
        b = bucket_axis(player['damage'] / duration_s, 'dps')
        if b: out['dps'] = b
    if 'healing' in player and player['healing'] > 0:
        b = bucket_axis(player['healing'] / duration_s, 'healing')
        if b: out['healing'] = b
    if 'cleanses' in player and player['cleanses'] > 0:
        b = bucket_axis(player['cleanses'] / duration_s * 60, 'cleanses_pm')
        if b: out['cleanses'] = b
    if 'strips' in player and player['strips'] > 0:
        b = bucket_axis(player['strips'] / duration_s * 60, 'strips_pm')
        if b: out['strips'] = b
    if ('hard_cc' in player or 'interrupts' in player):
        v = (player.get('hard_cc', 0) + player.get('interrupts', 0))
        if v > 0:
            b = bucket_axis(v / duration_s * 60, 'cc_pm')
            if b: out['cc'] = b
    if 'dmg_4s' in player and player['dmg_4s'] > 0:
        b = bucket_axis(player['dmg_4s'], 'burst_4s')
        if b: out['burst'] = b
    if 'downs' in player and player['downs'] > 0:
        b = bucket_axis(player['downs'] / duration_s * 60, 'downs_dealt_pm')
        if b: out['downs_dealt'] = b
    if 'kills' in player and player['kills'] > 0:
        b = bucket_axis(player['kills'] / duration_s * 60, 'kills_pm')
        if b: out['kills'] = b
    if 'stab_uptime' in player and player['stab_uptime'] > 0:
        b = bucket_axis(player['stab_uptime'], 'stability_uptime')
        if b: out['stab_uptime'] = b
    if 'downed_damage' in player and player['downed_damage'] > 0:
        b = bucket_axis(player['downed_damage'], 'downed_damage')
        if b: out['downed_damage'] = b
    if 'downed_healing' in player and player['downed_healing'] > 0:
        b = bucket_axis(player['downed_healing'], 'downed_healing')
        if b: out['downed_healing'] = b
    if 'resurrects' in player and player['resurrects'] > 0:
        b = bucket_axis(player['resurrects'], 'resurrects')
        if b: out['resurrects'] = b
    if 'damage_taken' in player and player['damage_taken'] > 0:
        b = bucket_axis(player['damage_taken'], 'damage_taken')
        if b: out['damage_taken'] = b
    for boon in ('might_gen', 'quickness_gen', 'alacrity_gen',
                 'protection_gen', 'stability_gen'):
        if boon in player and player[boon] > 0:
            b = bucket_axis(player[boon], boon)
            if b: out[boon] = b
    return out


# ---------------------------------------------------------------------------
# Build inference
# ---------------------------------------------------------------------------

def _has(buckets: dict, axis: str, min_tier: str = 'strong') -> bool:
    """True if player's bucket for axis is at min_tier or higher."""
    tiers = list(_BUCKET_LABELS)
    label = buckets.get(axis)
    if label is None:
        return False
    return tiers.index(label) >= tiers.index(min_tier)


def is_clutch(buckets: dict) -> bool:
    """Player flag: standout impact in downed-state moments.

    Distinct from the tier scale — additive marker. Fires when a player
    achieved a 'dominant' tier or above on either downed_damage or
    downed_healing. These are the players who finished the kill or
    saved the rez when it mattered.
    """
    return _has(buckets, 'downed_damage', 'dominant') or \
           _has(buckets, 'downed_healing', 'dominant')


def infer_build(profession: str, condi_share: float, buckets: dict) -> str:
    """Infer a build tag from class + condi share + active behavior buckets.

    Returns a string like "rez druid" / "condi scourge" / "heal evoker" /
    "boon firebrand" / "power reaper" / "{class}" (fallback for unclassified).

    Rules favor specificity: heal-focused tags before pure DPS, build-defining
    boons before generic "support". Order matters within a class block.
    """
    p = (profession or '').strip()

    # ---- Druid ----
    if p == 'Druid':
        if _has(buckets, 'downed_healing', 'strong') or _has(buckets, 'resurrects', 'strong'):
            return 'rez druid'
        if _has(buckets, 'healing', 'strong') and _has(buckets, 'cleanses', 'strong'):
            return 'cleanse druid'
        if _has(buckets, 'healing', 'strong'):
            return 'heal druid'
        if _has(buckets, 'might_gen', 'strong'):
            return 'boon druid'
        if _has(buckets, 'dps', 'strong'):
            return 'condi druid' if condi_share > 0.5 else 'power druid'
        return 'druid'

    # ---- Scourge / Necro family — strips and condi flavors ----
    if p == 'Scourge':
        if _has(buckets, 'strips', 'dominant'):
            return 'strip scourge'
        if condi_share > 0.6 and _has(buckets, 'dps', 'solid'):
            return 'condi scourge'
        if condi_share < 0.4 and _has(buckets, 'dps', 'solid'):
            return 'power scourge'
        if _has(buckets, 'healing', 'strong'):
            return 'heal scourge'
        return 'scourge'

    if p == 'Reaper':
        if _has(buckets, 'cc', 'exceptional'):
            return 'fear reaper'
        if _has(buckets, 'strips', 'exceptional'):
            return 'strip reaper'
        if _has(buckets, 'dps', 'dominant'):
            return 'power reaper' if condi_share < 0.4 else 'condi reaper'
        return 'reaper'

    if p in ('Necromancer', 'Harbinger'):
        if _has(buckets, 'quickness_gen', 'strong'):
            return f'quick {p.lower()}'
        if _has(buckets, 'strips', 'dominant'):
            return f'strip {p.lower()}'
        if _has(buckets, 'dps', 'strong'):
            return f'condi {p.lower()}' if condi_share > 0.5 else f'power {p.lower()}'
        return p.lower()

    # ---- Evoker (newer spec, multi-role) ----
    if p == 'Evoker':
        if _has(buckets, 'healing', 'strong'):
            return 'heal evoker'
        if _has(buckets, 'cleanses', 'dominant'):
            return 'support evoker'
        if _has(buckets, 'dps', 'dominant') and _has(buckets, 'burst', 'strong'):
            return 'burst evoker'
        if _has(buckets, 'dps', 'strong'):
            return 'dps evoker'
        return 'evoker'

    # ---- Firebrand — boon support primary ----
    if p == 'Firebrand':
        if _has(buckets, 'stability_gen', 'strong'):
            return 'stab firebrand'
        if _has(buckets, 'might_gen', 'strong') and _has(buckets, 'quickness_gen', 'strong'):
            return 'boon firebrand'
        if _has(buckets, 'healing', 'strong'):
            return 'heal firebrand'
        if _has(buckets, 'dps', 'dominant'):
            return 'condi firebrand' if condi_share > 0.5 else 'power firebrand'
        return 'firebrand'

    # ---- Dragonhunter — usually DPS ----
    if p == 'Dragonhunter':
        if _has(buckets, 'cc', 'dominant'):
            return 'trap dh'
        if _has(buckets, 'dps', 'dominant'):
            return 'power dh' if condi_share < 0.4 else 'condi dh'
        return 'dragonhunter'

    # ---- Tempest — heal or aura ----
    if p == 'Tempest':
        if _has(buckets, 'healing', 'strong'):
            return 'heal tempest'
        if _has(buckets, 'cleanses', 'strong'):
            return 'cleanse tempest'
        return 'tempest'

    # ---- Troubadour (newer spec, primary support) ----
    if p == 'Troubadour':
        if _has(buckets, 'protection_gen', 'strong') and _has(buckets, 'might_gen', 'strong'):
            return 'boon troubadour'
        if _has(buckets, 'healing', 'strong'):
            return 'heal troubadour'
        return 'troubadour'

    # ---- Luminary (newer spec) — boons + heals ----
    if p == 'Luminary':
        if _has(buckets, 'protection_gen', 'strong'):
            return 'prot luminary'
        if _has(buckets, 'healing', 'strong'):
            return 'heal luminary'
        if _has(buckets, 'might_gen', 'strong'):
            return 'might luminary'
        return 'luminary'

    # ---- Spellbreaker — strips + cc ----
    if p == 'Spellbreaker':
        if _has(buckets, 'strips', 'dominant'):
            return 'strip spellbreaker'
        if _has(buckets, 'cc', 'dominant'):
            return 'cc spellbreaker'
        return 'spellbreaker'

    # ---- Untamed / Soulbeast — DPS rangers ----
    if p in ('Untamed', 'Soulbeast'):
        if _has(buckets, 'dps', 'dominant'):
            return f'condi {p.lower()}' if condi_share > 0.5 else f'power {p.lower()}'
        return p.lower()

    # ---- Catalyst / Berserker / Amalgam / Conduit / Ritualist / Virtuoso etc ----
    # Generic DPS classifier
    if _has(buckets, 'dps', 'dominant'):
        flavor = 'condi' if condi_share > 0.5 else 'power'
        return f'{flavor} {p.lower()}'
    if _has(buckets, 'healing', 'strong'):
        return f'heal {p.lower()}'
    if _has(buckets, 'might_gen', 'strong') or _has(buckets, 'quickness_gen', 'strong'):
        return f'boon {p.lower()}'
    return p.lower()
