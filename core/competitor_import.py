"""Safe, consent-gated imports from other ArcDPS/WvW log tools.

Only values with a confirmed SparkyBot equivalent are read: machine-local
paths, Discord destinations, and safe preferences such as fight thresholds or
window behavior. Competitor programs are never executed and their API keys,
bot tokens, passwords, account data, and upload history are deliberately
ignored.
"""

from __future__ import annotations

import configparser
import copy
import json
import os
import re
import sqlite3
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence
from urllib.parse import quote

from core.arcdps_config import select_wvw_log_directory
from core.discord_bot import normalize_webhook_url


MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_DATABASE_BYTES = 32 * 1024 * 1024
MAX_DISCOVERY_ENTRIES = 4_000
MAX_DISCOVERY_DEPTH = 3
MAX_VALUE_CHARS = 4_096

WebhookRole = Literal["fight", "nightly", "unknown"]


class CompetitorConfigError(ValueError):
    """A selected file is unsafe, unreadable, or not a supported config."""


@dataclass(frozen=True)
class ImportedWebhook:
    """A validated Discord destination; its URL is never shown in summaries."""

    name: str
    url: str = field(repr=False)
    role: WebhookRole = "unknown"
    preferred: bool = True

    @property
    def display_name(self) -> str:
        return self.name.strip() or "Discord destination"


@dataclass(frozen=True)
class ImportedSetting:
    """One non-secret, validated preference with a SparkyBot equivalent."""

    section: str
    key: str
    value: str
    label: str

    @property
    def display_value(self) -> str:
        if self.value == "true":
            return "On"
        if self.value == "false":
            return "Off"
        if self.key == "embedColor" and self.value.lower().startswith("0x"):
            return f"#{self.value[2:].upper()}"
        return self.value


@dataclass(frozen=True)
class CompetitorFinding:
    app: str
    source_files: tuple[Path, ...]
    log_folders: tuple[Path, ...] = ()
    gw2_directories: tuple[Path, ...] = ()
    parser_executables: tuple[Path, ...] = ()
    webhooks: tuple[ImportedWebhook, ...] = ()
    settings: tuple[ImportedSetting, ...] = ()
    warnings: tuple[str, ...] = ()
    tier: int = 2

    @property
    def source_file(self) -> Path:
        return self.source_files[0]

    @property
    def useful(self) -> bool:
        return bool(
            self.log_folders
            or self.parser_executables
            or self.webhooks
            or self.settings
        )

    def summary(self) -> str:
        """Plain-language preview containing no webhook URLs or credentials."""
        lines = [f"Found: {self.app}", f"Config: {self.source_file}"]
        if self.log_folders:
            lines.append(f"Fight logs: {self.log_folders[0]}")
        else:
            lines.append("Fight logs: not stored in this config")
        if self.parser_executables:
            lines.append(f"Fight-log parser: {self.parser_executables[0]}")
        fights = [hook.display_name for hook in self.webhooks if hook.role == "fight"]
        nightly = [hook.display_name for hook in self.webhooks if hook.role == "nightly"]
        unknown = [hook.display_name for hook in self.webhooks if hook.role == "unknown"]
        if fights:
            lines.append("Fight reports: " + ", ".join(fights))
        if nightly:
            lines.append("Nightly debrief: " + ", ".join(nightly))
        if unknown:
            lines.append("Other Discord destinations: " + ", ".join(unknown))
        if not self.webhooks:
            lines.append("Discord: not stored here; SparkyBot will ask")
        if self.settings:
            lines.append("Other preferences to reuse:")
            current_section = ""
            for setting in self.settings:
                if setting.section != current_section:
                    current_section = setting.section
                    lines.append(f"  {current_section}:")
                lines.append(f"    {setting.label}: {setting.display_value}")
        lines.append("Passwords, API keys, bot tokens, and account credentials: ignored")
        if self.warnings:
            lines.extend(f"Note: {warning}" for warning in self.warnings)
        return "\n".join(lines)


@dataclass(frozen=True)
class CompetitorImportPlan:
    finding: CompetitorFinding
    log_folder: Path | None
    parser_executable: Path | None
    fight_webhook: ImportedWebhook | None
    nightly_webhook: ImportedWebhook | None
    settings: tuple[ImportedSetting, ...] = ()

    @property
    def has_discord_routing(self) -> bool:
        return self.fight_webhook is not None and self.nightly_webhook is not None

    @property
    def saved_webhooks(self) -> tuple[ImportedWebhook, ...]:
        """Selected routes first, then other named destinations, up to capacity."""
        if not self.has_discord_routing:
            return ()
        return _unique_webhooks(
            (
                self.fight_webhook,
                self.nightly_webhook,
                *self.finding.webhooks,
            )
        )[:3]

    @property
    def additional_webhooks(self) -> tuple[ImportedWebhook, ...]:
        selected_urls = {
            hook.url
            for hook in (self.fight_webhook, self.nightly_webhook)
            if hook is not None
        }
        return tuple(
            hook for hook in self.saved_webhooks if hook.url not in selected_urls
        )

    def summary(self) -> str:
        lines = [f"Import from {self.finding.app}"]
        lines.append(
            f"Fight logs: {self.log_folder}"
            if self.log_folder
            else "Fight logs: SparkyBot will help find them"
        )
        lines.append(
            f"Fight-log parser: {self.parser_executable}"
            if self.parser_executable
            else "Fight-log parser: SparkyBot will install or locate it"
        )
        lines.append(
            "Individual fight reports: " + self.fight_webhook.display_name
            if self.fight_webhook
            else "Individual fight reports: SparkyBot will ask"
        )
        lines.append(
            "Nightly debrief and logs: " + self.nightly_webhook.display_name
            if self.nightly_webhook
            else "Nightly debrief and logs: SparkyBot will ask"
        )
        if self.additional_webhooks:
            lines.append(
                "Other saved Discord destinations: "
                + ", ".join(hook.display_name for hook in self.additional_webhooks)
            )
        if self.settings:
            lines.append(f"Other matching preferences: {len(self.settings)}")
        lines.append(
            "AI, voice, and private credentials are not imported; optional "
            "features stay off"
        )
        return "\n".join(lines)


