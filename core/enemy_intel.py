"""Deterministic enemy-intelligence extraction for Sparky Pro reports.

The combiner exposes enemy profession counts per fight and world colour, but
does not expose enemy parties.  This module preserves the observed counts and
builds an explicitly inferred, role-aware party layout for presentation.  It
also extracts the session-scoped incoming-pressure evidence that the combiner
does provide.  No exact builds, weapons, or party membership are invented.
"""

from __future__ import annotations

import math
import re
from collections import Counter


_IMG_RE = re.compile(r"\[img[^\]]*?\[([^|\]]+)\|[^\]]*?\]\]")
_PIPE_LINK_RE = re.compile(r"\[\[([^\]|]*)\|([^\]]+)\]\]")
_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
_PROFESSION_COUNT_RE = re.compile(
    r"\{\{\s*([^}]+?)\s*\}\}\s*:\s*(\d+)", re.IGNORECASE
)
_COMPOSITION_CAPTION_RE = re.compile(
    r"^\|\s*Fight\s*-\s*(\d+)\s*:\s*(Red|Green|Blue)\s+Composition\s*\|c$",
    re.IGNORECASE,
)
_PIPE_SENTINEL = "\x00"


# These are intentionally broad placement families, not build claims.  They
# help distribute professions across estimated parties, but never become a
# displayed role without role-specific output evidence.
_ROLE_FAMILIES = {
    "primary_support": {
        "Chronomancer", "Firebrand", "Luminary", "Scrapper", "Troubadour",
    },
    "healer": {
        "Druid", "Tempest",
    },
    "utility_support": {
        "Catalyst", "Herald", "Paragon", "Scourge", "Spellbreaker",
    },
    "dps": {
        "Berserker", "Bladesworn", "Conduit", "Daredevil", "Deadeye",
        "Dragonhunter", "Elementalist", "Engineer", "Evoker", "Harbinger",
        "Holosmith", "Mesmer", "Mirage", "Necromancer", "Ranger", "Reaper",
        "Renegade", "Revenant", "Soulbeast", "Thief", "Untamed", "Vindicator",
        "Virtuoso", "Warrior", "Weaver", "Willbender",
    },
}
_ROLE_ORDER = ("primary_support", "healer", "utility_support", "hybrid", "dps")

# Skill ownership connects session-wide observed pressure to a profession
# archetype.
_DAMAGE_SKILL_PROFESSIONS = {
    "Soul Spiral": {"Reaper"},
    "Gravedigger": {"Reaper"},
    "Grasping Darkness": {"Reaper"},
    "Well of Suffering": {"Necromancer", "Reaper", "Scourge"},
    "Unholy Feast": {"Necromancer", "Reaper", "Scourge"},
    "Epidemic": {"Necromancer", "Scourge"},
    "Ghastly Breach": {"Scourge"},
    "Gravity Well": {"Chronomancer"},
    "Procession of Blades": {"Dragonhunter"},
    "True Shot": {"Dragonhunter"},
    "Coalescence of Ruin": {"Herald", "Renegade", "Revenant", "Vindicator"},
}
_PULL_SKILL_PROFESSIONS = {
    "Gravity Well": {"Chronomancer"},
    "Grasping Darkness": {"Reaper"},
    "Spectral Grasp": {"Necromancer", "Reaper", "Scourge"},
    "Chapter 3: Heated Rebuke": {"Firebrand"},
    "Magnetic Bomb": {"Engineer", "Holosmith", "Scrapper"},
}
_STRIP_CAPABLE_PROFESSIONS = {
    "Chronomancer", "Harbinger", "Mesmer", "Mirage", "Necromancer",
    "Reaper", "Scourge", "Spellbreaker", "Virtuoso",
}
_DAMAGING_CONDITIONS = {"Bleeding", "Burning", "Confusion", "Poison", "Torment"}


PRO_NAVIGATION = [
    {"id": "overview", "label": "Overview", "subviews": ["night-summary", "fight-timeline"]},
    {"id": "dps", "label": "DPS", "subviews": ["overview", "direct", "conditions", "skills", "pressure"]},
    {"id": "support", "label": "Support", "subviews": ["overview", "cleanses", "strips-cc", "boons", "resurrects"]},
    {"id": "healing", "label": "Healing", "subviews": ["overview", "healing-barrier", "profiles", "skills-targets"]},
    {"id": "high-scores", "label": "High Scores", "subviews": ["offense", "support", "healing", "defense"]},
    {"id": "enemy-intel", "label": "Enemy Intel", "subviews": ["overview", "composition", "damage-conditions", "control-strips", "skills-builds", "estimated-subgroups"]},
    {"id": "details", "label": "Details", "subviews": ["fights", "players", "attendance", "composition"]},
]


def _cells(line):
    line = _IMG_RE.sub(r"{\1}", line)
    line = _PIPE_LINK_RE.sub(r"[[\1" + _PIPE_SENTINEL + r"\2]]", line)
    return [cell.strip() for cell in line.split("|")]


