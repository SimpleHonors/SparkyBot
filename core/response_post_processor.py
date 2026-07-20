"""Post-processor for SparkyBot AI commentary output.

Enforces hard contracts (length, forbidden words, markdown) that the LLM
cannot reliably self-regulate. Returns the cleaned text plus a list of
lint warnings the caller may use to decide whether to retry.

Four passes in spec order; banned-words is lint-only (caller decides retry).
Pure functions, no deps beyond stdlib.
"""
from __future__ import annotations

import random
import re
from typing import Iterable

# Reasoning models (MiniMax M2.x, DeepSeek R1, etc.) sometimes emit
# <think>...</think> blocks in the response *content* instead of a separate
# channel. Strip them before any other pass — they are not user-visible text.
# Handles unterminated <think> too (when max_tokens cuts mid-thought) by
# falling back to deleting from <think> to end of string.
_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_UNCLOSED_RE = re.compile(r"<think\b[^>]*>.*\Z", re.IGNORECASE | re.DOTALL)

_PREAMBLE_RE = re.compile(
    r"^(?:here'?s|let me|i'll|sure[,.]|okay[,.]|alright)\b.*?\n",
    re.IGNORECASE | re.DOTALL,
)
# v1.7.0 — strip leading label-prefix tics like "HOT TAKE:", "TAKE:",
# "VERDICT:", "EDIT:", or any short ALL-CAPS slug followed by a colon.
# Models trained on op-ed structure love these; Discord doesn't need them.
_PREFIX_LABEL_RE = re.compile(
    r"^\s*(?:HOT\s*TAKE|TAKE|VERDICT|EDIT|TL;DR|TLDR|FINAL|UPDATE|NOTE|"
    r"ANALYSIS|SUMMARY|REPORT|RECAP|HEADLINE|HOOK)\s*[:\-—]\s*",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

_SHOULD_SUBS = ("gotta", "better", "needs to")
_BLOB_SUBS = ("meatball", "karma train", "furball", "trash compactor")
_SHOULD_RE = re.compile(r"\bshould\b", re.IGNORECASE)
_BLOB_RE = re.compile(r"\bblob\b", re.IGNORECASE)

_MD_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_MD_UNDERLINE_RE = re.compile(r"__([^_]+)__")
_MD_BLOCKQUOTE_RE = re.compile(r"^>\s+", re.MULTILINE)

WORD_CAP = 100

# v1.7.x data-leak scrub: render raw FIGHT-BUCKET tokens as natural language and
# drop data-key words (median/ratio) echoed from NARRATIVE FACTS. Deterministic,
# model-agnostic. See tests/test_post_processor.py.
_BUCKET_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
_DATA_KEY_RE = re.compile(r"\b(?:median|ratio)\b", re.IGNORECASE)
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,;:!?])")
_MULTISPACE_RE = re.compile(r"[ \t]{2,}")


def post_process(raw: str, banned_words: set[str]) -> tuple[str, list[str]]:
    """Run the four enforcement passes; return (text, lint warnings)."""
    warnings: list[str] = []
    text = raw

    # Pass 0: strip <think>...</think> reasoning traces emitted by reasoning
    # models (MiniMax M2.x, DeepSeek R1) into the content channel.
    text, thinking_stripped = _strip_thinking(text)
    if thinking_stripped:
        warnings.append("thinking_stripped")

    # Pass 1: strip preamble + leading label-prefix tics
    stripped = _PREAMBLE_RE.sub("", text, count=1)
    if stripped != text:
        warnings.append("preamble_stripped")
    text = stripped.lstrip()
    label_stripped = _PREFIX_LABEL_RE.sub("", text, count=1)
    if label_stripped != text:
        warnings.append("prefix_label_stripped")
    text = label_stripped.lstrip()

    # Pass 1.5 (v1.7.x): scrub data-shaped leaks BEFORE counting words so the cap
    # accounts for them — raw bucket tokens (HEAVY_SUPPORT -> "heavy support") and
    # data-key words (median/ratio) parroted from NARRATIVE FACTS.
    text, scrub_notes = _scrub_data_keys(text)
    warnings.extend(scrub_notes)

    # Pass 2: word-cap to 80 at sentence boundary
    text, capped = _cap_words(text, WORD_CAP)
    if capped:
        warnings.append("word_cap_applied")

    # Pass 3: forbidden-word swap
    text, swap_log = _forbidden_swap(text)
    warnings.extend(swap_log)

    # Pass 4: strip markdown
    text, md_stripped = _strip_markdown(text)
    if md_stripped:
        warnings.append("markdown_stripped")

    # Lint-only banned-words check
    if banned_words:
        leaked = _leaked_banned(text, banned_words)
        if leaked:
            warnings.append("banned_words_leaked:" + ",".join(sorted(leaked)))

    return text.strip(), warnings


