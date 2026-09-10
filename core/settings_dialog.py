"""Modal Settings dialog — the v2.0 settings surface (slice 4).

Classic category dialog (OBS/WinSCP model): a left category list drives a
QStackedWidget of pages, with right-aligned OK / Cancel / Apply underneath.
Real dirty tracking replaces the old silent-discard defect: Apply enables
only when something changed; OK saves and closes; Cancel discards; closing
the window with unsaved changes prompts save/discard/cancel.

The dialog does not own any settings widgets. It re-homes the controls of
the legacy SettingsWindow ("the engine") into task-shaped category pages —
the engine keeps its full attribute surface, its load/save routines and all
of its background-thread test/refresh machinery. Re-homing is presentation
only: existing config keys keep their on-disk compatibility. Obsolete upload
size controls remain out of the visible settings pages.

Categories (AI-off): Discord, Fight Reports, Watcher & Parsing,
Raid Reports, Twitch, Application, Updates, About. With AI enabled, a separator and
AI Commentary / Voice / Vocabulary follow. Visibility is decided from
AI/enableAiAnalysis at every open AND re-evaluated after every successful
Apply/OK, so flipping the master switch takes effect live (LAW #2) — the
AI-off dialog's only AI-labeled surface is the switch's own group on the
Application page.

Pages are composed lazily on first visit; the Updates page's first
view triggers the one-time GitHub update checks (never construction).
"""

import logging

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from core import theme

logger = logging.getLogger(__name__)

# Category names, in sidebar order. The AI block appears after a separator
# and only when AI/enableAiAnalysis is true at dialog open.
CAT_DISCORD = "Discord"
CAT_FIGHT_REPORTS = "Fight Reports"
CAT_WATCHER = "Watcher & Parsing"
CAT_RAID_REPORTS = "Fight Summary"
CAT_TWITCH = "Twitch"
CAT_APPLICATION = "Application"
CAT_UPDATES = "Updates"
CAT_ABOUT = "About"
CAT_AI = "AI Commentary"
CAT_VOICE = "Voice"
CAT_VOCABULARY = "Vocabulary"

BASE_CATEGORIES = (CAT_DISCORD, CAT_FIGHT_REPORTS, CAT_WATCHER,
                   CAT_RAID_REPORTS, CAT_TWITCH, CAT_APPLICATION,
                   CAT_UPDATES, CAT_ABOUT)
AI_CATEGORIES = (CAT_AI, CAT_VOICE, CAT_VOCABULARY)

_ROLE_CATEGORY = Qt.ItemDataRole.UserRole

# ---------------------------------------------------------------------------
# Dirty-tracked engine widgets, by kind. Everything _save_settings persists
# is here (vocabulary Default/Custom mode combos are deliberately absent —
# they apply immediately to sparkybot_vocabulary.json, not on OK/Apply).
# ---------------------------------------------------------------------------

_TRACKED_LINE_EDITS = (
    "discord_webhook", "discord_webhook_label", "discord_webhook2",
    "discord_webhook3", "discord_webhook_name1", "discord_webhook_name2",
    "discord_webhook_name3", "guild_icon", "log_folder", "gw2ei_exe",
    "twitch_channel", "twitch_token", "ai_base_url", "ai_api_key",
    "tts_elevenlabs_api_key", "tts_elevenlabs_voice_id", "tts_local_url",
    "raidreport_viewer_html", "raidreport_output_dir",
)
_TRACKED_CHECKBOXES = (
    "enable_discord", "large_upload_after",
    "show_quick_report", "show_damage", "show_heals", "show_defense",
    "show_ccs", "show_strips", "show_cleanses", "show_downs", "show_burst",
    "show_top_skills", "show_offensive_boons", "show_defensive_boons",
    "show_enemy_breakdown",
    "close_to_tray", "minimize_to_tray", "start_minimized",
    "start_watcher_on_startup", "start_with_windows", "hide_console",
    "check_updates_on_launch", "enable_ai", "ai_disable_thinking",
    "enable_twitch", "twitch_use_tls", "enable_tts", "tts_discord_attach",
    "tts_el_speaker_boost", "raidreport_cache_enabled",
    "raidreport_poison_tab", "raidreport_always_zip", "run_autopost",
    # dps.report links (radios track like checkboxes; both start unchecked,
    # so the first timing pick must mark the dialog dirty)
    "dpsreport_enabled", "dpsreport_link_later", "dpsreport_together",
)
_TRACKED_SPINS = (
    "poll_interval", "max_parse_memory", "min_duration", "min_downs",
    "min_damage", "max_upload", "ai_max_tokens", "ai_timeout", "tts_volume",
    "tts_el_stability", "tts_el_similarity", "tts_el_style", "tts_el_speed",
    "ai_vocab_shock", "ai_vocab_positive", "ai_vocab_negative",
    "ai_vocab_gates",
)
_TRACKED_COMBOS = (
    "active_webhook", "raid_report_webhook", "ai_provider", "ai_model",
    "ai_prompt_mode", "theme_combo",
    "tts_provider", "tts_edge_voice", "tts_elevenlabs_model",
    "tts_local_voice", "raidreport_default_view",
)
_TRACKED_TEXT_EDITS = ("ai_system_prompt",)


