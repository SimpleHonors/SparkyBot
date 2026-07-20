"""First-run setup wizard for SparkyBot"""

import subprocess
import sys
import importlib.metadata
from PySide6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog, QCheckBox,
    QProgressBar, QFrame, QComboBox, QWidget, QScrollArea, QFormLayout,
    QRadioButton, QSpinBox, QInputDialog
)
from PySide6.QtCore import Qt, Signal, Slot, QUrl
from PySide6.QtGui import QIcon
from pathlib import Path

from core import theme

# Explicit page IDs. The default flow is ID order; declining AI on the
# opt-in page removes the AI setup and voice pages from the flow entirely
# (LAW #2a: absent, never grayed — the wizard looks complete without them).
(PAGE_WELCOME, PAGE_AI_OPTIN, PAGE_USAGE_MODE, PAGE_DEPENDENCIES,
 PAGE_GW2EI, PAGE_LOG_FOLDER, PAGE_DISCORD, PAGE_TWITCH, PAGE_AI_SETUP,
 PAGE_TTS_VOICE, PAGE_BEHAVIOR, PAGE_COMPLETE) = range(12)


class SetupWizard(QWizard):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("SparkyBot Setup")
        # 760 wide so the usage-mode page's verbatim radio copy (FINAL-
        # DESIGN wording) never clips; was 700 before that page existed.
        self.setMinimumSize(760, 620)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        # Set window icon to sbtray.ico
        icon_path = Path(__file__).parent.parent / "assets" / "sbtray.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # No private palette or stylesheet here — the wizard inherits the
        # app-wide Workbench Dark theme applied in main.py (before this
        # wizard ever constructs).
        self.setPage(PAGE_WELCOME, WelcomePage())
        # LAW #2a: the AI question comes right after Welcome, before any
        # plumbing — a "No thanks" user never sees an AI setup page.
        self.ai_optin_page = AIOptInPage()
        self.setPage(PAGE_AI_OPTIN, self.ai_optin_page)
        # Usage-mode preference (operator law #3): asked right after the
        # AI question, same plain-language pattern.
        self.usage_mode_page = UsageModePage()
        self.setPage(PAGE_USAGE_MODE, self.usage_mode_page)
        # The dependencies page only makes sense running from source —
        # the installed exe bundles everything and has no pip.
        from core.apppaths import is_frozen
        if not is_frozen():
            self.setPage(PAGE_DEPENDENCIES, DependenciesPage())
        self.setPage(PAGE_GW2EI, GW2EIPage(config))
        self.setPage(PAGE_LOG_FOLDER, LogFolderPage(config))
        self.setPage(PAGE_DISCORD, DiscordPage(config))
        self.twitch_page = TwitchPage(config)
        self.ai_page = AIAnalysisPage(config)
        self.tts_page = TTSVoicePage(config)
        self.behavior_page = BehaviorPage(config)
        self.setPage(PAGE_TWITCH, self.twitch_page)
        self.setPage(PAGE_AI_SETUP, self.ai_page)
        self.setPage(PAGE_TTS_VOICE, self.tts_page)
        self.setPage(PAGE_BEHAVIOR, self.behavior_page)
        self.setPage(PAGE_COMPLETE, CompletePage())
        self.setStartId(PAGE_WELCOME)

    def ai_opted_in(self) -> bool:
        """The page-2 answer — single source for the AI/TTS page skip and
        for what accept() writes to AI/enableAiAnalysis."""
        return self.ai_optin_page.opted_in()

    def accept(self):
        """Save all wizard values to config on finish"""
        cfg = self.config.update
        ei_path = self.field("gw2ei_path")
        if ei_path:
            cfg('Paths', 'gw2eiExe', ei_path)
        log_folder = self.field("log_folder")
        if log_folder:
            cfg('Paths', 'logFolder', log_folder)
        webhook = self.field("webhook")
        if webhook:
            cfg('Discord', 'discordWebhook', webhook)

        # AI opt-in (page 2) is the SINGLE writer of the master switch —
        # the same AI/enableAiAnalysis key the Settings Application page
        # edits (LAW #2: zero new keys, zero renames).
        opted_in = self.ai_opted_in()
        cfg('AI', 'enableAiAnalysis', 'true' if opted_in else 'false')

        # Usage mode (page 3) — RaidReport/runMode, the same key the
        # Settings > Raid Reports "How reports get made" switch edits.
        cfg('RaidReport', 'runMode', self.usage_mode_page.selected_mode())

        # Twitch
        if hasattr(self.twitch_page, 'enable_twitch'):
            cfg('Twitch', 'enableTwitchBot', str(self.twitch_page.enable_twitch.isChecked()).lower())
            cfg('Twitch', 'twitchChannelName', self.twitch_page.twitch_channel.text().strip())
            cfg('Twitch', 'twitchBotToken', self.twitch_page.twitch_token.text().strip())
            cfg('Twitch', 'twitchUseTLS', str(self.twitch_page.twitch_use_tls.isChecked()).lower())

        # AI setup — the page is in the flow only when opted in; declined,
        # its widgets hold untouched defaults and must never be written.
        if opted_in and hasattr(self.ai_page, 'ai_provider'):
            cfg('AI', 'aiProvider', self.ai_page.ai_provider.currentText())
            cfg('AI', 'aiBaseUrl', self.ai_page.ai_base_url.text().strip())
            cfg('AI', 'aiApiKey', self.ai_page.ai_api_key.text().strip())
            cfg('AI', 'aiModel', self.ai_page.ai_model.currentText().strip())
            # Reasoning-probe parity: persist max-tokens + reasoning controls so
            # the probe's auto-applied fix survives to disk (config.save() writes
            # from the ConfigParser and reloads, so in-memory attrs alone are
            # dropped — these must go through cfg(), same as the settings dialog).
            if hasattr(self.ai_page, 'ai_max_tokens'):
                cfg('AI', 'aiMaxTokens', str(self.ai_page.ai_max_tokens.value()))
                cfg('AI', 'aiDisableThinking', str(self.ai_page.ai_disable_thinking.isChecked()).lower())
                cfg('AI', 'aiReasoningStrategy', getattr(self.ai_page, '_reasoning_strategy', ''))

        # TTS — voice speaks AI commentary, so the page follows the same
        # opt-in (skipped and unwritten when declined).
        if opted_in and hasattr(self.tts_page, 'enable_tts'):
            cfg('TTS', 'enableTts', str(self.tts_page.enable_tts.isChecked()).lower())
            cfg('TTS', 'ttsDiscordAttach', str(self.tts_page.tts_discord_attach.isChecked()).lower())
            cfg('TTS', 'ttsProvider', self.tts_page.tts_provider.currentText())
            el_key = self.tts_page.tts_el_api_key.text().strip()
            if el_key:
                cfg('TTS', 'ttsElevenLabsApiKey', el_key)
            el_voice = self.tts_page.tts_el_voice_id.text().strip()
            if el_voice:
                cfg('TTS', 'ttsElevenLabsVoiceId', el_voice)
            edge_voice = self.tts_page.tts_edge_voice.currentText().strip()
            if edge_voice:
                cfg('TTS', 'ttsEdgeVoice', edge_voice)
            local_url = self.tts_page.tts_local_url.text().strip()
            if local_url:
                cfg('TTS', 'ttsLocalUrl', local_url)
            local_voice = self.tts_page.tts_local_voice.currentText().strip()
            if local_voice:
                cfg('TTS', 'ttsLocalVoice', local_voice)

        # Behavior
        if hasattr(self.behavior_page, 'start_watcher_on_startup'):
            cfg('Behavior', 'startWatcherOnStartup', str(self.behavior_page.start_watcher_on_startup.isChecked()))
            cfg('Behavior', 'startMinimized', str(self.behavior_page.start_minimized.isChecked()))
            cfg('Behavior', 'closeToTray', str(self.behavior_page.close_to_tray.isChecked()))
            cfg('Behavior', 'minimizeToTray', str(self.behavior_page.minimize_to_tray.isChecked()))
            cfg('Behavior', 'checkUpdatesOnLaunch', str(self.behavior_page.check_updates_on_launch.isChecked()))

        self.config.save()
        super().accept()


class DependenciesPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Python Dependencies")
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 8, 12, 8)

        desc = QLabel(
            "SparkyBot requires certain Python packages to run. "
            "Click the button below to check and install any missing dependencies."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.install_btn = QPushButton("Check & Install Dependencies")
        self.install_btn.setMinimumHeight(36)
        theme.set_widget_class(self.install_btn, "primary")
        self.install_btn.clicked.connect(self._check_and_install)
        layout.addWidget(self.install_btn)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.details_label = QLabel("")
        self.details_label.setWordWrap(True)
        theme.mark_hint(self.details_label)
        layout.addWidget(self.details_label)

        layout.addStretch()

        # Auto-check on page load
        self._initial_check_done = False

    def initializePage(self):
        """Run check automatically when the page is shown."""
        if not self._initial_check_done:
            self._initial_check_done = True
            self._check_and_install(auto=True)

    def _get_requirements(self):
        """Read requirements.txt and return list of (package, version_spec) tuples."""
        req_file = Path(__file__).parent.parent / "requirements.txt"
        if not req_file.exists():
            return []
        requirements = []
        for line in req_file.read_text().strip().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            requirements.append(line)
        return requirements

    def _check_installed(self, requirements):
        """Check which packages are installed. Returns (installed, missing) lists."""
        installed = []
        missing = []
        for req in requirements:
            # Parse package name from requirement string (e.g., "PySide6>=6.10.0" -> "PySide6")
            pkg_name = req.split('>=')[0].split('==')[0].split('<')[0].split('>')[0].strip()
            try:
                version = importlib.metadata.version(pkg_name)
                installed.append(f"{pkg_name} ({version})")
            except importlib.metadata.PackageNotFoundError:
                missing.append(req)
        return installed, missing

    def _check_and_install(self, auto=False):
        """Check dependencies and install missing ones."""
        requirements = self._get_requirements()
        if not requirements:
            theme.set_state(self.status_label, "warn")
            self.status_label.setText("requirements.txt was not found")
            return

        installed, missing = self._check_installed(requirements)

        if not missing:
            theme.set_state(self.status_label, "ok")
            self.status_label.setText("All dependencies are installed")
            self.details_label.setText("\n".join(f"  Installed: {p}" for p in installed))
            self.install_btn.setText("All Dependencies Installed")
            self.install_btn.setEnabled(False)
            return

        if auto:
            # On auto-check, just show what's missing — don't install yet
            theme.set_state(self.status_label, "warn")
            self.status_label.setText(
                f"{len(missing)} missing package(s) found. "
                f"Click the button to install them."
            )
            details = []
            for p in installed:
                details.append(f"  Installed: {p}")
            for p in missing:
                details.append(f"  Missing: {p}")
            self.details_label.setText("\n".join(details))
            return

        # Actually install missing packages
        theme.set_state(self.status_label, "busy")
        self.status_label.setText(f"Installing {len(missing)} package(s)...")
        self.install_btn.setEnabled(False)
        self.repaint()

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install"] + missing,
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode == 0:
                theme.set_state(self.status_label, "ok")
                self.status_label.setText("All dependencies installed successfully")
                # Re-check to update the details
                installed, still_missing = self._check_installed(requirements)
                details = [f"  Installed: {p}" for p in installed]
                if still_missing:
                    details += [f"  Failed: {p}" for p in still_missing]
                self.details_label.setText("\n".join(details))
                self.install_btn.setText("All Dependencies Installed")
            else:
                theme.set_state(self.status_label, "error")
                self.status_label.setText("Installation failed")
                self.details_label.setText(result.stderr[:500] if result.stderr else result.stdout[:500])
                self.install_btn.setEnabled(True)

        except subprocess.TimeoutExpired:
            theme.set_state(self.status_label, "error")
            self.status_label.setText("Installation timed out after 120 seconds")
            self.install_btn.setEnabled(True)
        except Exception as e:
            theme.set_state(self.status_label, "error")
            self.status_label.setText(f"Error: {str(e)}")
            self.install_btn.setEnabled(True)


class WelcomePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Welcome to SparkyBot")
        layout = QVBoxLayout(self)
        label = QLabel(
            "<p>This wizard will help you configure SparkyBot for first use.</p>"
            "<p>You will need:</p>"
            "<ul>"
            "<li>GuildWars2EliteInsights-CLI.exe (GW2EI parser)</li>"
            "<li>Your ArcDPS log folder path</li>"
            "<li>A Discord webhook URL <b>or</b> a Twitch bot token (at least one required)</li>"
            "</ul>"
            "<p>Optional features configured in this wizard:</p>"
            "<ul>"
            "<li>AI-powered fight commentary</li>"
            "<li>Text-to-speech / voice commentary</li>"
            "</ul>"
            "<p>Click <b>Next</b> to begin.</p>"
        )
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(label)
        layout.addStretch()


class AIOptInPage(QWizardPage):
    """Page 2 — the one AI question (LAW #2a), asked before any plumbing.

    Wording is design-A §1.2a verbatim. Declining removes every later AI
    and voice page from the flow; accept() writes the answer to the
    existing AI/enableAiAnalysis key either way.
    """

    def __init__(self):
        super().__init__()
        self.setTitle("Want AI commentary?")
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 8, 12, 8)

        desc = QLabel(
            "SparkyBot can add an AI hype-commentator blurb to each fight "
            "report, and can read it aloud in a voice you pick. It needs "
            "an AI provider (free local options work). Everything else — "
            "fight reports, raid reports, Discord posting — is complete "
            "without it."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(8)

        self.decline_radio = QRadioButton("No thanks — keep it simple")
        theme.mark_option(self.decline_radio)
        self.decline_radio.setChecked(True)   # opt-IN: off is the default
        layout.addWidget(self.decline_radio)

        self.accept_radio = QRadioButton("Yes — set up AI commentary")
        theme.mark_option(self.accept_radio)
        layout.addWidget(self.accept_radio)

        layout.addSpacing(8)

        note = QLabel(
            "You can change your mind any time in Settings → Application.")
        note.setWordWrap(True)
        theme.mark_hint(note)
        layout.addWidget(note)

        layout.addStretch()

    def opted_in(self) -> bool:
        return self.accept_radio.isChecked()


class UsageModePage(QWizardPage):
    """Page 3 — how end-of-night raid reports get made (usage-mode
    preference, FINAL-DESIGN verbatim; describes workflows, never labels
    the user). Writes the existing RaidReport/runMode key on Finish."""

    def __init__(self):
        super().__init__()
        self.setTitle("How do you want to make end-of-night raid reports?")
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 8, 12, 8)

        self.run_button_radio = QRadioButton(
            "One-button runs — press Start Run when the raid starts; "
            "End Run builds the report and posts it. (recommended)"
        )
        theme.mark_option(self.run_button_radio)
        self.run_button_radio.setChecked(True)   # the recommended default
        layout.addWidget(self.run_button_radio)

        self.manual_radio = QRadioButton(
            "I'll pick fights myself — build reports on the Raid Report "
            "page whenever you want."
        )
        theme.mark_option(self.manual_radio)
        layout.addWidget(self.manual_radio)

        layout.addSpacing(8)

        note = QLabel(
            "You can switch this any time in Settings → Raid Reports.")
        note.setWordWrap(True)
        theme.mark_hint(note)
        layout.addWidget(note)

        layout.addStretch()

    def selected_mode(self) -> str:
        return "manual" if self.manual_radio.isChecked() else "run-button"