def _plain(cell):
    cell = cell.replace(_PIPE_SENTINEL, "|")
    cell = re.sub(r"<[^>]+>", "", cell)
    cell = _LINK_RE.sub(r"\1", cell)
    cell = re.sub(r"\{\{|\}\}", "", cell)
    cell = re.sub(r"'{2,}", "", cell)
    return cell.strip()


def _number(value):
    value = _plain(value).replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    if not match:
        return None
    number = float(match.group(0))
    return int(number) if number.is_integer() else number


def _label(value):
    value = _plain(value).lstrip("!").strip()
    value = value.strip("{}").strip()
    if "}-" in value:
        value = value.split("}-", 1)[1]
    elif (duplicate := re.match(r"^(.+)-\1$", value)):
        value = duplicate.group(1)
    return value


def _find_tiddler(tiddlers, suffix):
    return next(
        (item for item in tiddlers if item.get("title", "").endswith(suffix)),
        None,
    )


def _role_for(profession):
    for role, professions in _ROLE_FAMILIES.items():
        if profession in professions:
            return role
    return "hybrid"


def _confidence(observed_count, enemy_count):
    denominator = max(enemy_count, observed_count, 1)
    coverage = min(observed_count / denominator, 1.0)
    # Even complete profession counts cannot prove party membership.
    score = round(0.3 + (0.35 * coverage), 2)
    level = "medium" if coverage >= 0.8 else "low"
    return {
        "level": level,
        "score": score,
        "basis": "profession counts observed; party placement and roles inferred",
    }


def _dps_term(damage_profile, has_damage):
    if not has_damage:
        return "DPS"
    classification = (damage_profile or {}).get("classification")
    if classification == "Power Damage":
        return "Power DPS"
    if classification == "Condition Damage":
        return "Condition DPS"
    return "DPS"


_ROLE_LEVELS = {"Estimated": 1, "Likely": 2, "Confirmed": 3}
_ROLE_PRIORITY = {
    "Healer": 0, "Support / Healing": 1, "Boon Support": 2,
    "Power DPS": 3, "Condition DPS": 3, "DPS": 3,
    "Boon Strip": 4, "Crowd Control": 5, "DPS / Support": 6,
}


def _merge_role(roles, role, level, evidence, source_scope):
    current = roles.get(role)
    item = {
        "role": role,
        "level": level,
        "evidence": [evidence],
        "source_scope": source_scope,
    }
    if current is None:
        roles[role] = item
    elif _ROLE_LEVELS[level] > _ROLE_LEVELS[current["level"]]:
        item["evidence"] = current["evidence"] + item["evidence"]
        roles[role] = item
    else:
        current["evidence"].append(evidence)


def _archetype_role(role_family):
    return {
        "healer": "Support / Healing",
        "primary_support": "Boon Support",
        "utility_support": "DPS / Support",
        "dps": "DPS",
        "hybrid": "DPS / Support",
    }[role_family]