class SettingsDialog(QDialog):
    """Modal category Settings dialog over the legacy SettingsWindow engine."""

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.config = engine.config

        self.setWindowTitle("Settings")
        self.setMinimumSize(700, 480)
        self.resize(780, 560)

        self._pages = {}          # category name -> page widget (lazy)
        self._page_opened = set() # categories whose open-hook ran this open
        self._snapshot = {}
        self._loading = False
        self._relaunch_note = None
        self._ai_switch_group = None   # the master switch's group (lazy)

        self._build_ui()
        self._wire_dirty_tracking()

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)

        body = QHBoxLayout()
        body.setSpacing(8)

        self.category_list = QListWidget()
        theme.set_widget_class(self.category_list, "nav")
        self.category_list.setFixedWidth(176)
        for name in BASE_CATEGORIES:
            item = QListWidgetItem(name)
            item.setData(_ROLE_CATEGORY, name)
            self.category_list.addItem(item)
        # Separator row before the AI block (line glyph-free: a real frame)
        self._separator_item = QListWidgetItem("")
        self._separator_item.setFlags(Qt.ItemFlag.NoItemFlags)
        self._separator_item.setSizeHint(QSize(0, 9))
        self.category_list.addItem(self._separator_item)
        sep_frame = QFrame()
        sep_frame.setFrameShape(QFrame.Shape.HLine)
        self.category_list.setItemWidget(self._separator_item, sep_frame)
        for name in AI_CATEGORIES:
            item = QListWidgetItem(name)
            item.setData(_ROLE_CATEGORY, name)
            self.category_list.addItem(item)
        self.category_list.currentRowChanged.connect(self._on_category_changed)
        body.addWidget(self.category_list)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
        )
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.setEnabled(False)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        self.apply_button.clicked.connect(self._on_apply)
        outer.addWidget(buttons)

    # ------------------------------------------------------------------
    # open / AI visibility
    # ------------------------------------------------------------------

    def open_dialog(self):
        """(Re)open: reload values from config, re-evaluate AI visibility,
        reset dirty state, land on the first category."""
        self._loading = True
        try:
            self.engine._load_settings(prompt_updates=False)
        finally:
            self._loading = False
        self._sync_ai_visibility()
        self._page_opened.clear()
        self._take_snapshot()
        self.apply_button.setEnabled(False)
        if self._relaunch_note is not None:
            self._relaunch_note.setVisible(False)

        row = self.category_list.currentRow()
        current = self.category_list.item(row) if row >= 0 else None
        if current is None or self.category_list.isRowHidden(row):
            self.category_list.setCurrentRow(0)
        else:
            self._on_category_changed(row)
        self.open()

    def _sync_ai_visibility(self):
        """AI categories exist only while AI/enableAiAnalysis is true —
        evaluated at every open AND after every successful Apply/OK, so a
        saved flip of the master switch takes effect in the open dialog."""
        ai_on = bool(self.config.enable_ai_analysis)
        for row in range(self.category_list.count()):
            item = self.category_list.item(row)
            name = item.data(_ROLE_CATEGORY)
            if name in AI_CATEGORIES or item is self._separator_item:
                self.category_list.setRowHidden(row, not ai_on)
        # AI-conditional wording (design-A LAW-2 table): Twitch itself is
        # NOT AI-gated — only the tooltip's promise changes with the mode.
        self.engine.enable_twitch.setToolTip(
            "Posts fight summaries and AI commentary to a Twitch chat "
            "channel." if ai_on else
            "Posts fight summaries to a Twitch chat channel.")

    def _select_category(self, name: str):
        """Land the sidebar on the named category (no-op when absent)."""
        for row in range(self.category_list.count()):
            if self.category_list.item(row).data(_ROLE_CATEGORY) == name:
                self.category_list.setCurrentRow(row)
                return

    def visible_categories(self):
        """Category names currently offered (test/introspection helper)."""
        names = []
        for row in range(self.category_list.count()):
            item = self.category_list.item(row)
            name = item.data(_ROLE_CATEGORY)
            if name and not self.category_list.isRowHidden(row):
                names.append(name)
        return names

    # ------------------------------------------------------------------
    # navigation / lazy pages
    # ------------------------------------------------------------------

    def _on_category_changed(self, row: int):
        if row < 0:
            return
        item = self.category_list.item(row)
        name = item.data(_ROLE_CATEGORY)
        if not name:
            return  # separator
        page = self._pages.get(name)
        if page is None:
            page = self._build_page(name)
            self._pages[name] = page
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        self._on_page_opened(name)

    def _on_page_opened(self, name: str):
        """Per-open page hooks (rescan-on-open contract)."""
        if name in self._page_opened:
            return
        self._page_opened.add(name)
        if name == CAT_UPDATES:
            # First view of the update surface fires the one-time GitHub
            # checks (never at construction).
            self.engine.run_update_checks_once()
        elif name == CAT_VOCABULARY:
            self._sync_vocab_modes()

    def page_of(self, widget) -> str:
        """Category name whose page contains `widget` (test helper)."""
        for name, page in self._pages.items():
            if page.isAncestorOf(widget):
                return name
        return ""

    def _make_page(self, title: str):
        """Scrollable page scaffold with an uppercase heading."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)
        heading = QLabel(title.upper())
        theme.set_variant(heading, "heading")
        layout.addWidget(heading)
        scroll.setWidget(content)
        return scroll, layout

    def _build_page(self, name: str):
        builder = {
            CAT_DISCORD: self._build_discord_page,
            CAT_FIGHT_REPORTS: self._build_fight_reports_page,
            CAT_WATCHER: self._build_watcher_page,
            CAT_RAID_REPORTS: self._build_raid_reports_page,
            CAT_TWITCH: self._build_twitch_page,
            CAT_APPLICATION: self._build_application_page,
            CAT_UPDATES: self._build_updates_page,
            CAT_ABOUT: self._build_about_page,
            CAT_AI: self._build_ai_page,
            CAT_VOICE: self._build_voice_page,
            CAT_VOCABULARY: self._build_vocabulary_page,
        }[name]
        return builder()

    # ------------------------------------------------------------------
    # page builders — every control re-homed from the legacy tabs
    # ------------------------------------------------------------------

    @staticmethod
    def _pair(widget, button):
        """Field + trailing button row (path pickers)."""
        row = QHBoxLayout()
        row.addWidget(widget, 1)
        row.addWidget(button)
        return row

    def _build_discord_page(self):
        e = self.engine
        page, layout = self._make_page(CAT_DISCORD)

        layout.addWidget(e.enable_discord)

        webhooks = QGroupBox("Webhooks")
        form = QFormLayout(webhooks)
        form.addRow("Destination 1 name", e.discord_webhook_name1)
        form.addRow("Destination 1 webhook", e.discord_webhook)
        form.addRow("Destination 2 name", e.discord_webhook_name2)
        form.addRow("Destination 2 webhook", e.discord_webhook2)
        form.addRow("Destination 3 name", e.discord_webhook_name3)
        form.addRow("Destination 3 webhook", e.discord_webhook3)
        form.addRow("Fight reports", e.active_webhook)
        form.addRow("Fight Summary", e.raid_report_webhook)
        form.addRow("Bot name", e.discord_webhook_label)
        layout.addWidget(webhooks)

        delivery = QGroupBox("What gets posted")
        dlayout = QVBoxLayout(delivery)
        fight_delivery = QLabel(
            "Individual fight posts are Discord embeds. SparkyBot does not "
            "attach raw fight-log files (.evtc or .zevtc)."
        )
        fight_delivery.setWordWrap(True)
        dlayout.addWidget(fight_delivery)
        nightly_delivery = QLabel(
            "End-of-night posts include a quick run summary plus the report "
            "file. SparkyBot shrinks or zips it automatically when needed; "
            "Discord's hard limit is 10 MB."
        )
        nightly_delivery.setWordWrap(True)
        theme.mark_hint(nightly_delivery)
        dlayout.addWidget(nightly_delivery)
        layout.addWidget(delivery)

        # Master-checkbox dependency graying (the old UI never did this)
        def _gray(on):
            webhooks.setEnabled(on)
            delivery.setEnabled(on)
        e.enable_discord.toggled.connect(_gray)
        _gray(e.enable_discord.isChecked())

        layout.addStretch()
        return page

    def _build_fight_reports_page(self):
        e = self.engine
        page, layout = self._make_page(CAT_FIGHT_REPORTS)

        appearance = QGroupBox("Appearance")
        form = QFormLayout(appearance)
        form.addRow("Guild icon", self._pair(e.guild_icon, e.guild_icon_browse_btn))
        color_row = QHBoxLayout()
        color_row.addWidget(e.color_preview)
        color_row.addWidget(e.color_hex_label)
        color_row.addStretch()
        form.addRow("Embed color", color_row)
        layout.addWidget(appearance)

        sections = QGroupBox("Sections of the Discord fight report")
        grid = QGridLayout(sections)
        columns = (
            ("Summary", ("show_quick_report", "show_downs",
                         "show_enemy_breakdown", "show_top_skills")),
            ("Offense", ("show_damage", "show_burst", "show_ccs",
                         "show_strips", "show_offensive_boons")),
            ("Support & Defense", ("show_heals", "show_cleanses",
                                   "show_defense", "show_defensive_boons")),
        )
        for col, (title, attrs) in enumerate(columns):
            header = QLabel(title)
            theme.mark_hint(header)
            grid.addWidget(header, 0, col)
            for row, attr in enumerate(attrs, start=1):
                grid.addWidget(getattr(e, attr), row, col)
        grid.setRowStretch(len(max(columns, key=lambda c: len(c[1]))[1]) + 1, 1)
        layout.addWidget(sections)

        # dps.report links group moves wholesale from the engine (same
        # borrow pattern as the Interface Theme group on the Application
        # page); the engine's save path validates and persists it.
        layout.addWidget(e.dpsreport_group_box)

        layout.addStretch()
        return page

    def _build_watcher_page(self):
        e = self.engine
        page, layout = self._make_page(CAT_WATCHER)

        source = QGroupBox("Log source")
        form = QFormLayout(source)
        form.addRow("Log folder", self._pair(e.log_folder, e.log_folder_browse_btn))
        form.addRow("Poll interval", e.poll_interval)
        poll_hint = QLabel("Only used for network shares; local folders react instantly.")
        poll_hint.setWordWrap(True)
        theme.mark_hint(poll_hint)
        form.addRow("", poll_hint)
        layout.addWidget(source)

        parser = QGroupBox("Elite Insights parser")
        pform = QFormLayout(parser)
        pform.addRow("Max parse memory", e.max_parse_memory)
        ei_hint = QLabel("SparkyBot installs and repairs its fight-log parser automatically in its own program folder. Parser updates are available on the Updates page.")
        ei_hint.setWordWrap(True)
        theme.mark_hint(ei_hint)
        pform.addRow("", ei_hint)
        layout.addWidget(parser)

        filters = QGroupBox("Which fights get posted")
        fform = QFormLayout(filters)
        intro = QLabel(
            "A fight must pass all of these to be posted to Discord. "
            "Skipped fights still count for fight summaries."
        )
        intro.setWordWrap(True)
        theme.mark_hint(intro)
        fform.addRow(intro)
        e.min_duration.setToolTip("Fights shorter than this are skipped, not posted.")
        fform.addRow("Min duration", e.min_duration)
        e.min_downs.setToolTip("Fights with fewer downed players than this are skipped.")
        fform.addRow("Min downs", e.min_downs)
        e.min_damage.setToolTip("Fights with less total damage than this are skipped.")
        fform.addRow("Min total damage", e.min_damage)
        layout.addWidget(filters)

        layout.addStretch()
        return page

    def _build_raid_reports_page(self):
        e = self.engine
        page, layout = self._make_page(CAT_RAID_REPORTS)

        # Usage-mode preference group moves wholesale from the engine.
        layout.addWidget(e.runmode_group_box)

        output = QGroupBox("Output")
        form = QFormLayout(output)
        form.addRow("Report opens in", e.raidreport_default_view)
        form.addRow("Report output folder",
                    self._pair(e.raidreport_output_dir, e.raidreport_output_browse_btn))
        form.addRow("", e.raidreport_cache_enabled)
        form.addRow("", e.raidreport_always_zip)
        form.addRow("", e.raidreport_poison_tab)
        layout.addWidget(output)

        advanced = QGroupBox("Advanced")
        aform = QFormLayout(advanced)
        aform.addRow("Stats viewer HTML",
                     self._pair(e.raidreport_viewer_html, e.raidreport_viewer_browse_btn))
        layout.addWidget(advanced)

        layout.addStretch()
        return page

    def _build_twitch_page(self):
        e = self.engine
        page, layout = self._make_page(CAT_TWITCH)

        # Master checkbox out of the group so graying never disables it.
        layout.addWidget(e.enable_twitch)

        e.twitch_group.setTitle("Twitch chat")
        layout.addWidget(e.twitch_group)

        e.enable_twitch.toggled.connect(e.twitch_group.setEnabled)
        e.twitch_group.setEnabled(e.enable_twitch.isChecked())

        # The plaintext warning appears only while TLS is off.
        def _sync_tls_note(_=None):
            e.twitch_tls_note.setVisible(not e.twitch_use_tls.isChecked())
        e.twitch_use_tls.toggled.connect(_sync_tls_note)
        _sync_tls_note()

        layout.addStretch()
        return page

    def _build_application_page(self):
        e = self.engine
        page, layout = self._make_page(CAT_APPLICATION)

        # Interface Theme group moves wholesale from the engine (same borrow
        # pattern as runmode_group_box). Its combo live-applies via the
        # engine's _on_theme_changed; the engine's save path persists it.
        layout.addWidget(e.theme_group_box)

        # && renders a literal ampersand; a bare & becomes a Qt mnemonic
        # underscore ("Startup _tray") in the group title.
        startup = QGroupBox("Startup && tray")
        sform = QVBoxLayout(startup)
        for attr in ("start_with_windows", "start_minimized",
                     "start_watcher_on_startup", "minimize_to_tray",
                     "close_to_tray", "hide_console"):
            sform.addWidget(getattr(e, attr))
        windows_hint = QLabel(
            "Start with Windows is a Windows setting, applied when you click OK or Apply."
        )
        windows_hint.setWordWrap(True)
        theme.mark_hint(windows_hint)
        sform.addWidget(windows_hint)
        self._relaunch_note = QLabel(
            "Console window and Windows startup changes take effect the "
            "next time SparkyBot is launched."
        )
        self._relaunch_note.setWordWrap(True)
        theme.set_state(self._relaunch_note, "warn")
        self._relaunch_note.setVisible(False)
        sform.addWidget(self._relaunch_note)
        layout.addWidget(startup)

        # The ONE sanctioned AI surface in AI-off mode: the master switch
        # itself (design-A LAW-2b — "the only AI-related pixel"). The
        # gating tests allowlist exactly this group; gating is live now,
        # so the old "next time you open Settings" hint is gone.
        ai_group = QGroupBox("AI features")
        self._ai_switch_group = ai_group
        aform = QVBoxLayout(ai_group)
        aform.addWidget(e.enable_ai)
        ai_hint = QLabel(
            "SparkyBot writes short AI commentary about each fight and can "
            "read it aloud. Fully optional — everything else works without it."
        )
        ai_hint.setWordWrap(True)
        theme.mark_hint(ai_hint)
        aform.addWidget(ai_hint)
        layout.addWidget(ai_group)

        layout.addStretch()
        return page

    def _build_updates_page(self):
        e = self.engine
        page, layout = self._make_page(CAT_UPDATES)
        layout.addWidget(e.check_updates_on_launch)
        layout.addWidget(e.sparkybot_update_group)
        layout.addWidget(e.ei_update_group)
        layout.addStretch()
        return page

    def _build_about_page(self):
        e = self.engine
        page, layout = self._make_page(CAT_ABOUT)
        layout.addWidget(e.about_widget)
        e.about_widget.show()
        layout.addStretch()
        return page

    def _build_ai_page(self):
        e = self.engine
        page, layout = self._make_page(CAT_AI)

        e.ai_group.setTitle("AI service")
        layout.addWidget(e.ai_group)

        layout.addStretch()
        return page

    def _build_voice_page(self):
        e = self.engine
        page, layout = self._make_page(CAT_VOICE)

        # Master checkbox out of the moved group so graying spares it.
        layout.addWidget(e.enable_tts)

        e.tts_general_group.setTitle("Playback")
        layout.addWidget(e.tts_general_group)
        e.tts_provider_group.setTitle("Voice service")
        layout.addWidget(e.tts_provider_group)
        layout.addWidget(e.tts_test_group)

        def _gray(on):
            e.tts_general_group.setEnabled(on)
            e.tts_provider_group.setEnabled(on)
            e.tts_test_group.setEnabled(on)
        e.enable_tts.toggled.connect(_gray)
        _gray(e.enable_tts.isChecked())

        layout.addStretch()
        return page

    def _build_vocabulary_page(self):
        e = self.engine
        page, layout = self._make_page(CAT_VOCABULARY)

        note = QLabel(
            "Term and Default/Custom changes apply immediately. "
            "The frequency sliders apply when you click OK or Apply."
        )
        note.setWordWrap(True)
        theme.mark_hint(note)
        layout.addWidget(note)

        e.ai_vocab_group.setTitle("Catchphrase categories")
        layout.addWidget(e.ai_vocab_group)

        layout.addStretch()
        return page

    def _sync_vocab_modes(self):
        """Vocabulary page open: re-read sparkybot_vocabulary.json so the
        Default/Custom combos reflect external edits (rescan-on-open).
        Signals are blocked — syncing must never rewrite the file."""
        e = self.engine
        try:
            from core.ai_analyst import VocabularyConfig
            vc = VocabularyConfig(
                config_path=self.config.home_dir / "sparkybot_vocabulary.json")
            custom = set(vc._raw.get("custom_categories", []))
        except Exception as exc:
            logger.warning("Could not sync vocabulary modes: %s", exc)
            return
        rows = (
            ("shock", e.ai_vocab_shock_mode, e.ai_vocab_shock, e.shock_edit_btn),
            ("positive", e.ai_vocab_positive_mode, e.ai_vocab_positive, e.pos_edit_btn),
            ("negative", e.ai_vocab_negative_mode, e.ai_vocab_negative, e.neg_edit_btn),
            ("gates", e.ai_vocab_gates_mode, e.ai_vocab_gates, e.gates_edit_btn),
        )
        for cat, combo, spin, edit_btn in rows:
            is_custom = cat in custom
            combo.blockSignals(True)
            combo.setCurrentText("Custom" if is_custom else "Default")
            combo.blockSignals(False)
            spin.setEnabled(is_custom)
            edit_btn.setEnabled(is_custom)

    # ------------------------------------------------------------------
    # dirty tracking
    # ------------------------------------------------------------------

    def _wire_dirty_tracking(self):
        e = self.engine
        self._getters = {}

        for attr in _TRACKED_LINE_EDITS:
            w = getattr(e, attr)
            self._getters[attr] = w.text
            w.textChanged.connect(self._refresh_dirty)
        for attr in _TRACKED_CHECKBOXES:
            w = getattr(e, attr)
            self._getters[attr] = w.isChecked
            w.toggled.connect(self._refresh_dirty)
        for attr in _TRACKED_SPINS:
            w = getattr(e, attr)
            self._getters[attr] = w.value
            w.valueChanged.connect(self._refresh_dirty)
        for attr in _TRACKED_COMBOS:
            w = getattr(e, attr)
            self._getters[attr] = w.currentText
            w.currentTextChanged.connect(self._refresh_dirty)
            if w.isEditable():
                w.editTextChanged.connect(self._refresh_dirty)
        for attr in _TRACKED_TEXT_EDITS:
            w = getattr(e, attr)
            self._getters[attr] = w.toPlainText
            w.textChanged.connect(self._refresh_dirty)

        # Non-widget state: the picked embed color (QColorDialog runs inside
        # the engine's clicked slot; ours is connected after it, so the color
        # is final by the time we compare).
        self._getters["embed_color"] = lambda: e._current_embed_color.name()
        e.color_preview.clicked.connect(self._refresh_dirty)

        # Usage-mode radios (one is enough — they are exclusive).
        self._getters["run_mode"] = e.runmode_manual.isChecked
        e.runmode_manual.toggled.connect(self._refresh_dirty)

    def _take_snapshot(self):
        self._snapshot = {name: getter() for name, getter in self._getters.items()}

    def is_dirty(self) -> bool:
        return any(getter() != self._snapshot.get(name)
                   for name, getter in self._getters.items())

    def _refresh_dirty(self, *_args):
        if self._loading:
            return
        self.apply_button.setEnabled(self.is_dirty())

    # ------------------------------------------------------------------
    # OK / Cancel / Apply lifecycle
    # ------------------------------------------------------------------

    def _apply(self) -> bool:
        """Persist through the engine's single save path. True on success."""
        saved, relaunch_needed = self.engine._save_settings()
        if not saved:
            QMessageBox.warning(
                self, "Settings",
                getattr(self.engine, "_last_save_error", "")
                or "Failed to save settings.",
            )
            return False
        self._take_snapshot()
        self.apply_button.setEnabled(False)
        if relaunch_needed and self._relaunch_note is not None:
            self._relaunch_note.setVisible(True)
        # LAW #2, live: a saved flip of the AI master switch inserts or
        # removes the AI categories in this open dialog immediately (the
        # engine's save signal re-gates the main-window surfaces the same
        # moment). If the page under the cursor ceased to exist, land on
        # Application — where the switch lives.
        self._sync_ai_visibility()
        row = self.category_list.currentRow()
        if row >= 0 and self.category_list.isRowHidden(row):
            self._select_category(CAT_APPLICATION)
        return True

    def _on_apply(self):
        if self.is_dirty():
            self._apply()

    def _on_ok(self):
        if self.is_dirty() and not self._apply():
            return  # save failed — keep the dialog open
        self.accept()

    def _confirm_unsaved(self) -> str:
        """Prompt for unsaved changes on window close: save/discard/cancel."""
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved changes")
        box.setText("You have unsaved changes. Save them before closing?")
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        result = box.exec()
        if result == QMessageBox.StandardButton.Save:
            return "save"
        if result == QMessageBox.StandardButton.Discard:
            return "discard"
        return "cancel"

    def closeEvent(self, event):
        """Titlebar X with unsaved changes prompts; Cancel/Esc discard
        silently (classic dialog semantics)."""
        if self.is_dirty():
            choice = self._confirm_unsaved()
            if choice == "cancel" or (choice == "save" and not self._apply()):
                event.ignore()
                return
        super().closeEvent(event)