# One allowlist owns both adapter parsing and apply-time validation. Bounds are
# the ranges SparkyBot's own Settings screen accepts, not merely what a neighbor
# happens to store.
_SETTING_RULES: dict[tuple[str, str], tuple[str, int, int, str]] = {
    ("Discord", "discordWebhookLabel"): ("text", 1, 80, "Discord name"),
    ("Discord", "embedColor"): ("color", 0, 0xFFFFFF, "Discord color"),
    ("Thresholds", "minFightDuration"): (
        "int", 1, 3600, "Minimum fight duration (seconds)"
    ),
    ("Thresholds", "minFightDowns"): ("int", 0, 10, "Minimum downs"),
    ("Thresholds", "minFightTotalDmg"): (
        "int", 0, 9_999_999, "Minimum total damage"
    ),
    ("Thresholds", "maxUploadSize"): ("int", 1, 1024, "Upload limit (MB)"),
    ("Thresholds", "uploadLargeAfterParse"): (
        "bool", 0, 0, "Upload large reports after parsing"
    ),
    ("UI", "showDamage"): ("bool", 0, 0, "Show damage"),
    ("UI", "showHeals"): ("bool", 0, 0, "Show healing"),
    ("UI", "showDefense"): ("bool", 0, 0, "Show defense"),
    ("UI", "showCCs"): ("bool", 0, 0, "Show crowd control"),
    ("UI", "showStrips"): ("bool", 0, 0, "Show boon strips"),
    ("UI", "showCleanses"): ("bool", 0, 0, "Show cleanses"),
    ("UI", "showDownsKills"): ("bool", 0, 0, "Show downs and kills"),
    ("UI", "showBurstDmg"): ("bool", 0, 0, "Show burst damage"),
    ("UI", "showTopEnemySkills"): ("bool", 0, 0, "Show top enemy skills"),
    ("UI", "showOffensiveBoons"): ("bool", 0, 0, "Show offensive boons"),
    ("UI", "showDefensiveBoons"): ("bool", 0, 0, "Show defensive boons"),
    ("UI", "showEnemyBreakdown"): ("bool", 0, 0, "Show enemy breakdown"),
    ("UI", "showQuickReport"): ("bool", 0, 0, "Show quick report"),
    ("Behavior", "closeToTray"): ("bool", 0, 0, "Close to tray"),
    ("Behavior", "minimizeToTray"): ("bool", 0, 0, "Minimize to tray"),
    ("Behavior", "startMinimized"): ("bool", 0, 0, "Start minimized"),
    ("Behavior", "maxParseMemory"): (
        "int", 512, 16_384, "Parser memory (MB)"
    ),
    ("Twitch", "twitchChannelName"): ("text", 1, 80, "Twitch channel"),
    ("Twitch", "twitchUseTLS"): ("bool", 0, 0, "Secure Twitch connection"),
}


def normalize_parity_setting(section: str, key: str, raw: Any) -> str | None:
    """Normalize an allowlisted non-secret setting, or reject it safely."""
    rule = _SETTING_RULES.get((section, key))
    if rule is None:
        return None
    kind, minimum, maximum, _label = rule
    if kind == "bool":
        if isinstance(raw, bool):
            return "true" if raw else "false"
        if isinstance(raw, str):
            folded = raw.strip().casefold()
            if folded in {"true", "false"}:
                return folded
        return None
    if kind == "int":
        if isinstance(raw, bool):
            return None
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return None
        return str(value) if minimum <= value <= maximum else None
    if kind == "color":
        if isinstance(raw, bool):
            return None
        text = str(raw).strip()
        if re.fullmatch(r"(?i)(?:#|0x)[0-9a-f]{6}", text):
            value = int(re.sub(r"(?i)^(?:#|0x)", "", text), 16)
        else:
            try:
                value = int(text)
            except ValueError:
                return None
        return f"0x{value:06X}" if minimum <= value <= maximum else None
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not (minimum <= len(text) <= maximum):
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        return None
    return text


def describe_parity_setting(
    section: str, key: str, raw: Any
) -> tuple[str, str] | None:
    """Return the safe human label/value pair used by consent previews."""
    value = normalize_parity_setting(section, key, raw)
    rule = _SETTING_RULES.get((section, key))
    if value is None or rule is None:
        return None
    setting = ImportedSetting(section, key, value, rule[3])
    return setting.label, setting.display_value


def _make_setting(section: str, key: str, raw: Any) -> ImportedSetting | None:
    value = normalize_parity_setting(section, key, raw)
    if value is None:
        return None
    return ImportedSetting(section, key, value, _SETTING_RULES[(section, key)][3])


