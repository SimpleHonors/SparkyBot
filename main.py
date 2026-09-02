#!/usr/bin/env python3
"""
SparkyBot - Guild Wars 2 Fight Log Reporter
Python port using watchdog for efficient OS-native file watching

Features:
- System tray integration
- Full GUI settings
- Discord webhook reports
- GW2EI parsing
"""

import argparse
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
import threading
import json
import ctypes
from pathlib import Path
from enum import Enum
from typing import Optional
from core.version import VERSION

# Add core module to path
sys.path.insert(0, str(Path(__file__).parent / "core"))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Signal, QObject, QThread, QTimer, Qt

from core.config import Config
from core.theme import apply_theme
from core.file_watcher import FileWatcher
from core.discord_bot import DiscordWebhookManager
from core.gw2ei_invoker import GW2EIInvoker
from core.tray_manager import TrayManager
from core.main_window import MainWindow
from core.fight_report import FightReport
from core.tts import TTSClient
from core.update_flow import UpdateFlow

# Persistent AI singletons — created once, reused across all fights
_vocab_config: Optional['VocabularyConfig'] = None
_vocab_tracker: Optional['VocabularyTracker'] = None
_session_history: Optional['SessionHistoryTracker'] = None
# v1.7.0 — most-recent AI response, threaded into the next analyze() so the
# M4 freshness engine actually fires its cross-fight phrase suppression.
_last_ai_response: Optional[str] = None
# v1.7.0 — per-(player, axis) callout cooldown so the same person can't carry
# the same outlier callout 3 fights in a row.
_callout_cooldown = None

# v1.7.3 — external relauncher used to apply staged updates on network-share
# installs. Written to the OS temp dir (LOCAL disk) and run detached AFTER this
# process hard-exits, so nothing holds the share's .py files while bootstrap
# applies the update. It waits until main.py is writable (old process fully
# gone + SMB lock released), then starts bootstrap.py which does the apply.
_RELAUNCH_SRC = r'''
import os, sys, time, subprocess

app_dir = sys.argv[1]
python = sys.argv[2]
args = sys.argv[3:]
main_py = os.path.join(app_dir, "main.py")
bootstrap = os.path.join(app_dir, "bootstrap.py")

def writable(path):
    # Opening for append needs write access but does NOT truncate. If another
    # process still holds the file (deny-write / sharing violation) this raises.
    try:
        with open(path, "ab"):
            return True
    except OSError:
        return False

# Wait up to ~60s for the old SparkyBot process to fully release main.py.
for _ in range(120):
    if not os.path.exists(main_py) or writable(main_py):
        break
    time.sleep(0.5)

# Small extra settle for the SMB oplock to break, then start bootstrap, which
# applies the staged update (it has its own per-file retry as a safety net).
time.sleep(1.0)
try:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    subprocess.Popen([python, bootstrap, *args], cwd=app_dir, creationflags=flags)
except Exception as e:
    sys.stderr.write("relaunch failed: %s\n" % e)
'''


def _get_ai_components():
    """Lazy-init and return the shared VocabularyConfig, VocabularyTracker, and SessionHistoryTracker."""
    global _vocab_config, _vocab_tracker, _session_history, _callout_cooldown
    if _vocab_config is None:
        from core.ai_analyst import VocabularyConfig, VocabularyTracker, SessionHistoryTracker
        from core.apppaths import app_dir
        from core.callout_cooldown import CalloutCooldown
        from pathlib import Path
        _vocab_config = VocabularyConfig()
        _vocab_tracker = VocabularyTracker(vocab_config=_vocab_config)
        _session_history = SessionHistoryTracker()
        # State file lives next to other singleton JSONs in the SparkyBot dir
        _callout_cooldown = CalloutCooldown(
            state_path=app_dir() / 'sparkybot_callout_cooldown.json'
        )
    return _vocab_config, _vocab_tracker, _session_history


class FileProcessorWorker(QThread):
    """Background worker for processing log files."""
    file_started = Signal(int, int, str)    # index, total, filename
    # file_path, ProcessResult.value, exact skip detail
    file_finished = Signal(object, str, str)
    all_done = Signal(int)                   # total processed

    def __init__(self, file_paths: list, config, tts_client=None, parent=None):
        super().__init__(parent)
        self.file_paths = file_paths
        self.config = config
        self.tts_client = tts_client

    def run(self):
        gw2ei = GW2EIInvoker(self.config)
        discord = DiscordWebhookManager(self.config)

        for i, file_path in enumerate(self.file_paths, 1):
            self.file_started.emit(i, len(self.file_paths), file_path.name)
            try:
                skip_reasons = []

                def _events(kind: str, text: str):
                    if kind == "skipped":
                        skip_reasons.append(text)

                result = process_log_file(
                    file_path, self.config, gw2ei, discord,
                    tts_client=self.tts_client, events=_events,
                )
                detail = skip_reasons[-1] if skip_reasons else ""
                self.file_finished.emit(file_path, result.value, detail)
            except Exception as e:
                logger.error(f"Failed to process {file_path.name}: {e}")
                self.file_finished.emit(
                    file_path, ProcessResult.ERROR_OTHER.value, "")

        self.all_done.emit(len(self.file_paths))