class GW2EIPage(QWizardPage):
    # Worker threads never touch widgets — they emit these signals, which Qt
    # queues onto the GUI thread.
    sig_version_status = Signal(str, str)
    sig_status = Signal(str)
    sig_progress = Signal(int)
    sig_download_complete = Signal(bool, str)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self._install_success = False
        self.sig_version_status.connect(self._set_version_status)
        self.sig_status.connect(self._set_status)
        self.sig_progress.connect(lambda pct: self.progress_bar.setValue(pct))
        self.sig_download_complete.connect(self._on_download_complete)
        self.setTitle("GW2 Elite Insights Parser")

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 8, 12, 8)

        # Description - NOT in setSubTitle so it wraps properly
        desc = QLabel(
            "SparkyBot requires GW2 Elite Insights to parse log files."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # PRIMARY: Download and install
        rec_label = QLabel("<b>Recommended: Automatic Install</b>")
        rec_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(rec_label)

        rec_desc = QLabel(
            "Click below to automatically download and install GW2EI directly "
            "into the SparkyBot program folder. No manual steps required."
        )
        rec_desc.setWordWrap(True)
        layout.addWidget(rec_desc)

        self.download_btn = QPushButton("Download GW2 Elite Insights (Recommended)")
        self.download_btn.setMinimumHeight(36)
        theme.set_widget_class(self.download_btn, "primary")
        self.download_btn.clicked.connect(self._do_download)
        layout.addWidget(self.download_btn)

        self.download_status = QLabel("")
        self.download_status.setWordWrap(True)
        layout.addWidget(self.download_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(divider)

        # SECONDARY: Manual path
        adv_label = QLabel("<b>Advanced: I already have GW2EI installed elsewhere</b>")
        adv_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(adv_label)

        adv_desc = QLabel(
            "Only use this if you want to point SparkyBot to an existing "
            "GW2EI installation. Leave blank if you used the automatic install above."
        )
        adv_desc.setWordWrap(True)
        theme.mark_hint(adv_desc)
        layout.addWidget(adv_desc)

        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(
            "Optional: path to GuildWars2EliteInsights-CLI.exe"
        )
        # No prefill - do not expose user's personal folder structure
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse)
        row.addWidget(self.path_edit)
        row.addWidget(browse_btn)
        layout.addLayout(row)

        layout.addStretch()
        self.registerField("gw2ei_path", self.path_edit)

        # Determine initial button state by checking install and version
        self._check_initial_state()

    def _check_initial_state(self):
        """Check if GW2EI is installed and whether it needs updating."""
        default_exe = __import__("core.apppaths", fromlist=["gw2ei_dir"]).gw2ei_dir() / "GuildWars2EliteInsights-CLI.exe"

        if not default_exe.exists():
            self.download_btn.setText("Download GW2 Elite Insights (Recommended)")
            return

        # Installed - check version in background
        self._install_success = True
        theme.set_state(self.download_status, "busy")
        self.download_status.setText("GW2EI is installed — checking for updates...")
        self.download_btn.setText("Update GW2 Elite Insights")

        import threading
        threading.Thread(target=self._check_version_worker, daemon=True).start()

    def _check_version_worker(self):
        try:
            from core.ei_updater import EIUpdater
            from core.gw2ei_invoker import GW2EIInvoker
            invoker = GW2EIInvoker(self.config)
            updater = EIUpdater(invoker.get_gw2ei_folder())
            has_update, latest_version, _ = updater.check_for_update()
            current = updater.get_current_version() or "unknown"

            if has_update:
                msg = f"⬆ Update available: v{current} → v{latest_version}"
                btn = "Update GW2 Elite Insights"
            else:
                msg = f"GW2EI v{current} is up to date"
                btn = "Re-download GW2 Elite Insights"

            self.sig_version_status.emit(msg, btn)
        except Exception as e:
            self.sig_version_status.emit(
                f"GW2EI installed (could not check version: {e})",
                "Re-download GW2 Elite Insights"
            )

    @Slot(str, str)
    def _set_version_status(self, status: str, btn_text: str):
        theme.set_state(self.download_status, None)
        self.download_status.setText(status)
        self.download_btn.setText(btn_text)

    def _do_download(self):
        self.download_btn.setEnabled(False)
        self.download_btn.setText("Downloading...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        theme.set_state(self.download_status, "busy")
        self.download_status.setText("Connecting to GitHub...")

        import threading
        threading.Thread(target=self._download_worker, daemon=True).start()

    def _download_worker(self):
        try:
            from core.ei_updater import EIUpdater
            from core.gw2ei_invoker import GW2EIInvoker
            invoker = GW2EIInvoker(self.config)
            updater = EIUpdater(invoker.get_gw2ei_folder())

            # Always fetch latest release info - user explicitly requested download
            has_update, latest_version, download_url = updater.check_for_update()

            # If no update found, check_for_update may return empty URL.
            # Force fetch the latest URL directly if needed.
            if not download_url:
                import requests
                resp = requests.get(
                    "https://api.github.com/repos/baaron4/GW2-Elite-Insights-Parser/releases/latest",
                    timeout=10
                )
                data = resp.json()
                latest_version = data.get("tag_name", "").lstrip("v")
                for asset in data.get("assets", []):
                    if asset.get("name", "").endswith(".zip"):
                        download_url = asset.get("browser_download_url", "")
                        break

            if not download_url:
                raise ValueError("Could not find download URL from GitHub releases")

            self.sig_status.emit(f"Downloading GW2EI v{latest_version}...")

            def progress_cb(pct):
                self.sig_progress.emit(int(pct))

            success, message = updater.download_and_update(download_url, version=latest_version, progress_callback=progress_cb)

            self.sig_download_complete.emit(
                success, f"v{latest_version}" if success else message
            )
        except Exception as e:
            self.sig_download_complete.emit(False, str(e))

    @Slot(str)
    def _set_status(self, text: str):
        self.download_status.setText(text)

    @Slot(bool, str)
    def _on_download_complete(self, success: bool, message: str):
        self.progress_bar.setVisible(False)
        self.download_btn.setEnabled(True)
        if success:
            self._install_success = True
            theme.set_state(self.download_status, "ok")
            self.download_status.setText(f"GW2EI {message} installed successfully")
            self.download_btn.setText("Re-download GW2 Elite Insights")
        else:
            theme.set_state(self.download_status, "error")
            self.download_status.setText(f"Download failed: {message}")
            self.download_btn.setText("Download GW2 Elite Insights (Recommended)")

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select GW2EI CLI", "", "Executables (*.exe)"
        )
        if path:
            self.path_edit.setText(path)

    def validatePage(self):
        path = self.path_edit.text()
        if path and Path(path).exists():
            return True
        if self._install_success:
            return True
        theme.set_state(self.download_status, "warn")
        self.download_status.setText(
            "GW2EI was not found. Download it above or provide a valid path. "
            "You can continue but parsing will not work."
        )
        return True


class LogFolderPage(QWizardPage):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.setTitle("ArcDPS Log Folder")
        self.setSubTitle(
            "Select the folder where ArcDPS writes WvW combat logs."
        )
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 8, 12, 8)

        # Detect default path
        self._default_path = self._detect_default_log_path()

        # Show detected default as informational label
        if self._default_path:
            detected_label = QLabel(
                f"Default ArcDPS log location detected:<br>"
                f"<code>{self._default_path}</code><br>"
                f"<small>WvW logs are saved in a numbered subfolder here. "
                f"Clicking the button below will select it automatically.</small>"
            )
            detected_label.setWordWrap(True)
            detected_label.setTextFormat(Qt.TextFormat.RichText)
            layout.addWidget(detected_label)

            use_default_btn = QPushButton("Use Default Location (Recommended)")
            use_default_btn.setMinimumHeight(36)
            theme.set_widget_class(use_default_btn, "primary")
            use_default_btn.clicked.connect(self._use_default)
            layout.addWidget(use_default_btn)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(divider)

        # Manual entry
        manual_label = QLabel("<b>Or enter a custom path:</b>")
        manual_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(manual_label)

        row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText(
            "Path to your ArcDPS WvW log folder"
        )
        # No prefill - do not expose user's personal folder structure
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse)
        row.addWidget(self.folder_edit)
        row.addWidget(browse_btn)
        layout.addLayout(row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        layout.addStretch()
        self.registerField("log_folder", self.folder_edit)

    def _detect_default_log_path(self) -> str:
        """Auto-detect the default ArcDPS log folder for the current Windows user."""
        try:
            import ctypes
            import ctypes.wintypes

            # Use SHGetFolderPath to get Documents folder reliably
            # CSIDL_PERSONAL = 0x0005 (My Documents)
            buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
            ctypes.windll.shell32.SHGetFolderPathW(0, 0x0005, 0, 0, buf)
            documents = Path(buf.value)

            candidate = (
                documents
                / "Guild Wars 2"
                / "addons"
                / "arcdps"
                / "arcdps.cbtlogs"
            )
            # Return the path whether or not it exists yet -
            # the user may not have run GW2 since installing ArcDPS
            return str(candidate)
        except Exception:
            # Non-Windows or shell API unavailable - fall back to Path.home()
            candidate = (
                Path.home()
                / "Documents"
                / "Guild Wars 2"
                / "addons"
                / "arcdps"
                / "arcdps.cbtlogs"
            )
            return str(candidate)

    def _use_default(self):
        base = Path(self._default_path)

        # WvW logs go into a numbered subfolder — find it automatically
        wvw_folder = None
        if base.exists():
            numbered = sorted(
                [d for d in base.iterdir() if d.is_dir() and d.name.isdigit()],
                key=lambda d: int(d.name)
            )
            if numbered:
                wvw_folder = str(numbered[0])

        target = wvw_folder or str(base)
        self.folder_edit.setText(target)

        if wvw_folder:
            theme.set_state(self.status_label, "ok")
            self.status_label.setText(
                f"WvW log folder found: {target}"
            )
        elif base.exists():
            theme.set_state(self.status_label, "warn")
            self.status_label.setText(
                "Base folder found but no WvW subfolder yet. "
                "Play a WvW match first, then re-run setup — or browse manually."
            )
        else:
            theme.set_state(self.status_label, "warn")
            self.status_label.setText(
                "Default folder does not exist yet. Install ArcDPS and "
                "enable WvW logging, then play a match before starting the watcher."
            )

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Log Folder",
            self.folder_edit.text() or str(Path.home())
        )
        if folder:
            self.folder_edit.setText(folder)
            self.status_label.setText("")

    def validatePage(self):
        folder = self.folder_edit.text().strip()
        if not folder:
            theme.set_state(self.status_label, "warn")
            self.status_label.setText(
                "No folder selected. You can continue but the watcher "
                "will not work until a log folder is configured."
            )
        return True


