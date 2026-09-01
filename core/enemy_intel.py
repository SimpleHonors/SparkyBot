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


# These are intentionally broad role families, not build claims.  Anything
# absent from the map stays hybrid rather than being forced into a role.
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


def estimate_enemy_subgroups(professions, enemy_count):
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
        profession = str(row.get("profession") or "Unknown").strip() or "Unknown"
        count = max(int(row.get("count", 0)), 0)
        rows.append((profession, count, _role_for(profession)))

    for role in _ROLE_ORDER:
        role_rows = sorted(
            (row for row in rows if row[2] == role),
            key=lambda row: (-row[1], row[0].casefold()),
        )
        for profession, count, _ in role_rows:
            for _copy in range(count):
                candidates = [i for i, party in enumerate(parties) if len(party) < 5]
                if not candidates:
                    break
                chosen = min(
                    candidates,
                    key=lambda i: (
                        profession_counts[i][profession],
                        role_counts[i][role],
                        len(parties[i]),
                        i,
                    ),
                )
                parties[chosen].append({
                    "profession": profession,
                    "role": role,
                    "evidence": "inferred",
                    "role_evidence": "inferred_from_profession_family",
                })
                profession_counts[chosen][profession] += 1
                role_counts[chosen][role] += 1

    unknown_slots = max(int(enemy_count or 0) - observed_count, 0)
    for _ in range(unknown_slots):
        candidates = [i for i, party in enumerate(parties) if len(party) < 5]
        if not candidates:
            break
        chosen = min(candidates, key=lambda i: (len(parties[i]), i))
        parties[chosen].append({
            "profession": None,
            "role": "unknown",
            "evidence": "unknown",
            "role_evidence": "unknown",
        })

    confidence = _confidence(observed_count, int(enemy_count or 0))
    result = []
    for index, members in enumerate(parties, 1):
        party_unknown = sum(1 for member in members if member["evidence"] == "unknown")
        result.append({
            "party": index,
            "members": members,
            "unknown_slots": party_unknown,
            "open_slots": 5 - len(members),
            "confidence": confidence,
            "evidence": "inferred",
        })
    return result, unknown_slots, confidence


def _composition_snapshots(tiddlers, fight_rows):
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
            professions, enemy_count
        )
        output.append({
            "index": snapshot["index"],
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
            "total_casts": _number(values.get("total_casts", "")),
            "connected_hits": _number(values.get("connected_hits", "")),
            "percent": _number(values.get("%_of_total", "")),
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
    tiddlers, fight_rows, selected_fights=None, reported_fights=None
):
    """Build the deterministic Enemy Intel section for a night model."""
    snapshots = _composition_snapshots(tiddlers, fight_rows)
    colors = sorted({snapshot["color"] for snapshot in snapshots})
    scopes = [
        _scope_aggregate(color, [s for s in snapshots if s["color"] == color])
        for color in colors
    ]
    damage_skills = _enemy_damage_skills(tiddlers)
    conditions = _squad_average_table(tiddlers, "-Conditions-In")
    debuffs = _squad_average_table(tiddlers, "-Debuffs-In")
    cc = [row for row in debuffs if row["effect"].casefold() in {"daze", "stun"}]
    strip_effects = {
        "boon removal", "boon strip", "boon strips", "corrupt boon", "corrupt boons"
    }
    incoming_strips = [row for row in debuffs if row["effect"].casefold() in strip_effects]
    pulls = _incoming_pulls(tiddlers)
    modeled_fights = len(fight_rows)
    selected = int(selected_fights) if selected_fights is not None else None
    reported = int(reported_fights) if reported_fights is not None else modeled_fights
    sources = {
        "enemy_composition": {"available": bool(snapshots), "scope": "fight_color"},
        "enemy_damage_skills": {"available": bool(damage_skills), "scope": "session"},
        "conditions_in": {"available": bool(conditions), "scope": "session"},
        "incoming_strips": {
            "available": bool(incoming_strips),
            "scope": "session" if incoming_strips else "not_available",
        },
        "cc": {"available": bool(cc), "scope": "session"},
        "pulls": {"available": bool(pulls), "scope": "session"},
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
            "debuffs_in": debuffs,
            "incoming_strips": incoming_strips,
            "cc": cc,
            "pulls": pulls,
            "damage_profile": {
                "direct_percent": None,
                "condition_percent": None,
                "status": "not_available_from_combiner_summary",
            },
        },
        "methodology": {
            "profession_counts": "observed",
            "party_placement": "inferred",
            "roles": "inferred_from_profession_family",
            "exact_builds": "not_available",
            "all_scope": "comparison_only_never_blended",
        },
        # Deliberately empty: report generation never makes a network/model
        # call.  A future opt-in enrichment step may populate this structure.
        "ai_analysis": None,
    }
