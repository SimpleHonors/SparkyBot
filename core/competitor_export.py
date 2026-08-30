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

from core.competitor_import import CompetitorConfigError, MAX_CONFIG_BYTES


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
        "fight-log folder, individual-fight Discord route, and nightly report route",
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
        "fight-log folder and Discord destinations",
        "nightly scheduling, AI, voice, Twitch, and private credentials",
    ),
    CompetitorExportTarget(
        "mzfightreporter",
        "MzFightReporter",
        ("config.properties",),
        "fight-log folder and up to three named Discord destinations",
        "nightly scheduling, AI, voice, and private credentials",
    ),
    CompetitorExportTarget(
        "wvw-insights",
        "WvW Insights",
        ("settings.json", "webhooks.json"),
        "fight-log folder and saved Discord destinations",
        "nightly scheduling, AI, voice, Twitch, and private credentials",
    ),
    CompetitorExportTarget(
        "evtc-parser",
        "EVTC_parser",
        ("config.ini",),
        "fight-log folder and individual-fight Discord route",
        "nightly routing and every optional SparkyBot feature",
    ),
)

_TARGETS = {target.key: target for target in COMPETITOR_EXPORT_TARGETS}


@dataclass(frozen=True)
class _SparkyValues:
    log_folder: str
    webhooks: tuple[tuple[str, str], ...]
    fight_index: int
    nightly_index: int

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
    elif target.key != "topstatsaio":
        destinations = [name for name, url in values.webhooks if url]
        lines.append(
            "Discord destinations: " + ", ".join(destinations)
            if destinations
            else "Discord destinations: none configured"
        )
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
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise CompetitorConfigError(f"Refusing to replace non-regular file: {path}")
        try:
            parser.read(path, encoding="utf-8-sig")
        except (OSError, UnicodeError, configparser.Error) as exc:
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
    if not parser.has_section("Settings"):
        parser.add_section("Settings")
    parser.set("Settings", "ARCDPS_LOG_DIR", values.log_folder)
    parser.set("Settings", "WEBHOOK_URL", values.fight[1] if values.fight else "")
    from io import StringIO

    buffer = StringIO()
    parser.write(buffer)
    return {path: buffer.getvalue()}


_BUILDERS: dict[str, Callable[[Path, _SparkyValues], dict[Path, str]]] = {
    "axibridge": _patch_axibridge,
    "topstatsaio": _patch_topstats,
    "plenbot": _patch_plenbot,
    "mzfightreporter": _patch_mz,
    "wvw-insights": _patch_wvw_insights,
    "evtc-parser": _patch_evtc,
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