class DiscordPage(QWizardPage):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.setTitle("Discord Webhook")
        self.setSubTitle(
            "Enter your Discord webhook URL. "
            "Create one in Discord: Server Settings > Integrations > Webhooks."
        )
        layout = QVBoxLayout(self)

        self.webhook_edit = QLineEdit()
        self.webhook_edit.setPlaceholderText("https://discord.com/api/webhooks/...")
        if config.discord_webhook:
            self.webhook_edit.setText(config.discord_webhook)
        layout.addWidget(QLabel("Webhook URL:"))
        layout.addWidget(self.webhook_edit)

        self.skip_check = QCheckBox("Skip Discord setup for now")
        layout.addWidget(self.skip_check)

        self.registerField("webhook", self.webhook_edit)

    def validatePage(self):
        if self.skip_check.isChecked():
            return True
        url = self.webhook_edit.text().strip()
        if url.startswith("https://discord.com/api/webhooks/"):
            return True
        if not url:
            return True
        return True


class TwitchPage(QWizardPage):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.setTitle("Twitch Integration (Optional)")
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 8, 12, 8)

        desc = QLabel(
            "SparkyBot can post fight summaries and AI commentary to your Twitch chat "
            "in real time. This requires a Twitch OAuth token for a bot account."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(6)

        self.enable_twitch = QCheckBox("Enable Twitch Bot")
        layout.addWidget(self.enable_twitch)

        layout.addSpacing(8)

        # Channel name
        LABEL_WIDTH = 100

        def _make_row(label_text, widget):
            row = QHBoxLayout()
            label = QLabel(label_text)
            label.setFixedWidth(LABEL_WIDTH)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(label)
            row.addWidget(widget, 1)
            return row

        self.twitch_channel = QLineEdit()
        self.twitch_channel.setPlaceholderText("your_channel_name")
        layout.addLayout(_make_row("Channel Name:", self.twitch_channel))

        # Bot token
        self.twitch_token = QLineEdit()
        self.twitch_token.setPlaceholderText("oauth:...")
        self.twitch_token.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addLayout(_make_row("Bot Token:", self.twitch_token))

        self.twitch_use_tls = QCheckBox("Use secure connection (TLS)")
        self.twitch_use_tls.setChecked(True)
        tls_row = QHBoxLayout()
        tls_label = QLabel("")
        tls_label.setFixedWidth(LABEL_WIDTH)
        tls_row.addWidget(tls_label)
        tls_row.addWidget(self.twitch_use_tls, 1, Qt.AlignmentFlag.AlignLeft)
        layout.addLayout(tls_row)

        layout.addSpacing(10)

        # Help link
        help_link = QLabel(
            'Get a free token at <a href="https://twitchtokengenerator.com">twitchtokengenerator.com</a>'
        )
        help_link.setTextFormat(Qt.TextFormat.RichText)
        help_link.setOpenExternalLinks(True)
        layout.addWidget(help_link)

        help_note = QLabel(
            "Select 'Bot Chat Token' when generating. The token starts with oauth: "
            "and gives SparkyBot permission to send messages to your channel."
        )
        help_note.setWordWrap(True)
        theme.mark_hint(help_note)
        layout.addWidget(help_note)

        layout.addStretch()

        layout.addSpacing(12)

        self.skip_check = QCheckBox("Skip Twitch setup for now")
        layout.addWidget(self.skip_check)

        self.registerField("twitch_channel", self.twitch_channel)
        self.registerField("twitch_token", self.twitch_token)

        # Prefill from config
        if config.twitch_channel:
            self.twitch_channel.setText(config.twitch_channel)
        if config.twitch_token:
            self.twitch_token.setText(config.twitch_token)

    def validatePage(self):
        return True

    def nextId(self):
        """AI declined on the opt-in page -> the AI setup and voice pages
        are not in the flow at all (LAW #2a); continue at Behavior.
        NOTE: if this (cut-proposal) page is ever removed, this hop must
        move to the page that precedes PAGE_AI_SETUP in the flow."""
        wizard = self.wizard()
        if wizard is not None and hasattr(wizard, "ai_opted_in") \
                and not wizard.ai_opted_in():
            return PAGE_BEHAVIOR
        return super().nextId()


class AIAnalysisPage(QWizardPage):
    # Worker threads never touch widgets — they emit these signals, which Qt
    # queues onto the GUI thread. Payloads carry semantic states ("busy",
    # "ok", "warn", "error"), never colors — the theme maps state to look.
    sig_models = Signal(int, list)  # → _apply_models
    sig_ai_test_result = Signal(str, str)  # (text, state) → _set_ai_test_result
    sig_apply_pending_reasoning = Signal()  # → _apply_pending_reasoning
    sig_ai_test_done = Signal(str, bool)  # → _on_ai_test_done

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.sig_models.connect(self._apply_models)
        self.sig_ai_test_result.connect(self._set_ai_test_result)
        self.sig_apply_pending_reasoning.connect(self._apply_pending_reasoning)
        self.sig_ai_test_done.connect(self._on_ai_test_done)
        # Not "(Optional)" anymore — this page only appears after the
        # operator opted in on page 2.
        self.setTitle("AI Fight Commentary")

        # Scroll area wrapper for content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 8, 12, 8)

        desc = QLabel(
            "SparkyBot can use an AI language model to generate entertaining fight commentary "
            "after each battle. This works with cloud APIs (OpenAI, Google Gemini, Groq, and others) "
            "or local models (Ollama, LM Studio)."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(8)

        # Quick Start subsection
        quick_label = QLabel("<b>Quick Start (Recommended)</b>")
        quick_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(quick_label)

        quick_desc = QLabel(
            "The easiest free option is Google Gemini. Create a free API key, select Gemini below, "
            "paste the key, and you're done."
        )
        quick_desc.setWordWrap(True)
        theme.mark_hint(quick_desc)
        layout.addWidget(quick_desc)

        layout.addSpacing(8)

        # No enable checkbox here: the opt-in page (page 2) is the single
        # writer of AI/enableAiAnalysis, and this page only exists in the
        # flow after opting in.

        # Provider selection using manual label+field rows
        LABEL_WIDTH = 100

        from core.ai_analyst import PRESETS

        def _make_row(label_text, widget):
            row = QHBoxLayout()
            label = QLabel(label_text)
            label.setFixedWidth(LABEL_WIDTH)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(label)
            row.addWidget(widget, 1)
            return row

        self.ai_provider = QComboBox()
        self.ai_provider.blockSignals(True)
        self.ai_provider.addItems(list(PRESETS.keys()))
        self.ai_provider.blockSignals(False)
        self.ai_provider.currentTextChanged.connect(self._on_provider_changed)
        layout.addLayout(_make_row("Provider:", self.ai_provider))

        self.ai_base_url = QLineEdit()
        self.ai_base_url.setPlaceholderText("https://api.example.com/v1")
        layout.addLayout(_make_row("Base URL:", self.ai_base_url))

        self.ai_api_key = QLineEdit()
        self.ai_api_key.setPlaceholderText("sk-... (leave blank for local models)")
        self.ai_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addLayout(_make_row("API Key:", self.ai_api_key))

        # Model row: combo + a Refresh button that pulls the live model list
        # from the provider (uses the Base URL + API Key above). Falls back to
        # the provider's built-in preset list if the endpoint can't be reached.
        self.ai_model = QComboBox()
        self.ai_model.setEditable(True)
        self.ai_model.setPlaceholderText("model name")
        self.ai_refresh_btn = QPushButton("Refresh")
        self.ai_refresh_btn.setToolTip(
            "Fetch the live model list from this provider (needs Base URL, and an API Key for hosted providers)"
        )
        self.ai_refresh_btn.clicked.connect(self._fetch_models)
        _model_row = QHBoxLayout()
        _model_label = QLabel("Model:")
        _model_label.setFixedWidth(LABEL_WIDTH)
        _model_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        _model_row.addWidget(_model_label)
        _model_row.addWidget(self.ai_model, 1)
        _model_row.addWidget(self.ai_refresh_btn)
        layout.addLayout(_model_row)

        # Max tokens + reasoning controls (parity with the settings dialog).
        # The reasoning probe run by "Test Connection" auto-applies into these.
        self.ai_max_tokens = QSpinBox()
        self.ai_max_tokens.setRange(100, 8000)
        self.ai_max_tokens.setValue(self.config.ai_max_tokens or 450)
        layout.addLayout(_make_row("Max Tokens:", self.ai_max_tokens))

        self.ai_disable_thinking = QCheckBox("Disable Thinking / Reasoning Mode")
        self.ai_disable_thinking.setChecked(bool(getattr(self.config, "ai_disable_thinking", False)))
        layout.addLayout(_make_row("", self.ai_disable_thinking))

        # Probe-derived reasoning strategy (persisted in validatePage).
        self._reasoning_strategy = self.config.ai_reasoning_strategy or ""

        layout.addSpacing(12)

        # Model fetch status (compact hint)
        self.model_status = QLabel("")
        theme.mark_hint(self.model_status)
        layout.addWidget(self.model_status)

        layout.addSpacing(8)

        # Help links
        links_label = QLabel()
        links_label.setTextFormat(Qt.TextFormat.RichText)
        links_label.setOpenExternalLinks(True)
        links_label.setWordWrap(True)
        theme.mark_hint(links_label)
        links_label.setText(
            "Google Gemini (free tier): <a href='https://aistudio.google.com/apikey'>Get API Key</a><br>"
            "OpenAI: <a href='https://platform.openai.com/api-keys'>Get API Key</a><br>"
            "Groq (free tier): <a href='https://console.groq.com/keys'>Get API Key</a><br>"
            "OpenRouter: <a href='https://openrouter.ai/keys'>Get API Key</a><br>"
            "Ollama (local, free): <a href='https://ollama.com'>Download Ollama</a> — no API key needed"
        )
        layout.addWidget(links_label)

        layout.addSpacing(10)

        # Test Connection button — plain button, themed by the central QSS
        self.ai_test_btn = QPushButton("Test Connection")
        self.ai_test_btn.clicked.connect(self._test_ai_connection)
        self.ai_test_status = QLabel("")
        self.ai_test_status.setWordWrap(True)
        layout.addWidget(self.ai_test_btn)
        layout.addWidget(self.ai_test_status)

        layout.addStretch()

        layout.addSpacing(12)

        self.skip_check = QCheckBox("Skip AI setup for now")
        layout.addWidget(self.skip_check)

        scroll.setWidget(widget)

        # Set the scroll area as the page's main layout
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)

        self.registerField("ai_provider", self.ai_provider)
        self.registerField("ai_base_url", self.ai_base_url)
        self.registerField("ai_api_key", self.ai_api_key)
        self.registerField("ai_model", self.ai_model)

        # Fetch generation counter for discarding stale results
        self._fetch_generation = 0

        # Prefill from config — block signals to prevent triple-fire
        self.ai_provider.blockSignals(True)

        if config.ai_base_url:
            self.ai_base_url.setText(config.ai_base_url)
        if config.ai_api_key:
            self.ai_api_key.setText(config.ai_api_key)
        if config.ai_model:
            self.ai_model.setEditText(config.ai_model)
        if config.ai_provider and config.ai_provider in list(PRESETS.keys()):
            self.ai_provider.setCurrentText(config.ai_provider)

        self.ai_provider.blockSignals(False)

        # Now fire once for the current provider
        self._on_provider_changed(self.ai_provider.currentText())

    def _on_provider_changed(self, provider_name: str):
        from core.ai_analyst import PRESETS
        preset = PRESETS.get(provider_name, {})

        # Update base URL
        if preset.get("base_url"):
            self.ai_base_url.setText(preset["base_url"])
        else:
            self.ai_base_url.setText("")

        # Save current user selection before clearing
        previous_model = self.ai_model.currentText()

        self.ai_model.clear()

        preset_models = preset.get("models", [])
        if preset_models:
            self.ai_model.addItems(preset_models)

        default_model = preset.get("default_model", "")
        if default_model:
            idx = self.ai_model.findText(default_model)
            if idx >= 0:
                self.ai_model.setCurrentIndex(idx)
            else:
                self.ai_model.setEditText(default_model)
        elif previous_model:
            self.ai_model.setEditText(previous_model)

    def _fetch_models(self):
        """Fetch available models from the configured API endpoint."""
        base_url = self.ai_base_url.text().strip()
        api_key = self.ai_api_key.text().strip()

        if not base_url:
            return

        self.model_status.setText("Fetching models...")
        generation = self._fetch_generation  # capture current generation

        import threading
        def _fetch():
            from core.ai_analyst import FightAnalyst, PRESETS
            models = FightAnalyst.fetch_models(base_url, api_key)
            if not models:
                provider = self.ai_provider.currentText()
                preset = PRESETS.get(provider, {})
                models = preset.get("models", [])

            self.sig_models.emit(generation, models)

        threading.Thread(target=_fetch, daemon=True).start()

    @Slot(int, list)
    def _apply_models(self, generation: int, models: list):
        """Apply fetched model list to the combo box."""
        # Discard stale results from a previous provider selection
        if generation != self._fetch_generation:
            return

        if not models:
            self.model_status.setText("No models found — type a model name manually")
            return

        current = self.ai_model.currentText()
        self.ai_model.clear()
        self.ai_model.addItems(models)
        idx = self.ai_model.findText(current)
        if idx >= 0:
            self.ai_model.setCurrentIndex(idx)
        elif current:
            self.ai_model.setEditText(current)
        self.model_status.setText(f"Loaded {len(models)} models")

    def _test_ai_connection(self):
        """Probe the AI connection both ways and auto-apply the reasoning fix.

        Parity with the settings dialog (core/gui_settings.py). The worker
        thread NEVER touches a widget — it emits Qt Signals, which are
        queued onto the main thread, and auto-apply results are stashed on
        self._pending_apply then applied by the no-arg
        _apply_pending_reasoning slot.
        """
        base_url = self.ai_base_url.text().strip()
        api_key = self.ai_api_key.text().strip()
        model = self.ai_model.currentText().strip() if isinstance(self.ai_model, QComboBox) else self.ai_model.text().strip()

        if not base_url or not model:
            theme.set_state(self.ai_test_status, "warn")
            self.ai_test_status.setText("Enter a Base URL and Model first.")
            return

        test_summary = {
            "zone": "Eternal Battlegrounds",
            "duration": "05m 30s",
            "duration_seconds": 330,
            "outcome": "Decisive Win",
            "friendly_count": 35,
            "enemy_count": 50,
            "squad_count": 35,
            "ally_count": 10,
            "enemy_deaths": 27,
            "squad_damage": 5000000,
            "squad_dps": 15000,
            "squad_downs": 40,
            "squad_kills": 27,
            "squad_deaths": 6,
            "squad_healing": 8000000,
            "squad_barrier": 0,
            "enemy_total_damage": 6000000,
            "squad_strips": 45,
            "top_strips": [{"name": "TestPlayer", "profession": "Guardian", "boon_strips": 15}],
            "squad_cleanses": 30,
            "top_damage": [{"name": "TestPlayer", "profession": "Guardian", "damage": 800000}],
            "enemy_breakdown": {
                "Guardian": {"count": 8, "damage_per_player": 75000},
                "Necromancer": {"count": 6, "damage_per_player": 82000},
                "Elementalist": {"count": 5, "damage_per_player": 90000},
            },
            "top_enemy_skills": [
                {"name": "Meteor Shower", "damage": 120000},
                {"name": "Whirlwind", "damage": 95000},
            ],
            "enemy_teams": {"Red": 30, "Blue": 20},
            "squad_tag_distance": [
                {"name": "TestPlayer", "distance": 800.0},
                {"name": "TestPlayer2", "distance": 1200.0},
            ],
        }

        # The wizard AI page has no system-prompt field; use the analyst
        # default. Timeout comes from config (no per-page timeout widget here).
        system_prompt = None
        user_budget = self.ai_max_tokens.value()
        timeout = self.config.ai_timeout

        self.ai_test_btn.setEnabled(False)
        theme.set_state(self.ai_test_status, "busy")
        self.ai_test_status.setText("Testing…")
        self._last_report = None
        self._pending_apply = None

        import threading

        def _run():
            from core.reasoning_probe import run_probe, make_real_factory, format_report
            from core.reasoning_settings_apply import apply_report_to_config
            try:
                factory = make_real_factory(base_url, api_key, model, system_prompt)
                report = run_probe(
                    factory, test_summary, user_budget=user_budget,
                    base_url=base_url, model=model, timeout=timeout,
                    progress=lambda m: self.sig_ai_test_result.emit(m, "busy"),
                )
                self._last_report = report
                if report.auto_applicable and not report.failure:
                    self._pending_apply = apply_report_to_config(report)
                    self.sig_apply_pending_reasoning.emit()
                self.sig_ai_test_done.emit(format_report(report), not report.failure)
            except Exception as exc:  # noqa: BLE001
                self.sig_ai_test_done.emit(f"Test failed: {exc}", False)

        threading.Thread(target=_run, daemon=True).start()

    @Slot(str, str)
    def _set_ai_test_result(self, text, state):
        """Slot (main thread): progress updates from the probe worker."""
        theme.set_state(self.ai_test_status, state)
        self.ai_test_status.setText(text)

    @Slot()
    def _apply_pending_reasoning(self):
        """Slot (main thread): push probe-derived reasoning settings into widgets.

        Reads the dict stashed on self._pending_apply by the worker (passing a
        dict through a Signal argument is awkward, so we stash + emit the no-arg
        signal).
        """
        applied = getattr(self, "_pending_apply", None)
        if not applied:
            return
        self.ai_disable_thinking.setChecked(applied["ai_disable_thinking"])
        self.ai_max_tokens.setValue(applied["ai_max_tokens"])
        self._reasoning_strategy = applied["ai_reasoning_strategy"]  # saved on finish

    @Slot(str, bool)
    def _on_ai_test_done(self, message, success):
        """Slot (main thread): final probe report + re-enable + choice prompt."""
        theme.set_state(self.ai_test_status, "ok" if success else "error")
        self.ai_test_status.setText(message)
        self.ai_test_btn.setEnabled(True)

        report = getattr(self, "_last_report", None)
        if report is not None and getattr(report, "needs_choice", False):
            self._show_reasoning_choice(report)

    def _show_reasoning_choice(self, report):
        """Offer the user OFF vs ON reasoning alternatives with an Apply button.

        Runs on the main thread (invoked from _on_ai_test_done), so applying the
        chosen alternative directly through _apply_pending_reasoning is safe.
        """
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QRadioButton, QButtonGroup, QPushButton, QLabel
        )
        from core.reasoning_settings_apply import apply_report_to_config

        dialog = QDialog(self)
        dialog.setWindowTitle("Choose reasoning mode")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            "The model supports reasoning both ways. Pick which to apply:"
        ))

        group = QButtonGroup(dialog)
        buttons = {}  # alt_key -> QRadioButton
        for idx, alt in enumerate(report.alternatives):
            key, _sid, _dis, tokens, blurb = alt
            label = blurb or f"{key} ({tokens} max tokens)"
            rb = QRadioButton(label)
            if idx == 0:
                rb.setChecked(True)
            group.addButton(rb)
            buttons[key] = rb
            layout.addWidget(rb)

        apply_btn = QPushButton("Apply")
        layout.addWidget(apply_btn)

        def _do_apply():
            selected = next((k for k, rb in buttons.items() if rb.isChecked()), None)
            if selected is not None:
                self._pending_apply = apply_report_to_config(report, alt_key=selected)
                self._apply_pending_reasoning()
            dialog.accept()

        apply_btn.clicked.connect(_do_apply)
        dialog.exec()

    def validatePage(self):
        self.config.ai_max_tokens = self.ai_max_tokens.value()
        self.config.ai_disable_thinking = self.ai_disable_thinking.isChecked()
        self.config.ai_reasoning_strategy = getattr(self, "_reasoning_strategy", "")
        return True


