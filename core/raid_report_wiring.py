"""Raid Report wiring — builds RaidReportTab with real dependencies and CLI headless entry."""

import json
import logging
import uuid
from pathlib import Path

import requests as _requests

from core.raid_session import (
    RaidReportCache,
    current_session,
    discover_logs,
    recent_logs,
    today_logs,
)

logger = logging.getLogger(__name__)


def _resolve_viewer(config):
    """Lazy viewer resolver — called AFTER combiner.ensure_installed().

    Resolution order:
      1. Config path (if set and exists).
      2. Top_Stats_Index.html inside installed combiner dir.
      3. RuntimeError — only when both paths are truly unavailable.
    """
    if config.raidreport_viewer_html:
        p = Path(config.raidreport_viewer_html)
        if not p.exists():
            logger.error("Configured viewer file not found: %s", p)
            raise RuntimeError(
                "The Raid Report could not find its stats page at the "
                "configured location. Clear the path in Settings > "
                "Raid Reports to use the automatic one instead."
            )
        return p

    from core.apppaths import local_machine_dir

    combiner_root = local_machine_dir() / "RaidReportData" / "combiner"
    try:
        metadata = json.loads(
            (combiner_root / "meta.json").read_text(encoding="utf-8")
        )
        current_dir = combiner_root / str(metadata["version"])
        for candidate in current_dir.rglob("Top_Stats_Index.html"):
            return candidate
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        pass

    for candidate in combiner_root.rglob("Top_Stats_Index.html"):
        return candidate

    logger.error("Stats page not found after combiner install in %s", combiner_root)
    raise RuntimeError(
        "The Raid Report stats page could not be located. "
        "Please try again or set the path in Settings > Raid Reports."
    )


def make_runner(config):
    """RaidReportRunner wired to real dependencies.

    The ONE runner construction path — shared by the Raid Report page and
    the run-session End Run flow (no parallel backend, ever).
    """
    from core.gw2ei_invoker import GW2EIInvoker
    from core.combiner_manager import CombinerManager
    from core.apppaths import local_machine_dir
    from core.raid_report import RaidReportRunner

    cache = RaidReportCache(config.get_raidreport_cache_dir())
    invoker = GW2EIInvoker(config)

    def _parse_log(log_path):
        cfg_name = f"raidreport_{uuid.uuid4().hex[:8]}.conf"
        return invoker.parse_file(log_path, config_name=cfg_name)

    ei_version, fingerprint = invoker.cache_key()

    combiner = CombinerManager(local_machine_dir() / "RaidReportData")

    output_dir = config.get_raidreport_output_dir()
    log_folder = config.get_log_folders()
    log_folder = log_folder[0] if log_folder else Path(".")

    augment_json = None
    if getattr(config, 'raidreport_poison_tab', False):
        from core.poison_tab import augment_file
        augment_json = augment_file

    return RaidReportRunner(
        log_folder=log_folder,
        cache=cache,
        parse_log=_parse_log,
        ei_version=ei_version,
        settings_fingerprint=fingerprint,
        combiner=combiner,
        viewer_html=Path("."),
        viewer_factory=lambda: _resolve_viewer(config),
        output_dir=output_dir,
        guild_name="",
        guild_id="",
        api_key="",
        augment_json=augment_json,
    )


def publish_result(config, result):
    """Post a generated report to the active Discord webhook.

    The ONE publish path — shared by the Raid Report page and the
    run-session End Run flow.
    """
    from core.report_publisher import publish_report
    from core.discord_bot import DiscordWebhookManager
    from core.raid_report import make_publish_caption

    dm = DiscordWebhookManager(config)
    destination = config.get_raid_report_discord_webhook_index()
    bot = dm.get_webhook(destination)
    if bot is None:
        raise RuntimeError("No active Discord webhook configured")
    caption = make_publish_caption(result)

    # Wrap-up embed / AI zingers / voice-recap mp3 are PARKED until the
    # recap is respec'd with data worth reporting (operator, 2026-07-19).
    # Deliberately not config-gated: configs written by older versions
    # have the old true defaults baked in (save() persists every key),
    # so a config flag cannot be trusted to keep this off.
    publish_report(
        result.html_path,
        send_file=bot.send_file,
        caption=caption,
        always_zip=getattr(config, 'raidreport_always_zip', False),
    )