def _infer_tactical_role(profession, observed_frequency, role_context):
    role_family = _role_for(profession)
    evidence = [f"{observed_frequency} {profession} observed in this fight/color"]
    roles = {}
    context = role_context or {}
    actor_profile = (context.get("actor_role_evidence") or {}).get("professions", {}).get(profession, {})
    build_evidence = []
    for trait in actor_profile.get("traits", []):
        detail = (
            f"Proven trait {trait.get('trait')} ({trait.get('specialization')}): "
            f"{trait.get('observed_skill')} trait proc observed in "
            f"{trait.get('actor_appearances', 1)} enemy appearance(s)"
        )
        build_evidence.append(detail)
        evidence.append(detail)
    for consumable in actor_profile.get("consumables", []):
        detail = (
            f"Observed {consumable.get('classification', 'consumable')} "
            f"{consumable.get('name')} in {consumable.get('actor_appearances', 1)} "
            "enemy appearance(s)"
        )
        build_evidence.append(detail)
        evidence.append(detail)
    for signal in actor_profile.get("roles", []):
        _merge_role(
            roles,
            signal["role"],
            signal.get("level", "Likely"),
            signal.get("evidence", "role-specific enemy skill pattern observed"),
            signal.get("source_scope", "detailed_wvw_enemy_targets_across_selected_fights"),
        )
        evidence.append(signal.get("evidence", "role-specific enemy skill pattern observed"))
    damage_rows = context.get("damage_by_profession", {}).get(profession, [])
    pull_rows = context.get("pulls_by_profession", {}).get(profession, [])

    for row in damage_rows:
        share = row.get("percent")
        evidence.append(
            f"{row['skill']}: {int(row['damage']):,} enemy damage"
            + (f" ({share:g}% of session enemy damage)" if share is not None else "")
        )
        owners = _DAMAGE_SKILL_PROFESSIONS.get(row["skill"], set())
        level = "Likely" if len(owners) == 1 else "Estimated"
        _merge_role(
            roles,
            _dps_term(context.get("damage_profile") or {}, True),
            level,
            f"{row['skill']} observed across the night"
            + (" and uniquely identifies this profession" if len(owners) == 1
               else "; several professions can produce it"),
            "session_all_opponents",
        )
    for row in pull_rows:
        evidence.append(f"{row['skill']}: {row['count']:g} connected incoming pulls")
        owners = _PULL_SKILL_PROFESSIONS.get(row["skill"], set())
        _merge_role(
            roles,
            "Crowd Control",
            "Likely" if len(owners) == 1 else "Estimated",
            f"{row['skill']} connected {row['count']:g} times across the night"
            + (" and uniquely identifies this profession" if len(owners) == 1
               else "; several professions can produce it"),
            "session_all_opponents",
        )

    strip_count = context.get("incoming_strip_count")
    has_strips = profession in _STRIP_CAPABLE_PROFESSIONS and strip_count is not None
    if has_strips:
        evidence.append(
            f"enemy team removed {strip_count:,} squad boons; source profession not attributable"
        )
        _merge_role(
            roles,
            "Boon Strip",
            "Estimated",
            f"enemy team removed {strip_count:,} boons, but the source profession is not attributable",
            "session_all_opponents",
        )

    profile = context.get("damage_profile") or {}
    if profile.get("status") in {"observed", "inferred"}:
        evidence.append(
            f"session incoming profile {profile.get('direct_percent', 0):g}% direct / "
            f"{profile.get('condition_percent', 0):g}% condition; not profession-attributed"
        )

    role_rows = sorted(
        roles.values(),
        key=lambda item: (
            -_ROLE_LEVELS[item["level"]],
            _ROLE_PRIORITY.get(item["role"], 99),
            item["role"],
        ),
    )
    if not role_rows:
        evidence.append(
            "No role-specific skill or output evidence was retained; profession alone does not prove a role"
        )
        return {
            "label": "Unknown",
            "tags": [],
            "family": {
                "primary_support": "Support",
                "healer": "Healer",
                "utility_support": "Support",
                "dps": "DPS",
                "hybrid": "Hybrid",
            }[role_family],
            "qualifier": "Unresolved",
            "confidence": "unresolved",
            "roles": [],
            "evidence": evidence,
            "build_evidence": build_evidence,
            "source_scope": "fight_color_composition_plus_session_all_opponents",
            "limitation": (
                "profession frequency is observed; no positive role evidence was available"
            ),
        }
    primary = role_rows[0]
    evidence.append(
        f"Profession-level candidate: {primary['role']} ({primary['level']}); "
        "enemy identity is not retained, so this cannot be assigned to an estimated slot"
    )
    return {
        "label": "Unknown",
        "candidate_label": primary["role"],
        "candidate_qualifier": primary["level"],
        "tags": [item["role"] for item in role_rows],
        "family": {
            "primary_support": "Support",
            "healer": "Healer",
            "utility_support": "Support",
            "dps": "DPS",
            "hybrid": "Hybrid",
        }[role_family],
        "qualifier": "Unresolved",
        "confidence": "unresolved",
        "roles": role_rows,
        "evidence": evidence,
        "build_evidence": build_evidence,
        "source_scope": "fight_color_composition_plus_session_all_opponents",
        "limitation": (
            "profession frequency and profession-level signals are observed; individual "
            "role, build, healing, strip source, and party position are not attributable"
        ),
    }


