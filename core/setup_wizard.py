"""First-run setup wizard for SparkyBot"""

import subprocess
import sys
import importlib.metadata
from PyQt6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog, QCheckBox,
    QProgressBar, QFrame, QComboBox, QWidget, QScrollArea, QFormLayout
)
from PyQt6.QtCore import Qt, pyqtSlot, Q_ARG, QMetaObject, QUrl
from PyQt6.QtGui import QColor, QPalette, QIcon
from pathlib import Path


class SetupWizard(QWizard):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("SparkyBot Setup")
        self.setMinimumSize(700, 620)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        # Set window icon to sbtray.ico
        icon_path = Path(__file__).parent.parent / "assets" / "sbtray.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # Force dark palette on the wizard itself
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor('#2b2b2b'))
        palette.setColor(QPalette.ColorRole.WindowText, QColor('#ffffff'))
        palette.setColor(QPalette.ColorRole.Base, QColor('#3c3f41'))
        palette.setColor(QPalette.ColorRole.Text, QColor('#ffffff'))
        palette.setColor(QPalette.ColorRole.Button, QColor('#555555'))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor('#ffffff'))
        self.setPalette(palette)

        self.setStyleSheet("""
            QWizard, QWizardPage {
                background-color: #2b2b2b;
                color: #ffffff;
            }
            QLabel {
                color: #ffffff;
            }
            QLineEdit {
                background-color: #3c3f41;
                color: #ffffff;
                border: 1px solid #555;
                padding: 4px;
            }
            QPushButton#browseBtn {
                background-color: #555;
                color: white;
                border-radius: 3px;
                padding: 4px 8px;
            }
            QCheckBox {
                color: #ffffff;
            }
        """)
        self.addPage(WelcomePage())
        self.addPage(DependenciesPage())
        self.addPage(GW2EIPage(config))
        self.addPage(LogFolderPage(config))
        self.addPage(DiscordPage(config))
        self.twitch_page = TwitchPage(config)
        self.ai_page = AIAnalysisPage(config)
        self.tts_page = TTSVoicePage(config)
        self.behavior_page = BehaviorPage(config)
        self.addPage(self.twitch_page)
        self.addPage(self.ai_page)
        self.addPage(self.tts_page)
        self.addPage(self.behavior_page)
        self.addPage(CompletePage())

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

        # Twitch
        if hasattr(self.twitch_page, 'enable_twitch'):
            cfg('Twitch', 'enableTwitchBot', str(self.twitch_page.enable_twitch.isChecked()).lower())
            cfg('Twitch', 'twitchChannelName', self.twitch_page.twitch_channel.text().strip())
            cfg('Twitch', 'twitchBotToken', self.twitch_page.twitch_token.text().strip())
            cfg('Twitch', 'twitchUseTLS', str(self.twitch_page.twitch_use_tls.isChecked()).lower())

        # AI
        if hasattr(self.ai_page, 'enable_ai'):
            cfg('AI', 'enableAiAnalysis', str(self.ai_page.enable_ai.isChecked()))
            cfg('AI', 'aiProvider', self.ai_page.ai_provider.currentText())
            cfg('AI', 'aiBaseUrl', self.ai_page.ai_base_url.text().strip())
            cfg('AI', 'aiApiKey', self.ai_page.ai_api_key.text().strip())
            cfg('AI', 'aiModel', self.ai_page.ai_model.currentText().strip())

        # TTS
        if hasattr(self.tts_page, 'enable_tts'):
            cfg('TTS', 'enableTts', str(self.tts_page.enable_tts.isChecked()).lower())
            cfg('TTS', 'ttsDiscordAttach', str(self.tts_page.tts_discord_attach.isChecked()).lower())
            cfg('TTS', 'ttsProvider', self.tts_page.tts_provider.currentText())
            el_key = self.tts_page.tts_el_api_key.text().strip()
            if el_key:
                cfg('TTS', 'ttsElevenLabsApiKey', el_key)
            el_voice = self.tts_page.tts_el_voice_id.text().strip()
            if el_voice:
                cfg('TTS', 'ttsElevenLabsVoiceId', el_voice)

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
        self.install_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                font-size: 13px;
                border-radius: 5px;
                padding: 6px 16px;
            }
            QPushButton:pressed { background-color: #45a049; }
            QPushButton:disabled { background-color: #555; color: #aaa; }
        """)
        self.install_btn.clicked.connect(self._check_and_install)
        layout.addWidget(self.install_btn)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.details_label = QLabel("")
        self.details_label.setWordWrap(True)
        self.details_label.setStyleSheet("color: #aaa; font-size: 11px;")
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
            # Parse package name from requirement string (e.g., "PyQt6>=6.10.0" -> "PyQt6")
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
            self.status_label.setStyleSheet("color: #ffaa00;")
            self.status_label.setText("⚠ requirements.txt not found")
            return

        installed, missing = self._check_installed(requirements)

        if not missing:
            self.status_label.setStyleSheet("color: #4CAF50;")
            self.status_label.setText("✓ All dependencies are installed")
            self.details_label.setText("\n".join(f"  ✓ {p}" for p in installed))
            self.install_btn.setText("✓ All Dependencies Installed")
            self.install_btn.setEnabled(False)
            return

        if auto:
            # On auto-check, just show what's missing — don't install yet
            self.status_label.setStyleSheet("color: #ffaa00;")
            self.status_label.setText(
                f"⚠ {len(missing)} missing package(s) found. "
                f"Click the button to install them."
            )
            details = []
            for p in installed:
                details.append(f"  ✓ {p}")
            for p in missing:
                details.append(f"  ✗ {p} (missing)")
            self.details_label.setText("\n".join(details))
            return

        # Actually install missing packages
        self.status_label.setStyleSheet("color: #ffffff;")
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
                self.status_label.setStyleSheet("color: #4CAF50;")
                self.status_label.setText("✓ All dependencies installed successfully")
                # Re-check to update the details
                installed, still_missing = self._check_installed(requirements)
                details = [f"  ✓ {p}" for p in installed]
                if still_missing:
                    details += [f"  ✗ {p} (failed)" for p in still_missing]
                self.details_label.setText("\n".join(details))
                self.install_btn.setText("✓ All Dependencies Installed")
            else:
                self.status_label.setStyleSheet("color: #ff4444;")
                self.status_label.setText("✗ Installation failed")
                self.details_label.setText(result.stderr[:500] if result.stderr else result.stdout[:500])
                self.install_btn.setEnabled(True)

        except subprocess.TimeoutExpired:
            self.status_label.setStyleSheet("color: #ff4444;")
            self.status_label.setText("✗ Installation timed out after 120 seconds")
            self.install_btn.setEnabled(True)
        except Exception as e:
            self.status_label.setStyleSheet("color: #ff4444;")
            self.status_label.setText(f"✗ Error: {str(e)}")
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


class GW2EIPage(QWizardPage):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self._install_success = False
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

        self.download_btn = QPushButton("⬇  Download GW2 Elite Insights (Recommended)")
        self.download_btn.setMinimumHeight(36)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                font-size: 13px;
                border-radius: 5px;
                padding: 6px 16px;
            }
            QPushButton:pressed { background-color: #45a049; }
            QPushButton:disabled { background-color: #555; color: #aaa; }
        """)
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
        divider.setStyleSheet("background-color: #555;")
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
        adv_desc.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(adv_desc)

        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(
            "Optional: path to GuildWars2EliteInsights-CLI.exe"
        )
        # No prefill - do not expose user's personal folder structure
        browse_btn = QPushButton("Browse...")
        browse_btn.setObjectName("browseBtn")
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
        default_exe = Path(__file__).parent.parent / "GW2EI" / "GuildWars2EliteInsights-CLI.exe"

        if not default_exe.exists():
            self.download_btn.setText("⬇  Download GW2 Elite Insights (Recommended)")
            return

        # Installed - check version in background
        self._install_success = True
        self.download_status.setStyleSheet("color: #ffffff;")
        self.download_status.setText("✓ GW2EI is installed — checking for updates...")
        self.download_btn.setText("⬇  Update GW2 Elite Insights")

        import threading
        threading.Thread(target=self._check_version_worker, daemon=True).start()

    def _check_version_worker(self):
        from PyQt6.QtCore import QMetaObject, Q_ARG
        try:
            from core.ei_updater import EIUpdater
            from core.gw2ei_invoker import GW2EIInvoker
            invoker = GW2EIInvoker(self.config)
            updater = EIUpdater(invoker.get_gw2ei_folder())
            has_update, latest_version, _ = updater.check_for_update()
            current = updater.get_current_version() or "unknown"

            if has_update:
                msg = f"⬆ Update available: v{current} → v{latest_version}"
                btn = "⬇  Update GW2 Elite Insights"
            else:
                msg = f"✓ GW2EI v{current} is up to date"
                btn = "⬇  Re-download GW2 Elite Insights"

            QMetaObject.invokeMethod(
                self, "_set_version_status",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, msg),
                Q_ARG(str, btn)
            )
        except Exception as e:
            QMetaObject.invokeMethod(
                self, "_set_version_status",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, f"✓ GW2EI installed (could not check version: {e})"),
                Q_ARG(str, "⬇  Re-download GW2 Elite Insights")
            )

    @pyqtSlot(str, str)
    def _set_version_status(self, status: str, btn_text: str):
        self.download_status.setStyleSheet("color: #ffffff;")
        self.download_status.setText(status)
        self.download_btn.setText(btn_text)

    def _do_download(self):
        self.download_btn.setEnabled(False)
        self.download_btn.setText("Downloading...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.download_status.setStyleSheet("color: #ffffff;")
        self.download_status.setText("Connecting to GitHub...")

        import threading
        threading.Thread(target=self._download_worker, daemon=True).start()

    def _download_worker(self):
        from PyQt6.QtCore import QMetaObject, Q_ARG
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

            QMetaObject.invokeMethod(
                self, "_set_status",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, f"Downloading GW2EI v{latest_version}...")
            )

            def progress_cb(pct):
                QMetaObject.invokeMethod(
                    self.progress_bar, "setValue",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(int, int(pct))
                )

            success, message = updater.download_and_update(download_url, version=latest_version, progress_callback=progress_cb)

            QMetaObject.invokeMethod(
                self, "_on_download_complete",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(bool, success),
                Q_ARG(str, f"v{latest_version}" if success else message)
            )
        except Exception as e:
            QMetaObject.invokeMethod(
                self, "_on_download_complete",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(bool, False),
                Q_ARG(str, str(e))
            )

    @pyqtSlot(str)
    def _set_status(self, text: str):
        self.download_status.setText(text)

    @pyqtSlot(bool, str)
    def _on_download_complete(self, success: bool, message: str):
        self.progress_bar.setVisible(False)
        self.download_btn.setEnabled(True)
        if success:
            self._install_success = True
            self.download_status.setStyleSheet("color: #ffffff;")
            self.download_status.setText(f"✓ GW2EI {message} installed successfully")
            self.download_btn.setText("⬇  Re-download GW2 Elite Insights")
        else:
            self.download_status.setStyleSheet("color: #f44336;")
            self.download_status.setText(f"✗ Download failed: {message}")
            self.download_btn.setText("⬇  Download GW2 Elite Insights (Recommended)")

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
        self.download_status.setStyleSheet("color: #ffaa00;")
        self.download_status.setText(
            "⚠ GW2EI not found. Download it above or provide a valid path. "
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
                f"<small style='color:#aaa;'>WvW logs are saved in a numbered subfolder here. "
                f"Clicking the button below will select it automatically.</small>"
            )
            detected_label.setWordWrap(True)
            detected_label.setTextFormat(Qt.TextFormat.RichText)
            layout.addWidget(detected_label)

            use_default_btn = QPushButton("✓  Use Default Location (Recommended)")
            use_default_btn.setMinimumHeight(36)
            use_default_btn.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    font-weight: bold;
                    font-size: 12px;
                    border-radius: 5px;
                    padding: 6px 16px;
                }
                QPushButton:pressed { background-color: #45a049; }
            """)
            use_default_btn.clicked.connect(self._use_default)
            layout.addWidget(use_default_btn)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("background-color: #555;")
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
        browse_btn.setObjectName("browseBtn")
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
            self.status_label.setStyleSheet("color: #4CAF50;")
            self.status_label.setText(
                f"✓ WvW log folder found: {target}"
            )
        elif base.exists():
            self.status_label.setStyleSheet("color: #ffaa00;")
            self.status_label.setText(
                "⚠ Base folder found but no WvW subfolder yet. "
                "Play a WvW match first, then re-run setup — or browse manually."
            )
        else:
            self.status_label.setStyleSheet("color: #ffaa00;")
            self.status_label.setText(
                "⚠ Default folder does not exist yet. Install ArcDPS and "
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
            self.status_label.setStyleSheet("color: #ffaa00;")
            self.status_label.setText(
                "⚠ No folder selected. You can continue but the watcher "
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
        help_note.setStyleSheet("font-size: 11px; color: #aaa;")
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


class AIAnalysisPage(QWizardPage):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.setTitle("AI Fight Commentary (Optional)")

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
        quick_desc.setStyleSheet("color: #aaa;")
        layout.addWidget(quick_desc)

        layout.addSpacing(8)

        self.enable_ai = QCheckBox("Enable AI Fight Analysis")
        layout.addWidget(self.enable_ai)

        layout.addSpacing(6)

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

        self.ai_model = QComboBox()
        self.ai_model.setEditable(True)
        self.ai_model.setPlaceholderText("model name")
        layout.addLayout(_make_row("Model:", self.ai_model))

        layout.addSpacing(12)

        # Model fetch status (compact hint)
        self.model_status = QLabel("")
        self.model_status.setStyleSheet("font-size: 10px; color: #888;")
        layout.addWidget(self.model_status)

        layout.addSpacing(8)

        # Help links
        links_label = QLabel()
        links_label.setTextFormat(Qt.TextFormat.RichText)
        links_label.setOpenExternalLinks(True)
        links_label.setWordWrap(True)
        links_label.setStyleSheet("font-size: 11px; color: #aaa;")
        links_label.setText(
            "Google Gemini (free tier): <a href='https://aistudio.google.com/apikey'>Get API Key</a><br>"
            "OpenAI: <a href='https://platform.openai.com/api-keys'>Get API Key</a><br>"
            "Groq (free tier): <a href='https://console.groq.com/keys'>Get API Key</a><br>"
            "OpenRouter: <a href='https://openrouter.ai/keys'>Get API Key</a><br>"
            "Ollama (local, free): <a href='https://ollama.com'>Download Ollama</a> — no API key needed"
        )
        layout.addWidget(links_label)

        layout.addSpacing(10)

        # Test Connection button
        self.ai_test_btn = QPushButton("Test Connection")
        self.ai_test_btn.setStyleSheet("""
            QPushButton { background-color: #555; color: white; border-radius: 3px; padding: 6px 16px; }
            QPushButton:pressed { background-color: #666; }
            QPushButton:disabled { background-color: #444; color: #888; }
        """)
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

            from PyQt6.QtCore import QMetaObject, Qt, Q_ARG
            QMetaObject.invokeMethod(
                self, "_apply_models",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(int, generation),
                Q_ARG(list, models)
            )

        threading.Thread(target=_fetch, daemon=True).start()

    @pyqtSlot(int, list)
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
        base_url = self.ai_base_url.text().strip()
        api_key = self.ai_api_key.text().strip()
        model = self.ai_model.currentText().strip() if isinstance(self.ai_model, QComboBox) else self.ai_model.text().strip()

        if not base_url or not model:
            self.ai_test_status.setStyleSheet("color: #ffaa00;")
            self.ai_test_status.setText("Enter a Base URL and Model first.")
            return

        self.ai_test_btn.setEnabled(False)
        self.ai_test_status.setStyleSheet("color: #ffffff;")
        self.ai_test_status.setText("Testing...")

        import threading
        def _run():
            try:
                from core.ai_analyst import FightAnalyst
                analyst = FightAnalyst(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    max_tokens=350,
                )
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
                    },
                    "top_enemy_skills": [
                        {"name": "Meteor Shower", "damage": 120000},
                    ],
                    "enemy_teams": {"Red": 30, "Blue": 20},
                }
                result = analyst.analyze(test_summary, timeout=15)

                from PyQt6.QtCore import QMetaObject, Qt, Q_ARG
                if result:
                    preview = result[:150].replace('\n', ' ')
                    QMetaObject.invokeMethod(self, "_set_ai_test_result", Qt.ConnectionType.QueuedConnection,
                        Q_ARG(str, f"Success! Response: {preview}..."), Q_ARG(str, "#4CAF50"))
                else:
                    QMetaObject.invokeMethod(self, "_set_ai_test_result", Qt.ConnectionType.QueuedConnection,
                        Q_ARG(str, "No response. Check URL, API key, and model name."), Q_ARG(str, "#ff4444"))
            except Exception as e:
                from PyQt6.QtCore import QMetaObject, Qt, Q_ARG
                QMetaObject.invokeMethod(self, "_set_ai_test_result", Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, f"Error: {e}"), Q_ARG(str, "#ff4444"))

        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot(str, str)
    def _set_ai_test_result(self, text, color):
        self.ai_test_status.setStyleSheet(f"color: {color};")
        self.ai_test_status.setText(text)
        self.ai_test_btn.setEnabled(True)

    def validatePage(self):
        return True


class TTSVoicePage(QWizardPage):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.setTitle("Voice / Text-to-Speech (Optional)")

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
        self.tts_provider.addItems(["edge", "elevenlabs"])
        self.tts_provider.currentTextChanged.connect(self._on_provider_changed)
        layout.addLayout(_make_row("Provider:", self.tts_provider))

        provider_note = QLabel(
            "Edge: Free Microsoft neural voices, no API key needed (recommended). "
            "ElevenLabs: Premium quality voices, requires a paid API key."
        )
        provider_note.setWordWrap(True)
        provider_note.setStyleSheet("font-size: 11px; color: #aaa;")
        layout.addWidget(provider_note)

        layout.addSpacing(8)

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

        # Test Voice button
        self.tts_test_btn = QPushButton("Test Voice")
        self.tts_test_btn.setStyleSheet("""
            QPushButton { background-color: #555; color: white; border-radius: 3px; padding: 6px 16px; }
            QPushButton:pressed { background-color: #666; }
            QPushButton:disabled { background-color: #444; color: #888; }
        """)
        self.tts_test_btn.clicked.connect(self._test_tts)
        self.tts_test_status = QLabel("")
        self.tts_test_status.setWordWrap(True)
        layout.addWidget(self.tts_test_btn)
        layout.addWidget(self.tts_test_status)

        layout.addSpacing(10)

        # Help links
        edge_help = QLabel("Edge TTS is free and requires no setup — just enable and go.")
        edge_help.setWordWrap(True)
        edge_help.setStyleSheet("font-size: 11px; color: #aaa;")
        layout.addWidget(edge_help)

        elevenlabs_voices = QLabel("ElevenLabs: <a href='https://elevenlabs.io/app/voice-library'>Browse Voices</a>")
        elevenlabs_voices.setTextFormat(Qt.TextFormat.RichText)
        elevenlabs_voices.setOpenExternalLinks(True)
        elevenlabs_voices.setWordWrap(True)
        elevenlabs_voices.setStyleSheet("font-size: 11px; color: #aaa;")
        layout.addWidget(elevenlabs_voices)

        elevenlabs_api = QLabel("ElevenLabs: <a href='https://elevenlabs.io/app/settings/api-keys'>Get API Key</a>")
        elevenlabs_api.setTextFormat(Qt.TextFormat.RichText)
        elevenlabs_api.setOpenExternalLinks(True)
        elevenlabs_api.setWordWrap(True)
        elevenlabs_api.setStyleSheet("font-size: 11px; color: #aaa;")
        layout.addWidget(elevenlabs_api)

        note = QLabel(
            "Requires AI Fight Commentary to be enabled. TTS generates audio from the AI commentary text."
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 11px; color: #aaa;")
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
        from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
        self._audio_output = QAudioOutput()
        self._audio_output.setVolume(0.8)
        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._audio_output)
        self._temp_audio = None

        # Prefill from config
        if config.tts_provider:
            self.tts_provider.setCurrentText(config.tts_provider)
        if config.tts_elevenlabs_api_key:
            self.tts_el_api_key.setText(config.tts_elevenlabs_api_key)
        if config.tts_elevenlabs_voice_id:
            self.tts_el_voice_id.setText(config.tts_elevenlabs_voice_id)
        self._on_provider_changed(config.tts_provider or "edge")

    def _on_provider_changed(self, provider: str):
        is_el = provider.lower() == "elevenlabs"
        self.el_fields_widget.setVisible(is_el)

    def _test_tts(self):
        self.tts_test_btn.setEnabled(False)
        self.tts_test_status.setStyleSheet("color: #ffffff;")
        self.tts_test_status.setText("Generating test audio...")

        import threading
        def _run():
            try:
                from core.tts import generate_tts_bytes

                provider = self.tts_provider.currentText()

                class _Cfg:
                    tts_provider = provider
                    tts_edge_voice = "en-GB-RyanNeural"
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

                from PyQt6.QtCore import QMetaObject, Qt, Q_ARG
                if audio_bytes and self.enable_tts.isChecked():
                    import tempfile, os
                    fd, path = tempfile.mkstemp(suffix=".mp3", prefix="sparkybot_test_")
                    with os.fdopen(fd, "wb") as f:
                        f.write(audio_bytes)
                    QMetaObject.invokeMethod(self, "_play_audio", Qt.ConnectionType.QueuedConnection,
                        Q_ARG(str, path))
                    size_kb = len(audio_bytes) / 1024
                    QMetaObject.invokeMethod(self, "_set_tts_result", Qt.ConnectionType.QueuedConnection,
                        Q_ARG(str, f"Audio generated — playing through speakers."),
                        Q_ARG(str, "#4CAF50"))
                elif audio_bytes:
                    size_kb = len(audio_bytes) / 1024
                    QMetaObject.invokeMethod(self, "_set_tts_result", Qt.ConnectionType.QueuedConnection,
                        Q_ARG(str, f"Audio generated successfully ({size_kb:.1f} KB). Provider is working."),
                        Q_ARG(str, "#4CAF50"))
                else:
                    QMetaObject.invokeMethod(self, "_set_tts_result", Qt.ConnectionType.QueuedConnection,
                        Q_ARG(str, "Audio generation failed. Check provider settings and logs."),
                        Q_ARG(str, "#ff4444"))
            except Exception as e:
                from PyQt6.QtCore import QMetaObject, Qt, Q_ARG
                QMetaObject.invokeMethod(self, "_set_tts_result", Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, f"Error: {e}"), Q_ARG(str, "#ff4444"))

        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot(str, str)
    def _set_tts_result(self, text, color):
        self.tts_test_status.setStyleSheet(f"color: {color};")
        self.tts_test_status.setText(text)
        self.tts_test_btn.setEnabled(True)

    @pyqtSlot(str)
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
        self.start_watcher_on_startup.setStyleSheet("QCheckBox::indicator { width: 16px; height: 16px; }")
        self.start_watcher_on_startup.setChecked(config.start_watcher_on_startup)
        start_watcher_note = QLabel(
            "When enabled, SparkyBot begins monitoring your log folder immediately "
            "without needing to click Start Watcher."
        )
        start_watcher_note.setStyleSheet("font-size: 11px; color: #aaa;")
        layout.addWidget(self.start_watcher_on_startup)
        layout.addWidget(start_watcher_note)

        layout.addSpacing(12)

        # Start minimized
        self.start_minimized = QCheckBox("Start minimized to system tray")
        self.start_minimized.setStyleSheet("QCheckBox::indicator { width: 16px; height: 16px; }")
        self.start_minimized.setChecked(config.start_minimized)
        start_minimized_note = QLabel(
            "SparkyBot launches silently in the background. Access it from the system tray icon."
        )
        start_minimized_note.setStyleSheet("font-size: 11px; color: #aaa;")
        layout.addWidget(self.start_minimized)
        layout.addWidget(start_minimized_note)

        layout.addSpacing(12)

        # Close to tray
        self.close_to_tray = QCheckBox("Close to system tray instead of quitting")
        self.close_to_tray.setStyleSheet("QCheckBox::indicator { width: 16px; height: 16px; }")
        self.close_to_tray.setChecked(config.close_to_tray)
        close_note = QLabel(
            "Clicking the X button hides SparkyBot to the tray instead of exiting the application."
        )
        close_note.setStyleSheet("font-size: 11px; color: #aaa;")
        layout.addWidget(self.close_to_tray)
        layout.addWidget(close_note)

        layout.addSpacing(12)

        # Minimize to tray
        self.minimize_to_tray = QCheckBox("Minimize to system tray")
        self.minimize_to_tray.setStyleSheet("QCheckBox::indicator { width: 16px; height: 16px; }")
        self.minimize_to_tray.setChecked(config.minimize_to_tray)
        minimize_to_tray_note = QLabel(
            "When you click the minimize button, SparkyBot goes to the system tray instead of the taskbar."
        )
        minimize_to_tray_note.setStyleSheet("font-size: 10px; color: #888;")
        minimize_to_tray_note.setWordWrap(True)
        layout.addWidget(self.minimize_to_tray)
        layout.addWidget(minimize_to_tray_note)

        layout.addSpacing(12)

        # Check updates on launch
        self.check_updates_on_launch = QCheckBox("Check for updates on launch")
        self.check_updates_on_launch.setStyleSheet("QCheckBox::indicator { width: 16px; height: 16px; }")
        self.check_updates_on_launch.setChecked(config.check_updates_on_launch)
        check_updates_note = QLabel(
            "Automatically checks GitHub for new SparkyBot and Elite Insights versions at startup."
        )
        check_updates_note.setStyleSheet("font-size: 11px; color: #aaa;")
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