def setup_logging(verbose: bool = False):
    """Configure logging for the application"""
    level = logging.DEBUG if verbose else logging.INFO
    format_str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    logging.basicConfig(
        level=level,
        format=format_str,
        datefmt="%H:%M:%S"
    )

    from core.apppaths import app_dir
    log_path = app_dir() / "sparkybot.log"
    handler = RotatingFileHandler(
        str(log_path), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(format_str, datefmt="%H:%M:%S"))
    logging.getLogger().addHandler(handler)


class ProcessResult(Enum):
    """Result of processing a log file"""
    SUCCESS = "success"
    SKIPPED_THRESHOLD = "skipped_threshold"
    ERROR_PARSE = "error_parse"
    ERROR_JSON = "error_json"
    ERROR_DISCORD = "error_discord"
    ERROR_OTHER = "error_other"


def _format_mmss(seconds: int) -> str:
    """m:ss for feed skip reasons — '0:08' reads better than '8s'."""
    minutes, secs = divmod(max(0, int(seconds)), 60)
    return f"{minutes}:{secs:02d}"


def _try_delete_json(json_file: Path, logger: logging.Logger):
    """Attempt to delete JSON file, logging warning on failure"""
    try:
        json_file.unlink()
    except PermissionError:
        logger.warning(f"Could not delete JSON file: {json_file.name}")


def _cache_or_delete_json(json_file: Path, log_file: Path, config,
                          invoker, logger: logging.Logger):
    """Move JSON into RaidReportCache or delete it if caching is off/fails."""
    if config.raidreport_cache_enabled:
        try:
            from core.raid_session import RaidReportCache
            cache = RaidReportCache(config.get_raidreport_cache_dir())
            cache.store(log_file, json_file, *invoker.cache_key())
            logger.info(f"Cached parsed JSON for {log_file.name}")
            return
        except Exception as exc:
            logger.debug("Cache store failed, falling back to delete: %s", exc)
    _try_delete_json(json_file, logger)


