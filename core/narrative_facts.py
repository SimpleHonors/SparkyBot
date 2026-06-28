"""v3 NARRATIVE FACTS builder.

Replaces the v2 prompt's raw FIGHT DATA JSON dump with 5-8 declarative,
factual sentences. Sentences are pre-curated from bucketed data, with
player and topic cooldowns ALREADY APPLIED at sentence-build time, so
the LLM cannot route around them by leaking names from a JSON blob.

Caller responsibilities:
    - On LLM success: call cooldown.record_topic(t) for each topic in
      result['topic_emits'], cooldown.record_global(name) for each
      player in result['player_emits'], plus the existing per-axis
      record() calls if the caller still uses those.
    - On every fight (success or fail): call cooldown.tick() at end,
      cooldown.save() for persistence.
"""
from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from performance_buckets import bucket_player, infer_build, is_clutch
from pre_digester import bucket_v3

if TYPE_CHECKING:
    from callout_cooldown import CalloutCooldown

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Top-N arrays that contain individual player records — used to merge
# per-player observations across categories into a single record per player.
_TOP_ARRAYS = (
    'top_damage', 'top_healers', 'top_cleanses', 'top_strips',
    'top_bursts', 'top_cc', 'top_stability', 'top_downed_damage',
    'top_downed_healing', 'top_dying', 'top_resurrects', 'top_damage_taken',
    'top_might_gen', 'top_quickness_gen', 'top_alacrity_gen',
    'top_protection_gen', 'top_stability_gen',
)

# Axis → human-readable phrase used in template 5.
_AXIS_LABEL = {
    'dps':            'damage',
    'healing':        'healing',
    'cleanses':       'cleansing',
    'strips':         'boon-stripping',
    'cc':             'control',
    'burst':          'burst damage',
    'downs_dealt':    'downs dealt',
    'kills':          'kills',
    'stab_uptime':    'stability uptime',
    'damage_taken':   'damage absorbed',
    'might_gen':      'might generation',
    'quickness_gen':  'quickness generation',
    'alacrity_gen':   'alacrity generation',
    'protection_gen': 'protection generation',
    'stability_gen':  'stability generation',
}


def _merge_player_records(summary: dict) -> dict[str, dict]:
    """Walk every top_X array, merge each player's appearances into one record."""
    merged: dict[str, dict] = {}
    for arr_name in _TOP_ARRAYS:
        for entry in summary.get(arr_name, []) or []:
            name = entry.get('name')
            if not name:
                continue
            if name not in merged:
                merged[name] = {
                    'name': name,
                    'profession': entry.get('profession', 'Unknown'),
                }
            for k, v in entry.items():
                if k == 'name':
                    continue
                if k == 'profession' and not v:
                    continue
                merged[name][k] = v
    return merged


def _format_duration(summary: dict) -> str:
    """Prefer the pre-formatted string, fall back to seconds."""
    s = summary.get('duration', '') or ''
    if s:
        return s
    secs = summary.get('duration_seconds', 0) or 0
    if secs:
        return f"{secs // 60}m {secs % 60:02d}s"
    return "?"


def _short_zone(zone: str) -> str:
    """Compact zone name for terse prose."""
    if not zone:
        return "?"
    # "Blue Alpine Borderlands" -> "Blue BL"
    if 'Borderlands' in zone:
        parts = zone.split()
        if parts:
            return f"{parts[0]} BL"
    return zone


def _numbers_phrase(friendly: int, enemy: int) -> str:
    """Plain-English numbers context including the count."""
    if not friendly or not enemy:
        return f"{friendly} vs {enemy}"
    ratio = friendly / enemy
    if ratio >= 1.30:
        return f"outnumbering them {friendly}-vs-{enemy}"
    if ratio >= 1.15:
        return f"slightly more numerous ({friendly} vs {enemy})"
    if ratio >= 0.85:
        return f"even ({friendly} vs {enemy})"
    if ratio >= 0.70:
        return f"slightly outnumbered ({friendly} vs {enemy})"
    return f"outnumbered {friendly}-vs-{enemy}"


