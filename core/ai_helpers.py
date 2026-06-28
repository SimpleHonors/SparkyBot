"""Shared constants, regex patterns, and pure-function helpers.

This module has no dependencies on any other module in the ai_analyst
package. Every other module imports from here, and nothing here imports
from any sibling.

Requires Python 3.10+.
"""
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Pattern for stripping LLM think tags in any state (closed, unclosed, stray)
_THINK_TAG_RE = re.compile(r'<think>.*?</think>', re.DOTALL)
_THINK_UNCLOSED_RE = re.compile(r'<think>.*', re.DOTALL)
_THINK_STRAY_RE = re.compile(r'</?think>')

# Sentence splitting that respects common abbreviations and decimals
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')

# Matches stat/number references (digit sequences, percentages, ratios, "5.7 KDR")
# Negative lookahead excludes bare counts of meta items (sentences, players, etc.)
_STAT_RE = re.compile(
    r'\b\d[\d,]*\.?\d*\s*[kKmM]?\b'
    r'(?!\s*(?:sentences?|supports?|players?|words?|times?|responses?|fights?'
    r'|categories?|topics?|lines?|pages?|attempts?|entries?))'
    r'|\b\d+%'                          # 45%
    r'|\b\d+\s*:\s*\d+'                 # 3:1 ratio
    r'|\b\d+\.\d+\s+KDR\b',            # 5.73 KDR
    re.IGNORECASE
)

# Tag distance thresholds (units from commander tag)
TAG_DISTANCE_EXCELLENT = 1200  # <= 1200: tight squad
TAG_DISTANCE_ACCEPTABLE = 2000  # 1201-2000: acceptable spread
TAG_DISTANCE_LOOSE = 3500       # 2001-3500: drifting, call it out
                                 # > 3500: too loose, commander should address

# Hype-term suppression tiers for context_blocks_term (moved from inline to module level)
HYPE_HEAVY = frozenset({
    "holy shit", "absolute monsters", "gigachads", "big damage",
    "magnificent motherfuckers", "ride 'em like a pony", "yeet yeet delete",
    "massacre", "slaughter", "battering ram", "relentless", "here to pump",
    "bags",
})
HYPE_RESERVED = frozenset({
    "holy shit", "absolute monsters", "gigachads",
    "magnificent motherfuckers", "ride 'em like a pony",
})
HYPE_OUTSIZED = frozenset({
    "absolute monsters", "gigachads", "magnificent motherfuckers",
})

# Siege weapon skill names (single source of truth)
# NOTE: "Mortar Shot" is Engineer Mortar Kit, NOT siege - excluded to avoid false positives
SIEGE_SKILL_NAMES = frozenset({
    # Arrow Cart
    "Arrow Cart", "Superior Arrow Cart",
    "Arrow Cart Shot", "Barbed Arrow Cart Shot", "Poison Arrow Cart Shot",
    # Ballista
    "Ballista", "Superior Ballista",
    "Ballista Bolt", "Heavy Bolt", "Spread Shot",
    # Catapult (was missing entirely)
    "Catapult", "Superior Catapult",
    "Catapult Shot", "Boulder", "Heavy Boulder",
    # Trebuchet
    "Trebuchet", "Superior Trebuchet",
    "Trebuchet Shot", "Trebuchet Boulder",
    # Siege Mortar (NOT the engineer kit)
    "Heavy Shot", "Concussion Shot", "Endothermic Shell", "Elixir Shell",
    # Flame Ram
    "Flame Ram", "Superior Flame Ram", "Flame Blast",
})

# Substring fallbacks for naming variants (kept narrow to avoid Mortar Shot bug)
_SIEGE_NAME_SUBSTRINGS = ("arrow cart", "catapult", "trebuchet", "ballista")


def _is_siege_skill(skill_name: str) -> bool:
    """Return True if the skill name is from a WvW siege weapon."""
    if not skill_name:
        return False
    if skill_name in SIEGE_SKILL_NAMES:
        return True
    lower = skill_name.lower()
    return any(sub in lower for sub in _SIEGE_NAME_SUBSTRINGS)


def _atomic_write_json(path: Path, data: Any) -> None:
    """Persist JSON atomically via temp-file + os.replace to avoid corruption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _median(values: list) -> float:
    """Return the statistical median of a numeric list."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 0:
        return (s[mid - 1] + s[mid]) / 2
    return s[mid]


def _grade_tag_distance(tag_data: list) -> Optional[float]:
    """Return the trimmed median distance to tag, or None if insufficient data.

    Trims the top 10% of raw distances as outliers (dead players at spawn,
    etc.) before computing the median. This is the single source of truth
    for tag-discipline grading across mood, callouts, and analysis text.
    """
    if not tag_data or not isinstance(tag_data, list) or len(tag_data) <= 1:
        return None
    distances = sorted(p.get("distance", 0) for p in tag_data)
    cutoff_idx = max(1, int(len(distances) * 0.9))
    return _median(distances[:cutoff_idx])


def _strip_think_tags(content: str) -> str:
    """Remove LLM <think> tags and their content from a response."""
    result = _THINK_TAG_RE.sub('', content)
    result = _THINK_UNCLOSED_RE.sub('', result)
    result = _THINK_STRAY_RE.sub('', result)
    return result.strip()


def _extract_fallback_sentences(content: str, max_sentences: int = 4) -> Optional[str]:
    """Extract the last few sentences from think-tag inner content as a fallback."""
    inner = _THINK_STRAY_RE.sub('', content).strip()
    if not inner:
        return None
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(inner) if s.strip()]
    if not sentences:
        return None
    tail = sentences[-max_sentences:]
    result = ' '.join(tail)
    # Ensure it ends with terminal punctuation
    if result and result[-1] not in '.!?':
        result += '.'
    return result


def _extract_squad_roster(summary: Dict[str, Any]) -> list:
    """Collect all unique squad player names from a fight summary."""
    if not summary:
        return []
    roster = set()
    for key in ("top_damage", "top_strips", "top_cleanses",
                "top_healers", "top_cc", "top_bursts"):
        entries = summary.get(key) or []
        for entry in entries:
            if isinstance(entry, dict):
                name = entry.get("name")
                if name and isinstance(name, str):
                    roster.add(name.strip())
    return [n for n in roster if n]
