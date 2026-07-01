"""Backwards-compatibility shim for the original ai_analyst.py module.

The monolithic ai_analyst.py was split into five sibling modules:
    ai_helpers, vocabulary_config, vocabulary_tracker,
    session_history, fight_analyst.

Existing call sites that do `from ai_analyst import FightAnalyst`
(or any other public name from the original file) keep working
unchanged because this shim re-exports every public symbol.

When adding new code, prefer importing directly from the specific
module. This shim will be kept for a deprecation period and then
removed.
"""

# --- Module-level helpers, constants, and regex patterns -------------------
from ai_helpers import (  # noqa: F401
    HYPE_HEAVY,
    HYPE_OUTSIZED,
    HYPE_RESERVED,
    SIEGE_SKILL_NAMES,
    TAG_DISTANCE_ACCEPTABLE,
    TAG_DISTANCE_EXCELLENT,
    TAG_DISTANCE_LOOSE,
    _SENTENCE_SPLIT_RE,
    _SIEGE_NAME_SUBSTRINGS,
    _STAT_RE,
    _THINK_STRAY_RE,
    _THINK_TAG_RE,
    _THINK_UNCLOSED_RE,
    _atomic_write_json,
    _extract_fallback_sentences,
    _extract_squad_roster,
    _grade_tag_distance,
    _is_siege_skill,
    _median,
    _strip_think_tags,
)

# --- Classes ---------------------------------------------------------------
from vocabulary_config import VocabularyConfig  # noqa: F401
from vocabulary_tracker import VocabularyTracker  # noqa: F401
from session_history import SessionHistoryTracker  # noqa: F401
from fight_analyst import (  # noqa: F401
    DEFAULT_PROMPT_CHANGELOG,
    DEFAULT_PROMPT_VERSION,
    FightAnalyst,
    _REQUIRED_SUMMARY_KEYS,
)

# --- Provider presets ------------------------------------------------------
# PRESETS moved to providers.py in the split; re-exported here so first-run
# callers (setup_wizard, gui_settings) that do `from ai_analyst import PRESETS`
# keep working.
from providers import PRESETS  # noqa: F401

__all__ = [
    # Constants
    "HYPE_HEAVY",
    "HYPE_OUTSIZED",
    "HYPE_RESERVED",
    "SIEGE_SKILL_NAMES",
    "TAG_DISTANCE_ACCEPTABLE",
    "TAG_DISTANCE_EXCELLENT",
    "TAG_DISTANCE_LOOSE",
    # Helpers
    "_atomic_write_json",
    "_extract_fallback_sentences",
    "_extract_squad_roster",
    "_grade_tag_distance",
    "_is_siege_skill",
    "_median",
    "_strip_think_tags",
    # Classes
    "FightAnalyst",
    "SessionHistoryTracker",
    "VocabularyConfig",
    "VocabularyTracker",
    # Prompt metadata
    "DEFAULT_PROMPT_CHANGELOG",
    "DEFAULT_PROMPT_VERSION",
    # Provider presets
    "PRESETS",
]
