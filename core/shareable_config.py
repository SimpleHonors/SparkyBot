"""Strict import/export for shareable SparkyBot guild configuration.

Guild config files intentionally contain Discord webhook credentials and a
small allowlist of Discord presentation/routing options.  They never serialize
AI, Twitch, TTS, filesystem, or other application settings.
"""

from __future__ import annotations

import copy
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
    "To use this file: open SparkyBot and choose File > Use Guild Setup File. "
    "Keep it private because it contains working Discord webhook links; if "
    "it leaks, delete those webhooks in Discord."
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

    def destination_name(self, index: int) -> str:
        """Human-readable name for a one-based destination slot."""
        item = self.destinations[index - 1]
        return item.name or f"Destination {index}"

    @property
    def fight_destination_name(self) -> str:
        return self.destination_name(self.active_destination)

    @property
    def nightly_destination_name(self) -> str:
        index = self.raid_report_destination or self.active_destination
        return self.destination_name(index)

    def routing_summary(self) -> str:
        return (
            f"Individual fight reports → {self.fight_destination_name}\n"
            f"End-of-night debrief and logs → {self.nightly_destination_name}"
        )

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
            "SparkyBot couldn't safely read this setup file. Ask your guild "
            f"admin to create a fresh one. Details: unsupported {where} "
            f"field(s): {', '.join(unknown)}."
        )


def _required_text(value: Any, field: str, *, allow_blank: bool = True) -> str:
    if not isinstance(value, str):
        raise GuildConfigError(f"{field} must be text.")
    clean = value.strip()
    if not allow_blank and not clean:
        raise GuildConfigError(f"{field} cannot be blank.")
    if len(clean) > MAX_LABEL_LENGTH:
        raise GuildConfigError(f"{field} is longer than {MAX_LABEL_LENGTH} characters.")
    try:
        clean.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise GuildConfigError(f"{field} contains invalid text.") from exc
    return clean


def _ensure_encodable(value: str, field: str) -> None:
    """Reject JSON lone-surrogate escapes before any config mutation."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise GuildConfigError(f"{field} contains invalid text.") from exc


def _index(value: Any, field: str, allowed: range) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in allowed:
        choices = ", ".join(str(item) for item in allowed)
        raise GuildConfigError(f"{field} must be one of: {choices}.")
    return value


def parse_guild_config(data: Any) -> GuildConfigBundle:
    """Validate an already-decoded guild config without changing application state."""
    if not isinstance(data, dict):
        raise GuildConfigError(
            "This is not a SparkyBot setup file. Ask your guild admin to "
            "create a fresh one in SparkyBot."
        )
    if data.get("format") != FORMAT_NAME:
        raise GuildConfigError(
            "This is not a SparkyBot setup file. Ask your guild admin to "
            "create a fresh one in SparkyBot."
        )
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise GuildConfigError(
            "This SparkyBot setup file has an invalid version. Ask your guild "
            "admin to create a fresh one."
        )
    if version != FORMAT_VERSION:
        raise GuildConfigError(
            "This setup file came from a different version of SparkyBot. "
            "Update SparkyBot, then try the file again. "
            f"File version: {version}; supported version: {FORMAT_VERSION}."
        )
    # Identify the file and version first so wrong/newer files get useful
    # guidance instead of a schema-internals error.
    _reject_unknown_keys(data, _TOP_LEVEL_KEYS, "top-level")

    warning = data.get("warning")
    if warning is not None:
        if not isinstance(warning, str):
            raise GuildConfigError("warning must be text when present.")
        try:
            warning.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise GuildConfigError("warning contains invalid text.") from exc

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
        _ensure_encodable(url, f"Destination {number} webhook URL")
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
    if enabled and raid and not destinations[raid - 1].webhook_url:
        raise GuildConfigError(
            f"Discord is enabled, but end-of-night destination {raid} has no webhook URL."
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
        with source.open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise GuildConfigError(
            "SparkyBot couldn't open this setup file. Ask your guild admin "
            f"to send it again. Details: {exc}"
        ) from exc
    if len(raw) > MAX_FILE_BYTES:
        raise GuildConfigError(
            f"Guild config is too large (maximum is {MAX_FILE_BYTES} bytes)."
        )
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GuildConfigError(
            "SparkyBot couldn't read this setup file. Ask your guild admin "
            f"to create a fresh one. Details: {exc}"
        ) from exc
    return parse_guild_config(data)


def bundle_from_config(config: Any) -> GuildConfigBundle:
    """Build a validated allowlisted bundle from the live Config object."""
    if not any((
        config.discord_webhook,
        config.discord_webhook2,
        config.discord_webhook3,
    )):
        raise GuildConfigError(
            "There's nothing to put in a setup file yet. Add a Discord "
            "webhook in Settings, then try again."
        )
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
    snapshot = copy.deepcopy(config._config)
    try:
        for key, value in keys:
            config.update("Discord", key, value)
        if persist:
            if not config.save():
                raise GuildConfigError(
                    "SparkyBot could not save the imported guild setup."
                )
        else:
            # First-run imports remain in memory until the wizard is completed;
            # cancelling the wizard must not leave a partial config on disk.
            config._load_values()
    except Exception as exc:
        config._config = snapshot
        config._load_values()
        if isinstance(exc, GuildConfigError):
            raise
        raise GuildConfigError(f"SparkyBot could not apply the guild setup: {exc}") from exc


def write_guild_config(config: Any, path: str | Path) -> GuildConfigBundle:
    """Atomically export an allowlisted bundle with owner-only permissions."""
    bundle = bundle_from_config(config)
    if bundle.configured_destination_count == 0:
        raise GuildConfigError(
            "There's nothing to put in a setup file yet. Add a Discord "
            "webhook in Settings, then try again."
        )
    target = Path(path)
    payload = json.dumps(bundle.as_dict(), indent=2, ensure_ascii=False) + "\n"

    temp_name: str | None = None
    fd: int | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        handle = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
        fd = None  # handle owns and closes the descriptor from here.
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
        temp_name = None
        try:
            target.chmod(0o600)
        except OSError:
            pass  # Windows ACLs, rather than POSIX mode bits, govern access.
    except (OSError, UnicodeError) as exc:
        raise GuildConfigError(f"Could not export guild config: {exc}") from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if temp_name is not None:
            try:
                Path(temp_name).unlink()
            except OSError:
                pass
    return bundle