def _unique_paths(values: Iterable[Path | str]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[str] = set()
    for raw in values:
        if raw is None:
            continue
        path = Path(raw)
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return tuple(result)


def _unique_webhooks(values: Iterable[ImportedWebhook]) -> tuple[ImportedWebhook, ...]:
    result: list[ImportedWebhook] = []
    seen: set[str] = set()
    for hook in values:
        key = hook.url.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(hook)
    return tuple(result)


def _unique_settings(values: Iterable[ImportedSetting]) -> tuple[ImportedSetting, ...]:
    result: list[ImportedSetting] = []
    seen: set[tuple[str, str]] = set()
    for setting in values:
        key = (setting.section, setting.key)
        if key in seen:
            continue
        seen.add(key)
        result.append(setting)
    return tuple(result)


def _clean_name(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    text = " ".join(text.split())
    return (text[:80] or fallback)


def _make_webhook(
    name: Any,
    raw_url: Any,
    role: WebhookRole,
    *,
    preferred: bool = True,
) -> ImportedWebhook | None:
    if not isinstance(raw_url, str):
        return None
    normalized = normalize_webhook_url(raw_url)
    if not normalized:
        return None
    return ImportedWebhook(
        _clean_name(name, "Discord destination"),
        normalized,
        role,
        preferred,
    )


_WINDOWS_ENV_RE = re.compile(r"%([^%]+)%")


def _expand_path(raw: Any) -> Path | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip().strip('"').strip("'")
    if not value or len(value) > MAX_VALUE_CHARS or "\x00" in value:
        return None
    env = {key.casefold(): val for key, val in os.environ.items()}

    def replace_env(match: re.Match[str]) -> str:
        return env.get(match.group(1).casefold(), match.group(0))

    value = _WINDOWS_ENV_RE.sub(replace_env, value)
    value = os.path.expanduser(os.path.expandvars(value))
    return Path(value)


def _read_bytes(path: Path, *, limit: int = MAX_CONFIG_BYTES) -> bytes:
    try:
        stat = path.stat()
    except OSError as exc:
        raise CompetitorConfigError(f"Could not read {path.name}: {exc}") from exc
    if not path.is_file():
        raise CompetitorConfigError(f"{path.name} is not a file.")
    if stat.st_size > limit:
        raise CompetitorConfigError(
            f"{path.name} is too large to be a normal settings file."
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CompetitorConfigError(f"Could not read {path.name}: {exc}") from exc


def _read_text(path: Path) -> str:
    raw = _read_bytes(path)
    if b"\x00" in raw:
        raise CompetitorConfigError(f"{path.name} is not a text settings file.")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise CompetitorConfigError(
                f"{path.name} is not readable UTF-8 or Windows text."
            ) from exc


def _read_json(path: Path) -> Any:
    try:
        return json.loads(_read_text(path))
    except json.JSONDecodeError as exc:
        raise CompetitorConfigError(
            f"{path.name} is not valid JSON (line {exc.lineno})."
        ) from exc


def _neighbor_parsers(path: Path) -> tuple[Path, ...]:
    """Find only exact compatible executable names within two bounded levels."""
    wanted = {
        "guildwars2eliteinsights-cli.exe",
    }
    queue: list[tuple[Path, int]] = [(path.parent, 0)]
    found: list[Path] = []
    visited = 0
    while queue and visited < 250:
        directory, depth = queue.pop(0)
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            visited += 1
            if visited >= 250:
                break
            if entry.is_file() and entry.name.casefold() in wanted:
                found.append(entry)
            elif entry.is_dir() and depth < 2:
                folded = entry.name.casefold()
                if depth == 0 or "gw2ei" in folded or "elite" in folded:
                    queue.append((entry, depth + 1))
    return _unique_paths(found)


def _finding(
    app: str,
    source_files: Sequence[Path],
    *,
    log_folders: Iterable[Path | str] = (),
    gw2_directories: Iterable[Path | str] = (),
    parser_executables: Iterable[Path | str] = (),
    webhooks: Iterable[ImportedWebhook] = (),
    settings: Iterable[ImportedSetting] = (),
    warnings: Iterable[str] = (),
    tier: int = 2,
) -> CompetitorFinding:
    source_tuple = _unique_paths(source_files)
    parsers = _unique_paths((*parser_executables, *_neighbor_parsers(source_tuple[0])))
    return CompetitorFinding(
        app=app,
        source_files=source_tuple,
        log_folders=_unique_paths(log_folders),
        gw2_directories=_unique_paths(gw2_directories),
        parser_executables=parsers,
        webhooks=_unique_webhooks(webhooks),
        settings=_unique_settings(settings),
        warnings=tuple(dict.fromkeys(str(item) for item in warnings if item)),
        tier=tier,
    )


def _json_webhook_array(
    raw: Any,
    role: WebhookRole,
    *,
    active_key: str | None = None,
) -> list[ImportedWebhook]:
    if not isinstance(raw, list):
        return []
    hooks: list[ImportedWebhook] = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            continue
        preferred = bool(item.get(active_key, True)) if active_key else bool(
            item.get("enabled", True)
        )
        hook = _make_webhook(
            item.get("name"), item.get("url"), role, preferred=preferred
        )
        if hook:
            hooks.append(hook)
    return hooks


def _mapped_settings(
    values: dict[str, Any],
    mappings: Iterable[tuple[str, str, str]],
) -> tuple[ImportedSetting, ...]:
    settings: list[ImportedSetting] = []
    for source_key, section, target_key in mappings:
        if source_key not in values:
            continue
        setting = _make_setting(section, target_key, values[source_key])
        if setting is not None:
            settings.append(setting)
    return _unique_settings(settings)


def _contains_private_credentials(value: Any, depth: int = 0) -> bool:
    """Notice credential-bearing fields without copying or displaying them."""
    if depth >= 16:
        return False
    if isinstance(value, dict):
        for key, item in value.items():
            folded = str(key).casefold().replace("_", "")
            if any(word in folded for word in ("token", "apikey", "password", "secret")):
                if item not in (None, "", [], {}):
                    return True
            if _contains_private_credentials(item, depth + 1):
                return True
    elif isinstance(value, list):
        return any(_contains_private_credentials(item, depth + 1) for item in value)
    return False


def _parse_plenbot(path: Path, data: Any) -> CompetitorFinding | None:
    lower_name = path.name.casefold()
    is_settings = isinstance(data, dict) and (
        "logsLocation" in data or "gw2Location" in data
    )
    is_webhooks = lower_name == "discord_webhooks.json" and isinstance(data, list)
    if not (is_settings or is_webhooks):
        return None
    files = [path]
    settings = data if is_settings else {}
    hooks_data = data if is_webhooks else []
    sibling = path.parent / (
        "discord_webhooks.json" if is_settings else "app_settings.json"
    )
    if sibling.is_file():
        sibling_data = _read_json(sibling)
        files.append(sibling)
        if is_settings and isinstance(sibling_data, list):
            hooks_data = sibling_data
        elif is_webhooks and isinstance(sibling_data, dict):
            settings = sibling_data
    log_path = _expand_path(settings.get("logsLocation"))
    gw2_path = _expand_path(settings.get("gw2Location"))
    hooks = _json_webhook_array(hooks_data, "fight", active_key="isActive")
    parity_settings = _mapped_settings(
        settings,
        (
            ("closeToTry", "Behavior", "closeToTray"),
            ("minimiseToTry", "Behavior", "minimizeToTray"),
        ),
    )
    warnings = (
        ("Private PlenBot credentials were found and will not be copied.",)
        if _contains_private_credentials(settings)
        else ()
    )
    return _finding(
        "PlenBot Log Uploader",
        files,
        log_folders=(log_path,) if log_path else (),
        gw2_directories=(gw2_path,) if gw2_path else (),
        webhooks=hooks,
        settings=parity_settings,
        warnings=warnings,
        tier=1,
    )


def _parse_axibridge(path: Path, data: Any) -> CompetitorFinding | None:
    if not isinstance(data, dict):
        return None
    parent = path.parent.name.casefold()
    signature = any(
        key in data
        for key in ("discordWebhookUrl", "webhooks", "reportWebhooks")
    )
    if not signature and parent not in {"axibridge", "arcbridge"}:
        return None
    if "logDirectory" not in data and not signature:
        return None
    # Prefer AxiBridge's named lists over the legacy single-URL field when
    # both reference the same destination.
    hooks: list[ImportedWebhook] = _json_webhook_array(
        data.get("webhooks"), "fight"
    )
    primary = _make_webhook("Fight reports", data.get("discordWebhookUrl"), "fight")
    if primary:
        hooks.append(primary)
    hooks.extend(_json_webhook_array(data.get("reportWebhooks"), "nightly"))
    log_path = _expand_path(data.get("logDirectory"))
    embed_settings = data.get("embedStatSettings")
    parity_settings: list[ImportedSetting] = []
    if isinstance(embed_settings, dict):
        parity_settings.extend(
            _mapped_settings(
                embed_settings,
                (
                    ("showDamage", "UI", "showDamage"),
                    ("showHealing", "UI", "showHeals"),
                    ("showCleanses", "UI", "showCleanses"),
                    ("showBoonStrips", "UI", "showStrips"),
                    ("showCC", "UI", "showCCs"),
                ),
            )
        )
        # AxiBridge exposes downs and kills separately; SparkyBot combines
        # them, so only transfer the choice when both agree.
        if (
            isinstance(embed_settings.get("showDowns"), bool)
            and embed_settings.get("showDowns") == embed_settings.get("showKills")
        ):
            combined = _make_setting(
                "UI", "showDownsKills", embed_settings["showDowns"]
            )
            if combined:
                parity_settings.append(combined)
    close_behavior = data.get("closeBehavior")
    if isinstance(close_behavior, str) and close_behavior in {"minimize", "quit"}:
        close_setting = _make_setting(
            "Behavior", "closeToTray", close_behavior == "minimize"
        )
        if close_setting:
            parity_settings.append(close_setting)
    warnings = (
        ("Private AxiBridge credentials were found and will not be copied.",)
        if _contains_private_credentials(data)
        else ()
    )
    return _finding(
        "AxiBridge" if parent != "arcbridge" else "ArcBridge (now AxiBridge)",
        (path,),
        log_folders=(log_path,) if log_path else (),
        webhooks=hooks,
        settings=parity_settings,
        warnings=warnings,
        tier=1,
    )


def _parse_axipulse(path: Path, data: Any) -> CompetitorFinding | None:
    if not isinstance(data, dict) or "logDirectory" not in data:
        return None
    if "axipulse" not in str(path.parent).casefold():
        return None
    log_path = _expand_path(data.get("logDirectory"))
    return _finding(
        "AxiPulse",
        (path,),
        log_folders=(log_path,) if log_path else (),
        warnings=("AxiPulse does not store compatible Discord routing here.",),
    )


def _parse_topstats_json(path: Path, data: Any) -> CompetitorFinding | None:
    if not isinstance(data, dict) or "lastFolder" not in data:
        return None
    if path.name.casefold() != "ui-state.json" and "topstats" not in str(path).casefold():
        return None
    log_path = _expand_path(data.get("lastFolder"))
    return _finding(
        "TopStatsAIO",
        (path,),
        log_folders=(log_path,) if log_path else (),
        warnings=(
            "TopStatsAIO stores its last raw-log folder here; generated report output folders are not imported.",
        ),
        tier=1,
    )


def _parse_l0g(path: Path, data: Any) -> CompetitorFinding | None:
    if not isinstance(data, dict) or "arcdps_logs" not in data or "guilds" not in data:
        return None
    log_path = _expand_path(data.get("arcdps_logs"))
    hooks: list[ImportedWebhook] = []
    for item in data.get("guilds", []):
        if isinstance(item, dict):
            hook = _make_webhook(item.get("name"), item.get("webhook_url"), "fight")
            if hook:
                hooks.append(hook)
    return _finding(
        "L0G-101086",
        (path,),
        log_folders=(log_path,) if log_path else (),
        webhooks=hooks,
    )


def _parse_haxshoo(path: Path, data: Any) -> CompetitorFinding | None:
    if not isinstance(data, dict) or not ({"logPath", "webhookURL"} & data.keys()):
        return None
    log_path = _expand_path(data.get("logPath"))
    hook = _make_webhook("WvW fight reports", data.get("webhookURL"), "fight")
    return _finding(
        "WvW Log Uploader",
        (path,),
        log_folders=(log_path,) if log_path else (),
        webhooks=(hook,) if hook else (),
    )


def _parse_wvw_insights(path: Path, data: Any) -> CompetitorFinding | None:
    lower_path = str(path).casefold()
    if not isinstance(data, dict):
        return None
    is_settings = "log_directory" in data and (
        "wvw-insights" in lower_path or path.name.casefold() == "settings.json"
    )
    is_webhooks = "saved_webhooks" in data and path.name.casefold() == "webhooks.json"
    if not (is_settings or is_webhooks):
        return None
    files = [path]
    settings = data if is_settings else {}
    webhooks_data = data if is_webhooks else {}
    sibling = path.parent / ("webhooks.json" if is_settings else "settings.json")
    if sibling.is_file():
        sibling_data = _read_json(sibling)
        files.append(sibling)
        if is_settings and isinstance(sibling_data, dict):
            webhooks_data = sibling_data
        elif is_webhooks and isinstance(sibling_data, dict):
            settings = sibling_data
    hooks = _json_webhook_array(webhooks_data.get("saved_webhooks"), "fight")
    last = _make_webhook(
        "Last used WvW Insights destination",
        webhooks_data.get("last_webhook_url"),
        "fight",
    )
    if last:
        hooks.insert(0, last)
    log_path = _expand_path(settings.get("log_directory"))
    guild_name = _make_setting(
        "Discord", "discordWebhookLabel", settings.get("guild_name")
    )
    warnings = (
        ("Private WvW Insights tokens were found and will not be copied.",)
        if _contains_private_credentials(settings)
        else ()
    )
    return _finding(
        "WvW Insights",
        files,
        log_folders=(log_path,) if log_path else (),
        webhooks=hooks,
        settings=(guild_name,) if guild_name else (),
        warnings=warnings,
    )


def _parse_manny(path: Path, data: Any) -> CompetitorFinding | None:
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return None
    general = data.get("general")
    if not isinstance(general, dict) or "log_directory" not in general:
        return None
    log_path = _expand_path(general.get("log_directory"))
    return _finding(
        "GW2 Manny Uploader",
        (path,),
        log_folders=(log_path,) if log_path else (),
        warnings=("Manny keeps credentials separately; they are not read.",),
    )


def _parse_gw2scratch(path: Path, data: Any) -> CompetitorFinding | None:
    if not isinstance(data, dict) or "LogRootPaths" not in data:
        return None
    raw_paths = data.get("LogRootPaths")
    if not isinstance(raw_paths, list):
        raw_paths = []
    paths = [candidate for raw in raw_paths if (candidate := _expand_path(raw))]
    return _finding("GW2Scratch Log Manager", (path,), log_folders=paths)


def _parse_mz_json_or_commanders(path: Path, data: Any) -> CompetitorFinding | None:
    if not isinstance(data, dict) or "watch_folder" not in data:
        return None
    log_path = _expand_path(data.get("watch_folder"))
    return _finding(
        "GW2 Commanders Watch",
        (path,),
        log_folders=(log_path,) if log_path else (),
    )


def _parse_wingman(path: Path, data: Any) -> CompetitorFinding | None:
    if not isinstance(data, dict) or "logpath" not in data:
        return None
    if "wingman" not in str(path.parent).casefold():
        return None
    log_path = _expand_path(data.get("logpath"))
    return _finding(
        "Nexus Wingman Uploader",
        (path,),
        log_folders=(log_path,) if log_path else (),
    )


def _parse_json(path: Path) -> CompetitorFinding:
    data = _read_json(path)
    parsers = (
        _parse_plenbot,
        _parse_axipulse,
        _parse_axibridge,
        _parse_topstats_json,
        _parse_l0g,
        _parse_haxshoo,
        _parse_wvw_insights,
        _parse_manny,
        _parse_gw2scratch,
        _parse_mz_json_or_commanders,
        _parse_wingman,
    )
    for parser in parsers:
        finding = parser(path, data)
        if finding is not None and finding.useful:
            return finding
    raise CompetitorConfigError(
        "That JSON file is not a supported WvW log-tool config. Choose the "
        "tool's settings file, not a generated fight report."
    )


_MZ_SETTING_MAPPINGS = (
    ("embedColor", "Discord", "embedColor"),
    ("minFightDuration", "Thresholds", "minFightDuration"),
    ("minFightDowns", "Thresholds", "minFightDowns"),
    ("minFightTotalDmg", "Thresholds", "minFightTotalDmg"),
    ("maxUploadMegabytes", "Thresholds", "maxUploadSize"),
    ("largeUploadsAfterParse", "Thresholds", "uploadLargeAfterParse"),
    ("showDamage", "UI", "showDamage"),
    ("showHeals", "UI", "showHeals"),
    ("showDefense", "UI", "showDefense"),
    ("showCCs", "UI", "showCCs"),
    ("showStrips", "UI", "showStrips"),
    ("showCleanses", "UI", "showCleanses"),
    ("showDownsKills", "UI", "showDownsKills"),
    ("showBurstDmg", "UI", "showBurstDmg"),
    ("showTopEnemySkills", "UI", "showTopEnemySkills"),
    ("showOffensiveBoons", "UI", "showOffensiveBoons"),
    ("showDefensiveBoons", "UI", "showDefensiveBoons"),
    ("showEnemyBreakdown", "UI", "showEnemyBreakdown"),
    ("showQuickReport", "UI", "showQuickReport"),
    ("closeToTray", "Behavior", "closeToTray"),
    ("minimizeToTray", "Behavior", "minimizeToTray"),
    ("startMinimized", "Behavior", "startMinimized"),
    ("maxParseMemory", "Behavior", "maxParseMemory"),
    ("twitchChannelName", "Twitch", "twitchChannelName"),
    ("twitchUseTLS", "Twitch", "twitchUseTLS"),
)


def _parse_properties(path: Path) -> CompetitorFinding:
    values: dict[str, str] = {}
    for raw_line in _read_text(path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "!")):
            continue
        match = re.match(r"([^:=\s]+)\s*[:=]\s*(.*)$", line)
        if match:
            values[match.group(1)] = match.group(2).replace("\\\\", "\\")
    if "defaultLogFolder" not in values and "customLogFolder" not in values:
        raise CompetitorConfigError(
            "That properties file is not MzFightReporter's config.properties."
        )
    paths = [
        candidate
        for key in ("customLogFolder", "defaultLogFolder")
        if (candidate := _expand_path(values.get(key)))
    ]
    try:
        active = int(values.get("activeDiscordWebhook", "1"))
    except ValueError:
        active = 1
    hooks: list[ImportedWebhook] = []
    for index in range(1, 4):
        suffix = "" if index == 1 else str(index)
        hook = _make_webhook(
            values.get(f"discordWebhookLabel{suffix}"),
            values.get(f"discordWebhook{suffix}"),
            "fight",
            preferred=index == active,
        )
        if hook:
            hooks.append(hook)
    settings = _mapped_settings(values, _MZ_SETTING_MAPPINGS)
    warnings = (
        ("A Twitch bot token was found and will not be copied.",)
        if values.get("twitchBotToken", "").strip()
        else ()
    )
    return _finding(
        "MzFightReporter",
        (path,),
        log_folders=paths,
        webhooks=hooks,
        settings=settings,
        warnings=warnings,
        tier=1,
    )


def _read_ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        parser.read_string(_read_text(path))
    except configparser.Error as exc:
        raise CompetitorConfigError(f"{path.name} is not valid INI settings.") from exc
    return parser


def _ini_values(parser: configparser.ConfigParser) -> dict[str, str]:
    values: dict[str, str] = {}
    for section in parser.sections():
        for key, value in parser.items(section):
            values[f"{section}.{key}".casefold()] = value.strip()
            values.setdefault(key.casefold(), value.strip())
    return values


def _parse_ini(path: Path) -> CompetitorFinding:
    parser = _read_ini(path)
    values = _ini_values(parser)
    # WEBHOOK_URL by itself is not an EVTC signature. Drevarr's adjacent
    # GW2-WVW-Teams roster utility also has [Settings]/WEBHOOK_URL, but that
    # channel is for alliance/team embeds and must never become Logspam.
    if "arcdps_log_dir" in values:
        log_path = _expand_path(values.get("arcdps_log_dir"))
        hook = _make_webhook("EVTC fight reports", values.get("webhook_url"), "fight")
        return _finding(
            "EVTC_parser",
            (path,),
            log_folders=(log_path,) if log_path else (),
            webhooks=(hook,) if hook else (),
        )
    if "discordcfg.webhook_url" in values or "input_directory" in values:
        hook = _make_webhook(
            "TopStats nightly report", values.get("discordcfg.webhook_url"), "nightly"
        )
        warnings = (
            "The combiner's input_directory contains generated EI JSON, not raw ArcDPS logs, so it was not imported.",
        )
        guild_name = _make_setting(
            "Discord",
            "discordWebhookLabel",
            values.get("topstatscfg.guild_name", values.get("guild_name")),
        )
        if values.get("topstatscfg.api_key", values.get("api_key", "")).strip():
            warnings += ("A Guild Wars 2 API key was found and will not be copied.",)
        return _finding(
            "TopStats / GW2 EI Log Combiner",
            (path,),
            webhooks=(hook,) if hook else (),
            settings=(guild_name,) if guild_name else (),
            warnings=warnings,
            tier=1,
        )
    raise CompetitorConfigError(
        "That INI file is not an EVTC_parser or TopStats/GW2 EI Combiner config."
    )


def _parse_toml(path: Path) -> CompetitorFinding:
    try:
        data = tomllib.loads(_read_text(path))
    except tomllib.TOMLDecodeError as exc:
        raise CompetitorConfigError(f"{path.name} is not valid TOML settings.") from exc
    raw = data.get("logpath")
    if raw is None and isinstance(data.get("settings"), dict):
        raw = data["settings"].get("logpath")
    log_path = _expand_path(raw)
    if not log_path:
        raise CompetitorConfigError("That TOML file is not arclog's config.toml.")
    return _finding("arclog", (path,), log_folders=(log_path,))


def _parse_yaml(path: Path) -> CompetitorFinding:
    # Deliberately parse only the one allowlisted scalar.  Loading arbitrary
    # YAML objects is unnecessary and unsafe, and SparkyBot has no YAML runtime.
    match = re.search(
        r"(?mi)^\s*arcdps_logs\s*:\s*(?:[\"']([^\"']+)[\"']|([^#\r\n]+))",
        _read_text(path),
    )
    log_path = _expand_path((match.group(1) or match.group(2)).strip()) if match else None
    if not log_path:
        raise CompetitorConfigError(
            "That YAML file is not toxic-elitist's config.yml."
        )
    return _finding(
        "toxic-elitist",
        (path,),
        log_folders=(log_path,),
        warnings=("Discord bot tokens and channel IDs are intentionally not imported.",),
    )


def _parse_xml(path: Path) -> CompetitorFinding:
    try:
        root = ET.fromstring(_read_text(path))
    except ET.ParseError as exc:
        raise CompetitorConfigError(f"{path.name} is not valid XML settings.") from exc
    raw_path = None
    for setting in root.iter("setting"):
        if setting.attrib.get("name") == "ArcLogsPath":
            value = setting.find("value")
            raw_path = value.text if value is not None else None
            break
    log_path = _expand_path(raw_path)
    if not log_path:
        raise CompetitorConfigError(
            "That XML file is not LogUploader2's user.config."
        )
    return _finding(
        "LogUploader2",
        (path,),
        log_folders=(log_path,),
        warnings=("Encrypted webhook storage is left untouched.",),
    )


def _parse_sqlite(path: Path) -> CompetitorFinding:
    _read_bytes(path, limit=MAX_DATABASE_BYTES)  # validate size and readability first
    uri = f"file:{quote(str(path.resolve()))}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=1)
        try:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(webhooks)").fetchall()
            }
            if not {"name", "url"}.issubset(columns):
                raise CompetitorConfigError(
                    "That database does not contain arcdps-uploader webhooks."
                )
            selected = ["name", "url"]
            if "wvw" in columns:
                selected.append("wvw")
            rows = connection.execute(
                f"SELECT {', '.join(selected)} FROM webhooks LIMIT 100"
            ).fetchall()
        finally:
            connection.close()
    except (sqlite3.Error, OSError) as exc:
        raise CompetitorConfigError(
            f"Could not safely read {path.name} as an uploader database."
        ) from exc
    hooks: list[ImportedWebhook] = []
    for row in rows:
        if len(row) == 3 and not bool(row[2]):
            continue
        hook = _make_webhook(row[0], row[1], "fight")
        if hook:
            hooks.append(hook)
    if not hooks:
        raise CompetitorConfigError("No usable WvW Discord webhooks were found.")
    return _finding(
        "arcdps-uploader",
        (path,),
        webhooks=hooks,
        warnings=("Upload history and non-WvW webhooks are not imported.",),
    )


def parse_competitor_config(path: str | Path) -> CompetitorFinding:
    """Identify and parse one supported competitor settings file."""
    source = Path(path)
    name = source.name.casefold()
    suffix = source.suffix.casefold()
    if name == "user.config" or suffix == ".xml":
        finding = _parse_xml(source)
    elif suffix in {".db", ".sqlite", ".sqlite3"}:
        finding = _parse_sqlite(source)
    elif suffix == ".toml":
        finding = _parse_toml(source)
    elif suffix in {".yaml", ".yml"}:
        finding = _parse_yaml(source)
    elif suffix == ".properties":
        finding = _parse_properties(source)
    elif suffix == ".ini":
        finding = _parse_ini(source)
    elif suffix == ".json":
        finding = _parse_json(source)
    else:
        raise CompetitorConfigError(
            "Choose a JSON, INI, properties, TOML, YAML, XML, or uploader.db settings file."
        )
    if not finding.useful:
        raise CompetitorConfigError("That settings file contains nothing SparkyBot can use.")
    return finding


# Compatibility-friendly name used by the UI and external callers.
identify_and_parse = parse_competitor_config


_KNOWN_APP_DIRS: dict[str, tuple[str, ...]] = {
    "axibridge": ("config.json",),
    "arcbridge": ("config.json",),
    "axipulse": ("config.json",),
    "axipulse-default": ("config.json",),
    "topstatsaio": ("ui-state.json", "top_stats_config.ini"),
    "arclog": ("config.toml",),
    "arcdpslogmanager": ("Settings.json",),
    "mzfightreporter": ("config.properties",),
    "plenbot": ("app_settings.json", "discord_webhooks.json"),
    "plenbotloguploader": ("app_settings.json", "discord_webhooks.json"),
    "evtc_parser": ("config.ini",),
    "loguploader2": ("user.config",),
    "toxic-elitist": ("config.yml", "config.yaml"),
    "gw2_commanders_watch": ("config.json",),
}


@dataclass(frozen=True)
class CompetitorImportTarget:
    """A supported app plus narrow, known places for its primary settings."""

    key: str
    name: str
    appdata_paths: tuple[str, ...] = ()
    gw2_paths: tuple[str, ...] = ()
    portable_paths: tuple[str, ...] = ()


COMPETITOR_IMPORT_TARGETS = (
    CompetitorImportTarget(
        "axibridge", "AxiBridge / ArcBridge",
        ("AxiBridge/config.json", "ArcBridge/config.json"),
        portable_paths=("AxiBridge/config.json", "ArcBridge/config.json"),
    ),
    CompetitorImportTarget(
        "topstatsaio", "TopStatsAIO", ("TopStatsAIO/ui-state.json",),
        portable_paths=("TopStatsAIO/ui-state.json",),
    ),
    CompetitorImportTarget(
        "gw2-ei-combiner", "GW2 EI Log Combiner",
        ("TopStatsAIO/top_stats_config.ini",),
        portable_paths=("GW2_EI_log_combiner/top_stats_config.ini",),
    ),
    CompetitorImportTarget(
        "plenbot", "PlenBot Log Uploader",
        (
            "PlenBotLogUploader/app_settings.json",
            "PlenBot/app_settings.json",
        ),
        portable_paths=("PlenBotLogUploader/app_settings.json",),
    ),
    CompetitorImportTarget(
        "mzfightreporter", "MzFightReporter",
        ("MzFightReporter/config.properties",),
        portable_paths=("MzFightReporter/config.properties",),
    ),
    CompetitorImportTarget(
        "wvw-insights", "WvW Insights",
        gw2_paths=("addons/wvw-insights/settings.json",),
        appdata_paths=("wvw-insights/settings.json",),
    ),
    CompetitorImportTarget(
        "evtc-parser", "EVTC_parser", ("evtc_parser/config.ini",),
        portable_paths=("EVTC_parser/config.ini",),
    ),
    CompetitorImportTarget(
        "manny", "GW2 Manny Uploader",
        gw2_paths=("addons/manny-uploader/settings.json",),
    ),
    CompetitorImportTarget(
        "gw2scratch", "GW2Scratch Log Manager",
        ("ArcdpsLogManager/Settings.json",),
    ),
    CompetitorImportTarget(
        "wingman", "Nexus Wingman Uploader",
        gw2_paths=("addons/wingman-uploader/settings.json",),
    ),
    CompetitorImportTarget(
        "wvw-log-uploader", "WvW Log Uploader",
        portable_paths=("WvW-Log-Uploader/config.json",),
    ),
    CompetitorImportTarget(
        "commanders-watch", "GW2 Commanders Watch",
        portable_paths=("GW2_Commanders_Watch/config.json",),
    ),
    CompetitorImportTarget(
        "l0g", "L0G-101086",
        portable_paths=("L0G-101086/l0g-101086-config.json",),
    ),
    CompetitorImportTarget(
        "arclog", "arclog", ("arclog/config.toml",),
        portable_paths=("arclog/config.toml",),
    ),
    CompetitorImportTarget(
        "toxic-elitist", "toxic-elitist",
        portable_paths=("toxic-elitist/config.yml",),
    ),
    CompetitorImportTarget(
        "loguploader2", "LogUploader2", ("LogUploader2/user.config",),
        portable_paths=("LogUploader2/user.config",),
    ),
    CompetitorImportTarget(
        "arcdps-uploader", "arcdps-uploader",
        gw2_paths=("addons/uploader/uploader.db",),
    ),
    CompetitorImportTarget(
        "axipulse", "AxiPulse", ("AxiPulse/config.json",),
        portable_paths=("AxiPulse/config.json",),
    ),
)

_IMPORT_TARGETS = {target.key: target for target in COMPETITOR_IMPORT_TARGETS}


def expected_competitor_config_paths(
    target_key: str,
    *,
    gw2_dirs: Iterable[str | Path] = (),
    home: str | Path | None = None,
    appdata: str | Path | None = None,
    local_appdata: str | Path | None = None,
) -> tuple[Path, ...]:
    """Return bounded, tool-specific picker guesses in useful priority order."""
    target = _IMPORT_TARGETS.get(target_key)
    if target is None:
        raise CompetitorConfigError(f"Unknown log tool: {target_key}")
    user_home = Path(home) if home is not None else Path.home()
    roaming = Path(appdata) if appdata is not None else Path(
        os.environ.get("APPDATA", user_home / "AppData" / "Roaming")
    )
    local = Path(local_appdata) if local_appdata is not None else Path(
        os.environ.get("LOCALAPPDATA", user_home / "AppData" / "Local")
    )
    candidates: list[Path] = []
    candidates.extend(
        Path(gw2) / relative
        for gw2 in gw2_dirs
        for relative in target.gw2_paths
    )
    candidates.extend(
        base / relative
        for base in (roaming, local)
        for relative in target.appdata_paths
    )
    candidates.extend(
        base / relative
        for base in (
            user_home / "Documents",
            user_home / "Downloads",
            user_home / "Desktop",
        )
        for relative in target.portable_paths
    )
    return _unique_paths(candidates)


def expected_competitor_config_path(
    target_key: str,
    **kwargs: Any,
) -> Path:
    """Pick an existing expected file when possible, otherwise the best guess."""
    candidates = expected_competitor_config_paths(target_key, **kwargs)
    if not candidates:
        raise CompetitorConfigError(
            "SparkyBot does not know an expected settings location for that tool."
        )
    return next((path for path in candidates if path.is_file()), candidates[0])


def _known_candidates_in(directory: Path) -> list[Path]:
    names = _KNOWN_APP_DIRS.get(directory.name.casefold(), ())
    return [directory / name for name in names]


def _portable_candidates(roots: Iterable[Path]) -> list[Path]:
    """Bounded common-place walk; generic config files are never collected."""
    candidates: list[Path] = []
    entries_seen = 0
    direct_unique = {
        "l0g-101086-config.json",
        "l0g-101086-config.sample.json",
        "l0g-101086-config.multipleguilds.json",
    }
    queue: list[tuple[Path, int]] = [(Path(root), 0) for root in roots]
    while queue and entries_seen < MAX_DISCOVERY_ENTRIES:
        directory, depth = queue.pop(0)
        if not directory.is_dir():
            continue
        candidates.extend(_known_candidates_in(directory))
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            entries_seen += 1
            if entries_seen >= MAX_DISCOVERY_ENTRIES:
                break
            if entry.is_file() and entry.name.casefold() in direct_unique:
                candidates.append(entry)
            elif entry.is_dir() and depth < MAX_DISCOVERY_DEPTH:
                queue.append((entry, depth + 1))
    return candidates


def _merge_findings(findings: Iterable[CompetitorFinding]) -> tuple[CompetitorFinding, ...]:
    grouped: dict[tuple[str, str], list[CompetitorFinding]] = {}
    for finding in findings:
        root = os.path.normcase(str(finding.source_file.parent))
        grouped.setdefault((finding.app, root), []).append(finding)
    merged: list[CompetitorFinding] = []
    for group in grouped.values():
        first = group[0]
        merged.append(
            CompetitorFinding(
                app=first.app,
                source_files=_unique_paths(
                    path for item in group for path in item.source_files
                ),
                log_folders=_unique_paths(
                    path for item in group for path in item.log_folders
                ),
                gw2_directories=_unique_paths(
                    path for item in group for path in item.gw2_directories
                ),
                parser_executables=_unique_paths(
                    path for item in group for path in item.parser_executables
                ),
                webhooks=_unique_webhooks(
                    hook for item in group for hook in item.webhooks
                ),
                settings=_unique_settings(
                    setting for item in group for setting in item.settings
                ),
                warnings=tuple(
                    dict.fromkeys(warning for item in group for warning in item.warnings)
                ),
                tier=min(item.tier for item in group),
            )
        )
    return tuple(
        sorted(
            merged,
            key=lambda item: (
                item.tier,
                not any(path.is_dir() for path in item.log_folders),
                item.app.casefold(),
            ),
        )
    )


def discover_competitor_configs(
    *,
    gw2_dirs: Iterable[str | Path] = (),
    home: str | Path | None = None,
    appdata: str | Path | None = None,
    local_appdata: str | Path | None = None,
    portable_roots: Iterable[str | Path] | None = None,
    extra_files: Iterable[str | Path] = (),
) -> tuple[CompetitorFinding, ...]:
    """Find configs only in known Windows/app/addon locations and bounded roots."""
    user_home = Path(home) if home is not None else Path.home()
    roaming = Path(appdata) if appdata is not None else Path(
        os.environ.get("APPDATA", user_home / "AppData" / "Roaming")
    )
    local = Path(local_appdata) if local_appdata is not None else Path(
        os.environ.get("LOCALAPPDATA", user_home / "AppData" / "Local")
    )
    candidates: list[Path] = [Path(path) for path in extra_files]
    for base in (roaming, local):
        for app_dir, filenames in _KNOWN_APP_DIRS.items():
            candidates.extend(base / app_dir / name for name in filenames)
        # Windows is case-insensitive, while development/test hosts may not be.
        # Inspect only immediate children so real Electron names such as
        # ``AxiBridge`` are found without turning this into a broad search.
        try:
            children = list(base.iterdir())[:500]
        except OSError:
            children = []
        for child in children:
            if child.is_dir():
                candidates.extend(_known_candidates_in(child))
    candidates.extend(
        (
            local / "ArcdpsLogManager" / "Settings.json",
            roaming / "ArcdpsLogManager" / "Settings.json",
        )
    )
    for raw_gw2 in gw2_dirs:
        gw2 = Path(raw_gw2)
        candidates.extend(
            (
                gw2 / "addons" / "wingman-uploader" / "settings.json",
                gw2 / "addons" / "wvw-insights" / "settings.json",
                gw2 / "addons" / "wvw-insights" / "webhooks.json",
                gw2 / "addons" / "manny-uploader" / "settings.json",
                gw2 / "addons" / "uploader" / "uploader.db",
            )
        )
    roots = portable_roots
    if roots is None:
        roots = (user_home / "Downloads", user_home / "Desktop", user_home / "Documents")
    candidates.extend(_portable_candidates(Path(root) for root in roots))

    findings: list[CompetitorFinding] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate))
        if key in seen or not candidate.is_file():
            continue
        seen.add(key)
        try:
            findings.append(parse_competitor_config(candidate))
        except CompetitorConfigError:
            # Discovery is opportunistic: stale, unrelated, or malformed files
            # are ignored. A manually chosen file receives the precise error.
            continue
    return _merge_findings(findings)


