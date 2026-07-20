"""SparkyBot main window — the v2.0 application shell.

QMainWindow with a classic menu bar (File / Tools / Help, mnemonics), a left
sidebar (QListWidget) driving a QStackedWidget, and a QStatusBar carrying the
watcher indicator.

Sidebar pages are the ACTION surfaces: Home, Raid Report, Process Files and
Calibration. Settings are not a page — the "Settings" sidebar entry (and
File > Settings..., Ctrl+,) opens the modal SettingsDialog, which re-homes
the legacy SettingsWindow's controls into category pages. The legacy window
survives headless as the settings engine: it still owns every control
attribute, load/save, and the promoted Raid Report / Calibration action
tabs mounted under the sidebar. It is built lazily on first need, so
opening the main window constructs nothing network-flavored.

Lifecycle contract (matches the old SettingsWindow window semantics):
- The app controller keeps this window as its lazy `settings_window`
  singleton; the attribute name is preserved so every `is not None` check
  and signal hookup in main.py keeps working.
- Closing with closeToTray on hides to tray; off quits the app explicitly
  (the app sets quitOnLastWindowClosed False, so quitting is always an
  explicit act — tray Quit, File > Exit, or this closeEvent).
- set_watcher_state(bool) is the single UI entry point for watcher state:
  the Home CTA, the File-menu action, the status-bar indicator and the
  settings engine all flip together.
"""

import logging
import threading
from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QGroupBox, QHBoxLayout,
    QLabel, QListView, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QProgressBar, QPushButton, QStackedWidget, QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QIcon, QKeySequence
from PySide6.QtCore import Qt, Signal, QEvent, QTimer
from pathlib import Path

from core import theme
from core.activity_feed import ActivityFeedModel, format_clock
from core.gui_settings import ProcessFilesWidget, SettingsWindow
from core.raid_session import discover_logs
# Stage-weighted progress shares the Raid Report page's weights — the run
# panel's inline bar must behave exactly like the page's.
from core.raid_report_tab import _STAGE_BASE, _STAGE_SPAN
from core.run_session import (
    RESUME_WINDOW_HOURS, STALE_HINT_HOURS, RunSession, fights_in_window,
    format_elapsed,
)
from core.version import VERSION

logger = logging.getLogger(__name__)


def _log_paths_from_mime(mime) -> list:
    """Local .evtc/.zevtc paths in a drag payload (anything else ignored)."""
    if not mime.hasUrls():
        return []
    return [url.toLocalFile() for url in mime.urls()
            if url.toLocalFile().lower().endswith(('.evtc', '.zevtc'))]