def build_raid_report_tab(config, parent=None):
    """Construct RaidReportTab wired to real dependencies."""
    from core.raid_report_tab import RaidReportTab

    def _discover():
        folders = config.get_log_folders()
        if not folders:
            return []
        return discover_logs(folders[0])

    def _select_session(logs):
        return current_session(logs)

    def _select_recent(logs):
        return recent_logs(logs)

    def _select_today(logs):
        return today_logs(logs)

    return RaidReportTab(
        discover=_discover,
        select_session=_select_session,
        select_recent=_select_recent,
        select_today=_select_today,
        runner_factory=lambda: make_runner(config),
        publish=lambda result: publish_result(config, result),
        parent=parent,
    )


def run_headless_raid_report(config):
    """CLI entry: discover → recent → generate → return baked HTML path.

    Prints progress via logging. No Qt imports.
    """
    folders = config.get_log_folders()
    if not folders:
        raise SystemExit("No log folders configured. Set one in Settings → Paths.")
    log_folder = folders[0]

    logs = discover_logs(log_folder)
    selected = recent_logs(logs)
    if not selected:
        raise SystemExit("No logs found in the last 12 hours.")

    from core.gw2ei_invoker import GW2EIInvoker
    from core.combiner_manager import CombinerManager
    from core.apppaths import local_machine_dir
    from core.raid_report import RaidReportRunner

    cache = RaidReportCache(config.get_raidreport_cache_dir())
    invoker = GW2EIInvoker(config)

    def _parse_log(log_path):
        cfg_name = f"raidreport_cli_{uuid.uuid4().hex[:8]}.conf"
        return invoker.parse_file(log_path, config_name=cfg_name)

    ei_version, fingerprint = invoker.cache_key()
    combiner = CombinerManager(local_machine_dir() / "RaidReportData")

    output_dir = config.get_raidreport_output_dir()

    augment_json = None
    if getattr(config, 'raidreport_poison_tab', False):
        from core.poison_tab import augment_file
        augment_json = augment_file

    runner = RaidReportRunner(
        log_folder=log_folder,
        cache=cache,
        parse_log=_parse_log,
        ei_version=ei_version,
        settings_fingerprint=fingerprint,
        combiner=combiner,
        viewer_html=Path("."),
        viewer_factory=lambda: _resolve_viewer(config),
        output_dir=output_dir,
        guild_name="",
        guild_id="",
        api_key="",
        augment_json=augment_json,
    )

    name = f"Raid Report {selected[0].timestamp:%Y-%m-%d} ({len(selected)} fights)"
    logger.info("Raid Report: generating %s with %d logs", name, len(selected))

    result = runner.generate(selected, report_name=name)
    logger.info("Raid Report done: %s", result.html_path)
    return result.html_path


def _fetch_zingers(config, leaders, timeout=20):
    import json as _json
    import re as _re

    base_url = config.ai_base_url.strip()
    api_key = config.ai_api_key.strip()
    model = config.ai_model.strip()
    if not base_url or not model:
        return None

    prompt_file = Path(__file__).resolve().parent.parent / "prompts" / "raid_wrapup_zingers.txt"
    system_prompt = prompt_file.read_text(encoding="utf-8")

    from core.raid_wrapup import build_zinger_prompt
    user_prompt = build_zinger_prompt(leaders)

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 500,
    }

    try:
        response = _requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except Exception:
        return None

    if response.status_code != 200:
        return None

    try:
        data = response.json()
    except Exception:
        return None

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None

    content = content.strip()
    if content.startswith("```"):
        content = _re.sub(r"^```(?:json)?\s*", "", content)
        content = _re.sub(r"\s*```$", "", content)

    try:
        zingers = _json.loads(content)
    except Exception:
        return None

    if not isinstance(zingers, list):
        return None
    return [str(z) for z in zingers]


def _generate_recap_mp3(config, result, leaders, zingers):
    from core.raid_wrapup import compose_recap_script
    from core.tts import generate_tts_bytes

    script = compose_recap_script(result.name, leaders, zingers)
    audio_bytes = generate_tts_bytes(script, config)
    if not audio_bytes:
        return None

    mp3_path = result.html_path.with_suffix(".recap.mp3")
    mp3_path.write_bytes(audio_bytes)
    return mp3_path
