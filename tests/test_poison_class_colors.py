"""Every profession the fight parser can name gets a real chart color.

The poison chart fell back to grey for professions missing from
CLASS_COLORS (Antiquary, Galeshot, all Engineer/Thief lines, the core
professions). Key the roster on fight_report.PROFESSION_NAMES so a new
elite spec added to the parser fails here until it gets a color.
"""

import re

from core.fight_report import FightReport
from core.poison_tab import CLASS_COLORS, class_color

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def test_every_parser_profession_has_an_explicit_color():
    missing = [name for name in FightReport.PROFESSION_NAMES.values()
               if name not in CLASS_COLORS]
    assert missing == [], f"professions falling back to grey: {missing}"


def test_all_colors_are_valid_hex_and_not_the_grey_default():
    for name in FightReport.PROFESSION_NAMES.values():
        color = class_color(name)
        assert _HEX.match(color), f"{name}: bad color {color!r}"
        assert color != CLASS_COLORS["_default"], f"{name} is default grey"


def test_unknown_and_blank_professions_still_get_the_default():
    assert class_color("Chair") == CLASS_COLORS["_default"]
    assert class_color(None) == CLASS_COLORS["_default"]
    assert class_color("  Reaper  ") == CLASS_COLORS["Reaper"]


def test_family_bases_follow_the_core_share_convention():
    # Core professions share their family base with a representative spec,
    # matching the existing Necromancer/Scourge convention.
    assert CLASS_COLORS["Necromancer"] == CLASS_COLORS["Scourge"]
    assert CLASS_COLORS["Guardian"] == CLASS_COLORS["Firebrand"]
    assert CLASS_COLORS["Elementalist"] == CLASS_COLORS["Tempest"]
    assert CLASS_COLORS["Mesmer"] == CLASS_COLORS["Chronomancer"]
    assert CLASS_COLORS["Warrior"] == CLASS_COLORS["Spellbreaker"]
    assert CLASS_COLORS["Revenant"] == CLASS_COLORS["Herald"]
    assert CLASS_COLORS["Ranger"] == CLASS_COLORS["Druid"]
