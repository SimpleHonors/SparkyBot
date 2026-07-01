"""Newer OpenAI/Azure models reject request params that every other
OpenAI-compatible endpoint accepts:

  * 'max_tokens' -> must use 'max_completion_tokens'
  * a custom 'temperature' (e.g. 0.7) -> only the default is allowed

We can't tell from the model name (Azure deployment names are opaque), so the
analyst learns from the 400, swaps the payload, retries, and caches the fix for
the rest of the session -- instead of a blanket rename that would break older
models and non-OpenAI providers.

Regression guard for jackpoz's report:
    "Unsupported parameter: 'max_tokens' is not supported with this model.
     Use 'max_completion_tokens' instead."
"""
import copy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core.fight_analyst import FightAnalyst


# --- pure-logic unit tests -------------------------------------------------

def _bare_analyst():
    fa = FightAnalyst.__new__(FightAnalyst)
    fa._use_max_completion_tokens = False
    fa._drop_temperature = False
    return fa


def test_token_budget_key_tracks_learned_flag():
    fa = _bare_analyst()
    assert fa._token_budget_key() == "max_tokens"
    fa._use_max_completion_tokens = True
    assert fa._token_budget_key() == "max_completion_tokens"


def test_remediate_renames_max_tokens():
    fa = _bare_analyst()
    payload = {"max_tokens": 350, "temperature": 0.7}
    body = ("Unsupported parameter: 'max_tokens' is not supported with this "
            "model. Use 'max_completion_tokens' instead.")
    assert fa._remediate_unsupported_param(body, payload) is True
    assert "max_tokens" not in payload
    assert payload["max_completion_tokens"] == 350       # budget preserved
    assert fa._use_max_completion_tokens is True
    # temperature untouched: this 400 didn't mention it
    assert payload["temperature"] == 0.7


def test_remediate_drops_unsupported_temperature():
    fa = _bare_analyst()
    payload = {"max_tokens": 350, "temperature": 0.7}
    body = ("Unsupported value: 'temperature' does not support 0.7 with this "
            "model. Only the default (1) value is supported.")
    assert fa._remediate_unsupported_param(body, payload) is True
    assert "temperature" not in payload
    assert fa._drop_temperature is True
    # token key not touched by a temperature-only error
    assert payload["max_tokens"] == 350


def test_remediate_is_idempotent_and_reports_no_further_change():
    """Once a fix is learned, seeing the same error again changes nothing
    (prevents an infinite retry loop)."""
    fa = _bare_analyst()
    payload = {"max_completion_tokens": 350}   # already swapped
    fa._use_max_completion_tokens = True
    body = "Use 'max_completion_tokens' instead."
    assert fa._remediate_unsupported_param(body, payload) is False


def test_remediate_ignores_unrelated_400():
    fa = _bare_analyst()
    payload = {"max_tokens": 350, "temperature": 0.7}
    body = "Invalid 'messages': the array must not be empty."
    assert fa._remediate_unsupported_param(body, payload) is False
    assert payload == {"max_tokens": 350, "temperature": 0.7}


# --- integration test through analyze() ------------------------------------

class _FakeResponse:
    def __init__(self, status_code, *, text="", json_body=None):
        self.status_code = status_code
        self.text = text
        self._json = json_body or {}

    def json(self):
        return self._json


def _ok_body():
    return {
        "choices": [{"message": {"content": "gg ez"}, "finish_reason": "stop"}],
        "usage": {"completion_tokens": 20},
    }


def _stub_analyst_for_analyze():
    """A FightAnalyst wired just enough to reach and drive the request loop."""
    fa = FightAnalyst.__new__(FightAnalyst)
    fa.base_url = "https://my-azure.openai.azure.com"
    fa.api_key = "k"
    fa.model = "prod-chat"          # opaque Azure deployment name -> name heuristics can't help
    fa.max_tokens = 350
    fa.thinking = True
    fa.prompt_version = "v2"
    fa.session_history = None
    fa.vocab_tracker = None
    fa.vocab_config = None
    fa._use_max_completion_tokens = False
    fa._drop_temperature = False

    # Short-circuit the heavy collaborators.
    fa._build_prompt = lambda *a, **k: ("user prompt", [])
    fa._build_system_prompt = lambda *a, **k: "system prompt"
    fa._write_debug_request = lambda *a, **k: None
    fa._apply_provider_overrides = lambda payload: None
    fa._handle_success = lambda data, *a, **k: data["choices"][0]["message"]["content"]

    class _Guard:
        def is_silent_failure(self, *a, **k):
            return False
    fa._silent_guard = _Guard()
    return fa


