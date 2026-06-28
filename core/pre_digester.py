"""Deterministic fight summary bucketer for SparkyBot.

Translates raw Elite Insights (EI) metrics into qualitative categories
for the creative LLM prompt. Adapted to use real FightReport keys.

v1.7.0 — M7 integration: appends a `players` key with per-player
performance buckets (calibrated against the corpus) and an inferred
build role like "rez druid" / "condi scourge" / "boon firebrand".

v3 — adds corpus-calibrated squad-bucket functions and a new
bucket_v3() entry point that returns the FIGHT BUCKETS shape. The
legacy bucket() function is kept for the v2 prompt path.
"""
from __future__ import annotations

from performance_buckets import bucket_player, infer_build


# ---------------------------------------------------------------------------
# v3 calibrated bucket functions
#
# All thresholds derived from a calibration corpus of recorded fights.
# ---------------------------------------------------------------------------

# Class taxonomy for comp_signal computation. Elite-spec names map to a
# coarse role: "support" classes generate boons / heal / strip; "dps"
# classes are damage-dealers. "hybrid" classes (Scourge, Harbinger) lean
# offensive in WvW so we count them as dps.
_SUPPORT_CLASSES = frozenset({
    "Firebrand", "Druid", "Luminary", "Troubadour", "Amalgam",
    "Tempest", "Specter", "Renegade", "Chronomancer", "Mechanist",
    "Herald",
})
_DPS_CLASSES = frozenset({
    "Reaper", "Scourge", "Harbinger",
    "Berserker", "Spellbreaker", "Bladesworn",
    "Evoker", "Weaver", "Catalyst",
    "Dragonhunter", "Willbender",
    "Scrapper", "Holosmith",
    "Daredevil", "Deadeye",
    "Soulbeast",
    "Mirage", "Virtuoso",
    "Vindicator",
})


def squad_stomp_discipline(squad_kills: int, squad_downs: int,
                           duration_s: int) -> str | None:
    """Calibrated stomp discipline bucket. Returns None below noise floor."""
    if squad_downs < 5:
        return None
    capped = min(squad_kills / squad_downs, 1.0)
    raw = squad_kills / squad_downs
    if duration_s < 240:
        if capped >= 0.95: return "ELITE"
        if capped >= 0.65: return "SOLID"
        return "POOR"
    # Long fight (>=4m): allow FARMING tier and tighten SOLID floor.
    if raw >= 1.50: return "FARMING"
    if capped >= 0.95: return "ELITE"
    if capped >= 0.70: return "SOLID"
    return "POOR"


def squad_support_quality(squad_healing: int, squad_damage_taken: int) -> str | None:
    """Calibrated heal/damage_taken bucket."""
    if squad_damage_taken <= 0:
        return None
    r = squad_healing / squad_damage_taken
    if r >= 0.85: return "ELITE"
    if r >= 0.45: return "SOLID"
    return "SCRAMBLING"


