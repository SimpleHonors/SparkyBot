"""First-run setup wizard for SparkyBot"""

import subprocess
import sys
import importlib.metadata
from PyQt6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog, QCheckBox, QProgressBar, QFrame
)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QColor, QPalette, QIcon
from pathlib import Path


class SetupWizard(QWizard):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("SparkyBot Setup")
        self.setMinimumSize(600, 400)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        # Set window icon to sbtray.ico
        icon_path = Path(__file__).parent.parent / "sbtray.ico"
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
            "<li>A Discord webhook URL</li>"
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

            success, message = updater.download_and_update(download_url, progress_cb)

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