class TTSVoicePage(QWizardPage):
    # Worker threads never touch widgets — they emit these signals, which Qt
    # queues onto the GUI thread. sig_tts_result carries a semantic state
    # ("ok"/"error"), never a color — the theme maps state to look.
    sig_play_audio = Signal(str)  # → _play_audio
    sig_tts_result = Signal(str, str)  # (text, state) → _set_tts_result
    sig_voices = Signal(str, list)  # → _set_voices (kind, names)
    sig_local_upload = Signal(str, str, str)  # voice, message, state

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.sig_play_audio.connect(self._play_audio)
        self.sig_tts_result.connect(self._set_tts_result)
        self.sig_voices.connect(self._set_voices)
        self.sig_local_upload.connect(self._finish_local_upload)
        # Not "(Optional)" anymore — this page only appears after the
        # operator opted in on page 2.
        self.setTitle("Voice / Text-to-Speech")

        # Scroll area wrapper for content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 8, 12, 8)

        desc = QLabel(
            "SparkyBot can read the AI fight commentary out loud using text-to-speech. "
            "Audio can play locally through your speakers and/or be attached to the Discord post "
            "as an inline audio player."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(6)

        self.enable_tts = QCheckBox("Play AI commentary through speakers")
        layout.addWidget(self.enable_tts)

        layout.addSpacing(6)

        self.tts_discord_attach = QCheckBox("Attach audio to Discord post")
        layout.addWidget(self.tts_discord_attach)

        layout.addSpacing(8)

        # Provider
        LABEL_WIDTH = 100

        def _make_row(label_text, widget):
            row = QHBoxLayout()
            label = QLabel(label_text)
            label.setFixedWidth(LABEL_WIDTH)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(label)
            row.addWidget(widget, 1)
            return row

        self.tts_provider = QComboBox()
        self.tts_provider.addItems(["edge", "elevenlabs", "local"])
        self.tts_provider.currentTextChanged.connect(self._on_provider_changed)
        layout.addLayout(_make_row("Provider:", self.tts_provider))

        provider_note = QLabel(
            "Edge: Free Microsoft neural voices, no API key needed (recommended). "
            "ElevenLabs: Premium quality voices, requires a paid API key. "
            "Local: your own speech server (OpenAI-compatible)."
        )
        provider_note.setWordWrap(True)
        theme.mark_hint(provider_note)
        layout.addWidget(provider_note)

        layout.addSpacing(8)

        # Edge fields (visible only when edge selected)
        self.edge_fields_widget = QFrame()
        edge_layout = QHBoxLayout(self.edge_fields_widget)
        edge_layout.setContentsMargins(0, 0, 0, 0)
        edge_label = QLabel("Voice:")
        edge_label.setFixedWidth(LABEL_WIDTH)
        edge_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        edge_layout.addWidget(edge_label)
        self.tts_edge_voice = QComboBox()
        self.tts_edge_voice.setEditable(True)
        self.tts_edge_voice.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.tts_edge_voice.addItem("en-GB-RyanNeural")
        edge_layout.addWidget(self.tts_edge_voice, 1)
        self.tts_edge_refresh_btn = QPushButton("Refresh Voices")
        self.tts_edge_refresh_btn.clicked.connect(self._refresh_edge_voices)
        edge_layout.addWidget(self.tts_edge_refresh_btn)
        layout.addWidget(self.edge_fields_widget)

        # Local server fields (visible only when local selected)
        self.local_fields_widget = QFrame()
        local_layout = QVBoxLayout(self.local_fields_widget)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.setSpacing(8)
        url_row = QHBoxLayout()
        url_label = QLabel("Server URL:")
        url_label.setFixedWidth(LABEL_WIDTH)
        url_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        url_row.addWidget(url_label)
        self.tts_local_url = QLineEdit()
        self.tts_local_url.setPlaceholderText("http://127.0.0.1:5820")
        url_row.addWidget(self.tts_local_url, 1)
        local_layout.addLayout(url_row)
        voice_row = QHBoxLayout()
        voice_label = QLabel("Voice:")
        voice_label.setFixedWidth(LABEL_WIDTH)
        voice_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        voice_row.addWidget(voice_label)
        self.tts_local_voice = QComboBox()
        self.tts_local_voice.setEditable(True)
        self.tts_local_voice.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        voice_row.addWidget(self.tts_local_voice, 1)
        self.tts_local_refresh_btn = QPushButton("Refresh")
        self.tts_local_refresh_btn.clicked.connect(self._refresh_local_voices)
        voice_row.addWidget(self.tts_local_refresh_btn)
        self.tts_local_upload_btn = QPushButton("Add Voice...")
        self.tts_local_upload_btn.setToolTip(
            "Choose a short (at least 3 seconds), clean recording.\n"
            "The local speech server adds it as a voice you can select.\n"
            "Only use a voice you own or have permission to clone."
        )
        self.tts_local_upload_btn.clicked.connect(self._upload_local_sample)
        voice_row.addWidget(self.tts_local_upload_btn)
        local_layout.addLayout(voice_row)
        local_note = QLabel(
            "Add Voice uploads a clean WAV, MP3, M4A, or FLAC sample to "
            "your local speech server and selects it here.")
        local_note.setWordWrap(True)
        theme.mark_hint(local_note)
        local_layout.addWidget(local_note)
        layout.addWidget(self.local_fields_widget)

        # ElevenLabs fields (visible only when elevenlabs selected)
        self.el_fields_widget = QFrame()

        def _make_el_row(label_text, widget):
            row = QHBoxLayout()
            label = QLabel(label_text)
            label.setFixedWidth(LABEL_WIDTH)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(label)
            row.addWidget(widget, 1)
            return row

        el_layout = QVBoxLayout(self.el_fields_widget)
        el_layout.setSpacing(8)

        self.tts_el_api_key = QLineEdit()
        self.tts_el_api_key.setPlaceholderText("sk_...")
        self.tts_el_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        el_layout.addLayout(_make_el_row("API Key:", self.tts_el_api_key))

        self.tts_el_voice_id = QLineEdit()
        self.tts_el_voice_id.setPlaceholderText("JBFqnCBsd6RMkjVDRZzb (George)")
        el_layout.addLayout(_make_el_row("Voice ID:", self.tts_el_voice_id))

        layout.addWidget(self.el_fields_widget)

        layout.addSpacing(10)

        # Test Voice button — plain button, themed by the central QSS
        self.tts_test_btn = QPushButton("Test Voice")
        self.tts_test_btn.clicked.connect(self._test_tts)
        self.tts_test_status = QLabel("")
        self.tts_test_status.setWordWrap(True)
        layout.addWidget(self.tts_test_btn)
        layout.addWidget(self.tts_test_status)

        layout.addSpacing(10)

        # Help links
        edge_help = QLabel("Edge TTS is free and requires no setup — just enable and go.")
        edge_help.setWordWrap(True)
        theme.mark_hint(edge_help)
        layout.addWidget(edge_help)

        elevenlabs_voices = QLabel("ElevenLabs: <a href='https://elevenlabs.io/app/voice-library'>Browse Voices</a>")
        elevenlabs_voices.setTextFormat(Qt.TextFormat.RichText)
        elevenlabs_voices.setOpenExternalLinks(True)
        elevenlabs_voices.setWordWrap(True)
        theme.mark_hint(elevenlabs_voices)
        layout.addWidget(elevenlabs_voices)

        elevenlabs_api = QLabel("ElevenLabs: <a href='https://elevenlabs.io/app/settings/api-keys'>Get API Key</a>")
        elevenlabs_api.setTextFormat(Qt.TextFormat.RichText)
        elevenlabs_api.setOpenExternalLinks(True)
        elevenlabs_api.setWordWrap(True)
        theme.mark_hint(elevenlabs_api)
        layout.addWidget(elevenlabs_api)

        note = QLabel(
            "Requires AI Fight Commentary to be enabled. TTS generates audio from the AI commentary text."
        )
        note.setWordWrap(True)
        theme.mark_hint(note)
        layout.addWidget(note)

        layout.addStretch()

        layout.addSpacing(12)

        self.skip_check = QCheckBox("Skip voice setup for now")
        layout.addWidget(self.skip_check)

        scroll.setWidget(widget)

        # Set the scroll area as the page's main layout
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)

        self.registerField("tts_provider", self.tts_provider)
        self.registerField("tts_el_api_key", self.tts_el_api_key)
        self.registerField("tts_el_voice_id", self.tts_el_voice_id)

        # Audio playback for test
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        self._audio_output = QAudioOutput()
        self._audio_output.setVolume(0.8)
        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._audio_output)
        self._temp_audio = None

        # Prefill from config
        if config.tts_provider:
            self.tts_provider.setCurrentText(config.tts_provider)
        if config.tts_edge_voice:
            self.tts_edge_voice.setEditText(config.tts_edge_voice)
        if getattr(config, "tts_local_url", ""):
            self.tts_local_url.setText(config.tts_local_url)
        if getattr(config, "tts_local_voice", ""):
            self.tts_local_voice.setEditText(config.tts_local_voice)
        if config.tts_elevenlabs_api_key:
            self.tts_el_api_key.setText(config.tts_elevenlabs_api_key)
        if config.tts_elevenlabs_voice_id:
            self.tts_el_voice_id.setText(config.tts_elevenlabs_voice_id)
        self._on_provider_changed(config.tts_provider or "edge")

    def _on_provider_changed(self, provider: str):
        provider = provider.lower()
        self.el_fields_widget.setVisible(provider == "elevenlabs")
        self.edge_fields_widget.setVisible(provider == "edge")
        self.local_fields_widget.setVisible(provider == "local")

    def _refresh_edge_voices(self):
        self.tts_edge_refresh_btn.setEnabled(False)
        self.tts_test_status.setText("Fetching Edge voices...")
        import threading

        def _fetch():
            try:
                import asyncio
                import edge_tts
                voices = asyncio.run(edge_tts.list_voices())
                names = sorted(v["ShortName"] for v in voices
                               if v["ShortName"].startswith("en-"))
                self.sig_voices.emit("edge", names)
            except Exception as e:
                self.sig_tts_result.emit(f"Failed to fetch voices: {e}", "error")
        threading.Thread(target=_fetch, daemon=True).start()

    def _refresh_local_voices(self):
        base_url = self.tts_local_url.text().strip().rstrip("/")
        if not base_url:
            self.tts_test_status.setText("Set the local server URL first.")
            return
        self.tts_local_refresh_btn.setEnabled(False)
        self.tts_test_status.setText("Fetching voices...")
        import threading

        def _fetch():
            try:
                import requests
                response = requests.get(f"{base_url}/v1/voices", timeout=10)
                response.raise_for_status()
                names = [v["voice"] for v in response.json().get("voices", [])]
                self.sig_voices.emit("local", names)
            except Exception as e:
                self.sig_tts_result.emit(f"Failed to fetch voices: {e}", "error")
        threading.Thread(target=_fetch, daemon=True).start()

    def _upload_local_sample(self):
        """Choose and upload a reference recording to the local TTS server."""
        base_url = self.tts_local_url.text().strip().rstrip("/")
        if not base_url:
            self._set_tts_result("Set the local server URL first.", "error")
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Choose a voice sample (at least 3 seconds of clean speech)",
            "", "Audio files (*.wav *.mp3 *.m4a *.flac);;All files (*)",
        )
        if not file_path:
            return

        import os
        raw_default = os.path.splitext(os.path.basename(file_path))[0]
        default_name = re.sub(
            r"[^A-Za-z0-9._-]+", "-", raw_default
        ).strip("-._") or "sample"
        name, ok = QInputDialog.getText(
            self, "Voice name",
            "Name this voice (letters, digits, . _ - only):",
            text=default_name,
        )
        if not ok or not name.strip():
            return
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-._")
        if not name:
            self._set_tts_result(
                "Voice name needs at least one letter or digit.", "error")
            return

        self.tts_local_upload_btn.setEnabled(False)
        self._set_tts_result("Adding voice sample...", "busy")

        def _upload():
            try:
                import requests
                with open(file_path, "rb") as sample:
                    response = requests.post(
                        f"{base_url}/v1/voices/samples",
                        files={"file": sample},
                        data={"name": name},
                        timeout=60,
                    )
                response.raise_for_status()
                voice = response.json().get("voice", f"sample:{name}")
                self.sig_local_upload.emit(
                    voice, f"Voice added as {voice}. It is selected and ready.",
                    "ok")
            except Exception as exc:
                detail = ""
                response = getattr(exc, "response", None)
                if response is not None:
                    try:
                        detail = response.json().get("detail", "")
                    except Exception:
                        detail = (response.text or "")[:200]
                self.sig_local_upload.emit(
                    "", f"Could not add voice: {detail or exc}", "error")

        threading.Thread(target=_upload, daemon=True).start()

    @Slot(str, str, str)
    def _finish_local_upload(self, voice: str, message: str, state: str):
        self.tts_local_upload_btn.setEnabled(True)
        if voice:
            self.tts_local_voice.setEditText(voice)
        self._set_tts_result(message, state)

    def _test_tts(self):
        self.tts_test_btn.setEnabled(False)
        theme.set_state(self.tts_test_status, "busy")
        self.tts_test_status.setText("Generating test audio...")

        import threading
        def _run():
            try:
                from core.tts import generate_tts_bytes

                provider = self.tts_provider.currentText()

                class _Cfg:
                    tts_provider = provider
                    tts_edge_voice = (self.tts_edge_voice.currentText().strip()
                                      or "en-GB-RyanNeural")
                    tts_local_url = self.tts_local_url.text().strip()
                    tts_local_voice = self.tts_local_voice.currentText().strip()
                    tts_volume = 80
                    tts_elevenlabs_api_key = self.tts_el_api_key.text().strip()
                    tts_elevenlabs_voice_id = self.tts_el_voice_id.text().strip() or "JBFqnCBsd6RMkjVDRZzb"
                    tts_elevenlabs_model = "eleven_multilingual_v2"
                    tts_elevenlabs_stability = 0.35
                    tts_elevenlabs_similarity_boost = 0.75
                    tts_elevenlabs_style = 0.15
                    tts_elevenlabs_speaker_boost = True
                    tts_elevenlabs_speed = 1.0

                audio_bytes = generate_tts_bytes(
                    "SparkyBot voice test. Let's get those bags.", _Cfg()
                )

                if audio_bytes and self.enable_tts.isChecked():
                    import tempfile, os
                    fd, path = tempfile.mkstemp(suffix=".mp3", prefix="sparkybot_test_")
                    with os.fdopen(fd, "wb") as f:
                        f.write(audio_bytes)
                    self.sig_play_audio.emit(path)
                    size_kb = len(audio_bytes) / 1024
                    self.sig_tts_result.emit(
                        f"Audio generated — playing through speakers.", "ok")
                elif audio_bytes:
                    size_kb = len(audio_bytes) / 1024
                    self.sig_tts_result.emit(
                        f"Audio generated successfully ({size_kb:.1f} KB). Provider is working.", "ok")
                else:
                    self.sig_tts_result.emit(
                        "Audio generation failed. Check provider settings and logs.", "error")
            except Exception as e:
                self.sig_tts_result.emit(f"Error: {e}", "error")

        threading.Thread(target=_run, daemon=True).start()

    @Slot(str, list)
    def _set_voices(self, kind: str, names: list):
        combo = self.tts_edge_voice if kind == "edge" else self.tts_local_voice
        current = combo.currentText().strip()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(names)
        if current and current in names:
            combo.setCurrentText(current)
        elif current:
            combo.setEditText(current)
        combo.blockSignals(False)
        btn = (self.tts_edge_refresh_btn if kind == "edge"
               else self.tts_local_refresh_btn)
        btn.setEnabled(True)
        theme.set_state(self.tts_test_status, "ok")
        self.tts_test_status.setText(f"{len(names)} voices loaded.")

    @Slot(str, str)
    def _set_tts_result(self, text, state):
        theme.set_state(self.tts_test_status, state)
        self.tts_test_status.setText(text)
        self.tts_test_btn.setEnabled(True)
        for btn in (getattr(self, "tts_edge_refresh_btn", None),
                    getattr(self, "tts_local_refresh_btn", None)):
            if btn is not None:
                btn.setEnabled(True)

    @Slot(str)
    def _play_audio(self, path: str):
        import os
        # Clean up previous temp file
        if self._temp_audio and os.path.exists(self._temp_audio):
            try:
                os.remove(self._temp_audio)
            except OSError:
                pass
        self._temp_audio = path
        self._player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
        self._player.play()

    def validatePage(self):
        return True


