"""Main Settings Window for SparkyBot"""

import threading

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QLineEdit, QSpinBox, QCheckBox, QPushButton,
    QGroupBox, QFormLayout, QScrollArea,
    QComboBox, QFileDialog, QMessageBox, QProgressBar, QColorDialog
)
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QEvent
from pathlib import Path
from version import VERSION


class SettingsWindow(QWidget):
    """Main settings window with tabs for different configuration sections"""

    settings_changed = pyqtSignal()
    watcher_toggled = pyqtSignal()

    # Signals for thread-safe UI updates from background threads
    sig_status_text = pyqtSignal(str)
    sig_button_state = pyqtSignal(str, bool)
    sig_progress = pyqtSignal(bool, int)
    sig_progress_value = pyqtSignal(int)
    sig_ei_status_refresh = pyqtSignal()
    sig_ei_latest = pyqtSignal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("SparkyBot Settings")
        self.setMinimumSize(600, 500)

        # Set window icon to sbtray.png
        icon_path = Path(__file__).parent.parent / "sbtray.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._setup_ui()
        self._load_settings()
        self._connect_thread_signals()

    def _setup_ui(self):
        """Setup the user interface"""
        layout = QVBoxLayout(self)

        # Create tab widget
        tabs = QTabWidget()

        # Add tabs
        tabs.addTab(self._create_discord_tab(), "Discord")
        tabs.addTab(self._create_paths_tab(), "Paths")
        tabs.addTab(self._create_thresholds_tab(), "Thresholds")
        tabs.addTab(self._create_display_tab(), "Display")
        tabs.addTab(self._create_behavior_tab(), "Behavior")
        tabs.addTab(self._create_updates_tab(), "Updates")
        tabs.addTab(self._create_about_tab(), "About")

        layout.addWidget(tabs)

        # Bottom buttons
        button_layout = QHBoxLayout()

        self.start_button = QPushButton("Start Watcher")
        self.start_button.setMinimumHeight(40)
        self.start_button.setStyleSheet("""
            QPushButton { background-color: #4CAF50; color: white; font-weight: bold; border-radius: 5px; }
            QPushButton:pressed { background-color: #45a049; }
        """)
        self.start_button.clicked.connect(self._on_start_clicked)
        button_layout.addWidget(self.start_button)

        self.save_button = QPushButton("Save Settings")
        self.save_button.setMinimumHeight(40)
        self.save_button.clicked.connect(self._on_save_clicked)
        button_layout.addWidget(self.save_button)

        self.close_button = QPushButton("Close to Tray")
        self.close_button.setMinimumHeight(40)
        self.close_button.clicked.connect(self.hide)
        button_layout.addWidget(self.close_button)

        layout.addLayout(button_layout)

    def _create_discord_tab(self) -> QWidget:
        """Create Discord settings tab"""
        scroll = QScrollArea()
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Webhook settings
        group = QGroupBox("Discord Webhooks")
        form = QFormLayout(group)

        self.discord_webhook = QLineEdit()
        self.discord_webhook.setPlaceholderText("https://discord.com/api/webhooks/...")
        form.addRow("Primary Webhook:", self.discord_webhook)

        self.discord_webhook_label = QLineEdit()
        self.discord_webhook_label.setPlaceholderText("SparkyBot")
        form.addRow("Webhook Label:", self.discord_webhook_label)

        # Thumbnail icon file
        thumb_layout = QHBoxLayout()
        self.guild_icon = QLineEdit()
        self.guild_icon.setPlaceholderText("wvw_icon.png")
        browse_thumb_btn = QPushButton("Browse...")
        browse_thumb_btn.clicked.connect(self._browse_guild_icon)
        thumb_layout.addWidget(self.guild_icon)
        thumb_layout.addWidget(browse_thumb_btn)
        form.addRow("Guild Icon:", thumb_layout)

        # Embed color picker
        color_layout = QHBoxLayout()
        self.color_preview = QPushButton()
        self.color_preview.setFixedSize(40, 25)
        self._current_embed_color = QColor(
            (self.config.embed_color >> 16) & 0xFF,
            (self.config.embed_color >> 8) & 0xFF,
            self.config.embed_color & 0xFF,
        )
        self._update_color_preview()
        self.color_preview.clicked.connect(self._pick_embed_color)
        self.color_hex_label = QLabel(f"#{self.config.embed_color:06X}")
        color_layout.addWidget(QLabel("Embed Color:"))
        color_layout.addWidget(self.color_preview)
        color_layout.addWidget(self.color_hex_label)
        color_layout.addStretch()
        form.addRow("", color_layout)

        self.discord_webhook2 = QLineEdit()
        form.addRow("Secondary:", self.discord_webhook2)

        self.discord_webhook3 = QLineEdit()
        form.addRow("Tertiary:", self.discord_webhook3)

        self.active_webhook = QComboBox()
        self.active_webhook.addItems(["Primary", "Secondary", "Tertiary"])
        form.addRow("Active Webhook:", self.active_webhook)

        layout.addWidget(group)

        # Options
        options_group = QGroupBox("Options")
        options_layout = QVBoxLayout(options_group)

        self.enable_discord = QCheckBox("Enable Discord Bot")
        self.enable_discord.setChecked(True)
        options_layout.addWidget(self.enable_discord)

        layout.addWidget(options_group)
        layout.addStretch()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        return scroll

    def _browse_guild_icon(self):
        """Browse for the thumbnail/guild icon image."""
        from PyQt6.QtWidgets import QFileDialog
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Thumbnail Icon",
            self.guild_icon.text() or str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.gif *.webp)"
        )
        if file_path:
            app_dir = Path(__file__).parent.parent
            try:
                rel = Path(file_path).relative_to(app_dir)
                self.guild_icon.setText(str(rel.name))
            except ValueError:
                self.guild_icon.setText(file_path)

    def _update_color_preview(self):
        """Update the color preview button's background."""
        self.color_preview.setStyleSheet(
            f"background-color: {self._current_embed_color.name()}; "
            f"border: 1px solid #555; border-radius: 3px;"
        )

    def _pick_embed_color(self):
        """Open Qt color picker dialog."""
        color = QColorDialog.getColor(
            self._current_embed_color,
            self,
            "Select Embed Color",
        )
        if color.isValid():
            self._current_embed_color = color
            self._update_color_preview()
            self.color_hex_label.setText(color.name().upper())

    def _create_paths_tab(self) -> QWidget:
        """Create paths settings tab"""
        scroll = QScrollArea()
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Log folder
        group = QGroupBox("Log Folder")
        form = QFormLayout(group)

        folder_layout = QHBoxLayout()
        self.log_folder = QLineEdit()
        self.log_folder.setPlaceholderText("Z:\\Logs\\arcdps.cbtlogs\\1")
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(lambda: self._browse_folder(self.log_folder))
        folder_layout.addWidget(self.log_folder)
        folder_layout.addWidget(browse_btn)
        form.addRow("Folder:", folder_layout)

        layout.addWidget(group)

        # GW2EI
        ei_group = QGroupBox("GW2EI Parser")
        ei_form = QFormLayout(ei_group)

        ei_layout = QHBoxLayout()
        self.gw2ei_exe = QLineEdit()
        self.gw2ei_exe.setPlaceholderText("GuildWars2EliteInsights-CLI.exe")
        browse_ei_btn = QPushButton("Browse...")
        browse_ei_btn.clicked.connect(self._browse_gw2ei_exe)
        ei_layout.addWidget(self.gw2ei_exe)
        ei_layout.addWidget(browse_ei_btn)
        ei_form.addRow("Executable:", ei_layout)

        layout.addWidget(ei_group)
        layout.addStretch()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        return scroll

    def _create_thresholds_tab(self) -> QWidget:
        """Create thresholds settings tab"""
        scroll = QScrollArea()
        widget = QWidget()
        layout = QVBoxLayout(widget)

        group = QGroupBox("Fight Thresholds")
        form = QFormLayout(group)

        self.min_duration = QSpinBox()
        self.min_duration.setRange(0, 600)
        self.min_duration.setSuffix(" seconds")
        form.addRow("Min Fight Duration:", self.min_duration)

        self.min_downs = QSpinBox()
        self.min_downs.setRange(0, 100)
        form.addRow("Min Downs:", self.min_downs)

        self.min_damage = QSpinBox()
        self.min_damage.setRange(0, 10000000)
        self.min_damage.setSingleStep(10000)
        form.addRow("Min Total Damage:", self.min_damage)

        layout.addWidget(group)

        # Upload thresholds
        upload_group = QGroupBox("Upload Settings")
        upload_form = QFormLayout(upload_group)

        self.max_upload = QSpinBox()
        self.max_upload.setRange(1, 999)
        self.max_upload.setSuffix(" MB")
        upload_form.addRow("Max Upload Size:", self.max_upload)

        self.large_upload_after = QCheckBox("Upload large files after parsing")
        upload_form.addRow("", self.large_upload_after)

        layout.addWidget(upload_group)
        layout.addStretch()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        return scroll

    def _create_display_tab(self) -> QWidget:
        """Create display options tab"""
        scroll = QScrollArea()
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Combat stats
        combat_group = QGroupBox("Combat Statistics")
        combat_grid = QVBoxLayout(combat_group)

        self.show_damage = QCheckBox("Show Damage")
        self.show_damage.setChecked(True)
        combat_grid.addWidget(self.show_damage)

        self.show_heals = QCheckBox("Show Heals")
        combat_grid.addWidget(self.show_heals)

        self.show_defense = QCheckBox("Show Defense")
        combat_grid.addWidget(self.show_defense)

        self.show_ccs = QCheckBox("Show Crowd Control")
        combat_grid.addWidget(self.show_ccs)

        self.show_cleanses = QCheckBox("Show Cleanses")
        combat_grid.addWidget(self.show_cleanses)

        self.show_downs = QCheckBox("Show Downs/Kills")
        combat_grid.addWidget(self.show_downs)

        layout.addWidget(combat_group)

        # Advanced display
        adv_group = QGroupBox("Advanced Display")
        adv_grid = QVBoxLayout(adv_group)

        self.show_burst = QCheckBox("Show Burst Damage")
        adv_grid.addWidget(self.show_burst)

        self.show_spike = QCheckBox("Show Spike Damage")
        adv_grid.addWidget(self.show_spike)

        self.show_top_skills = QCheckBox("Show Top Enemy Skills")
        adv_grid.addWidget(self.show_top_skills)

        self.show_offensive_boons = QCheckBox("Show Offensive Boons")
        adv_grid.addWidget(self.show_offensive_boons)

        self.show_defensive_boons = QCheckBox("Show Defensive Boons")
        adv_grid.addWidget(self.show_defensive_boons)

        self.show_enemy_breakdown = QCheckBox("Show Enemy Breakdown")
        adv_grid.addWidget(self.show_enemy_breakdown)

        layout.addWidget(adv_group)
        layout.addStretch()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        return scroll

    def _create_behavior_tab(self) -> QWidget:
        """Create behavior settings tab"""
        scroll = QScrollArea()
        widget = QWidget()
        layout = QVBoxLayout(widget)

        group = QGroupBox("Window Behavior")
        grid = QVBoxLayout(group)

        self.close_to_tray = QCheckBox("Close to System Tray")
        grid.addWidget(self.close_to_tray)

        self.minimize_to_tray = QCheckBox("Minimize to System Tray")
        grid.addWidget(self.minimize_to_tray)

        self.start_minimized = QCheckBox("Start Minimized")
        grid.addWidget(self.start_minimized)

        self.show_quick_report = QCheckBox("Show Quick Report")
        grid.addWidget(self.show_quick_report)

        self.start_watcher_on_startup = QCheckBox("Start Watcher on Startup")
        grid.addWidget(self.start_watcher_on_startup)

        layout.addWidget(group)

        # Memory
        memory_group = QGroupBox("Memory")
        memory_form = QFormLayout(memory_group)

        self.max_parse_memory = QSpinBox()
        self.max_parse_memory.setRange(512, 16384)
        self.max_parse_memory.setSingleStep(256)
        self.max_parse_memory.setSuffix(" MB")
        memory_form.addRow("Max Parse Memory:", self.max_parse_memory)

        layout.addWidget(memory_group)
        layout.addStretch()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        return scroll

    def _create_updates_tab(self) -> QWidget:
        """Create updates tab for Elite Insights"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Elite Insights section
        ei_group = QGroupBox("Elite Insights Parser")
        ei_layout = QVBoxLayout(ei_group)

        # Info label
        self.ei_status_label = QLabel("Checking...")
        ei_layout.addWidget(self.ei_status_label)

        # Version comparison layout
        version_grid = QFormLayout()

        self.ei_installed_label = QLabel("Not installed")
        version_grid.addRow("Installed Version:", self.ei_installed_label)

        self.ei_latest_label = QLabel("Checking...")
        self.ei_latest_label.setText('<a href="https://github.com/baaron4/GW2-Elite-Insights-Parser/releases">Checking GitHub...</a>')
        self.ei_latest_label.setOpenExternalLinks(True)
        version_grid.addRow("Latest Version:", self.ei_latest_label)

        ei_layout.addLayout(version_grid)

        # Update button
        self.update_ei_button = QPushButton("Check for Elite Insights Update")
        self.update_ei_button.setMinimumHeight(40)
        self.update_ei_button.clicked.connect(self._on_update_ei_clicked)
        ei_layout.addWidget(self.update_ei_button)

        # Progress bar
        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(0)
        self.update_progress.setVisible(False)
        ei_layout.addWidget(self.update_progress)

        # Status text
        self.update_status_label = QLabel("")
        self.update_status_label.setWordWrap(True)
        ei_layout.addWidget(self.update_status_label)

        layout.addWidget(ei_group)

        # Note about config preservation
        note_group = QGroupBox("Note")
        note_layout = QVBoxLayout(note_group)
        note = QLabel(
            "The update process preserves all Settings files (wvwupload.conf, etc.).<br>"
            "Only the parser executable and related files are updated."
        )
        note.setWordWrap(True)
        note_layout.addWidget(note)
        layout.addWidget(note_group)

        layout.addStretch()

        # Check initial status
        QTimer.singleShot(100, self._check_ei_status)

        return widget

    def _check_ei_status(self):
        """Check current EI status and latest version from GitHub"""
        try:
            from core.ei_updater import EIUpdater
            from core.gw2ei_invoker import GW2EIInvoker

            invoker = GW2EIInvoker(self.config)
            updater = EIUpdater(invoker.get_gw2ei_folder())
            info = updater.get_current_info()

            if info["exists"]:
                if info["has_cli"]:
                    current_version = updater.get_current_version()
                    self.ei_installed_label.setText(current_version if current_version else "Installed")
                    self.ei_status_label.setText("Elite Insights is installed in GW2EI folder")
                else:
                    self.ei_installed_label.setText("Missing CLI")
                    self.ei_status_label.setText("GuildWars2EliteInsights-CLI.exe not found")
            else:
                self.ei_installed_label.setText("Not found")
                self.ei_status_label.setText("GW2EI folder not found - Elite Insights not installed")

            if info["has_settings"]:
                self.ei_status_label.setText(self.ei_status_label.text() + " | Settings preserved")

            # Also fetch latest version from GitHub in background
            thread = threading.Thread(target=self._fetch_latest_ei_version, daemon=True)
            thread.start()

        except Exception as e:
            self.ei_status_label.setText(f"Error checking status: {e}")

    def _fetch_latest_ei_version(self):
        """Fetch latest EI version from GitHub API (runs on background thread)"""
        try:
            import requests
            response = requests.get(
                "https://api.github.com/repos/baaron4/GW2-Elite-Insights-Parser/releases/latest",
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                latest_version = data.get("tag_name", "").lstrip("v")
                text = f'<a href="https://github.com/baaron4/GW2-Elite-Insights-Parser/releases">v{latest_version}</a>'
                self.sig_ei_latest.emit(text)
            else:
                self.sig_ei_latest.emit("Unable to fetch")
        except Exception:
            self.sig_ei_latest.emit("Unable to fetch")

    def _on_update_ei_clicked(self):
        """Handle update button click"""
        self.update_ei_button.setEnabled(False)
        self.update_ei_button.setText("Checking...")
        self.update_status_label.setText("Connecting to GitHub...")

        # Run update check in thread
        thread = threading.Thread(target=self._do_ei_update_check)
        thread.daemon = True
        thread.start()

    def _do_ei_update_check(self):
        """Background thread for update check and download"""
        try:
            from core.ei_updater import EIUpdater
            from core.gw2ei_invoker import GW2EIInvoker

            invoker = GW2EIInvoker(self.config)
            updater = EIUpdater(invoker.get_gw2ei_folder())

            # Check for update
            self.sig_status_text.emit("Checking GitHub for updates...")

            has_update, latest_version, download_url = updater.check_for_update()

            current_version = updater.get_current_version()

            if not has_update:
                if current_version:
                    self.sig_status_text.emit(f"You have the latest Elite Insights (v{current_version}).")
                else:
                    self.sig_status_text.emit("You have the latest Elite Insights.")
                self.sig_button_state.emit("Already Up to Date", True)
                return

            # Update available
            current_str = f"v{current_version}" if current_version else "installed"
            self.sig_status_text.emit(f"Update available: {current_str} → v{latest_version}")
            self.sig_button_state.emit("Downloading...", True)
            self.sig_progress.emit(True, 0)

            def progress_callback(pct):
                self.sig_progress_value.emit(int(pct))

            success, message = updater.download_and_update(download_url, progress_callback)

            self.sig_progress.emit(False, 0)
            self.sig_status_text.emit(message)
            self.sig_button_state.emit("Update Complete" if success else "Update Failed", True)

            if success:
                self.sig_ei_status_refresh.emit()

        except Exception as e:
            self.sig_status_text.emit(f"Error: {e}")
            self.sig_button_state.emit("Check for Updates", True)

    def _connect_thread_signals(self):
        """Connect cross-thread signals to their UI slot handlers"""
        self.sig_status_text.connect(
            lambda t: self.update_status_label.setText(t)
        )
        self.sig_button_state.connect(
            lambda t, e: (self.update_ei_button.setText(t), self.update_ei_button.setEnabled(e))
        )
        self.sig_progress.connect(
            lambda v, val: (self.update_progress.setVisible(v), self.update_progress.setValue(val))
        )
        self.sig_progress_value.connect(
            lambda val: self.update_progress.setValue(val)
        )
        self.sig_ei_status_refresh.connect(self._check_ei_status)
        self.sig_ei_latest.connect(
            lambda t: self.ei_latest_label.setText(t)
        )

    def _create_about_tab(self) -> QWidget:
        """Create about tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        title = QLabel("<h1>SparkyBot</h1>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        version = QLabel(f"Version {VERSION}")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)

        desc = QLabel(
            "Guild Wars 2 Fight Log Reporter<br>"
            "Monitors ArcDPS logs and sends WvW fight reports to Discord.<br><br>"
            "Python port with efficient OS-native file watching."
        )
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addStretch()

        # Core inspiration section
        inspired_label = QLabel("<b>Built on the shoulders of giants:</b>")
        inspired_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(inspired_label)

        layout.addSpacing(5)

        # MzFightReporter credit
        mz_link = QLabel(
            '<a href="https://github.com/Swedemon/MzFightReporter">'
            '<b>MzFightReporter</b></a> by Swedemon<br>'
            '<small>The original C# application that inspired this project</small>'
        )
        mz_link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mz_link.setTextFormat(Qt.TextFormat.RichText)
        mz_link.setOpenExternalLinks(True)
        layout.addWidget(mz_link)

        layout.addSpacing(5)

        # Elite Insights credit
        ei_link = QLabel(
            '<a href="https://github.com/baaron4/GW2-Elite-Insights-Parser">'
            '<b>GW2 Elite Insights</b></a> by baaron4<br>'
            '<small>The parser that powers all log analysis</small>'
        )
        ei_link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ei_link.setTextFormat(Qt.TextFormat.RichText)
        ei_link.setOpenExternalLinks(True)
        layout.addWidget(ei_link)

        layout.addStretch()

        credits = QLabel(
            "<small>SparkyBot is a community-built alternative for users who prefer "
            "a Python-based solution with a focus on reliability and ease of deployment.</small>"
        )
        credits.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credits.setWordWrap(True)
        layout.addWidget(credits)

        return widget

    def _browse_folder(self, line_edit: QLineEdit):
        """Open folder browser dialog"""
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder", line_edit.text() or str(Path.home())
        )
        if folder:
            line_edit.setText(folder)

    def _browse_gw2ei_exe(self):
        """Browse for GW2EI CLI executable"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select GW2EI CLI Executable",
            self.gw2ei_exe.text() or str(Path(__file__).parent.parent / "GW2EI"),
            "Executables (*.exe)"
        )
        if file_path:
            app_dir = Path(__file__).parent.parent
            try:
                rel = Path(file_path).relative_to(app_dir)
                self.gw2ei_exe.setText(str(rel))
            except ValueError:
                self.gw2ei_exe.setText(file_path)

    def _load_settings(self):
        """Load settings from config into UI"""
        # Discord
        self.discord_webhook.setText(self.config.discord_webhook)
        self.discord_webhook_label.setText(self.config.discord_webhook_label)
        self.discord_webhook2.setText(self.config.discord_webhook2)
        self.discord_webhook3.setText(self.config.discord_webhook3)
        self.active_webhook.setCurrentIndex(max(0, self.config.active_discord_webhook - 1))
        self.enable_discord.setChecked(self.config.enable_discord_bot)
        self.guild_icon.setText(self.config.guild_icon)
        self._current_embed_color = QColor(
            (self.config.embed_color >> 16) & 0xFF,
            (self.config.embed_color >> 8) & 0xFF,
            self.config.embed_color & 0xFF,
        )
        self._update_color_preview()
        self.color_hex_label.setText(f"#{self.config.embed_color:06X}")

        # Paths
        self.log_folder.setText(self.config.log_folder)
        self.gw2ei_exe.setText(self.config.gw2ei_exe)

        # Thresholds
        self.min_duration.setValue(self.config.min_fight_duration)
        self.min_downs.setValue(self.config.min_fight_downs)
        self.min_damage.setValue(self.config.min_fight_total_dmg)
        self.max_upload.setValue(self.config.max_upload_size)
        self.large_upload_after.setChecked(self.config.upload_large_after_parse)

        # Display
        self.show_quick_report.setChecked(self.config.show_quick_report)
        self.show_damage.setChecked(self.config.show_damage)
        self.show_heals.setChecked(self.config.show_heals)
        self.show_defense.setChecked(self.config.show_defense)
        self.show_ccs.setChecked(self.config.show_ccs)
        self.show_cleanses.setChecked(self.config.show_cleanses)
        self.show_downs.setChecked(self.config.show_downs_kills)
        self.show_burst.setChecked(self.config.show_burst_dmg)
        self.show_spike.setChecked(self.config.show_spike_dmg)
        self.show_top_skills.setChecked(self.config.show_top_enemy_skills)
        self.show_offensive_boons.setChecked(self.config.show_offensive_boons)
        self.show_defensive_boons.setChecked(self.config.show_defensive_boons)
        self.show_enemy_breakdown.setChecked(self.config.show_enemy_breakdown)

        # Behavior
        self.close_to_tray.setChecked(self.config.close_to_tray)
        self.minimize_to_tray.setChecked(self.config.minimize_to_tray)
        self.start_minimized.setChecked(self.config.start_minimized)
        self.start_watcher_on_startup.setChecked(self.config.start_watcher_on_startup)

        # Memory
        self.max_parse_memory.setValue(self.config.max_parse_memory)

    def _on_save_clicked(self):
        """Save settings from UI to config"""
        cfg = self.config.update
        cfg('Discord', 'discordWebhook', self.discord_webhook.text())
        cfg('Discord', 'discordWebhookLabel', self.discord_webhook_label.text())
        cfg('Discord', 'discordWebhook2', self.discord_webhook2.text())
        cfg('Discord', 'discordWebhook3', self.discord_webhook3.text())
        cfg('Discord', 'activeDiscordWebhook', str(self.active_webhook.currentIndex() + 1))
        cfg('Discord', 'enableDiscordBot', str(self.enable_discord.isChecked()))
        cfg('Discord', 'guildIcon', self.guild_icon.text())
        c = self._current_embed_color
        cfg('Discord', 'embedColor', hex((c.red() << 16) | (c.green() << 8) | c.blue()))

        # Paths
        cfg('Paths', 'logFolder', self.log_folder.text())
        cfg('Paths', 'gw2eiExe', self.gw2ei_exe.text())

        # Thresholds
        cfg('Thresholds', 'minFightDuration', str(self.min_duration.value()))
        cfg('Thresholds', 'minFightDowns', str(self.min_downs.value()))
        cfg('Thresholds', 'minFightTotalDmg', str(self.min_damage.value()))
        cfg('Thresholds', 'maxUploadSize', str(self.max_upload.value()))
        cfg('Thresholds', 'uploadLargeAfterParse', str(self.large_upload_after.isChecked()))

        # Display settings
        cfg('UI', 'showQuickReport', str(self.show_quick_report.isChecked()))
        cfg('UI', 'showDamage', str(self.show_damage.isChecked()))
        cfg('UI', 'showHeals', str(self.show_heals.isChecked()))
        cfg('UI', 'showDefense', str(self.show_defense.isChecked()))
        cfg('UI', 'showCCs', str(self.show_ccs.isChecked()))
        cfg('UI', 'showCleanses', str(self.show_cleanses.isChecked()))
        cfg('UI', 'showDownsKills', str(self.show_downs.isChecked()))
        cfg('UI', 'showBurstDmg', str(self.show_burst.isChecked()))
        cfg('UI', 'showSpikeDmg', str(self.show_spike.isChecked()))
        cfg('UI', 'showTopEnemySkills', str(self.show_top_skills.isChecked()))
        cfg('UI', 'showOffensiveBoons', str(self.show_offensive_boons.isChecked()))
        cfg('UI', 'showDefensiveBoons', str(self.show_defensive_boons.isChecked()))
        cfg('UI', 'showEnemyBreakdown', str(self.show_enemy_breakdown.isChecked()))

        # Behavior
        cfg('Behavior', 'closeToTray', str(self.close_to_tray.isChecked()))
        cfg('Behavior', 'minimizeToTray', str(self.minimize_to_tray.isChecked()))
        cfg('Behavior', 'startMinimized', str(self.start_minimized.isChecked()))
        cfg('Behavior', 'startWatcherOnStartup', str(self.start_watcher_on_startup.isChecked()))
        cfg('Behavior', 'maxParseMemory', str(self.max_parse_memory.value()))

        # Write to file and reload attributes
        if self.config.save():
            self.settings_changed.emit()
            QMessageBox.information(self, "Settings", "Settings saved successfully!")
        else:
            QMessageBox.warning(self, "Settings", "Failed to save settings.")

    def _on_start_clicked(self):
        """Toggle watcher start/stop"""
        self.watcher_toggled.emit()

    def set_watcher_state(self, running: bool):
        """Update UI to reflect watcher state"""
        if running:
            self.start_button.setText("Stop Watcher")
            self.start_button.setStyleSheet("""
                QPushButton { background-color: #f44336; color: white; font-weight: bold; border-radius: 5px; }
                QPushButton:pressed { background-color: #da190b; }
            """)
        else:
            self.start_button.setText("Start Watcher")
            self.start_button.setStyleSheet("""
                QPushButton { background-color: #4CAF50; color: white; font-weight: bold; border-radius: 5px; }
                QPushButton:pressed { background-color: #45a049; }
            """)

    def closeEvent(self, event):
        """Handle window close button - minimize to tray or quit based on config"""
        if self.config.close_to_tray:
            event.ignore()
            self.hide()
        else:
            event.accept()

    def changeEvent(self, event):
        """Handle window state changes - minimize to tray if configured"""
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized() and self.config.minimize_to_tray:
                event.ignore()
                self.hide()
                return
        super().changeEvent(event)