def squad_tag_discipline(distance_list: list) -> str | None:
    """Calibrated median-distance-from-commander bucket.

    distance_list: [{'name', 'distance'}, ...]; commander has distance 0
    and is excluded from the median computation.
    """
    nonzero = [d.get("distance", 0) for d in distance_list
               if isinstance(d, dict) and d.get("distance", 0) > 0]
    if len(nonzero) < 5:
        return None
    median = sorted(nonzero)[len(nonzero) // 2]
    if median < 600:  return "TIGHT"
    if median < 2000: return "LOOSE"
    return "SCATTERED"


def squad_strip_volume(squad_strips: int, duration_s: int) -> str:
    """Volume of boon-stripping happening — NOT a quality grade.

    High volume correlates with enemy boon pressure, not squad excellence.
    Decisive Losses have HIGHER strip rates than Decisive Wins in the corpus.
    """
    pm = squad_strips / max(duration_s / 60.0, 0.1)
    if pm < 40:  return "LIGHT"
    if pm < 200: return "STEADY"
    if pm < 300: return "HEAVY"
    return "EXTREME"


def squad_cleanse_volume(squad_cleanses: int, duration_s: int) -> str:
    """Volume of condition cleansing — NOT a quality grade.

    Same caveat as squad_strip_volume: HIGH means heavy incoming condi
    pressure, often a brawl or losing fight.
    """
    pm = squad_cleanses / max(duration_s / 60.0, 0.1)
    if pm < 175: return "LIGHT"
    if pm < 650: return "STEADY"
    if pm < 900: return "HEAVY"
    return "EXTREME"


def _comp_signal_from_class_counts(class_counts: dict[str, int],
                                   support_threshold: float = 0.40,
                                   dps_threshold: float = 0.20) -> str:
    """Return BALANCED / HEAVY_DPS / HEAVY_SUPPORT / UNKNOWN.

    Thresholds are configurable because our_comp_signal samples from a
    role-biased subset (top_damage / top_strips etc., which over-weight
    DPS roles by construction) while their_comp_signal sees the full
    enemy roster.
    """
    total = sum(class_counts.values())
    if total < 4:
        return "UNKNOWN"
    support = sum(c for cls, c in class_counts.items()
                  if cls in _SUPPORT_CLASSES)
    dps = sum(c for cls, c in class_counts.items()
              if cls in _DPS_CLASSES)
    classified = support + dps
    if classified < total * 0.5:
        return "UNKNOWN"
    support_share = support / max(classified, 1)
    if support_share >= support_threshold: return "HEAVY_SUPPORT"
    if support_share <= dps_threshold: return "HEAVY_DPS"
    return "BALANCED"


def our_comp_signal(summary: dict) -> str:
    """Approximate squad comp from top-performer professions.

    Caveat: only top performers across role-specific arrays are visible.
    Sampling top_healers + top_cleanses is biased toward support
    representation by construction, so the HEAVY_SUPPORT threshold is
    raised (corpus shows ~50%+ natural support share under the bias).
    """
    classes: dict[str, int] = {}
    seen: set[str] = set()
    for key in ("top_damage", "top_healers", "top_cleanses", "top_strips",
                "top_bursts", "top_cc"):
        for entry in summary.get(key, []) or []:
            name = entry.get("name")
            prof = entry.get("profession")
            if not name or not prof or name in seen:
                continue
            seen.add(name)
            classes[prof] = classes.get(prof, 0) + 1
    # Bias-corrected thresholds for our_comp:
    #   HEAVY_SUPPORT requires >=60% support share (vs. 40% baseline)
    #   HEAVY_DPS requires <=15% support share (vs. 20% baseline)
    return _comp_signal_from_class_counts(
        classes, support_threshold=0.60, dps_threshold=0.15,
    )


def their_comp_signal(summary: dict) -> str:
    """Enemy comp signal from enemy_breakdown class counts."""
    breakdown = summary.get("enemy_breakdown", {}) or {}
    counts = {cls: info.get("count", 0) if isinstance(info, dict) else 0
              for cls, info in breakdown.items()}
    return _comp_signal_from_class_counts(counts)


def numbers_context(friendly: int, enemy: int) -> str:
    """Symmetric numbers enum, squad-perspective explicit in the name."""
    if friendly <= 0 or enemy <= 0:
        return "EVEN_NUMBERS"
    ratio = friendly / enemy
    if ratio >= 1.30: return "WE_OUTNUMBERED_THEM_HARD"
    if ratio >= 1.15: return "WE_OUTNUMBERED_THEM_SOFT"
    if ratio >= 0.85: return "EVEN_NUMBERS"
    if ratio >= 0.70: return "THEY_OUTNUMBERED_US_SOFT"
    return "THEY_OUTNUMBERED_US_HARD"


def fight_duration_bucket(duration_s: int) -> str:
    """Coarse duration enum used by the FARMING gate and narrative templates."""
    if duration_s < 90:  return "BLITZ"
    if duration_s < 240: return "SHORT"   # under 4 minutes
    if duration_s < 480: return "STANDARD"
    return "LONG"


def outcome_shape(summary: dict) -> str:
    """Outcome enum — flat plain-English buckets per spec.

    Order matters: stomp checks fire before decisive checks because a
    stomp is the more specific descriptor.
    """
    duration_s = summary.get("duration_seconds", 0) or 0
    sq_n = summary.get("squad_count", 0) or 0
    sq_deaths = summary.get("squad_deaths", 0) or 0
    en_n = summary.get("enemy_count", 0) or 0
    en_deaths = summary.get("enemy_deaths", 0) or 0
    outcome = (summary.get("outcome", "") or "").lower()

    # COLLAPSE — one side folded fast (under 30s of real engagement).
    # Use duration + lopsided deaths as a proxy.
    if duration_s and duration_s < 30 and (sq_deaths == 0 or en_deaths == 0):
        return "COLLAPSE"

    sq_loss_pct = sq_deaths / max(sq_n, 1)
    en_loss_pct = en_deaths / max(en_n, 1)

    # Stomps: lopsided wipe with the winning side losing <20%.
    if "win" in outcome and sq_loss_pct < 0.20 and en_loss_pct >= 0.40:
        return "WE_STOMPED_THEM"
    if "loss" in outcome and en_loss_pct < 0.20 and sq_loss_pct >= 0.40:
        return "WE_GOT_STOMPED"

    # Decisive: 60%+ loss vs <30% kill on the losing side.
    if "loss" in outcome and sq_loss_pct >= 0.60 and en_loss_pct < 0.30:
        return "DECISIVE_LOSS"
    if "win" in outcome and en_loss_pct >= 0.60 and sq_loss_pct < 0.30:
        return "DECISIVE_WIN"

    # EXECUTION: clean win without big resistance (we won, low casualties).
    if "win" in outcome and sq_loss_pct < 0.15:
        return "EXECUTION"

    # GRIND: long fight, no clean outcome.
    if duration_s >= 480:
        return "GRIND"

    return "BRAWL"


def bucket_v3(summary: dict) -> dict[str, object]:
    """v3 FIGHT BUCKETS shape — corpus-calibrated, name-prefixed, no ambiguity.

    Returns a dict matching the FIGHT BUCKETS section. None values mean the bucket
    fell below its noise floor and should be omitted from the rendered
    prompt block (caller's responsibility).
    """
    duration_s = summary.get("duration_seconds", 0) or 0
    return {
        "outcome_shape":          outcome_shape(summary),
        "numbers_context":        numbers_context(
            summary.get("friendly_count", 0) or 0,
            summary.get("enemy_count", 0) or 0,
        ),
        "fight_duration":         fight_duration_bucket(duration_s),
        "squad_stomp_discipline": squad_stomp_discipline(
            summary.get("squad_kills", 0) or 0,
            summary.get("squad_downs", 0) or 0,
            duration_s,
        ),
        "squad_support_quality":  squad_support_quality(
            summary.get("squad_healing", 0) or 0,
            summary.get("squad_damage_taken")
                or summary.get("enemy_total_damage", 0) or 0,
        ),
        "squad_tag_discipline":   squad_tag_discipline(
            summary.get("squad_tag_distance", []) or [],
        ),
        "squad_strip_volume":     squad_strip_volume(
            summary.get("squad_strips", 0) or 0, duration_s,
        ),
        "squad_cleanse_volume":   squad_cleanse_volume(
            summary.get("squad_cleanses", 0) or 0, duration_s,
        ),
        "our_comp_signal":        our_comp_signal(summary),
        "their_comp_signal":      their_comp_signal(summary),
        "zone":                   summary.get("zone", "Unknown"),
        "duration":               summary.get("duration", ""),
        "numbers": (
            f"{summary.get('friendly_count', 0)} vs "
            f"{summary.get('enemy_count', 0)}"
        ),
    }


# ---------------------------------------------------------------------------
# Legacy v2 buckets (kept for backward compat with the v2 prompt path)
# ---------------------------------------------------------------------------


# Top-N array names that contain individual player records — used to merge
# per-player observations across categories into a single record per player.
_TOP_ARRAYS = (
    'top_damage', 'top_healers', 'top_cleanses', 'top_strips',
    'top_bursts', 'top_cc', 'top_stability', 'top_downed_damage',
    'top_downed_healing', 'top_dying', 'top_resurrects', 'top_damage_taken',
    'top_might_gen', 'top_quickness_gen', 'top_alacrity_gen',
    'top_protection_gen', 'top_stability_gen',
)


def _merge_player_records(summary: dict) -> dict[str, dict]:
    """Walk every top_X array, merge each player's appearances into one record."""
    merged: dict[str, dict] = {}
    for arr_name in _TOP_ARRAYS:
        for entry in summary.get(arr_name, []):
            name = entry.get('name')
            if not name:
                continue
            if name not in merged:
                merged[name] = {'name': name, 'profession': entry.get('profession', 'Unknown')}
            for k, v in entry.items():
                if k == 'name':
                    continue
                if k == 'profession' and not v:
                    continue
                merged[name][k] = v
    return merged


def _emit_player_buckets(summary: dict) -> list[dict]:
    """Compute per-player {build role, highlight buckets} for the prompt."""
    duration_s = summary.get('duration_seconds', 0) or 0
    if duration_s <= 0:
        return []
    out = []
    for rec in _merge_player_records(summary).values():
        buckets = bucket_player(rec, duration_s)
        if not buckets:
            continue
        condi_share = rec.get('condi_share', 0.0)
        role = infer_build(rec['profession'], condi_share, buckets)
        highlights = [f"{axis}:{tier}" for axis, tier in buckets.items()]
        out.append({
            'name': rec['name'],
            'role': role,
            'highlights': highlights,
        })
    out.sort(key=lambda r: (-len(r['highlights']), r['name']))
    # Cap at 7 players to keep the prompt focused — the model otherwise
    # tries to namecheck everyone and blows the length budget.
    return out[:7]


def bucket(summary: dict) -> dict[str, object]:
    """Bucket real FightReport summary metrics into qualitative categories."""
    buckets: dict[str, object] = {}
    
    duration_s = summary.get("duration_seconds", 1)
    mins = max(0.1, duration_s / 60.0)
    
    # 1. strip_ratio & cleanse_rate (Derived from totals)
    strips_raw = summary.get("squad_strips", 0)
    strips_pm = strips_raw / mins
    if strips_pm == 0: buckets["strip_ratio"] = "NONE"
    elif strips_pm < 5: buckets["strip_ratio"] = "LIGHT"
    elif strips_pm < 15: buckets["strip_ratio"] = "MODERATE"
    elif strips_pm < 30: buckets["strip_ratio"] = "HEAVY"
    else: buckets["strip_ratio"] = "EXTREME"

    cleanses_raw = summary.get("squad_cleanses", 0)
    cleanses_pm = cleanses_raw / mins
    if cleanses_pm == 0: buckets["cleanse_rate"] = "NONE"
    elif cleanses_pm < 10: buckets["cleanse_rate"] = "LIGHT"
    elif cleanses_pm < 30: buckets["cleanse_rate"] = "MODERATE"
    elif cleanses_pm < 60: buckets["cleanse_rate"] = "HEAVY"
    else: buckets["cleanse_rate"] = "EXTREME"

    # 2. stomp_discipline (Rally %)
    # Proxy: squad_downs_received (how many of us went down) vs enemy_deaths (how many they actually killed)
    downs = summary.get("squad_downs_received", 0)
    deaths = summary.get("squad_deaths", 0)
    rally = (downs - deaths) / max(1, downs) if downs > deaths else 0.0
    
    if rally < 0.1: buckets["stomp_discipline"] = "ELITE"
    elif rally < 0.25: buckets["stomp_discipline"] = "OK"
    elif rally < 0.5: buckets["stomp_discipline"] = "SLOPPY"
    else: buckets["stomp_discipline"] = "LOL"

    # 3. fight_duration
    if duration_s < 90: buckets["fight_duration"] = "BLITZ"
    elif duration_s < 180: buckets["fight_duration"] = "SHORT"
    elif duration_s < 480: buckets["fight_duration"] = "STANDARD"
    else: buckets["fight_duration"] = "SLOG"

    # 4. numbers_context (Squad vs Enemy)
    squad = summary.get("squad_count", 1)
    enemy = summary.get("enemy_count", 1)
    ratio = squad / enemy if enemy > 0 else 2.0
    if ratio < 0.6: buckets["numbers_context"] = "OUTNUMBERED_HARD"
    elif ratio < 0.85: buckets["numbers_context"] = "OUTNUMBERED"
    elif ratio < 1.15: buckets["numbers_context"] = "EVEN"
    elif ratio < 1.6: buckets["numbers_context"] = "FAVORED"
    else: buckets["numbers_context"] = "DOMINANT"

    # 5. support_quality (% enemy dmg outhealed)
    healing = summary.get("squad_healing", 0)
    enemy_dmg = summary.get("enemy_total_damage", 1)
    outheal = healing / enemy_dmg if enemy_dmg > 0 else 0.0
    
    if outheal < 0.2: buckets["support_quality"] = "WEAK"
    elif outheal < 0.5: buckets["support_quality"] = "SOLID"
    else: buckets["support_quality"] = "EXCEPTIONAL"

    # 6. outcome_shape (Outcome + Intensity)
    kdr = summary.get("kdr", 1.0)
    outcome = summary.get("outcome", "win").lower()
    
    if "win" in outcome:
        if kdr > 3.0: buckets["outcome_shape"] = "EXECUTION"
        elif duration_s > 300: buckets["outcome_shape"] = "GRIND"
        else: buckets["outcome_shape"] = "BLOWOUT"
    elif "loss" in outcome:
        if kdr < 0.3: buckets["outcome_shape"] = "COLLAPSE"
        else: buckets["outcome_shape"] = "NAILBITER"
    else:
        buckets["outcome_shape"] = "GRIND"

    # 7. comp_archetype (Derived from top performer professions)
    comp = []
    classes = {}
    for key in ("top_damage", "top_cleanses", "top_strips", "top_healers"):
        for p in summary.get(key, []):
            prof = p.get("profession")
            if prof:
                classes[prof] = classes.get(prof, 0) + 1
                
    if classes.get("Firebrand", 0) >= 2: comp.append("boon_ball")
    if classes.get("Dragonhunter", 0) >= 1: comp.append("dh_trap")
    if classes.get("Berserker", 0) >= 1: comp.append("berserker_glass")
    if classes.get("Scourge", 0) >= 2: comp.append("scourge_bomb")
    buckets["comp_archetype"] = comp

    # 8. M7: per-player performance buckets + inferred build roles
    buckets["players"] = _emit_player_buckets(summary)

    return buckets