def test_analyze_recovers_from_max_tokens_then_temperature_400(monkeypatch):
    import core.fight_analyst as fa_mod

    fa = _stub_analyst_for_analyze()

    sent_payloads = []
    responses = iter([
        _FakeResponse(400, text="Unsupported parameter: 'max_tokens' is not "
                                "supported with this model. Use "
                                "'max_completion_tokens' instead."),
        _FakeResponse(400, text="Unsupported value: 'temperature' does not "
                                "support 0.7 with this model."),
        _FakeResponse(200, json_body=_ok_body()),
    ])

    def fake_post(endpoint, headers=None, json=None, timeout=None):
        sent_payloads.append(copy.deepcopy(json))   # payload is mutated in place
        return next(responses)

    monkeypatch.setattr(fa_mod, "requests", type("R", (), {
        "post": staticmethod(fake_post),
        "Timeout": fa_mod.requests.Timeout,
        "ConnectionError": fa_mod.requests.ConnectionError,
    }))
    monkeypatch.setattr(fa_mod.time, "sleep", lambda *_: None)

    result = fa.analyze({"outcome": "win"}, timeout=5)

    assert result == "gg ez", "analyze should recover and return commentary"
    assert len(sent_payloads) == 3, "expected 400(max_tokens) -> 400(temp) -> 200"

    # First attempt used the legacy shape...
    assert "max_tokens" in sent_payloads[0]
    assert "temperature" in sent_payloads[0]
    # ...final attempt used the adapted shape.
    final = sent_payloads[-1]
    assert final.get("max_completion_tokens") == 350
    assert "max_tokens" not in final
    assert "temperature" not in final

    # Fixes cached on the instance for the rest of the session.
    assert fa._use_max_completion_tokens is True
    assert fa._drop_temperature is True


def test_silent_failure_retry_writes_learned_token_key(monkeypatch):
    """Regression (bura review, Low): once the token key is swapped to
    'max_completion_tokens', the silent-failure retry must lower the budget on
    THAT key -- it must not reintroduce 'max_tokens'."""
    import core.fight_analyst as fa_mod

    fa = _stub_analyst_for_analyze()
    fa._use_max_completion_tokens = True          # swap already learned this session

    # First 200 is a silent failure (empty content, ate the budget); second is real.
    class _Guard:
        fallback_token_limit = 400

        def __init__(self):
            self._calls = 0

        def is_silent_failure(self, *a, **k):
            self._calls += 1
            return self._calls == 1               # only the first response is silent

        def handle_failure(self, *a, **k):
            return "", "retry_required"           # force the reduced-budget retry
    fa._silent_guard = _Guard()

    sent_payloads = []
    responses = iter([
        _FakeResponse(200, json_body={
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
            "usage": {"completion_tokens": 349},
        }),
        _FakeResponse(200, json_body=_ok_body()),
    ])

    def fake_post(endpoint, headers=None, json=None, timeout=None):
        sent_payloads.append(copy.deepcopy(json))
        return next(responses)

    monkeypatch.setattr(fa_mod, "requests", type("R", (), {
        "post": staticmethod(fake_post),
        "Timeout": fa_mod.requests.Timeout,
        "ConnectionError": fa_mod.requests.ConnectionError,
    }))
    monkeypatch.setattr(fa_mod.time, "sleep", lambda *_: None)

    result = fa.analyze({"outcome": "win"}, timeout=5)

    assert result == "gg ez"
    assert len(sent_payloads) == 2, "expected a silent-failure retry"
    retry = sent_payloads[1]
    assert retry.get("max_completion_tokens") == 400, "retry must lower the LEARNED key"
    assert "max_tokens" not in retry, "retry must not reintroduce the rejected 'max_tokens'"