def _result_verb(outcome: str, deaths: int, count: int) -> str:
    """Pick a verb for the outcome sentence based on outcome + casualties."""
    o = (outcome or "").lower()
    if "win" in o:
        if count and deaths / max(count, 1) < 0.10:
            return "took"  # "Squad took 2 of 48 deaths in..."  → clean win
        return "won"
    if "loss" in o:
        return "lost"
    if "draw" in o:
        return "drew"
    return "took"


def _role_phrase(player_record: dict, build_role: str) -> str:
    """How to refer to a player when their name is on cooldown.

    Uses the inferred build role (e.g. 'burst evoker', 'rez druid') with
    'the' or 'our' depending on phrasing. Single-class deduplication
    intentionally not done here — a known limitation (anonymization is
    "soft" when the role is unique).
    """
    role = (build_role or "").strip()
    if not role:
        prof = (player_record.get('profession') or '').strip()
        return f"the {prof.lower()}" if prof else "a squad member"
    return f"the {role}"


def _select_dominant_axis(buckets: dict) -> Optional[str]:
    """Pick the most impressive axis a player has (legendary > exceptional > dominant)."""
    tier_rank = {'legendary': 4, 'exceptional': 3, 'dominant': 2,
                 'strong': 1, 'solid': 0}
    best_axis = None
    best_rank = -1
    # Prefer impact axes (dps, healing, strips, cc) over generic ones
    for axis in ('dps', 'healing', 'strips', 'cc', 'cleanses', 'burst',
                 'downs_dealt', 'kills', 'stab_uptime'):
        tier = buckets.get(axis)
        if tier is None:
            continue
        rank = tier_rank.get(tier, -1)
        if rank >= 2 and rank > best_rank:  # only dominant+
            best_axis = axis
            best_rank = rank
    return best_axis


def _format_primary_stat(player_record: dict, axis: str,
                         duration_s: int) -> str:
    """Render a numeric primary-stat phrase for the dominant sentence."""
    if axis == 'dps' and 'damage' in player_record and duration_s:
        dps = player_record['damage'] / duration_s
        return f"{dps:,.0f} dps"
    if axis == 'healing' and 'healing' in player_record:
        return f"{player_record['healing']:,} healing"
    if axis == 'strips' and 'strips' in player_record:
        return f"{player_record['strips']} strips"
    if axis == 'cc' and ('hard_cc' in player_record or 'interrupts' in player_record):
        cc = player_record.get('hard_cc', 0) + player_record.get('interrupts', 0)
        return f"{cc} hard CC"
    if axis == 'burst' and 'dmg_4s' in player_record:
        k = player_record['dmg_4s'] / 1000
        return f"{k:.0f}k 4-second burst"
    if axis == 'cleanses' and 'cleanses' in player_record:
        return f"{player_record['cleanses']} cleanses"
    if axis == 'downs_dealt' and 'downs' in player_record:
        return f"{player_record['downs']} downs"
    if axis == 'kills' and 'kills' in player_record:
        return f"{player_record['kills']} kills"
    if axis == 'stab_uptime' and 'stab_uptime' in player_record:
        return f"{player_record['stab_uptime']:.1f}s stability uptime"
    return axis


# ---------------------------------------------------------------------------
# Sentence builders — each returns Optional[str] or skips
# ---------------------------------------------------------------------------

def _sentence_outcome(summary: dict) -> Optional[str]:
    """Template 1 — always fires."""
    sq_n = summary.get('squad_count', 0) or 0
    sq_deaths = summary.get('squad_deaths', 0) or 0
    duration = _format_duration(summary)
    zone = _short_zone(summary.get('zone', ''))
    friendly = summary.get('friendly_count', 0) or 0
    enemy = summary.get('enemy_count', 0) or 0
    outcome = summary.get('outcome', '') or ''
    verb = _result_verb(outcome, sq_deaths, sq_n)
    nums = _numbers_phrase(friendly, enemy)

    o = outcome.lower()
    if "win" in o and sq_deaths == 0:
        return f"Squad won the fight on {zone} in {duration}, {nums}, with no deaths."
    if "win" in o:
        return (f"Squad won the fight on {zone} in {duration}, {nums}, "
                f"losing {sq_deaths} of {sq_n}.")
    if "loss" in o:
        return (f"Squad lost {sq_deaths} of {sq_n} in {duration} on {zone}, "
                f"{nums}.")
    if "draw" in o:
        return f"Squad drew on {zone} after {duration}, {nums}."
    return f"Fight on {zone} ended after {duration}, {nums}."