def estimate_enemy_subgroups(professions, enemy_count, role_context=None):
    """Return a deterministic role-aware five-player party estimate.

    ``professions`` is a list of observed ``{profession, count}`` rows.
    Party positions and broad roles are always labelled inferred.  Empty space
    in a partial final party is distinct from an unknown observed enemy.
    """
    observed_count = sum(max(int(row.get("count", 0)), 0) for row in professions)
    effective_size = max(int(enemy_count or 0), observed_count)
    if effective_size <= 0:
        return [], 0, _confidence(0, 0)

    party_count = math.ceil(effective_size / 5)
    parties = [[] for _ in range(party_count)]
    role_counts = [Counter() for _ in range(party_count)]
    profession_counts = [Counter() for _ in range(party_count)]

    rows = []
    for row in professions:
        profession = str(row.get("profession") or "Unidentified").strip() or "Unidentified"
        count = max(int(row.get("count", 0)), 0)
        family = _role_for(profession)
        rows.append((
            profession,
            count,
            family,
            _infer_tactical_role(profession, count, role_context),
        ))

    for role in _ROLE_ORDER:
        role_rows = sorted(
            (row for row in rows if row[2] == role),
            key=lambda row: (-row[1], row[0].casefold()),
        )
        for profession, count, family, inference in role_rows:
            for _copy in range(count):
                candidates = [i for i, party in enumerate(parties) if len(party) < 5]
                if not candidates:
                    break
                chosen = min(
                    candidates,
                    key=lambda i: (
                        profession_counts[i][profession],
                        role_counts[i][family],
                        len(parties[i]),
                        i,
                    ),
                )
                parties[chosen].append({
                    "profession": profession,
                    "role": inference["label"],
                    "role_tags": inference["tags"],
                    "role_family": inference["family"],
                    "evidence": "inferred",
                    "role_evidence": (
                        "observed_metrics_and_profession_archetype"
                        if inference["confidence"] == "medium"
                        else "profession_archetype_with_observed_frequency"
                    ),
                    "role_inference": inference,
                })
                profession_counts[chosen][profession] += 1
                role_counts[chosen][family] += 1

    unknown_slots = max(int(enemy_count or 0) - observed_count, 0)
    for _ in range(unknown_slots):
        candidates = [i for i, party in enumerate(parties) if len(party) < 5]
        if not candidates:
            break
        chosen = min(candidates, key=lambda i: (len(parties[i]), i))
        parties[chosen].append({
            "profession": None,
            "role": "Unidentified",
            "role_tags": [],
            "role_family": "Unidentified",
            "evidence": "not_observed",
            "role_evidence": "not_observed",
            "role_inference": {
                "label": "Unidentified",
                "tags": [],
                "family": "Unidentified",
                "qualifier": "Unresolved",
                "confidence": "unresolved",
                "evidence": ["enemy identity and profession were not present in the report"],
                "source_scope": "fight_color_composition",
                "limitation": "no profession evidence",
            },
        })

    confidence = _confidence(observed_count, int(enemy_count or 0))
    result = []
    for index, members in enumerate(parties, 1):
        party_unknown = sum(1 for member in members if member["profession"] is None)
        result.append({
            "party": index,
            "members": members,
            "unknown_slots": party_unknown,
            "open_slots": 5 - len(members),
            "confidence": confidence,
            "evidence": "inferred",
        })
    return result, unknown_slots, confidence


def _composition_snapshots(tiddlers, fight_rows, role_context=None):
    tiddler = _find_tiddler(tiddlers, "-Squad-Composition")
    if not tiddler or not tiddler.get("text"):
        return []
    fight_by_index = {int(row["index"]): row for row in fight_rows}
    snapshots = []
    current = None
    for raw_line in tiddler["text"].splitlines():
        line = raw_line.strip()
        caption_match = _COMPOSITION_CAPTION_RE.match(line)
        if caption_match:
            current = {
                "index": int(caption_match.group(1)),
                "color": caption_match.group(2).casefold(),
                "counts": Counter(),
            }
            snapshots.append(current)
            continue
        if current is None or not line.startswith("|") or line.endswith(("|h", "|k", "|c", "|f")):
            continue
        for profession, count in _PROFESSION_COUNT_RE.findall(line):
            current["counts"][profession.strip()] += int(count)

    output = []
    color_key = {"red": "r", "green": "g", "blue": "b"}
    for snapshot in snapshots:
        if not snapshot["counts"]:
            continue
        fight = fight_by_index.get(snapshot["index"], {})
        rgb = fight.get("rgb") or {}
        observed_count = sum(snapshot["counts"].values())
        enemy_count = rgb.get(color_key[snapshot["color"]])
        if enemy_count is None:
            enemy_count = observed_count
        professions = [
            {"profession": profession, "count": count, "evidence": "observed"}
            for profession, count in sorted(
                snapshot["counts"].items(), key=lambda item: (-item[1], item[0].casefold())
            )
        ]
        parties, unknown_slots, confidence = estimate_enemy_subgroups(
            professions, enemy_count, role_context=role_context
        )
        output.append({
            "index": snapshot["index"],
            "time_label": fight.get("time_label"),
            "color": snapshot["color"],
            "enemy_count": enemy_count,
            "observed_profession_count": observed_count,
            "professions": professions,
            "estimated_subgroups": parties,
            "unknown_slots": unknown_slots,
            "confidence": confidence,
            "composition_evidence": "observed",
            "party_placement_evidence": "inferred",
        })
    return output


def _enemy_damage_skills(tiddlers):
    tiddler = _find_tiddler(tiddlers, "-Top-Damage-By-Skill")
    if not tiddler or not tiddler.get("text"):
        return []
    lines = tiddler["text"].splitlines()
    caption_index = next(
        (i for i, line in enumerate(lines) if line.strip() == "| Enemy Damage Output |c"),
        None,
    )
    if caption_index is None:
        return []
    header_index = next(
        (
            i for i in range(caption_index - 1, -1, -1)
            if lines[i].strip().endswith("|h") and "Skill Name" in lines[i]
        ),
        None,
    )
    if header_index is None:
        return []
    headers = [_label(cell).casefold().replace(" ", "_") for cell in _cells(lines[header_index])]
    rows = []
    for line in lines[header_index + 1:caption_index]:
        if not line.strip().startswith("|") or line.strip().endswith(("|h", "|k", "|c", "|f")):
            continue
        cells = _cells(line)
        values = {headers[i]: cells[i] for i in range(min(len(headers), len(cells))) if headers[i]}
        skill = _label(values.get("skill_name", ""))
        damage = _number(values.get("damage", ""))
        if not skill or damage is None:
            continue
        rows.append({
            "skill": skill,
            "damage": damage,
            "damage_unit": "hit_point_damage",
            "total_casts": _number(values.get("total_casts", "")),
            "connected_hits": _number(values.get("connected_hits", "")),
            "percent": _number(values.get("%_of_total", "")),
            "percent_unit": "percent_of_session_enemy_damage",
            "evidence": "observed",
            "source_scope": "session",
        })
    return sorted(rows, key=lambda row: (-row["damage"], row["skill"].casefold()))