def _choose_path(paths: Sequence[Path], *, require_file: bool) -> Path | None:
    predicate = Path.is_file if require_file else Path.is_dir
    for path in paths:
        try:
            if predicate(path):
                return path
        except OSError:
            continue
    return paths[0] if paths else None


def _prefer_wvw_log_folder(path: Path | None) -> Path | None:
    """Resolve a raw-log root to its obvious WvW child without drive scanning."""
    return select_wvw_log_directory(path) if path is not None else None


def _choose_webhook(
    hooks: Sequence[ImportedWebhook], role: WebhookRole, *, exclude_url: str = ""
) -> ImportedWebhook | None:
    candidates = [hook for hook in hooks if hook.url != exclude_url]
    for preferred_role in (role, "unknown"):
        preferred = [
            hook
            for hook in candidates
            if hook.role == preferred_role and hook.preferred
        ]
        if preferred:
            return preferred[0]
        matching = [hook for hook in candidates if hook.role == preferred_role]
        if matching:
            return matching[0]
    return None


def build_import_plan(finding: CompetitorFinding) -> CompetitorImportPlan:
    """Make the obvious selections; the UI previews and can override them."""
    fight = _choose_webhook(finding.webhooks, "fight")
    nightly = _choose_webhook(
        finding.webhooks, "nightly", exclude_url=fight.url if fight else ""
    )
    # One existing webhook is still a complete functional setup: both kinds of
    # report go to it until the user adds a second destination later.
    if fight is None and nightly is not None:
        fight = nightly
    if nightly is None and fight is not None:
        nightly = fight
    log_root = _choose_path(finding.log_folders, require_file=False)
    return CompetitorImportPlan(
        finding=finding,
        log_folder=_prefer_wvw_log_folder(log_root),
        parser_executable=_choose_path(
            finding.parser_executables, require_file=True
        ),
        fight_webhook=fight,
        nightly_webhook=nightly,
        settings=finding.settings,
    )


