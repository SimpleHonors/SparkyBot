"""Main Settings Window for SparkyBot"""

import html
import re
import sys
import hashlib
import logging
import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QTabWidget,
    QLabel, QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QPushButton,
    QGroupBox, QFormLayout, QScrollArea, QSizePolicy,
    QComboBox, QFileDialog, QMessageBox, QProgressBar, QColorDialog,
    QTextEdit, QDialog, QDialogButtonBox, QListWidget, QListWidgetItem,
    QInputDialog, QRadioButton, QButtonGroup
)
from PySide6.QtGui import QColor, QIcon
from PySide6.QtCore import Qt, Signal, QTimer, QEvent
from pathlib import Path
from core import theme
from core.interop_catalog import INTEROP_PROJECTS
from core.update_flow import UpdateFlow
from core.version import VERSION
from core.discord_bot import normalize_webhook_url


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


class ProcessFilesWidget(QWidget):
    """Manual log-processing queue (the Process Files tab).

    The app controller drives processing exclusively through the method API
    (set_processing / show_progress / mark_file_result / finish_processing) —
    child widgets are an implementation detail and must not be poked from
    outside this class.
    """

    # Signal emitted when user clicks Process — sends list of Path objects
    process_requested = Signal(list)

    # Per-item data roles: the queued file's path string, and the outcome of
    # the last processing pass (True/False; unset while pending). Outcome
    # state lives HERE, not in the visible text prefix, so the display can
    # change without breaking row bookkeeping.
    PATH_ROLE = Qt.ItemDataRole.UserRole
    RESULT_ROLE = Qt.ItemDataRole.UserRole + 1

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
        theme.mark_hint(header)
        layout.addWidget(header)

        # Drop zone — QLabel[dropzone="true"] in the central QSS;
        # drag hover flips the "drag" state property.
        self.drop_label = QLabel("Drag & drop .evtc / .zevtc files here\nor use Browse below")
        self.drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_label.setMinimumHeight(120)
        self.drop_label.setProperty("dropzone", True)
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

    # ------------------------------------------------------------------
    # Method API for the app controller
    # ------------------------------------------------------------------

    def add_files(self, paths):
        """Queue log files programmatically (Home drag-drop routes here).
        Duplicates are ignored, same as the drop zone."""
        for path in paths:
            self._add_file(str(path))

    def set_processing(self, active: bool):
        """Lock the Process button during a run; restore it afterwards
        (enabled only while the queue is non-empty)."""
        if active:
            self.process_btn.setEnabled(False)
        else:
            self.process_btn.setEnabled(self.file_list.count() > 0)

    def show_progress(self, index: int, total: int, filename: str):
        """Show per-file progress while the controller works the queue."""
        self.status_label.setText(f"Processing {index} of {total}: {filename}")

    def mark_file_result(self, file_path, success: bool):
        """Record a file's outcome on its queue row (and display it)."""
        # Normalize to resolve slash differences between Path objects and stored strings
        target = str(Path(str(file_path)).resolve())
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            stored = str(Path(item.data(self.PATH_ROLE)).resolve())
            if stored == target:
                item.setData(self.RESULT_ROLE, success)
                item.setText(Path(str(file_path)).name)
                item.setToolTip(
                    f"{Path(str(file_path)).name}: "
                    f"{'processed' if success else 'failed'}")
                break

    def finish_processing(self, total: int):
        """End-of-run bookkeeping: drop succeeded rows, keep failures."""
        self.status_label.setText(f"Done — processed {total} file(s)")

        # Remove successfully processed files by their recorded outcome (never
        # by parsing the visible text), in reverse so indices don't shift.
        for i in reversed(range(self.file_list.count())):
            item = self.file_list.item(i)
            if item and item.data(self.RESULT_ROLE) is True:
                self.file_list.takeItem(i)

        self.set_processing(False)

    # ------------------------------------------------------------------
    # Internal queue handling
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(('.evtc', '.zevtc')):
                    event.acceptProposedAction()
                    theme.set_state(self.drop_label, "drag")
                    return
        event.ignore()

    def dragLeaveEvent(self, event):
        theme.set_state(self.drop_label, None)

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
            if Path(folder).exists():
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
            if self.file_list.item(i).data(self.PATH_ROLE) == path:
                return
        item = QListWidgetItem(Path(path).name)
        item.setData(self.PATH_ROLE, path)
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
            paths.append(Path(item.data(self.PATH_ROLE)))
        if paths:
            self.process_requested.emit(paths)


