#!/usr/bin/env python3
"""Build the conservative GW2 trait-proc catalog used by SparkyBot reports.

Only major traits with a skill ID that belongs to exactly one major trait are
kept. Runtime evidence must also be marked ``isTraitProc`` by Elite Insights,
so this catalog cannot turn an ordinary shared weapon/utility skill into a
trait claim.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import urllib.request
from collections import Counter
from pathlib import Path


API = "https://api.guildwars2.com/v2"
DEFAULT_OUTPUT = Path(__file__).parents[1] / "core" / "trait_evidence_catalog.json"


def _get(endpoint):
    with urllib.request.urlopen(f"{API}/{endpoint}", timeout=30) as response:
        return json.load(response)


def _plain(value):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value or ""))).strip()


def _roles(*values):
    text = " ".join(_plain(value).casefold() for value in values)
    rules = {
        "Healing": ("heal", "revive", "revival", "regeneration"),
        "Support": ("boon", "barrier", "cleanse", "condition from you", "condition on you",
                    "stability", "protection", "aegis", "resolution", "resistance", "quickness",
                    "alacrity", "might", "fury", "vigor"),
        "Damage": ("damage", "strike", "critical", "bleed", "burn", "poison", "torment",
                   "confusion", "vulnerability"),
        "Control": ("disable", "stun", "daze", "knock", "pull", "launch", "immobilize", "fear"),
        "Defense": ("block", "evade", "invulner", "damage reduction", "take less damage"),
    }
    found = [role for role, words in rules.items() if any(word in text for word in words)]
    return found or ["Utility / build-specific"]


def build_catalog():
    specializations = _get("specializations?ids=all")
    spec_by_trait = {}
    trait_ids = []
    for specialization in specializations:
        for trait_id in specialization.get("major_traits") or []:
            trait_ids.append(trait_id)
            spec_by_trait[trait_id] = {
                "specialization_id": specialization["id"],
                "specialization": specialization["name"],
                "profession": specialization["profession"],
                "elite": bool(specialization.get("elite")),
            }
    traits = []
    for offset in range(0, len(trait_ids), 200):
        traits.extend(_get("traits?ids=" + ",".join(map(str, trait_ids[offset:offset + 200]))))

    ownership = Counter(
        skill["id"]
        for trait in traits
        for skill in trait.get("skills") or []
        if skill.get("id") is not None
    )
    skills = {}
    for trait in traits:
        context = spec_by_trait.get(trait["id"], {})
        for skill in trait.get("skills") or []:
            skill_id = skill.get("id")
            if skill_id is None or ownership[skill_id] != 1:
                continue
            description = _plain(skill.get("description") or trait.get("description"))
            skills[str(skill_id)] = {
                **context,
                "trait_id": trait["id"],
                "trait": trait["name"],
                "trait_description": _plain(trait.get("description")),
                "evidence_skill": skill.get("name") or f"Skill {skill_id}",
                "evidence_skill_description": description,
                "roles": _roles(trait.get("name"), trait.get("description"), skill.get("name"), description),
                "proof_rule": "unique_major_trait_skill_and_elite_insights_trait_proc",
            }
    return {
        "schema_version": 1,
        "sources": [f"{API}/specializations?ids=all", f"{API}/traits?ids=<major-trait-ids>"],
        "method": (
            "All current specializations and major traits were enumerated. Only a skill ID referenced by "
            "exactly one major trait is retained; runtime use additionally requires Elite Insights isTraitProc."
        ),
        "counts": {
            "specializations_scanned": len(specializations),
            "major_traits_scanned": len(traits),
            "traits_with_skill_references": sum(bool(trait.get("skills")) for trait in traits),
            "unique_observable_trait_skills": len(skills),
        },
        "skills": dict(sorted(skills.items(), key=lambda item: int(item[0]))),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    catalog = build_catalog()
    args.output.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(catalog["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
