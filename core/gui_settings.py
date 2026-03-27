"""Main Settings Window for SparkyBot"""

import sys
import hashlib
import threading

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QTabWidget,
    QLabel, QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QPushButton,
    QGroupBox, QFormLayout, QScrollArea, QSizePolicy,
    QComboBox, QFileDialog, QMessageBox, QProgressBar, QColorDialog,
    QTextEdit, QDialog, QDialogButtonBox, QListWidget, QListWidgetItem
)
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QEvent
from pathlib import Path
from version import VERSION


def _parse_version(version_str: str) -> tuple:
    """Parse version string like 'v1.5' or '1.12.3' into a comparable tuple of ints."""
    clean = version_str.strip().lstrip('v')
    parts = []
    for part in clean.split('.'):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


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
    sig_sparkybot_status = pyqtSignal(str)
    sig_sparkybot_button_state = pyqtSignal(str, bool)
    sig_sparkybot_latest = pyqtSignal(str)
    sig_update_complete = pyqtSignal(str)  # version string

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("SparkyBot Settings")
        self.setMinimumSize(600, 500)  # height only; width computed after tabs are added

        # Set window icon to sbtray.ico
        icon_path = Path(__file__).parent.parent / "assets" / "sbtray.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._setup_ui()
        self._load_settings()
        self._connect_thread_signals()

        # Dynamically size window to fit all tabs without scrolling
        tab_bar = self.tab_widget.tabBar()
        tabs_width = sum(tab_bar.tabRect(i).width() for i in range(tab_bar.count()))
        min_width = tabs_width + 40
        self.setMinimumWidth(min_width)
        self.resize(max(min_width, self.width()), self.height())

    def _setup_ui(self):
        """Setup the user interface"""
        layout = QVBoxLayout(self)

        # Create tab widget
        self.tab_widget = QTabWidget()
        tabs = self.tab_widget

        # Add tabs
        tabs.addTab(self._create_messaging_tab(), "Messaging")
        tabs.addTab(self._create_paths_tab(), "Paths")
        tabs.addTab(self._create_thresholds_tab(), "Thresholds")
        tabs.addTab(self._create_display_tab(), "Display")
        tabs.addTab(self._create_behavior_tab(), "Behavior")
        tabs.addTab(self._create_updates_tab(), "Updates")
        tabs.addTab(self._create_ai_tab(), "AI")
        tabs.addTab(self._create_tts_tab(), "TTS")
        tabs.addTab(self._create_process_files_tab(), "Process Files")
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

    def _create_messaging_tab(self) -> QWidget:
        """Create Messaging settings tab (Discord + Twitch)"""
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
        self.guild_icon.setPlaceholderText("assets/wvw_icon.png")
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

        # Twitch Integration group box
        twitch_group = QGroupBox("Twitch Integration")
        twitch_layout = QFormLayout(twitch_group)

        self.enable_twitch = QCheckBox("Enable Twitch Bot")
        self.enable_twitch.setToolTip("Post fight summaries and AI commentary to a Twitch chat channel.")
        twitch_layout.addRow("", self.enable_twitch)

        self.twitch_channel = QLineEdit()
        self.twitch_channel.setPlaceholderText("your_channel_name")
        self.twitch_channel.setToolTip("The Twitch channel name to post messages to.")
        twitch_layout.addRow("Channel Name:", self.twitch_channel)

        self.twitch_token = QLineEdit()
        self.twitch_token.setPlaceholderText("oauth:...")
        self.twitch_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.twitch_token.setToolTip("OAuth token for the Twitch bot account.")
        twitch_layout.addRow("Bot Token:", self.twitch_token)

        self.twitch_use_tls = QCheckBox("Use secure connection (TLS)")
        self.twitch_use_tls.setChecked(True)
        self.twitch_use_tls.setToolTip(
            "Encrypts your OAuth token in transit. Disable only if you have connection issues."
        )
        twitch_layout.addRow("", self.twitch_use_tls)

        twitch_tls_note = QLabel(
            "⚠ Disabling TLS sends your OAuth token in plaintext over port 6667. "
            "Only disable this if TLS connections fail due to firewall or network restrictions."
        )
        twitch_tls_note.setWordWrap(True)
        twitch_tls_note.setStyleSheet("font-size: 10px; color: #888; padding-left: 4px;")
        twitch_layout.addRow("", twitch_tls_note)

        twitch_help = QLabel(
            'Get a token at <a href="https://twitchtokengenerator.com" style="color: #5bc0de;">twitchtokengenerator.com</a>'
        )
        twitch_help.setOpenExternalLinks(True)
        twitch_help.setStyleSheet("font-size: 11px; color: #aaa;")
        twitch_layout.addRow("", twitch_help)

        self.twitch_test_btn = QPushButton("Test Connection")
        self.twitch_test_btn.clicked.connect(self._test_twitch_connection)
        self.twitch_test_status = QLabel("")
        self.twitch_test_status.setWordWrap(True)
        twitch_layout.addRow("", self.twitch_test_btn)
        twitch_layout.addRow("", self.twitch_test_status)

        layout.addWidget(twitch_group)
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

    def _test_twitch_connection(self):
        """Test Twitch IRC connection."""
        channel = self.twitch_channel.text().strip()
        token = self.twitch_token.text().strip()

        if not channel or not token:
            self.twitch_test_status.setText("Enter a channel name and bot token first.")
            return

        self.twitch_test_status.setText("Connecting...")
        self.twitch_test_btn.setEnabled(False)

        import threading
        def _test():
            try:
                from core.twitch_bot import TwitchBot
                bot = TwitchBot(token, channel, use_tls=self.twitch_use_tls.isChecked())
                bot.send_message("SparkyBot Twitch connection test — if you see this, it works!")
                bot.close()
                self.twitch_test_status.setText("✓ Message sent successfully!")
            except Exception as e:
                self.twitch_test_status.setText(f"✗ Connection failed: {e}")
            finally:
                self.twitch_test_btn.setEnabled(True)

        threading.Thread(target=_test, daemon=True).start()

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

    def _create_paths_tab(self) -> QWidget:
        """Create paths configuration tab"""
        scroll = QScrollArea()
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Logs folder
        group = QGroupBox("Logging")
        form = QFormLayout(group)

        log_layout = QHBoxLayout()
        self.log_folder = QLineEdit()
        self.log_folder.setPlaceholderText("Path to GW2 logs folder")
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(lambda: self._browse_folder(self.log_folder))
        log_layout.addWidget(self.log_folder)
        log_layout.addWidget(browse_btn)
        form.addRow("Log Folder:", log_layout)

        layout.addWidget(group)

        # GW2EI settings
        group = QGroupBox("Elite Insights")
        form = QFormLayout(group)

        gw2ei_layout = QHBoxLayout()
        self.gw2ei_exe = QLineEdit()
        self.gw2ei_exe.setPlaceholderText("Path to GuildWars2EliteInsights-CLI.exe")
        browse_gw2ei = QPushButton("Browse...")
        browse_gw2ei.clicked.connect(self._browse_gw2ei_exe)
        gw2ei_layout.addWidget(self.gw2ei_exe)
        gw2ei_layout.addWidget(browse_gw2ei)
        form.addRow("CLI Executable:", gw2ei_layout)

        layout.addWidget(group)

        # Network polling
        group = QGroupBox("Network")
        poll_form = QFormLayout(group)

        self.poll_interval = QSpinBox()
        self.poll_interval.setRange(1, 30)
        self.poll_interval.setSuffix(" seconds")
        self.poll_interval.setValue(5)
        self.poll_interval.setToolTip(
            "How often to check for new files when watching a network share.\n"
            "Does not affect local folder monitoring (which uses instant OS events)."
        )
        poll_form.addRow("Network Poll Interval:", self.poll_interval)

        layout.addWidget(group)
        layout.addStretch()

        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        return scroll

    def _create_thresholds_tab(self) -> QWidget:
        """Create thresholds configuration tab"""
        scroll = QScrollArea()
        widget = QWidget()
        layout = QVBoxLayout(widget)

        group = QGroupBox("Fight Thresholds")
        form = QFormLayout(group)

        self.min_duration = QSpinBox()
        self.min_duration.setRange(1, 3600)
        self.min_duration.setSingleStep(1)
        self.min_duration.setSuffix(" seconds")
        form.addRow("Min Fight Duration:", self.min_duration)

        self.min_downs = QSpinBox()
        self.min_downs.setRange(0, 10)
        form.addRow("Min Fight Downs:", self.min_downs)

        self.min_damage = QSpinBox()
        self.min_damage.setRange(0, 9999999)
        self.min_damage.setSingleStep(10000)
        form.addRow("Min Fight Total DMG:", self.min_damage)

        self.max_upload = QSpinBox()
        self.max_upload.setRange(1, 1024)
        self.max_upload.setSingleStep(1)
        self.max_upload.setSuffix(" MB")
        form.addRow("Max Upload Size:", self.max_upload)

        self.large_upload_after = QCheckBox("Upload Large Files After Parsing")
        form.addRow("", self.large_upload_after)

        layout.addWidget(group)
        layout.addStretch()

        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        return scroll

    def _create_display_tab(self) -> QWidget:
        """Create display configuration tab"""
        scroll = QScrollArea()
        widget = QWidget()
        layout = QVBoxLayout(widget)

        group = QGroupBox("Report Display Options")
        grid = QGridLayout(group)

        checkboxes = [
            ("show_quick_report", "Show Quick Report"),
            ("show_damage", "Show Damage Stats"),
            ("show_heals", "Show Heals"),
            ("show_defense", "Show Defense"),
            ("show_ccs", "Show Crowd Control"),
            ("show_strips", "Show Strips"),
            ("show_cleanses", "Show Cleanses"),
            ("show_downs", "Show Downs/Kills"),
            ("show_burst", "Show Burst Damage"),
            ("show_top_skills", "Show Top Enemy Skills"),
            ("show_offensive_boons", "Show Offensive Boons"),
            ("show_defensive_boons", "Show Defensive Boons"),
            ("show_enemy_breakdown", "Show Enemy Breakdown"),
        ]

        for i, (attr, label) in enumerate(checkboxes):
            cb = QCheckBox(label)
            setattr(self, attr, cb)
            row = i // 2
            col = i % 2
            grid.addWidget(cb, row, col)

        layout.addWidget(group)
        layout.addStretch()

        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        return scroll

    def _create_behavior_tab(self) -> QWidget:
        """Create behavior configuration tab"""
        scroll = QScrollArea()
        widget = QWidget()
        layout = QVBoxLayout(widget)

        group = QGroupBox("Behavior")
        grid = QVBoxLayout(group)

        self.close_to_tray = QCheckBox("Close to System Tray")
        grid.addWidget(self.close_to_tray)

        self.minimize_to_tray = QCheckBox("Minimize to System Tray")
        grid.addWidget(self.minimize_to_tray)

        self.start_minimized = QCheckBox("Start Minimized")
        grid.addWidget(self.start_minimized)

        self.start_watcher_on_startup = QCheckBox("Start Watcher on Startup")
        grid.addWidget(self.start_watcher_on_startup)

        self.start_with_windows = QCheckBox("Start with Windows")
        grid.addWidget(self.start_with_windows)

        self.hide_console = QCheckBox("Hide Console Window (use pythonw.exe)")
        grid.addWidget(self.hide_console)

        self.check_updates_on_launch = QCheckBox("Check for updates on launch")
        self.check_updates_on_launch.setToolTip(
            "Automatically check for SparkyBot and Elite Insights updates when the app starts."
        )
        grid.addWidget(self.check_updates_on_launch)

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

    def _create_ai_tab(self) -> QWidget:
        """Create AI analysis configuration tab"""
        from core.ai_analyst import PRESETS

        scroll = QScrollArea()
        widget = QWidget()
        layout = QVBoxLayout(widget)

        group = QGroupBox("AI Fight Analysis")
        form = QFormLayout(group)

        self.enable_ai = QCheckBox("Enable AI Fight Analysis")
        form.addRow("", self.enable_ai)

        # Provider + Model on one row
        provider_model_row = QHBoxLayout()

        provider_label = QLabel("Provider:")
        self.ai_provider = QComboBox()
        self.ai_provider.addItems(list(PRESETS.keys()))
        self.ai_provider.currentTextChanged.connect(self._on_ai_provider_changed)

        model_label = QLabel("Model:")
        self.ai_model = QComboBox()
        self.ai_model.setEditable(True)
        self.ai_model.setPlaceholderText("Select or type model")
        self.ai_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

        provider_model_row.addWidget(provider_label)
        provider_model_row.addWidget(self.ai_provider, 1)  # stretch=1
        provider_model_row.addSpacing(10)
        provider_model_row.addWidget(model_label)
        provider_model_row.addWidget(self.ai_model, 1)  # stretch=1
        form.addRow(provider_model_row)

        # Base URL + small Refresh button on one row
        url_row = QHBoxLayout()
        self.ai_base_url = QLineEdit()
        self.ai_base_url.setPlaceholderText("https://api.example.com/v1")
        self.ai_refresh_models_btn = QPushButton("Refresh Models")
        self.ai_refresh_models_btn.setFixedWidth(110)
        self.ai_refresh_models_btn.clicked.connect(self._refresh_ai_models)
        url_row.addWidget(self.ai_base_url, 1)  # stretch=1, takes most of the space
        url_row.addWidget(self.ai_refresh_models_btn)
        form.addRow("API Base URL:", url_row)

        # API Key stays on its own row
        self.ai_api_key = QLineEdit()
        self.ai_api_key.setPlaceholderText("sk-... (leave blank for local models)")
        self.ai_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("API Key:", self.ai_api_key)

        # Max tokens
        self.ai_max_tokens = QSpinBox()
        self.ai_max_tokens.setRange(100, 4000)
        self.ai_max_tokens.setValue(350)
        form.addRow("Max Tokens:", self.ai_max_tokens)

        # System Prompt — small read-only preview with pop-out editor
        prompt_layout = QVBoxLayout()
        self.ai_system_prompt = QTextEdit()
        self.ai_system_prompt.setMaximumHeight(80)
        self.ai_system_prompt.setReadOnly(True)
        self.ai_system_prompt.setStyleSheet("background-color: #333; color: #aaa;")
        self.ai_system_prompt.setPlaceholderText("Using default SparkyBot analyst prompt")

        edit_prompt_btn = QPushButton("Edit System Prompt...")
        edit_prompt_btn.clicked.connect(self._edit_system_prompt)

        prompt_layout.addWidget(self.ai_system_prompt)
        prompt_layout.addWidget(edit_prompt_btn)
        form.addRow("System Prompt:", prompt_layout)

        # Test button
        self.ai_test_btn = QPushButton("Test Connection")
        self.ai_test_btn.clicked.connect(self._test_ai_connection)
        form.addRow("", self.ai_test_btn)

        self.ai_test_status = QLabel("")
        self.ai_test_status.setWordWrap(True)
        form.addRow("", self.ai_test_status)

        layout.addWidget(group)
        layout.addStretch()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        return scroll

    def _create_tts_tab(self) -> QWidget:
        """Create TTS (local audio playback + Discord attachment) tab"""
        scroll = QScrollArea()
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # -- General --
        general_group = QGroupBox("General")
        general_form = QFormLayout(general_group)

        self.enable_tts = QCheckBox("Play AI commentary through speakers")
        self.enable_tts.setToolTip(
            "Generates speech from the AI fight commentary after each fight "
            "and plays it locally on this machine using the selected TTS provider."
        )
        general_form.addRow("", self.enable_tts)

        self.tts_discord_attach = QCheckBox("Attach audio file to Discord post")
        self.tts_discord_attach.setToolTip(
            "Uploads the generated audio alongside the AI commentary embed.\n"
            "On Discord desktop it renders as an inline audio player.\n"
            "On Discord mobile it appears as a downloadable attachment (Discord limitation)."
        )
        general_form.addRow("", self.tts_discord_attach)

        discord_attach_note = QLabel(
            "⚠ Discord mobile does not support inline audio playback for file attachments."
        )
        discord_attach_note.setWordWrap(True)
        discord_attach_note.setStyleSheet("font-size: 10px; color: #888; padding-left: 4px;")
        general_form.addRow("", discord_attach_note)

        self.tts_volume = QSpinBox()
        self.tts_volume.setRange(0, 100)
        self.tts_volume.setSuffix("%")
        self.tts_volume.setValue(80)
        self.tts_volume.setToolTip("Local playback volume (0 = muted, 100 = full).")
        general_form.addRow("Volume:", self.tts_volume)

        layout.addWidget(general_group)

        # -- Provider --
        provider_group = QGroupBox("Provider")
        provider_form = QFormLayout(provider_group)

        self.tts_provider = QComboBox()
        self.tts_provider.addItems(["edge", "elevenlabs"])
        self.tts_provider.setToolTip(
            "edge: Microsoft neural voices via edge-tts (free, no API key, online)\n"
            "elevenlabs: ElevenLabs API (highest quality, API key required)"
        )
        self.tts_provider.currentTextChanged.connect(self._on_tts_provider_changed)
        provider_form.addRow("Provider:", self.tts_provider)

        # edge voice row
        self.tts_edge_voice = QComboBox()
        self.tts_edge_voice.setEditable(True)
        self.tts_edge_voice.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.tts_edge_voice.setPlaceholderText("e.g. en-GB-RyanNeural")
        self.tts_refresh_voices_btn = QPushButton("Refresh Voices")
        self.tts_refresh_voices_btn.setFixedWidth(110)
        self.tts_refresh_voices_btn.clicked.connect(self._refresh_tts_voices)
        edge_row = QHBoxLayout()
        edge_row.addWidget(self.tts_edge_voice, 1)
        edge_row.addWidget(self.tts_refresh_voices_btn)
        self.tts_edge_voice_label = QLabel("Edge Voice:")
        provider_form.addRow(self.tts_edge_voice_label, edge_row)

        # ElevenLabs fields
        self.tts_el_api_key_label = QLabel("API Key:")
        self.tts_elevenlabs_api_key = QLineEdit()
        self.tts_elevenlabs_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.tts_elevenlabs_api_key.setPlaceholderText("sk_...")
        provider_form.addRow(self.tts_el_api_key_label, self.tts_elevenlabs_api_key)

        self.tts_el_voice_id_label = QLabel("Voice ID:")
        self.tts_elevenlabs_voice_id = QLineEdit()
        self.tts_elevenlabs_voice_id.setPlaceholderText("JBFqnCBsd6RMkjVDRZzb  (George)")
        self.tts_elevenlabs_voice_id.setToolTip(
            "ElevenLabs voice ID. Browse voices at elevenlabs.io/app/voice-library."
        )
        provider_form.addRow(self.tts_el_voice_id_label, self.tts_elevenlabs_voice_id)

        self.tts_el_model_label = QLabel("Model:")
        self.tts_elevenlabs_model = QComboBox()
        self.tts_elevenlabs_model.addItems([
            "eleven_multilingual_v2",
            "eleven_v3",
            "eleven_turbo_v2_5",
            "eleven_turbo_v2",
            "eleven_monolingual_v1",
        ])
        self.tts_elevenlabs_model.setEditable(True)
        provider_form.addRow(self.tts_el_model_label, self.tts_elevenlabs_model)

        # Stability slider
        self.tts_el_stability_label = QLabel("Stability:")
        self.tts_el_stability = QSpinBox()
        self.tts_el_stability.setRange(0, 100)
        self.tts_el_stability.setSuffix("%")
        self.tts_el_stability.setValue(35)
        self.tts_el_stability.setToolTip(
            "0% = widest emotional range (expressive, unpredictable)\n"
            "100% = most consistent (monotone at extremes)\n"
            "Recommended for SparkyBot's commentator voice: 30-40%"
        )
        provider_form.addRow(self.tts_el_stability_label, self.tts_el_stability)

        # Similarity boost slider
        self.tts_el_similarity_label = QLabel("Similarity:")
        self.tts_el_similarity = QSpinBox()
        self.tts_el_similarity.setRange(0, 100)
        self.tts_el_similarity.setSuffix("%")
        self.tts_el_similarity.setValue(75)
        self.tts_el_similarity.setToolTip(
            "How closely the output adheres to the original voice recording.\n"
            "High values are cleaner but may reproduce recording artifacts.\n"
            "Recommended: 70-80%"
        )
        provider_form.addRow(self.tts_el_similarity_label, self.tts_el_similarity)

        # Style exaggeration slider
        self.tts_el_style_label = QLabel("Style:")
        self.tts_el_style = QSpinBox()
        self.tts_el_style.setRange(0, 100)
        self.tts_el_style.setSuffix("%")
        self.tts_el_style.setValue(15)
        self.tts_el_style.setToolTip(
            "Amplifies the voice's characteristic style.\n"
            "Non-zero values increase latency and reduce stability slightly.\n"
            "Recommended: 10-20% for dramatic delivery, 0% for neutral."
        )
        provider_form.addRow(self.tts_el_style_label, self.tts_el_style)

        # Speaker boost checkbox
        self.tts_el_speaker_boost_label = QLabel("")
        self.tts_el_speaker_boost = QCheckBox("Use Speaker Boost")
        self.tts_el_speaker_boost.setChecked(True)
        self.tts_el_speaker_boost.setToolTip(
            "Boosts similarity to the original speaker at a minor latency cost.\n"
            "Generally recommended to keep enabled."
        )
        provider_form.addRow(self.tts_el_speaker_boost_label, self.tts_el_speaker_boost)

        self.tts_el_speed_label = QLabel("Speed:")
        self.tts_el_speed = QDoubleSpinBox()
        self.tts_el_speed.setRange(0.7, 1.2)
        self.tts_el_speed.setSingleStep(0.05)
        self.tts_el_speed.setDecimals(2)
        self.tts_el_speed.setValue(1.0)
        self.tts_el_speed.setToolTip(
            "Speech rate multiplier. 1.0 = normal speed.\n"
            "0.7 = slowest (30% slower), 1.2 = fastest (20% faster).\n"
            "For fight commentary, 1.05–1.15 suits an energetic delivery."
        )
        provider_form.addRow(self.tts_el_speed_label, self.tts_el_speed)

        layout.addWidget(provider_group)

        # -- Test --
        test_group = QGroupBox("Test")
        test_form = QFormLayout(test_group)
        self.tts_test_btn = QPushButton("Test TTS")
        self.tts_test_btn.clicked.connect(self._test_tts)
        self.tts_test_status = QLabel("")
        self.tts_test_status.setWordWrap(True)
        test_form.addRow("", self.tts_test_btn)
        test_form.addRow("", self.tts_test_status)
        layout.addWidget(test_group)

        layout.addStretch()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)

        self._on_tts_provider_changed(self.tts_provider.currentText())
        return scroll

    def _on_tts_provider_changed(self, provider: str):
        is_edge = provider.lower() == "edge"
        is_el = provider.lower() == "elevenlabs"
        self.tts_edge_voice_label.setVisible(is_edge)
        self.tts_edge_voice.setVisible(is_edge)
        self.tts_refresh_voices_btn.setVisible(is_edge)
        self.tts_el_api_key_label.setVisible(is_el)
        self.tts_elevenlabs_api_key.setVisible(is_el)
        self.tts_el_voice_id_label.setVisible(is_el)
        self.tts_elevenlabs_voice_id.setVisible(is_el)
        self.tts_el_model_label.setVisible(is_el)
        self.tts_elevenlabs_model.setVisible(is_el)
        self.tts_el_stability_label.setVisible(is_el)
        self.tts_el_stability.setVisible(is_el)
        self.tts_el_similarity_label.setVisible(is_el)
        self.tts_el_similarity.setVisible(is_el)
        self.tts_el_style_label.setVisible(is_el)
        self.tts_el_style.setVisible(is_el)
        self.tts_el_speaker_boost_label.setVisible(is_el)
        self.tts_el_speaker_boost.setVisible(is_el)
        self.tts_el_speed_label.setVisible(is_el)
        self.tts_el_speed.setVisible(is_el)

    def _refresh_tts_voices(self):
        self.tts_refresh_voices_btn.setEnabled(False)
        self.tts_refresh_voices_btn.setText("Fetching...")
        self.tts_test_status.setText("")
        import threading

        def _fetch():
            try:
                import asyncio, edge_tts

                async def _list():
                    return await edge_tts.list_voices()

                loop = asyncio.new_event_loop()
                try:
                    voices = loop.run_until_complete(_list())
                finally:
                    loop.close()

                en_voices = sorted(
                    [v["ShortName"] for v in voices if v["ShortName"].startswith("en-")]
                )
                all_voices = sorted([v["ShortName"] for v in voices])
                ordered = en_voices + [v for v in all_voices if v not in en_voices]
                current = self.tts_edge_voice.currentText().strip()
                self.tts_edge_voice.blockSignals(True)
                self.tts_edge_voice.clear()
                self.tts_edge_voice.addItems(ordered)
                if current and current in ordered:
                    self.tts_edge_voice.setCurrentText(current)
                elif current:
                    self.tts_edge_voice.setEditText(current)
                self.tts_edge_voice.blockSignals(False)
                self.tts_test_status.setText(f"✓ {len(ordered)} voices loaded.")
            except ImportError:
                self.tts_test_status.setText("edge-tts is not installed.")
            except Exception as e:
                self.tts_test_status.setText(f"Failed to fetch voices: {e}")
            finally:
                self.tts_refresh_voices_btn.setEnabled(True)
                self.tts_refresh_voices_btn.setText("Refresh Voices")

        threading.Thread(target=_fetch, daemon=True).start()

    def _test_tts(self):
        self.tts_test_btn.setEnabled(False)
        self.tts_test_status.setText("Generating audio...")
        import threading

        def _run():
            try:
                from core.tts import generate_tts_bytes

                class _Cfg:
                    tts_provider = self.tts_provider.currentText()
                    tts_edge_voice = self.tts_edge_voice.currentText().strip() or "en-GB-RyanNeural"
                    tts_elevenlabs_api_key = self.tts_elevenlabs_api_key.text().strip()
                    tts_elevenlabs_voice_id = self.tts_elevenlabs_voice_id.text().strip() or "JBFqnCBsd6RMkjVDRZzb"
                    tts_elevenlabs_model = self.tts_elevenlabs_model.currentText().strip() or "eleven_multilingual_v2"
                    tts_elevenlabs_stability = self.tts_el_stability.value() / 100.0
                    tts_elevenlabs_similarity_boost = self.tts_el_similarity.value() / 100.0
                    tts_elevenlabs_style = self.tts_el_style.value() / 100.0
                    tts_elevenlabs_speaker_boost = self.tts_el_speaker_boost.isChecked()
                    tts_elevenlabs_speed = self.tts_el_speed.value()

                audio_bytes = generate_tts_bytes(
                    "SparkyBot TTS is working. Let's get those bags.", _Cfg()
                )
                if not audio_bytes:
                    self.tts_test_status.setText("✗ Audio generation failed — check logs.")
                    return

                client = getattr(self, '_tts_client', None)
                if client is not None:
                    client.update_volume(self.tts_volume.value())
                    client.speak_from_bytes(audio_bytes)
                    self.tts_test_status.setText("✓ Audio queued — check your speakers.")
                else:
                    self.tts_test_status.setText(
                        "✓ Audio generated successfully. Save & restart to enable local playback."
                    )
            except Exception as e:
                self.tts_test_status.setText(f"Test failed: {e}")
            finally:
                self.tts_test_btn.setEnabled(True)

        threading.Thread(target=_run, daemon=True).start()

    def _on_ai_provider_changed(self, provider_name: str):
        """Fill in base URL and model from preset, then refresh model list."""
        from core.ai_analyst import PRESETS
        preset = PRESETS.get(provider_name, {})
        if preset.get("base_url"):
            self.ai_base_url.setText(preset["base_url"])
        if preset.get("default_model"):
            self.ai_model.setEditText(preset["default_model"])
        # Auto-fetch available models for this provider
        if preset.get("base_url"):
            self._refresh_ai_models()

    def _refresh_ai_models(self):
        """Fetch available models from the configured API endpoint."""
        base_url = self.ai_base_url.text().strip()
        api_key = self.ai_api_key.text().strip()

        if not base_url:
            self.ai_test_status.setText("Enter a Base URL first")
            return

        self.ai_refresh_models_btn.setEnabled(False)
        self.ai_refresh_models_btn.setText("Fetching...")

        import threading
        def _fetch():
            from core.ai_analyst import FightAnalyst, PRESETS
            models = FightAnalyst.fetch_models(base_url, api_key)
            source = "API"

            if not models:
                # Fallback to preset model list
                provider = self.ai_provider.currentText()
                preset = PRESETS.get(provider, {})
                models = preset.get("models", [])
                source = "preset"

            self.ai_refresh_models_btn.setEnabled(True)
            self.ai_refresh_models_btn.setText("Refresh Models")

            if models:
                current = self.ai_model.currentText()
                self.ai_model.clear()
                self.ai_model.addItems(models)
                idx = self.ai_model.findText(current)
                if idx >= 0:
                    self.ai_model.setCurrentIndex(idx)
                elif current:
                    self.ai_model.setEditText(current)
                self.ai_test_status.setText(f"Loaded {len(models)} models (from {source})")
            else:
                self.ai_test_status.setText("No models found — type a model name manually")

        threading.Thread(target=_fetch, daemon=True).start()

        threading.Thread(target=_fetch, daemon=True).start()

    def _test_ai_connection(self):
        """Send a test request to verify the AI connection works."""
        from core.ai_analyst import FightAnalyst

        analyst = FightAnalyst(
            base_url=self.ai_base_url.text(),
            api_key=self.ai_api_key.text(),
            model=self.ai_model.currentText(),
            system_prompt=self.ai_system_prompt.toPlainText() or None,
            max_tokens=self.ai_max_tokens.value(),
        )

        test_summary = {
            "zone": "Eternal Battlegrounds",
            "duration": "05m 30s",
            "kdr": 4.5,
            "squad_count": 35,
            "ally_count": 10,
            "squad_damage": 5000000,
            "squad_dps": 15000,
            "squad_downs": 40,
            "squad_kills": 27,
            "squad_deaths": 6,
            "enemy_count": 50,
            "enemy_deaths": 27,
            "top_damage": [{"name": "TestPlayer", "profession": "Guardian", "damage": 800000}],
            "enemy_breakdown": {"GUAR": 8, "NECR": 6, "ELEM": 5},
            "enemy_teams": {"Red": 30, "Blue": 20},
        }

        self.ai_test_status.setText("Testing...")
        self.ai_test_btn.setEnabled(False)

        import threading
        def _run_test():
            result = analyst.analyze(test_summary, timeout=15)
            if result:
                self.ai_test_status.setText(f"Success! Response:\n{result[:200]}")
            else:
                self.ai_test_status.setText("Failed — check URL, key, and model name")
            self.ai_test_btn.setEnabled(True)

        threading.Thread(target=_run_test, daemon=True).start()

    def _edit_system_prompt(self):
        """Open a larger dialog for editing the system prompt."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QDialogButtonBox, QLabel
        from PyQt6.QtCore import Qt

        dialog = QDialog(self)
        dialog.setWindowTitle("Edit System Prompt")
        dialog.setMinimumSize(700, 500)

        # Apply same icon as main window
        icon_path = Path(__file__).parent.parent / "assets" / "sbtray.ico"
        if icon_path.exists():
            from PyQt6.QtGui import QIcon
            dialog.setWindowIcon(QIcon(str(icon_path)))

        layout = QVBoxLayout(dialog)

        hint = QLabel("Customize the AI's personality and analysis style. Leave blank to use the built-in default.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        editor = QTextEdit()
        editor.setPlainText(self.ai_system_prompt.toPlainText())
        editor.setMinimumHeight(400)
        layout.addWidget(editor)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)

        # Add a "Reset to Default" button
        reset_btn = buttons.addButton("Reset to Default", QDialogButtonBox.ButtonRole.ResetRole)

        def on_reset():
            from core.ai_analyst import FightAnalyst
            editor.setPlainText(FightAnalyst._default_system_prompt())

        reset_btn.clicked.connect(on_reset)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.ai_system_prompt.setPlainText(editor.toPlainText())

    # ---- Windows startup registry helpers ----
    _STARTUP_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    _STARTUP_REG_NAME = "SparkyBot"

    def _get_startup_command(self) -> str:
        """Build the command that Windows will run at startup."""
        python_exe = sys.executable
        if self.config.hide_console:
            pythonw = python_exe.replace("python.exe", "pythonw.exe")
            if Path(pythonw).exists():
                python_exe = pythonw
        bootstrap = Path(__file__).parent.parent / "bootstrap.py"
        return f'"{python_exe}" "{bootstrap}"'

    def _is_in_startup_registry(self) -> bool:
        """Check if SparkyBot is registered to start with Windows."""
        if sys.platform != "win32":
            return False
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._STARTUP_REG_KEY, 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, self._STARTUP_REG_NAME)
            winreg.CloseKey(key)
            return True
        except (FileNotFoundError, OSError):
            return False

    def _add_to_startup_registry(self):
        """Add SparkyBot to Windows startup."""
        if sys.platform != "win32":
            return
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._STARTUP_REG_KEY, 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, self._STARTUP_REG_NAME, 0, winreg.REG_SZ, self._get_startup_command())
            winreg.CloseKey(key)
        except OSError as e:
            logging.getLogger(__name__).warning(f"Failed to add startup registry entry: {e}")

    def _remove_from_startup_registry(self):
        """Remove SparkyBot from Windows startup."""
        if sys.platform != "win32":
            return
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._STARTUP_REG_KEY, 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, self._STARTUP_REG_NAME)
            winreg.CloseKey(key)
        except (FileNotFoundError, OSError):
            pass

    def _create_updates_tab(self) -> QWidget:
        """Create updates tab for SparkyBot and Elite Insights"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # SparkyBot section
        sparkybot_group = QGroupBox("SparkyBot")
        sparkybot_layout = QVBoxLayout(sparkybot_group)

        # Current version
        version_layout = QFormLayout()
        self.sparkybot_version_label = QLabel(f"v{VERSION}")
        version_layout.addRow("Current Version:", self.sparkybot_version_label)

        self.sparkybot_latest_label = QLabel("Checking GitHub...")
        self.sparkybot_latest_label.setOpenExternalLinks(True)
        version_layout.addRow("Latest Version:", self.sparkybot_latest_label)

        sparkybot_layout.addLayout(version_layout)

        # Update button
        self.update_sparkybot_button = QPushButton("Check for SparkyBot Update")
        self.update_sparkybot_button.setMinimumHeight(40)
        self.update_sparkybot_button.clicked.connect(self._on_update_sparkybot_clicked)
        sparkybot_layout.addWidget(self.update_sparkybot_button)

        # Progress bar
        self.sparkybot_progress = QProgressBar()
        self.sparkybot_progress.setRange(0, 100)
        self.sparkybot_progress.setValue(0)
        self.sparkybot_progress.setVisible(False)
        sparkybot_layout.addWidget(self.sparkybot_progress)

        # Status text
        self.sparkybot_status_label = QLabel("")
        self.sparkybot_status_label.setWordWrap(True)
        self.sparkybot_status_label.setTextFormat(Qt.TextFormat.RichText)
        self.sparkybot_status_label.setOpenExternalLinks(True)
        sparkybot_layout.addWidget(self.sparkybot_status_label)

        layout.addWidget(sparkybot_group)

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

        layout.addStretch()

        # Check initial status
        QTimer.singleShot(100, self._check_ei_status)
        QTimer.singleShot(100, self._check_sparkybot_status)

        return widget

    def _check_sparkybot_status(self):
        """Check current SparkyBot version and latest from GitHub"""
        try:
            self.sparkybot_status_label.setText("Checking for updates...")
            
            thread = threading.Thread(target=self._fetch_latest_sparkybot_version, daemon=True)
            thread.start()
        except Exception as e:
            self.sparkybot_status_label.setText(f"Error checking status: {e}")

    def _fetch_latest_sparkybot_version(self):
        """Fetch latest SparkyBot version from GitHub API"""
        try:
            import requests
            import re
            response = requests.get(
                "https://api.github.com/repos/SimpleHonors/SparkyBot/releases/latest",
                headers={"User-Agent": "SparkyBot"},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                # Try release name first (e.g., "v1.1.2"), fall back to tag_name
                raw_version = data.get("name", "") or data.get("tag_name", "")
                latest_version = raw_version.lstrip("v").strip()

                # Validate it looks like a version number (digits and dots)
                if not re.match(r'^\d+\.\d+', latest_version):
                    self.sig_sparkybot_latest.emit("Unable to parse version")
                    return

                text = f'<a href="https://github.com/SimpleHonors/SparkyBot/releases">v{latest_version}</a>'
                self.sig_sparkybot_latest.emit(text)

                current = _parse_version(VERSION)
                latest = _parse_version(latest_version)

                if latest > current:
                    self.sig_sparkybot_status.emit(f"Update available: v{VERSION} → v{latest_version}")
                elif latest == current:
                    self.sig_sparkybot_status.emit(f"You have the latest version (v{VERSION}).")
                else:
                    self.sig_sparkybot_status.emit(f"You are ahead of the latest release (v{VERSION} > v{latest_version})")
            elif response.status_code == 404:
                self.sig_sparkybot_latest.emit("No releases yet")
                self.sig_sparkybot_status.emit("No releases found on GitHub.")
            else:
                self.sig_sparkybot_latest.emit("Unable to fetch")
                self.sig_sparkybot_status.emit(f"GitHub API returned {response.status_code}")
        except Exception as e:
            self.sig_sparkybot_latest.emit("Unable to fetch")
            self.sig_sparkybot_status.emit(f"Error: {e}")

    def _on_update_sparkybot_clicked(self):
        """Handle SparkyBot update button click"""
        if hasattr(self, '_sparkybot_update_url') and self._sparkybot_update_url:
            url = self._sparkybot_update_url
            version = getattr(self, '_sparkybot_latest_version', 'unknown')
            self._sparkybot_update_url = None
            self.update_sparkybot_button.setEnabled(False)
            thread = threading.Thread(
                target=self._do_sparkybot_install,
                args=(url, version),
                daemon=True
            )
            thread.start()
        else:
            self.update_sparkybot_button.setEnabled(False)
            self.update_sparkybot_button.setText("Checking...")
            thread = threading.Thread(target=self._do_sparkybot_update_check, daemon=True)
            thread.start()

    def _do_sparkybot_update_check(self):
        """Background thread for SparkyBot update check"""
        try:
            import requests
            import re
            self.sig_sparkybot_status.emit("Checking GitHub for updates...")

            response = requests.get(
                "https://api.github.com/repos/SimpleHonors/SparkyBot/releases/latest",
                headers={"User-Agent": "SparkyBot"},
                timeout=10
            )

            if response.status_code == 404:
                self.sig_sparkybot_status.emit("No releases found on GitHub yet.")
                self.sig_sparkybot_button_state.emit("Check for SparkyBot Update", True)
                return

            if response.status_code != 200:
                self.sig_sparkybot_status.emit(f"GitHub API returned {response.status_code}")
                self.sig_sparkybot_button_state.emit("Check for SparkyBot Update", True)
                return

            data = response.json()
            # Try release name first (e.g., "v1.1.2"), fall back to tag_name
            raw_version = data.get("name", "") or data.get("tag_name", "")
            latest_version = raw_version.lstrip("v").strip()

            # Validate it looks like a version number (digits and dots)
            if not re.match(r'^\d+\.\d+', latest_version):
                self.sig_sparkybot_status.emit("Could not parse version from GitHub API.")
                self.sig_sparkybot_button_state.emit("Check for SparkyBot Update", True)
                return

            current_version = VERSION
            latest_tuple = _parse_version(latest_version)
            current_tuple = _parse_version(current_version)

            if latest_tuple > current_tuple:
                # Update available — find the download URL
                pass  # continues to download URL logic below
            elif latest_tuple == current_tuple:
                self.sig_sparkybot_status.emit(f"You have the latest SparkyBot (v{current_version}).")
                self.sig_sparkybot_button_state.emit("Already Up to Date", True)
                return
            else:
                # Current is newer than latest release (dev/pre-release build)
                self.sig_sparkybot_status.emit(
                    f"v{current_version} is newer than latest release (v{latest_version})"
                )
                self.sig_sparkybot_button_state.emit("Already Up to Date", True)
                return

            # Update available — find the download URL
            assets = data.get("assets", [])
            download_url = None
            for asset in assets:
                if asset.get("name", "").endswith(".zip"):
                    download_url = asset.get("browser_download_url")
                    break

            if not download_url:
                download_url = data.get("zipball_url")

            # Log what we found for debugging
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"SparkyBot update: assets={len(assets)}, zipball_url={data.get('zipball_url')}, download_url={download_url}")

            if not download_url or download_url == "None":
                self.sig_sparkybot_status.emit(
                    f"Update available: v{current_version} → v{latest_version}\n"
                    f"Could not find download URL. Visit GitHub manually."
                )
                self.sig_sparkybot_button_state.emit("Check for SparkyBot Update", True)
                return

            self._sparkybot_update_url = download_url
            self._sparkybot_latest_version = latest_version
            self.sig_sparkybot_status.emit(
                f"Update available: v{current_version} → v{latest_version}"
            )
            self.sig_sparkybot_button_state.emit("Download & Install Update", True)

        except Exception as e:
            self.sig_sparkybot_status.emit(f"Error: {e}")
            self.sig_sparkybot_button_state.emit("Check for SparkyBot Update", True)

    def _do_sparkybot_install(self, url, version):
        """Download and install SparkyBot update."""
        try:
            import requests
            import zipfile
            import shutil
            import tempfile
            import logging
            logger = logging.getLogger(__name__)

            logger.info(f"Starting SparkyBot update download from: {url}")

            app_dir = Path(__file__).parent.parent

            self.sig_sparkybot_status.emit("Downloading update...")
            self.sig_sparkybot_button_state.emit("Downloading...", False)

            # Download to temp file
            response = requests.get(url, stream=True, timeout=60)
            response.raise_for_status()

            with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
                tmp_path = Path(tmp.name)
                for chunk in response.iter_content(chunk_size=8192):
                    tmp.write(chunk)

            self.sig_sparkybot_status.emit("Installing update...")

            # Paths to protect during the update
            PROTECTED_PATHS = {'config.properties', 'GW2EI'}
            SKIP_PATHS = {'.github', 'CODE_OF_CONDUCT.md', 'CONTRIBUTING.md', 'SECURITY.md', 'LICENSE', '.gitignore'}

            # Extract only files that are new or changed
            with zipfile.ZipFile(tmp_path, 'r') as zf:
                names = zf.namelist()
                logger.info(f"Zip contains {len(names)} entries")
                logger.info(f"Zip root: {names[0] if names else 'empty'}")

                # Log a few sample paths
                for n in names[1:5]:
                    logger.info(f"  Sample: {n}")

                skipped = updated = new_files = 0

                for member in zf.namelist():
                    # Get path relative to zip's root directory
                    parts = member.split('/', 1)
                    if len(parts) < 2 or not parts[1]:
                        continue  # skip top-level directory entry
                    relative_path = parts[1]

                    # Skip protected paths
                    top_level = relative_path.split('/')[0]
                    if top_level in PROTECTED_PATHS:
                        continue

                    # Skip repo-only files (not needed on user machines)
                    if top_level in SKIP_PATHS or member in SKIP_PATHS:
                        continue

                    target = app_dir / relative_path

                    # Create directories as needed
                    if member.endswith('/'):
                        target.mkdir(parents=True, exist_ok=True)
                        continue

                    # Log version.py specifically
                    if 'version.py' in relative_path:
                        logger.info(f"Found version.py: member={member}, target={target}, exists={target.exists()}")
                        if target.exists():
                            logger.info(f"  Local content: {target.read_text()[:50]}")
                            new_content = zf.read(member)
                            logger.info(f"  Zip content: {new_content[:50]}")

                    # Skip if local file exists and is identical by content hash
                    if target.exists():
                        new_content = zf.read(member)
                        old_hash = hashlib.md5(target.read_bytes()).digest()
                        new_hash = hashlib.md5(new_content).digest()
                        if old_hash == new_hash:
                            skipped += 1
                            continue
                        # Content differs — write it
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with open(target, 'wb') as dst:
                            dst.write(new_content)
                        updated += 1
                        logger.debug(f"Updated: {relative_path}")
                        continue

                    # File doesn't exist locally — write it
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target, 'wb') as dst:
                        dst.write(src.read())
                    new_files += 1
                    logger.debug(f"New file: {relative_path}")

            logger.info(f"Update result: {new_files} new, {updated} updated, {skipped} unchanged")

            tmp_path.unlink()

            self.sig_sparkybot_status.emit(
                f"Updated to v{version}. "
                f"Please restart SparkyBot for changes to take effect."
            )
            self.sig_sparkybot_button_state.emit("Restart Required", False)
            self.sig_update_complete.emit(version)

        except Exception as e:
            self.sig_sparkybot_status.emit(f"Update failed: {e}")
            self.sig_sparkybot_button_state.emit("Check for SparkyBot Update", True)

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

            success, message = updater.download_and_update(download_url, version=latest_version, progress_callback=progress_callback)

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
            lambda show, val: (self.update_progress.setVisible(show), self.update_progress.setValue(val))
        )
        self.sig_progress_value.connect(
            lambda val: self.update_progress.setValue(val)
        )
        self.sig_ei_latest.connect(
            lambda t: self.ei_latest_label.setText(t)
        )
        self.sig_ei_status_refresh.connect(
            lambda: QTimer.singleShot(100, self._check_ei_status)
        )
        
        # SparkyBot signals
        self.sig_sparkybot_status.connect(
            lambda t: self.sparkybot_status_label.setText(t)
        )
        self.sig_sparkybot_button_state.connect(
            lambda t, e: (self.update_sparkybot_button.setText(t), self.update_sparkybot_button.setEnabled(e))
        )
        self.sig_sparkybot_latest.connect(
            lambda t: self.sparkybot_latest_label.setText(t)
        )

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
        self.poll_interval.setValue(self.config.poll_interval)

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
        self.show_strips.setChecked(self.config.show_strips)
        self.show_cleanses.setChecked(self.config.show_cleanses)
        self.show_downs.setChecked(self.config.show_downs_kills)
        self.show_burst.setChecked(self.config.show_burst_dmg)
        self.show_top_skills.setChecked(self.config.show_top_enemy_skills)
        self.show_offensive_boons.setChecked(self.config.show_offensive_boons)
        self.show_defensive_boons.setChecked(self.config.show_defensive_boons)
        self.show_enemy_breakdown.setChecked(self.config.show_enemy_breakdown)

        # Behavior
        self.close_to_tray.setChecked(self.config.close_to_tray)
        self.minimize_to_tray.setChecked(self.config.minimize_to_tray)
        self.start_minimized.setChecked(self.config.start_minimized)
        self.start_watcher_on_startup.setChecked(self.config.start_watcher_on_startup)

        # Capture initial values for relaunch-change detection
        self._initial_hide_console = self.config.hide_console
        self._initial_start_with_windows = self._is_in_startup_registry()

        # Check if SparkyBot is in the Windows startup registry
        self.start_with_windows.setChecked(self._is_in_startup_registry())
        self.hide_console.setChecked(self.config.hide_console)
        self.check_updates_on_launch.setChecked(self.config.check_updates_on_launch)

        # Memory
        self.max_parse_memory.setValue(self.config.max_parse_memory)

        # AI
        self.enable_ai.setChecked(self.config.enable_ai_analysis)
        self.ai_provider.setCurrentText(self.config.ai_provider)
        self.ai_base_url.setText(self.config.ai_base_url)
        self.ai_api_key.setText(self.config.ai_api_key)
        self.ai_model.setEditText(self.config.ai_model)
        self.ai_max_tokens.setValue(self.config.ai_max_tokens)
        if self.config.ai_system_prompt:
            self.ai_system_prompt.setPlainText(self.config.ai_system_prompt)
        else:
            # Show the built-in default so users can see and edit it
            from core.ai_analyst import FightAnalyst
            self.ai_system_prompt.setPlainText(FightAnalyst._default_system_prompt())

        # Twitch
        self.enable_twitch.setChecked(self.config.enable_twitch)
        self.twitch_channel.setText(self.config.twitch_channel)
        self.twitch_token.setText(self.config.twitch_token)
        self.twitch_use_tls.setChecked(self.config.twitch_use_tls)

        # TTS
        self.tts_provider.setCurrentText(self.config.tts_provider)
        self.tts_edge_voice.setEditText(self.config.tts_edge_voice)
        self.tts_volume.setValue(self.config.tts_volume)
        self.enable_tts.setChecked(self.config.tts_enabled)
        self.tts_discord_attach.setChecked(self.config.tts_discord_attach)
        self.tts_elevenlabs_api_key.setText(self.config.tts_elevenlabs_api_key)
        self.tts_elevenlabs_voice_id.setText(self.config.tts_elevenlabs_voice_id)
        self.tts_elevenlabs_model.setCurrentText(self.config.tts_elevenlabs_model)
        self.tts_el_stability.setValue(int(self.config.tts_elevenlabs_stability * 100))
        self.tts_el_similarity.setValue(int(self.config.tts_elevenlabs_similarity_boost * 100))
        self.tts_el_style.setValue(int(self.config.tts_elevenlabs_style * 100))
        self.tts_el_speaker_boost.setChecked(self.config.tts_elevenlabs_speaker_boost)
        self.tts_el_speed.setValue(self.config.tts_elevenlabs_speed)
        self._on_tts_provider_changed(self.config.tts_provider)

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
        cfg('Paths', 'pollInterval', str(self.poll_interval.value()))

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
        cfg('UI', 'showStrips', str(self.show_strips.isChecked()))
        cfg('UI', 'showCleanses', str(self.show_cleanses.isChecked()))
        cfg('UI', 'showDownsKills', str(self.show_downs.isChecked()))
        cfg('UI', 'showBurstDmg', str(self.show_burst.isChecked()))
        cfg('UI', 'showTopEnemySkills', str(self.show_top_skills.isChecked()))
        cfg('UI', 'showOffensiveBoons', str(self.show_offensive_boons.isChecked()))
        cfg('UI', 'showDefensiveBoons', str(self.show_defensive_boons.isChecked()))
        cfg('UI', 'showEnemyBreakdown', str(self.show_enemy_breakdown.isChecked()))

        # Behavior
        cfg('Behavior', 'closeToTray', str(self.close_to_tray.isChecked()))
        cfg('Behavior', 'minimizeToTray', str(self.minimize_to_tray.isChecked()))
        cfg('Behavior', 'startMinimized', str(self.start_minimized.isChecked()))
        cfg('Behavior', 'startWatcherOnStartup', str(self.start_watcher_on_startup.isChecked()))
        cfg('Behavior', 'hideConsole', str(self.hide_console.isChecked()))
        cfg('Behavior', 'checkUpdatesOnLaunch', str(self.check_updates_on_launch.isChecked()))

        # Windows startup registry
        if self.start_with_windows.isChecked():
            self._add_to_startup_registry()
        else:
            self._remove_from_startup_registry()

        cfg('Behavior', 'maxParseMemory', str(self.max_parse_memory.value()))

        # AI
        cfg('AI', 'enableAiAnalysis', str(self.enable_ai.isChecked()))
        cfg('AI', 'aiProvider', self.ai_provider.currentText())
        cfg('AI', 'aiBaseUrl', self.ai_base_url.text())
        cfg('AI', 'aiApiKey', self.ai_api_key.text())
        cfg('AI', 'aiModel', self.ai_model.currentText())
        cfg('AI', 'aiMaxTokens', str(self.ai_max_tokens.value()))
        cfg('AI', 'aiSystemPrompt', self.ai_system_prompt.toPlainText())

        # Twitch
        cfg('Twitch', 'enableTwitchBot', str(self.enable_twitch.isChecked()).lower())
        cfg('Twitch', 'twitchChannelName', self.twitch_channel.text().strip())
        cfg('Twitch', 'twitchBotToken', self.twitch_token.text().strip())
        cfg('Twitch', 'twitchUseTLS', str(self.twitch_use_tls.isChecked()).lower())

        # TTS
        cfg('TTS', 'enableTts', str(self.enable_tts.isChecked()).lower())
        cfg('TTS', 'ttsProvider', self.tts_provider.currentText())
        cfg('TTS', 'ttsEdgeVoice', self.tts_edge_voice.currentText().strip())
        cfg('TTS', 'ttsVolume', str(self.tts_volume.value()))
        cfg('TTS', 'ttsDiscordAttach', str(self.tts_discord_attach.isChecked()).lower())
        cfg('TTS', 'ttsElevenLabsApiKey', self.tts_elevenlabs_api_key.text().strip())
        cfg('TTS', 'ttsElevenLabsVoiceId', self.tts_elevenlabs_voice_id.text().strip())
        cfg('TTS', 'ttsElevenLabsModel', self.tts_elevenlabs_model.currentText().strip())
        cfg('TTS', 'ttsElevenLabsStability', str(self.tts_el_stability.value() / 100.0))
        cfg('TTS', 'ttsElevenLabsSimilarityBoost', str(self.tts_el_similarity.value() / 100.0))
        cfg('TTS', 'ttsElevenLabsStyle', str(self.tts_el_style.value() / 100.0))
        cfg('TTS', 'ttsElevenLabsSpeakerBoost', str(self.tts_el_speaker_boost.isChecked()).lower())
        cfg('TTS', 'ttsElevenLabsSpeed', str(self.tts_el_speed.value()))

        # Write to file and reload attributes
        if self.config.save():
            self.settings_changed.emit()
            QMessageBox.information(self, "Settings", "Settings saved successfully!")

            # Relaunch notice if console or startup settings changed
            relaunch_needed = False
            if self.hide_console.isChecked() != self._initial_hide_console:
                relaunch_needed = True
            if self.start_with_windows.isChecked() != self._initial_start_with_windows:
                relaunch_needed = True

            if relaunch_needed:
                QMessageBox.information(
                    self,
                    "Relaunch Required",
                    "Console window and Windows startup changes will take effect the next time SparkyBot is launched.",
                )
                # Update so we don't nag again on next save
                self._initial_hide_console = self.hide_console.isChecked()
                self._initial_start_with_windows = self.start_with_windows.isChecked()
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

    def _create_process_files_tab(self) -> QWidget:
        """Create process files tab for manual log processing."""
        from PyQt6.QtCore import QMimeData
        from pathlib import Path as P

        class ProcessFilesWidget(QWidget):
            # Signal emitted when user clicks Process — sends list of Path objects
            process_requested = pyqtSignal(list)

            def __init__(self, config, parent=None):
                super().__init__(parent)
                self.config = config
                self.setAcceptDrops(True)
                self._build_ui()

            def _build_ui(self):
                layout = QVBoxLayout(self)

                # Header description
                header = QLabel(
                    "Manually process individual log files without the file watcher. "
                    "Drop or browse for .evtc/.zevtc files below — they'll run through "
                    "the full pipeline (GW2EI parse → report → Discord) as a one-off."
                )
                header.setWordWrap(True)
                header.setStyleSheet("color: #aaa; padding: 4px 0 8px 0;")
                layout.addWidget(header)

                # Drop zone
                self.drop_label = QLabel("Drag & drop .evtc / .zevtc files here\nor use Browse below")
                self.drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.drop_label.setMinimumHeight(120)
                self.drop_label.setStyleSheet(
                    "border: 2px dashed #666; border-radius: 8px; "
                    "padding: 20px; font-size: 14px; color: #999;"
                )
                layout.addWidget(self.drop_label)

                # Browse button
                browse_btn = QPushButton("Browse Files...")
                browse_btn.clicked.connect(self._browse_files)
                layout.addWidget(browse_btn)

                # File queue list
                self.file_list = QListWidget()
                layout.addWidget(self.file_list)

                # Button row
                btn_row = QHBoxLayout()
                remove_btn = QPushButton("Remove Selected")
                remove_btn.clicked.connect(self._remove_selected)
                clear_btn = QPushButton("Clear All")
                clear_btn.clicked.connect(self.file_list.clear)
                self.process_btn = QPushButton("Process Files")
                self.process_btn.setEnabled(False)
                self.process_btn.clicked.connect(self._process)
                btn_row.addWidget(remove_btn)
                btn_row.addWidget(clear_btn)
                btn_row.addStretch()
                btn_row.addWidget(self.process_btn)
                layout.addLayout(btn_row)

                # Status label
                self.status_label = QLabel("")
                layout.addWidget(self.status_label)

            def dragEnterEvent(self, event):
                if event.mimeData().hasUrls():
                    for url in event.mimeData().urls():
                        if url.toLocalFile().lower().endswith(('.evtc', '.zevtc')):
                            event.acceptProposedAction()
                            self.drop_label.setStyleSheet(
                                "border: 2px dashed #4CAF50; border-radius: 8px; "
                                "padding: 20px; font-size: 14px; color: #4CAF50;"
                            )
                            return
                event.ignore()

            def dragLeaveEvent(self, event):
                self.drop_label.setStyleSheet(
                    "border: 2px dashed #666; border-radius: 8px; "
                    "padding: 20px; font-size: 14px; color: #999;"
                )

            def dropEvent(self, event):
                self.dragLeaveEvent(event)
                for url in event.mimeData().urls():
                    path = url.toLocalFile()
                    if path.lower().endswith(('.evtc', '.zevtc')):
                        self._add_file(path)

            def _browse_files(self):
                # Default to the first configured log folder
                start_dir = ""
                log_folders = self.config.get_log_folders()
                if log_folders:
                    folder = str(log_folders[0])
                    if P(folder).exists():
                        start_dir = folder

                files, _ = QFileDialog.getOpenFileNames(
                    self, "Select Log Files", start_dir,
                    "ArcDPS Logs (*.evtc *.zevtc);;All Files (*)"
                )
                for f in files:
                    self._add_file(f)

            def _add_file(self, path: str):
                # Avoid duplicates
                for i in range(self.file_list.count()):
                    if self.file_list.item(i).data(Qt.ItemDataRole.UserRole) == path:
                        return
                item = QListWidgetItem(P(path).name)
                item.setData(Qt.ItemDataRole.UserRole, path)
                item.setToolTip(path)
                self.file_list.addItem(item)
                self.process_btn.setEnabled(True)

            def _remove_selected(self):
                for item in self.file_list.selectedItems():
                    self.file_list.takeItem(self.file_list.row(item))
                self.process_btn.setEnabled(self.file_list.count() > 0)

            def _process(self):
                paths = []
                for i in range(self.file_list.count()):
                    item = self.file_list.item(i)
                    paths.append(Path(item.data(Qt.ItemDataRole.UserRole)))
                if paths:
                    self.process_requested.emit(paths)

        # Create and return the widget
        self.process_files_widget = ProcessFilesWidget(self.config)
        return self.process_files_widget

    def _create_about_tab(self) -> QWidget:
        """Create about tab"""
        widget = QWidget()
        outer_layout = QVBoxLayout(widget)

        # Inner widget with fixed content — doesn't grow with window
        inner = QWidget()
        inner.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addSpacing(10)

        title = QLabel("<b>SparkyBot</b>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        version_row = QHBoxLayout()
        version_label = QLabel(f"Version {VERSION}")
        version_label.setStyleSheet("font-size: 11px; color: #aaa;")
        github_link = QLabel('<a href="https://github.com/SimpleHonors/SparkyBot" style="color: #5bc0de;">View on GitHub</a>')
        github_link.setOpenExternalLinks(True)
        github_link.setStyleSheet("font-size: 11px;")
        version_row.addStretch()
        version_row.addWidget(version_label)
        version_row.addWidget(QLabel("  •  "))
        version_row.addWidget(github_link)
        version_row.addStretch()
        layout.addLayout(version_row)

        layout.addSpacing(12)

        # "Built on the shoulders of giants" section
        credits_header = QLabel("<b>Built on the shoulders of giants:</b>")
        credits_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credits_header.setStyleSheet("font-size: 13px; font-weight: bold;")
        layout.addWidget(credits_header)

        layout.addSpacing(10)

        # Helper to add a credit row: name link + optional byline + description
        def add_credit(name_html, desc, url):
            name_label = QLabel(name_html)
            name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_label.setTextFormat(Qt.TextFormat.RichText)
            name_label.setOpenExternalLinks(True)
            name_label.setStyleSheet("font-size: 12px;")
            layout.addWidget(name_label)
            if desc:
                desc_label = QLabel(desc)
                desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                desc_label.setStyleSheet("font-size: 11px; color: #aaa;")
                layout.addWidget(desc_label)
            layout.addSpacing(5)

        add_credit(
            '<a href="https://github.com/Swedemon/MzFightReporter">'
            '<b>MzFightReporter</b></a> by Swedemon',
            "The original Java WvW fight reporter that inspired this project",
            "https://github.com/Swedemon/MzFightReporter"
        )

        add_credit(
            '<a href="https://github.com/baaron4/GW2-Elite-Insights-Parser">'
            '<b>GW2 Elite Insights</b></a> by baaron4',
            "The parser that powers all log analysis",
            "https://github.com/baaron4/GW2-Elite-Insights-Parser"
        )

        add_credit(
            '<a href="https://www.deltaconnected.com/arcdps/">'
            '<b>ArcDPS</b></a> by deltaconnected',
            "The combat logging addon that makes all of this possible",
            "https://www.deltaconnected.com/arcdps/"
        )

        add_credit(
            '<a href="https://github.com/Plenyx/PlenBotLogUploader">'
            '<b>PlenBot Log Uploader</b></a> by Plenyx',
            "Log uploader and Discord reporter for GW2",
            "https://github.com/Plenyx/PlenBotLogUploader"
        )

        add_credit(
            '<a href="https://github.com/Drevarr/EVTC_parser">'
            '<b>EVTC Parser</b></a> by Drevarr',
            "Python EVTC parser and WvW stats aggregator",
            "https://github.com/Drevarr/EVTC_parser"
        )

        layout.addSpacing(15)

        tagline = QLabel(
            "SparkyBot is a community-built alternative for users who prefer "
            "a Python-based solution with a focus on reliability and ease of deployment."
        )
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline.setWordWrap(True)
        tagline.setStyleSheet("font-size: 11px; color: #888;")
        layout.addWidget(tagline)

        # Add the inner widget to the outer layout with stretches on both sides
        outer_layout.addStretch()
        outer_layout.addWidget(inner)
        outer_layout.addStretch()

        return widget