def process_log_file(file_path: Path, config: Config, gw2ei: GW2EIInvoker,
                     discord: Optional[DiscordWebhookManager],
                     tts_client=None, events=None) -> ProcessResult:
    """Process a single log file through GW2EI and send to Discord

    Args:
        events: optional callable(kind: str, text: str) receiving
            feed-worthy pipeline moments: the exact failed filter for a
            skip (named here, at the decision site) and AI commentary /
            voice outcomes. A failing callback never breaks the pipeline.

    Returns:
        ProcessResult indicating what happened
    """
    logger = logging.getLogger(__name__)

    def _emit_event(kind: str, text: str):
        if events is not None:
            try:
                events(kind, text)
            except Exception:
                logger.debug("events callback failed", exc_info=True)

    logger.info(f"Processing: {file_path.name}")

    # Parse with GW2EI
    json_file = gw2ei.parse_file(file_path)
    if not json_file:
        logger.error("GW2EI parsing failed")
        return ProcessResult.ERROR_PARSE

    try:
        # Parse JSON using FightReport
        with open(json_file, 'r', encoding='utf-8') as f:
            report_data = json.load(f)

        report = FightReport(report_data)
        # v1.7.0 — wire the (player, axis) cooldown tracker into outlier picking
        global _callout_cooldown
        if _callout_cooldown is None:
            _get_ai_components()  # lazy-init the singletons
        report.callout_cooldown = _callout_cooldown
        report.set_embed_color(config.embed_color)

        # Check fight minimums
        duration = report.duration_ms // 1000  # Convert ms to seconds

        if duration < config.min_fight_duration:
            logger.info(f"SKIPPING: Fight duration {duration}s below minimum {config.min_fight_duration}s")
            _emit_event('skipped',
                        f"Too short ({_format_mmss(duration)} < "
                        f"{_format_mmss(config.min_fight_duration)} minimum)")
            _cache_or_delete_json(json_file, file_path, config, gw2ei, logger)
            return ProcessResult.SKIPPED_THRESHOLD

        if report.total_downs < config.min_fight_downs:
            logger.info(f"SKIPPING: {report.total_downs} downs below minimum {config.min_fight_downs}")
            _emit_event('skipped',
                        f"Too few downs ({report.total_downs} < "
                        f"{config.min_fight_downs} minimum)")
            _cache_or_delete_json(json_file, file_path, config, gw2ei, logger)
            return ProcessResult.SKIPPED_THRESHOLD

        if report.total_damage < config.min_fight_total_dmg:
            logger.info(f"SKIPPING: {report.total_damage:,} damage below minimum {config.min_fight_total_dmg:,}")
            _emit_event('skipped',
                        f"Too little damage ({report.total_damage:,} < "
                        f"{config.min_fight_total_dmg:,} minimum)")
            _cache_or_delete_json(json_file, file_path, config, gw2ei, logger)
            return ProcessResult.SKIPPED_THRESHOLD

        # Compute the AI/corpus summary once and reuse it for both the corpus
        # hook below and the AI analysis path further down (get_ai_summary rebuilds
        # ~18 sorted top-N arrays, so we avoid doing that twice per fight).
        # Wrapped so a summary failure can never break the Discord report path.
        ai_summary = None
        try:
            ai_summary = report.get_ai_summary()
        except Exception as sum_err:
            logger.warning(f"get_ai_summary failed: {sum_err}")

        # Auto-accumulate the calibration corpus: every accepted live fight
        # contributes its summary so the GUI "Calibrate to Your Guild" feature
        # can recompute thresholds from real fights. Best-effort — a failure here
        # must never break the report/AI pipeline.
        if ai_summary is not None:
            try:
                from core.calibration import append_summary
                append_summary(ai_summary)
            except Exception as cal_err:
                logger.debug(f"Calibration corpus append skipped: {cal_err}")

        # Discord enabled in config but webhook not initialized -> real error
        if config.enable_discord_bot and discord is None:
            logger.error("Discord enabled in config but webhook not initialized")
            return ProcessResult.ERROR_DISCORD

        # Whether we will actually post to Discord this run. AI analysis must run
        # regardless (e.g. --debug-ai-prompt, local TTS), so do NOT early-return
        # when Discord is disabled.
        discord_active = config.enable_discord_bot and discord is not None
        success_count = 0

        # Send the fight report to Discord with rich embeds (only when active)
        if discord_active:
            display_config = {
                'showSquadSummary': True,
                'showEnemySummary': True,
                'showDamage': config.show_damage,
                'showBurstDmg': config.show_burst_dmg,
                'showStrips': config.show_strips,
                'showCleanses': config.show_cleanses,
                'showHeals': config.show_heals,
                'showDefense': config.show_defense,
                'showCCs': config.show_ccs,
                'showDownsKills': config.show_downs_kills,
                'showQuickReport': config.show_quick_report,
                'showOffensiveBoons': config.show_offensive_boons,
                'showDefensiveBoons': config.show_defensive_boons,
                'showTopEnemySkills': config.show_top_enemy_skills,
                'showEnemyBreakdown': config.show_enemy_breakdown,
            }

            # Resolve guild icon path for thumbnail attachment
            icon_path = config.get_thumbnail_path()

            embeds = report.get_discord_embeds(
                display_config,
                icon_filename=icon_path  # guild icon for thumbnail
            )

            # Send fight report immediately — no waiting on AI
            success_count = discord.send_to_all(
                embeds=embeds,
                icon_path=icon_path,
                compact_single_message=True,
            )

        # AI analysis runs AFTER report is already posted
        ai_text = None
        if config.enable_ai_analysis and config.ai_base_url and config.ai_model:
            try:
                from core.ai_analyst import FightAnalyst
                vocab_config, vocab_tracker, session_history = _get_ai_components()

                # v3 pipeline is the active path. The custom prompt field
                # (config.ai_system_prompt) is only respected if it contains
                # the {commander_block} placeholder; otherwise v3 loads from
                # prompts/sparky_system_v3.md.
                # Single-source cooldown: the analyst reads the SAME object that
                # record()/tick()/save() below mutate, so callouts actually get
                # suppressed across fights (avoids the split-brain that caused
                # repetitive commentary).
                analyst = FightAnalyst(
                    base_url=config.ai_base_url,
                    api_key=config.ai_api_key,
                    model=config.ai_model,
                    system_prompt=config.ai_system_prompt or None,
                    max_tokens=config.ai_max_tokens,
                    vocab_tracker=vocab_tracker,
                    vocab_config=vocab_config,
                    vocab_weights={
                        "shock": config.ai_vocab_weight_shock,
                        "positive": config.ai_vocab_weight_positive,
                        "negative": config.ai_vocab_weight_negative,
                        "gates": config.ai_vocab_weight_gates,
                    },
                    session_history=session_history,
                    thinking=not config.ai_disable_thinking,
                    prompt_version="v3",
                    callout_cooldown=_callout_cooldown,
                )
                # Reuse the summary computed once above; only recompute if that
                # precompute failed (preserves the original behavior of raising
                # inside this try, where it is caught).
                summary = ai_summary if ai_summary is not None else report.get_ai_summary()
                global _last_ai_response
                analysis = analyst.analyze(summary, previous_response=_last_ai_response)
                if analysis:
                    _last_ai_response = analysis
                # Record fired callouts + advance cooldowns for the NEXT fight.
                # Done after analyze() so a model error doesn't spend cooldowns.
                if _callout_cooldown is not None:
                    for axis, info in (summary.get('outliers') or {}).items():
                        name = info.get('name', '')
                        # Co-outlier names join with " and "; record both halves.
                        for n in (name.split(' and ') if ' and ' in name else [name]):
                            n = n.strip()
                            if n:
                                _callout_cooldown.record(n, axis)
                    _callout_cooldown.tick()
                    _callout_cooldown.save()
                if analysis:
                    # Truncate to last complete sentence within Discord's limit
                    if len(analysis) > 4096:
                        truncated = analysis[:4093]
                        last_period = max(
                            truncated.rfind('.'), truncated.rfind('!'), truncated.rfind('?')
                        )
                        analysis = truncated[:last_period + 1] if last_period > 0 else truncated + "..."

                    ai_label = config.discord_webhook_label or "SparkyBot"
                    ai_embed = {
                        "color": report.EMBED_COLOR,
                        "author": {
                            "name": f"{ai_label} Hot Take and Bad Advice!",
                            "icon_url": report.AUTHOR_ICON_URL,
                        },
                        "description": analysis[:4096],
                    }

                    # Generate TTS audio once if either local playback or Discord
                    # attachment is enabled — avoids generating it twice.
                    audio_bytes = None
                    tts_needed = (
                        (config.tts_enabled and tts_client is not None)
                        or config.tts_discord_attach
                    )
                    if tts_needed:
                        try:
                            from core.tts import generate_tts_bytes
                            audio_bytes = generate_tts_bytes(analysis, config)
                        except Exception as tts_err:
                            logger.warning(f"TTS audio generation failed: {tts_err}")

                    # Send AI embed to Discord — audio posted separately so the player renders below the embed
                    if discord_active:
                        discord.send_to_all(
                            embeds=[ai_embed],
                            audio_bytes=audio_bytes if config.tts_discord_attach else None,
                        )
                        # Feed row (these events only occur while AI is on —
                        # this whole block is gated on enableAiAnalysis)
                        _emit_event('commentary', "Commentary posted")

                    ai_text = analysis  # Save for Twitch

                    # Play locally using pre-generated bytes to avoid a second fetch
                    if config.tts_enabled and tts_client is not None:
                        if audio_bytes:
                            tts_client.speak_from_bytes(audio_bytes)
                        else:
                            tts_client.speak(analysis)

                    if audio_bytes:
                        if config.tts_discord_attach and discord_active:
                            _emit_event('voice', "Voice clip attached")
                        elif config.tts_enabled and tts_client is not None:
                            _emit_event('voice', "Voice clip played")

            except Exception as e:
                logger.warning(f"AI analysis failed: {e}")

        # Send to Twitch if configured (after Discord and AI are done)
        if config.enable_twitch and config.twitch_token and config.twitch_channel:
            try:
                from core.twitch_bot import TwitchBot
                twitch = TwitchBot(config.twitch_token, config.twitch_channel, use_tls=config.twitch_use_tls)
                # Send Quick Report
                quick_text = report.get_twitch_summary()
                if quick_text:
                    twitch.send_message(quick_text)
                # Send AI commentary if available
                if ai_text:
                    bot_label = config.discord_webhook_label or "SparkyBot"
                    twitch.send_message(f"[{bot_label}] Hot Take and Bad Advice! — {ai_text}")
                twitch.close()
            except Exception as e:
                logger.warning(f"Twitch send failed: {e}")

        # Discord posting was not attempted (disabled) — processing still
        # succeeded, so clean up and report success.
        if not discord_active:
            _cache_or_delete_json(json_file, file_path, config, gw2ei, logger)
            return ProcessResult.SUCCESS

        # success_count can be: 0 (all failed), 1+ (webhooks succeeded), or True (single, deprecated)
        if isinstance(success_count, bool):
            discord_success = success_count
        else:
            discord_success = success_count > 0

        if discord_success:
            logger.info(f"Report sent to {success_count} Discord webhook(s)")
            _cache_or_delete_json(json_file, file_path, config, gw2ei, logger)
            return ProcessResult.SUCCESS
        else:
            logger.warning("Failed to send to all Discord webhooks")
            # Keep JSON for retry possibility
            return ProcessResult.ERROR_DISCORD

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON report: {e}")
        # Don't delete JSON on parse error - it's useful for debugging
        return ProcessResult.ERROR_JSON
    except Exception as e:
        logger.error(f"Error processing file: {e}")
        # Don't delete JSON on error - it's useful for debugging
        return ProcessResult.ERROR_OTHER