def _squad_average_table(tiddlers, suffix):
    tiddler = _find_tiddler(tiddlers, suffix)
    if not tiddler or not tiddler.get("text"):
        return []
    lines = tiddler["text"].splitlines()
    header = next((line for line in lines if "!Party" in line and "FightTime" in line), None)
    average = next((line for line in lines if line.strip().startswith("|Squad Average Uptime")), None)
    if not header or not average:
        return []
    header_cells = _cells(header)
    average_cells = _cells(average)
    output = []
    for index in range(5, min(len(header_cells), len(average_cells))):
        label = _label(header_cells[index])
        value = _number(average_cells[index])
        if label and value is not None:
            output.append({
                "effect": label,
                "uptime_percent": float(value),
                "uptime_unit": "percent_of_squad_active_time",
                "evidence": "observed",
                "source_scope": "session",
            })
    return sorted(output, key=lambda row: (-row["uptime_percent"], row["effect"].casefold()))


def _incoming_pulls(tiddlers):
    tiddler = _find_tiddler(tiddlers, "-Pull-Skills")
    if not tiddler or not tiddler.get("text"):
        return []
    lines = tiddler["text"].splitlines()
    caption_index = next(
        (i for i, line in enumerate(lines) if line.strip() == "| Incoming Pulls |c"),
        None,
    )
    if caption_index is None:
        return []
    header_index = next(
        (i for i in range(caption_index + 1, len(lines)) if "!Player" in lines[i] and lines[i].strip().endswith("|h")),
        None,
    )
    if header_index is None:
        return []
    headers = [_label(cell) for cell in _cells(lines[header_index])]
    totals = Counter()
    for line in lines[header_index + 1:]:
        stripped = line.strip()
        if stripped == "| Outgoing Pulls |c":
            break
        if not stripped.startswith("|") or stripped.endswith(("|h", "|k", "|c", "|f")):
            continue
        cells = _cells(line)
        for index in range(4, min(len(headers), len(cells))):
            value = _number(cells[index])
            if headers[index] and value is not None:
                totals[headers[index]] += value
    return [
        {
            "skill": skill,
            "count": count,
            "evidence": "observed",
            "source_scope": "session",
            "measurement": "connected_pull_hits",
        }
        for skill, count in sorted(totals.items(), key=lambda item: (-item[1], item[0].casefold()))
        if count > 0
    ]


def _combat_seconds(fight_rows):
    total = 0.0
    for fight in fight_rows:
        if fight.get("duration_seconds") is not None:
            total += float(fight["duration_seconds"])
            continue
        duration = str(fight.get("duration") or "")
        minutes = re.search(r"(\d+)m", duration)
        seconds = re.search(r"(\d+(?:\.\d+)?)s", duration)
        milliseconds = re.search(r"(\d+)ms", duration)
        total += int(minutes.group(1)) * 60 if minutes else 0
        total += float(seconds.group(1)) if seconds else 0
        total += int(milliseconds.group(1)) / 1000 if milliseconds else 0
    return total


def _defense_pressure(tiddlers):
    """Aggregate exact incoming damage and strip totals from the Total table."""
    tiddler = _find_tiddler(tiddlers, "-Defenses-Summary")
    if not tiddler or not tiddler.get("text"):
        return {"available": False}
    lines = tiddler["text"].splitlines()
    header_index = next(
        (
            i for i, line in enumerate(lines)
            if line.strip().endswith("|h")
            and "conditionDamageTaken" in line
            and "powerDamageTaken" in line
            and "boonStrips" in line
        ),
        None,
    )
    if header_index is None:
        return {"available": False}

    headers = [_label(cell) for cell in _cells(lines[header_index])]
    indexes = {
        name: next((i for i, value in enumerate(headers) if value == name), None)
        for name in (
            "conditionDamageTaken", "powerDamageTaken", "boonStrips",
            "receivedCrowdControl",
        )
    }
    if any(index is None for index in indexes.values()):
        return {"available": False}

    totals = Counter()
    row_count = 0
    for raw_line in lines[header_index + 1:]:
        line = raw_line.strip()
        if line.endswith("|c") or line.startswith("</$reveal"):
            break
        if not line.startswith("|") or line.endswith(("|h", "|k", "|f")):
            continue
        cells = _cells(raw_line)
        if len(cells) <= max(indexes.values()):
            continue
        row_count += 1
        for name, index in indexes.items():
            totals[name] += _number(cells[index]) or 0

    return {
        "available": row_count > 0,
        "row_count": row_count,
        "condition_damage": totals["conditionDamageTaken"],
        "direct_damage": totals["powerDamageTaken"],
        "incoming_strips": totals["boonStrips"],
        "incoming_crowd_control": totals["receivedCrowdControl"],
    }


