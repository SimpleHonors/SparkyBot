"""Reciprocal, non-locking exports to established WvW log tools.

Exports are intentionally narrow.  They write only fields whose meaning was
confirmed in the target project's source.  Existing files are patched rather
than replaced wholesale, backed up before the atomic write, and restored if a
multi-file export fails.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core.competitor_import import (
    CompetitorConfigError,
    MAX_CONFIG_BYTES,
    describe_parity_setting,
    normalize_parity_setting,
)


@dataclass(frozen=True)
class CompetitorExportTarget:
    key: str
    name: str
    filenames: tuple[str, ...]
    transfers: str
    limitation: str


@dataclass(frozen=True)
class CompetitorExportResult:
    target: CompetitorExportTarget
    written_files: tuple[Path, ...]
    backup_files: tuple[Path, ...]
    limitation: str

    def summary(self) -> str:
        lines = [f"{self.target.name} setup is ready.", "", "Saved to:"]
        lines.extend(str(path) for path in self.written_files)
        if self.backup_files:
            lines.extend(("", "Backups kept:"))
            lines.extend(str(path) for path in self.backup_files)
        if self.limitation:
            lines.extend(("", f"Not included: {self.limitation}"))
        lines.extend(("", "Your SparkyBot setup was not changed."))
        return "\n".join(lines)


COMPETITOR_EXPORT_TARGETS = (
    CompetitorExportTarget(
        "axibridge",
        "AxiBridge",
        ("config.json",),
        "fight-log folder, Discord routes, and matching report/display preferences",
        "AI, voice, Twitch, API keys, tokens, and SparkyBot-only behavior",
    ),
    CompetitorExportTarget(
        "topstatsaio",
        "TopStatsAIO",
        ("ui-state.json",),
        "raw ArcDPS log folder",
        "Discord routing (TopStatsAIO asks for it per run rather than storing it here)",
    ),
    CompetitorExportTarget(
        "plenbot",
        "PlenBot Log Uploader",
        ("app_settings.json", "discord_webhooks.json"),
        "fight-log folder, Discord destinations, and close/minimize behavior",
        "nightly scheduling, AI, voice, Twitch, and private credentials",
    ),
    CompetitorExportTarget(
        "mzfightreporter",
        "MzFightReporter",
        ("config.properties",),
        "fight-log folder, named Discord destinations, and matching report, tray, and non-secret Twitch preferences",
        "nightly scheduling, AI, voice, and private credentials",
    ),
    CompetitorExportTarget(
        "wvw-insights",
        "WvW Insights",
        ("settings.json", "webhooks.json"),
        "fight-log folder, saved Discord destinations, and guild display name",
        "nightly scheduling, AI, voice, Twitch, and private credentials",
    ),
    CompetitorExportTarget(
        "evtc-parser",
        "EVTC_parser",
        ("config.ini",),
        "fight-log folder and individual-fight Discord route",
        "nightly routing and every optional SparkyBot feature",
    ),
    CompetitorExportTarget(
        "gw2-ei-combiner",
        "GW2 EI Log Combiner",
        ("top_stats_config.ini",),
        "nightly debrief Discord route and guild display name",
        "raw ArcDPS logs, generated-EI input paths, AI, voice, Twitch, and private credentials",
    ),
)

_TARGETS = {target.key: target for target in COMPETITOR_EXPORT_TARGETS}


@dataclass(frozen=True)
class _SparkyValues:
    log_folder: str
    webhooks: tuple[tuple[str, str], ...]
    fight_index: int
    nightly_index: int
    settings: tuple[tuple[str, str, str], ...]

    def route(self, index: int) -> tuple[str, str] | None:
        if index < 1 or index > len(self.webhooks):
            return None
        name, url = self.webhooks[index - 1]
        return (name, url) if url else None

    @property
    def fight(self) -> tuple[str, str] | None:
        return self.route(self.fight_index)

    @property
    def nightly(self) -> tuple[str, str] | None:
        return self.route(self.nightly_index) or self.fight

    def setting(self, section: str, key: str) -> str | None:
        return next(
            (
                value
                for saved_section, saved_key, value in self.settings
                if saved_section == section and saved_key == key
            ),
            None,
        )


_PARITY_CONFIG_KEYS = (
    ("Discord", "discordWebhookLabel"),
    ("Discord", "embedColor"),
    ("Thresholds", "minFightDuration"),
    ("Thresholds", "minFightDowns"),
    ("Thresholds", "minFightTotalDmg"),
    ("UI", "showDamage"),
    ("UI", "showHeals"),
    ("UI", "showDefense"),
    ("UI", "showCCs"),
    ("UI", "showStrips"),
    ("UI", "showCleanses"),
    ("UI", "showDownsKills"),
    ("UI", "showBurstDmg"),
    ("UI", "showTopEnemySkills"),
    ("UI", "showOffensiveBoons"),
    ("UI", "showDefensiveBoons"),
    ("UI", "showEnemyBreakdown"),
    ("UI", "showQuickReport"),
    ("Behavior", "closeToTray"),
    ("Behavior", "minimizeToTray"),
    ("Behavior", "startMinimized"),
    ("Behavior", "maxParseMemory"),
    ("Twitch", "twitchChannelName"),
    ("Twitch", "twitchUseTLS"),
)

_TARGET_PARITY_KEYS = {
    "axibridge": (
        ("UI", "showDamage"),
        ("UI", "showHeals"),
        ("UI", "showCleanses"),
        ("UI", "showStrips"),
        ("UI", "showCCs"),
        ("UI", "showDownsKills"),
        ("Behavior", "closeToTray"),
    ),
    "plenbot": (
        ("Behavior", "closeToTray"),
        ("Behavior", "minimizeToTray"),
    ),
    "mzfightreporter": tuple(
        item
        for item in _PARITY_CONFIG_KEYS
        if item != ("Discord", "discordWebhookLabel")
    ),
    "wvw-insights": (("Discord", "discordWebhookLabel"),),
    "gw2-ei-combiner": (("Discord", "discordWebhookLabel"),),
}


def _parity_values_from_config(config: Any) -> tuple[tuple[str, str, str], ...]:
    parser = getattr(config, "_config", None)
    if parser is None:
        return ()
    result: list[tuple[str, str, str]] = []
    for section, key in _PARITY_CONFIG_KEYS:
        try:
            raw = parser.get(section, key)
        except (configparser.Error, KeyError, AttributeError):
            continue
        value = normalize_parity_setting(section, key, raw)
        if value is not None:
            result.append((section, key, value))
    return tuple(result)


def _values_from_config(config: Any) -> _SparkyValues:
    names = (
        getattr(config, "discord_webhook_name1", ""),
        getattr(config, "discord_webhook_name2", ""),
        getattr(config, "discord_webhook_name3", ""),
    )
    urls = (
        getattr(config, "discord_webhook", ""),
        getattr(config, "discord_webhook2", ""),
        getattr(config, "discord_webhook3", ""),
    )
    webhooks = tuple(
        ((str(name).strip() or f"Destination {index}"), str(url).strip())
        for index, (name, url) in enumerate(zip(names, urls), 1)
    )
    fight_index = getattr(config, "active_discord_webhook", 1)
    nightly_index = getattr(config, "raid_report_discord_webhook", 0)
    if nightly_index not in (1, 2, 3):
        nightly_index = fight_index
    return _SparkyValues(
        log_folder=str(getattr(config, "log_folder", "") or "").strip(),
        webhooks=webhooks,
        fight_index=fight_index if fight_index in (1, 2, 3) else 1,
        nightly_index=nightly_index,
        settings=_parity_values_from_config(config),
    )


def export_preview(config: Any, target_key: str) -> str:
    target = _TARGETS.get(target_key)
    if target is None:
        raise CompetitorConfigError(f"Unknown export target: {target_key}")
    values = _values_from_config(config)
    lines = [f"Fight logs: {values.log_folder or 'not configured'}"]
    if target.key == "axibridge":
        lines.append(
            f"Individual fights will post to: {values.fight[0]}"
            if values.fight
            else "Individual fights: no Discord destination configured"
        )
        lines.append(
            f"Nightly debrief will post to: {values.nightly[0]}"
            if values.nightly
            else "Nightly debrief: no Discord destination configured"
        )
    elif target.key == "evtc-parser":
        lines.append(
            f"Individual fights will post to: {values.fight[0]}"
            if values.fight
            else "Individual fights: no Discord destination configured"
        )
    elif target.key == "gw2-ei-combiner":
        lines.append(
            f"Nightly debrief will post to: {values.nightly[0]}"
            if values.nightly
            else "Nightly debrief: no Discord destination configured"
        )
    elif target.key != "topstatsaio":
        destinations = [name for name, url in values.webhooks if url]
        lines.append(
            "Discord destinations: " + ", ".join(destinations)
            if destinations
            else "Discord destinations: none configured"
        )
    preview_settings = [
        (section, describe_parity_setting(section, key, values.setting(section, key)))
        for section, key in _TARGET_PARITY_KEYS.get(target.key, ())
    ]
    preview_settings = [
        (section, description)
        for section, description in preview_settings
        if description is not None
    ]
    if preview_settings:
        lines.append("Matching preferences:")
        current_section = ""
        for section, description in preview_settings:
            if section != current_section:
                current_section = section
                lines.append(f"  {section}:")
            label, display_value = description
            lines.append(f"    {label}: {display_value}")
    lines.append(f"Stays in SparkyBot: {target.limitation}")
    return "\n".join(lines)


def _read_existing_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    if path.is_symlink() or not path.is_file():
        raise CompetitorConfigError(f"Refusing to replace non-regular file: {path}")
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise CompetitorConfigError(f"{path.name} is too large to patch safely.")
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, CompetitorConfigError):
            raise
        raise CompetitorConfigError(
            f"Could not safely read the existing {path.name}."
        ) from exc


def _json_payload(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _upsert_url_object(
    items: list[Any],
    *,
    name: str,
    url: str,
    base_id: str | None = None,
    updates: dict[str, Any] | None = None,
    defaults: dict[str, Any] | None = None,
) -> tuple[list[Any], str | None]:
    """Merge one route by URL while preserving every neighbor-owned field."""
    merged_items = [dict(item) if isinstance(item, dict) else item for item in items]
    used_ids = {
        str(item.get("id"))
        for item in merged_items
        if isinstance(item, dict) and item.get("id") is not None
    }
    for index, item in enumerate(merged_items):
        if not isinstance(item, dict) or str(item.get("url", "")).strip() != url:
            continue
        merged = dict(item)
        merged.update({"name": name, "url": url})
        if base_id and not merged.get("id"):
            candidate = base_id
            suffix = 2
            while candidate in used_ids:
                candidate = f"{base_id}-{suffix}"
                suffix += 1
            merged["id"] = candidate
        for key, value in (defaults or {}).items():
            merged.setdefault(key, value)
        merged.update(updates or {})
        merged_items[index] = merged
        return merged_items, str(merged.get("id")) if merged.get("id") else None

    payload: dict[str, Any] = {"name": name, "url": url}
    if base_id:
        candidate = base_id
        suffix = 2
        while candidate in used_ids:
            candidate = f"{base_id}-{suffix}"
            suffix += 1
        payload["id"] = candidate
    payload.update(defaults or {})
    payload.update(updates or {})
    merged_items.append(payload)
    return merged_items, str(payload.get("id")) if payload.get("id") else None


def _patch_axibridge(directory: Path, values: _SparkyValues) -> dict[Path, str]:
    path = directory / "config.json"
    data = _read_existing_json(path, {})
    if not isinstance(data, dict):
        raise CompetitorConfigError("AxiBridge config.json must contain an object.")
    data["logDirectory"] = values.log_folder
    fight = values.fight
    nightly = values.nightly
    if fight:
        existing_fights = data.get("webhooks", [])
        if not isinstance(existing_fights, list):
            raise CompetitorConfigError("AxiBridge webhooks must contain a list.")
        merged_fights, selected_id = _upsert_url_object(
            existing_fights,
            name=fight[0],
            url=fight[1],
            base_id="sparkybot-fight",
        )
        data["discordWebhookUrl"] = fight[1]
        data["webhooks"] = merged_fights
        if selected_id:
            data["selectedWebhookId"] = selected_id
    if nightly:
        existing_reports = data.get("reportWebhooks", [])
        if not isinstance(existing_reports, list):
            raise CompetitorConfigError(
                "AxiBridge reportWebhooks must contain a list."
            )
        data["reportWebhooks"], _report_id = _upsert_url_object(
            existing_reports,
            name=nightly[0],
            url=nightly[1],
            base_id="sparkybot-nightly",
            updates={"enabled": True},
            defaults={
                "isForum": False,
                "titleTemplate": "{date} - {day_of_week} - {commander}",
            },
        )
    embed_settings = data.get("embedStatSettings", {})
    if not isinstance(embed_settings, dict):
        raise CompetitorConfigError(
            "AxiBridge embedStatSettings must contain an object."
        )
    embed_mappings = (
        ("UI", "showDamage", "showDamage"),
        ("UI", "showHeals", "showHealing"),
        ("UI", "showCleanses", "showCleanses"),
        ("UI", "showStrips", "showBoonStrips"),
        ("UI", "showCCs", "showCC"),
    )
    for section, key, axi_key in embed_mappings:
        value = values.setting(section, key)
        if value is not None:
            embed_settings[axi_key] = value == "true"
    downs_kills = values.setting("UI", "showDownsKills")
    if downs_kills is not None:
        embed_settings["showDowns"] = downs_kills == "true"
        embed_settings["showKills"] = downs_kills == "true"
    data["embedStatSettings"] = embed_settings
    close_to_tray = values.setting("Behavior", "closeToTray")
    if close_to_tray is not None:
        data["closeBehavior"] = "minimize" if close_to_tray == "true" else "quit"
    return {path: _json_payload(data)}


def _patch_topstats(directory: Path, values: _SparkyValues) -> dict[Path, str]:
    path = directory / "ui-state.json"
    data = _read_existing_json(path, {})
    if not isinstance(data, dict):
        raise CompetitorConfigError("TopStatsAIO ui-state.json must contain an object.")
    data["lastFolder"] = values.log_folder
    return {path: _json_payload(data)}


def _patch_plenbot(directory: Path, values: _SparkyValues) -> dict[Path, str]:
    settings_path = directory / "app_settings.json"
    settings = _read_existing_json(settings_path, {})
    if not isinstance(settings, dict):
        raise CompetitorConfigError("PlenBot app_settings.json must contain an object.")
    settings["logsLocation"] = values.log_folder
    close_to_tray = values.setting("Behavior", "closeToTray")
    minimize_to_tray = values.setting("Behavior", "minimizeToTray")
    if close_to_tray is not None:
        settings["closeToTry"] = close_to_tray == "true"
    if minimize_to_tray is not None:
        settings["minimiseToTry"] = minimize_to_tray == "true"

    hooks_path = directory / "discord_webhooks.json"
    existing_hooks = _read_existing_json(hooks_path, [])
    if not isinstance(existing_hooks, list):
        raise CompetitorConfigError("PlenBot discord_webhooks.json must contain a list.")
    hooks = [dict(item) if isinstance(item, dict) else item for item in existing_hooks]
    seen: set[str] = set()
    for name, url in values.webhooks:
        if not url or url in seen:
            continue
        seen.add(url)
        is_fight = bool(values.fight and url == values.fight[1])
        hooks, _unused_id = _upsert_url_object(
            hooks,
            name=name,
            url=url,
            updates={"isActive": True} if is_fight else {},
            defaults={
                "isActive": False,
                "successFailToggle": 0,
                "summaryType": 0,
                "disabledBosses": [],
                "allowUnknownBossIds": True,
                "teamId": 0,
                "includeNormalLogs": True,
                "includeChallengeModeLogs": True,
                "includeLegendaryChallengeModeLogs": True,
            },
        )
    return {
        settings_path: _json_payload(settings),
        hooks_path: _json_payload(hooks),
    }


def _parse_properties(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"\s*([^#!:=\s]+)\s*[:=]\s*(.*)$", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def _patch_properties_text(text: str, updates: dict[str, str]) -> str:
    remaining = dict(updates)
    lines: list[str] = []
    for line in text.splitlines():
        match = re.match(r"(\s*)([^#!:=\s]+)(\s*[:=]\s*)(.*)$", line)
        if match and match.group(2) in remaining:
            key = match.group(2)
            lines.append(f"{match.group(1)}{key}{match.group(3)}{remaining.pop(key)}")
        else:
            lines.append(line)
    lines.extend(f"{key}={value}" for key, value in remaining.items())
    return "\n".join(lines).rstrip() + "\n"


def _read_existing_text(path: Path, description: str) -> str:
    if not path.exists():
        return ""
    if path.is_symlink() or not path.is_file():
        raise CompetitorConfigError(f"Refusing to replace non-regular file: {path}")
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise CompetitorConfigError(f"{path.name} is too large to patch safely.")
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        if isinstance(exc, CompetitorConfigError):
            raise
        raise CompetitorConfigError(f"Could not read {description} settings.") from exc


def _patch_ini_section(text: str, section: str, updates: dict[str, str]) -> str:
    """Patch one INI section without erasing comments or unrelated settings."""
    remaining = dict(updates)
    rendered: list[str] = []
    in_target = False
    section_seen = False

    def append_missing() -> None:
        rendered.extend(f"{key} = {value}" for key, value in remaining.items())
        remaining.clear()

    for line in text.splitlines():
        stripped = line.strip()
        section_match = re.fullmatch(r"\[([^]]+)]", stripped)
        if section_match:
            if in_target:
                append_missing()
            in_target = section_match.group(1).strip().casefold() == section.casefold()
            section_seen = section_seen or in_target
            rendered.append(line)
            continue
        if in_target and stripped and not stripped.startswith(("#", ";")):
            key_match = re.match(r"(\s*)([^:=\s]+)(\s*[:=]\s*)(.*)$", line)
            if key_match:
                existing_key = key_match.group(2)
                wanted = next(
                    (
                        candidate
                        for candidate in remaining
                        if candidate.casefold() == existing_key.casefold()
                    ),
                    None,
                )
                if wanted is not None:
                    rendered.append(
                        f"{key_match.group(1)}{existing_key}"
                        f"{key_match.group(3)}{remaining.pop(wanted)}"
                    )
                    continue
        rendered.append(line)

    if in_target:
        append_missing()
    if remaining and not section_seen:
        if rendered and rendered[-1].strip():
            rendered.append("")
        rendered.append(f"[{section}]")
        append_missing()
    return "\n".join(rendered).rstrip("\n") + "\n"


_MZ_EXPORT_MAPPINGS = (
    ("Discord", "embedColor", "embedColor"),
    ("Thresholds", "minFightDuration", "minFightDuration"),
    ("Thresholds", "minFightDowns", "minFightDowns"),
    ("Thresholds", "minFightTotalDmg", "minFightTotalDmg"),
    ("UI", "showDamage", "showDamage"),
    ("UI", "showHeals", "showHeals"),
    ("UI", "showDefense", "showDefense"),
    ("UI", "showCCs", "showCCs"),
    ("UI", "showStrips", "showStrips"),
    ("UI", "showCleanses", "showCleanses"),
    ("UI", "showDownsKills", "showDownsKills"),
    ("UI", "showBurstDmg", "showBurstDmg"),
    ("UI", "showTopEnemySkills", "showTopEnemySkills"),
    ("UI", "showOffensiveBoons", "showOffensiveBoons"),
    ("UI", "showDefensiveBoons", "showDefensiveBoons"),
    ("UI", "showEnemyBreakdown", "showEnemyBreakdown"),
    ("UI", "showQuickReport", "showQuickReport"),
    ("Behavior", "closeToTray", "closeToTray"),
    ("Behavior", "minimizeToTray", "minimizeToTray"),
    ("Behavior", "startMinimized", "startMinimized"),
    ("Behavior", "maxParseMemory", "maxParseMemory"),
    ("Twitch", "twitchChannelName", "twitchChannelName"),
    ("Twitch", "twitchUseTLS", "twitchUseTLS"),
)


def _patch_mz(directory: Path, values: _SparkyValues) -> dict[Path, str]:
    path = directory / "config.properties"
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise CompetitorConfigError(f"Refusing to replace non-regular file: {path}")
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise CompetitorConfigError("Could not read MzFightReporter settings.") from exc
    else:
        text = ""
    current = _parse_properties(text)
    log_key = (
        "customLogFolder"
        if current.get("customLogFolder", "").strip()
        else "defaultLogFolder"
    )
    updates = {log_key: values.log_folder}
    for section, key, mz_key in _MZ_EXPORT_MAPPINGS:
        value = values.setting(section, key)
        if value is not None:
            updates[mz_key] = value
    slots: dict[int, tuple[str, str]] = {}
    for index in range(1, 4):
        suffix = "" if index == 1 else str(index)
        slots[index] = (
            current.get(f"discordWebhookLabel{suffix}", ""),
            current.get(f"discordWebhook{suffix}", "").strip(),
        )
    used_urls: set[str] = set()
    route_slots: dict[str, int] = {}
    for name, url in values.webhooks:
        if not url or url in used_urls:
            continue
        used_urls.add(url)
        slot = next(
            (index for index, (_old_name, old_url) in slots.items() if old_url == url),
            None,
        )
        if slot is None:
            slot = next(
                (index for index, (_old_name, old_url) in slots.items() if not old_url),
                None,
            )
        if slot is None:
            raise CompetitorConfigError(
                "MzFightReporter already uses all three Discord slots. Choose "
                "a separate export folder so none of its destinations are replaced."
            )
        suffix = "" if slot == 1 else str(slot)
        updates[f"discordWebhook{suffix}"] = url
        updates[f"discordWebhookLabel{suffix}"] = name
        slots[slot] = (name, url)
        route_slots[url] = slot
    if values.fight and values.fight[1] in route_slots:
        updates["activeDiscordWebhook"] = str(route_slots[values.fight[1]])
    return {path: _patch_properties_text(text, updates)}


def _patch_wvw_insights(directory: Path, values: _SparkyValues) -> dict[Path, str]:
    settings_path = directory / "settings.json"
    settings = _read_existing_json(settings_path, {})
    if not isinstance(settings, dict):
        raise CompetitorConfigError("WvW Insights settings.json must contain an object.")
    settings["log_directory"] = values.log_folder
    guild_name = values.setting("Discord", "discordWebhookLabel")
    if guild_name is not None:
        settings["guild_name"] = guild_name

    webhooks_path = directory / "webhooks.json"
    webhooks_data = _read_existing_json(webhooks_path, {})
    if not isinstance(webhooks_data, dict):
        raise CompetitorConfigError("WvW Insights webhooks.json must contain an object.")
    existing_destinations = webhooks_data.get("saved_webhooks", [])
    if not isinstance(existing_destinations, list):
        raise CompetitorConfigError(
            "WvW Insights saved_webhooks must contain a list."
        )
    destinations = [
        dict(item) if isinstance(item, dict) else item
        for item in existing_destinations
    ]
    seen: set[str] = set()
    for name, url in values.webhooks:
        if not url or url in seen:
            continue
        seen.add(url)
        destinations, _unused_id = _upsert_url_object(
            destinations,
            name=name,
            url=url,
        )
    webhooks_data["saved_webhooks"] = destinations
    webhooks_data["last_webhook_url"] = values.fight[1] if values.fight else ""
    webhooks_data.setdefault("remember_last_webhook", True)
    return {
        settings_path: _json_payload(settings),
        webhooks_path: _json_payload(webhooks_data),
    }


def _patch_evtc(directory: Path, values: _SparkyValues) -> dict[Path, str]:
    path = directory / "config.ini"
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    text = _read_existing_text(path, "EVTC_parser")
    if path.exists():
        try:
            parser.read_string(text)
        except configparser.Error as exc:
            raise CompetitorConfigError("Could not read EVTC_parser settings.") from exc
        settings_keys = (
            {key.casefold() for key, _value in parser.items("Settings")}
            if parser.has_section("Settings")
            else set()
        )
        if "arcdps_log_dir" not in settings_keys:
            raise CompetitorConfigError(
                "That config.ini is not an existing EVTC_parser setup. Choose "
                "a separate export folder so another tool's INI is not changed."
            )
    return {
        path: _patch_ini_section(
            text,
            "Settings",
            {
                "ARCDPS_LOG_DIR": values.log_folder,
                "WEBHOOK_URL": values.fight[1] if values.fight else "",
            },
        )
    }


def _patch_gw2_ei_combiner(
    directory: Path, values: _SparkyValues
) -> dict[Path, str]:
    """Hand off the Combiner's confirmed nightly route and guild name.

    ``input_directory`` contains generated Elite Insights JSON, not ArcDPS
    encounter logs, so it is deliberately left alone.
    """
    path = directory / "top_stats_config.ini"
    text = _read_existing_text(path, "GW2 EI Log Combiner")
    if path.exists():
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(text)
        except configparser.Error as exc:
            raise CompetitorConfigError(
                "Could not read GW2 EI Log Combiner settings."
            ) from exc
        if not (parser.has_section("TopStatsCfg") or parser.has_section("DiscordCfg")):
            raise CompetitorConfigError(
                "That top_stats_config.ini is not a recognized GW2 EI Log "
                "Combiner setup. Choose a separate export folder."
            )
    nightly = values.nightly
    if not nightly:
        raise CompetitorConfigError(
            "Set a nightly Discord destination before exporting to the Combiner."
        )
    guild_name = values.setting("Discord", "discordWebhookLabel")
    updated = text
    if guild_name is not None:
        updated = _patch_ini_section(
            updated,
            "TopStatsCfg",
            {"guild_name": guild_name},
        )
    updated = _patch_ini_section(
        updated,
        "DiscordCfg",
        {"webhook_url": nightly[1]},
    )
    return {path: updated}


_BUILDERS: dict[str, Callable[[Path, _SparkyValues], dict[Path, str]]] = {
    "axibridge": _patch_axibridge,
    "topstatsaio": _patch_topstats,
    "plenbot": _patch_plenbot,
    "mzfightreporter": _patch_mz,
    "wvw-insights": _patch_wvw_insights,
    "evtc-parser": _patch_evtc,
    "gw2-ei-combiner": _patch_gw2_ei_combiner,
}


def _backup_path(path: Path) -> Path:
    base = path.with_name(path.name + ".before-sparkybot")
    candidate = base
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.before-sparkybot-{counter}")
        counter += 1
    return candidate


def _stage_payload(path: Path, payload: str) -> Path:
    fd = -1
    temp_name = ""
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return Path(temp_name)
    except (OSError, UnicodeError) as exc:
        if fd >= 0:
            os.close(fd)
        if temp_name:
            try:
                Path(temp_name).unlink()
            except OSError:
                pass
        raise CompetitorConfigError(f"Could not stage {path.name}: {exc}") from exc


def export_competitor_config(
    config: Any,
    target_key: str,
    directory: str | Path,
) -> CompetitorExportResult:
    """Create or safely patch a target tool's real settings files."""
    target = _TARGETS.get(target_key)
    builder = _BUILDERS.get(target_key)
    if target is None or builder is None:
        raise CompetitorConfigError(f"Unknown export target: {target_key}")
    values = _values_from_config(config)
    if not values.log_folder:
        raise CompetitorConfigError(
            "Set SparkyBot's fight-log folder before exporting to another tool."
        )
    output_dir = Path(directory)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CompetitorConfigError(f"Could not create {output_dir}: {exc}") from exc
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise CompetitorConfigError("Choose a real local folder for the exported setup.")

    payloads = builder(output_dir, values)
    backups: dict[Path, Path] = {}
    staged: dict[Path, Path] = {}
    replaced: list[Path] = []
    originally_missing = {path for path in payloads if not path.exists()}
    try:
        for path, payload in payloads.items():
            if path.exists():
                backup = _backup_path(path)
                shutil.copy2(path, backup)
                backups[path] = backup
            staged[path] = _stage_payload(path, payload)
        for path, temporary in staged.items():
            os.replace(temporary, path)
            replaced.append(path)
        staged.clear()
    except (OSError, CompetitorConfigError) as exc:
        for path in reversed(replaced):
            backup = backups.get(path)
            try:
                if backup and backup.exists():
                    shutil.copy2(backup, path)
                elif path in originally_missing and path.exists():
                    path.unlink()
            except OSError:
                pass
        if isinstance(exc, CompetitorConfigError):
            raise
        raise CompetitorConfigError(
            f"Could not finish the {target.name} export; existing files were restored."
        ) from exc
    finally:
        for temporary in staged.values():
            try:
                temporary.unlink()
            except OSError:
                pass

    return CompetitorExportResult(
        target=target,
        written_files=tuple(payloads),
        backup_files=tuple(backups.values()),
        limitation=target.limitation,
    )