def merge_competitor_findings(
    findings: Sequence[CompetitorFinding], *, primary_index: int
) -> CompetitorFinding:
    """Combine detected setups, using one user-selected source for conflicts.

    The selected source is prioritized only where two tools provide different
    values. Missing paths, routes, and safe preferences are still filled from
    every other finding. The result is an ordinary ``CompetitorFinding`` so the
    existing grouped consent preview and single transactional apply path remain
    authoritative.
    """
    items = tuple(findings)
    if not items:
        raise CompetitorConfigError("No existing log-tool setups were found to combine.")
    if not isinstance(primary_index, int) or not 0 <= primary_index < len(items):
        raise CompetitorConfigError("Choose which detected log tool you use most.")

    primary = items[primary_index]
    priority_indexes = (
        primary_index,
        *(index for index in range(len(items)) if index != primary_index),
    )

    def source_name(index: int) -> str:
        finding = items[index]
        duplicate = sum(item.app == finding.app for item in items) > 1
        if duplicate and finding.source_files:
            return f"{finding.app} at {finding.source_file.parent}"
        return finding.app

    conflict_notes: list[str] = []

    def merge_paths(attribute: str, label: str) -> tuple[Path, ...]:
        providers = [
            index for index, item in enumerate(items) if getattr(item, attribute)
        ]
        if not providers:
            return ()
        winner = primary_index if primary_index in providers else providers[0]
        representatives = {
            os.path.normcase(str(getattr(items[index], attribute)[0]))
            for index in providers
        }
        if len(representatives) > 1:
            conflict_notes.append(
                f"{label} differed; prioritizing {source_name(winner)}."
            )
        ordered_indexes = (winner, *(index for index in providers if index != winner))
        return _unique_paths(
            path
            for index in ordered_indexes
            for path in getattr(items[index], attribute)
        )

    setting_candidates: dict[
        tuple[str, str], list[tuple[int, ImportedSetting]]
    ] = {}
    setting_keys: list[tuple[str, str]] = []
    for index in priority_indexes:
        for setting in items[index].settings:
            key = (setting.section, setting.key)
            if key not in setting_candidates:
                setting_keys.append(key)
                setting_candidates[key] = []
    for index, item in enumerate(items):
        for setting in item.settings:
            setting_candidates[(setting.section, setting.key)].append((index, setting))

    merged_settings: list[ImportedSetting] = []
    for key in setting_keys:
        candidates = setting_candidates[key]
        primary_candidates = [
            candidate for candidate in candidates if candidate[0] == primary_index
        ]
        winner_index, winner_setting = (
            primary_candidates[0] if primary_candidates else candidates[0]
        )
        merged_settings.append(winner_setting)
        if len({setting.value for _index, setting in candidates}) > 1:
            conflict_notes.append(
                f"{winner_setting.label} differed; using "
                f"{source_name(winner_index)}: {winner_setting.display_value}."
            )

    def route_candidate(
        role: WebhookRole,
    ) -> tuple[ImportedWebhook | None, int | None, list[tuple[int, ImportedWebhook]]]:
        explicit: list[tuple[int, ImportedWebhook]] = []
        unknown: list[tuple[int, ImportedWebhook]] = []
        for index, item in enumerate(items):
            exact = [hook for hook in item.webhooks if hook.role == role]
            fallback = [hook for hook in item.webhooks if hook.role == "unknown"]
            if exact:
                explicit.append(
                    (index, next((hook for hook in exact if hook.preferred), exact[0]))
                )
            elif fallback:
                unknown.append(
                    (
                        index,
                        next((hook for hook in fallback if hook.preferred), fallback[0]),
                    )
                )
        candidates = explicit or unknown
        if not candidates:
            return None, None, []
        winner = next(
            (candidate for candidate in candidates if candidate[0] == primary_index),
            candidates[0],
        )
        return winner[1], winner[0], candidates

    fight, fight_source, fight_candidates = route_candidate("fight")
    nightly, nightly_source, nightly_candidates = route_candidate("nightly")
    if fight is None and nightly is not None:
        fight, fight_source = nightly, nightly_source
    if nightly is None and fight is not None:
        nightly, nightly_source = fight, fight_source

    def note_route_conflict(
        label: str,
        selected: ImportedWebhook | None,
        selected_source: int | None,
        candidates: Sequence[tuple[int, ImportedWebhook]],
    ) -> None:
        if (
            selected is not None
            and selected_source is not None
            and len({hook.url.casefold() for _index, hook in candidates}) > 1
        ):
            conflict_notes.append(
                f"{label} differed; using {source_name(selected_source)}: "
                f"{selected.display_name}."
            )

    note_route_conflict(
        "Individual fight channel", fight, fight_source, fight_candidates
    )
    note_route_conflict(
        "Nightly debrief channel", nightly, nightly_source, nightly_candidates
    )

    selected_hooks: list[ImportedWebhook] = []
    if fight is not None:
        selected_hooks.append(
            ImportedWebhook(fight.name, fight.url, "fight", preferred=True)
        )
    if nightly is not None:
        selected_hooks.append(
            ImportedWebhook(nightly.name, nightly.url, "nightly", preferred=True)
        )
    all_hooks = _unique_webhooks(
        (
            *selected_hooks,
            *(
                hook
                for index in priority_indexes
                for hook in items[index].webhooks
            ),
        )
    )
    merged_hooks = all_hooks[:3]
    if len(all_hooks) > len(merged_hooks):
        conflict_notes.append(
            f"Found {len(all_hooks)} Discord destinations; SparkyBot can save "
            "three. The consent preview shows the three that will be copied."
        )

    app_names = tuple(dict.fromkeys(item.app for item in items))
    return CompetitorFinding(
        app=" + ".join(app_names),
        source_files=_unique_paths(
            path
            for index in priority_indexes
            for path in items[index].source_files
        ),
        log_folders=merge_paths("log_folders", "Fight-file folders"),
        gw2_directories=merge_paths("gw2_directories", "Guild Wars 2 folders"),
        parser_executables=merge_paths(
            "parser_executables", "Report helpers"
        ),
        webhooks=merged_hooks,
        settings=tuple(merged_settings),
        warnings=tuple(
            dict.fromkeys(
                (
                    *(warning for item in items for warning in item.warnings),
                    *conflict_notes,
                )
            )
        ),
        tier=min(item.tier for item in items),
    )