def _damage_profile(defense_pressure, damage_skills, combat_seconds):
    if defense_pressure.get("available"):
        direct = defense_pressure["direct_damage"]
        condition = defense_pressure["condition_damage"]
        total = direct + condition
        direct_percent = round((direct / total) * 100, 2) if total else 0.0
        condition_percent = round((condition / total) * 100, 2) if total else 0.0
        classification = (
            "Condition Damage" if condition_percent >= 55
            else "Power Damage" if condition_percent <= 20
            else "Mixed Damage"
        )
        return {
            "total_incoming_damage": total,
            "direct_damage": direct,
            "condition_damage": condition,
            "combat_seconds": round(combat_seconds, 3),
            "total_damage_per_second": round(total / combat_seconds, 2) if combat_seconds else None,
            "direct_damage_per_second": round(direct / combat_seconds, 2) if combat_seconds else None,
            "condition_damage_per_second": round(condition / combat_seconds, 2) if combat_seconds else None,
            "direct_percent": direct_percent,
            "condition_percent": condition_percent,
            "classification": classification,
            "status": "observed",
            "evidence": "observed",
            "damage_unit": "hit_point_damage",
            "rate_unit": "aggregate_squad_damage_per_combat_second",
            "combat_time_unit": "seconds",
            "source_scope": "session_all_opponents",
            "source": "Defenses-Summary",
            "attribution": "enemy_profession_not_attributed",
        }

    # Fallback for older stores: condition-effect rows in the enemy skill table
    # provide a partial but concrete session-wide damage mix instead of Unknown.
    sample_total = sum(row["damage"] for row in damage_skills)
    if not sample_total:
        return {
            "status": "not_observed",
            "evidence": "not_observed",
            "source_scope": "session_all_opponents",
            "source": "Top-Damage-By-Skill",
        }
    condition = sum(
        row["damage"] for row in damage_skills if row["skill"] in _DAMAGING_CONDITIONS
    )
    direct = max(sample_total - condition, 0)
    condition_percent = round((condition / sample_total) * 100, 2) if sample_total else 0.0
    direct_percent = round(100 - condition_percent, 2) if sample_total else 0.0
    return {
        "total_incoming_damage": sample_total,
        "direct_damage": direct,
        "condition_damage": condition,
        "combat_seconds": round(combat_seconds, 3),
        "total_damage_per_second": round(sample_total / combat_seconds, 2) if combat_seconds else None,
        "direct_damage_per_second": round(direct / combat_seconds, 2) if combat_seconds else None,
        "condition_damage_per_second": round(condition / combat_seconds, 2) if combat_seconds else None,
        "direct_percent": direct_percent,
        "condition_percent": condition_percent,
        "classification": (
            "Condition Damage" if condition_percent >= 55
            else "Power Damage" if condition_percent <= 20
            else "Mixed Damage"
        ),
        "status": "inferred",
        "evidence": "inferred",
        "damage_unit": "hit_point_damage",
        "rate_unit": "aggregate_squad_damage_per_combat_second",
        "combat_time_unit": "seconds",
        "source_scope": "session_all_opponents",
        "source": "Top-Damage-By-Skill sample",
        "attribution": "enemy_profession_not_attributed",
    }


def _condition_profile(conditions):
    if not conditions:
        return {
            "status": "not_observed",
            "source_scope": "session_all_opponents",
            "source": "Conditions-In",
        }
    damaging = [row for row in conditions if row["effect"] in _DAMAGING_CONDITIONS]
    total = sum(row["uptime_percent"] for row in damaging)
    normalized = [
        {
            "effect": row["effect"],
            "uptime_percent": row["uptime_percent"],
            "uptime_unit": "percent_of_squad_active_time",
            "pressure_share_percent": round((row["uptime_percent"] / total) * 100, 2) if total else 0.0,
            "evidence": "observed",
        }
        for row in damaging
    ]
    return {
        "status": "observed",
        "measurement": "normalized_squad_average_uptime",
        "damaging_condition_uptime_index": round(total, 3),
        "dominant_condition": normalized[0]["effect"] if normalized else None,
        "normalized": normalized,
        "source_scope": "session_all_opponents",
        "source": "Conditions-In",
        "attribution": "enemy_profession_not_attributed",
    }


