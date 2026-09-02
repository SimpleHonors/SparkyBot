# Trait and consumable evidence

SparkyBot treats build identification as evidence, not a build-template guess.

## What Elite Insights exposes

- Squad players: rotations, weapon sets, consumables, buffs, and—when the
  extensions are present—healing/barrier totals. Elite Insights can also place
  nearby friendly players in `players`; exclude records marked `NotInSquad` or
  `FriendlyNPC` before building squad comparisons.
- Enemy WvW targets: rotations, damage, buffs, and buff volumes. Enemy targets
  do not expose the squad-player `Consumables` field or reliable outgoing
  healing totals.
- Skill metadata: `IsTraitProc` distinguishes a trait-created proc from an
  ordinary weapon or utility skill.
- Buff metadata: `Classification` distinguishes `Nourishment`, `Enhancement`,
  and `Other Consumable` effects.

Parser references:

- <https://baaron4.github.io/GW2-Elite-Insights-Parser/Json/class_g_w2_e_i_j_s_o_n_1_1_json_player.html>
- <https://github.com/baaron4/GW2-Elite-Insights-Parser/blob/master/GW2EIJSON/JsonActors/JsonPlayer.cs>
- <https://baaron4.github.io/GW2-Elite-Insights-Parser/Json/class_g_w2_e_i_j_s_o_n_1_1_json_n_p_c.html>
- <https://baaron4.github.io/GW2-Elite-Insights-Parser/Json/class_g_w2_e_i_j_s_o_n_1_1_json_log_1_1_skill_desc.html>
- <https://baaron4.github.io/GW2-Elite-Insights-Parser/Json/class_g_w2_e_i_j_s_o_n_1_1_json_log_1_1_buff_desc.html>

## Trait proof rule

A report names a selected major trait only when both conditions are true:

1. Elite Insights marked an actually observed skill as `IsTraitProc`.
2. The current official GW2 API maps that skill ID to exactly one major trait.

The generated catalog scans every specialization and major trait from the
official `/v2/specializations` and `/v2/traits` endpoints. The 2026-09-01 scan
covered 81 specializations and 729 major traits; 126 unique observable
trait-skill fingerprints survived the proof rule. Minor traits, shared skill
IDs, description-only modifiers, and unobserved effects are excluded.

Refresh it with:

```bash
python3 tools/build_trait_evidence_catalog.py
```

Review the count change and run the full test suite before accepting a refresh.

## Food and utility proof rule

- Squad: the exact exported `Consumables` entries are resolved through the
  report `buffMap`.
- Enemy: an item is named only when its specific Nourishment/Enhancement buff
  appears in that target's observed `buffs`. If it is absent, the result is
  unknown—not “no food.”
- Role classification uses the resolved effect description. Matching food and
  utility effects create a strong role signal only when both independently
  indicate the same single role. Performance data is still shown separately.

This supports “probably healing/support/damage” explanations without claiming
the complete build, gear, or unobserved trait choices.

## Enemy-role attribution rule

Enemy skill, damage, trait, and consumable signals are aggregated by profession
because detailed WvW exports do not retain a stable identity that can be joined
to an estimated subgroup slot. Preserve those signals as candidate evidence,
but label each reconstructed slot `Role not inferred`. Do not stamp a
profession-level DPS/support signal onto every estimated player of that
profession.
