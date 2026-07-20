"""_get_ai_components must initialize without NameError.

Regression for the live 2026-07-19 failure: main._get_ai_components()
called app_dir() without importing it. No test walked this path (it
only runs when the AI stage fires on a real fight), so the break
reached the operator's live run.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))


def test_get_ai_components_initializes(monkeypatch, tmp_path):
    import main
    import core.ai_analyst as ai_analyst
    import core.callout_cooldown as callout_cooldown
    import core.apppaths as apppaths

    class _Stub:
        def __init__(self, *a, **kw):
            self.args, self.kwargs = a, kw

    monkeypatch.setattr(ai_analyst, "VocabularyConfig", _Stub)
    monkeypatch.setattr(ai_analyst, "VocabularyTracker", _Stub)
    monkeypatch.setattr(ai_analyst, "SessionHistoryTracker", _Stub)
    monkeypatch.setattr(callout_cooldown, "CalloutCooldown", _Stub)
    monkeypatch.setattr(apppaths, "app_dir", lambda: tmp_path)

    # Force fresh init even if another test already populated the globals
    monkeypatch.setattr(main, "_vocab_config", None)
    monkeypatch.setattr(main, "_vocab_tracker", None)
    monkeypatch.setattr(main, "_session_history", None)
    monkeypatch.setattr(main, "_callout_cooldown", None)

    vocab_config, vocab_tracker, session_history = main._get_ai_components()

    assert vocab_config is not None
    assert vocab_tracker is not None
    assert session_history is not None
    cooldown = main._callout_cooldown
    assert cooldown.kwargs["state_path"] == tmp_path / "sparkybot_callout_cooldown.json"
