"""Discord webhook integration for sending fight reports"""

import io
import json
import logging
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Optional, Dict, Any, List
import requests

logger = logging.getLogger(__name__)

MAX_TOTAL_CHARS = 5900  # Safe margin under Discord's 6000 total embed char limit
MAX_EMBEDS_PER_POST = 10  # Discord caps at 10 embeds per message

_DISCORD_WEBHOOK_RE = re.compile(
    r"^https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[^/\s?#]+(?:\?[^\s#]*)?$",
    re.IGNORECASE,
)
_DISCORD_WEBHOOK_SHORTHAND_RE = re.compile(
    r"^(\d+)/([^/\s?#]+(?:\?[^\s#]*)?)$",
    re.IGNORECASE,
)


def normalize_webhook_url(url: str) -> Optional[str]:
    """Normalize a full URL or Discord's ``ID/token`` shorthand.

    A raw token cannot be expanded because the webhook ID is not encoded in it.
    """
    value = (url or "").strip()
    if not value:
        return ""
    if _DISCORD_WEBHOOK_RE.fullmatch(value):
        return value
    shorthand = _DISCORD_WEBHOOK_SHORTHAND_RE.fullmatch(value)
    if shorthand:
        return f"https://discord.com/api/webhooks/{shorthand.group(1)}/{shorthand.group(2)}"
    return None


def validate_webhook_url(url: str) -> bool:
    """Return True for a blank slot or a complete Discord webhook URL."""
    return normalize_webhook_url(url) is not None


def _embed_char_count(embed: Dict[str, Any]) -> int:
    """Count the characters Discord applies to its per-message embed limit."""
    total = len(embed.get("description", "")) + len(embed.get("title", ""))
    total += len(embed.get("author", {}).get("name", ""))
    total += len(embed.get("footer", {}).get("text", ""))
    for field in embed.get("fields", []):
        total += len(field.get("name", "")) + len(field.get("value", ""))
    return total


def _truncate_field_value(value: str, limit: int) -> str:
    """Shorten one field at a line boundary while preserving code fences."""
    if len(value) <= limit:
        return value

    marker = "… condensed; full details in linked report"
    if limit <= len(marker):
        return marker[:max(1, limit)]

    if value.startswith("```"):
        first_newline = value.find("\n")
        opening = value[:first_newline] if first_newline >= 0 else "```"
        body = value[first_newline + 1:] if first_newline >= 0 else value[3:]
        if body.endswith("```"):
            body = body[:-3].rstrip("\n")
        fixed = len(opening) + len(marker) + len("\n\n\n```")
        body_limit = max(0, limit - fixed)
        shortened = body[:body_limit]
        if "\n" in shortened and len(body) > body_limit:
            shortened = shortened.rsplit("\n", 1)[0]
        result = f"{opening}\n{shortened}\n{marker}\n```"
        return result[:limit]

    body_limit = max(0, limit - len(marker) - 1)
    shortened = value[:body_limit]
    if "\n" in shortened and len(value) > body_limit:
        shortened = shortened.rsplit("\n", 1)[0]
    return f"{shortened}\n{marker}"[:limit]


