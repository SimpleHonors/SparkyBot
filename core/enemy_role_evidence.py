"""Extract conservative enemy-role signals from detailed GW2EI JSON.

Enemy outgoing healing is normally not measurable because the healing extension
is installed by squad players, not opponents.  Detailed WvW targets do expose
per-enemy damage and rotations, which support a narrower inference:

* damaging skills plus meaningful DPS can validate DPS;
* low DPS plus role-specific healing/support casts can validate support intent;
* low DPS alone never proves healing.

The result is aggregated by profession because the combined night report keeps
profession counts but intentionally discards enemy identity.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path


_TRAIT_CATALOG_PATH = Path(__file__).with_name("trait_evidence_catalog.json")
try:
    _TRAIT_SKILLS = json.loads(_TRAIT_CATALOG_PATH.read_text(encoding="utf-8")).get("skills", {})
except (OSError, UnicodeDecodeError, json.JSONDecodeError):
    _TRAIT_SKILLS = {}


_SKILL_ROLE = {
    # Elementalist / Tempest
    "Overload Water": "Support / Healing",
    '"Wash the Pain Away!"': "Support / Healing",
    "Wash the Pain Away!": "Support / Healing",
    '"Rebound!"': "Boon Support",
    "Rebound!": "Boon Support",
    '"Eye of the Storm!"': "Boon Support",
    "Eye of the Storm!": "Boon Support",
    "Sand Squall": "Boon Support",
    "Geyser": "Support / Healing",
    "Healing Rain": "Support / Healing",
    "Water Trident": "Support / Healing",
    # Druid
    "Cosmic Ray": "Support / Healing",
    "Seed of Life": "Support / Healing",
    "Lunar Impact": "Support / Healing",
    "Rejuvenating Tides": "Support / Healing",
    "Glyph of the Stars": "Support / Healing",
    "Glyph of Renewal": "Support / Healing",
    # Guardian / Firebrand
    "Mantra of Solace": "Support / Healing",
    "Bow of Truth": "Support / Healing",
    "Chapter 2: Radiant Recovery": "Support / Healing",
    "Chapter 4: Stalwart Stand": "Boon Support",
    "Chapter 3: Valiant Bulwark": "Boon Support",
    # Engineer / Scrapper
    "Med Blaster": "Support / Healing",
    "Healing Mist": "Support / Healing",
    "Reconstruction Field": "Support / Healing",
    "Purge Gyro": "Boon Support",
    "Bulwark Gyro": "Boon Support",
    "Defense Field": "Boon Support",
    # Mesmer / Chronomancer
    "Well of Eternity": "Support / Healing",
    "Signet of Inspiration": "Boon Support",
    "Well of Precognition": "Boon Support",
    "Gravity Well": "Crowd Control",
    # Necromancer / Scourge
    "Sand Cascade": "Support / Healing",
    "Nefarious Favor": "Boon Support",
    "Desert Empowerment": "Support / Healing",
    "Garish Pillar": "Crowd Control",
    # Warrior / Spellbreaker
    "Winds of Disenchantment": "Boon Strip",
    # Revenant
    "Ventari's Will": "Support / Healing",
    "Protective Solace": "Boon Support",
    "Natural Harmony": "Support / Healing",
}


def _first_dict(value):
    return value[0] if isinstance(value, list) and value and isinstance(value[0], dict) else {}


def _skill_name(skill_map, skill_id):
    value = skill_map.get(f"s{skill_id}", {})
    return value.get("name") if isinstance(value, dict) else None


def _buff_desc(buff_map, buff_id):
    value = buff_map.get(f"b{buff_id}", {})
    return value if isinstance(value, dict) else {}


def _role_terms(*values):
    text = " ".join(str(value or "").casefold() for value in values)
    rules = {
        "Healing": ("healing power", "outgoing healing", "healing effectiveness", "revive"),
        "Support": ("concentration", "boon duration", "condition cleanse", "condition duration removed"),
        "Condition Damage": ("condition damage", "expertise", "condition duration"),
        "Power Damage": ("power", "precision", "ferocity", "critical damage"),
        "Defense": ("toughness", "vitality", "maximum health", "damage reduction"),
    }
    found = []
    for role, terms in rules.items():
        haystack = text.replace("healing power", "") if role == "Power Damage" else text
        if any(term in haystack for term in terms):
            found.append(role)
    return found


def _consumable_rows(actor, buff_map, *, enemy=False):
    """Return exact observed consumable buffs; never infer an unobserved item."""
    ids = Counter()
    source = actor.get("buffs") if enemy else actor.get("consumables")

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            buff_id = value.get("id")
            if isinstance(buff_id, int):
                desc = _buff_desc(buff_map, buff_id)
                classification = str(desc.get("classification") or "")
                if classification in {"Nourishment", "Enhancement", "Other Consumable"}:
                    ids[buff_id] += 1
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    visit(nested)

    visit(source or [])
    rows = []
    for buff_id, observations in ids.items():
        desc = _buff_desc(buff_map, buff_id)
        description = " ".join(desc.get("descriptions") or [])
        rows.append({
            "id": buff_id,
            "name": desc.get("name") or f"Buff {buff_id}",
            "classification": desc.get("classification") or "Other Consumable",
            "description": description,
            "roles": _role_terms(desc.get("name"), description),
            "observations": observations,
            "source_scope": "enemy_observed_buffs" if enemy else "player_consumables",
        })
    return sorted(rows, key=lambda row: (row["classification"], row["name"]))


def _consumable_role_signal(rows):
    food_roles = set()
    utility_roles = set()
    for row in rows:
        target = food_roles if row["classification"] == "Nourishment" else utility_roles
        target.update(row.get("roles") or [])
    aligned = sorted(food_roles & utility_roles)
    if len(aligned) != 1:
        return None
    return {
        "role": aligned[0],
        "confidence": "strong",
        "evidence": "Nourishment and Enhancement independently point to the same role",
    }


def _observed_skill_ids(actor):
    ids = set()

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if isinstance(value.get("id"), int):
                ids.add(value["id"])
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    visit(nested)

    visit(actor.get("rotation") or [])
    visit(actor.get("totalDamageDist") or [])
    return ids


def _trait_observations(actor, skill_map):
    rows = []
    for skill_id in sorted(_observed_skill_ids(actor)):
        desc = skill_map.get(f"s{skill_id}", {})
        catalog = _TRAIT_SKILLS.get(str(skill_id))
        if not isinstance(desc, dict) or not desc.get("isTraitProc") or not catalog:
            continue
        rows.append({
            **catalog,
            "observed_skill_id": skill_id,
            "observed_skill": desc.get("name") or catalog.get("evidence_skill"),
            "proof": "Elite Insights marked this observed skill as a trait proc; the official API maps the skill uniquely to one major trait.",
        })
    return rows


def _rotation_evidence(target, skill_map):
    counts = Counter()
    timed_casts = []

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        skill_id = value.get("id")
        casts = value.get("skills")
        if skill_id is not None and isinstance(casts, list):
            name = _skill_name(skill_map, skill_id)
            if name:
                counts[name] += len(casts)
                for cast in casts:
                    if not isinstance(cast, dict):
                        continue
                    timestamp = next((
                        cast.get(key) for key in (
                            "castTime", "time", "start", "startTime"
                        ) if isinstance(cast.get(key), (int, float))
                    ), None)
                    if timestamp is not None:
                        timed_casts.append((timestamp, name))
            return
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                visit(nested)

    visit(target.get("rotation") or [])
    timed_casts.sort(key=lambda item: item[0])
    links = Counter(
        f"{first[1]} → {second[1]}"
        for first, second in zip(timed_casts, timed_casts[1:])
    )
    return counts, links


def _rotation_counts(target, skill_map):
    return _rotation_evidence(target, skill_map)[0]


def _weapon_names(player):
    """Return distinct observable weapon names from current or legacy EI JSON."""
    found = []

    def visit(value):
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned and cleaned.casefold() not in {
                "unknown", "2hand", "none", "no weapon",
            }:
                found.append(cleaned)
            return
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if isinstance(value, dict):
            for item in value.values():
                visit(item)

    visit(player.get("weaponSets") or player.get("weapons") or [])
    return set(found)


def _damage_skill_counts(target, skill_map):
    counts = Counter()
    distribution = target.get("totalDamageDist") or []
    phase = distribution[0] if distribution and isinstance(distribution[0], list) else []
    for row in phase:
        if not isinstance(row, dict):
            continue
        name = _skill_name(skill_map, row.get("id"))
        if name and (row.get("totalDamage") or 0) > 0:
            counts[name] += int(row.get("hits") or row.get("connectedHits") or 1)
    return counts


def collect_player_skill_evidence(json_paths):
    """Aggregate squad-player skill casts from detailed EI JSON paths.

    The combined Top Stats export retains connected and total damage-hit counts
    but not cast counts. Detailed EI JSON still has each player's rotation, so
    keeping the two sources separate explains pulsing skills without pretending
    either measurement is an observed pull event.
    """
    players = {}
    for path in json_paths:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        skill_map = data.get("skillMap") or {}
        buff_map = data.get("buffMap") or {}
        for player in data.get("players") or []:
            if not isinstance(player, dict):
                continue
            # Elite Insights keeps nearby friendly players in the ``players``
            # collection for WvW and marks them explicitly.  They are useful
            # combat context, but they are not valid squad-comparison rows.
            if player.get("notInSquad") or player.get("friendlyNPC"):
                continue
            name = str(player.get("name") or "").strip()
            account = str(player.get("account") or "").strip().lstrip(":")
            identity = (account or name).casefold()
            if not identity:
                continue
            casts, rotation_links = _rotation_evidence(player, skill_map)
            row = players.setdefault(identity, {
                "account": account or None,
                "names": set(),
                "professions": set(),
                "fight_appearances": 0,
                "skill_casts": Counter(),
                "weapons": set(),
                "rotation_links": Counter(),
                "consumables": {},
                "traits": {},
            })
            if name:
                row["names"].add(name)
            profession = str(player.get("profession") or "").strip()
            if profession:
                row["professions"].add(profession)
            row["fight_appearances"] += 1
            row["skill_casts"].update(casts)
            row["weapons"].update(_weapon_names(player))
            row["rotation_links"].update(rotation_links)
            for consumable in _consumable_rows(player, buff_map):
                existing = row["consumables"].setdefault(consumable["id"], {**consumable, "observations": 0})
                existing["observations"] += consumable["observations"]
            for trait in _trait_observations(player, skill_map):
                existing = row["traits"].setdefault(trait["observed_skill_id"], {**trait, "appearances": 0})
                existing["appearances"] += 1

    return {
        "source": "detailed_gw2ei_json_player_rotations",
        "players": [
            {
                "account": row["account"],
                "names": sorted(row["names"]),
                "professions": sorted(row["professions"]),
                "fight_appearances": row["fight_appearances"],
                "skill_casts": dict(row["skill_casts"]),
                "weapons": sorted(row["weapons"]),
                "rotation_links": dict(row["rotation_links"]),
                "consumables": sorted(row["consumables"].values(), key=lambda item: (item["classification"], item["name"])),
                "consumable_role_signal": _consumable_role_signal(row["consumables"].values()),
                "traits": sorted(row["traits"].values(), key=lambda item: (item["specialization"], item["trait"])),
            }
            for row in players.values()
        ],
        "limitations": (
            "Cast counts come from player rotations. Connected and total "
            "damage-hit counts come from the combined Pull-Skills table; "
            "actual pull events and distinct enemies moved are not measured."
        ),
    }


def collect_enemy_role_evidence(json_paths):
    """Return profession-level role signals from detailed EI JSON paths.

    Bad/missing/non-detailed files are ignored.  The function is deliberately
    pure and makes no network calls.
    """
    actors = []
    for path in json_paths:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        skill_map = data.get("skillMap") or {}
        buff_map = data.get("buffMap") or {}
        duration_ms = int(data.get("durationMS") or 0)
        for target in data.get("targets") or []:
            if not isinstance(target, dict) or target.get("name", "").startswith("Dummy"):
                continue
            profession = target.get("profession") or str(target.get("name") or "").split(" ", 1)[0]
            if not profession:
                continue
            dps_row = _first_dict(target.get("dpsAll"))
            active = target.get("activeTimes") or []
            active_ms = int(active[0] if active and isinstance(active[0], (int, float)) else duration_ms)
            rotation = _rotation_counts(target, skill_map)
            damage_skills = _damage_skill_counts(target, skill_map)
            consumables = _consumable_rows(target, buff_map, enemy=True)
            actors.append({
                "profession": profession,
                "dps": float(dps_row.get("dps") or 0),
                "damage": int(dps_row.get("damage") or 0),
                "active_ms": active_ms,
                "rotation": rotation,
                "damage_skills": damage_skills,
                "consumables": consumables,
                "consumable_role_signal": _consumable_role_signal(consumables),
                "traits": _trait_observations(target, skill_map),
            })

    meaningful = [row["dps"] for row in actors if row["active_ms"] >= 15_000 and row["damage"] > 0]
    median_dps = statistics.median(meaningful) if meaningful else 0.0
    upper_count = max(1, math.ceil(len(meaningful) / 3))
    damage_reference = statistics.median(sorted(meaningful, reverse=True)[:upper_count]) if meaningful else 0.0
    by_profession = defaultdict(list)
    for actor in actors:
        by_profession[actor["profession"]].append(actor)

    professions = {}
    for profession, rows in by_profession.items():
        profession_meaningful = [
            row["dps"] for row in rows
            if row["active_ms"] >= 15_000 and row["damage"] > 0
        ]
        baseline_ready = len(profession_meaningful) >= 2
        profession_upper_count = max(1, math.ceil(len(profession_meaningful) / 3))
        profession_reference = (
            statistics.median(
                sorted(profession_meaningful, reverse=True)[:profession_upper_count]
            )
            if profession_meaningful else 0.0
        )
        role_votes = Counter()
        skill_totals = Counter()
        low_damage = 0
        meaningful_rows = 0
        consumable_totals = {}
        trait_totals = {}
        for row in rows:
            if row["active_ms"] < 15_000:
                continue
            meaningful_rows += 1
            low = bool(
                baseline_ready and profession_reference
                and row["dps"] <= profession_reference * 0.35
            )
            low_damage += int(low)
            for skill, casts in row["rotation"].items():
                role = _SKILL_ROLE.get(skill)
                if role:
                    skill_totals[skill] += casts
                    if low or role in {"Crowd Control", "Boon Strip"}:
                        role_votes[role] += casts
            if (
                row["damage"] > 0 and row["damage_skills"]
                and baseline_ready and profession_reference
                and row["dps"] >= profession_reference * 0.75
            ):
                role_votes["DPS"] += 1
            for consumable in row["consumables"]:
                existing = consumable_totals.setdefault(consumable["id"], {**consumable, "actor_appearances": 0, "observations": 0})
                existing["actor_appearances"] += 1
                existing["observations"] += consumable["observations"]
            for trait in row["traits"]:
                existing = trait_totals.setdefault(trait["observed_skill_id"], {**trait, "actor_appearances": 0})
                existing["actor_appearances"] += 1
        signals = []
        for role, votes in role_votes.most_common():
            matching = [f"{name} ×{count}" for name, count in skill_totals.most_common()
                        if _SKILL_ROLE.get(name) == role]
            signals.append({
                "role": role,
                "level": "Likely",
                "evidence": (
                    f"{votes} role-signature observations across {meaningful_rows} meaningful enemy appearances"
                    + (f": {', '.join(matching[:5])}" if matching else "")
                ),
                "source_scope": "detailed_wvw_enemy_targets_across_selected_fights",
            })
        professions[profession] = {
            "actor_appearances": len(rows),
            "meaningful_appearances": meaningful_rows,
            "median_dps": round(statistics.median([row["dps"] for row in rows]), 2),
            "profession_average_dps": round(statistics.mean(profession_meaningful), 2)
            if profession_meaningful else 0,
            "profession_damage_reference_dps": round(profession_reference, 2),
            "profession_baseline_status": "observed" if baseline_ready else "insufficient_sample",
            "low_damage_appearances": low_damage,
            "skills": dict(skill_totals),
            "roles": signals,
            "consumables": sorted(consumable_totals.values(), key=lambda item: (item["classification"], item["name"])),
            "traits": sorted(trait_totals.values(), key=lambda item: (item["specialization"], item["trait"])),
        }
    return {
        "source": "detailed_gw2ei_json",
        "enemy_actor_appearances": len(actors),
        "session_enemy_median_dps": round(median_dps, 2),
        "session_enemy_damage_reference_dps": round(damage_reference, 2),
        "professions": dict(professions),
        "limitations": (
            "Enemy healing totals are not measured. Healing/support roles use damage normalized against the "
            "same profession plus role-specific casts; low DPS alone never proves healing. A single profession "
            "appearance is not enough for a relative damage role."
        ),
    }