def _cap_words(text: str, cap: int) -> tuple[str, bool]:
    """Truncate at the last full sentence that fits within `cap` words."""
    sentences = _SENTENCE_SPLIT_RE.split(text)
    accumulated: list[str] = []
    word_count = 0
    overflowed = False
    for sentence in sentences:
        words = sentence.split()
        if not words:
            continue
        if word_count + len(words) <= cap:
            accumulated.append(sentence)
            word_count += len(words)
            continue
        overflowed = True
        break
    if not accumulated and sentences and sentences[0].split():
        # Single overlong sentence with no prior content — hard cut
        return " ".join(sentences[0].split()[:cap]), True
    return " ".join(accumulated).strip(), overflowed


def _strip_thinking(text: str) -> tuple[str, bool]:
    """Remove <think>...</think> blocks (and unterminated <think>... tails)."""
    original = text
    text = _THINK_BLOCK_RE.sub("", text)
    text = _THINK_UNCLOSED_RE.sub("", text)
    return text.strip(), text != original


def _forbidden_swap(text: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    new_text, n_should = _SHOULD_RE.subn(
        lambda _m: random.choice(_SHOULD_SUBS), text
    )
    if n_should:
        notes.append(f"should_swapped:{n_should}")
    new_text, n_blob = _BLOB_RE.subn(
        lambda _m: random.choice(_BLOB_SUBS), new_text
    )
    if n_blob:
        notes.append(f"blob_swapped:{n_blob}")
    return new_text, notes


def _strip_markdown(text: str) -> tuple[str, bool]:
    original = text
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_UNDERLINE_RE.sub(r"\1", text)
    text = _MD_BLOCKQUOTE_RE.sub("", text)
    return text, text != original


def _scrub_data_keys(text: str) -> tuple[str, list[str]]:
    """Render raw bucket tokens as natural language; drop echoed data-key words.

    HEAVY_SUPPORT -> "heavy support"; THEY_OUTNUMBERED_US_HARD -> "they
    outnumbered us hard". The capitalized underscored form is a data-key shape
    and must never reach Discord. "median"/"ratio" are field words the prompt
    forbids ("speak the values, not the keys") but the model still parrots.
    """
    notes: list[str] = []
    new_text, n_tok = _BUCKET_TOKEN_RE.subn(
        lambda m: m.group(0).replace("_", " ").lower(), text
    )
    if n_tok:
        notes.append(f"bucket_token_rendered:{n_tok}")
    new_text, n_key = _DATA_KEY_RE.subn("", new_text)
    if n_key:
        notes.append(f"data_key_stripped:{n_key}")
        new_text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", new_text)
        new_text = _MULTISPACE_RE.sub(" ", new_text)
    return new_text.strip(), notes


def _leaked_banned(text: str, banned: Iterable[str]) -> set[str]:
    """Return any banned words found in text (case-insensitive, whole-word)."""
    lower = text.lower()
    return {w for w in banned if re.search(rf"\b{re.escape(w.lower())}\b", lower)}
