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
import sys
import logging
import threading
import json
import ctypes
from pathlib import Path
from enum import Enum
from typing import Optional
from core.version import VERSION

# Add core module to path
sys.path.insert(0, str(Path(__file__).parent / "core"))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import pyqtSignal, QObject, QThread, QTimer, Qt

from core.config import Config
from core.file_watcher import FileWatcher
from core.discord_bot import DiscordWebhookManager
from core.gw2ei_invoker import GW2EIInvoker
from core.tray_manager import TrayManager
from core.gui_settings import SettingsWindow
from core.fight_report import FightReport


def setup_logging(verbose: bool = False):
    """Configure logging for the application"""
    level = logging.DEBUG if verbose else logging.INFO
    format_str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    logging.basicConfig(
        level=level,
        format=format_str,
        datefmt="%H:%M:%S"
    )


class ProcessResult(Enum):
    """Result of processing a log file"""
    SUCCESS = "success"
    SKIPPED_THRESHOLD = "skipped_threshold"
    ERROR_PARSE = "error_parse"
    ERROR_JSON = "error_json"
    ERROR_DISCORD = "error_discord"
    ERROR_OTHER = "error_other"


def _try_delete_json(json_file: Path, logger: logging.Logger):
    """Attempt to delete JSON file, logging warning on failure"""
    try:
        json_file.unlink()
    except PermissionError:
        logger.warning(f"Could not delete JSON file: {json_file.name}")