class BehaviorPage(QWizardPage):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.setTitle("Startup Behavior")

        # Scroll area wrapper for content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 8, 12, 8)

        desc = QLabel(
            "Configure how SparkyBot behaves when it starts and how it interacts with your system tray."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(8)

        # Start watcher on startup
        self.start_watcher_on_startup = QCheckBox("Start watching for logs automatically on launch")
        self.start_watcher_on_startup.setChecked(config.start_watcher_on_startup)
        start_watcher_note = QLabel(
            "When enabled, SparkyBot begins monitoring your log folder immediately "
            "without needing to click Start Watcher."
        )
        theme.mark_hint(start_watcher_note)
        layout.addWidget(self.start_watcher_on_startup)
        layout.addWidget(start_watcher_note)

        layout.addSpacing(12)

        # Start minimized
        self.start_minimized = QCheckBox("Start minimized to system tray")
        self.start_minimized.setChecked(config.start_minimized)
        start_minimized_note = QLabel(
            "SparkyBot launches silently in the background. Access it from the system tray icon."
        )
        theme.mark_hint(start_minimized_note)
        layout.addWidget(self.start_minimized)
        layout.addWidget(start_minimized_note)

        layout.addSpacing(12)

        # Close to tray
        self.close_to_tray = QCheckBox("Close to system tray instead of quitting")
        self.close_to_tray.setChecked(config.close_to_tray)
        close_note = QLabel(
            "Clicking the X button hides SparkyBot to the tray instead of exiting the application."
        )
        theme.mark_hint(close_note)
        layout.addWidget(self.close_to_tray)
        layout.addWidget(close_note)

        layout.addSpacing(12)

        # Minimize to tray
        self.minimize_to_tray = QCheckBox("Minimize to system tray")
        self.minimize_to_tray.setChecked(config.minimize_to_tray)
        minimize_to_tray_note = QLabel(
            "When you click the minimize button, SparkyBot goes to the system tray instead of the taskbar."
        )
        theme.mark_hint(minimize_to_tray_note)
        minimize_to_tray_note.setWordWrap(True)
        layout.addWidget(self.minimize_to_tray)
        layout.addWidget(minimize_to_tray_note)

        layout.addSpacing(12)

        # Check updates on launch
        self.check_updates_on_launch = QCheckBox("Check for updates on launch")
        self.check_updates_on_launch.setChecked(config.check_updates_on_launch)
        check_updates_note = QLabel(
            "Automatically checks GitHub for new SparkyBot and Elite Insights versions at startup."
        )
        theme.mark_hint(check_updates_note)
        layout.addWidget(self.check_updates_on_launch)
        layout.addWidget(check_updates_note)

        layout.addStretch()

        scroll.setWidget(widget)

        # Set the scroll area as the page's main layout
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)


class CompletePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Setup Complete")
        layout = QVBoxLayout(self)
        label = QLabel(
            "<p>SparkyBot is configured and ready.</p>"
            "<p>Click <b>Finish</b> to open the main settings window "
            "where you can adjust additional options.</p>"
            "<p>To start watching for logs, click <b>Start Watcher</b> "
            "in the main window.</p>"
        )
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(label)
        layout.addStretch()