class HomePage(QWidget):
    """Home page surface. Dropping fight logs anywhere on it routes them
    to the Process Files queue (the MainWindow handles the navigation)."""

    sig_logs_dropped = Signal(list)   # list[str] of local file paths

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if _log_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = _log_paths_from_mime(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.sig_logs_dropped.emit(paths)

# Sidebar entries, top to bottom: (stable key, label). Rows are DYNAMIC —
# the Calibration entry exists only while AI features are on (LAW #2), so
# pages are addressed by these keys everywhere; nothing may assume a row
# index. The PAGE_* names are kept for their many import sites.
PAGE_HOME = "home"
PAGE_RAID_REPORT = "raid-report"
PAGE_PROCESS_FILES = "process-files"
PAGE_CALIBRATION = "calibration"
PAGE_SETTINGS = "settings"

NAV_ENTRIES = (
    (PAGE_HOME, "Home"),
    (PAGE_RAID_REPORT, "Raid Report"),
    (PAGE_PROCESS_FILES, "Process Files"),
    (PAGE_CALIBRATION, "Calibration"),
    (PAGE_SETTINGS, "Settings"),
)
_NAV_LABELS = dict(NAV_ENTRIES)

# Nav entries that exist only while AI features are enabled (absent when
# off — never grayed). Calibration tunes the tiers AI commentary grades
# against; it is pure AI infrastructure.
_AI_ONLY_PAGES = frozenset({PAGE_CALIBRATION})

_NAV_ROLE = Qt.ItemDataRole.UserRole


class MainWindow(QMainWindow):
    """Application shell: sidebar navigation over the existing pages."""

    # Same outward contract as the old SettingsWindow so the app controller's
    # wiring is unchanged: watcher_toggled asks the controller to flip the
    # watcher; settings_changed re-emits the embedded host's save signal.
    watcher_toggled = Signal()
    settings_changed = Signal()

    # End Run report thread -> GUI thread (same idiom as RaidReportTab).
    sig_run_progress = Signal(str, int, int, str)
    sig_run_done = Signal(object)
    sig_run_error = Signal(str)

    def __init__(self, config, parent=None, update_flow=None, clock=None):
        super().__init__(parent)
        self.config = config
        # Shared with the embedded settings host so its Updates tab drives
        # (and reflects) the same pipeline as the app's launch check.
        self.update_flow = update_flow
        self._clock = clock or datetime.now   # injectable for tests
        self._settings = None        # lazy SettingsWindow engine (headless)
        self._settings_dialog = None  # lazy modal SettingsDialog
        self._raid_report_page = None  # promoted action tab (rescan hook)
        self.__tts_client = None     # forwarded to the engine when it exists
        self._watcher_running = False
        self._launch_update = None   # (version, release_data) for the banner

        # Run session state (only ever constructed in run-button mode —
        # manual mode keeps the whole module dormant, no state file).
        self._run_session = None
        self._run_fight_count = 0
        self._run_report_busy = False
        self._run_auto_posted = False
        self._quit_after_run = False
        self._run_tick_timer = QTimer(self)          # elapsed readout
        self._run_tick_timer.setInterval(1000)
        self._run_tick_timer.timeout.connect(self._update_run_panel_clock)
        self._run_scan_timer = QTimer(self)          # slow fights-so-far scan
        self._run_scan_timer.setInterval(60_000)
        self._run_scan_timer.timeout.connect(self._refresh_run_counter)

        self.setWindowTitle("SparkyBot")
        # Shell target size — replaces the old 13-tab-label width computation.
        self.setMinimumSize(600, 460)

        icon_path = Path(__file__).parent.parent / "assets" / "sbtray.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._build_menus()
        self._build_central()
        self._build_status_bar()

        self.sig_run_progress.connect(self._on_run_progress)
        self.sig_run_done.connect(self._on_run_done)
        self.sig_run_error.connect(self._on_run_error)

        # The run panel exists only while runMode is "run-button"; the
        # Settings switch takes effect live through the settings-changed
        # seam (the engine re-emits through this window on every save).
        self.settings_changed.connect(self._sync_run_panel_mode)
        # Same seam for the AI master switch: every settings save re-gates
        # the AI-only surfaces (Calibration nav entry + Tools action) live.
        self.settings_changed.connect(self._sync_ai_gating)

        # Quiet status-bar banner for the silent launch check. Subscribes to
        # sig_launch_available ONLY — sig_available is the manual check on
        # the Settings Application page and hooking a banner to it would
        # double-surface manual checks.
        if self.update_flow is not None:
            self.update_flow.sig_launch_available.connect(
                self._on_update_launch_available)

        self.navigate(PAGE_HOME)

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------

    def _build_menus(self):
        """Classic menu bar with mnemonics — File / Tools / Help."""
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        self.action_watcher = file_menu.addAction("Start &Watcher")
        self.action_watcher.triggered.connect(self._on_start_clicked)
        file_menu.addSeparator()
        self.action_settings_dialog = file_menu.addAction("&Settings...")
        self.action_settings_dialog.setShortcut(QKeySequence("Ctrl+,"))
        self.action_settings_dialog.triggered.connect(
            lambda checked=False: self._open_settings_from_menu())
        file_menu.addSeparator()
        self.action_exit = file_menu.addAction("E&xit")
        self.action_exit.triggered.connect(self._quit_app)

        tools_menu = bar.addMenu("&Tools")
        self._nav_actions = {}
        for key, label in NAV_ENTRIES:
            if key == PAGE_SETTINGS:
                continue      # Settings has its own action below
            action = tools_menu.addAction("&" + label)
            action.triggered.connect(
                lambda checked=False, k=key: self.navigate(k))
            self._nav_actions[key] = action
        self._nav_actions[PAGE_RAID_REPORT].setShortcut(
            QKeySequence("Ctrl+R"))
        self._nav_actions[PAGE_PROCESS_FILES].setShortcut(
            QKeySequence("Ctrl+E"))
        # Calibration is an AI surface — its action gates with the nav
        # entry (invisible = absent from the menu, LAW #2).
        self.action_calibration = self._nav_actions[PAGE_CALIBRATION]
        self.action_calibration.setVisible(self._ai_enabled())
        tools_menu.addSeparator()
        self.action_settings = tools_menu.addAction("&Settings")
        self.action_settings.triggered.connect(
            lambda checked=False: self._open_settings_from_menu())

        help_menu = bar.addMenu("&Help")
        self.action_about = help_menu.addAction("&About SparkyBot")
        self.action_about.triggered.connect(self._show_about)

    def _build_central(self):
        """Sidebar (QListWidget) + page stack (QStackedWidget)."""
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = QListWidget()
        theme.set_widget_class(self.sidebar, "nav")
        self.sidebar.setFixedWidth(176)
        for key, label in NAV_ENTRIES:
            if key in _AI_ONLY_PAGES and not self._ai_enabled():
                continue      # absent, not grayed (LAW #2)
            self._add_nav_item(key, label)
        self.sidebar.currentRowChanged.connect(self._on_nav_changed)
        layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        # One container per page key — containers exist for gated pages too
        # (cheap empty holders), only the nav entry comes and goes. Lazy
        # pages fill theirs on first visit so an unvisited page costs
        # nothing at window open.
        self._containers = {}
        self._built = set()
        for key, _label in NAV_ENTRIES:
            holder = QWidget()
            holder_layout = QVBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            self._containers[key] = holder
            self.stack.addWidget(holder)

        self._mount(PAGE_HOME, self._build_home_page())
        # Eager: the app controller connects process_files_widget signals
        # right after constructing this window, so the queue cannot be lazy.
        # It is cheap — pure widgets, no I/O.
        self.process_files_widget = ProcessFilesWidget(self.config)
        self._mount(PAGE_PROCESS_FILES, self.process_files_widget)
        # The Settings page is a stub behind the modal dialog (selecting the
        # sidebar entry opens the dialog; the button covers reopening it
        # while the entry is already current).
        self._mount(PAGE_SETTINGS, self._build_settings_page())
        self.sidebar.itemClicked.connect(self._on_sidebar_clicked)

        self.setCentralWidget(central)

    def _build_status_bar(self):
        """Watcher indicator (state dot + text), the "Last: ..." feed echo,
        and the quiet update banner.

        The dot is U+25CF BLACK CIRCLE — a plain text glyph, not an emoji;
        QSS colors it via the status-dot class + state property.
        """
        bar = self.statusBar()
        self.status_dot = QLabel("●")
        theme.set_widget_class(self.status_dot, "status-dot")
        self.status_text = QLabel("Watcher stopped")
        bar.addWidget(self.status_dot)
        bar.addWidget(self.status_text)
        # "Last: fight posted 8:14 PM" — updated from feed events.
        self.status_last_label = QLabel("")
        theme.mark_hint(self.status_last_label)
        bar.addWidget(self.status_last_label)
        # Quiet "Update available — Install vX.Y.Z" button; hidden until the
        # launch check finds something.
        self.update_banner = QPushButton("")
        self.update_banner.setVisible(False)
        self.update_banner.clicked.connect(self._on_install_update_clicked)
        bar.addPermanentWidget(self.update_banner)

    def _build_home_page(self) -> QWidget:
        """The real Home (HOME slice): run panel (run-button mode only),
        watcher status row with the small independent toggle, and the
        model-backed activity feed. Drag-dropping .evtc/.zevtc anywhere on
        the page routes to the Process Files queue."""
        page = HomePage()
        page.sig_logs_dropped.connect(self._on_home_logs_dropped)
        self.home_page = page
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        self._home_layout = layout

        # Run panel — present ONLY in run-button mode; in manual mode it is
        # absent (not built, not hidden). _sync_run_panel_mode() flips it
        # live when the Settings switch changes. Inserted at the END of this
        # builder (still visually on top) because resuming a persisted run
        # writes a feed row, so the feed must exist first.
        self.run_panel = None
        self.run_button = None
        self.run_status_label = None
        self.run_hint_label = None
        self.run_stale_hint = None
        self.run_progress = None
        self.run_stale_banner = None
        self.run_stale_label = None

        # Watcher row: status text + the small independent start/stop
        # toggle. Same watcher_toggled / set_watcher_state contract and the
        # same primary/state styling pattern as always.
        watcher_row = QHBoxLayout()
        self.home_status_label = QLabel("Watcher is stopped.")
        watcher_row.addWidget(self.home_status_label)
        watcher_row.addStretch()
        self.start_button = QPushButton("Start Watcher")
        theme.set_widget_class(self.start_button, "secondary")
        self.start_button.clicked.connect(self._on_start_clicked)
        watcher_row.addWidget(self.start_button)
        layout.addLayout(watcher_row)

        # Activity feed — the live answer to "what did the bot just do".
        feed_header = QLabel("Activity")
        theme.set_variant(feed_header, "heading")
        layout.addWidget(feed_header)
        self.activity_hint = QLabel(
            "Nothing yet — detected fights and their progress will appear here.")
        self.activity_hint.setWordWrap(True)
        theme.mark_hint(self.activity_hint)
        layout.addWidget(self.activity_hint)

        self.activity_model = ActivityFeedModel(self)
        self.activity_view = QListView()
        self.activity_view.setModel(self.activity_model)
        self.activity_view.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.activity_view.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.activity_view.setUniformItemSizes(True)
        self.activity_view.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.activity_view.customContextMenuRequested.connect(
            self._on_feed_context_menu)
        layout.addWidget(self.activity_view, 1)

        if self._run_panel_allowed():
            self._insert_run_panel()

        return page

    # ------------------------------------------------------------------
    # run panel presence (RaidReport/runMode)
    # ------------------------------------------------------------------

    def _run_panel_allowed(self) -> bool:
        """Run UI exists only in run-button mode (usage-mode preference)."""
        return getattr(self.config, "raidreport_run_mode",
                       "run-button") == "run-button"

    def _insert_run_panel(self):
        """Build the run panel at the top of Home. The big button reuses the
        slice-1 primary/state pattern (state="running" paints the open-run
        red stop look via the central QSS)."""
        panel = QGroupBox("Run")
        v = QVBoxLayout(panel)
        v.setSpacing(6)

        # Quiet next-launch banner for a run the operator forgot to end
        # (never a popup). Hidden unless _init_run_state finds one stale.
        banner = QWidget()
        bv = QVBoxLayout(banner)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(4)
        self.run_stale_label = QLabel("")
        self.run_stale_label.setWordWrap(True)
        theme.set_widget_class(self.run_stale_label, "warn-banner")
        bv.addWidget(self.run_stale_label)
        stale_buttons = QHBoxLayout()
        self.run_stale_end_button = QPushButton("End run && make report")
        self.run_stale_end_button.clicked.connect(self._end_stale_run)
        stale_buttons.addWidget(self.run_stale_end_button)
        self.run_stale_discard_button = QPushButton("Discard")
        self.run_stale_discard_button.clicked.connect(self._discard_stale_run)
        stale_buttons.addWidget(self.run_stale_discard_button)
        stale_buttons.addStretch()
        bv.addLayout(stale_buttons)
        banner.setVisible(False)
        self.run_stale_banner = banner
        v.addWidget(banner)

        self.run_status_label = QLabel("No run in progress.")
        self.run_status_label.setWordWrap(True)
        v.addWidget(self.run_status_label)

        self.run_button = QPushButton("Start Run")
        theme.set_widget_class(self.run_button, "primary")
        self.run_button.setMinimumHeight(36)
        self.run_button.clicked.connect(self._on_run_button_clicked)
        v.addWidget(self.run_button, 0, Qt.AlignmentFlag.AlignLeft)

        # Inline stage-weighted progress for the End Run report — no page
        # jump; renders right under the primary button.
        self.run_progress = QProgressBar()
        self.run_progress.setRange(0, 100)
        self.run_progress.setVisible(False)
        v.addWidget(self.run_progress)

        self.run_hint_label = QLabel(self._IDLE_HINT)
        self.run_hint_label.setWordWrap(True)
        theme.mark_hint(self.run_hint_label)
        v.addWidget(self.run_hint_label)

        # Passive warn-tint hint once a run has been open a very long time.
        self.run_stale_hint = QLabel("")
        self.run_stale_hint.setWordWrap(True)
        theme.set_state(self.run_stale_hint, "warn")
        self.run_stale_hint.setVisible(False)
        v.addWidget(self.run_stale_hint)

        self.run_panel = panel
        self._home_layout.insertWidget(0, panel)
        self._init_run_state()

    def _remove_run_panel(self):
        self._run_tick_timer.stop()
        self._run_scan_timer.stop()
        panel = self.run_panel
        self._home_layout.removeWidget(panel)
        panel.setParent(None)
        panel.deleteLater()
        self.run_panel = None
        self.run_button = None
        self.run_status_label = None
        self.run_hint_label = None
        self.run_stale_hint = None
        self.run_progress = None
        self.run_stale_banner = None
        self.run_stale_label = None

    def _sync_run_panel_mode(self):
        """React live to the Settings 'How reports get made' switch."""
        allowed = self._run_panel_allowed()
        if allowed and self.run_panel is None:
            self._insert_run_panel()
        elif not allowed and self.run_panel is not None:
            self._remove_run_panel()
            # Dormant in manual mode: any state file on disk is left
            # untouched, but nothing reads or writes it.
            self._run_session = None

    # ------------------------------------------------------------------
    # run session mechanics
    # ------------------------------------------------------------------

    _IDLE_HINT = ("Starts watching your log folder and counts every fight "
                  "until you end the run.")
    _OPEN_HINT = "Skipped fights still count for raid reports."

    def _init_run_state(self):
        """Load persisted run state when the panel comes up: resume a fresh
        run silently, offer finish-or-discard for a stale one."""
        home = getattr(self.config, "home_dir", None)
        if home is None:
            self._run_session = None     # test stub configs — no state I/O
            self._show_run_idle()
            return
        self._run_session = RunSession(home, clock=self._clock)
        if not self._run_session.is_open:
            self._show_run_idle()
            return
        logs = self._discover_run_logs()
        in_window = fights_in_window(logs, self._run_session.started_at)
        newest = (in_window[-1].timestamp if in_window
                  else self._run_session.started_at)
        self._show_run_open()
        if self._clock() - newest < timedelta(hours=RESUME_WINDOW_HOURS):
            self.feed_event("run", "Run resumed")
        else:
            self._show_stale_banner(len(in_window))

    def _run_open(self) -> bool:
        return self._run_session is not None and self._run_session.is_open

    def _discover_run_logs(self):
        """Same source of truth as the raid report: discover_logs over the
        first configured log folder."""
        folders = self.config.get_log_folders()
        if not folders:
            return []
        try:
            return discover_logs(folders[0])
        except OSError:
            logger.warning("Log discovery failed", exc_info=True)
            return []

    def _on_run_button_clicked(self):
        self._toggle_run()

    def _toggle_run(self):
        """Start Run <-> End Run (the panel's one morphing button)."""
        if self._run_session is None or self._run_report_busy:
            return
        if self._run_session.is_open:
            self._request_end_run()
        else:
            self._start_run()

    def _start_run(self):
        self._run_session.start()
        if not self._watcher_running:
            self.watcher_toggled.emit()   # Start Run subsumes the watcher
        self.feed_event("run", "Run started")
        self._hide_stale_banner()
        self._show_run_open()

    def _request_end_run(self):
        """End Run: exactly ONE confirm dialog, remembered auto-post."""
        session = self._run_session
        logs = self._discover_run_logs()
        count = len(fights_in_window(logs, session.started_at))
        elapsed_text = format_elapsed(session.elapsed())
        confirmed, auto_post = self._exec_end_run_dialog(count, elapsed_text)
        if not confirmed:
            return

        if count and auto_post != bool(self.config.run_auto_post):
            # Remembered choice — the same key Settings > Raid Reports edits.
            self.config.update('RaidReport', 'runAutoPost',
                               'true' if auto_post else 'false')
            self.config.save()

        started, ended = session.end()
        selected = fights_in_window(self._discover_run_logs(), started, ended)
        if not selected:
            self.feed_event(
                "run",
                "Run ended — no fights were recorded, nothing was posted.")
            self._show_run_idle()
            if self._quit_after_run:
                self._quit_app_now()
            return
        name = (f"Raid Report {selected[-1].timestamp:%Y-%m-%d} "
                f"({len(selected)} fights)")
        self._start_run_report(selected, name, auto_post)

    def _exec_end_run_dialog(self, fight_count: int, elapsed_text: str):
        """The one End Run confirm. Returns (confirmed, auto_post)."""
        box = QMessageBox(self)
        box.setWindowTitle("End run?")
        box.setIcon(QMessageBox.Icon.Question)
        if fight_count == 0:
            box.setText("No fights were recorded during this run — "
                        "nothing to post.")
            end_btn = box.addButton(
                "End Run", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            return (box.clickedButton() is end_btn, False)

        fights = f"{fight_count} fight{'s' if fight_count != 1 else ''}"
        box.setText(f"{fights} · {elapsed_text}\n\n"
                    "A raid report will be made for these fights.")
        check = QCheckBox("Post it to Discord when done")
        check.setChecked(bool(getattr(self.config, "run_auto_post", True)))
        box.setCheckBox(check)
        end_btn = box.addButton(
            "End Run && Make Report", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return (box.clickedButton() is end_btn, check.isChecked())

    # ------------------------------------------------------------------
    # run panel states
    # ------------------------------------------------------------------

    def _show_run_idle(self):
        self._run_tick_timer.stop()
        self._run_scan_timer.stop()
        self._run_fight_count = 0
        if self.run_panel is None:
            return
        self.run_button.setText("Start Run")
        self.run_button.setEnabled(True)
        theme.set_state(self.run_button, None)
        theme.set_state(self.run_status_label, None)
        self.run_status_label.setText("No run in progress.")
        self.run_hint_label.setText(self._IDLE_HINT)
        self.run_progress.setVisible(False)
        self.run_progress.setValue(0)
        self.run_stale_hint.setVisible(False)

    def _show_run_open(self):
        if self.run_panel is None:
            return
        self.run_button.setText("End Run…")
        self.run_button.setEnabled(True)
        theme.set_state(self.run_button, "running")
        theme.set_state(self.run_status_label, None)
        self.run_hint_label.setText(self._OPEN_HINT)
        self.run_progress.setVisible(False)
        self._refresh_run_counter()
        self._run_tick_timer.start()
        self._run_scan_timer.start()

    def _refresh_run_counter(self):
        """Fights-so-far from discover_logs over the open window — refreshed
        on every file-processed signal plus the slow timer."""
        if not self._run_open():
            return
        logs = self._discover_run_logs()
        self._run_fight_count = len(
            fights_in_window(logs, self._run_session.started_at))
        self._update_run_panel_clock()

    def _update_run_panel_clock(self):
        if not self._run_open() or self.run_panel is None \
                or self._run_report_busy:
            return
        elapsed = self._run_session.elapsed()
        count = self._run_fight_count
        fights = f"{count} fight{'s' if count != 1 else ''}"
        started = format_clock(self._run_session.started_at)
        self.run_status_label.setText(
            f"Run open {format_elapsed(elapsed)} · {fights} · "
            f"started {started}")
        # Passive warn-tint hint for a very long open run — never a popup.
        stale = elapsed > timedelta(hours=STALE_HINT_HOURS)
        if stale:
            self.run_stale_hint.setText(
                f"This run has been open {format_elapsed(elapsed)} — "
                "forgot to end it?")
        self.run_stale_hint.setVisible(stale)

    # ------------------------------------------------------------------
    # stale-run banner (quiet next-launch finish-or-discard)
    # ------------------------------------------------------------------

    def _show_stale_banner(self, fight_count: int):
        if self.run_stale_banner is None:
            return
        started = self._run_session.started_at
        fights = f"{fight_count} fight{'s' if fight_count != 1 else ''}"
        self.run_stale_label.setText(
            f"A run from {started:%a} {format_clock(started)} is still "
            f"open ({fights}).")
        self.run_stale_banner.setVisible(True)

    def _hide_stale_banner(self):
        if self.run_stale_banner is not None:
            self.run_stale_banner.setVisible(False)

    def _end_stale_run(self):
        """Banner action: end at the LAST fight's timestamp (not now), so
        ending the next morning never sweeps in newer logs."""
        logs = self._discover_run_logs()
        started = self._run_session.started_at
        in_window = fights_in_window(logs, started)
        self._hide_stale_banner()
        if not in_window:
            self._run_session.discard()
            self.feed_event(
                "run",
                "Run ended — no fights were recorded, nothing was posted.")
            self._show_run_idle()
            return
        ended_at = in_window[-1].timestamp
        started, ended = self._run_session.end(ended_at=ended_at)
        selected = fights_in_window(logs, started, ended)
        name = (f"Raid Report {selected[-1].timestamp:%Y-%m-%d} "
                f"({len(selected)} fights)")
        self._start_run_report(selected, name,
                               bool(self.config.run_auto_post))

    def _discard_stale_run(self):
        self._run_session.discard()
        self.feed_event("run", "Run discarded")
        self._hide_stale_banner()
        self._show_run_idle()

    # ------------------------------------------------------------------
    # End Run report (existing discover_logs -> runner -> publish path)
    # ------------------------------------------------------------------

    def _start_run_report(self, selected: list, name: str, auto_post: bool,
                          quit_after: bool = False):
        """Generate (and optionally post) the run's report on a worker
        thread with the inline stage-weighted progress bar."""
        self._run_report_busy = True
        self._run_auto_posted = auto_post
        self._quit_after_run = self._quit_after_run or quit_after
        self._run_tick_timer.stop()
        self._run_scan_timer.stop()
        if self.run_panel is not None:
            self.run_button.setText("Start Run")
            theme.set_state(self.run_button, None)
            self.run_button.setEnabled(False)
            theme.set_state(self.run_status_label, "busy")
            self.run_status_label.setText("Getting things ready…")
            self.run_hint_label.setText(self._OPEN_HINT)
            self.run_stale_hint.setVisible(False)
            self.run_progress.setValue(0)
            self.run_progress.setVisible(True)

        config = self.config

        def _work():
            try:
                from core.raid_report_wiring import make_runner, publish_result
                runner = make_runner(config)
                result = runner.generate(
                    selected, name,
                    progress=lambda stage, cur, tot, detail:
                        self.sig_run_progress.emit(stage, cur, tot, detail))
                if auto_post:
                    publish_result(config, result)
                self.sig_run_done.emit(result)
            except Exception as exc:
                logger.exception("End Run report failed")
                self.sig_run_error.emit(str(exc))

        threading.Thread(target=_work, daemon=True).start()

    def _on_run_progress(self, stage: str, current: int, total: int,
                         detail: str):
        if self.run_panel is None:
            return
        base = _STAGE_BASE.get(stage, 0)
        span = _STAGE_SPAN.get(stage, 0)
        frac = (current / total) if total > 0 else 0
        # Stage-weighted: only reaches 100% when the report is done.
        self.run_progress.setValue(min(int(base + span * frac), 99))
        messages = {
            "parse": f"Reading fight {current} of {total}…",
            "combine": "Crunching the numbers…",
            "bake": "Building your page…",
        }
        msg = messages.get(stage)
        if msg:
            self.run_status_label.setText(msg)

    def _on_run_done(self, result):
        self._run_report_busy = False
        posted = self._run_auto_posted
        verb = "posted" if posted else "ready"
        self.feed_event("report", f"Raid report {verb} — {result.name}")
        self._show_run_idle()
        if self.run_panel is not None:
            theme.set_state(self.run_status_label, "ok")
            self.run_status_label.setText(
                f"Report {verb} — {result.name}")
        if self._quit_after_run:
            self._quit_app_now()

    def _on_run_error(self, message: str):
        self._run_report_busy = False
        self._quit_after_run = False   # never quit into an unseen error
        first_line = message.splitlines()[0][:160] if message else "unknown"
        self.feed_event("error", f"End Run report failed — {first_line}")
        self._show_run_idle()
        if self.run_panel is not None:
            theme.set_state(self.run_status_label, "error")
            self.run_status_label.setText("The report could not be made.")
        self._show_run_error_dialog(message)

    def _show_run_error_dialog(self, message: str):
        box = QMessageBox(self)
        box.setWindowTitle("End Run report failed")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("The raid report could not be made or posted.\n"
                    "Your fights are safe — you can build the report any "
                    "time on the Raid Report page.")
        box.setInformativeText(message)
        box.exec()

    # ------------------------------------------------------------------
    # activity feed API (called by the app controller per fight)
    # ------------------------------------------------------------------

    def feed_event(self, kind: str, text: str, when=None):
        """Append a feed row and echo posted events into the status bar."""
        if when is None:
            when = self._clock()
        self.activity_model.add(kind, text, when)
        if self.activity_hint is not None:
            self.activity_hint.setVisible(False)
        if kind == "posted":
            self.status_last_label.setText(
                f"Last: fight posted {format_clock(when)}")
        elif kind == "report":
            self.status_last_label.setText(
                f"Last: report posted {format_clock(when)}")

    def feed_file_event(self, filename: str, result_name: str,
                        detail: str = ""):
        """Per-fight pipeline outcome -> feed row. `result_name` is a
        ProcessResult value string; `detail` carries the exact failed
        filter for skips (named at the decision site in main.py)."""
        name = Path(filename).name
        if result_name == "success":
            self.feed_event("posted", f"Fight posted — {name}")
        elif result_name == "skipped_threshold":
            reason = detail or "did not pass the posting filters"
            self.feed_event("skipped", f"{name} — {reason}")
        elif result_name == "error_discord":
            self.feed_event(
                "error", f"{name} processed but the Discord post failed")
        else:
            self.feed_event("error", f"{name} failed to process")
        # Every processed file may change fights-so-far (skips count too).
        if self._run_open():
            self._refresh_run_counter()

    def _on_feed_context_menu(self, pos):
        index = self.activity_view.indexAt(pos)
        if not index.isValid():
            return
        menu = QMenu(self.activity_view)
        copy_action = menu.addAction("Copy line")
        copy_action.triggered.connect(
            lambda checked=False, row=index.row(): self.copy_feed_line(row))
        menu.exec(self.activity_view.viewport().mapToGlobal(pos))

    def copy_feed_line(self, row: int):
        """Right-click Copy line: put the formatted row on the clipboard."""
        line = self.activity_model.line(row)
        if line:
            QApplication.clipboard().setText(line)

    # ------------------------------------------------------------------
    # drag-drop routing + update banner
    # ------------------------------------------------------------------

    def _on_home_logs_dropped(self, paths: list):
        """Home drop -> Process Files page with the queue pre-filled."""
        self.process_files_widget.add_files(paths)
        self.navigate(PAGE_PROCESS_FILES)

    def _on_update_launch_available(self, version: str, release_data: dict):
        self._launch_update = (version, release_data)
        self.update_banner.setText(f"Update available — Install v{version}")
        self.update_banner.setVisible(True)

    def _on_install_update_clicked(self):
        if self._launch_update is None or self.update_flow is None:
            return
        version, release_data = self._launch_update
        self._launch_update = None
        self.update_banner.setVisible(False)
        self.update_flow.start_update(release_data, version)

    def _build_settings_page(self) -> QWidget:
        """Stub page behind the modal Settings dialog: selecting the sidebar
        entry opens the dialog; this page hosts a reopen button for when the
        entry is already selected."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(8)

        title = QLabel("Settings")
        theme.set_variant(title, "title")
        layout.addWidget(title)

        blurb = QLabel(
            "All SparkyBot options live in the Settings dialog: Discord, "
            "fight reports, watcher and parsing, raid reports, Twitch, "
            "and application behavior."
        )
        blurb.setWordWrap(True)
        theme.mark_hint(blurb)
        layout.addWidget(blurb)

        layout.addSpacing(12)
        self.open_settings_button = QPushButton("Open Settings...")
        self.open_settings_button.clicked.connect(self.open_settings_dialog)
        layout.addWidget(self.open_settings_button, 0,
                         Qt.AlignmentFlag.AlignLeft)

        layout.addStretch()
        return page

    # ------------------------------------------------------------------
    # navigation / lazy pages (key-addressed — rows are dynamic)
    # ------------------------------------------------------------------

    def _ai_enabled(self) -> bool:
        """LAW #2 master switch — the existing AI/enableAiAnalysis key."""
        return bool(getattr(self.config, "enable_ai_analysis", False))

    def _add_nav_item(self, key: str, label: str, row: int = None):
        item = QListWidgetItem(label)
        item.setData(_NAV_ROLE, key)
        if row is None:
            self.sidebar.addItem(item)
        else:
            self.sidebar.insertItem(row, item)

    def _nav_row_of(self, key: str) -> int:
        """Current sidebar row for a page key, -1 when the entry is absent
        (AI-gated). Never cache the result — rows shift."""
        for row in range(self.sidebar.count()):
            if self.sidebar.item(row).data(_NAV_ROLE) == key:
                return row
        return -1

    def nav_keys(self) -> list:
        """Page keys currently offered in the sidebar, top to bottom."""
        return [self.sidebar.item(row).data(_NAV_ROLE)
                for row in range(self.sidebar.count())]

    @property
    def current_page(self):
        """Key of the selected sidebar entry (None before selection)."""
        item = self.sidebar.currentItem()
        return item.data(_NAV_ROLE) if item is not None else None

    def _mount(self, key: str, widget: QWidget):
        """Put real content into a page container and mark it built."""
        self._containers[key].layout().addWidget(widget)
        # QTabWidget leaves a removed non-current tab explicitly hidden.
        # These promoted pages are re-homed into the sidebar, so clear that
        # stale hidden state or the container appears completely blank.
        widget.setVisible(True)
        self._built.add(key)

    def navigate(self, key: str):
        """Select a sidebar entry by key (builds the page on first visit).
        A key whose entry is absent (AI-gated) is ignored."""
        row = self._nav_row_of(key)
        if row >= 0:
            self.sidebar.setCurrentRow(row)

    def _on_nav_changed(self, row: int):
        if row < 0:
            return
        item = self.sidebar.item(row)
        key = item.data(_NAV_ROLE) if item is not None else None
        if key is None:
            return
        if key not in self._built:
            # The remaining lazy pages (Raid Report, Calibration) are the
            # promoted action tabs of the settings engine.
            self._ensure_settings()
        self.stack.setCurrentWidget(self._containers[key])
        if key == PAGE_RAID_REPORT and self._raid_report_page is not None:
            # Lists that show folder contents rescan on every page open.
            self._raid_report_page.rescan()
        if key == PAGE_SETTINGS:
            self.open_settings_dialog()

    def _on_sidebar_clicked(self, item):
        """Clicking the already-selected Settings entry reopens the dialog
        (currentRowChanged does not fire for a same-row click)."""
        if item.data(_NAV_ROLE) == PAGE_SETTINGS:
            self.open_settings_dialog()

    # ------------------------------------------------------------------
    # AI gating (LAW #2) — the master switch takes effect live
    # ------------------------------------------------------------------

    def _nav_insert_row(self, key: str) -> int:
        """Row where `key` belongs, given which entries are present now:
        right after the last present entry that precedes it in NAV_ENTRIES
        order."""
        order = [k for k, _ in NAV_ENTRIES]
        before = set(order[:order.index(key)])
        row = 0
        for r in range(self.sidebar.count()):
            if self.sidebar.item(r).data(_NAV_ROLE) in before:
                row = r + 1
        return row

    def _sync_ai_gating(self):
        """React live to a saved flip of the AI master switch: the
        Calibration sidebar entry and its Tools-menu action exist only
        while AI features are enabled — inserted/removed on every settings
        save, never grayed, no restart. (The Settings dialog gates its own
        AI categories; commentary/voice feed rows are pipeline-gated.)"""
        ai_on = self._ai_enabled()
        self.action_calibration.setVisible(ai_on)
        row = self._nav_row_of(PAGE_CALIBRATION)
        if ai_on and row < 0:
            self._add_nav_item(PAGE_CALIBRATION,
                               _NAV_LABELS[PAGE_CALIBRATION],
                               row=self._nav_insert_row(PAGE_CALIBRATION))
        elif not ai_on and row >= 0:
            if self.current_page == PAGE_CALIBRATION:
                self.navigate(PAGE_HOME)
            self.sidebar.takeItem(self._nav_row_of(PAGE_CALIBRATION))

    def _open_settings_from_menu(self):
        """Menu path: land the sidebar on Settings, then open the dialog."""
        self.navigate(PAGE_SETTINGS)   # opens the dialog when the row changes
        self.open_settings_dialog()    # ...and when it was already current

    def open_settings_dialog(self):
        """Open the modal Settings dialog (lazy singleton over the engine)."""
        self._ensure_settings()
        if self._settings_dialog is None:
            from core.settings_dialog import SettingsDialog
            self._settings_dialog = SettingsDialog(self._settings, parent=self)
        if self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return self._settings_dialog
        self._settings_dialog.open_dialog()
        return self._settings_dialog

    def _ensure_settings(self):
        """Build the headless SettingsWindow engine on first need.

        Lazy so opening the main window constructs none of the legacy
        tabs (and nothing touches GitHub — update checks wait for the
        Settings dialog's Application page). The action tabs promoted to
        sidebar pages are stripped out of the engine's tab widget and
        re-mounted; the engine keeps its full attribute surface so
        save/load and the thread-signal wiring are untouched. The engine
        itself is never shown — the modal SettingsDialog re-homes its
        controls into category pages.
        """
        if self._settings is not None:
            return self._settings

        settings = SettingsWindow(self.config, update_flow=self.update_flow)
        settings._tts_client = self.__tts_client
        # Forward the old window's signal contract through this shell.
        settings.settings_changed.connect(self.settings_changed)
        settings.watcher_toggled.connect(self.watcher_toggled)

        tabs = settings.tab_widget

        def _take(title: str):
            for i in range(tabs.count()):
                if tabs.tabText(i) == title:
                    widget = tabs.widget(i)
                    tabs.removeTab(i)
                    return widget
            return None

        raid_report = _take("Raid Report")
        if raid_report is not None:
            self._raid_report_page = raid_report
            raid_report.sig_process_files_requested.connect(
                lambda: self.navigate(PAGE_PROCESS_FILES))
            self._mount(PAGE_RAID_REPORT, raid_report)
        calibration = _take("Calibration")
        if calibration is not None:
            self._mount(PAGE_CALIBRATION, calibration)

        settings.set_watcher_state(self._watcher_running)

        self._settings = settings
        return settings

    # ------------------------------------------------------------------
    # watcher state / app-controller surface
    # ------------------------------------------------------------------

    def _on_start_clicked(self):
        """Ask the app controller to toggle the watcher."""
        self.watcher_toggled.emit()

    def set_watcher_state(self, running: bool):
        """Update every watcher-state surface in the shell at once."""
        self._watcher_running = running
        if running:
            self.start_button.setText("Stop Watcher")
            theme.set_state(self.start_button, "running")
            self.action_watcher.setText("Stop &Watcher")
            self.home_status_label.setText("Watcher is running.")
            self.status_text.setText("Watcher running")
            theme.set_state(self.status_dot, "running")
        else:
            self.start_button.setText("Start Watcher")
            theme.set_state(self.start_button, "stopped")
            self.action_watcher.setText("Start &Watcher")
            self.home_status_label.setText("Watcher is stopped.")
            self.status_text.setText("Watcher stopped")
            theme.set_state(self.status_dot, None)
        if self._settings is not None:
            self._settings.set_watcher_state(running)

    @property
    def _tts_client(self):
        """TTS client reference the app controller stashes on the window
        (legacy contract). Forwarded to the settings host so its TTS test
        button works whether the host exists yet or not."""
        return self.__tts_client

    @_tts_client.setter
    def _tts_client(self, client):
        self.__tts_client = client
        if self._settings is not None:
            self._settings._tts_client = client

    # ------------------------------------------------------------------
    # window lifecycle
    # ------------------------------------------------------------------

    def _quit_app(self):
        """Explicit quit request (File > Exit, or closeEvent fallthrough).
        With a run open this becomes the 3-way confirm."""
        if self._run_confirm_needed():
            choice = self._confirm_quit_with_run()
            if choice == "tray":
                self.hide()
            elif choice == "end":
                self._end_run_and_quit()
            return
        self._quit_app_now()

    def _quit_app_now(self):
        QApplication.instance().quit()

    # ------------------------------------------------------------------
    # quit-with-open-run confirm
    # ------------------------------------------------------------------

    def _run_confirm_needed(self) -> bool:
        return self._run_open() and not self._quit_after_run

    def _build_quit_confirm(self):
        """3-way confirm for quitting with an open run. Returns
        (box, {"tray"|"end"|"cancel": button})."""
        box = QMessageBox(self)
        box.setWindowTitle("A run is still open")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("A run is still open.")
        box.setInformativeText(
            "Keep SparkyBot running in the tray, or end the run and quit? "
            "Ending the run makes its report first.")
        keep_btn = box.addButton(
            "Keep running in tray", QMessageBox.ButtonRole.AcceptRole)
        end_btn = box.addButton(
            "End run && quit", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton(
            "Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep_btn)
        return box, {"tray": keep_btn, "end": end_btn, "cancel": cancel_btn}

    def _confirm_quit_with_run(self) -> str:
        box, buttons = self._build_quit_confirm()
        box.exec()
        clicked = box.clickedButton()
        for name, btn in buttons.items():
            if btn is clicked:
                return name
        return "cancel"

    def _end_run_and_quit(self):
        """Quit path 'End run & quit': the 3-way WAS the confirm, so the
        run ends with the remembered auto-post setting, the inline report
        runs to completion, then the app quits."""
        started, ended = self._run_session.end()
        selected = fights_in_window(self._discover_run_logs(), started, ended)
        if not selected:
            self.feed_event(
                "run",
                "Run ended — no fights were recorded, nothing was posted.")
            self._show_run_idle()
            self._quit_app_now()
            return
        name = (f"Raid Report {selected[-1].timestamp:%Y-%m-%d} "
                f"({len(selected)} fights)")
        self._start_run_report(selected, name,
                               bool(self.config.run_auto_post),
                               quit_after=True)

    def _show_about(self):
        """Small About box; full credits live on Settings > Application."""
        QMessageBox.about(
            self, "About SparkyBot",
            f"SparkyBot v{VERSION}\n\n"
            "Guild Wars 2 fight log reporter.\n"
            "Full credits: Settings > Application.")

    def closeEvent(self, event):
        """X button: hide to tray or quit, per config (same semantics the
        old window had — but quitting is explicit now because the app runs
        with quitOnLastWindowClosed off). With a run open, closing always
        goes through the 3-way confirm first."""
        if self._run_confirm_needed() and not self.config.close_to_tray:
            choice = self._confirm_quit_with_run()
            event.ignore()
            if choice == "tray":
                self.hide()
            elif choice == "end":
                self._end_run_and_quit()
            return
        if self.config.close_to_tray:
            event.ignore()
            self.hide()
        else:
            event.accept()
            self._quit_app()

    def changeEvent(self, event):
        """Minimize button: hide to tray if configured (legacy behavior)."""
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized() and self.config.minimize_to_tray:
                event.ignore()
                self.hide()
                return
        super().changeEvent(event)