def _incoming_strip_profile(defense_pressure, debuff_strips, snapshots, combat_seconds):
    if defense_pressure.get("available"):
        count = defense_pressure["incoming_strips"]
        return [{
            "effect": "Boon Strip",
            "count": count,
            "count_unit": "boons_removed_from_squad",
            "rate_per_combat_second": round(count / combat_seconds, 2) if combat_seconds else None,
            "rate_per_combat_minute": (
                round(count / (combat_seconds / 60), 2) if combat_seconds else None
            ),
            "rate_unit": "aggregate_squad_boon_strips_per_combat_time",
            "evidence": "observed",
            "source_scope": "session_all_opponents",
            "measurement": "boons_removed_from_squad",
            "source": "Defenses-Summary",
            "attribution": "enemy_profession_not_attributed",
        }]
    if debuff_strips:
        return debuff_strips

    capable = Counter()
    for snapshot in snapshots:
        for row in snapshot["professions"]:
            if row["profession"] in _STRIP_CAPABLE_PROFESSIONS:
                capable[row["profession"]] += row["count"]
    if not capable:
        return []
    return [{
        "effect": "Boon Strip",
        "evidence": "inferred" if capable else "not_observed",
        "source_scope": "session_all_opponents",
        "measurement": "composition_capability",
        "source": "enemy profession composition",
        "attribution": "capability_only_not_observed_usage",
        "capable_professions": [
            {"profession": profession, "snapshot_appearances": count}
            for profession, count in sorted(capable.items(), key=lambda item: (-item[1], item[0]))
        ],
    }]


def _control_profile(defense_pressure, combat_seconds):
    if not defense_pressure.get("available"):
        return []
    count = defense_pressure["incoming_crowd_control"]
    return [{
        "effect": "Incoming Crowd Control",
        "count": count,
        "count_unit": "received_crowd_control_events",
        "rate_per_combat_second": round(count / combat_seconds, 2) if combat_seconds else None,
        "rate_per_combat_minute": round(count / (combat_seconds / 60), 2) if combat_seconds else None,
        "rate_unit": "aggregate_squad_events_per_combat_time",
        "evidence": "observed",
        "source_scope": "session_all_opponents",
        "measurement": "received_crowd_control_events",
        "source": "Defenses-Summary",
        "attribution": "enemy_profession_not_attributed",
    }]


def _pull_rates(pulls, combat_seconds):
    return [
        {
            **row,
            "count_unit": "connected_pull_hits",
            "rate_per_combat_second": round(row["count"] / combat_seconds, 3) if combat_seconds else None,
            "rate_per_combat_minute": round(row["count"] / (combat_seconds / 60), 2) if combat_seconds else None,
            "rate_unit": "aggregate_squad_connected_pulls_per_combat_time",
        }
        for row in pulls
    ]


def _role_context(damage_skills, pulls, incoming_strips, damage_profile, actor_role_evidence=None):
    damage_by_profession = {}
    for row in damage_skills:
        for profession in _DAMAGE_SKILL_PROFESSIONS.get(row["skill"], set()):
            damage_by_profession.setdefault(profession, []).append(row)
    pulls_by_profession = {}
    for row in pulls:
        for profession in _PULL_SKILL_PROFESSIONS.get(row["skill"], set()):
            pulls_by_profession.setdefault(profession, []).append(row)
    exact_strip = next(
        (row.get("count") for row in incoming_strips if row.get("evidence") == "observed"),
        None,
    )
    return {
        "damage_by_profession": damage_by_profession,
        "pulls_by_profession": pulls_by_profession,
        "incoming_strip_count": exact_strip,
        "damage_profile": damage_profile,
        "actor_role_evidence": actor_role_evidence or {},
    }


def _scope_aggregate(color, snapshots):
    counts = Counter()
    for snapshot in snapshots:
        for row in snapshot["professions"]:
            counts[row["profession"]] += row["count"]
    sample_count = len(snapshots)
    enemy_sizes = [snapshot["enemy_count"] for snapshot in snapshots]
    professions = [
        {
            "profession": profession,
            "count": count,
            "avg_per_fight": round(count / sample_count, 2) if sample_count else 0.0,
            "evidence": "observed",
        }
        for profession, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    ]
    fight_indexes = sorted({snapshot["index"] for snapshot in snapshots})
    return {
        "id": color,
        "label": color.title(),
        "color": color,
        "fight_indexes": fight_indexes,
        "aggregate": {
            "enemy_size_avg": round(sum(enemy_sizes) / sample_count, 1) if sample_count else 0.0,
            "enemy_size_max": max(enemy_sizes, default=0),
            "professions": professions,
            # These sources are session-wide and must not be falsely assigned
            # to a world colour.
            "top_damage_skills": [],
            "conditions_in": [],
            "incoming_strips": [],
            "cc": [],
            "pulls": [],
            "pressure_scope": "session_only",
        },
        "groups": [{
            "id": f"{color}-unclustered",
            "label": f"{color.title()} opponents (cohort not yet detected)",
            "cohort": None,
            "cohort_status": "future",
            "grouping_method": "color_only",
            "fight_indexes": fight_indexes,
        }],
    }