def process_log_file(file_path: Path, config: Config, gw2ei: GW2EIInvoker, discord: Optional[DiscordWebhookManager]) -> ProcessResult:
    """Process a single log file through GW2EI and send to Discord

    Returns:
        ProcessResult indicating what happened
    """
    logger = logging.getLogger(__name__)

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
        report.set_embed_color(config.embed_color)

        # Check fight minimums
        duration = report.duration_ms // 1000  # Convert ms to seconds

        if duration < config.min_fight_duration:
            logger.info(f"SKIPPING: Fight duration {duration}s below minimum {config.min_fight_duration}s")
            _try_delete_json(json_file, logger)
            return ProcessResult.SKIPPED_THRESHOLD

        if report.total_downs < config.min_fight_downs:
            logger.info(f"SKIPPING: {report.total_downs} downs below minimum {config.min_fight_downs}")
            _try_delete_json(json_file, logger)
            return ProcessResult.SKIPPED_THRESHOLD

        if report.total_damage < config.min_fight_total_dmg:
            logger.info(f"SKIPPING: {report.total_damage:,} damage below minimum {config.min_fight_total_dmg:,}")
            _try_delete_json(json_file, logger)
            return ProcessResult.SKIPPED_THRESHOLD

        # Send to Discord with rich embeds
        if not config.enable_discord_bot:
            # Discord disabled - just clean up and report success
            _try_delete_json(json_file, logger)
            return ProcessResult.SUCCESS

        # Discord is enabled - check instance is available
        if discord is None:
            logger.error("Discord enabled in config but webhook not initialized")
            return ProcessResult.ERROR_DISCORD

        display_config = {
            'showSquadSummary': config.show_damage,
            'showEnemySummary': config.show_defense,
            'showDamage': config.show_damage,
            'showBurstDmg': config.show_burst_dmg,
            'showStrips': config.show_ccs,
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
        success_count = discord.send_to_all(embeds=embeds, icon_path=icon_path)

        # success_count can be: 0 (all failed), 1+ (webhooks succeeded), or True (single, deprecated)
        if isinstance(success_count, bool):
            discord_success = success_count
        else:
            discord_success = success_count > 0

        if discord_success:
            logger.info(f"Report sent to {success_count} Discord webhook(s)")
            _try_delete_json(json_file, logger)
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

    status_changed = pyqtSignal(str)
    running_state_changed = pyqtSignal(bool)  # (is_running)
    file_processed = pyqtSignal(str, str)  # (filename, result_name)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.watcher: Optional[FileWatcher] = None
        self._running = False
        self._lock = threading.Lock()

    def start(self):
        """Start the watcher (called on the watcher thread)"""
        # Construct outside lock - constructors may do I/O
        gw2ei = GW2EIInvoker(self.config)
        discord = DiscordWebhookManager(self.config) if self.config.enable_discord_bot else None

        with self._lock:
            if self._running:
                return
            self._running = True

            def on_new_file(file_path: Path):
                result = process_log_file(file_path, self.config, gw2ei, discord)
                self.file_processed.emit(str(file_path), result.value)

            watcher = FileWatcher(self.config, on_new_file)

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

        # Set application-wide icon (taskbar, alt-tab, title bars)
        from PyQt6.QtGui import QIcon
        icon_path = Path(__file__).parent / "sbtray.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.config = config
        self.logger = logging.getLogger("SparkyBot")

        # Setup components
        self.watcher_thread: Optional[QThread] = None
        self.watcher_worker: Optional[WatcherWorker] = None

        self.tray_manager = TrayManager("SparkyBot")
        self.settings_window: Optional[SettingsWindow] = None

        if self._is_first_run():
            self._run_setup_wizard()

        self._setup_tray()
        self._setup_signals()
        self.aboutToQuit.connect(self._shutdown)

    def _is_first_run(self) -> bool:
        """First run if no log folder and no webhook configured"""
        return not self.config.log_folder and not self.config.discord_webhook

    def _run_setup_wizard(self):
        from core.setup_wizard import SetupWizard
        wizard = SetupWizard(self.config)
        wizard.exec()

    def _setup_tray(self):
        """Setup system tray"""
        icon_path = str(Path(__file__).parent / "sbtray.ico")
        self.tray_manager.setup(icon_path)
        self.tray_manager.show()

    def _connect_watcher_signals(self):
        """Connect watcher worker signals to slots."""
        self.watcher_worker.status_changed.connect(self.tray_manager.set_status)
        self.watcher_worker.running_state_changed.connect(self.tray_manager.set_watcher_running)
        self.watcher_worker.file_processed.connect(self._on_file_processed)

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

    def _on_file_processed(self, filename: str, result_name: str):
        """Handle file processed event"""
        if result_name == ProcessResult.SUCCESS.value:
            self.tray_manager.show_message(
                "Fight Report Sent",
                f"Successfully processed {Path(filename).name}"
            )
        elif result_name == ProcessResult.SKIPPED_THRESHOLD.value:
            self.tray_manager.show_message(
                "Fight Skipped",
                f"File {Path(filename).name} did not meet thresholds",
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

    def start_watcher(self):
        """Start the file watcher on a new thread."""
        # Always create fresh worker and thread
        self.watcher_worker = WatcherWorker(self.config)
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
        """Show settings window"""
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self.config)
            self.settings_window.watcher_toggled.connect(self.toggle_watcher)
            self.settings_window.settings_changed.connect(self._on_settings_changed)
            self.settings_window.destroyed.connect(self._on_settings_window_destroyed)
            # Connect to watcher if running
            if self.watcher_worker is not None:
                self.watcher_worker.running_state_changed.connect(
                    self.settings_window.set_watcher_state
                )
                self.settings_window.set_watcher_state(self.watcher_worker.is_running())

        self.settings_window.show()
        self.settings_window.activateWindow()

    def _on_settings_window_destroyed(self):
        """Handle settings window close"""
        self.settings_window = None

    def _on_settings_changed(self):
        """Handle settings changed"""
        # Could restart watcher with new settings if needed
        self.logger.info("Settings updated")

    def _shutdown(self):
        """Clean shutdown - stop watcher and wait for thread"""
        self.stop_watcher()

    def run(self):
        """Run the application"""
        self.logger.info(f"SparkyBot v{VERSION} starting...")

        if self.config.start_minimized:
            self.logger.info("Starting minimized to tray")
        else:
            QTimer.singleShot(500, self.show_settings)

        if self.config.start_watcher_on_startup:
            QTimer.singleShot(600, self.toggle_watcher)

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
        "--config",
        metavar="PATH",
        help="Path to config.properties file"
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    # Load configuration
    config = Config(args.config) if args.config else Config()

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

    gw2ei = GW2EIInvoker(config)
    discord = DiscordWebhookManager(config) if config.enable_discord_bot else None

    def on_new_file(file_path: Path):
        result = process_log_file(file_path, config, gw2ei, discord)
        logger.info(f"Processed {file_path.name}: {result.value}")

    watcher = FileWatcher(config, on_new_file)

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
