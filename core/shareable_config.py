"""Strict import/export for shareable SparkyBot guild configuration.

Guild config files intentionally contain Discord webhook credentials and a
small allowlist of Discord presentation/routing options.  They never serialize
AI, Twitch, TTS, filesystem, or other application settings.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from core.discord_bot import normalize_webhook_url


FORMAT_NAME = "sparkybot-guild-config"
FORMAT_VERSION = 1
DEFAULT_FILENAME = "sparkybot-guild-config.json"
FILE_WARNING = (
    "Contains Discord webhook credentials. Share privately and revoke the "
    "webhooks in Discord if this file is exposed."
)
MAX_FILE_BYTES = 64 * 1024
MAX_WEBHOOK_LENGTH = 2048
MAX_LABEL_LENGTH = 80

_TOP_LEVEL_KEYS = frozenset({"format", "version", "warning", "discord"})
_DISCORD_KEYS = frozenset({
    "enabled",
    "destinations",
    "active_destination",
    "raid_report_destination",
    "bot_name",
    "embed_color",
})
_DESTINATION_KEYS = frozenset({"name", "webhook_url"})
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class GuildConfigError(ValueError):
    """A guild config is unsafe, malformed, or unsupported."""


@dataclass(frozen=True)
class DiscordDestination:
    name: str
    webhook_url: str


@dataclass(frozen=True)
class GuildConfigBundle:
    enabled: bool
    destinations: tuple[DiscordDestination, DiscordDestination, DiscordDestination]
    active_destination: int
    raid_report_destination: int
    bot_name: str
    embed_color: str

    @property
    def configured_destination_count(self) -> int:
        return sum(bool(item.webhook_url) for item in self.destinations)

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT_NAME,
            "version": FORMAT_VERSION,
            "warning": FILE_WARNING,
            "discord": {
                "enabled": self.enabled,
                "destinations": [
                    {"name": item.name, "webhook_url": item.webhook_url}
                    for item in self.destinations
                ],
                "active_destination": self.active_destination,
                "raid_report_destination": self.raid_report_destination,
                "bot_name": self.bot_name,
                "embed_color": self.embed_color,
            },
        }


def _reject_unknown_keys(data: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise GuildConfigError(
            f"Unsupported {where} field(s): {', '.join(unknown)}. "
            "Only the limited guild-config allowlist can be imported."
        )


def _required_text(value: Any, field: str, *, allow_blank: bool = True) -> str:
    if not isinstance(value, str):
        raise GuildConfigError(f"{field} must be text.")
    clean = value.strip()
    if not allow_blank and not clean:
        raise GuildConfigError(f"{field} cannot be blank.")
    if len(clean) > MAX_LABEL_LENGTH:
        raise GuildConfigError(f"{field} is longer than {MAX_LABEL_LENGTH} characters.")
    return clean


def _index(value: Any, field: str, allowed: range) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in allowed:
        choices = ", ".join(str(item) for item in allowed)
        raise GuildConfigError(f"{field} must be one of: {choices}.")
    return value


def parse_guild_config(data: Any) -> GuildConfigBundle:
    """Validate an already-decoded guild config without changing application state."""
    if not isinstance(data, dict):
        raise GuildConfigError("Guild config must be a JSON object.")
    _reject_unknown_keys(data, _TOP_LEVEL_KEYS, "top-level")

    if data.get("format") != FORMAT_NAME:
        raise GuildConfigError(f"This is not a {FORMAT_NAME} file.")
    if data.get("version") != FORMAT_VERSION:
        raise GuildConfigError(
            f"Unsupported guild config version {data.get('version')!r}; "
            f"this app supports version {FORMAT_VERSION}."
        )

    discord = data.get("discord")
    if not isinstance(discord, dict):
        raise GuildConfigError("discord must be a JSON object.")
    _reject_unknown_keys(discord, _DISCORD_KEYS, "discord")

    enabled = discord.get("enabled")
    if not isinstance(enabled, bool):
        raise GuildConfigError("discord.enabled must be true or false.")

    raw_destinations = discord.get("destinations")
    if not isinstance(raw_destinations, list) or len(raw_destinations) != 3:
        raise GuildConfigError("discord.destinations must contain exactly three slots.")

    destinations: list[DiscordDestination] = []
    for number, raw in enumerate(raw_destinations, start=1):
        if not isinstance(raw, dict):
            raise GuildConfigError(f"Destination {number} must be a JSON object.")
        _reject_unknown_keys(raw, _DESTINATION_KEYS, f"destination {number}")
        name = _required_text(raw.get("name", ""), f"Destination {number} name")
        url = raw.get("webhook_url", "")
        if not isinstance(url, str):
            raise GuildConfigError(f"Destination {number} webhook_url must be text.")
        if len(url) > MAX_WEBHOOK_LENGTH:
            raise GuildConfigError(f"Destination {number} webhook URL is too long.")
        normalized = normalize_webhook_url(url)
        if normalized is None:
            raise GuildConfigError(
                f"Destination {number} is not a complete Discord webhook URL or ID/token."
            )
        destinations.append(DiscordDestination(name=name, webhook_url=normalized))

    active = _index(discord.get("active_destination"), "active_destination", range(1, 4))
    raid = _index(discord.get("raid_report_destination"), "raid_report_destination", range(0, 4))
    bot_name = _required_text(
        discord.get("bot_name", "SparkyBot"), "bot_name", allow_blank=False
    )
    color = discord.get("embed_color")
    if not isinstance(color, str) or not _COLOR_RE.fullmatch(color):
        raise GuildConfigError("embed_color must use #RRGGBB format.")
    color = color.upper()

    if enabled and not destinations[active - 1].webhook_url:
        raise GuildConfigError(
            f"Discord is enabled, but active destination {active} has no webhook URL."
        )

    return GuildConfigBundle(
        enabled=enabled,
        destinations=tuple(destinations),  # type: ignore[arg-type]
        active_destination=active,
        raid_report_destination=raid,
        bot_name=bot_name,
        embed_color=color,
    )


def load_guild_config(path: str | Path) -> GuildConfigBundle:
    """Read and validate a small UTF-8/UTF-8-BOM guild config file."""
    source = Path(path)
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise GuildConfigError(f"Could not read guild config: {exc}") from exc
    if size > MAX_FILE_BYTES:
        raise GuildConfigError(
            f"Guild config is too large ({size} bytes; maximum is {MAX_FILE_BYTES})."
        )
    try:
        data = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GuildConfigError(f"Guild config is not valid UTF-8 JSON: {exc}") from exc
    return parse_guild_config(data)


def bundle_from_config(config: Any) -> GuildConfigBundle:
    """Build a validated allowlisted bundle from the live Config object."""
    raw = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "discord": {
            "enabled": bool(config.enable_discord_bot),
            "destinations": [
                {"name": config.discord_webhook_name1, "webhook_url": config.discord_webhook},
                {"name": config.discord_webhook_name2, "webhook_url": config.discord_webhook2},
                {"name": config.discord_webhook_name3, "webhook_url": config.discord_webhook3},
            ],
            "active_destination": int(config.active_discord_webhook),
            "raid_report_destination": int(config.raid_report_discord_webhook),
            "bot_name": config.discord_webhook_label or "SparkyBot",
            "embed_color": f"#{int(config.embed_color) & 0xFFFFFF:06X}",
        },
    }
    return parse_guild_config(raw)


def apply_guild_config(config: Any, bundle: GuildConfigBundle, *, persist: bool = True) -> None:
    """Apply only the allowlisted Discord fields from a validated bundle."""
    keys = (
        ("discordWebhook", bundle.destinations[0].webhook_url),
        ("discordWebhook2", bundle.destinations[1].webhook_url),
        ("discordWebhook3", bundle.destinations[2].webhook_url),
        ("discordWebhookName1", bundle.destinations[0].name),
        ("discordWebhookName2", bundle.destinations[1].name),
        ("discordWebhookName3", bundle.destinations[2].name),
        ("discordWebhookLabel", bundle.bot_name),
        ("activeDiscordWebhook", str(bundle.active_destination)),
        ("raidReportDiscordWebhook", str(bundle.raid_report_destination)),
        ("enableDiscordBot", str(bundle.enabled).lower()),
        ("embedColor", f"0x{bundle.embed_color[1:]}"),
    )
    for key, value in keys:
        config.update("Discord", key, value)
    if persist:
        if not config.save():
            raise GuildConfigError("SparkyBot could not save the imported guild config.")
    else:
        # First-run imports remain in memory until the wizard is completed;
        # cancelling the wizard must not leave a partial config on disk.
        config._load_values()


def write_guild_config(config: Any, path: str | Path) -> GuildConfigBundle:
    """Atomically export an allowlisted bundle with owner-only permissions."""
    bundle = bundle_from_config(config)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(bundle.as_dict(), indent=2, ensure_ascii=False) + "\n"

    temp_name: str | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
        temp_name = None
        try:
            target.chmod(0o600)
        except OSError:
            pass  # Windows ACLs, rather than POSIX mode bits, govern access.
    except OSError as exc:
        raise GuildConfigError(f"Could not export guild config: {exc}") from exc
    finally:
        if temp_name is not None:
            try:
                Path(temp_name).unlink()
            except OSError:
                pass
    return bundle