def build_enemy_intel(
    tiddlers, fight_rows, selected_fights=None, reported_fights=None,
    actor_role_evidence=None,
):
    """Build the deterministic Enemy Intel section for a night model."""
    damage_skills = _enemy_damage_skills(tiddlers)
    conditions = _squad_average_table(tiddlers, "-Conditions-In")
    debuffs = _squad_average_table(tiddlers, "-Debuffs-In")
    cc = [row for row in debuffs if row["effect"].casefold() in {"daze", "stun"}]
    strip_effects = {
        "boon removal", "boon strip", "boon strips", "corrupt boon", "corrupt boons"
    }
    debuff_strips = [row for row in debuffs if row["effect"].casefold() in strip_effects]
    combat_seconds = _combat_seconds(fight_rows)
    pulls = _pull_rates(_incoming_pulls(tiddlers), combat_seconds)
    defense_pressure = _defense_pressure(tiddlers)
    damage_profile = _damage_profile(defense_pressure, damage_skills, combat_seconds)
    # Composition must be parsed once before the fallback strip capability can
    # be generalized, then enriched once with the complete numerical context.
    base_snapshots = _composition_snapshots(tiddlers, fight_rows)
    incoming_strips = _incoming_strip_profile(
        defense_pressure,
        debuff_strips,
        base_snapshots,
        combat_seconds,
    )
    snapshots = _composition_snapshots(
        tiddlers,
        fight_rows,
        role_context=_role_context(
            damage_skills, pulls, incoming_strips, damage_profile,
            actor_role_evidence=actor_role_evidence,
        ),
    )
    colors = sorted({snapshot["color"] for snapshot in snapshots})
    scopes = [
        _scope_aggregate(color, [s for s in snapshots if s["color"] == color])
        for color in colors
    ]
    modeled_fights = len(fight_rows)
    selected = int(selected_fights) if selected_fights is not None else None
    reported = int(reported_fights) if reported_fights is not None else modeled_fights
    sources = {
        "enemy_composition": {"available": bool(snapshots), "scope": "fight_color"},
        "enemy_damage_skills": {"available": bool(damage_skills), "scope": "session"},
        "conditions_in": {"available": bool(conditions), "scope": "session"},
        "incoming_strips": {
            "available": bool(incoming_strips),
            "scope": "session",
            "evidence": incoming_strips[0]["evidence"] if incoming_strips else "not_observed",
        },
        "cc": {"available": bool(cc), "scope": "session"},
        "pulls": {"available": bool(pulls), "scope": "session"},
        "enemy_role_skills": {
            "available": bool((actor_role_evidence or {}).get("enemy_actor_appearances")),
            "scope": "detailed_enemy_targets_across_selected_fights",
        },
    }
    return {
        "coverage": {
            "selected_fights": selected,
            "reported_fights": reported,
            "modeled_fights": modeled_fights,
            "composition_fights": len({snapshot["index"] for snapshot in snapshots}),
            "composition_snapshots": len(snapshots),
            "colors": colors,
            "sources": sources,
        },
        "scopes": scopes,
        "fights": snapshots,
        "all": {
            "mode": "comparison_only",
            "estimated_subgroups": None,
            "comparisons": [
                {
                    "scope_id": scope["id"],
                    "label": scope["label"],
                    "fight_count": len(scope["fight_indexes"]),
                    "composition_snapshots": len(
                        [snapshot for snapshot in snapshots if snapshot["color"] == scope["color"]]
                    ),
                    "enemy_size_avg": scope["aggregate"]["enemy_size_avg"],
                    "top_professions": scope["aggregate"]["professions"][:5],
                }
                for scope in scopes
            ],
        },
        "session_pressure": {
            "source_scope": "session_all_opponents",
            "top_damage_skills": damage_skills,
            "conditions_in": conditions,
            "condition_profile": _condition_profile(conditions),
            "debuffs_in": debuffs,
            "incoming_strips": incoming_strips,
            "cc": cc,
            "control_profile": _control_profile(defense_pressure, combat_seconds),
            "pulls": pulls,
            "damage_profile": damage_profile,
        },
        "role_validation": actor_role_evidence or {
            "source": "combined_report_only",
            "enemy_actor_appearances": 0,
            "professions": {},
            "limitations": (
                "This recovered report contains profession counts and session-wide incoming skills, "
                "but no per-enemy rotations or DPS. Roles remain estimated."
            ),
        },
        "methodology": {
            "profession_counts": "observed",
            "party_placement": "inferred",
            "roles": "skill_and_dps_validated_when_detailed_json_exists_otherwise_estimated",
            "exact_builds": "not_available",
            "all_scope": "representative_average_across_snapshots",
        },
        # Deliberately empty: report generation never makes a network/model
        # call.  A future opt-in enrichment step may populate this structure.
        "ai_analysis": None,
    }
