"""Silent failure guard for SparkyBot LLM calls.

Detects the Kimi/K2.6 finish_reason=length + empty-content failure mode,
which consumes the full token budget producing nothing visible. Attempts
recovery via reasoning_content extraction or a forced short-format retry.

Also patches the default max_tokens from 8000 -> 2000 in the caller.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SilentFailureGuard:
    """Wraps an LLM call and handles silent-length failures gracefully."""

    # Tunable thresholds
    emptiness_threshold: float = 0.95  # completion_tokens / max_tokens to flag as "ate budget"
    fallback_token_limit: int = 400   # max_tokens for the HOT TAKE retry
    headroom_floor: int = 4000        # retry budget floor for reasoning models (never shrink below this)
    strategy_id: str = ""             # probe-configured off-switch to engage on retry
    max_retries: int = 1               # number of retry attempts before giving up

    _retry_count: int = field(default=0)

    def is_silent_failure(self, completion_tokens: int, max_tokens: int, content: str) -> bool:
        """Return True when the model ate the token budget and returned nothing."""
        if not content or not content.strip():
            return True
        budget_ratio = completion_tokens / max_tokens if max_tokens > 0 else 0
        return (
            budget_ratio >= self.emptiness_threshold
            and len(content.strip()) < 20
        )

    def extract_reasoning_fallback(self, response: dict) -> Optional[str]:
        """If the model emitted reasoning_content, grab the first 100 words as a last resort."""
        reasoning = response.get("reasoning_content", "") or response.get("thinking", "")
        if not reasoning:
            return None
        words = reasoning.split()
        if words:
            return " ".join(words[:100])
        return None

    def build_retry_prompt(self) -> str:
        """Appended to the user message on retry to force short-form output."""
        return (
            " Output the commentary now. "
            "Begin with 'HOT TAKE:' — no thinking, no preamble, "
            "just 2-3 sentences of Discord-ready text."
        )

    def handle_failure(self, response: dict, max_tokens: int) -> tuple[str, str]:
        """Attempt recovery. Returns (recovered_text, log_message)."""
        ct = response.get("usage", {}).get("completion_tokens", 0)

        # Tier 1: pull from reasoning_content if present
        fallback = self.extract_reasoning_fallback(response)
        if fallback:
            logger.warning(
                "silent_failure: recovered %d tokens from reasoning_content "
                "(%d completion, max_tokens=%d)",
                len(fallback.split()), ct, max_tokens
            )
            return fallback, "recovered_from_reasoning"

        # Tier 2: not recoverable — caller must retry with reduced max_tokens + HOT TAKE prefix
        logger.warning(
            "silent_failure: no reasoning_content fallback, "
            "completion_tokens=%d max_tokens=%d content=%r",
            ct, max_tokens, response.get("choices", [{}])[0].get("message", {}).get("content", "")[:50]
        )
        return "", "retry_required"


@dataclass
class CallResult:
    content: str
    finish_reason: str
    completion_tokens: int
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_api_response(cls, response: dict) -> "CallResult":
        choice = response.get("choices", [{}])[0]
        msg = choice.get("message", {})
        return cls(
            content=msg.get("content", ""),
            finish_reason=choice.get("finish_reason", "unknown"),
            completion_tokens=response.get("usage", {}).get("completion_tokens", 0),
        )


def guard_and_call(
    client,
    prompt: str,
    max_tokens: int = 2000,
    guard: Optional[SilentFailureGuard] = None,
) -> CallResult:
    """Call the LLM, detect silent failures, and retry if warranted.

    Returns CallResult with content (possibly empty) and a finish_reason.
    Caller is responsible for further handling (retry with different model, etc.).
    """
    if guard is None:
        guard = SilentFailureGuard()

    result = _single_call(client, prompt, max_tokens)
    if guard.is_silent_failure(result.completion_tokens, max_tokens, result.content):
        recovered, log_msg = guard.handle_failure(result.to_dict(), max_tokens)

        if log_msg == "retry_required" and guard._retry_count < guard.max_retries:
            guard._retry_count += 1
            retry_result = _single_call(
                client,
                prompt + guard.build_retry_prompt(),
                guard.fallback_token_limit,
            )
            return retry_result

        if recovered:
            result.content = recovered
            result.warnings.append("silent_failure_recovered:" + log_msg)

    return result


def _single_call(client, prompt: str, max_tokens: int) -> CallResult:
    raw = client.chat.completions.create(
        model="auto",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.7,
    )
    return CallResult.from_api_response(raw)


# Monkeypatch hook for fight_analyst.py (M7 integration point)
def patch_max_tokens_defaults(analyst_module) -> None:
    """Patch the default max_tokens in fight_analyst.py from 8000 to 2000."""
    for name in dir(analyst_module):
        obj = getattr(analyst_module, name, None)
        if isinstance(obj, (int, float)) and obj == 8000:
            setattr(analyst_module, name, 2000)
            logger.info("patched %s: 8000 -> 2000", name)