class SettingsWindow(QWidget):
    """Main settings window with tabs for different configuration sections"""

    settings_changed = Signal()
    watcher_toggled = Signal()

    # Signals for thread-safe UI updates from background threads
    sig_status_text = Signal(str)
    sig_button_state = Signal(str, bool)
    sig_progress = Signal(bool, int)
    sig_progress_value = Signal(int)
    sig_ei_status_refresh = Signal()
    sig_ei_latest = Signal(str)
    sig_sparkybot_status = Signal(str)
    sig_sparkybot_latest = Signal(str)

    # Thread-safe UI signals for test/refresh operations
    _sig_models_result = Signal(list, str)      # models, source
    _sig_ai_test_done = Signal(str, bool)        # message, success
    _sig_ai_test_progress = Signal(str)          # staged probe status
    _sig_ai_apply = Signal(dict)                 # reasoning settings to apply
    _sig_twitch_test_done = Signal(str, bool)    # message, success
    _sig_tts_test_done = Signal(str, bool)       # message, success

    # Calibration tab signals (background EI import)
    _sig_calib_status = Signal(str)              # status text
    _sig_calib_progress = Signal(int, int)       # value, maximum (max 0 hides)
    _sig_calib_import_done = Signal(int, int)    # imported_ok, total

    def __init__(self, config, parent=None, update_flow=None):
        super().__init__(parent)
        self.config = config
        # Window-free update engine. The app controller passes its own so the
        # launch check and this window share one flow; standalone construction
        # (tests, direct use) gets a private instance.
        self.update_flow = update_flow if update_flow is not None else UpdateFlow(config, parent=self)
        # Release data armed by a successful check — while set, the morphing
        # update button is in "Download & Install Update" mode.
        self._sparkybot_release_data = None
        self._sparkybot_latest_version = None
        self.setWindowTitle("SparkyBot Settings")
        # No minimum/fit-all-tabs sizing here anymore: the MainWindow shell
        # owns window geometry and embeds this widget as a page. (The old
        # 13-tab-label width computation forced a ~1000px window.)

        # Set window icon to sbtray.ico
        icon_path = Path(__file__).parent.parent / "assets" / "sbtray.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._setup_ui()
        self._load_settings()
        self._connect_thread_signals()

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
        tabs.addTab(self._create_calibration_tab(), "Calibration")
        tabs.addTab(self._create_raid_report_settings_tab(), "Raid Report Settings")

        from core.raid_report_wiring import build_raid_report_tab
        about_idx = tabs.count()  # About hasn't been added yet — insert just before it
        tabs.insertTab(about_idx, build_raid_report_tab(self.config, parent=self), "Raid Report")
        self.about_widget = self._create_about_tab()
        tabs.addTab(self.about_widget, "About")

        # GitHub status checks run the first time the update surface is
        # actually shown (the Settings dialog's Updates page) — never at
        # construction. run_update_checks_once() is the single entry point.
        self._updates_checked = False

        layout.addWidget(tabs)

        # Bottom buttons
        button_layout = QHBoxLayout()

        self.start_button = QPushButton("Start Watcher")
        self.start_button.setMinimumHeight(40)
        # Primary CTA; set_watcher_state() flips the "running" state property
        # (green start / red stop) via the central QSS.
        theme.set_widget_class(self.start_button, "primary")
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
        self.discord_webhook_name1 = QLineEdit()
        self.discord_webhook_name1.setPlaceholderText("e.g. Main WvW")
        form.addRow("Destination 1 name:", self.discord_webhook_name1)
        form.addRow("Destination 1 webhook:", self.discord_webhook)

        self.discord_webhook_label = QLineEdit()
        self.discord_webhook_label.setPlaceholderText("SparkyBot")
        form.addRow("Webhook Label:", self.discord_webhook_label)

        # Thumbnail icon file
        thumb_layout = QHBoxLayout()
        self.guild_icon = QLineEdit()
        self.guild_icon.setPlaceholderText("assets/wvw_icon.png")
        self.guild_icon_browse_btn = QPushButton("Browse...")
        self.guild_icon_browse_btn.clicked.connect(self._browse_guild_icon)
        thumb_layout.addWidget(self.guild_icon)
        thumb_layout.addWidget(self.guild_icon_browse_btn)
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
        self.discord_webhook2.setPlaceholderText("https://discord.com/api/webhooks/...")
        self.discord_webhook_name2 = QLineEdit()
        self.discord_webhook_name2.setPlaceholderText("e.g. Raid Reports")
        form.addRow("Destination 2 name:", self.discord_webhook_name2)
        form.addRow("Destination 2 webhook:", self.discord_webhook2)

        self.discord_webhook3 = QLineEdit()
        self.discord_webhook3.setPlaceholderText("https://discord.com/api/webhooks/...")
        self.discord_webhook_name3 = QLineEdit()
        self.discord_webhook_name3.setPlaceholderText("e.g. Officers")
        form.addRow("Destination 3 name:", self.discord_webhook_name3)
        form.addRow("Destination 3 webhook:", self.discord_webhook3)

        self.active_webhook = QComboBox()
        self.raid_report_webhook = QComboBox()
        form.addRow("Fight reports:", self.active_webhook)
        form.addRow("Raid reports:", self.raid_report_webhook)

        routing_help = QLabel(
            "Use one destination for everything, or send end-of-run Raid "
            "Reports somewhere else.")
        routing_help.setWordWrap(True)
        form.addRow("", routing_help)

        for field in (
                self.discord_webhook, self.discord_webhook2,
                self.discord_webhook3, self.discord_webhook_name1,
                self.discord_webhook_name2, self.discord_webhook_name3):
            field.textChanged.connect(self._refresh_discord_destinations)

        layout.addWidget(group)

        # Options
        options_group = QGroupBox("Options")
        options_layout = QVBoxLayout(options_group)

        self.enable_discord = QCheckBox("Enable Discord Bot")
        self.enable_discord.setChecked(True)
        options_layout.addWidget(self.enable_discord)

        layout.addWidget(options_group)

        # Twitch Integration group box
        self.twitch_group = QGroupBox("Twitch Integration")
        twitch_group = self.twitch_group
        twitch_layout = QFormLayout(twitch_group)

        self.enable_twitch = QCheckBox("Enable Twitch Bot")
        # AI-off default wording; the Settings dialog upgrades it to the
        # "and AI commentary" form only while AI features are on (LAW #2).
        self.enable_twitch.setToolTip("Posts fight summaries to a Twitch chat channel.")
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

        self.twitch_tls_note = QLabel(
            "Disabling TLS sends your OAuth token in plaintext over port 6667. "
            "Only disable this if TLS connections fail due to firewall or network restrictions."
        )
        self.twitch_tls_note.setWordWrap(True)
        theme.mark_hint(self.twitch_tls_note)
        twitch_layout.addRow("", self.twitch_tls_note)

        self.twitch_help_link = QLabel(
            'Get a token at <a href="https://twitchtokengenerator.com">twitchtokengenerator.com</a>'
        )
        self.twitch_help_link.setOpenExternalLinks(True)
        theme.mark_hint(self.twitch_help_link)
        twitch_layout.addRow("", self.twitch_help_link)

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

    def _refresh_discord_destinations(self):
        """Show configured webhook slots by their optional short names."""
        fight_selected = self.active_webhook.currentData()
        raid_selected = self.raid_report_webhook.currentData()
        urls = (
            self.discord_webhook.text().strip(),
            self.discord_webhook2.text().strip(),
            self.discord_webhook3.text().strip(),
        )
        names = (
            self.discord_webhook_name1.text().strip(),
            self.discord_webhook_name2.text().strip(),
            self.discord_webhook_name3.text().strip(),
        )

        self.active_webhook.blockSignals(True)
        self.raid_report_webhook.blockSignals(True)
        self.active_webhook.clear()
        self.raid_report_webhook.clear()
        self.raid_report_webhook.addItem("Same as fight reports", 0)
        for index, (url, name) in enumerate(zip(urls, names), start=1):
            if not url:
                continue
            label = name or f"Destination {index}"
            self.active_webhook.addItem(label, index)
            self.raid_report_webhook.addItem(label, index)
        if not self.active_webhook.count():
            self.active_webhook.addItem("Destination 1", 1)

        fight_index = self.active_webhook.findData(fight_selected)
        self.active_webhook.setCurrentIndex(max(0, fight_index))
        raid_index = self.raid_report_webhook.findData(raid_selected)
        self.raid_report_webhook.setCurrentIndex(max(0, raid_index))
        self.active_webhook.blockSignals(False)
        self.raid_report_webhook.blockSignals(False)

    def _browse_guild_icon(self):
        """Browse for the thumbnail/guild icon image."""
        from PySide6.QtWidgets import QFileDialog
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
        use_tls = self.twitch_use_tls.isChecked()

        import threading
        def _test():
            try:
                from core.twitch_bot import TwitchBot
                bot = TwitchBot(token, channel, use_tls=use_tls)
                bot.send_message("SparkyBot Twitch connection test — if you see this, it works!")
                bot.close()
                self._sig_twitch_test_done.emit("Message sent successfully.", True)
            except Exception as e:
                self._sig_twitch_test_done.emit(f"Connection failed: {e}", False)

        threading.Thread(target=_test, daemon=True).start()

    def _on_twitch_test_done(self, message: str, success: bool):
        """Slot: display Twitch test result (main thread)."""
        self.twitch_test_status.setText(message)
        self.twitch_test_btn.setEnabled(True)

    def _update_color_preview(self):
        """Update the color preview button's background (data-driven color)."""
        theme.set_swatch_color(self.color_preview, self._current_embed_color)

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
        self.log_folder_browse_btn = QPushButton("Browse...")
        self.log_folder_browse_btn.clicked.connect(lambda: self._browse_folder(self.log_folder))
        log_layout.addWidget(self.log_folder)
        log_layout.addWidget(self.log_folder_browse_btn)
        form.addRow("Log Folder:", log_layout)

        layout.addWidget(group)

        # GW2EI settings
        group = QGroupBox("Elite Insights")
        form = QFormLayout(group)

        gw2ei_layout = QHBoxLayout()
        self.gw2ei_exe = QLineEdit()
        self.gw2ei_exe.setPlaceholderText("Path to GuildWars2EliteInsights-CLI.exe")
        self.gw2ei_browse_btn = QPushButton("Browse...")
        self.gw2ei_browse_btn.clicked.connect(self._browse_gw2ei_exe)
        gw2ei_layout.addWidget(self.gw2ei_exe)
        gw2ei_layout.addWidget(self.gw2ei_browse_btn)
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
        from core.providers import PRESETS

        scroll = QScrollArea()
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.ai_group = QGroupBox("AI Fight Analysis")
        group = self.ai_group
        form = QFormLayout(group)

        # Master switch — re-homed by the Settings dialog onto the
        # Application page as "AI features" (design-B §5.2, same config key).
        self.enable_ai = QCheckBox("Enable AI features (fight commentary and voice)")
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
        self.ai_max_tokens.setRange(100, 8000)
        self.ai_max_tokens.setValue(450)
        form.addRow("Max Tokens:", self.ai_max_tokens)

        # API Timeout
        self.ai_timeout = QSpinBox()
        self.ai_timeout.setRange(10, 120)
        self.ai_timeout.setValue(30)
        self.ai_timeout.setSuffix(" seconds")
        form.addRow("API Timeout:", self.ai_timeout)

        # Disable Thinking / Reasoning Mode
        self.ai_disable_thinking = QCheckBox("Disable Thinking / Reasoning Mode")
        self.ai_disable_thinking.setToolTip(
            "Disable chain-of-thought reasoning for models that support it.\n"
            "Enable this if AI responses are being truncated because the model\n"
            "spends its entire token budget on internal reasoning.\n\n"
            "Affects: Kimi K2.5/K2.6, DeepSeek, Gemini, and any model\n"
            "routed through OpenRouter that supports reasoning control."
        )
        form.addRow("", self.ai_disable_thinking)

        # System Prompt — mode selector (Default vs Custom) with preview
        prompt_layout = QVBoxLayout()

        self.ai_prompt_mode = QComboBox()
        self.ai_prompt_mode.addItems(["Default (SparkyBot Analyst)", "Custom"])
        self.ai_prompt_mode.currentTextChanged.connect(self._on_prompt_mode_changed)
        prompt_layout.addWidget(self.ai_prompt_mode)

        prompt_note = QLabel(
            "Default: SparkyBot builds the prompt dynamically each call with vocabulary dice rolls, "
            "pre-computed fight analysis, and tracker-driven variety. "
            "Custom: Your prompt is used as-is for the system message. "
            "Fight data, pre-analysis, and vocabulary are still injected into the user message."
        )
        prompt_note.setWordWrap(True)
        theme.mark_hint(prompt_note)
        prompt_layout.addWidget(prompt_note)

        self.ai_system_prompt = QTextEdit()
        self.ai_system_prompt.setMaximumHeight(80)
        self.ai_system_prompt.setPlaceholderText("Using default SparkyBot analyst prompt")
        prompt_layout.addWidget(self.ai_system_prompt)

        self.ai_edit_prompt_btn = QPushButton("Edit System Prompt...")
        self.ai_edit_prompt_btn.clicked.connect(self._edit_system_prompt)
        prompt_layout.addWidget(self.ai_edit_prompt_btn)

        form.addRow("System Prompt:", prompt_layout)

        # Test button
        self.ai_test_btn = QPushButton("Test Connection")
        self.ai_test_btn.clicked.connect(self._test_ai_connection)
        form.addRow("", self.ai_test_btn)

        self.ai_test_status = QLabel("")
        self.ai_test_status.setWordWrap(True)
        form.addRow("", self.ai_test_status)

        # Vocabulary
        self.ai_vocab_group = QGroupBox("Vocabulary")
        vocab_group = self.ai_vocab_group
        vocab_form = QFormLayout()

        vocab_note = QLabel(
            "These sliders control how often SparkyBot reaches for its predefined catchphrases "
            "versus making up something original on the fly.\n\n"
            "Lower = more original freestyle commentary. Higher = more predefined terms.\n"
            "At 0%, SparkyBot will never use terms from that category and will always improvise. "
            "At 100%, every available term in the category is offered to the AI each time."
        )
        vocab_note.setWordWrap(True)
        theme.mark_hint(vocab_note)
        vocab_form.addRow(vocab_note)

        # Shock row: mode + spinbox + edit
        self.ai_vocab_shock_mode = QComboBox()
        self.ai_vocab_shock_mode.addItems(["Default", "Custom"])
        self.ai_vocab_shock_mode.setFixedWidth(80)
        self.ai_vocab_shock = QSpinBox()
        self.ai_vocab_shock.setRange(0, 100)
        self.ai_vocab_shock.setSuffix("%")
        self.ai_vocab_shock.setValue(33)
        self.ai_vocab_shock.setEnabled(False)
        self.ai_vocab_shock.setToolTip(
            "Shock exclamations like HOLY SHIT, WHAT THE HELL, JESUS CHRIST.\n"
            "These are dramatic reactions to extreme outcomes."
        )
        self.shock_edit_btn = QPushButton("Edit...")
        self.shock_edit_btn.setFixedWidth(60)
        self.shock_edit_btn.setEnabled(False)
        self.shock_edit_btn.clicked.connect(lambda: self._edit_vocabulary("shock"))
        self.ai_vocab_shock_mode.currentTextChanged.connect(
            lambda mode: self._on_vocab_mode_changed(
                "shock", mode, self.ai_vocab_shock, self.shock_edit_btn
            )
        )
        shock_row = QHBoxLayout()
        shock_row.addWidget(self.ai_vocab_shock_mode)
        shock_row.addWidget(self.ai_vocab_shock, 1)
        shock_row.addWidget(self.shock_edit_btn)
        vocab_form.addRow("Shock Exclamations:", shock_row)

        # Positive row: mode + spinbox + edit
        self.ai_vocab_positive_mode = QComboBox()
        self.ai_vocab_positive_mode.addItems(["Default", "Custom"])
        self.ai_vocab_positive_mode.setFixedWidth(80)
        self.ai_vocab_positive = QSpinBox()
        self.ai_vocab_positive.setRange(0, 100)
        self.ai_vocab_positive.setSuffix("%")
        self.ai_vocab_positive.setValue(33)
        self.ai_vocab_positive.setEnabled(False)
        self.ai_vocab_positive.setToolTip(
            "Hype terms like ABSOLUTE MONSTERS, YEET YEET DELETE, RIDE 'EM LIKE A PONY.\n"
            "Used to celebrate wins and standout performances."
        )
        self.pos_edit_btn = QPushButton("Edit...")
        self.pos_edit_btn.setFixedWidth(60)
        self.pos_edit_btn.setEnabled(False)
        self.pos_edit_btn.clicked.connect(lambda: self._edit_vocabulary("positive"))
        self.ai_vocab_positive_mode.currentTextChanged.connect(
            lambda mode: self._on_vocab_mode_changed(
                "positive", mode, self.ai_vocab_positive, self.pos_edit_btn
            )
        )
        pos_row = QHBoxLayout()
        pos_row.addWidget(self.ai_vocab_positive_mode)
        pos_row.addWidget(self.ai_vocab_positive, 1)
        pos_row.addWidget(self.pos_edit_btn)
        vocab_form.addRow("Hype Terms:", pos_row)

        # Negative row: mode + spinbox + edit
        self.ai_vocab_negative_mode = QComboBox()
        self.ai_vocab_negative_mode.addItems(["Default", "Custom"])
        self.ai_vocab_negative_mode.setFixedWidth(80)
        self.ai_vocab_negative = QSpinBox()
        self.ai_vocab_negative.setRange(0, 100)
        self.ai_vocab_negative.setSuffix("%")
        self.ai_vocab_negative.setValue(33)
        self.ai_vocab_negative.setEnabled(False)
        self.ai_vocab_negative.setToolTip(
            "Negative terms like fed to the wolves, TOIGHT LIKE A TIGER.\n"
            "Used for losses and when the squad underperforms."
        )
        self.neg_edit_btn = QPushButton("Edit...")
        self.neg_edit_btn.setFixedWidth(60)
        self.neg_edit_btn.setEnabled(False)
        self.neg_edit_btn.clicked.connect(lambda: self._edit_vocabulary("negative"))
        self.ai_vocab_negative_mode.currentTextChanged.connect(
            lambda mode: self._on_vocab_mode_changed(
                "negative", mode, self.ai_vocab_negative, self.neg_edit_btn
            )
        )
        neg_row = QHBoxLayout()
        neg_row.addWidget(self.ai_vocab_negative_mode)
        neg_row.addWidget(self.ai_vocab_negative, 1)
        neg_row.addWidget(self.neg_edit_btn)
        vocab_form.addRow("Negative Terms:", neg_row)

        # Gates row: mode + spinbox + edit
        self.ai_vocab_gates_mode = QComboBox()
        self.ai_vocab_gates_mode.addItems(["Default", "Custom"])
        self.ai_vocab_gates_mode.setFixedWidth(80)
        self.ai_vocab_gates = QSpinBox()
        self.ai_vocab_gates.setRange(0, 100)
        self.ai_vocab_gates.setSuffix("%")
        self.ai_vocab_gates.setValue(33)
        self.ai_vocab_gates.setEnabled(False)
        self.ai_vocab_gates.setToolTip(
            "Situational slang like Bags, Rallybot, Siege Humping, Mudda Fucka.\n"
            "These only trigger when specific fight conditions are met,\n"
            "like a decisive loss, PUGs feeding rallies, or enemy using siege."
        )
        self.gates_edit_btn = QPushButton("Edit...")
        self.gates_edit_btn.setFixedWidth(60)
        self.gates_edit_btn.setEnabled(False)
        self.gates_edit_btn.clicked.connect(lambda: self._edit_vocabulary("gates"))
        self.ai_vocab_gates_mode.currentTextChanged.connect(
            lambda mode: self._on_vocab_mode_changed(
                "gates", mode, self.ai_vocab_gates, self.gates_edit_btn
            )
        )
        gates_row = QHBoxLayout()
        gates_row.addWidget(self.ai_vocab_gates_mode)
        gates_row.addWidget(self.ai_vocab_gates, 1)
        gates_row.addWidget(self.gates_edit_btn)
        vocab_form.addRow("Situational Slang:", gates_row)

        vocab_group.setLayout(vocab_form)
        layout.addWidget(vocab_group)

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
        self.tts_general_group = QGroupBox("General")
        general_group = self.tts_general_group
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

        self.tts_discord_attach_note = QLabel(
            "Discord mobile does not support inline audio playback for file attachments."
        )
        self.tts_discord_attach_note.setWordWrap(True)
        theme.mark_hint(self.tts_discord_attach_note)
        general_form.addRow("", self.tts_discord_attach_note)

        self.tts_volume = QSpinBox()
        self.tts_volume.setRange(0, 100)
        self.tts_volume.setSuffix("%")
        self.tts_volume.setValue(80)
        self.tts_volume.setToolTip("Local playback volume (0 = muted, 100 = full).")
        general_form.addRow("Volume:", self.tts_volume)

        layout.addWidget(general_group)

        # -- Provider --
        self.tts_provider_group = QGroupBox("Provider")
        provider_group = self.tts_provider_group
        provider_form = QFormLayout(provider_group)

        self.tts_provider = QComboBox()
        self.tts_provider.addItems(["edge", "elevenlabs", "local"])
        self.tts_provider.setToolTip(
            "edge: Microsoft neural voices via edge-tts (free, no API key, online)\n"
            "elevenlabs: ElevenLabs API (highest quality, API key required)\n"
            "local: self-hosted OpenAI-compatible speech server\n"
            "       (voice cloning, free, private — e.g. Chatterbox)"
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

        # Local server fields (OpenAI-compatible /v1/audio/speech endpoint)
        self.tts_local_url_label = QLabel("Server URL:")
        self.tts_local_url = QLineEdit()
        self.tts_local_url.setPlaceholderText("http://127.0.0.1:5820")
        self.tts_local_url.setToolTip(
            "Base URL of a self-hosted OpenAI-compatible speech server\n"
            "(POST {url}/v1/audio/speech). Voices and uploaded samples are\n"
            "listed from GET {url}/v1/voices when the server supports it."
        )
        provider_form.addRow(self.tts_local_url_label, self.tts_local_url)

        self.tts_local_voice = QComboBox()
        self.tts_local_voice.setEditable(True)
        self.tts_local_voice.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.tts_local_voice.setPlaceholderText("pick or type a voice name")
        self.tts_local_refresh_btn = QPushButton("Refresh")
        self.tts_local_refresh_btn.setFixedWidth(70)
        self.tts_local_refresh_btn.clicked.connect(self._refresh_local_voices)
        self.tts_local_upload_btn = QPushButton("Upload Sample...")
        self.tts_local_upload_btn.setFixedWidth(110)
        self.tts_local_upload_btn.setToolTip(
            "Upload a short (>= 3 s) clean speech recording to the server.\n"
            "It becomes a pickable voice that is cloned at generation time.\n"
            "Only upload voices you own or have consent to clone."
        )
        self.tts_local_upload_btn.clicked.connect(self._upload_local_sample)
        local_row = QHBoxLayout()
        local_row.addWidget(self.tts_local_voice, 1)
        local_row.addWidget(self.tts_local_refresh_btn)
        local_row.addWidget(self.tts_local_upload_btn)
        self.tts_local_voice_label = QLabel("Voice:")
        provider_form.addRow(self.tts_local_voice_label, local_row)

        layout.addWidget(provider_group)

        # -- Test --
        self.tts_test_group = QGroupBox("Test")
        test_group = self.tts_test_group
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
        is_local = provider.lower() == "local"
        self.tts_local_url_label.setVisible(is_local)
        self.tts_local_url.setVisible(is_local)
        self.tts_local_voice_label.setVisible(is_local)
        self.tts_local_voice.setVisible(is_local)
        self.tts_local_refresh_btn.setVisible(is_local)
        self.tts_local_upload_btn.setVisible(is_local)
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
                self.tts_test_status.setText(f"{len(ordered)} voices loaded.")
            except ImportError:
                self.tts_test_status.setText("edge-tts is not installed.")
            except Exception as e:
                self.tts_test_status.setText(f"Failed to fetch voices: {e}")
            finally:
                self.tts_refresh_voices_btn.setEnabled(True)
                self.tts_refresh_voices_btn.setText("Refresh Voices")

        threading.Thread(target=_fetch, daemon=True).start()

    def _refresh_local_voices(self):
        """Fetch voice/sample names from the local server's /v1/voices."""
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
                current = self.tts_local_voice.currentText().strip()
                self.tts_local_voice.blockSignals(True)
                self.tts_local_voice.clear()
                self.tts_local_voice.addItems(names)
                if current and current in names:
                    self.tts_local_voice.setCurrentText(current)
                elif current:
                    self.tts_local_voice.setEditText(current)
                self.tts_local_voice.blockSignals(False)
                self.tts_test_status.setText(f"{len(names)} voices/samples loaded.")
            except Exception as e:
                self.tts_test_status.setText(f"Failed to fetch voices: {e}")
            finally:
                self.tts_local_refresh_btn.setEnabled(True)

        threading.Thread(target=_fetch, daemon=True).start()

    def _upload_local_sample(self):
        """Upload a reference recording; it becomes a cloneable voice."""
        base_url = self.tts_local_url.text().strip().rstrip("/")
        if not base_url:
            self.tts_test_status.setText("Set the local server URL first.")
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Choose a voice sample (>= 3 s of clean speech)",
            "", "Audio files (*.wav *.mp3 *.m4a *.flac);;All files (*)",
        )
        if not file_path:
            return
        import os as _os
        import re as _re
        raw_default = _os.path.splitext(_os.path.basename(file_path))[0]
        # Pre-clean to a valid voice id (letters/digits/._-); the server does
        # the same, but showing the cleaned name up front avoids surprises.
        default_name = _re.sub(r"[^A-Za-z0-9._-]+", "-", raw_default).strip("-._") or "sample"
        name, ok = QInputDialog.getText(
            self, "Sample name",
            "Name for this voice sample (letters, digits, . _ - only):",
            text=default_name,
        )
        if not ok or not name.strip():
            return
        name = _re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-._")
        if not name:
            self.tts_test_status.setText("Sample name needs at least one letter or digit.")
            return
        self.tts_local_upload_btn.setEnabled(False)
        self.tts_test_status.setText("Uploading sample...")
        import threading

        def _upload():
            try:
                import requests
                with open(file_path, "rb") as f:
                    response = requests.post(
                        f"{base_url}/v1/voices/samples",
                        files={"file": f}, data={"name": name}, timeout=60,
                    )
                response.raise_for_status()
                voice = response.json().get("voice", f"sample:{name}")
                self.tts_local_voice.setEditText(voice)
                self.tts_test_status.setText(
                    f"Sample uploaded as {voice} — it will be cloned at generation time."
                )
            except Exception as e:
                detail = ""
                resp = getattr(e, "response", None)
                if resp is not None:
                    try:
                        detail = resp.json().get("detail", "")
                    except Exception:
                        detail = (resp.text or "")[:200]
                self.tts_test_status.setText(
                    f"Upload failed: {detail or e}"
                )
            finally:
                self.tts_local_upload_btn.setEnabled(True)

        threading.Thread(target=_upload, daemon=True).start()

    def _test_tts(self):
        self.tts_test_btn.setEnabled(False)
        self.tts_test_status.setText("Generating audio...")

        # Capture all widget values on the main thread before spawning background work
        cfg_provider = self.tts_provider.currentText()
        cfg_edge_voice = self.tts_edge_voice.currentText().strip() or "en-GB-RyanNeural"
        cfg_el_api_key = self.tts_elevenlabs_api_key.text().strip()
        cfg_el_voice_id = self.tts_elevenlabs_voice_id.text().strip() or "JBFqnCBsd6RMkjVDRZzb"
        cfg_el_model = self.tts_elevenlabs_model.currentText().strip() or "eleven_multilingual_v2"
        cfg_el_stability = self.tts_el_stability.value() / 100.0
        cfg_el_similarity = self.tts_el_similarity.value() / 100.0
        cfg_el_style = self.tts_el_style.value() / 100.0
        cfg_el_speaker_boost = self.tts_el_speaker_boost.isChecked()
        cfg_el_speed = self.tts_el_speed.value()
        cfg_local_url = self.tts_local_url.text().strip()
        cfg_local_voice = self.tts_local_voice.currentText().strip()
        cfg_volume = self.tts_volume.value()
        tts_client = getattr(self, '_tts_client', None)

        import threading
        def _run():
            try:
                from core.tts import generate_tts_bytes

                class _Cfg:
                    tts_provider = cfg_provider
                    tts_edge_voice = cfg_edge_voice
                    tts_elevenlabs_api_key = cfg_el_api_key
                    tts_elevenlabs_voice_id = cfg_el_voice_id
                    tts_elevenlabs_model = cfg_el_model
                    tts_elevenlabs_stability = cfg_el_stability
                    tts_elevenlabs_similarity_boost = cfg_el_similarity
                    tts_elevenlabs_style = cfg_el_style
                    tts_elevenlabs_speaker_boost = cfg_el_speaker_boost
                    tts_elevenlabs_speed = cfg_el_speed
                    tts_local_url = cfg_local_url
                    tts_local_voice = cfg_local_voice

                audio_bytes = generate_tts_bytes(
                    "SparkyBot TTS is working. Let's get those bags.", _Cfg()
                )
                if not audio_bytes:
                    self._sig_tts_test_done.emit("Audio generation failed — check logs.", False)
                    return

                if tts_client is not None:
                    tts_client.update_volume(cfg_volume)
                    tts_client.speak_from_bytes(audio_bytes)
                    self._sig_tts_test_done.emit("Audio queued — check your speakers.", True)
                else:
                    self._sig_tts_test_done.emit(
                        "Audio generated successfully. Save and restart to enable local playback.", True
                    )
            except Exception as e:
                self._sig_tts_test_done.emit(f"Test failed: {e}", False)

        threading.Thread(target=_run, daemon=True).start()

    def _on_tts_test_done(self, message: str, success: bool):
        """Slot: display TTS test result (main thread)."""
        self.tts_test_status.setText(message)
        self.tts_test_btn.setEnabled(True)

    def _on_ai_provider_changed(self, provider_name: str):
        """Fill in base URL and model from preset, then refresh model list."""
        from core.providers import PRESETS
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
        current_provider = self.ai_provider.currentText()

        import threading
        def _fetch():
            from core.providers import PRESETS, fetch_models
            from core.ai_analyst import FightAnalyst
            models = FightAnalyst.fetch_models(base_url, api_key)
            source = "API"

            if not models:
                # Fallback to preset model list
                preset = PRESETS.get(current_provider, {})
                models = preset.get("models", [])
                source = "preset"

            self._sig_models_result.emit(models, source)

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_models_result(self, models: list, source: str):
        """Slot: apply fetched model list to combo box (main thread)."""
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

    def _test_ai_connection(self):
        """Probe the AI connection both ways and auto-apply the reasoning fix."""
        from core.reasoning_probe import run_probe, make_real_factory, format_report
        from core.reasoning_settings_apply import apply_report_to_config

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

        base_url = self.ai_base_url.text()
        api_key = self.ai_api_key.text()
        model = self.ai_model.currentText()
        system_prompt = self.ai_system_prompt.toPlainText() or None
        user_budget = self.ai_max_tokens.value()
        timeout = self.ai_timeout.value()

        self.ai_test_status.setText("Testing…")
        self.ai_test_btn.setEnabled(False)
        self._last_report = None

        import threading

        def _run_test():
            try:
                factory = make_real_factory(base_url, api_key, model, system_prompt)
                report = run_probe(
                    factory, test_summary, user_budget=user_budget,
                    base_url=base_url, model=model, timeout=timeout,
                    progress=lambda m: self._sig_ai_test_progress.emit(m),
                )
                self._last_report = report
                if report.auto_applicable and not report.failure:
                    applied = apply_report_to_config(report)
                    self._sig_ai_apply.emit(applied)
                self._sig_ai_test_done.emit(format_report(report), not report.failure)
            except Exception as exc:  # noqa: BLE001
                self._sig_ai_test_done.emit(f"Test failed: {exc}", False)

        threading.Thread(target=_run_test, daemon=True).start()

    def _apply_reasoning_settings(self, applied: dict):
        """Slot (main thread): push probe-derived reasoning settings into the widgets."""
        self.ai_disable_thinking.setChecked(applied["ai_disable_thinking"])
        self.ai_max_tokens.setValue(applied["ai_max_tokens"])
        self._reasoning_strategy = applied["ai_reasoning_strategy"]  # saved on Save

    def _on_ai_test_done(self, message: str, success: bool):
        """Slot: display AI test result (main thread)."""
        self.ai_test_status.setText(message)
        self.ai_test_btn.setEnabled(True)

        report = getattr(self, "_last_report", None)
        if report is not None and getattr(report, "needs_choice", False):
            self._show_reasoning_choice(report)

    def _show_reasoning_choice(self, report):
        """Offer the user OFF vs ON reasoning alternatives with an Apply button."""
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
                self._apply_reasoning_settings(
                    apply_report_to_config(report, alt_key=selected)
                )
            dialog.accept()

        apply_btn.clicked.connect(_do_apply)
        dialog.exec()

    def _edit_vocabulary(self, initial_tab: str = "shock"):
        """Open a structured dialog for editing vocabulary terms.

        Args:
            initial_tab: Category to pre-select when opening ("shock", "positive", "negative", "gates").
        """
        import json
        from core.ai_analyst import VocabularyConfig

        vocab_path = self.config.home_dir / "sparkybot_vocabulary.json"
        try:
            raw = json.loads(vocab_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = VocabularyConfig._default_vocabulary()

        # --- Dialog shell ---
        dialog = QDialog(self)
        dialog.setWindowTitle("Edit Vocabulary")
        dialog.setMinimumSize(700, 520)
        dlg_layout = QVBoxLayout(dialog)

        # --- Tab widget, one tab per category ---
        cat_tabs = QTabWidget()
        category_map = {}  # tab_index -> category name
        list_widgets = {}  # category name -> QListWidget

        for cat in ("shock", "positive", "negative", "gates"):
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            tab_layout.setContentsMargins(4, 4, 4, 4)

            list_widget = QListWidget()
            list_widget.setAlternatingRowColors(True)
            list_widget.setWordWrap(True)
            list_widget.setSpacing(2)
            list_widgets[cat] = list_widget
            tab_layout.addWidget(list_widget, stretch=1)

            # Load items — show term + description preview
            for entry in raw.get(cat, []):
                display = entry.get("term", "?")
                if entry.get("alt"):
                    display += f'  /  {entry["alt"]}'

                # Gates use 'condition' for the description line, others use 'desc'
                if cat == "gates":
                    subtitle = entry.get("condition", "")
                else:
                    subtitle = entry.get("desc", "")

                if subtitle:
                    display += f'\n    {subtitle[:80]}'

                item = QListWidgetItem(display)
                item.setData(Qt.ItemDataRole.UserRole, dict(entry))
                item.setToolTip(entry.get("condition", "") if cat == "gates" else entry.get("desc", ""))
                list_widget.addItem(item)

            # Description note for Gates tab
            if cat == "gates":
                note = QLabel(
                    "Slang terms that trigger when specific fight conditions are met "
                    "(e.g., siege detected, decisive loss, PUGs feeding rallies)"
                )
                note.setWordWrap(True)
                theme.mark_hint(note)
                tab_layout.addWidget(note)

            # --- Button row ---
            btn_row = QHBoxLayout()

            btn_add = QPushButton("Add")
            btn_edit = QPushButton("Edit")
            btn_remove = QPushButton("Remove")
            btn_up = QPushButton("▲")
            btn_down = QPushButton("▼")
            for btn in (btn_add, btn_edit, btn_remove, btn_up, btn_down):
                btn.setMaximumWidth(70)
            btn_row.addWidget(btn_add)
            btn_row.addWidget(btn_edit)
            btn_row.addWidget(btn_remove)
            btn_row.addStretch()
            btn_row.addWidget(btn_up)
            btn_row.addWidget(btn_down)
            tab_layout.addLayout(btn_row)

            tab_label = "Situational Slang" if cat == "gates" else cat.capitalize()
            idx = cat_tabs.addTab(tab, tab_label)
            category_map[idx] = cat

            # Wire buttons
            btn_add.clicked.connect(lambda _, c=cat: self._vocab_add_term(c, list_widgets[c], raw))
            btn_edit.clicked.connect(lambda _, c=cat: self._vocab_edit_term(c, list_widgets[c], raw))
            btn_remove.clicked.connect(lambda _, c=cat: self._vocab_remove_term(list_widgets[c], raw))
            btn_up.clicked.connect(lambda _, c=cat: self._vocab_move_term(list_widgets[c], raw, -1))
            btn_down.clicked.connect(lambda _, c=cat: self._vocab_move_term(list_widgets[c], raw, 1))

        dlg_layout.addWidget(cat_tabs, stretch=1)

        # Pre-select the requested category tab
        tab_index = {"shock": 0, "positive": 1, "negative": 2, "gates": 3}.get(initial_tab, 0)
        cat_tabs.setCurrentIndex(tab_index)

        # --- Bottom buttons ---
        bottom_row = QHBoxLayout()
        btn_defaults = QPushButton("Reset to Defaults")
        btn_defaults.setMaximumWidth(140)
        bottom_row.addWidget(btn_defaults)
        bottom_row.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        bottom_row.addWidget(buttons)
        dlg_layout.addLayout(bottom_row)

        def _reset_to_defaults():
            defaults = VocabularyConfig._default_vocabulary()
            raw.clear()
            raw.update({k: list(v) for k, v in defaults.items()})
            for cat, lw in list_widgets.items():
                lw.clear()
                for entry in raw.get(cat, []):
                    item = QListWidgetItem()
                    label = entry.get("term", "?")
                    if entry.get("alt"):
                        label += f" / {entry['alt']}"
                    item.setText(label)
                    item.setData(Qt.ItemDataRole.UserRole, entry)
                    lw.addItem(item)

        btn_defaults.clicked.connect(_reset_to_defaults)

        def _save():
            # Write back to JSON
            for cat, lw in list_widgets.items():
                raw[cat] = []
                for i in range(lw.count()):
                    entry = lw.item(i).data(Qt.ItemDataRole.UserRole)
                    if entry:
                        raw[cat].append(entry)
            try:
                vocab_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
                # Mark user_modified so future updates prompt instead of auto-merging
                from core.ai_analyst import VocabularyConfig
                vc = VocabularyConfig(config_path=vocab_path)
                vc.mark_modified()
                dialog.accept()
            except OSError as e:
                QMessageBox.warning(dialog, "Error", f"Could not write vocabulary file:\n{e}")

        buttons.button(QDialogButtonBox.StandardButton.Save).clicked.connect(_save)
        buttons.rejected.connect(dialog.reject)
        dialog.exec()

    def _vocab_add_term(self, category: str, list_widget: QListWidget, raw: dict):
        """Add a new term to the given category via _vocab_term_dialog."""
        result = self._vocab_term_dialog(category)
        if result:
            entry = dict(result)
            # Auto-generate pattern from term name
            term_text = entry.get("term", "")
            entry["pattern"] = re.escape(term_text).replace(r"\ ", r"\s+")
            if entry.get("alt"):
                entry["alt_pattern"] = re.escape(entry["alt"]).replace(r"\ ", r"\s+")

            display = entry["term"]
            if entry.get("alt"):
                display += f'  /  {entry["alt"]}'
            subtitle = entry.get("condition", "") if category == "gates" else entry.get("desc", "")
            if subtitle:
                display += f'\n    {subtitle[:80]}'
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(entry.get("condition", "") if category == "gates" else entry.get("desc", ""))
            list_widget.addItem(item)

    def _vocab_edit_term(self, category: str, list_widget: QListWidget, raw: dict):
        """Edit the currently selected term in the given category."""
        row = list_widget.currentRow()
        if row < 0:
            return
        current = list_widget.item(row).data(Qt.ItemDataRole.UserRole)
        result = self._vocab_term_dialog(category, current)
        if result:
            entry = dict(result)
            term_text = entry.get("term", "")
            entry["pattern"] = re.escape(term_text).replace(r"\ ", r"\s+")
            if entry.get("alt"):
                entry["alt_pattern"] = re.escape(entry["alt"]).replace(r"\ ", r"\s+")

            list_widget.item(row).setData(Qt.ItemDataRole.UserRole, entry)
            display = entry["term"]
            if entry.get("alt"):
                display += f'  /  {entry["alt"]}'
            subtitle = entry.get("condition", "") if category == "gates" else entry.get("desc", "")
            if subtitle:
                display += f'\n    {subtitle[:80]}'
            list_widget.item(row).setText(display)
            list_widget.item(row).setToolTip(entry.get("condition", "") if category == "gates" else entry.get("desc", ""))

    def _vocab_remove_term(self, list_widget: QListWidget, raw: dict):
        """Remove the currently selected term."""
        row = list_widget.currentRow()
        if row >= 0:
            list_widget.takeItem(row)

    def _vocab_move_term(self, list_widget: QListWidget, raw: dict, direction: int):
        """Move the selected term up (-1) or down (+1) in the list."""
        row = list_widget.currentRow()
        if row < 0:
            return
        new_row = row + direction
        if 0 <= new_row < list_widget.count():
            item = list_widget.takeItem(row)
            list_widget.insertItem(new_row, item)
            list_widget.setCurrentRow(new_row)

    def _vocab_term_dialog(self, category: str, existing: dict = None) -> dict:
        import re

        dialog = QDialog(self)
        dialog.setWindowTitle("Edit Term" if existing else "Add Term")
        dialog.setMinimumWidth(450)
        layout = QFormLayout(dialog)

        # Term name
        term_input = QLineEdit()
        term_input.setPlaceholderText("e.g. YEET YEET DELETE")
        if existing:
            term_input.setText(existing.get("term", ""))
        layout.addRow("Term:", term_input)

        # Alternate term
        alt_input = QLineEdit()
        alt_input.setPlaceholderText("Optional alternate wording")
        if existing:
            alt_input.setText(existing.get("alt", ""))
        layout.addRow("Also matches:", alt_input)

        # Description - this is the key field
        desc_input = QTextEdit()
        desc_input.setMaximumHeight(80)
        desc_input.setPlaceholderText(
            "Describe when SparkyBot should use this term.\n"
            "e.g. 'When the squad absolutely steamrolls the enemy'"
        )
        if existing:
            desc_input.setPlainText(existing.get("desc", ""))
        layout.addRow("When to use it:", desc_input)

        # Caps - simplified labels
        caps_input = QComboBox()
        caps_input.addItems(["ALL CAPS always", "Normal (caps optional)"])
        if existing:
            if existing.get("caps") == "always":
                caps_input.setCurrentIndex(0)
            else:
                caps_input.setCurrentIndex(1)
        layout.addRow("Style:", caps_input)

        # Gate-specific fields with friendlier labels
        condition_input = None
        instruction_input = None
        if category == "gates":
            condition_input = QTextEdit()
            condition_input.setMaximumHeight(60)
            condition_input.setPlaceholderText(
                "What fight conditions trigger this term?\n"
                "e.g. 'Decisive loss' or 'Enemy used siege weapons'"
            )
            if existing:
                condition_input.setPlainText(existing.get("condition", ""))
            layout.addRow("Triggers when:", condition_input)

            instruction_input = QTextEdit()
            instruction_input.setMaximumHeight(60)
            instruction_input.setPlaceholderText(
                "What should SparkyBot say or do?\n"
                "e.g. 'Mock the enemy for hiding behind catapults'"
            )
            if existing:
                instruction_input.setPlainText(existing.get("instruction", ""))
            layout.addRow("SparkyBot should:", instruction_input)

        # NO pattern field shown - auto-generated from term name

        # Validation
        validation = QLabel("")
        theme.set_state(validation, "error")
        layout.addRow("", validation)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        layout.addRow(buttons)

        out = {}

        def _on_accept():
            term = term_input.text().strip()
            if not term:
                validation.setText("Term is required.")
                return
            if category == "gates" and not condition_input.toPlainText().strip():
                validation.setText("Triggers when is required.")
                return
            if category == "gates" and not instruction_input.toPlainText().strip():
                validation.setText("SparkyBot should is required.")
                return

            result = {
                "term": term,
                "caps": "always" if caps_input.currentIndex() == 0 else "optional",
            }
            if alt_input.text().strip():
                result["alt"] = alt_input.text().strip()
            if desc_input.toPlainText().strip():
                result["desc"] = desc_input.toPlainText().strip()
            if category == "gates":
                result["condition"] = condition_input.toPlainText().strip()
                result["instruction"] = instruction_input.toPlainText().strip()
            out.clear()
            out.update(result)
            dialog.accept()

        buttons.button(QDialogButtonBox.StandardButton.Ok).clicked.connect(_on_accept)
        buttons.rejected.connect(dialog.reject)

        if dialog.exec() == QDialog.DialogCode.Accepted and out:
            return out
        return None

    def _prompt_vocab_update(self, vc):
        """Ask user how to handle an updated default vocabulary."""
        diff = vc.get_update_diff()

        detail_parts = []
        if diff["added"]:
            detail_parts.append(f"New terms: {', '.join(diff['added'])}")
        if diff["removed"]:
            detail_parts.append(f"Removed from defaults: {', '.join(diff['removed'])}")
        detail_text = "\n".join(detail_parts) if detail_parts else "Internal improvements."

        msg = QMessageBox(self)
        msg.setWindowTitle("Vocabulary Update Available")
        msg.setText("SparkyBot learned some new words!")
        msg.setInformativeText(
            f"{detail_text}\n\n"
            "Merge will add them to your vocabulary without touching "
            "anything you've already customized."
        )

        merge_btn = msg.addButton("Merge New Terms", QMessageBox.ButtonRole.AcceptRole)
        skip_btn = msg.addButton("Skip", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(skip_btn)
        msg.exec()

        if msg.clickedButton() == merge_btn:
            vc.apply_default_update(merge=True)
            QMessageBox.information(self, "Vocabulary Updated",
                f"Merged {len(diff['added'])} new term(s). "
                "Your existing terms and weights were preserved."
            )

    def _on_vocab_mode_changed(self, category: str, mode: str, spinbox: QSpinBox, edit_btn: QPushButton):
        """Handle Default/Custom toggle for a vocabulary category."""
        if mode == "Default":
            # Restore default weight and terms for this category
            from core.ai_analyst import VocabularyConfig
            vocab_path = self.config.home_dir / "sparkybot_vocabulary.json"
            vc = VocabularyConfig(config_path=vocab_path)
            defaults = VocabularyConfig._default_vocabulary()

            vc._raw[category] = defaults[category]
            vc._raw.setdefault("weights", {})[category] = 0.33

            # Remove from custom_categories so update check skips this category
            custom = vc._raw.get("custom_categories", [])
            if category in custom:
                custom.remove(category)

            vc._compile_patterns()
            vc._write_defaults()

            spinbox.setValue(33)
            spinbox.setEnabled(False)
            edit_btn.setEnabled(False)
        else:
            # Switching to Custom — mark category as customized
            from core.ai_analyst import VocabularyConfig
            vocab_path = self.config.home_dir / "sparkybot_vocabulary.json"
            vc = VocabularyConfig(config_path=vocab_path)

            vc._raw.setdefault("custom_categories", [])
            if category not in vc._raw["custom_categories"]:
                vc._raw["custom_categories"].append(category)
            vc._write_defaults()

            spinbox.setEnabled(True)
            edit_btn.setEnabled(True)

    def _prompt_system_prompt_update(self):
        """Notify custom-prompt users that the default prompt has been updated."""
        from core.ai_analyst import FightAnalyst, DEFAULT_PROMPT_VERSION, DEFAULT_PROMPT_CHANGELOG

        # Build the changelog text from all versions the user missed
        changelog_parts = []
        for v in range(self.config.ai_prompt_version + 1, DEFAULT_PROMPT_VERSION + 1):
            entry = DEFAULT_PROMPT_CHANGELOG.get(v)
            if entry:
                changelog_parts.append(entry["title"] + ":")
                for change in entry["changes"]:
                    changelog_parts.append(f"  • {change}")
                if entry.get("reason"):
                    changelog_parts.append("")
                    changelog_parts.append(entry["reason"])

        changelog_text = "\n".join(changelog_parts) if changelog_parts else "Various improvements."

        msg = QMessageBox(self)
        msg.setWindowTitle("Prompt Update Available")
        msg.setText("SparkyBot's default prompt has been improved!")
        msg.setInformativeText(
            f"{changelog_text}\n\n"
            "You're using a custom system prompt, so nothing was changed automatically. "
            "You can switch to the new default or keep yours."
        )

        switch_btn = msg.addButton("Switch to New Default", QMessageBox.ButtonRole.AcceptRole)
        view_btn = msg.addButton("View New Default", QMessageBox.ButtonRole.HelpRole)
        keep_btn = msg.addButton("Keep Mine", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(keep_btn)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked == switch_btn:
            self.config.update('AI', 'aiSystemPrompt', '')
            self.config.update('AI', 'aiPromptVersion', str(DEFAULT_PROMPT_VERSION))
            self.ai_prompt_mode.setCurrentText("Default (SparkyBot Analyst)")
            new_default = FightAnalyst._core_system_prompt() + FightAnalyst._rules_section()
            self.ai_system_prompt.setPlainText(new_default)
            theme.set_read_only(self.ai_system_prompt, True)
        elif clicked == view_btn:
            new_default = FightAnalyst._core_system_prompt() + FightAnalyst._rules_section()
            view_dialog = QDialog(self)
            view_dialog.setWindowTitle("New Default Prompt")
            view_dialog.setMinimumSize(600, 400)
            view_layout = QVBoxLayout(view_dialog)
            viewer = QTextEdit()
            viewer.setReadOnly(True)
            viewer.setPlainText(new_default)
            view_layout.addWidget(viewer)
            close_btn = QPushButton("Close")
            close_btn.clicked.connect(view_dialog.accept)
            view_layout.addWidget(close_btn)
            view_dialog.exec()
            # After viewing, mark version as seen so we don't nag again
            self.config.update('AI', 'aiPromptVersion', str(DEFAULT_PROMPT_VERSION))
        else:
            # Keep mine — mark version as seen so we don't ask again
            self.config.update('AI', 'aiPromptVersion', str(DEFAULT_PROMPT_VERSION))

    def _on_prompt_mode_changed(self, mode: str):
        """Toggle system prompt between default and custom."""
        if mode.startswith("Default"):
            theme.set_read_only(self.ai_system_prompt, True)
            from core.ai_analyst import FightAnalyst
            self.ai_system_prompt.setPlainText(
                FightAnalyst._core_system_prompt() + FightAnalyst._rules_section()
            )
        else:
            theme.set_read_only(self.ai_system_prompt, False)
            # If switching to custom and the text is still the default, clear it
            # so the user starts fresh
            from core.ai_analyst import FightAnalyst
            default_text = FightAnalyst._core_system_prompt() + FightAnalyst._rules_section()
            if self.ai_system_prompt.toPlainText() == default_text:
                self.ai_system_prompt.clear()

    def _edit_system_prompt(self):
        """Open a larger dialog for editing the system prompt."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QDialogButtonBox, QLabel
        from PySide6.QtCore import Qt

        dialog = QDialog(self)
        dialog.setWindowTitle("Edit System Prompt")
        dialog.setMinimumSize(700, 500)

        # Apply same icon as main window
        icon_path = Path(__file__).parent.parent / "assets" / "sbtray.ico"
        if icon_path.exists():
            from PySide6.QtGui import QIcon
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
            editor.setPlainText(
                FightAnalyst._core_system_prompt() + FightAnalyst._rules_section()
            )

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
        import core.apppaths

        if core.apppaths.is_frozen():
            return f'"{sys.executable}"'

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
        self.sparkybot_update_group = QGroupBox("SparkyBot")
        sparkybot_group = self.sparkybot_update_group
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
        self.ei_update_group = QGroupBox("Elite Insights Parser")
        ei_group = self.ei_update_group
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

        # Initial status checks are deferred to the first view of the
        # Settings dialog's Updates page — see run_update_checks_once.

        return widget

    def run_update_checks_once(self):
        """Fire the SparkyBot/EI GitHub status checks the first time the
        update surface becomes visible (the Settings dialog calls this when
        the Updates page is first opened). Construction never checks."""
        if self._updates_checked:
            return
        self._updates_checked = True
        self._check_sparkybot_status()
        self._check_ei_status()

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
                # Try tag_name first (most consistent from GitHub), fall back to release name
                raw_version = data.get("tag_name", "") or data.get("name", "")
                match = re.search(r'(\d+\.\d+(?:\.\d+)*)', raw_version)
                latest_version = match.group(1) if match else ""

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
        """Morphing update button: check first; once a release is armed, install it."""
        if self._sparkybot_release_data is not None:
            release_data = self._sparkybot_release_data
            version = self._sparkybot_latest_version or "unknown"
            self._sparkybot_release_data = None
            self.update_sparkybot_button.setEnabled(False)
            self.update_sparkybot_button.setText("Downloading...")
            self.update_flow.start_update(release_data, version)
        else:
            self.update_sparkybot_button.setEnabled(False)
            self.update_sparkybot_button.setText("Checking...")
            self.update_flow.check_now()

    # -- UpdateFlow → Updates tab slots (signals fire from worker threads and
    # -- queue back to the GUI thread, so touching widgets here is safe) -----

    def _on_update_flow_progress(self, text: str):
        self.sparkybot_status_label.setText(text)

    def _on_update_flow_available(self, latest_version: str, release_data):
        """Arm the morphing button with the release the flow found."""
        self._sparkybot_release_data = release_data
        self._sparkybot_latest_version = latest_version
        self.sparkybot_status_label.setText(
            f"Update available: v{VERSION} → v{latest_version}"
        )
        self.update_sparkybot_button.setText("Download & Install Update")
        self.update_sparkybot_button.setEnabled(True)

    def _on_update_flow_not_available(self, message: str):
        self.sparkybot_status_label.setText(message)
        self.update_sparkybot_button.setText("Already Up to Date")
        self.update_sparkybot_button.setEnabled(True)

    def _on_update_flow_error(self, message: str):
        self.sparkybot_status_label.setText(message)
        self.update_sparkybot_button.setText("Check for SparkyBot Update")
        self.update_sparkybot_button.setEnabled(True)

    def _on_update_flow_staged(self, version: str):
        """Update staged into .update_pending/ — restart applies it. The app
        controller owns the restart prompt (it hooks sig_staged directly)."""
        self.sparkybot_status_label.setText(
            f"Update v{version} downloaded. Restart SparkyBot to finish installing."
        )
        self.update_sparkybot_button.setText("Restart Required")
        self.update_sparkybot_button.setEnabled(False)

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
        self.sig_sparkybot_latest.connect(
            lambda t: self.sparkybot_latest_label.setText(t)
        )

        # UpdateFlow → Updates tab (the window is a thin view over the
        # window-free flow owned by the app controller)
        self.update_flow.sig_progress.connect(self._on_update_flow_progress)
        self.update_flow.sig_available.connect(self._on_update_flow_available)
        self.update_flow.sig_not_available.connect(self._on_update_flow_not_available)
        self.update_flow.sig_error.connect(self._on_update_flow_error)
        self.update_flow.sig_staged.connect(self._on_update_flow_staged)

        # Test/refresh operation signals
        self._sig_models_result.connect(self._on_models_result)
        self._sig_ai_test_done.connect(self._on_ai_test_done)
        self._sig_ai_test_progress.connect(lambda m: self.ai_test_status.setText(m))
        self._sig_ai_apply.connect(self._apply_reasoning_settings)
        self._sig_twitch_test_done.connect(self._on_twitch_test_done)
        self._sig_tts_test_done.connect(self._on_tts_test_done)

        # Calibration signals
        self._sig_calib_status.connect(lambda t: self.calib_status_label.setText(t))
        self._sig_calib_progress.connect(self._on_calib_progress)
        self._sig_calib_import_done.connect(self._on_calib_import_done)

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

    def _load_settings(self, prompt_updates: bool = True):
        """Load settings from config into UI.

        prompt_updates=False (the Settings dialog's reload-on-open path)
        skips the vocabulary/prompt update-available dialogs — those may
        only fire once, at first construction.
        """
        # Discord
        self.discord_webhook.setText(self.config.discord_webhook)
        self.discord_webhook_name1.setText(self.config.discord_webhook_name1)
        self.discord_webhook_label.setText(self.config.discord_webhook_label)
        self.discord_webhook2.setText(self.config.discord_webhook2)
        self.discord_webhook_name2.setText(self.config.discord_webhook_name2)
        self.discord_webhook3.setText(self.config.discord_webhook3)
        self.discord_webhook_name3.setText(self.config.discord_webhook_name3)
        self._refresh_discord_destinations()
        fight_index = self.active_webhook.findData(
            self.config.active_discord_webhook)
        self.active_webhook.setCurrentIndex(max(0, fight_index))
        raid_index = self.raid_report_webhook.findData(
            self.config.raid_report_discord_webhook)
        self.raid_report_webhook.setCurrentIndex(max(0, raid_index))
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
        self.ai_timeout.setValue(self.config.ai_timeout)
        self.ai_disable_thinking.setChecked(self.config.ai_disable_thinking)
        self._reasoning_strategy = self.config.ai_reasoning_strategy or ""
        if self.config.ai_system_prompt:
            self.ai_prompt_mode.setCurrentText("Custom")
            self.ai_system_prompt.setPlainText(self.config.ai_system_prompt)
        else:
            self.ai_prompt_mode.setCurrentText("Default (SparkyBot Analyst)")
            from core.ai_analyst import FightAnalyst
            self.ai_system_prompt.setPlainText(
                FightAnalyst._core_system_prompt() + FightAnalyst._rules_section()
            )
            theme.set_read_only(self.ai_system_prompt, True)

        # AI Vocabulary Weights — load custom_categories and sync mode/weight state
        from core.ai_analyst import VocabularyConfig
        vocab_path = self.config.home_dir / "sparkybot_vocabulary.json"
        vc = VocabularyConfig(config_path=vocab_path)
        custom_cats = vc._raw.get("custom_categories", [])

        for cat, mode_combo, spinbox, edit_btn in [
            ("shock", self.ai_vocab_shock_mode, self.ai_vocab_shock, self.shock_edit_btn),
            ("positive", self.ai_vocab_positive_mode, self.ai_vocab_positive, self.pos_edit_btn),
            ("negative", self.ai_vocab_negative_mode, self.ai_vocab_negative, self.neg_edit_btn),
            ("gates", self.ai_vocab_gates_mode, self.ai_vocab_gates, self.gates_edit_btn),
        ]:
            spinbox.setValue(int(getattr(self.config, f"ai_vocab_weight_{cat}") * 100))
            if cat in custom_cats:
                mode_combo.setCurrentText("Custom")
                spinbox.setEnabled(True)
                edit_btn.setEnabled(True)
            else:
                mode_combo.setCurrentText("Default")
                spinbox.setEnabled(False)
                edit_btn.setEnabled(False)

        if prompt_updates:
            # Check for vocabulary updates
            try:
                from core.ai_analyst import VocabularyConfig
                vocab_path = self.config.home_dir / "sparkybot_vocabulary.json"
                vc = VocabularyConfig(config_path=vocab_path)
                if vc.update_available():
                    if vc.is_user_modified():
                        self._prompt_vocab_update(vc)
                    else:
                        # User never customized, silently merge
                        vc.apply_default_update(merge=True)
            except Exception as e:
                logging.getLogger(__name__).warning("Could not check vocabulary updates: %s", e)

            # Check if user is on a custom prompt and the default has been updated
            try:
                from core.ai_analyst import DEFAULT_PROMPT_VERSION
                if self.config.ai_system_prompt and self.config.ai_prompt_version < DEFAULT_PROMPT_VERSION:
                    self._prompt_system_prompt_update()
            except Exception as e:
                logging.getLogger(__name__).warning("Could not check prompt updates: %s", e)

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
        self.tts_local_url.setText(self.config.tts_local_url)
        self.tts_local_voice.setEditText(self.config.tts_local_voice)
        self._on_tts_provider_changed(self.config.tts_provider)

        # Raid Report settings
        self.raidreport_viewer_html.setText(self.config.raidreport_viewer_html)
        self.raidreport_output_dir.setText(self.config.raidreport_output_dir)
        self.raidreport_cache_enabled.setChecked(self.config.raidreport_cache_enabled)
        self.raidreport_poison_tab.setChecked(self.config.raidreport_poison_tab)
        self.raidreport_always_zip.setChecked(self.config.raidreport_always_zip)
        manual = self.config.raidreport_run_mode == 'manual'
        self.runmode_manual.setChecked(manual)
        self.runmode_run_button.setChecked(not manual)
        self.run_autopost.setChecked(self.config.run_auto_post)

    def _save_settings(self) -> tuple:
        """Persist every settings widget into config (no dialogs).

        The single writer for the whole settings surface — the modal
        Settings dialog's OK/Apply and the legacy Save button both route
        here. Returns (saved, relaunch_needed).
        """
        webhook_fields = (
            ("Destination 1", self.discord_webhook.text()),
            ("Destination 2", self.discord_webhook2.text()),
            ("Destination 3", self.discord_webhook3.text()),
        )
        normalized = [normalize_webhook_url(value) for _name, value in webhook_fields]
        invalid = [webhook_fields[index][0] for index, value in enumerate(normalized)
                   if value is None]
        active_index = int(self.active_webhook.currentData() or 1) - 1
        active_value = webhook_fields[active_index][1].strip()
        if self.enable_discord.isChecked() and not active_value:
            invalid.append("Active destination")
        if invalid:
            self._last_save_error = (
                f"{', '.join(invalid)} has an incomplete webhook. "
                "A token alone is missing the webhook ID. In Discord, open "
                "Server Settings > Integrations > Webhooks, then choose "
                "Copy Webhook URL. You can paste a full URL or ID/token."
            )
            return False, False

        self._last_save_error = ""
        self.discord_webhook.setText(normalized[0])
        self.discord_webhook2.setText(normalized[1])
        self.discord_webhook3.setText(normalized[2])
        cfg = self.config.update
        cfg('Discord', 'discordWebhook', self.discord_webhook.text())
        cfg('Discord', 'discordWebhookName1', self.discord_webhook_name1.text())
        cfg('Discord', 'discordWebhookLabel', self.discord_webhook_label.text())
        cfg('Discord', 'discordWebhook2', self.discord_webhook2.text())
        cfg('Discord', 'discordWebhookName2', self.discord_webhook_name2.text())
        cfg('Discord', 'discordWebhook3', self.discord_webhook3.text())
        cfg('Discord', 'discordWebhookName3', self.discord_webhook_name3.text())
        cfg('Discord', 'activeDiscordWebhook',
            str(self.active_webhook.currentData() or 1))
        cfg('Discord', 'raidReportDiscordWebhook',
            str(self.raid_report_webhook.currentData() or 0))
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
        cfg('AI', 'aiTimeout', str(self.ai_timeout.value()))
        cfg('AI', 'aiDisableThinking', str(self.ai_disable_thinking.isChecked()).lower())
        cfg('AI', 'aiReasoningStrategy', getattr(self, '_reasoning_strategy', ''))
        if self.ai_prompt_mode.currentText().startswith("Default"):
            cfg('AI', 'aiSystemPrompt', '')
            from core.ai_analyst import DEFAULT_PROMPT_VERSION
            cfg('AI', 'aiPromptVersion', str(DEFAULT_PROMPT_VERSION))
        else:
            cfg('AI', 'aiSystemPrompt', self.ai_system_prompt.toPlainText())
        cfg('AI', 'aiVocabWeightShock', str(self.ai_vocab_shock.value()))
        cfg('AI', 'aiVocabWeightPositive', str(self.ai_vocab_positive.value()))
        cfg('AI', 'aiVocabWeightNegative', str(self.ai_vocab_negative.value()))
        cfg('AI', 'aiVocabWeightGates', str(self.ai_vocab_gates.value()))

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
        cfg('TTS', 'ttsLocalUrl', self.tts_local_url.text().strip())
        cfg('TTS', 'ttsLocalVoice', self.tts_local_voice.currentText().strip())

        # Raid Report
        cfg('RaidReport', 'raidreportViewerHtml', self.raidreport_viewer_html.text())
        cfg('RaidReport', 'raidreportOutputDir', self.raidreport_output_dir.text())
        cfg('RaidReport', 'raidreportCacheEnabled', str(self.raidreport_cache_enabled.isChecked()).lower())
        cfg('RaidReport', 'raidreportPoisonTab', str(self.raidreport_poison_tab.isChecked()).lower())
        cfg('RaidReport', 'raidreportAlwaysZip', str(self.raidreport_always_zip.isChecked()).lower())
        cfg('RaidReport', 'runMode',
            'manual' if self.runmode_manual.isChecked() else 'run-button')
        cfg('RaidReport', 'runAutoPost',
            str(self.run_autopost.isChecked()).lower())

        # Write to file and reload attributes
        if not self.config.save():
            return False, False

        self.settings_changed.emit()

        # Relaunch notice if console or startup settings changed
        relaunch_needed = (
            self.hide_console.isChecked() != self._initial_hide_console
            or self.start_with_windows.isChecked() != self._initial_start_with_windows
        )
        if relaunch_needed:
            # Update so we don't nag again on next save
            self._initial_hide_console = self.hide_console.isChecked()
            self._initial_start_with_windows = self.start_with_windows.isChecked()
        return True, relaunch_needed

    def _on_save_clicked(self):
        """Legacy Save Settings button: persist, then modal feedback."""
        saved, relaunch_needed = self._save_settings()
        if saved:
            QMessageBox.information(self, "Settings", "Settings saved successfully!")
            if relaunch_needed:
                QMessageBox.information(
                    self,
                    "Relaunch Required",
                    "Console window and Windows startup changes will take effect the next time SparkyBot is launched.",
                )
        else:
            QMessageBox.warning(
                self, "Settings",
                getattr(self, "_last_save_error", "") or "Failed to save settings.",
            )

    def _on_start_clicked(self):
        """Toggle watcher start/stop"""
        self.watcher_toggled.emit()

    def set_watcher_state(self, running: bool):
        """Update UI to reflect watcher state (QSS keys off the state prop)"""
        if running:
            self.start_button.setText("Stop Watcher")
            theme.set_state(self.start_button, "running")
        else:
            self.start_button.setText("Start Watcher")
            theme.set_state(self.start_button, "stopped")

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

    # (The Process Files queue is owned by the MainWindow shell — this class
    # stopped building its duplicate when settings moved into the dialog.)

    # ------------------------------------------------------------------
    # Calibration tab — "Calibrate to Your Guild"
    # ------------------------------------------------------------------
    # Soft confidence floor: axes pooled from fewer observations than this get
    # a "thin data" warning in the preview. It NEVER blocks a recalibration.
    CALIB_CONFIDENCE_FLOOR = 30

    # Friendly metric names for the preview rows (raw axis key -> label). The raw
    # key is still stashed in each row's UserRole for reference.
    _CALIB_AXIS_NAMES = {
        'dps':              "Damage/sec",
        'healing':          "Healing/sec",
        'cleanses_pm':      "Cleanses/min",
        'strips_pm':        "Boon Strips/min",
        'cc_pm':            "Hard CC/min",
        'burst_4s':         "Burst Damage (4s)",
        'downs_dealt_pm':   "Downs/min",
        'kills_pm':         "Kills/min",
        'stability_uptime': "Stability Uptime",
        'downed_damage':    "Damage to Downed",
        'downed_healing':   "Healing to Downed",
        'resurrects':       "Resurrects",
        'damage_taken':     "Damage Taken",
        'might_gen':        "Might Output",
        'quickness_gen':    "Quickness Output",
        'alacrity_gen':     "Alacrity Output",
        'protection_gen':   "Protection Output",
        'stability_gen':    "Stability Output",
    }

    def _calib_axis_label(self, axis: str) -> str:
        """Friendly display name for an axis key (falls back to a title-cased key)."""
        return self._CALIB_AXIS_NAMES.get(axis, axis.replace('_', ' ').title())

    def _set_calib_status(self, text: str, ok: bool | None = None):
        """Set the calibration status line, colored green (ok) / red (fail) / neutral."""
        if ok is True:
            theme.set_state(self.calib_status_label, "ok")
        elif ok is False:
            theme.set_state(self.calib_status_label, "error")
        else:
            theme.set_state(self.calib_status_label, None)
        self.calib_status_label.setText(text)

    def _create_calibration_tab(self) -> QWidget:
        """Recalibrate performance thresholds from your own guild's fights."""
        scroll = QScrollArea()
        widget = QWidget()
        layout = QVBoxLayout(widget)

        header = QLabel(
            "Tune SparkyBot's performance tiers to your own guild's fights. Fights "
            "are collected as the watcher runs; you can also import old logs."
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        # Collected fights
        corpus_group = QGroupBox("Collected Fights")
        corpus_layout = QVBoxLayout(corpus_group)
        self.calib_count_label = QLabel("0 fights collected")
        self.calib_count_label.setWordWrap(True)
        corpus_layout.addWidget(self.calib_count_label)
        layout.addWidget(corpus_group)

        # --- Import Logs group ---
        import_group = QGroupBox("Import Logs")
        import_form = QFormLayout(import_group)

        self.calib_concurrency = QComboBox()
        self.calib_concurrency.addItems(["1", "2", "4", "8", "16", "32"])
        self.calib_concurrency.setCurrentText("4")  # default
        self.calib_concurrency.setToolTip(
            "How many log files Elite Insights parses at once. Higher is faster but "
            "uses more CPU and RAM — high values (16/32) can spike both. Start at 4."
        )
        self.calib_concurrency.setFixedWidth(70)
        import_form.addRow("Import speed (parallel files):", self.calib_concurrency)

        self.calib_import_btn = QPushButton("Add Fight Logs...")
        theme.set_widget_class(self.calib_import_btn, "primary")
        self.calib_import_btn.setToolTip(
            "Select .evtc/.zevtc files to run through Elite Insights and add to the "
            "collected fights."
        )
        self.calib_import_btn.clicked.connect(self._calib_import_logs)
        import_form.addRow("", self.calib_import_btn)

        self.calib_progress = QProgressBar()
        self.calib_progress.setVisible(False)
        import_form.addRow("", self.calib_progress)

        layout.addWidget(import_group)

        # --- Apply Calibration group ---
        apply_group = QGroupBox("Apply Calibration")
        apply_layout = QVBoxLayout(apply_group)

        btn_row = QHBoxLayout()
        self.calib_recalibrate_btn = QPushButton("Recalibrate")
        self.calib_recalibrate_btn.setMinimumHeight(36)
        theme.set_widget_class(self.calib_recalibrate_btn, "primary")
        self.calib_recalibrate_btn.setToolTip(
            "Compute new thresholds from the collected fights and preview the change "
            "before applying. Nothing is overwritten until you confirm."
        )
        self.calib_recalibrate_btn.clicked.connect(lambda: self._calib_recalibrate())
        self.calib_reset_btn = QPushButton("Reset to Defaults")
        self.calib_reset_btn.setToolTip(
            "Discard your calibration and revert to SparkyBot's built-in thresholds."
        )
        self.calib_reset_btn.clicked.connect(self._calib_reset_defaults)
        btn_row.addWidget(self.calib_recalibrate_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.calib_reset_btn)
        apply_layout.addLayout(btn_row)

        self.calib_status_label = QLabel("")
        self.calib_status_label.setWordWrap(True)
        apply_layout.addWidget(self.calib_status_label)

        layout.addWidget(apply_group)
        layout.addStretch()

        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)

        # Initialize the collected-fights count + button enabled states.
        self._refresh_calib_count()
        return scroll

    def _calib_corpus_path(self) -> Path:
        return self.config.home_dir / "calibration_corpus.jsonl"

    def _calib_thresholds_path(self) -> Path:
        return self.config.home_dir / "calibration_thresholds.json"

    def _refresh_calib_count(self):
        """Refresh the fight count, the empty-state hint, and button enabled states."""
        from core.calibration import corpus_count
        n = corpus_count(self._calib_corpus_path())
        if n == 0:
            self.calib_count_label.setText(
                "No fights collected yet. Add old .evtc or .zevtc logs below, "
                "or leave the watcher running during fights. Once SparkyBot has "
                "some examples, it can tune its ratings to your guild."
            )
        else:
            self.calib_count_label.setText(f"{n} fight{'s' if n != 1 else ''} collected.")
        # Recalibrate only makes sense with at least one fight; Reset only when an
        # override actually exists.
        self.calib_recalibrate_btn.setEnabled(n > 0)
        self.calib_reset_btn.setEnabled(self._calib_thresholds_path().exists())

    def _on_calib_progress(self, value: int, maximum: int):
        """Slot: drive the calibration progress bar (main thread)."""
        if maximum <= 0:
            self.calib_progress.setVisible(False)
            return
        self.calib_progress.setVisible(True)
        self.calib_progress.setMaximum(maximum)
        self.calib_progress.setValue(value)

    def _on_calib_import_done(self, imported_ok: int, total: int):
        """Slot: import finished — re-enable buttons and refresh the count.

        Runs on the UI thread (it's a Qt signal slot, _sig_calib_import_done,
        emitted from the worker thread and delivered here via the event loop), so
        it can safely open the recalibration preview dialog.
        """
        self.calib_import_btn.setEnabled(True)
        self.calib_progress.setVisible(False)
        # _refresh_calib_count owns the Recalibrate/Reset enabled state.
        self._refresh_calib_count()
        failed = total - imported_ok
        msg = f"Imported {imported_ok} of {total} log(s)."
        if failed:
            msg += f" {failed} failed to parse (see logs)."
        if total and failed == 0:
            ok = True
        elif imported_ok == 0:
            ok = False
        else:
            ok = None
        self._set_calib_status(msg, ok=ok)

        # Auto-prompt recalibration once at least one fight was added — the user
        # shouldn't have to remember to click Recalibrate. This only ASKS: the
        # preview's Confirm/Deny still gates whether thresholds are written
        # (auto=True suppresses no-data pop-ups so a useless dialog never opens).
        if imported_ok >= 1:
            self._calib_recalibrate(auto=True)

    def _calib_import_logs(self):
        """Pick .evtc/.zevtc files and import them via Elite Insights (threaded)."""
        start_dir = ""
        try:
            log_folders = self.config.get_log_folders()
            if log_folders and Path(str(log_folders[0])).exists():
                start_dir = str(log_folders[0])
        except Exception:
            pass

        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Log Files to Import", start_dir,
            "ArcDPS Logs (*.evtc *.zevtc);;All Files (*)"
        )
        if not files:
            return

        self.calib_import_btn.setEnabled(False)
        self.calib_recalibrate_btn.setEnabled(False)
        self.calib_reset_btn.setEnabled(False)
        # Capture the concurrency on the UI thread (no widget access off-thread).
        try:
            concurrency = int(self.calib_concurrency.currentText())
        except (ValueError, AttributeError):
            concurrency = 4
        self.calib_status_label.setText(
            f"Importing {len(files)} log(s) through Elite Insights "
            f"({concurrency} at a time)..."
        )
        self._sig_calib_progress.emit(0, len(files))

        paths = [Path(f) for f in files]
        threading.Thread(
            target=self._calib_import_worker, args=(paths, concurrency), daemon=True
        ).start()

    def _calib_import_worker(self, paths, concurrency):
        """Background: parse logs concurrently through EI -> summary -> corpus.

        Runs on a worker thread (off the UI loop). The actual fan-out is the pure
        parallel_harvest helper; per-file work happens on pool threads and touches
        NO widgets — progress is marshaled back via Qt signals only.
        """
        import json as _json
        import uuid
        from core.gw2ei_invoker import GW2EIInvoker
        from core.fight_report import FightReport
        from core.calibration import append_summary, parallel_harvest

        invoker = GW2EIInvoker(self.config)
        corpus_path = self._calib_corpus_path()
        total = len(paths)

        def harvest_one(path):
            # Unique per-job EI config so concurrent parses don't share/clobber
            # the single wvwupload.conf (see GW2EIInvoker.parse_file).
            config_name = f"wvwupload_{uuid.uuid4().hex}.conf"
            json_file = invoker.parse_file(path, config_name=config_name)
            if not json_file:
                raise RuntimeError(f"Elite Insights produced no JSON for {path.name}")
            with open(json_file, "r", encoding="utf-8") as f:
                report_data = _json.load(f)
            report = FightReport(report_data)
            # append_summary holds its own lock — safe under concurrency.
            append_summary(report.get_ai_summary(), corpus_path)
            return path

        def on_progress(completed, _total, item):
            self._sig_calib_status.emit(f"Parsed {completed}/{total}: {item.name}")
            self._sig_calib_progress.emit(completed, total)

        results, errors = parallel_harvest(
            paths, harvest_one, concurrency, on_progress=on_progress
        )
        for item, exc in errors:
            logging.getLogger(__name__).warning(
                "Calibration import failed for %s: %s", getattr(item, "name", item), exc
            )

        self._sig_calib_import_done.emit(len(results), total)

    def _calib_recalibrate(self, auto: bool = False):
        """Compute proposed thresholds and show the side-by-side preview dialog.

        `auto=True` is used when an import auto-prompts recalibration: it suppresses
        the informational "no data" pop-ups (so a finished import with too little
        data doesn't spawn a useless dialog) and only opens the preview when there
        is actually something to propose. The manual Recalibrate button passes
        auto=False and keeps its existing feedback dialogs.
        """
        from PySide6.QtWidgets import QApplication
        from core.calibration import compute_thresholds, load_corpus, write_thresholds
        from core.performance_buckets import active_thresholds, reload_thresholds

        # load_corpus + compute_thresholds are synchronous and can be slow on a big
        # corpus; show a wait cursor so the window doesn't look frozen. Restore it
        # before any dialog/message box.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            summaries = load_corpus(self._calib_corpus_path())
            proposed, obs_counts = compute_thresholds(summaries) if summaries else ({}, {})
        finally:
            QApplication.restoreOverrideCursor()

        if not summaries:
            if not auto:
                QMessageBox.information(
                    self, "No Fights Collected",
                    "No fights have been collected yet. Let the watcher analyze some "
                    "fights, or import logs first."
                )
            return

        if not proposed:
            if not auto:
                QMessageBox.information(
                    self, "Not Enough Data",
                    "There aren't enough observations on any axis (need 5+) to compute "
                    "thresholds yet. Collect or import more fights."
                )
            return

        current = active_thresholds()
        fight_count = len(summaries)  # distinct fights pooled (one summary per fight)
        if self._show_recalibration_preview(current, proposed, obs_counts, fight_count):
            write_thresholds(proposed, self._calib_thresholds_path())
            reload_thresholds(self._calib_thresholds_path())
            thin = sum(1 for a in proposed if obs_counts.get(a, 0) < self.CALIB_CONFIDENCE_FLOOR)
            if thin:
                note = f" ({thin} {'axis' if thin == 1 else 'axes'} on thin data)"
            else:
                note = ""
            self._set_calib_status(f"Calibration applied{note}. Active immediately.", ok=True)
            self._refresh_calib_count()  # Reset button now has an override to remove
        else:
            self._set_calib_status("Recalibration discarded — thresholds unchanged.")

    # Tier labels shown as the per-tier delta columns.
    _CALIB_TIER_LABELS = ("p25", "p50", "p75", "p90", "p95")

    @staticmethod
    def _calib_num(v) -> str:
        """Human-readable number formatting for the delta cells (never sci notation)."""
        from core.calibration import format_threshold
        return format_threshold(v)

    def _show_recalibration_preview(self, current: dict, proposed: dict,
                                    obs_counts: dict, fight_count: int) -> bool:
        """Geeky per-tier delta view (current -> proposed, colored by direction).

        Returns True if the operator confirms. Rendering only — the delta math and
        the fight-count warning decision live in core.calibration (tier_delta /
        fight_count_warning), kept pure and tested there.
        """
        from core.calibration import AXES, tier_delta, fight_count_warning
        from core.performance_buckets import _BUCKET_LABELS
        from PySide6.QtWidgets import (
            QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
        )

        up_color = theme.color("ok")        # green: tier rose
        down_color = theme.color("error")   # red: tier dropped
        same_color = theme.color("neutral")  # gray: unchanged
        thin_color = theme.color("warn")    # amber: thin-obs metric flag

        dialog = QDialog(self)
        dialog.setWindowTitle("Recalibration Preview")
        # Comfortable default, but a small minimum so it can't open off-screen on a
        # 1366x768 laptop (columns stretch; tier headers wrap).
        dialog.resize(1000, 560)
        dialog.setMinimumSize(720, 420)
        dlg_layout = QVBoxLayout(dialog)

        # Baseline header — defaults vs a prior applied calibration.
        override_exists = self._calib_thresholds_path().exists()
        baseline = ("Comparing against your applied calibration" if override_exists
                    else "Comparing against built-in defaults")
        header = QLabel(
            f"<b>Calibrating from {fight_count} fight"
            f"{'s' if fight_count != 1 else ''}.</b> &nbsp; {baseline}."
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        dlg_layout.addWidget(header)

        # Distinct-fight soft warning banner (never blocks).
        if fight_count_warning(fight_count):
            warn = QLabel(
                f"Only {fight_count} fight"
                f"{'s' if fight_count != 1 else ''} — the percentile curve may be "
                "unreliable. Consider importing more logs before relying on this. "
                "(You can still apply it.)"
            )
            warn.setWordWrap(True)
            theme.set_widget_class(warn, "warn-banner")
            dlg_layout.addWidget(warn)

        # One-line legend; thin-data detail lives in cell + header tooltips.
        legend = QLabel("Green = up, red = down, gray = unchanged.")
        theme.mark_hint(legend)
        dlg_layout.addWidget(legend)

        # Tier columns speak TIER NAMES (percentile in parens + header tooltip).
        tier_headers = [
            f"{label.capitalize()} ({pct})"
            for label, pct in zip(_BUCKET_LABELS, self._CALIB_TIER_LABELS)
        ]
        axis_names = [a[0] for a in AXES]
        cols = ["Metric", "Samples", *tier_headers]
        table = QTableWidget(len(axis_names), len(cols))
        table.setHorizontalHeaderLabels(cols)

        # Header tooltips: explain Samples and each tier's percentile floor.
        samples_hdr = table.horizontalHeaderItem(1)
        if samples_hdr is not None:
            samples_hdr.setToolTip(
                "Player-fight observations pooled for this metric — not the same as "
                "the fight count above."
            )
        for i, (label, pct) in enumerate(zip(_BUCKET_LABELS, self._CALIB_TIER_LABELS)):
            hdr = table.horizontalHeaderItem(2 + i)
            if hdr is not None:
                hdr.setToolTip(f"{label.capitalize()} tier — {pct} percentile floor.")

        # Read-only, non-selectable, no focus, zebra striping.
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

        center = Qt.AlignmentFlag.AlignCenter
        for row, axis in enumerate(axis_names):
            n = obs_counts.get(axis, 0)
            has_new = axis in proposed
            is_thin = n < self.CALIB_CONFIDENCE_FLOOR

            label = self._calib_axis_label(axis)
            metric_item = QTableWidgetItem(label)
            metric_item.setData(Qt.ItemDataRole.UserRole, axis)  # keep the raw key
            if has_new and is_thin:
                metric_item.setForeground(thin_color)
                metric_item.setToolTip(
                    f"Thin data: only {n} player-fight observations pooled "
                    f"(under {self.CALIB_CONFIDENCE_FLOOR}). Applied anyway."
                )
            table.setItem(row, 0, metric_item)

            samples_item = QTableWidgetItem(str(n))
            samples_item.setTextAlignment(center)
            table.setItem(row, 1, samples_item)

            cur = current.get(axis)
            if not has_new or not cur:
                # Under 5 obs (or no current) -> unchanged; span the tier columns.
                kept = QTableWidgetItem("unchanged — keeping current")
                kept.setForeground(same_color)
                kept.setTextAlignment(center)
                kept.setToolTip(
                    "Fewer than 5 observations — not enough to recalibrate, so the "
                    "current values are kept."
                )
                table.setSpan(row, 2, 1, len(self._CALIB_TIER_LABELS))
                table.setItem(row, 2, kept)
                continue

            prop = proposed[axis]
            for i in range(len(self._CALIB_TIER_LABELS)):
                d = tier_delta(cur[i], prop[i])
                arrow = {"up": "↑", "down": "↓", "same": "="}[d.direction]
                if d.pct is None:
                    pct_s = "n/a%"
                else:
                    sign = "+" if d.pct >= 0 else ""  # format_threshold carries any '-'
                    pct_s = f"{sign}{self._calib_num(d.pct)}%"
                text = (f"{self._calib_num(cur[i])}→{self._calib_num(prop[i])}  "
                        f"{arrow}{self._calib_num(abs(d.delta))} ({pct_s})")
                cell = QTableWidgetItem(text)
                cell.setTextAlignment(center)
                cell.setForeground(
                    {"up": up_color, "down": down_color, "same": same_color}[d.direction]
                )
                table.setItem(row, 2 + i, cell)

        dlg_layout.addWidget(table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Confirm & Apply")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dlg_layout.addWidget(buttons)

        return dialog.exec() == QDialog.DialogCode.Accepted

    def _calib_reset_defaults(self):
        """Delete the override file and revert to built-in defaults."""
        from core.performance_buckets import reload_thresholds

        path = self._calib_thresholds_path()
        if not path.exists():
            self._set_calib_status("Already using built-in defaults — nothing to reset.")
            self._refresh_calib_count()
            return

        confirm = QMessageBox.question(
            self, "Reset to Defaults",
            "Discard your guild calibration and revert to SparkyBot's built-in "
            "thresholds? Collected fights are kept; only the calibrated thresholds "
            "are removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink()
        except OSError as exc:
            self._set_calib_status(f"Could not remove calibration file: {exc}", ok=False)
            return
        reload_thresholds(self._calib_thresholds_path())
        self._set_calib_status("Reverted to built-in defaults.", ok=True)
        self._refresh_calib_count()  # Reset button disables now that the override is gone

    def _create_raid_report_settings_tab(self) -> QWidget:
        """Create Raid Report settings tab."""
        scroll = QScrollArea()
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Stats viewer HTML
        viewer_group = QGroupBox("Stats Viewer")
        viewer_form = QFormLayout(viewer_group)

        viewer_layout = QHBoxLayout()
        self.raidreport_viewer_html = QLineEdit()
        self.raidreport_viewer_html.setPlaceholderText(
            "Path to Top_Stats_Index.html (leave blank to auto-detect)"
        )
        self.raidreport_viewer_browse_btn = QPushButton("Browse...")
        self.raidreport_viewer_browse_btn.clicked.connect(self._browse_raidreport_viewer)
        viewer_layout.addWidget(self.raidreport_viewer_html)
        viewer_layout.addWidget(self.raidreport_viewer_browse_btn)
        viewer_form.addRow("Stats Viewer HTML:", viewer_layout)

        layout.addWidget(viewer_group)

        # How reports get made — usage-mode preference (RaidReport/runMode).
        # Persisted now; the Home-page run panel honors it in a later slice.
        self.runmode_group_box = QGroupBox("How reports get made")
        runmode_layout = QVBoxLayout(self.runmode_group_box)

        self.runmode_run_button = QRadioButton("One-button runs (recommended)")
        theme.mark_option(self.runmode_run_button)
        runmode_layout.addWidget(self.runmode_run_button)
        run_hint = QLabel(
            "Press Start Run when the raid starts; End Run builds the "
            "report and posts it."
        )
        run_hint.setWordWrap(True)
        theme.mark_hint(run_hint)
        runmode_layout.addWidget(run_hint)

        self.runmode_manual = QRadioButton("I'll pick fights myself")
        theme.mark_option(self.runmode_manual)
        runmode_layout.addWidget(self.runmode_manual)
        manual_hint = QLabel(
            "Build reports on the Raid Report page whenever you want."
        )
        manual_hint.setWordWrap(True)
        theme.mark_hint(manual_hint)
        runmode_layout.addWidget(manual_hint)

        # Explicit group: exclusivity must survive re-parenting into the
        # Settings dialog's Raid Reports page.
        self.runmode_buttons = QButtonGroup(self)
        self.runmode_buttons.addButton(self.runmode_run_button)
        self.runmode_buttons.addButton(self.runmode_manual)
        self.runmode_run_button.setChecked(True)

        # Remembered End Run auto-post choice (RaidReport/runAutoPost —
        # the End Run confirm dialog writes the same key). Inert in manual
        # mode, so it grays with the mode choice.
        self.run_autopost = QCheckBox(
            "When I end a run, post the report to Discord automatically")
        self.run_autopost.setChecked(True)
        runmode_layout.addSpacing(4)
        runmode_layout.addWidget(self.run_autopost)
        self.runmode_manual.toggled.connect(self.run_autopost.setDisabled)

        layout.addWidget(self.runmode_group_box)

        # Output
        output_group = QGroupBox("Output")
        output_form = QFormLayout(output_group)

        output_layout = QHBoxLayout()
        self.raidreport_output_dir = QLineEdit()
        self.raidreport_output_dir.setPlaceholderText(
            "Leave blank: same folder as viewer, then app directory"
        )
        self.raidreport_output_browse_btn = QPushButton("Browse...")
        self.raidreport_output_browse_btn.clicked.connect(self._browse_raidreport_output)
        output_layout.addWidget(self.raidreport_output_dir)
        output_layout.addWidget(self.raidreport_output_browse_btn)
        output_form.addRow("Report Output Folder:", output_layout)

        layout.addWidget(output_group)

        # Options
        options_group = QGroupBox("Options")
        options_layout = QVBoxLayout(options_group)

        self.raidreport_cache_enabled = QCheckBox(
            "Fast reports (reuse live fight data) \u2014 recommended"
        )
        self.raidreport_cache_enabled.setChecked(True)
        self.raidreport_cache_enabled.setToolTip(
            "SparkyBot already analyzes every fight seconds after it ends. With this "
            "on, it keeps each fight's analysis file (about 20 MB per fight) in a "
            "RaidReportCache folder instead of discarding it. Reports then build from "
            "work already done \u2014 seconds instead of minutes \u2014 and only re-analyze "
            "fights SparkyBot missed. Files clean themselves up after 48 hours. Turn "
            "this off to save disk space; reports will re-analyze every log from "
            "scratch."
        )
        options_layout.addWidget(self.raidreport_cache_enabled)

        self.raidreport_poison_tab = QCheckBox(
            "Include poison coverage page in the report"
        )
        options_layout.addWidget(self.raidreport_poison_tab)

        self.raidreport_always_zip = QCheckBox(
            "Always zip Discord uploads"
        )
        options_layout.addWidget(self.raidreport_always_zip)

        layout.addWidget(options_group)
        layout.addStretch()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        return scroll

    def _browse_raidreport_viewer(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Stats Viewer HTML",
            self.raidreport_viewer_html.text() or str(Path.home()),
            "HTML files (*.html);;All files (*)"
        )
        if file_path:
            self.raidreport_viewer_html.setText(file_path)

    def _browse_raidreport_output(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Report Output Folder",
            self.raidreport_output_dir.text() or str(Path.home()),
        )
        if folder:
            self.raidreport_output_dir.setText(folder)

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
        theme.set_variant(title, "title")
        layout.addWidget(title)

        version_row = QHBoxLayout()
        version_label = QLabel(f"Version {VERSION}")
        theme.mark_hint(version_label)
        github_link = QLabel('<a href="https://github.com/SimpleHonors/SparkyBot">View on GitHub</a>')
        github_link.setOpenExternalLinks(True)
        theme.mark_hint(github_link)
        version_row.addStretch()
        version_row.addWidget(version_label)
        version_row.addWidget(QLabel("  •  "))
        version_row.addWidget(github_link)
        version_row.addStretch()
        layout.addLayout(version_row)

        layout.addSpacing(12)

        credits_header = QLabel("<b>Credits &amp; links</b>")
        credits_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        theme.set_variant(credits_header, "heading")
        layout.addWidget(credits_header)

        used_names = {"ArcDPS", "GW2 Elite Insights", "GW2 EI Log Combiner"}

        def add_project_links(heading, projects):
            section = QLabel(f"<b>{html.escape(heading)}</b>")
            section.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(section)
            grid = QGridLayout()
            grid.setHorizontalSpacing(24)
            grid.setVerticalSpacing(5)
            for index, project in enumerate(projects):
                project_link = QLabel(
                    f'<a href="{html.escape(project.url, quote=True)}">'
                    f'{html.escape(project.name)}</a>'
                )
                project_link.setTextFormat(Qt.TextFormat.RichText)
                project_link.setOpenExternalLinks(True)
                project_link.setAlignment(Qt.AlignmentFlag.AlignCenter)
                grid.addWidget(project_link, index // 2, index % 2)
            layout.addLayout(grid)

        used = [project for project in INTEROP_PROJECTS
                if project.name in used_names]
        neighbors = [project for project in INTEROP_PROJECTS
                     if project.name not in used_names]

        add_project_links("Tools SparkyBot uses", used)
        used_note = QLabel(
            "ArcDPS creates the logs, Elite Insights parses them, and the "
            "Log Combiner builds optional whole-night reports."
        )
        used_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        used_note.setWordWrap(True)
        theme.mark_hint(used_note)
        layout.addWidget(used_note)

        layout.addSpacing(8)
        add_project_links("Independent neighboring tools", neighbors)
        neighbor_note = QLabel(
            "Listed for credit and interoperability—not as a claim that "
            "their code is bundled or that SparkyBot was based on them."
        )
        neighbor_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        neighbor_note.setWordWrap(True)
        theme.mark_hint(neighbor_note)
        layout.addWidget(neighbor_note)

        comparison_link = QLabel(
            '<a href="https://github.com/SimpleHonors/SparkyBot/blob/main/'
            'docs/WVW_LOG_TOOL_INTEROPERABILITY.md">'
            '<b>How these tools differ</b></a>'
        )
        comparison_link.setTextFormat(Qt.TextFormat.RichText)
        comparison_link.setOpenExternalLinks(True)
        comparison_link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(comparison_link)

        # Add the inner widget to the outer layout with stretches on both sides
        outer_layout.addStretch()
        outer_layout.addWidget(inner)
        outer_layout.addStretch()

        return widget
