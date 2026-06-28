"""
Regression tests for the 5-bug prompt-quality cluster:

  A) DEFAULT_PROMPT_VERSION guard — version 4 is current default
  B) VocabularyTracker store path — stable, not cwd-relative
  C) Player mention cooldown — threshold 2 (not 3); path matches tracker
  D) Tag distance stat — only injected for LOOSE/SCATTERED grades
  E) AI-slop templates — mandatory ALL-CAPS and 'that is not a story' removed
"""

import json
import time
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers — import only the bits under test (no PyQt6 required)
# ---------------------------------------------------------------------------

from core.ai_analyst import (
    DEFAULT_PROMPT_VERSION,
    TAG_DISTANCE_EXCELLENT,
    TAG_DISTANCE_ACCEPTABLE,
    TAG_DISTANCE_LOOSE,
    VocabularyConfig,
    VocabularyTracker,
    FightAnalyst,
)


# ---------------------------------------------------------------------------
# A: version guard
# ---------------------------------------------------------------------------

class TestVersionGuard:
    def test_default_prompt_version_is_4(self):
        """Prod runs aiPromptVersion=4; DEFAULT_PROMPT_VERSION must equal 4
        so the guard `if ai_prompt_version < DEFAULT_PROMPT_VERSION` fires
        correctly for users still on version < 4."""
        assert DEFAULT_PROMPT_VERSION == 4, (
            f"Expected DEFAULT_PROMPT_VERSION=4, got {DEFAULT_PROMPT_VERSION}. "
            "This guard controls when users on custom prompts get the upgrade notification."
        )

    def test_version_4_in_changelog(self):
        from core.ai_analyst import DEFAULT_PROMPT_CHANGELOG
        assert 4 in DEFAULT_PROMPT_CHANGELOG, "Version 4 changelog entry is missing"


# ---------------------------------------------------------------------------
# B: stable store path
# ---------------------------------------------------------------------------

class TestVocabTrackerPath:
    def test_default_path_is_stable_across_dirs(self, tmp_path, monkeypatch):
        """VocabularyTracker store_path must not shift when the working directory
        changes. If it uses Path.cwd(), launching the app from a different folder
        produces a different path and history is silently lost."""
        import os
        tracker_from_project = VocabularyTracker()
        # Change cwd to an unrelated temp dir and re-construct
        monkeypatch.chdir(tmp_path)
        tracker_from_tmp = VocabularyTracker()
        assert tracker_from_project.store_path == tracker_from_tmp.store_path, (
            "store_path changed when cwd changed — path is still cwd-relative"
        )

    def test_vocab_config_path_is_stable_across_dirs(self, tmp_path, monkeypatch):
        """VocabularyConfig config_path must not shift when cwd changes."""
        vc_from_project = VocabularyConfig()
        monkeypatch.chdir(tmp_path)
        vc_from_tmp = VocabularyConfig()
        assert vc_from_project.config_path == vc_from_tmp.config_path, (
            "config_path changed when cwd changed — path is still cwd-relative"
        )

    def test_tracker_persists_and_reloads(self, tmp_path):
        """Data written by one tracker instance is readable by a fresh instance
        pointed at the same path — i.e. the _save/_load round-trip works."""
        store = tmp_path / "vocab_test.json"
        vc = VocabularyConfig()
        t1 = VocabularyTracker(store_path=store, vocab_config=vc)

        t1._events.append({"term": "HOLY SHIT", "ts": time.time()})
        t1._save()

        t2 = VocabularyTracker(store_path=store, vocab_config=vc)
        assert any(e["term"] == "HOLY SHIT" for e in t2._events)


# ---------------------------------------------------------------------------
# C: player cooldown threshold = 2
# ---------------------------------------------------------------------------

class TestPlayerCooldown:
    def _make_tracker_with_player(self, name: str, count: int, tmp_path: Path):
        store = tmp_path / "vocab.json"
        vc = VocabularyConfig()
        tracker = VocabularyTracker(store_path=store, vocab_config=vc)
        now = time.time()
        for _ in range(count):
            tracker._player_events.append({"name": name, "ts": now - 10})
        return tracker

    def test_player_suppressed_after_two_mentions(self, tmp_path):
        """Player should appear in suppression guidance after 2 mentions
        (threshold lowered from 3 to 2)."""
        tracker = self._make_tracker_with_player("Taeyeon", 2, tmp_path)
        guide = tracker._build_player_suppression_guidance()
        assert "Taeyeon" in guide, (
            "Taeyeon should be suppressed after 2 mentions but wasn't. "
            "Check that threshold is 2, not 3."
        )

    def test_player_not_suppressed_after_one_mention(self, tmp_path):
        """A single mention should NOT trigger suppression — threshold is 2."""
        tracker = self._make_tracker_with_player("Taeyeon", 1, tmp_path)
        guide = tracker._build_player_suppression_guidance()
        assert "Taeyeon" not in guide or guide == "", (
            "Taeyeon should not be suppressed after only 1 mention"
        )

    def test_player_cooldown_path_matches_tracker_path(self):
        """VocabularyTracker store_path must be stable so player events
        actually accumulate across sessions (same path fix as B)."""
        t1 = VocabularyTracker()
        t2 = VocabularyTracker()
        assert t1.store_path == t2.store_path, (
            "Two default-path trackers point at different files — path is not stable"
        )


