"""Long-horizon 'signature phrase' suppression for VocabularyTracker.

Root cause this guards against: the n-gram suppression block is built only from
the last 10 recorded fights (rolling window). Stock phrases that recur on a
cadence longer than ~10 fights age out of the window, get reused, get re-flagged,
and oscillate forever — they leak across the corpus even though the model obeys
the ban ~97% of the time WHEN the phrase is in the block. Fix: once a multi-word
phrase has recurred across enough distinct fights, treat it as a signature and
keep banning it even after it leaves the rolling window.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
# The app runs with both the repo root and core/ on sys.path (core modules
# import siblings bare, e.g. `from ai_helpers import ...`).
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core.vocabulary_tracker import VocabularyTracker


def _tracker(tmp_path):
    return VocabularyTracker(store_path=tmp_path / "vocab.json")


def test_signature_phrase_survives_rolling_window(tmp_path):
    """A 3-gram seen in >=4 separate fights stays in the DO-NOT-REUSE block
    even after 12 later fights that never use it push it out of the last-10 window."""
    t = _tracker(tmp_path)

    # 4 fights that use the signature phrase, each with distinct surrounding text
    for i in range(4):
        t.record(f"Fight {i}: the squad showed poor stomp discipline near the wall.")

    # 12 fights that never mention it — more than the 10-event cap, so the
    # original phrase_events for the signature have all been evicted.
    for i in range(12):
        t.record(f"Clean fight {i}: a clean push with zero downs and fast caps everywhere.")

    guidance = t._build_phrase_guidance()
    assert "poor stomp discipline" in guidance.lower(), (
        "signature phrase aged out of the rolling window and is no longer "
        f"suppressed.\n--- guidance ---\n{guidance}"
    )


def test_one_off_phrase_is_not_treated_as_signature(tmp_path):
    """A phrase used in only 1-2 fights must NOT become a permanent signature ban
    (that would over-suppress normal varied language)."""
    t = _tracker(tmp_path)

    t.record("Fight 0: a glorious bloodbath by the broken tower happened today.")
    t.record("Fight 1: the broken tower again but otherwise totally different words here.")
    for i in range(12):
        t.record(f"Clean fight {i}: a clean push with zero downs and fast caps everywhere.")

    guidance = t._build_phrase_guidance()
    assert "glorious bloodbath" not in guidance.lower(), (
        "a 1-off phrase was wrongly promoted to a permanent signature ban"
    )


def test_ban_block_shows_more_than_old_cap_when_many_crutches_learned(tmp_path):
    """With many learned crutch phrases, the ban block must surface more than the
    old 15-entry / 8-signature cap, so high-frequency stragglers ("tight on tag")
    aren't crowded out and left unsuppressed. Requires SIGNATURE_MAX >= 16."""
    t = _tracker(tmp_path)
    # 18 fully distinct 3-gram crutches, each used in 5 separate fights -> signatures
    phrases = [f"sig{i}aaa sig{i}bbb sig{i}ccc" for i in range(18)]
    for p in phrases:
        for _ in range(5):
            t.record(f"the squad {p} near the wall today")
    # filler fights to push the originals out of the rolling 10-window
    for i in range(10):
        t.record(f"a clean unremarkable push number {i} with totally fresh wording")

    guidance = t._build_phrase_guidance()
    shown = guidance.count('" (x')  # each banned entry is rendered as "phrase" (x..)
    assert shown >= 16, f"only {shown} crutches shown — long tail still crowded out"


def test_signature_decays_after_long_disuse(tmp_path):
    """A signature the bot hasn't used in > SIGNATURE_TTL_FIGHTS fights is
    forgotten, so the phrase is eventually released for reuse (the ban list must
    not grow forever and over-constrain the bot)."""
    t = _tracker(tmp_path)

    for i in range(4):
        t.record(f"Fight {i}: the squad showed poor stomp discipline near the wall.")

    # Far more clean fights than the TTL — the signature should age out entirely.
    for i in range(t.SIGNATURE_TTL_FIGHTS + 2):
        t.record(f"Clean fight {i}: solid rotations, distinct wording number {i} here.")

    assert "poorstompdiscipline" not in t._signature_counts, (
        "stale signature was never decayed — ledger grows unbounded"
    )
    guidance = t._build_phrase_guidance()
    assert "poor stomp discipline" not in guidance.lower(), (
        "decayed signature is still being suppressed"
    )