def apply_competitor_import(
    config: Any,
    plan: CompetitorImportPlan,
    *,
    persist: bool = True,
    turn_off_optional: bool = True,
    include_discord: bool = True,
) -> None:
    """Apply the previewed plan transactionally; never touch competitor files."""
    snapshot = copy.deepcopy(config._config)
    try:
        if plan.log_folder:
            config.update("Paths", "logFolder", str(plan.log_folder))
        if plan.parser_executable:
            config.update("Paths", "gw2eiExe", str(plan.parser_executable))

        for setting in plan.settings:
            normalized = normalize_parity_setting(
                setting.section, setting.key, setting.value
            )
            if normalized is None or normalized != setting.value:
                raise CompetitorConfigError(
                    f"{setting.label} is not a safe SparkyBot setting."
                )
            # A guild-admin file remains authoritative for every Discord
            # presentation/routing value when local neighbor setup is added.
            if setting.section == "Discord" and not include_discord:
                continue
            config.update(setting.section, setting.key, setting.value)

        if include_discord and plan.fight_webhook and plan.nightly_webhook:
            fight = plan.fight_webhook
            nightly = plan.nightly_webhook
            saved = plan.saved_webhooks
            for index in range(1, 4):
                suffix = "" if index == 1 else str(index)
                hook = saved[index - 1] if index <= len(saved) else None
                config.update(
                    "Discord", f"discordWebhook{suffix}", hook.url if hook else ""
                )
                config.update(
                    "Discord",
                    f"discordWebhookName{index}",
                    hook.display_name if hook else "",
                )
            nightly_index = next(
                (
                    index
                    for index, hook in enumerate(saved, 1)
                    if hook.url == nightly.url
                ),
                1,
            )
            config.update("Discord", "activeDiscordWebhook", "1")
            config.update(
                "Discord", "raidReportDiscordWebhook", str(nightly_index)
            )
            config.update("Discord", "enableDiscordBot", "true")

        if turn_off_optional:
            config.update("AI", "enableAiAnalysis", "false")
            config.update("TTS", "enableTts", "false")
            config.update("Twitch", "enableTwitchBot", "false")
            config.update("RaidReport", "runMode", "run-button")

        if persist:
            if not config.save():
                raise CompetitorConfigError(
                    "SparkyBot could not save the imported setup."
                )
        else:
            config._load_values()
    except Exception as exc:
        config._config = snapshot
        config._load_values()
        if isinstance(exc, CompetitorConfigError):
            raise
        raise CompetitorConfigError(
            f"SparkyBot could not apply the imported setup: {exc}"
        ) from exc


SUPPORTED_COMPETITOR_CONFIGS = tuple(
    target.name for target in COMPETITOR_IMPORT_TARGETS
)