class WatcherWorker(QObject):
    """Worker class to run file watcher in background thread"""

    status_changed = Signal(str)
    running_state_changed = Signal(bool)  # (is_running)
    # (filename, result_name, detail) — detail is the exact skip reason
    # named at the decision site inside process_log_file, else "".
    file_processed = Signal(str, str, str)
    # (filename, kind, text) — AI commentary/voice feed moments (these only
    # fire while AI analysis is enabled) plus concise processing lifecycle.
    pipeline_event = Signal(str, str, str)

    def __init__(self, config, tts_client=None):
        super().__init__()
        self.config = config
        self.tts_client = tts_client
        self.watcher: Optional[FileWatcher] = None
        self._running = False
        self._lock = threading.Lock()

    def start(self):
        """Start the watcher (called on the watcher thread)"""
        if self.config.raidreport_cache_enabled:
            try:
                from core.raid_session import (
                    RaidReportCache, prune_raidreport_output,
                )
                cache = RaidReportCache(self.config.get_raidreport_cache_dir())
                removed = cache.prune(self.config.raidreport_cache_retention_hours)
                removed += prune_raidreport_output(
                    self.config.raidreport_cache_retention_hours)
                if removed:
                    _prune_logger = logging.getLogger(__name__)
                    _prune_logger.info(
                        "Pruned %d stale raid-report cache file(s)", removed
                    )
            except Exception:
                pass

        # Construct outside lock - constructors may do I/O
        gw2ei = GW2EIInvoker(self.config)
        discord = DiscordWebhookManager(self.config) if self.config.enable_discord_bot else None

        with self._lock:
            if self._running:
                return
            self._running = True

            def on_new_file(file_path: Path):
                self.pipeline_event.emit(
                    str(file_path), "processing",
                    f"New fight detected — processing {file_path.name}",
                )
                # Capture the skip reason so it rides along with the
                # outcome; commentary/voice moments stream out live.
                skip_reasons = []

                def _events(kind: str, text: str):
                    if kind == 'skipped':
                        skip_reasons.append(text)
                    else:
                        self.pipeline_event.emit(str(file_path), kind, text)

                result = process_log_file(
                    file_path, self.config, gw2ei, discord,
                    tts_client=self.tts_client, events=_events,
                )
                detail = skip_reasons[-1] if skip_reasons else ""
                self.file_processed.emit(str(file_path), result.value, detail)

            watcher = FileWatcher(self.config, on_new_file, poll_interval=getattr(self.config, 'poll_interval', 5))

        # Emit after releasing lock to avoid deadlock
        self.status_changed.emit("Starting watcher...")

        try:
            watcher.start()
        except Exception as e:
            # Roll back on failure - watcher failed to start
            with self._lock:
                self._running = False
            self.running_state_changed.emit(False)
            self.status_changed.emit(f"Watcher failed: {e}")
            return

        # Only assign after confirmed start - prevents stop() from seeing a pre-start watcher
        with self._lock:
            if not self._running:
                # stop() was called during watcher.start() - stop the watcher we just started
                watcher.stop()
                return
            self.watcher = watcher

        self.running_state_changed.emit(True)
        self.status_changed.emit("Watching for logs...")

    def stop(self):
        """Stop the watcher"""
        watcher = None
        with self._lock:
            if not self._running:
                return
            self._running = False
            watcher = self.watcher
            self.watcher = None

        # Release lock before blocking on watcher.stop()
        if watcher:
            watcher.stop()
        self.running_state_changed.emit(False)
        self.status_changed.emit("Watcher stopped")

    def is_running(self) -> bool:
        """Check if watcher is running"""
        with self._lock:
            return self._running


