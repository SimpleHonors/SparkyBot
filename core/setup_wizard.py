"""First-run setup wizard for SparkyBot"""

import subprocess
import sys
import importlib.metadata
from PySide6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog, QCheckBox,
    QProgressBar, QFrame, QComboBox, QWidget, QScrollArea, QFormLayout,
    QRadioButton, QSpinBox, QInputDialog, QMessageBox
)
from PySide6.QtCore import Qt, Signal, Slot, QUrl, QStandardPaths
from PySide6.QtGui import QIcon
from pathlib import Path

from core import theme
from core.arcdps_config import (
    ArcDPSSetup,
    GW2Installation,
    discover_arcdps_setups,
    discover_gw2_installations,
    select_wvw_log_directory,
)
from core.discord_bot import normalize_webhook_url
from core.competitor_import import (
    CompetitorConfigError,
    CompetitorImportPlan,
    apply_competitor_import,
    merge_competitor_findings,
)
from core.competitor_import import discover_competitor_configs
from core.competitor_migration_ui import (
    choose_manual_competitor_import,
    preview_competitor_finding,
)
from core.shareable_config import (
    GuildConfigBundle, GuildConfigError, apply_guild_config, load_guild_config,
)

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
        self.imported_guild_config: GuildConfigBundle | None = None
        self.imported_competitor_plan: CompetitorImportPlan | None = None
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
        self.welcome_page = WelcomePage(config)
        self.setPage(PAGE_WELCOME, self.welcome_page)
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
        self.gw2ei_page = GW2EIPage(config)
        self.log_folder_page = LogFolderPage(config)
        self.setPage(PAGE_GW2EI, self.gw2ei_page)
        self.setPage(PAGE_LOG_FOLDER, self.log_folder_page)
        self.discord_page = DiscordPage(config)
        self.setPage(PAGE_DISCORD, self.discord_page)
        self.twitch_page = TwitchPage(config)
        self.ai_page = AIAnalysisPage(config)
        self.tts_page = TTSVoicePage(config)
        self.behavior_page = BehaviorPage(config)
        self.setPage(PAGE_TWITCH, self.twitch_page)
        self.setPage(PAGE_AI_SETUP, self.ai_page)
        self.setPage(PAGE_TTS_VOICE, self.tts_page)
        self.setPage(PAGE_BEHAVIOR, self.behavior_page)
        self.complete_page = CompletePage()
        self.setPage(PAGE_COMPLETE, self.complete_page)
        self.setStartId(PAGE_WELCOME)

    def ai_opted_in(self) -> bool:
        """The page-2 answer — single source for the AI/TTS page skip and
        for what accept() writes to AI/enableAiAnalysis."""
        return self.ai_optin_page.opted_in()

    def use_imported_guild_config(self, bundle: GuildConfigBundle) -> None:
        """Stage a GO-ready guild setup without writing a partial install."""
        if not bundle.enabled:
            raise GuildConfigError(
                "Discord posting is turned off in this setup file. Ask your "
                "guild admin to create a new setup file with posting enabled."
            )
        apply_guild_config(self.config, bundle, persist=False)
        self.imported_guild_config = bundle
        self.discord_page.load_from_config()

    def use_competitor_import(self, plan: CompetitorImportPlan) -> None:
        """Stage a competitor migration without touching the other tool."""
        # If a guild setup file was already loaded, its Discord routes remain
        # authoritative; the competitor contributes machine-local paths only.
        apply_competitor_import(
            self.config,
            plan,
            persist=False,
            turn_off_optional=True,
            include_discord=self.imported_guild_config is None,
        )
        self.imported_competitor_plan = plan
        if plan.parser_executable:
            self.gw2ei_page.path_edit.setText(str(plan.parser_executable))
        if plan.log_folder:
            self.log_folder_page.use_imported_folder(plan.log_folder)
        self.discord_page.load_from_config()

    def has_basic_import(self) -> bool:
        return bool(self.imported_guild_config or self.imported_competitor_plan)

    def imported_routing_ready(self) -> bool:
        if self.imported_guild_config is not None:
            return True
        plan = self.imported_competitor_plan
        return bool(plan and plan.has_discord_routing)

    def accept(self):
        """Save all wizard values to config on finish"""
        cfg = self.config.update
        imported_setup = self.has_basic_import()
        ei_path = self.field("gw2ei_path")
        if ei_path:
            cfg('Paths', 'gw2eiExe', ei_path)
        log_folder = self.field("log_folder")
        if log_folder:
            cfg('Paths', 'logFolder', log_folder)
        webhook = self.field("webhook") or ""
        cfg('Discord', 'discordWebhook', webhook)
        if not imported_setup or self.imported_competitor_plan is not None:
            cfg(
                'Discord', 'enableDiscordBot',
                str(not self.discord_page.skip_check.isChecked()).lower(),
            )

        # AI opt-in (page 2) is the SINGLE writer of the master switch —
        # the same AI/enableAiAnalysis key the Settings Application page
        # edits (LAW #2: zero new keys, zero renames).
        opted_in = False if imported_setup else self.ai_opted_in()
        cfg('AI', 'enableAiAnalysis', 'true' if opted_in else 'false')

        # Usage mode (page 3) — RaidReport/runMode, the same key the
        # Settings > Raid Reports "How reports get made" switch edits.
        cfg(
            'RaidReport', 'runMode',
            'run-button' if imported_setup else self.usage_mode_page.selected_mode(),
        )

        # Twitch
        if imported_setup:
            cfg('Twitch', 'enableTwitchBot', 'false')
            cfg('TTS', 'enableTts', 'false')
        elif hasattr(self.twitch_page, 'enable_twitch'):
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

        if not self.config.save():
            QMessageBox.warning(
                self,
                "Setup Was Not Saved",
                "SparkyBot could not save setup on this computer. Nothing was "
                "finished; check that the program folder is writable and try again.",
            )
            return
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
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.setTitle("Set up SparkyBot")
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # This starts hidden, so a computer with no supported neighbor app
        # never sees or has to understand this feature. initializePage() fills
        # and reveals it before the page is painted only when detection wins.
        self.competitor_offer = QFrame()
        offer_layout = QVBoxLayout(self.competitor_offer)
        offer_layout.setContentsMargins(0, 0, 0, 0)
        offer_layout.setSpacing(6)
        self.competitor_import_status = QLabel("")
        self.competitor_import_status.setWordWrap(True)
        self.competitor_import_status.setTextFormat(Qt.TextFormat.PlainText)
        offer_layout.addWidget(self.competitor_import_status)
        self.competitor_import_button = QPushButton("")
        self.competitor_import_button.setMinimumHeight(46)
        self.competitor_import_button.clicked.connect(
            self._import_competitor_config
        )
        offer_layout.addWidget(self.competitor_import_button)
        self.competitor_choose_different_button = QPushButton("Choose a settings file instead")
        self.competitor_choose_different_button.setFlat(True)
        self.competitor_choose_different_button.clicked.connect(
            self._choose_different_competitor_config
        )
        offer_layout.addWidget(self.competitor_choose_different_button)
        self.competitor_offer.hide()
        layout.addWidget(self.competitor_offer)

        self._detected_findings = ()
        self._competitor_scan_done = False

        # The easy path leads: most people have nothing to import, so the
        # default screen is just "click Next and we'll walk you through it."
        self.easy_path_intro = QLabel(
            "SparkyBot will walk you through setup step by step — where your "
            "fight logs live and which Discord channels get your reports.\n\n"
            "Click Next to begin."
        )
        self.easy_path_intro.setWordWrap(True)
        layout.addWidget(self.easy_path_intro)

        layout.addStretch()

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(divider)

        # Guild setup files are rare (one guild's members); the offer stays
        # findable at the bottom without competing with the main path.
        self.guild_file_intro = QLabel(
            "Did a guild member send you a SparkyBot setup file? Most people "
            "won't have one."
        )
        self.guild_file_intro.setWordWrap(True)
        theme.mark_hint(self.guild_file_intro)
        layout.addWidget(self.guild_file_intro)

        self.import_button = QPushButton("Use a Guild Setup File...")
        self.import_button.setFlat(True)
        self.import_button.setMinimumHeight(32)
        theme.set_widget_class(self.import_button, "secondary")
        self.import_button.setToolTip(
            "Loads your guild's Discord channels automatically. Only needed "
            "if a guild admin sent you a SparkyBot setup file."
        )
        self.import_button.clicked.connect(self._import_guild_config)
        layout.addWidget(self.import_button)

        self.guild_file_help = QLabel("")
        self.guild_file_help.setWordWrap(True)
        theme.mark_hint(self.guild_file_help)
        self.guild_file_help.hide()
        layout.addWidget(self.guild_file_help)

        self.import_status = QLabel("")
        self.import_status.setWordWrap(True)
        self.import_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.import_status)

        self.manual_help = QLabel("")
        self.manual_help.setWordWrap(True)
        theme.mark_hint(self.manual_help)
        self.manual_help.hide()
        layout.addWidget(self.manual_help)

        # Power-user escape hatch. The default screen says only "Advanced";
        # the missed-app wording does not exist visually until they open it.
        self.advanced_toggle = QPushButton("Advanced")
        self.advanced_toggle.setFlat(True)
        self.advanced_toggle.clicked.connect(self._toggle_advanced)
        layout.addWidget(self.advanced_toggle)
        self.advanced_options = QFrame()
        advanced_layout = QVBoxLayout(self.advanced_options)
        advanced_layout.setContentsMargins(12, 0, 0, 0)
        advanced_help = QLabel(
            "Using a fight-report app we did not find? Choose it manually."
        )
        advanced_help.setWordWrap(True)
        theme.mark_hint(advanced_help)
        advanced_layout.addWidget(advanced_help)
        self.advanced_competitor_button = QPushButton(
            "Choose an App and Its File"
        )
        self.advanced_competitor_button.setFlat(True)
        self.advanced_competitor_button.clicked.connect(
            self._choose_different_competitor_config
        )
        advanced_layout.addWidget(self.advanced_competitor_button)
        self.advanced_options.hide()
        layout.addWidget(self.advanced_options)

    def initializePage(self):
        super().initializePage()
        self._offer_detected_setups()

    def _offer_detected_setups(self):
        """Detect installed log tools and lead with a named one-click offer.

        EZ pleb mode: if PlenBot (or friends) is already configured on this
        computer, the user should not have to know or hunt for that — the
        button names the tool and one click opens the consent preview."""
        if self._competitor_scan_done:
            return
        self._competitor_scan_done = True
        wizard = self.wizard()
        if wizard is None or wizard.has_basic_import():
            return
        gw2_dirs = tuple(
            install.directory
            for install in getattr(
                getattr(wizard, "log_folder_page", None),
                "_gw2_installations", (),
            )
        )
        try:
            self._detected_findings = tuple(
                discover_competitor_configs(gw2_dirs=gw2_dirs)
            )
        except Exception:
            self._detected_findings = ()
        if not self._detected_findings:
            return
        findings = self._detected_findings
        top = findings[0]
        if len(findings) == 1:
            self.competitor_import_status.setText(
                f"SparkyBot found {top.app} already set up."
            )
            self.competitor_import_button.setText(
                f"Found {top.app} — set me up from it"
            )
            self.competitor_import_button.setToolTip(
                f"Reuse your existing {top.app} choices."
            )
            tone_app = top.app
        else:
            # Every found tool is combined in one click — nothing is buried.
            names = ", ".join(item.app for item in findings)
            self.competitor_import_status.setText(
                f"SparkyBot found {len(findings)} log-tool setups on this PC "
                f"({names}) and will combine them."
            )
            self.competitor_import_button.setText(
                "Set me up from my log tools"
            )
            self.competitor_import_button.setToolTip(
                "Combine settings from every setup found; you pick which one "
                "wins if any disagree."
            )
            tone_app = "your log tools"
        theme.set_state(self.competitor_import_status, "success")
        theme.set_widget_class(self.competitor_import_button, "primary")
        self.competitor_offer.show()
        self.advanced_toggle.hide()
        self.advanced_options.hide()
        self._set_guild_file_tone(tone_app, imported=False)

    def _toggle_advanced(self) -> None:
        opening = self.advanced_options.isHidden()
        self.advanced_options.setVisible(opening)
        self.advanced_toggle.setText("Hide Advanced" if opening else "Advanced")

    def _set_guild_file_tone(self, app: str, *, imported: bool) -> None:
        """Keep the guild override available without competing with detection."""
        # Detection (or a finished import) is now the lead story; the generic
        # walk-through pitch would contradict it, and the helper labels earn
        # their space back.
        self.easy_path_intro.hide()
        self.guild_file_help.show()
        self.manual_help.show()
        if imported:
            self.guild_file_intro.setText(
                "Did your guild admin also send you a SparkyBot file? Add it "
                "on top if you have one. Otherwise, keep going — your base "
                "setup is ready."
            )
            self.guild_file_help.setText(
                f"It can correct your guild's Discord channels. Your fight "
                f"folder and other choices from {app} will stay."
            )
            self.import_button.setText("Add My Guild's File")
            self.manual_help.setText(
                "No guild file? Click Next. SparkyBot will check anything "
                "still needed."
            )
        else:
            self.guild_file_intro.setText(
                f"Start with {app} above. If your guild admin also sent you a "
                "SparkyBot file, you can add it afterward."
            )
            self.guild_file_help.setText(
                "The guild file can supply the exact Logspam and nightly "
                "channels. If you do not have one, that is okay."
            )
            self.import_button.setText("I Also Have a Guild File")
            self.manual_help.setText(
                "Use the found setup above, or click Next to set up by hand."
            )
        self.import_button.setFlat(True)
        self.import_button.setMinimumHeight(32)
        theme.set_widget_class(self.import_button, "secondary")

    def nextId(self):
        wizard = self.wizard()
        if wizard is not None and wizard.has_basic_import():
            if wizard.page(PAGE_DEPENDENCIES) is not None:
                return PAGE_DEPENDENCIES
            if wizard.gw2ei_page.is_ready():
                if wizard.log_folder_page.is_ready():
                    return (
                        PAGE_COMPLETE
                        if wizard.imported_routing_ready()
                        else PAGE_DISCORD
                    )
                return PAGE_LOG_FOLDER
            return PAGE_GW2EI
        return PAGE_AI_OPTIN

    def _import_competitor_config(self):
        if len(self._detected_findings) > 1:
            self._start_merged_competitor_setup()
        else:
            self._start_competitor_setup(use_detected=True)

    def _choose_different_competitor_config(self):
        self._start_competitor_setup(use_detected=False)

    def _start_merged_competitor_setup(self):
        findings = self._detected_findings
        # One question is the whole ask: which tool wins on any conflict.
        # Labels are made unique so duplicate app names map to the right index.
        labels = []
        for index, item in enumerate(findings):
            label = item.app
            if any(other.app == item.app for j, other in enumerate(findings) if j != index):
                # Same app in two places (e.g. AxiBridge in Roaming AND Local):
                # the parent folder name alone is often just the app name, so
                # show the full path so the human can tell the installs apart.
                label = f"{item.app} — {item.source_file.parent}"
            while label in labels:
                label = f"{label} ({index + 1})"
            labels.append(label)
        choice, ok = QInputDialog.getItem(
            self,
            "Combine Your Log-Tool Setups",
            f"SparkyBot found {len(findings)} log-tool setups on this PC and "
            "will combine them.\nWhich do you use most? (it wins if any "
            "settings disagree)",
            labels,
            0,
            False,
        )
        if not ok:
            return
        primary_index = labels.index(choice)
        try:
            merged = merge_competitor_findings(findings, primary_index=primary_index)
        except CompetitorConfigError as exc:
            QMessageBox.warning(self, "Settings Were Not Combined", str(exc))
            return
        # Reuse the existing grouped consent preview + single atomic apply.
        plan = preview_competitor_finding(merged, self)
        if plan is None:
            return
        wizard = self.wizard()
        if wizard is None or not hasattr(wizard, "use_competitor_import"):
            QMessageBox.warning(
                self,
                "Setup Import Is Not Ready",
                "Close this setup window, reopen it, and try again.",
            )
            return
        try:
            wizard.use_competitor_import(plan)
        except CompetitorConfigError as exc:
            QMessageBox.warning(self, "Settings Were Not Imported", str(exc))
            return
        theme.set_state(self.competitor_import_status, "success")
        self.competitor_import_status.setText(
            f"SparkyBot combined settings from {len(findings)} tools "
            f"(using {findings[primary_index].app} for anything they "
            "disagreed on).\nAdd your guild's file below if you have one, "
            "or click Next."
        )
        self.competitor_import_button.hide()
        self._detected_findings = (plan.finding,)
        self.competitor_offer.show()
        self.advanced_toggle.hide()
        self.advanced_options.hide()
        self._set_guild_file_tone(findings[primary_index].app, imported=True)

    def _start_competitor_setup(self, *, use_detected: bool):
        wizard = self.wizard()
        if wizard is None or not hasattr(wizard, "use_competitor_import"):
            QMessageBox.warning(
                self,
                "Setup Import Is Not Ready",
                "Close this setup window, reopen it, and try again.",
            )
            return
        gw2_dirs = tuple(
            install.directory
            for install in getattr(
                wizard.log_folder_page, "_gw2_installations", ()
            )
        )
        if use_detected:
            if not self._detected_findings:
                return
            # The primary offer always names this exact tool — no picker,
            # straight to the complete consent preview even when others exist.
            plan = preview_competitor_finding(self._detected_findings[0], self)
        else:
            plan = choose_manual_competitor_import(self, gw2_dirs=gw2_dirs)
        if plan is None:
            return
        try:
            wizard.use_competitor_import(plan)
        except CompetitorConfigError as exc:
            QMessageBox.warning(self, "Settings Were Not Imported", str(exc))
            return
        theme.set_state(self.competitor_import_status, "success")
        self.competitor_import_status.setText(
            f"SparkyBot is set up from {plan.finding.app}.\n"
            "The other tool was not changed. Add your guild's file below if "
            "you have one, or click Next to check anything it did not store."
        )
        self.competitor_import_button.setText(
            f"Set me up again from {plan.finding.app}"
        )
        self.competitor_import_button.hide()
        self._detected_findings = (plan.finding,)
        self.competitor_offer.show()
        self.advanced_toggle.hide()
        self.advanced_options.hide()
        self._set_guild_file_tone(plan.finding.app, imported=True)

    def _confirm_import(self, bundle: GuildConfigBundle) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Use This Guild Setup?")
        box.setText("Set up SparkyBot with these Discord destinations?")
        box.setInformativeText(
            f"{bundle.routing_summary()}\n\n"
            "Only use a setup file sent by a guild admin you trust."
        )
        box.setMinimumWidth(480)
        accept_button = box.addButton(
            "Set Up SparkyBot", QMessageBox.ButtonRole.AcceptRole
        )
        cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel_button)
        box.exec()
        return box.clickedButton() is accept_button

    def _import_guild_config(self):
        downloads = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DownloadLocation
        )
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Choose Guild Setup File",
            downloads,
            "SparkyBot Guild Setup (*.json);;JSON files (*.json)",
        )
        if not path:
            return
        try:
            bundle = load_guild_config(path)
        except GuildConfigError as exc:
            QMessageBox.warning(self, "Setup File Not Used", str(exc))
            return

        if not bundle.enabled:
            QMessageBox.warning(
                self,
                "Setup File Not Ready",
                "Discord posting is turned off in this setup file. Ask your "
                "guild admin to create a new setup file with posting enabled.",
            )
            return

        if not self._confirm_import(bundle):
            return
        try:
            wizard = self.wizard()
            if wizard is None or not hasattr(wizard, "use_imported_guild_config"):
                raise GuildConfigError("The setup window is not ready. Please try again.")
            wizard.use_imported_guild_config(bundle)
        except GuildConfigError as exc:
            QMessageBox.warning(self, "Setup File Not Used", str(exc))
            return

        theme.set_state(self.import_status, "success")
        self.import_status.setText(
            "Guild setup loaded.\n"
            f"{bundle.routing_summary()}\n\n"
            "Click Next. SparkyBot will only check the parser and fight-log "
            "folder on this computer."
        )
        self.import_button.setText("Choose a Different Setup File...")
        theme.set_widget_class(self.import_button, "")
        wizard.next()


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
        self.setTitle("Install the fight-log parser")

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 8, 12, 8)

        # Description - NOT in setSubTitle so it wraps properly
        desc = QLabel(
            "SparkyBot needs GW2 Elite Insights to turn game logs into reports. "
            "The recommended button handles the setup automatically."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # PRIMARY: Download and install
        rec_label = QLabel("<b>Automatic setup (recommended)</b>")
        rec_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(rec_label)

        rec_desc = QLabel(
            "Install the required parser in the SparkyBot program folder. "
            "No paths or technical choices are needed."
        )
        rec_desc.setWordWrap(True)
        layout.addWidget(rec_desc)

        self.download_btn = QPushButton("Install Fight-Log Parser")
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

        self.advanced_toggle = QPushButton("I already have the parser (advanced)")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        layout.addWidget(self.advanced_toggle)

        self.advanced_divider = QFrame()
        self.advanced_divider.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(self.advanced_divider)

        # SECONDARY: Manual path
        self.advanced_label = QLabel(
            "<b>Use an existing GW2EI installation</b>"
        )
        self.advanced_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.advanced_label)

        self.advanced_description = QLabel(
            "Only use this if you want to point SparkyBot to an existing "
            "parser. Leave this blank after using automatic setup."
        )
        self.advanced_description.setWordWrap(True)
        theme.mark_hint(self.advanced_description)
        layout.addWidget(self.advanced_description)

        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(
            "Optional existing GuildWars2EliteInsights-CLI.exe"
        )
        # No prefill - do not expose user's personal folder structure
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._browse)
        row.addWidget(self.path_edit)
        row.addWidget(self.browse_btn)
        layout.addLayout(row)

        self._toggle_advanced(False)

        layout.addStretch()
        self.registerField("gw2ei_path", self.path_edit)

        # Determine initial button state by checking install and version
        self._check_initial_state()

    def _toggle_advanced(self, visible: bool):
        for widget in (
            self.advanced_divider,
            self.advanced_label,
            self.advanced_description,
            self.path_edit,
            self.browse_btn,
        ):
            widget.setVisible(visible)

    def _check_initial_state(self):
        """Check if GW2EI is installed and whether it needs updating."""
        default_exe = __import__("core.apppaths", fromlist=["gw2ei_dir"]).gw2ei_dir() / "GuildWars2EliteInsights-CLI.exe"

        if not default_exe.exists():
            self.download_btn.setText("Install Fight-Log Parser")
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
            self.download_btn.setText("Reinstall Fight-Log Parser")
        else:
            theme.set_state(self.download_status, "error")
            self.download_status.setText(f"Download failed: {message}")
            self.download_btn.setText("Install Fight-Log Parser")

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
            "The fight-log parser is required. Click Install Fight-Log Parser "
            "above, or choose an existing copy under Advanced."
        )
        return False

    def is_ready(self) -> bool:
        path = self.path_edit.text().strip()
        return bool((path and Path(path).is_file()) or self._install_success)

    def nextId(self):
        wizard = self.wizard()
        if (
            wizard is not None
            and wizard.has_basic_import()
            and wizard.log_folder_page.is_ready()
        ):
            return (
                PAGE_COMPLETE
                if wizard.imported_routing_ready()
                else PAGE_DISCORD
            )
        return PAGE_LOG_FOLDER


