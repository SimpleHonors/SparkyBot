"""Shared Raid Report progress weights and operator-facing messages."""

_STAGE_BASE = {
    "plan": 0,
    "parse": 3,
    "collect": 58,
    "resolve": 61,
    "combine": 64,
    "augment": 76,
    "view": 79,
    "bake": 96,
}
_STAGE_SPAN = {
    "plan": 3,
    "parse": 55,
    "collect": 3,
    "resolve": 3,
    "combine": 12,
    "augment": 3,
    "view": 17,
    "bake": 4,
}


def progress_state(stage: str, current: int, total: int) -> tuple[int, str]:
    """Return capped percent and an honest message for a pipeline stage."""
    base = _STAGE_BASE.get(stage, 0)
    span = _STAGE_SPAN.get(stage, 0)
    fraction = (current / total) if total > 0 else 0
    percent = min(int(base + span * fraction), 99)
    messages = {
        "plan": "Checking saved fight data…",
        "parse": f"Reading fight {current} of {total}…",
        "collect": "Preparing fight data…",
        "resolve": "Preparing the report viewer…",
        "combine": "Combining fight stats…",
        "augment": "Adding report details…",
        "view": "Building player and enemy analysis…",
        "bake": "Finishing the report…",
    }
    return percent, messages.get(stage, "")