class SparkyBotApp(QApplication):
    """Main application class with GUI and system tray"""

    def __init__(self, args, config):
        super().__init__(args)

        # Workbench Dark theme, applied app-wide BEFORE any window (including
        # the first-run wizard) is constructed — everything inherits it.
        apply_theme(self)

        # Quitting is always explicit (tray Quit, File > Exit, or the main
        # window's closeEvent when closeToTray is off). Qt's default
        # quit-on-last-window-close would kill the bot — watcher and all —
        # the moment a transient dialog closed with the window hidden.
        self.setQuitOnLastWindowClosed(False)

        # Set application-wide icon (taskbar, alt-tab, title bars)
        from PySide6.QtGui import QIcon
        icon_path = Path(__file__).parent / "assets" / "sbtray.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.config = config
        self.logger = logging.getLogger("SparkyBot")

        # Window-free update engine: launch checks, downloads, and staging into
        # .update_pending/ all live in UpdateFlow, so start-minimized sessions
        # (no window ever constructed) can still prompt and self-update. Its
        # signals fire from worker threads and queue back to this (GUI) thread.
        self.update_flow = UpdateFlow(config, parent=self)
        self.update_flow.sig_launch_available.connect(self._show_update_dialog)
        self.update_flow.sig_ei_launch_available.connect(self._show_ei_update_dialog)
        self.update_flow.sig_staged.connect(self._on_update_complete)

        # Setup components
        self.watcher_thread: Optional[QThread] = None
        self.watcher_worker: Optional[WatcherWorker] = None

        self.tray_manager = TrayManager("SparkyBot")
        # The MainWindow shell. Keeps the legacy attribute name so every
        # `is not None` check and signal hookup below stays valid.
        self.settings_window: Optional[MainWindow] = None

        if self._is_first_run():
            self._run_setup_wizard()

        # TTSClient must be created on the main thread (QApplication already exists here)
        self.tts_client: Optional[TTSClient] = None
        if config.tts_enabled:
            self.tts_client = TTSClient(config)

        self._setup_tray()
        self._setup_signals()
        self.aboutToQuit.connect(self._shutdown)

    def _is_first_run(self) -> bool:
        """First run if no config file existed when the app started"""
        return self.config.is_new_config

    def _run_setup_wizard(self):
        from core.setup_wizard import SetupWizard
        wizard = SetupWizard(self.config)
        wizard.exec()

    def _setup_tray(self):
        """Setup system tray"""
        icon_path = str(Path(__file__).parent / "assets" / "sbtray.ico")
        self.tray_manager.setup(icon_path)
        self.tray_manager.show()

    def _connect_watcher_signals(self):
        """Connect watcher worker signals to slots."""
        self.watcher_worker.status_changed.connect(self.tray_manager.set_status)
        self.watcher_worker.running_state_changed.connect(self.tray_manager.set_watcher_running)
        self.watcher_worker.file_processed.connect(self._on_file_processed)
        self.watcher_worker.pipeline_event.connect(self._on_pipeline_event)

    def _setup_signals(self):
        """Setup signal connections"""
        self.tray_manager.activated.connect(self._on_tray_action)
        self.tray_manager.quit_requested.connect(self.quit)

        if self.watcher_worker is not None:
            self._connect_watcher_signals()

    def _on_tray_action(self, action: str):
        """Handle tray actions"""
        if action == "show":
            self.show_settings()
        elif action == "toggle_watcher":
            self.toggle_watcher()

    def _on_file_processed(self, filename: str, result_name: str,
                           detail: str = ""):
        """Per-fight outcome: feed the Home activity model, then balloon
        only while the main window can't show it (hidden to tray). The
        feed always gets the row so a reopened window has the scrollback."""
        window = self.settings_window
        if window is not None:
            window.feed_file_event(filename, result_name, detail)
        if window is not None and window.isVisible():
            return  # the feed showed it; balloons only while hidden

        if result_name == ProcessResult.SUCCESS.value:
            self.tray_manager.show_message(
                "Fight Report Sent",
                f"Successfully processed {Path(filename).name}"
            )
        elif result_name == ProcessResult.SKIPPED_THRESHOLD.value:
            message = (f"{Path(filename).name}: {detail}" if detail
                       else f"File {Path(filename).name} did not pass the posting filters")
            self.tray_manager.show_message(
                "Fight Skipped",
                message,
                icon=self.tray_manager.MessageIcon.Warning
            )
        elif result_name == ProcessResult.ERROR_DISCORD.value:
            self.tray_manager.show_message(
                "Report Not Sent",
                f"File {Path(filename).name} processed but Discord failed",
                icon=self.tray_manager.MessageIcon.Warning
            )
        else:
            self.tray_manager.show_message(
                "Fight Error",
                f"File {Path(filename).name} failed to process",
                icon=self.tray_manager.MessageIcon.Critical
            )

    def _on_pipeline_event(self, filename: str, kind: str, text: str):
        """Live processing/commentary/voice rows for the Home activity feed."""
        if self.settings_window is not None:
            self.settings_window.feed_event(kind, text)

    def start_watcher(self):
        """Start the file watcher on a new thread."""
        # Always create fresh worker and thread
        self.watcher_worker = WatcherWorker(self.config, tts_client=self.tts_client)
        self.watcher_thread = QThread()
        self.watcher_worker.moveToThread(self.watcher_thread)
        self.watcher_thread.started.connect(self.watcher_worker.start)
        self.watcher_thread.start()
        # Reconnect signals for the new worker
        self._connect_watcher_signals()
        # Reconnect settings window if it exists (use UniqueConnection to avoid duplicates)
        if self.settings_window is not None:
            self.watcher_worker.running_state_changed.connect(
                self.settings_window.set_watcher_state,
                Qt.ConnectionType.UniqueConnection
            )

    def stop_watcher(self):
        """Stop the file watcher and clean up."""
        if hasattr(self, 'watcher_worker') and self.watcher_worker is not None:
            self.watcher_worker.stop()
        if hasattr(self, 'watcher_thread') and self.watcher_thread is not None:
            self.watcher_thread.quit()
            self.watcher_thread.wait(5000)  # Wait up to 5 seconds
            self.watcher_thread = None
            self.watcher_worker = None

    def toggle_watcher(self):
        """Toggle watcher on/off"""
        if self.watcher_worker is not None and self.watcher_worker.is_running():
            self.stop_watcher()
        else:
            self.start_watcher()

    def show_settings(self):
        """Show the main window (lazy singleton, hidden — not destroyed —
        when closed to tray)."""
        if self.settings_window is None:
            # Shares the app's UpdateFlow so the Updates tab and the launch
            # check drive (and reflect) the same pipeline.
            self.settings_window = MainWindow(self.config, update_flow=self.update_flow)
            self.settings_window.watcher_toggled.connect(self.toggle_watcher)
            self.settings_window.settings_changed.connect(self._on_settings_changed)
            self.settings_window.destroyed.connect(self._on_settings_window_destroyed)
            self.settings_window.process_files_widget.process_requested.connect(self._process_manual_files)
            # Connect to watcher if running
            if self.watcher_worker is not None:
                self.watcher_worker.running_state_changed.connect(
                    self.settings_window.set_watcher_state
                )
                self.settings_window.set_watcher_state(self.watcher_worker.is_running())

            # Wire TTS client reference so the Settings test button works
            self.settings_window._tts_client = self.tts_client

        self.settings_window.show()
        # show()+activateWindow() alone leaves a previously-minimized window
        # in the taskbar without focus; clear the minimized state and raise.
        self.settings_window.setWindowState(
            (self.settings_window.windowState()
             & ~Qt.WindowState.WindowMinimized)
            | Qt.WindowState.WindowActive)
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def _on_settings_window_destroyed(self):
        """Handle settings window close"""
        self.settings_window = None

    def _on_settings_changed(self):
        """Handle settings changed"""
        self.logger.info("Settings updated")

        if self.config.tts_enabled:
            if self.tts_client is None:
                self.tts_client = TTSClient(self.config)
                self.logger.info("TTSClient created after settings change")
            else:
                self.tts_client.config = self.config
                self.tts_client.update_volume(self.config.tts_volume)
        else:
            if self.tts_client is not None:
                self.tts_client.stop()
                self.tts_client = None
                self.logger.info("TTSClient stopped after settings change")

        if self.settings_window is not None:
            self.settings_window._tts_client = self.tts_client

    def _process_manual_files(self, file_paths: list):
        """Process manually selected files on a background thread.

        The Process Files tab is driven purely through its method API
        (set_processing / show_progress / mark_file_result /
        finish_processing) — never through its child widgets.
        """
        self.settings_window.process_files_widget.set_processing(True)

        self._file_worker = FileProcessorWorker(
            file_paths, self.config, tts_client=self.tts_client
        )
        self._file_worker.file_started.connect(self._on_file_started)
        self._file_worker.file_finished.connect(self._on_file_finished)
        self._file_worker.all_done.connect(self._on_all_files_done)
        self._file_worker.start()

    def _on_file_started(self, index: int, total: int, filename: str):
        self.logger.info(f"Manual processing ({index}/{total}): {filename}")
        self.settings_window.process_files_widget.show_progress(index, total, filename)

    def _on_file_finished(self, file_path, result_name: str, detail: str):
        parsed = result_name in {
            ProcessResult.SUCCESS.value,
            ProcessResult.SKIPPED_THRESHOLD.value,
            ProcessResult.ERROR_DISCORD.value,
        }
        self.settings_window.process_files_widget.mark_file_result(
            file_path, parsed)
        self.settings_window.feed_file_event(
            str(file_path), result_name, detail)

    def _on_all_files_done(self, total: int):
        self.settings_window.process_files_widget.finish_processing(total)

    def _shutdown(self):
        """Clean shutdown - stop watcher and wait for thread"""
        self.stop_watcher()
        if self.tts_client is not None:
            self.tts_client.stop()
            self.tts_client = None

    def _check_updates_on_launch(self):
        """Check for updates on startup if enabled.

        All logic (config gate, staged-update anti-loop guard, version
        resolution, EI check) lives in UpdateFlow — window-free.
        """
        self.update_flow.check_on_launch()

    def _show_update_dialog(self, latest_version: str, release_data: dict):
        """Show update prompt to user."""
        from PySide6.QtWidgets import QMessageBox
        from PySide6.QtCore import Qt
        from core.version import VERSION

        msg = QMessageBox()
        msg.setWindowTitle("SparkyBot Update Available")
        msg.setText(f"A new version of SparkyBot is available.\n\n"
                    f"Current: v{VERSION}\n"
                    f"Latest: v{latest_version}\n\n"
                    f"Would you like to update now?")
        msg.setIcon(QMessageBox.Icon.Information)
        # Force on top — this dialog has no parent and was opening BEHIND the
        # main window, so the user never saw the prompt (or the error label).
        msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        update_btn = msg.addButton("Update Now", QMessageBox.ButtonRole.AcceptRole)
        skip_btn = msg.addButton("Skip This Time", QMessageBox.ButtonRole.RejectRole)
        never_btn = msg.addButton("Don't Ask Again", QMessageBox.ButtonRole.DestructiveRole)

        msg.exec()

        if msg.clickedButton() == update_btn:
            self._trigger_sparkybot_update(release_data)
        elif msg.clickedButton() == never_btn:
            self.config.update('Behavior', 'checkUpdatesOnLaunch', 'false')
            self.config.save()

    def _show_ei_update_dialog(self, current: str, latest: str, url: str):
        """Show EI update prompt to user."""
        from PySide6.QtWidgets import QMessageBox

        msg = QMessageBox()
        msg.setWindowTitle("Elite Insights Update Available")
        msg.setText(f"A new version of Elite Insights is available.\n\n"
                    f"Current: v{current}\n"
                    f"Latest: v{latest}\n\n"
                    f"Would you like to update now?")
        msg.setIcon(QMessageBox.Icon.Information)

        update_btn = msg.addButton("Update Now", QMessageBox.ButtonRole.AcceptRole)
        skip_btn = msg.addButton("Skip", QMessageBox.ButtonRole.RejectRole)

        msg.exec()

        if msg.clickedButton() == update_btn:
            try:
                from core.ei_updater import EIUpdater
                from core.gw2ei_invoker import GW2EIInvoker
                invoker = GW2EIInvoker(self.config)
                ei = EIUpdater(invoker.get_gw2ei_folder())
                success, result_msg = ei.download_and_update(url, version=latest)
                if success:
                    ei._save_version(latest)
                    self.logger.info(f"Elite Insights updated to v{latest}")
            except Exception as e:
                self.logger.error(f"EI update failed: {e}")

    def _on_update_complete(self, version: str):
        """Show restart dialog after successful update."""
        from PySide6.QtWidgets import QMessageBox
        from PySide6.QtCore import Qt

        msg = QMessageBox()
        msg.setWindowTitle("Update Installed")
        msg.setText(f"SparkyBot has been updated to v{version}.\n\n"
                    f"The application needs to restart for changes to take effect.")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        restart_btn = msg.addButton("Restart Now", QMessageBox.ButtonRole.AcceptRole)
        later_btn = msg.addButton("Later", QMessageBox.ButtonRole.RejectRole)

        msg.exec()

        if msg.clickedButton() == restart_btn:
            self._restart_app()

    def _restart_app(self):
        """Restart the application to apply a staged update.

        On a network-share install the program files (main.py, core/*.py) stay
        LOCKED for as long as ANY SparkyBot process holds them. os.execv does not
        truly kill the old process on Windows — it overlaps the new one, so the
        staged update can never be copied over the still-locked files (the
        infinite "still locked" loop the operator hit).

        Fix: hand off to a tiny relauncher that lives on LOCAL disk (so it holds
        no lock on the share), then HARD-exit this process with os._exit so every
        share handle is released instantly. The relauncher waits until main.py is
        actually writable again, then starts bootstrap.py, which applies the
        staged update with nothing holding the files.
        """
        import sys
        import os
        import subprocess
        import tempfile

        self.logger.info("Closing SparkyBot to apply update (handing off to relauncher)...")

        try:
            self.stop_watcher()
        except Exception:
            pass

        python = sys.executable
        app_dir = os.path.dirname(os.path.abspath(__file__))
        args = [a for a in sys.argv[1:] if a != '--test-update']

        if getattr(sys, "frozen", False) and os.name == "nt":
            from core.apppaths import app_dir as installed_app_dir
            from core.update_handoff import launch_frozen_update_helper

            launched = launch_frozen_update_helper(
                app_dir=installed_app_dir(),
                executable=sys.executable,
                process_id=os.getpid(),
                app_args=args,
            )
            if not launched:
                self.logger.error(
                    "SparkyBotUpdater.exe is missing; exiting so a manual relaunch can retry."
                )
            os._exit(0)

        relauncher = os.path.join(tempfile.gettempdir(), "sparkybot_relaunch.py")
        try:
            with open(relauncher, "w", encoding="utf-8") as f:
                f.write(_RELAUNCH_SRC)

            # CREATE_NEW_PROCESS_GROUP so the child outlives this process and is
            # not killed by the console's Ctrl+C; it still inherits the console
            # so the relaunched app's startup log stays visible to the user.
            creationflags = 0x00000200 if os.name == "nt" else 0
            subprocess.Popen(
                [python, relauncher, app_dir, python, *args],
                cwd=tempfile.gettempdir(),
                creationflags=creationflags,
            )
        except Exception as e:
            # If we can't spawn the relauncher, fall back to a plain exit so the
            # user can reopen manually (bootstrap will apply on next launch).
            self.logger.error(f"Relauncher spawn failed ({e}); exiting for manual restart.")

        # HARD exit: release all file handles NOW. No thread teardown, no Qt drain.
        os._exit(0)

    def _trigger_sparkybot_update(self, release_data: dict):
        """Download + stage the update via UpdateFlow — no window required."""
        version = release_data.get("tag_name", "").lstrip("v")
        self.update_flow.start_update(release_data, version)

    def run(self):
        """Run the application"""
        self.logger.info(f"SparkyBot v{VERSION} starting...")

        if self.config.start_minimized:
            self.logger.info("Starting minimized to tray")
        else:
            QTimer.singleShot(500, self.show_settings)

        if self.config.start_watcher_on_startup:
            QTimer.singleShot(600, self.toggle_watcher)

        # Check for updates after GUI is ready
        QTimer.singleShot(2000, self._check_updates_on_launch)

        return self.exec()


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="SparkyBot - Guild Wars 2 Fight Log Reporter"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose debug logging"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without GUI (CLI only)"
    )
    parser.add_argument(
        "--raid-report",
        action="store_true",
        help="Generate a Raid Report for today's logs and exit (CLI only)"
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to config.properties file"
    )
    parser.add_argument(
        "--debug-ai-prompt",
        action="store_true",
        help="Save AI analysis prompts to JSON files for debugging"
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    # Set debug flag for AI prompt logging if requested
    if args.debug_ai_prompt:
        os.environ["SPARKY_DEBUG_AI_PROMPT"] = "1"

    # Load configuration
    config = Config(args.config) if args.config else Config()

    if args.raid_report:
        from core.raid_report_wiring import run_headless_raid_report
        try:
            html_path = run_headless_raid_report(config)
            print(str(html_path))
            return 0
        except SystemExit as e:
            return e.code or 1

    if args.headless:
        # CLI-only mode
        return run_headless(config)
    else:
        # GUI mode with system tray
        # Tell Windows this is its own app (not python.exe) so it gets its own taskbar icon
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('SimpleHonors.SparkyBot')
        except Exception:
            pass

        app = SparkyBotApp(sys.argv, config)

        return app.run()


def run_headless(config: Config) -> int:
    """Run in headless CLI mode

    Returns:
        Exit code (0 for success, non-zero for errors)
    """
    logger = logging.getLogger("SparkyBot")

    logger.info("Running in headless mode...")

    if config.raidreport_cache_enabled:
        try:
            from core.raid_session import (
                RaidReportCache, prune_raidreport_output,
            )
            cache = RaidReportCache(config.get_raidreport_cache_dir())
            removed = cache.prune(config.raidreport_cache_retention_hours)
            removed += prune_raidreport_output(
                config.raidreport_cache_retention_hours)
            if removed:
                logger.info(
                    "Pruned %d stale raid-report cache file(s)", removed
                )
        except Exception:
            pass

    gw2ei = GW2EIInvoker(config)
    discord = DiscordWebhookManager(config) if config.enable_discord_bot else None

    def on_new_file(file_path: Path):
        result = process_log_file(file_path, config, gw2ei, discord)
        logger.info(f"Processed {file_path.name}: {result.value}")

    watcher = FileWatcher(config, on_new_file, poll_interval=getattr(config, 'poll_interval', 5))

    try:
        watcher.run_until_stopped()
        return 0
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        watcher.stop()
        return 0
    except Exception as e:
        logger.error(f"Headless mode error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