class LogFolderPage(QWizardPage):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.setTitle("Find fight logs on this computer")
        self.setSubTitle(
            "SparkyBot watches the folder where ArcDPS saves WvW fight logs."
        )
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 8, 12, 8)

        # Prefer ArcDPS's own explicit setting. The conventional Documents
        # location remains a fallback when ArcDPS has no custom path set.
        self._gw2_installations = self._detect_gw2_installations()
        self._arcdps_setups = self._detect_arcdps_setups()
        self._arcdps_setup = self._arcdps_setups[0] if self._arcdps_setups else None
        self._default_path = self._detect_default_log_path()

        if self._arcdps_setup is not None:
            self.detected_label = QLabel(self._detected_setup_text(self._arcdps_setup))
        else:
            gw2_note = ""
            if self._gw2_installations:
                first = self._gw2_installations[0].directory
                more = len(self._gw2_installations) - 1
                gw2_note = (
                    f"Guild Wars 2 found: <code>{first}</code><br>"
                    + (f"<small>Also found {more} other GW2 install(s).</small><br>" if more else "")
                    + "<small>ArcDPS settings were not found beside it.</small><br><br>"
                )
            self.detected_label = QLabel(
                gw2_note
                +
                f"Default ArcDPS log location detected:<br>"
                f"<code>{self._default_path}</code><br>"
                f"<small>SparkyBot selects the WvW subfolder automatically. "
                f"Change it below only if this location is wrong.</small>"
            )
        self.detected_label.setWordWrap(True)
        self.detected_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.detected_label)

        self.setup_combo = None
        if len(self._arcdps_setups) > 1:
            choose_label = QLabel("More than one ArcDPS setup was found:")
            layout.addWidget(choose_label)
            self.setup_combo = QComboBox()
            for setup in self._arcdps_setups:
                self.setup_combo.addItem(str(setup.arcdps_directory), setup)
            self.setup_combo.currentIndexChanged.connect(self._select_detected_setup)
            layout.addWidget(self.setup_combo)

        self.use_arcdps_btn = QPushButton("Yes — Use This ArcDPS Setup")
        self.use_arcdps_btn.setMinimumHeight(36)
        theme.set_widget_class(self.use_arcdps_btn, "primary")
        self.use_arcdps_btn.clicked.connect(self._use_arcdps_location)
        self.use_arcdps_btn.setVisible(self._arcdps_setup is not None)
        layout.addWidget(self.use_arcdps_btn)

        self.use_default_btn = QPushButton("Yes — Use This Folder")
        self.use_default_btn.setMinimumHeight(36)
        theme.set_widget_class(self.use_default_btn, "primary")
        self.use_default_btn.clicked.connect(self._use_default)
        self.use_default_btn.setVisible(self._arcdps_setup is None)
        layout.addWidget(self.use_default_btn)

        self.find_arcdps_btn = QPushButton("ArcDPS Is Somewhere Else...")
        self.find_arcdps_btn.clicked.connect(self._browse_arcdps_settings)
        layout.addWidget(self.find_arcdps_btn)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(divider)

        self.manual_toggle = QPushButton("Choose a Different Folder (advanced)")
        self.manual_toggle.setCheckable(True)
        self.manual_toggle.toggled.connect(self._toggle_manual_folder)
        layout.addWidget(self.manual_toggle)

        self.manual_label = QLabel("<b>Enter the fight-log folder:</b>")
        self.manual_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.manual_label)

        row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText(
            "Path to your ArcDPS WvW log folder"
        )
        # No prefill - do not expose user's personal folder structure
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._browse)
        row.addWidget(self.folder_edit)
        row.addWidget(self.browse_btn)
        layout.addLayout(row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        layout.addStretch()
        self.registerField("log_folder", self.folder_edit)
        self._toggle_manual_folder(False)
        if self._arcdps_setup is not None:
            theme.set_state(self.status_label, "warn")
            self.status_label.setText(
                "Check the locations above, then accept them or choose another setup."
            )
        else:
            theme.set_state(self.status_label, "warn")
            self.status_label.setText(
                "Use the folder above if it is right, or choose a different folder."
            )

    def _toggle_manual_folder(self, visible: bool):
        self.manual_label.setVisible(visible)
        self.folder_edit.setVisible(visible)
        self.browse_btn.setVisible(visible)

    @staticmethod
    def _documents_path() -> Path:
        """Resolve the current Windows user's Documents folder."""
        try:
            import ctypes
            import ctypes.wintypes

            buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
            ctypes.windll.shell32.SHGetFolderPathW(0, 0x0005, 0, 0, buf)
            if buf.value:
                return Path(buf.value)
        except Exception:
            pass
        return Path.home() / "Documents"

    def _detect_gw2_installations(self) -> tuple[GW2Installation, ...]:
        return discover_gw2_installations()

    def _detect_arcdps_setups(self) -> tuple[ArcDPSSetup, ...]:
        return discover_arcdps_setups(
            self._documents_path(),
            gw2_installations=self._gw2_installations,
        )

    @staticmethod
    def _detected_setup_text(setup: ArcDPSSetup) -> str:
        gw2 = (
            str(setup.gw2_directory)
            if setup.gw2_directory
            else "not matched automatically"
        )
        wvw_logs = select_wvw_log_directory(setup.log_directory)
        return (
            "SparkyBot found an ArcDPS setup:<br>"
            f"<small>Guild Wars 2: <code>{gw2}</code><br>"
            f"ArcDPS: <code>{setup.arcdps_directory}</code></small><br><br>"
            "ArcDPS says WvW fight logs go here:<br>"
            f"<code>{wvw_logs}</code><br>"
            "<small>Use these locations?</small>"
        )

    def _select_detected_setup(self, index: int):
        if self.setup_combo is None:
            return
        setup = self.setup_combo.itemData(index)
        if not isinstance(setup, ArcDPSSetup):
            return
        self._arcdps_setup = setup
        self.detected_label.setText(self._detected_setup_text(setup))
        self.folder_edit.clear()
        self.manual_toggle.setChecked(False)
        self.use_arcdps_btn.setVisible(True)
        theme.set_state(self.status_label, "warn")
        self.status_label.setText("Check the locations above, then accept them.")

    def _browse_arcdps_settings(self):
        start = (
            str(self._arcdps_setup.arcdps_directory)
            if self._arcdps_setup is not None
            else str(self._documents_path())
        )
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Find ArcDPS Settings",
            start,
            "ArcDPS settings (arcdps.ini);;INI files (*.ini)",
        )
        if not path:
            return
        setups = discover_arcdps_setups(
            self._documents_path(),
            gw2_installations=(),
            extra_config_files=(path,),
            include_system=False,
        )
        if not setups:
            QMessageBox.warning(
                self,
                "ArcDPS Settings Not Found",
                "That file is not readable ArcDPS settings. Choose arcdps.ini.",
            )
            return
        selected = setups[0]
        if self.setup_combo is not None:
            selected_index = -1
            for index in range(self.setup_combo.count()):
                item = self.setup_combo.itemData(index)
                if (
                    isinstance(item, ArcDPSSetup)
                    and item.config_file == selected.config_file
                ):
                    selected_index = index
                    break
            if selected_index < 0:
                self.setup_combo.addItem(
                    f"Chosen manually: {selected.arcdps_directory}", selected
                )
                selected_index = self.setup_combo.count() - 1
            self.setup_combo.setCurrentIndex(selected_index)
        self._arcdps_setup = selected
        self.detected_label.setText(self._detected_setup_text(self._arcdps_setup))
        self.use_default_btn.setVisible(False)
        self.use_arcdps_btn.setVisible(True)
        self.folder_edit.clear()
        self.manual_toggle.setChecked(False)
        theme.set_state(self.status_label, "warn")
        self.status_label.setText("Check the locations above, then accept them.")

    def _detect_default_log_path(self) -> str:
        """Auto-detect the default ArcDPS log folder for the current Windows user."""
        candidate = (
            self._documents_path()
            / "Guild Wars 2"
            / "addons"
            / "arcdps"
            / "arcdps.cbtlogs"
        )
        # Return it even before it exists so the fallback remains visible.
        return str(candidate)

    def _use_arcdps_location(self):
        if self._arcdps_setup is None:
            return
        target = select_wvw_log_directory(self._arcdps_setup.log_directory)
        self.folder_edit.setText(str(target))
        if target.is_dir():
            theme.set_state(self.status_label, "ok")
            self.status_label.setText("ArcDPS fight-log folder selected.")
            if self.use_arcdps_btn is not None:
                self.use_arcdps_btn.setVisible(False)
            self.manual_toggle.setChecked(False)
        else:
            theme.set_state(self.status_label, "warn")
            self.status_label.setText(
                "ArcDPS points to this folder, but it is not available right "
                "now. Connect that drive or choose a different folder."
            )

    def _use_default(self):
        base = Path(self._default_path)
        selected = select_wvw_log_directory(base)
        wvw_folder = str(selected) if selected.is_dir() else None
        target = str(selected)
        self.folder_edit.setText(target)

        if wvw_folder:
            theme.set_state(self.status_label, "ok")
            self.status_label.setText(
                f"WvW log folder found: {target}"
            )
            if self.use_default_btn is not None:
                self.use_default_btn.setVisible(False)
            self.manual_toggle.setChecked(False)
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
            self.manual_toggle.setChecked(True)

    def use_imported_folder(self, folder: str | Path) -> None:
        """Use a path the user explicitly approved in the import preview."""
        target = Path(folder)
        self.folder_edit.setText(str(target))
        if target.is_dir():
            theme.set_state(self.status_label, "ok")
            self.status_label.setText(
                "Fight-log folder imported from the other log tool."
            )
            self.manual_toggle.setChecked(False)
            self.use_arcdps_btn.setVisible(False)
            self.use_default_btn.setVisible(False)
        else:
            theme.set_state(self.status_label, "warn")
            self.status_label.setText(
                "The imported folder is not available right now. Connect "
                "that drive or choose a different folder."
            )
            self.manual_toggle.setChecked(True)

    def validatePage(self):
        folder = self.folder_edit.text().strip()
        if not self.is_ready():
            theme.set_state(self.status_label, "warn")
            self.status_label.setText(
                "SparkyBot needs a real fight-log folder before setup can finish. "
                "Play one WvW fight with ArcDPS logging enabled, then use the "
                "recommended location again or choose the folder manually."
            )
            return False
        theme.set_state(self.status_label, "ok")
        self.status_label.setText("Fight-log folder ready.")
        return True

    def is_ready(self) -> bool:
        folder = self.folder_edit.text().strip()
        return bool(folder and Path(folder).is_dir())

    def nextId(self):
        wizard = self.wizard()
        if wizard is not None and wizard.has_basic_import():
            return (
                PAGE_COMPLETE
                if wizard.imported_routing_ready()
                else PAGE_DISCORD
            )
        return PAGE_DISCORD


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
        layout.addWidget(QLabel("Webhook URL:"))
        layout.addWidget(self.webhook_edit)

        self.skip_check = QCheckBox("Skip Discord setup for now")
        layout.addWidget(self.skip_check)

        self.registerField("webhook", self.webhook_edit)
        self.load_from_config()

    def load_from_config(self):
        """Refresh the visible first slot after a Welcome-page import."""
        self.webhook_edit.setText(self.config.discord_webhook or "")
        self.skip_check.setChecked(not self.config.enable_discord_bot)

    def validatePage(self):
        if self.skip_check.isChecked():
            wizard = self.wizard()
            if (
                wizard is not None
                and getattr(wizard, "imported_competitor_plan", None) is not None
            ):
                QMessageBox.warning(
                    self,
                    "Discord Destination Needed",
                    "The imported tool did not provide complete Discord routing. "
                    "Paste one webhook so SparkyBot can post both individual "
                    "fights and the nightly debrief. You can split them into "
                    "separate channels later.",
                )
                self.skip_check.setChecked(False)
                return False
            return True
        url = self.webhook_edit.text().strip()
        if not url:
            QMessageBox.warning(
                self,
                "Discord Webhook Needed",
                "Paste the Discord webhook from your guild admin, or choose "
                "Skip Discord setup for now.",
            )
            self.webhook_edit.setFocus()
            return False
        normalized = normalize_webhook_url(url)
        if normalized is not None:
            self.webhook_edit.setText(normalized)
            return True
        QMessageBox.warning(
            self,
            "Incomplete Discord Webhook",
            "A token alone is missing the webhook ID. In Discord, open "
            "Server Settings > Integrations > Webhooks, choose your webhook, "
            "then click Copy Webhook URL. You can also paste ID/token.",
        )
        self.webhook_edit.setFocus()
        return False

    def nextId(self):
        wizard = self.wizard()
        if (
            wizard is not None
            and getattr(wizard, "imported_competitor_plan", None) is not None
        ):
            return PAGE_COMPLETE
        return super().nextId()


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
        self.setTitle("Ready to go")
        layout = QVBoxLayout(self)
        self.summary_label = QLabel(
            "SparkyBot is configured and ready.\n\n"
            "Click Finish and Open SparkyBot, then click Start Run."
        )
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary_label)
        layout.addStretch()

    def initializePage(self):
        wizard = self.wizard()
        bundle = getattr(wizard, "imported_guild_config", None)
        if bundle is not None:
            self.summary_label.setText(
                "SparkyBot is ready.\n\n"
                f"✓ {bundle.routing_summary().replace(chr(10), chr(10) + '✓ ')}\n"
                "✓ AI, voice, and Twitch are off\n"
                "✓ This computer's parser and fight-log folder are ready\n\n"
                "Click Finish and Open SparkyBot, then click Start Run."
            )
        elif getattr(wizard, "imported_competitor_plan", None) is not None:
            plan = wizard.imported_competitor_plan
            self.summary_label.setText(
                "SparkyBot is ready.\n\n"
                f"✓ Setup reused from {plan.finding.app}\n"
                "✓ Individual fights and nightly debrief are routed\n"
                "✓ AI, voice, and Twitch are off\n"
                "✓ This computer's parser and fight-log folder are ready\n"
                "✓ The other log tool and its credentials were not changed\n\n"
                "Click Finish and Open SparkyBot, then click Start Run."
            )
        else:
            self.summary_label.setText(
                "SparkyBot is configured and ready.\n\n"
                "Click Finish and Open SparkyBot, then click Start Run."
            )
        if wizard is not None:
            wizard.setButtonText(
                QWizard.WizardButton.FinishButton,
                "Finish and Open SparkyBot"
            )