# ---------------------------------------------------------------------------
# D: distance-from-tag gated to extreme only
# ---------------------------------------------------------------------------

class TestTagDistanceGating:
    """_pre_analyze must NOT inject a tag discipline line for EXCELLENT or
    ACCEPTABLE spreads. Only LOOSE (>= TAG_DISTANCE_LOOSE) should appear."""

    def _make_summary(self, median_distance: float, outcome: str = "Win") -> dict:
        """Build a minimal fight summary with a synthetic squad_tag_distance list."""
        n = 10
        # Place all players at exactly median_distance so median == median_distance
        players = [{"name": f"Player{i}", "distance": median_distance} for i in range(n)]
        return {
            "outcome": outcome,
            "friendly_count": 30,
            "enemy_count": 30,
            "squad_count": 30,
            "squad_deaths": 5,
            "squad_downs": 8,
            "enemy_deaths": 10,
            "squad_cleanses": 100,
            "squad_strips": 50,
            "squad_tag_distance": players,
            "top_damage": [],
            "top_strips": [],
            "top_cleanses": [],
            "top_healers": [],
            "top_cc": [],
            "top_bursts": [],
            "outliers": {},
        }

    def test_excellent_spread_no_tag_line(self):
        summary = self._make_summary(TAG_DISTANCE_EXCELLENT - 100)
        result = FightAnalyst._pre_analyze(summary, set())
        assert "Tag discipline" not in result["analysis"], (
            "Tag discipline should NOT be reported for excellent spread"
        )

    def test_acceptable_spread_no_tag_line(self):
        summary = self._make_summary(TAG_DISTANCE_ACCEPTABLE - 100)
        result = FightAnalyst._pre_analyze(summary, set())
        assert "Tag discipline" not in result["analysis"], (
            "Tag discipline should NOT be reported for acceptable spread"
        )

    def test_loose_spread_tag_line_present(self):
        summary = self._make_summary(TAG_DISTANCE_LOOSE + 100)
        result = FightAnalyst._pre_analyze(summary, set())
        assert "Tag discipline" in result["analysis"], (
            "Tag discipline SHOULD be reported for loose spread"
        )

    def test_scattered_spread_tag_line_present(self):
        summary = self._make_summary(TAG_DISTANCE_LOOSE * 2, outcome="Loss")
        result = FightAnalyst._pre_analyze(summary, set())
        assert "Tag discipline" in result["analysis"], (
            "Tag discipline SHOULD be reported for scattered spread"
        )


# ---------------------------------------------------------------------------
# E: AI-slop templates removed from prompt
# ---------------------------------------------------------------------------

class TestAISlop:
    def test_no_mandatory_all_caps_in_format_active_terms(self):
        """The 'Freestyle inventions should be written in ALL CAPS' mandate
        must be gone. It caused the model to shout every invented phrase."""
        vc = VocabularyConfig()
        active = vc.roll_active_terms(set())
        output = FightAnalyst._format_active_terms(active)
        assert "Freestyle inventions should be written in ALL CAPS" not in output, (
            "Mandatory ALL-CAPS instruction is still present in _format_active_terms"
        )

    def test_no_that_is_not_a_story_in_mood(self):
        """The 'that is not a story' construction in the blowout mood text
        must be removed — it was teaching the model to use this template."""
        summary = {
            "outcome": "Decisive Win",
            "friendly_count": 40,
            "enemy_count": 18,   # ~2.2x advantage triggers the blowout branch
            "squad_count": 40,
            "squad_deaths": 2,
            "squad_downs": 5,
            "enemy_deaths": 15,
            "squad_cleanses": 100,
            "squad_strips": 50,
            "squad_tag_distance": [],
            "top_damage": [],
            "top_strips": [],
            "top_cleanses": [],
            "top_healers": [],
            "top_cc": [],
            "top_bursts": [],
            "outliers": {},
        }
        result = FightAnalyst._pre_analyze(summary, set())
        mood = result.get("mood", "")
        assert "that is not a story" not in mood, (
            "'that is not a story' template is still in the blowout mood directive"
        )
        # Also check gratuitous ALL-CAPS "POORLY" is gone
        assert "POORLY" not in mood, (
            "Gratuitous ALL-CAPS 'POORLY' is still in the blowout mood directive"
        )