def _sentence_stomp(summary: dict, bucket: Optional[str]) -> Optional[str]:
    """Templates 2a/2b/2c — varies by bucket."""
    if bucket is None or bucket == "SOLID":
        return None
    downs = summary.get('squad_downs', 0) or 0
    kills = summary.get('squad_kills', 0) or 0
    if bucket == "POOR":
        # rally rate only meaningful when kills <= downs
        rallied = max(downs - kills, 0)
        rally_pct = int(round(rallied / max(downs, 1) * 100))
        return (f"Stomp discipline was POOR: {downs} downs but only "
                f"{kills} kills ({rally_pct}% rally rate).")
    if bucket == "ELITE":
        # When kills > downs, squad cleaned up pre-existing downs — phrase
        # accordingly so the prose isn't mathematically nonsense.
        if kills > downs:
            extra = kills - downs
            return (f"Stomp discipline was ELITE: {downs} of {downs} downs "
                    f"converted plus {extra} extra clean-up kills.")
        return (f"Stomp discipline was ELITE: {kills} of {downs} downs "
                f"converted to kills.")
    if bucket == "FARMING":
        duration = _format_duration(summary)
        return (f"Squad farmed spawn returners: {kills} kills on "
                f"{downs} downs over {duration}.")
    return None


def _sentence_tag(summary: dict, bucket: Optional[str]) -> Optional[str]:
    """Template 3 — only fires on LOOSE / SCATTERED."""
    if bucket not in ("LOOSE", "SCATTERED"):
        return None
    distances = [d.get('distance', 0) for d in summary.get('squad_tag_distance', [])
                 if isinstance(d, dict) and d.get('distance', 0) > 0]
    if not distances:
        return None
    median = sorted(distances)[len(distances) // 2]
    return f"Tag was {bucket}: squad strung out {int(median)} from commander."


def _sentence_support(summary: dict, bucket: Optional[str]) -> Optional[str]:
    """Template 4 — only fires when not SOLID."""
    if bucket is None or bucket == "SOLID":
        return None
    healing = summary.get('squad_healing', 0) or 0
    dmg_taken = (summary.get('squad_damage_taken')
                 or summary.get('enemy_total_damage', 0) or 0)
    if dmg_taken == 0:
        return None
    pct = int(round(healing / dmg_taken * 100))
    if bucket == "ELITE":
        return f"Support was ELITE: healing covered {pct}% of incoming damage."
    if bucket == "SCRAMBLING":
        return f"Support was scrambling: healing only covered {pct}% of incoming damage."
    return None


def _sentence_enemy(summary: dict) -> Optional[str]:
    """Template 7 — facts only, no labels."""
    breakdown = summary.get('enemy_breakdown', {}) or {}
    if not breakdown:
        return None
    # Sort classes by count, take top 2.
    by_count = sorted(
        ((cls, info) for cls, info in breakdown.items() if isinstance(info, dict)),
        key=lambda kv: kv[1].get('count', 0),
        reverse=True,
    )
    if not by_count:
        return None
    top_skills = summary.get('top_enemy_skills', []) or []
    top_skill_phrase = ""
    if top_skills:
        s = top_skills[0]
        top_skill_phrase = f"; top damage from {s.get('name', '?')} ({s.get('damage', 0):,})"

    parts = []
    for cls, info in by_count[:2]:
        c = info.get('count', 0)
        parts.append(f"{c} {cls}")
    return f"Enemy: {', '.join(parts)}{top_skill_phrase}."


def _build_player_sentences(summary: dict,
                            cooldown: Optional["CalloutCooldown"],
                            ) -> tuple[list[str], set[str]]:
    """Templates 5 + 6 — dominant + clutch player callouts.

    Returns (sentences, names_emitted). Player cooldowns are applied:
    - Per-axis cooldown gates which axis a player can be named on
    - Global player cap blocks any further sentence about this player
    - If gated, sentence falls back to role phrase (anonymous credit)
    """
    duration_s = summary.get('duration_seconds', 0) or 0
    if duration_s <= 0:
        return [], set()

    sentences: list[str] = []
    names_emitted: set[str] = set()

    # Build per-player records sorted by # of dominant+ tiers (most impressive first)
    candidates = []
    for rec in _merge_player_records(summary).values():
        buckets = bucket_player(rec, duration_s)
        if not buckets:
            continue
        condi_share = rec.get('condi_share', 0.0) or 0.0
        role = infer_build(rec.get('profession', ''), condi_share, buckets)
        clutch = is_clutch(buckets)
        dom_axis = _select_dominant_axis(buckets)
        if not dom_axis and not clutch:
            continue
        candidates.append({
            'rec': rec, 'buckets': buckets, 'role': role,
            'dom_axis': dom_axis, 'clutch': clutch,
        })

    # Sort: legendary/exceptional first, then dominant, then clutch-only
    def _rank(c):
        if not c['dom_axis']:
            return -1  # clutch-only
        tier = c['buckets'].get(c['dom_axis'], 'solid')
        return {'legendary': 4, 'exceptional': 3, 'dominant': 2}.get(tier, 0)
    candidates.sort(key=_rank, reverse=True)

    # Emit dominant sentences (template 5) — max 2 per fight, no axis dupes
    dom_emits = 0
    used_axes: set[str] = set()
    for c in candidates:
        if dom_emits >= 2:
            break
        if not c['dom_axis']:
            continue
        rec = c['rec']
        name = rec['name']

        # Pick best axis NOT already taken by a previous dominant sentence.
        # Walk this player's tier-ranked axes from most impressive down.
        tier_rank = {'legendary': 4, 'exceptional': 3, 'dominant': 2}
        ranked_axes = sorted(
            ((axis, tier_rank.get(c['buckets'][axis], 0))
             for axis in c['buckets']
             if c['buckets'][axis] in tier_rank),
            key=lambda kv: kv[1], reverse=True,
        )
        chosen_axis = next(
            (axis for axis, _r in ranked_axes if axis not in used_axes),
            None,
        )
        if chosen_axis is None:
            continue
        used_axes.add(chosen_axis)

        # Decide name vs. role substitution
        on_axis_cd = cooldown.is_on_cooldown(name, chosen_axis) if cooldown else False
        on_global_cd = cooldown.is_globally_on_cooldown(name) if cooldown else False
        use_role = on_axis_cd or on_global_cd

        subject = _role_phrase(rec, c['role']) if use_role else name
        category_label = _AXIS_LABEL.get(chosen_axis, chosen_axis)
        primary_stat = _format_primary_stat(rec, chosen_axis, duration_s)
        sentences.append(
            f"{subject} was dominant in {category_label} with {primary_stat}."
        )
        if not use_role:
            names_emitted.add(name)
        dom_emits += 1

    # Emit clutch sentences (template 6) — max 1 per fight
    clutch_emitted = False
    for c in candidates:
        if clutch_emitted:
            break
        if not c['clutch']:
            continue
        rec = c['rec']
        name = rec['name']
        # Don't double-emit the same player
        if name in names_emitted:
            continue
        on_global_cd = cooldown.is_globally_on_cooldown(name) if cooldown else False
        on_axis_cd = (cooldown.is_on_cooldown(name, 'downed_damage')
                      if cooldown else False)
        use_role = on_global_cd or on_axis_cd
        subject = _role_phrase(rec, c['role']) if use_role else name
        downed_dmg = rec.get('downed_damage', 0) or 0
        downed_heal = rec.get('downed_healing', 0) or 0
        if downed_dmg >= downed_heal:
            tail = f"with {downed_dmg:,} down damage"
        else:
            tail = f"saving {downed_heal:,} healing in downed-state"
        sentences.append(f"{subject} was clutch {tail}.")
        if not use_role:
            names_emitted.add(name)
        clutch_emitted = True

    return sentences, names_emitted


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_narrative_facts(
    summary: dict,
    cooldown: Optional["CalloutCooldown"] = None,
    *,
    max_sentences: int = 8,
) -> dict:
    """Generate the v3 NARRATIVE FACTS payload for one fight.

    Returns:
        {
            'sentences':    list[str],   # rendered NARRATIVE FACTS lines
            'buckets':      dict,        # bucket_v3() output for FIGHT BUCKETS block
            'topic_emits':  set[str],    # topics that shipped (caller records)
            'player_emits': set[str],    # player names that shipped (caller records_global)
        }

    Topic / player cooldowns are CONSULTED here but not RECORDED — caller
    must record after a successful LLM response so retries don't burn cooldowns.
    """
    buckets = bucket_v3(summary)
    topic_emits: set[str] = set()
    player_emits: set[str] = set()
    sentences: list[str] = []

    # Priority order: 1 > 7 > 2 > 3 > 4 > 5 > 6 > 8
    # (Template 8 — streak callout — not yet implemented.)

    # 1: outcome (always)
    s1 = _sentence_outcome(summary)
    if s1:
        sentences.append(s1)

    # 7: enemy comp facts (always)
    s7 = _sentence_enemy(summary)
    if s7:
        sentences.append(s7)

    # 2: stomp discipline (gated by topic cooldown)
    on_cd = cooldown.is_topic_on_cooldown('stomp_discipline') if cooldown else False
    if not on_cd:
        s2 = _sentence_stomp(summary, buckets.get('squad_stomp_discipline'))
        if s2:
            sentences.append(s2)
            topic_emits.add('stomp_discipline')

    # 3: tag discipline
    on_cd = cooldown.is_topic_on_cooldown('tag_discipline') if cooldown else False
    if not on_cd:
        s3 = _sentence_tag(summary, buckets.get('squad_tag_discipline'))
        if s3:
            sentences.append(s3)
            topic_emits.add('tag_discipline')

    # 4: support quality
    on_cd = cooldown.is_topic_on_cooldown('support_quality') if cooldown else False
    if not on_cd:
        s4 = _sentence_support(summary, buckets.get('squad_support_quality'))
        if s4:
            sentences.append(s4)
            topic_emits.add('support_quality')

    # 5+6: dominant + clutch player callouts (cooldown-aware)
    p_sentences, p_emits = _build_player_sentences(summary, cooldown)
    sentences.extend(p_sentences)
    player_emits.update(p_emits)

    # Cap at max_sentences (priority already enforced by emission order above)
    if len(sentences) > max_sentences:
        sentences = sentences[:max_sentences]

    return {
        'sentences':    sentences,
        'buckets':      buckets,
        'topic_emits':  topic_emits,
        'player_emits': player_emits,
    }


def extract_roster_names(summary: dict) -> set[str]:
    """Pull every distinct player name from the fight summary.

    Used as the filter set for palette name-poisoning checks.
    """
    names: set[str] = set()
    for arr in _TOP_ARRAYS:
        for entry in summary.get(arr, []) or []:
            n = entry.get('name')
            if n:
                names.add(n)
    # Also grab the commander name if present.
    commander = (summary.get('commander') or "").strip()
    if commander:
        names.add(commander)
    return names


def filter_palette_for_name_poisoning(active_terms: dict, roster: set[str]) -> dict:
    """Strip palette terms whose text contains a current squad-member name.

    Defensive: a customized palette could accidentally name a player.
    Returns a copy of active_terms with offending entries removed.
    Logs a warning per dropped term.
    """
    if not roster:
        return active_terms
    cleaned: dict = {}
    for category, terms in active_terms.items():
        kept = []
        for t in terms or []:
            term_text = t.get('term', '') if isinstance(t, dict) else str(t)
            if any(name and name in term_text for name in roster):
                logger.warning(
                    "filter_palette: dropped term %r (matches roster name)",
                    term_text,
                )
                continue
            kept.append(t)
        cleaned[category] = kept
    return cleaned


def render_narrative_block(facts: dict) -> str:
    """Render the {sentences, buckets} dict to the user-message format."""
    lines = ["NARRATIVE FACTS (these are the ground truth — do not invent more):"]
    for s in facts['sentences']:
        lines.append(f"- {s}")
    lines.append("")
    lines.append("FIGHT BUCKETS:")
    b = facts['buckets']
    for key in ("outcome_shape", "numbers_context", "fight_duration",
                "squad_stomp_discipline", "squad_support_quality",
                "squad_tag_discipline", "squad_strip_volume",
                "squad_cleanse_volume",
                "zone", "duration", "numbers"):
        v = b.get(key)
        if v is None:
            continue
        lines.append(f"  {key:<24}: {v}")
    return "\n".join(lines)
