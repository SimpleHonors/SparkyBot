"""Data-layer player-cooldown: when a player is on name-cooldown, redact their
individual stat rows from the LLM request so the model can't credit them — not
by name, not by class/build periphrasis ("the power amalgam"). Their numbers
still flow into the anonymous team totals, so the fight's scale stays truthful.
When the cooldown lifts, their rows return (caller stops passing their name).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core.narrative_facts import redact_players_from_summary, _TOP_ARRAYS, _build_player_sentences
from core.vocabulary_tracker import VocabularyTracker
from core.callout_cooldown import CalloutCooldown


def _summary():
    return {
        # team aggregates — must survive redaction untouched
        "squad_count": 10, "squad_kills": 50, "squad_deaths": 3,
        "squad_healing": 1_000_000, "enemy_count": 40, "outcome": "win",
        "commander": "Test Commander",
        # per-player leaderboards
        "top_damage": [
            {"name": "Test Carry", "profession": "Mechanist", "damage": 800000},
            {"name": "Other Guy", "profession": "Firebrand", "damage": 500000},
        ],
        "top_healers": [
            {"name": "Healy", "profession": "Druid", "healing": 300000},
            {"name": "Test Carry", "profession": "Mechanist", "healing": 50000},
        ],
    }


def test_redaction_removes_player_from_every_array():
    out = redact_players_from_summary(_summary(), ["Test Carry"])
    for arr in ("top_damage", "top_healers"):
        names = [e["name"] for e in out.get(arr, [])]
        assert "Test Carry" not in names, f"{arr} still lists the suppressed player"
    assert "Other Guy" in [e["name"] for e in out["top_damage"]], "innocent player dropped"
    assert "Healy" in [e["name"] for e in out["top_healers"]], "innocent player dropped"


def test_redaction_preserves_team_totals():
    out = redact_players_from_summary(_summary(), ["Test Carry"])
    assert out["squad_kills"] == 50
    assert out["squad_healing"] == 1_000_000
    assert out["enemy_count"] == 40
    assert out["outcome"] == "win"


def test_redaction_does_not_mutate_original():
    s = _summary()
    redact_players_from_summary(s, ["Test Carry"])
    assert any(e["name"] == "Test Carry" for e in s["top_damage"]), \
        "original summary was mutated — caller's data corrupted"


def test_redaction_empty_namelist_is_noop():
    s = _summary()
    out = redact_players_from_summary(s, [])
    assert [e["name"] for e in out["top_damage"]] == ["Test Carry", "Other Guy"]


def test_get_suppressed_players_returns_repeat_offenders(tmp_path):
    t = VocabularyTracker(store_path=tmp_path / "v.json")
    roster = ["Test Carry", "Other Guy", "Healy"]
    t.record_players("Test Carry hard carried that one", roster)
    t.record_players("Test Carry again, and Other Guy showed up too", roster)
    t.record_players("Test Carry once more for good measure", roster)

    sup = t.get_suppressed_players({"commander": "Test Commander"})
    assert "Test Carry" in sup, "3-mention player should be on cooldown"
    assert "Other Guy" not in sup, "1-mention player should NOT be on cooldown"


def test_get_suppressed_players_empty_when_no_history(tmp_path):
    t = VocabularyTracker(store_path=tmp_path / "v.json")
    assert t.get_suppressed_players({"commander": "Duke"}) == []


def test_v3_prompt_redacts_cooldown_player_but_keeps_team_scale(tmp_path):
    """End-to-end: a player on name-cooldown has no individual stats in the v3
    user prompt (model can't credit them by name or build), but the fight's team
    scale still shows up in the narrative facts."""
    from core.fight_analyst import FightAnalyst

    tracker = VocabularyTracker(store_path=tmp_path / "v.json")
    roster = ["Test Carry", "Other Guy"]
    for _ in range(3):  # put Test Carry on cooldown
        tracker.record_players("Test Carry hard carried again", roster)

    summary = {
        "squad_count": 50, "squad_kills": 67, "squad_deaths": 2,
        "squad_healing": 2_000_000, "friendly_count": 50, "enemy_count": 40,
        "outcome": "win", "duration_seconds": 90, "commander": "Test Commander",
        "top_damage": [
            {"name": "Test Carry", "profession": "Mechanist", "damage": 900000},
            {"name": "Other Guy", "profession": "Firebrand", "damage": 400000},
        ],
    }

    # Build a FightAnalyst without the heavy constructor; set only what
    # _build_prompt_v3 reads.
    fa = FightAnalyst.__new__(FightAnalyst)
    fa.vocab_tracker = tracker
    fa._callout_cooldown = None
    fa._pending_topic_emits = set()
    fa._pending_player_emits = set()

    empty_terms = {"shock": [], "positive": [], "negative": [], "gates": []}
    body, _ = fa._build_prompt_v3(summary, empty_terms)

    assert "Test Carry" not in body, "cooled-down player leaked into the v3 prompt"
    # team scale survives redaction (always-fire line renders friendly/enemy + squad size)
    assert "50 vs 40" in body, "team scale (50 vs 40) was lost"
    assert "of 50" in body, "squad size was lost from the team line"


def test_globally_cooled_down_player_is_skipped_not_role_named(monkeypatch):
    """Regression: a player on the global callout cooldown must be benched
    from the dominant/clutch spotlight sentences entirely -- never renamed to
    a class/build nickname ("the burst evoker") as a substitute for their
    real name. That nickname is still individually identifying and defeats
    the whole point of the cooldown; the reported symptom was exactly this
    phrase appearing across multiple fights in a row."""
    import core.narrative_facts as nf

    cooldown = CalloutCooldown(state_path=None)
    cooldown.record_global("Dena", fights=3)  # Dena is benched this fight

    summary = {
        "duration_seconds": 60,
        "top_bursts": [
            {"name": "Dena", "profession": "Elementalist", "dmg_4s": 59000},
            {"name": "Formuele", "profession": "Guardian", "dmg_4s": 40000},
        ],
    }

    # Deterministic buckets, independent of the real calibration thresholds.
    def fake_bucket_player(rec, duration_s):
        return {"burst": "dominant"} if rec["name"] == "Dena" else {"dps": "dominant"}
    monkeypatch.setattr(nf, "bucket_player", fake_bucket_player)
    monkeypatch.setattr(nf, "is_clutch", lambda buckets: False)

    sentences, names_emitted = _build_player_sentences(summary, cooldown)
    text = " ".join(sentences)

    assert "Dena" not in text, "benched player's real name leaked"
    assert "evoker" not in text.lower(), "class/build nickname substituted for the benched name"
    assert "elementalist" not in text.lower(), "profession-based periphrasis substituted for the benched name"
    assert "Formuele" in text, "the eligible (non-cooldown) player should still be named normally"
    assert names_emitted == {"Formuele"}