def _compact_embeds_to_one_message(embeds: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """Return a condensed copy that fits one Discord message, when possible."""
    if len(embeds) > MAX_EMBEDS_PER_POST:
        return None

    compacted = deepcopy(embeds)
    original_total = sum(_embed_char_count(embed) for embed in compacted)
    if original_total <= MAX_TOTAL_CHARS:
        return compacted

    fields = [
        field
        for embed in compacted
        for field in embed.get("fields", [])
    ]
    value_lengths = [len(field.get("value", "")) for field in fields]
    fixed_chars = original_total - sum(value_lengths)
    value_budget = MAX_TOTAL_CHARS - fixed_chars
    if not fields or value_budget < len(fields):
        return None

    allocations = [0] * len(fields)
    remaining = set(range(len(fields)))
    remaining_budget = value_budget
    while remaining:
        share = remaining_budget // len(remaining)
        small = [index for index in remaining if value_lengths[index] <= share]
        if small:
            for index in small:
                allocations[index] = value_lengths[index]
                remaining_budget -= value_lengths[index]
                remaining.remove(index)
            continue

        for offset, index in enumerate(sorted(remaining)):
            allocations[index] = share + (1 if offset < remaining_budget % len(remaining) else 0)
        break

    for field, allocation in zip(fields, allocations):
        field["value"] = _truncate_field_value(field.get("value", ""), allocation)

    compacted_total = sum(_embed_char_count(embed) for embed in compacted)
    if compacted_total > MAX_TOTAL_CHARS:
        return None
    logger.info(
        "Condensed Discord fight report from %s to %s embed characters to keep one post",
        original_total,
        compacted_total,
    )
    return compacted


class DiscordBot:
    """Sends fight reports to Discord via webhooks"""

    def __init__(self, webhook_url: str, timeout: int = 30):
        self.webhook_url = webhook_url
        self.timeout = timeout

    def send_message(self, content: str = "", embeds=None, icon_path=None) -> bool:
        if not self.webhook_url:
            logger.warning("No Discord webhook configured")
            return False

        payload = {"content": content}
        if embeds:
            payload["embeds"] = embeds

        try:
            if icon_path and Path(icon_path).exists():
                icon_p = Path(icon_path)
                with open(icon_p, 'rb') as f:
                    files = {
                        'file': (icon_p.name, f, 'image/png'),
                        'payload_json': (None, json.dumps(payload), 'application/json')
                    }
                    response = requests.post(
                        self.webhook_url,
                        files=files,
                        timeout=self.timeout
                    )
            else:
                response = requests.post(
                    self.webhook_url,
                    json=payload,
                    timeout=self.timeout
                )

            if response.status_code in (200, 204):
                logger.info("Discord message sent successfully")
                return True
            else:
                logger.error(f"Discord API error: {response.status_code} - {response.text}")
                return False

        except requests.RequestException as e:
            logger.error(f"Failed to send Discord message: {e}")
            return False

    def send_file(self, file_path: Path, caption: str = "",
                  embed=None, extra_files: list | None = None) -> bool:
        """Send a file attachment to Discord, optionally with a rich embed.

        When *extra_files* is provided, each Path is attached as an additional
        file in the same multipart post (file0=file_path, file1=extra_files[0], …).
        """
        if not self.webhook_url:
            logger.warning("No Discord webhook configured")
            return False

        try:
            files: dict = {}

            with open(file_path, 'rb') as f:
                files['file'] = (file_path.name, f.read())

            if extra_files:
                for idx, ef in enumerate(extra_files, start=1):
                    with open(ef, 'rb') as efh:
                        files[f'file{idx}'] = (ef.name, efh.read())

            data: dict = {}
            if embed is not None:
                payload = {"content": caption, "embeds": [embed]}
                data['payload_json'] = json.dumps(payload)
            else:
                data['content'] = caption

            response = requests.post(
                self.webhook_url,
                data=data,
                files=files,
                timeout=self.timeout + 15
            )

            # File uploads return 200; webhook-only posts return 204
            if response.status_code in (200, 204):
                logger.info(f"File sent successfully: {file_path.name}")
                return True
            else:
                logger.error(f"Discord file upload error: {response.status_code}")
                return False

        except requests.RequestException as e:
            logger.error(f"Failed to send file: {e}")
            return False

    def send_audio(self, audio_bytes: bytes,
                   audio_filename: str = "commentary.mp3") -> bool:
        """Send an audio file as a standalone message (no embeds).

        Intended to be posted immediately after the AI commentary embed
        so the audio player renders below it in Discord.
        """
        if not self.webhook_url:
            logger.warning("No Discord webhook configured")
            return False

        files = {
            "file": (audio_filename, io.BytesIO(audio_bytes), "audio/mpeg"),
        }

        try:
            response = requests.post(
                self.webhook_url,
                files=files,
                timeout=self.timeout + 15,
            )
            if response.status_code in (200, 204):
                logger.info(f"Discord audio sent successfully ({len(audio_bytes):,} bytes)")
                return True
            else:
                logger.error(f"Discord audio send error: {response.status_code} - {response.text[:300]}")
                return False
        except requests.RequestException as e:
            logger.error(f"Failed to send Discord audio: {e}")
            return False


class DiscordWebhookManager:
    """Manages multiple Discord webhooks"""

    def __init__(self, config):
        self.config = config
        self._webhooks: Dict[int, DiscordBot] = {}

    def get_webhook(self, index: int = None) -> Optional[DiscordBot]:
        """Get DiscordBot instance for specified webhook index (cached)"""
        if index is None:
            index = self.config.active_discord_webhook

        if index not in self._webhooks:
            webhook_url = self._get_webhook_url(index)
            if webhook_url:
                self._webhooks[index] = DiscordBot(webhook_url)
            else:
                return None

        return self._webhooks[index]

    def _get_webhook_url(self, index: int) -> str:
        """Get webhook URL by index (1, 2, or 3)"""
        if index == 1:
            return self.config.discord_webhook
        elif index == 2:
            return self.config.discord_webhook2
        elif index == 3:
            return self.config.discord_webhook3
        return ""

    def send_to_all(self, message: str = "", embeds=None, icon_path=None,
                    audio_bytes: bytes = None, audio_filename: str = "sparkybot-commentary.mp3",
                    compact_single_message: bool = False) -> int:
        """Send embeds batched to stay under Discord's 6000 char total limit per POST.

        When audio_bytes is provided, the AI commentary embed is sent first
        via a normal embed POST, then the audio file is sent as a separate
        follow-up message so the player renders below the embed in Discord.
        The guild icon is attached only to the first stats batch.
        """
        webhook_urls = [self._get_webhook_url(self.config.active_discord_webhook)]
        if not webhook_urls:
            return 0

        success_count = 0

        if not embeds:
            return 0

        if compact_single_message:
            compacted = _compact_embeds_to_one_message(embeds)
            if compacted is not None:
                embeds = compacted

        # Build batches that stay under MAX_TOTAL_CHARS
        batches = []
        current_batch = []
        current_chars = 0

        for embed in embeds:
            embed_chars = _embed_char_count(embed)

            if current_batch and (current_chars + embed_chars > MAX_TOTAL_CHARS or len(current_batch) >= MAX_EMBEDS_PER_POST):
                batches.append(current_batch)
                current_batch = []
                current_chars = 0

            current_batch.append(embed)
            current_chars += embed_chars

        if current_batch:
            batches.append(current_batch)

        for webhook_url in webhook_urls:
            if not webhook_url:
                continue

            bot = DiscordBot(webhook_url)
            batch_success = True
            last_batch_idx = len(batches) - 1

            for i, batch in enumerate(batches):
                if i > 0:
                    time.sleep(0.5)

                chunk_icon = icon_path if i == 0 else None

                # Send the embed batch normally
                if not bot.send_message(message if i == 0 else "", batch, icon_path=chunk_icon):
                    batch_success = False
                    break

                # After the last batch, send audio as a separate message
                # so the player renders below the AI commentary embed.
                if audio_bytes and i == last_batch_idx:
                    time.sleep(1.0)
                    if not bot.send_audio(
                            audio_bytes=audio_bytes,
                            audio_filename=audio_filename):
                        batch_success = False
                        break

            if batch_success:
                success_count += 1

            time.sleep(0.5)

        return success_count
