"""Raid Report GUI tab — injected callables keep tests independent of the real pipeline.

Fight picking: a compact checkbox table with sortable Date / Fight /
Size columns. Clicking a row ticks its box; shift-click ticks the range.
Checkboxes are the only visual state — hover/selection painting is
suppressed so the table stays flat and quiet like a native file list.
"""

import logging
import threading
import time

from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QLineEdit, QProgressBar, QSizePolicy, QHeaderView,
    QMessageBox, QAbstractItemView,
)
from PySide6.QtCore import Qt, Signal, Slot, QUrl, QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QDesktopServices

from core import theme
from core.raid_report import ReportResult
from core.raid_report_progress import progress_state
from core.raid_session import LogInfo

logger = logging.getLogger(__name__)

_ROLE_LOG = Qt.ItemDataRole.UserRole
_ROLE_SORT = Qt.ItemDataRole.UserRole + 1

_COL_CHECK = 0
_COL_DATE = 1
_COL_FIGHT = 2
_COL_SIZE = 3

class _SortableItem(QTableWidgetItem):
    """Sorts by the numeric _ROLE_SORT payload, not the display text."""

    def __lt__(self, other):
        return ((self.data(_ROLE_SORT) or 0)
                < (other.data(_ROLE_SORT) or 0))


class RaidReportTab(QWidget):
    sig_progress = Signal(str, int, int, str)
    sig_done = Signal(object)
    sig_error = Signal(str)
    sig_publish_done = Signal(bool, str)
    sig_process_files_requested = Signal()
    # Background discover thread -> GUI thread. The log folder is typically a
    # network share; scanning it on the GUI thread froze the window whenever
    # the share hiccuped (ticket 6c8e08f9). (scoped result, sizes, preserve, chip)
    sig_logs_discovered = Signal(object, object, object, object)

    def __init__(self, *, discover, select_session, select_today,
                 select_recent=None, runner_factory, publish,
                 discovery_root=None, parent=None):
        super().__init__(parent)

        self._discover = discover
        self._discovery_root = discovery_root
        self._select_session = select_session
        self._select_today = select_today
        self._select_recent = select_recent
        self._runner_factory = runner_factory
        self._publish_fn = publish

        self.sig_progress.connect(self._on_progress)
        self.sig_done.connect(self._on_done)
        self.sig_error.connect(self._on_error)
        self.sig_publish_done.connect(self._on_publish_done)
        self.sig_logs_discovered.connect(
            self._on_logs_discovered, Qt.ConnectionType.QueuedConnection)

        self._running = False
        self._discovering = False
        self._scope = discovery_root() if discovery_root else None
        self._rows_scope = self._scope
        self._epoch = 0
        self._request_id = 0
        self._trailing = False
        self._entry_waiting = False
        self._after_generation = False
        self._deferred_discovery = None
        self._complete_at = None
        self._list_state = "loading"
        self._list_error = ""
        self._initial_chip = "recent"
        self._retry_left = 1
        self._logs: list[LogInfo] = []
        self._log_sizes: dict = {}
        self._error_details = ""
        self.last_result: Optional[ReportResult] = None

        self._setup_ui()
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.setInterval(1500)
        self._retry_timer.timeout.connect(self._retry_discovery)
        self._freshness_timer = QTimer(self)
        self._freshness_timer.setInterval(60000)
        self._freshness_timer.timeout.connect(self.rescan)
        # Async initial scan; the "recent" quick-pick applies when it lands.
        self._refresh(chip="recent")

    # ------------------------------------------------------------------
    # ui
    # ------------------------------------------------------------------

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 8, 12, 8)

        # 1. Header
        header = QLabel("<b>Combined Fight Log Summary</b>")
        header.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(header)

        # 2. Description
        desc = QLabel(
            "One click turns your latest fights into a stats page "
            "you can share with your guild. Big reports shrink automatically "
            "to help them fit Discord's free upload limit. "
            "Click a fight to tick it; "
            "hold Shift and click to tick a whole range. Click a column "
            "heading to sort."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # 3. Quick-pick row.  Keep the discovery/selection count on its own
        # line: the packaged default window leaves about 400 px for this page,
        # and placing the count after all four chips clipped it to
        # "No fights foun..." on a real Windows desktop.
        quick_picks = QHBoxLayout()
        pick_label = QLabel("Quick pick:")
        theme.mark_hint(pick_label)
        quick_picks.addWidget(pick_label)

        self._chip_recent = QPushButton("Last 12 hours")
        self._chip_recent.setToolTip(
            "Ticks every fight from the last 12 hours and unticks the "
            "rest. Handy right after a play session."
        )
        theme.set_widget_class(self._chip_recent, "chip")
        self._chip_recent.clicked.connect(lambda: self._apply_chip("recent"))
        quick_picks.addWidget(self._chip_recent)

        self._chip_today = QPushButton("Today")
        self._chip_today.setToolTip(
            "Ticks every fight recorded since midnight and unticks the rest."
        )
        theme.set_widget_class(self._chip_today, "chip")
        self._chip_today.clicked.connect(lambda: self._apply_chip("today"))
        quick_picks.addWidget(self._chip_today)

        self._chip_all = QPushButton("All")
        self._chip_all.setToolTip(
            "Ticks every fight in the list, no matter how old."
        )
        theme.set_widget_class(self._chip_all, "chip")
        self._chip_all.clicked.connect(lambda: self._apply_chip("all"))
        quick_picks.addWidget(self._chip_all)

        self._chip_none = QPushButton("None")
        self._chip_none.setToolTip(
            "Unticks everything so you can start your pick from scratch."
        )
        theme.set_widget_class(self._chip_none, "chip")
        self._chip_none.clicked.connect(lambda: self._apply_chip("none"))
        quick_picks.addWidget(self._chip_none)
        quick_picks.addStretch()
        layout.addLayout(quick_picks)

        found_row = QHBoxLayout()
        self._found_label = QLabel("")
        self._found_label.setWordWrap(True)
        theme.mark_hint(self._found_label)
        found_row.addWidget(self._found_label, 1)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setToolTip("Read the log folder again without making a report.")
        self.refresh_btn.clicked.connect(self._manual_refresh)
        found_row.addWidget(self.refresh_btn)
        layout.addLayout(found_row)

        # Empty-state bridge: raw logs first go through Process Files, then
        # appear here as pickable fights. Do not leave a blank table with no
        # clue where its rows come from.
        self.empty_panel = QWidget()
        empty_layout = QHBoxLayout(self.empty_panel)
        empty_layout.setContentsMargins(12, 10, 12, 10)
        empty_copy = QLabel(
            "<b>No fights here yet.</b><br>"
            "Add your .evtc or .zevtc files first; SparkyBot will process "
            "them, then they will show up here."
        )
        empty_copy.setWordWrap(True)
        empty_layout.addWidget(empty_copy, 1)
        self.add_fights_btn = QPushButton("Add fight files...")
        self.add_fights_btn.setToolTip(
            "Takes you to the Process Files screen where you add your "
            ".evtc or .zevtc fight recordings. Once processed, they show "
            "up in this list."
        )
        theme.set_widget_class(self.add_fights_btn, "primary")
        self.add_fights_btn.clicked.connect(
            self.sig_process_files_requested.emit)
        empty_layout.addWidget(self.add_fights_btn)
        layout.addWidget(self.empty_panel)

        # 4. The table — checkbox is the only visual state
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["", "Date", "Fight", "Size"])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(_COL_CHECK,
                                    QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(_COL_CHECK, 24)
        header.setSectionResizeMode(_COL_DATE,
                                    QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_FIGHT,
                                    QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_SIZE,
                                    QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(20)
        self._table.setShowGrid(False)
        # Flat, quiet table: hover/selection painting suppressed by the
        # QTableWidget[quiet="true"] rules in the central QSS.
        self._table.setProperty("quiet", True)
        self._anchor_row = 0
        self._table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self._table, stretch=1)

        # 5. Name row
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Report name (optional):"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(
            "Leave blank for: Combined Fight Log Summary <date> (<n> fights)"
        )
        self.name_edit.setToolTip(
            "A friendly title shown at the top of the finished summary, "
            "for example \"Tuesday strike missions\". Leave it blank and "
            "SparkyBot names it after the date and number of fights."
        )
        name_row.addWidget(self.name_edit)
        layout.addLayout(name_row)

        # 6. Big button
        self.big_btn = QPushButton("Make fight summary")
        theme.set_widget_class(self.big_btn, "primary")
        self.big_btn.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Fixed)
        self.big_btn.clicked.connect(self._on_make_report)
        layout.addWidget(self.big_btn)

        # 7. Status + progress + details
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        theme.set_variant(self.status_label, "status")
        layout.addWidget(self.status_label)

        self._show_details_btn = QPushButton("Show details")
        theme.set_widget_class(self._show_details_btn, "error-outline")
        self._show_details_btn.setVisible(False)
        self._show_details_btn.clicked.connect(self._on_show_error_details)
        layout.addWidget(self._show_details_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # 8. Post-success row — plain buttons, themed by the central QSS
        post_row = QHBoxLayout()
        self.open_report_btn = QPushButton("Open the report")
        self.open_report_btn.setEnabled(False)
        self.open_report_btn.clicked.connect(self._on_open_report)
        post_row.addWidget(self.open_report_btn)

        self.post_discord_btn = QPushButton("Post to Discord")
        self.post_discord_btn.setToolTip(
            "Uploads the finished summary to your Discord channel using "
            "the webhook from Settings. Big files are shrunk first to fit "
            "Discord's free upload limit."
        )
        self.post_discord_btn.setEnabled(False)
        self.post_discord_btn.clicked.connect(self._on_publish)
        post_row.addWidget(self.post_discord_btn)
        post_row.addStretch()
        layout.addLayout(post_row)

    # ------------------------------------------------------------------
    # public helpers
    # ------------------------------------------------------------------

    def selected_logs(self) -> list[LogInfo]:
        logs = []
        for row in range(self._table.rowCount()):
            item = self._table.item(row, _COL_CHECK)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            log = item.data(_ROLE_LOG)
            if log is not None:
                logs.append(log)
        logs.sort(key=lambda log: log.timestamp)
        return logs

    def _set_row_checked(self, row: int, checked: bool):
        item = self._table.item(row, _COL_CHECK)
        if item is not None:
            item.setCheckState(Qt.CheckState.Checked if checked
                               else Qt.CheckState.Unchecked)

    def _row_checked(self, row: int) -> bool:
        item = self._table.item(row, _COL_CHECK)
        return (item is not None
                and item.checkState() == Qt.CheckState.Checked)

    @Slot(int, int)
    def _on_cell_clicked(self, row: int, _col: int):
        shift = bool(QApplication.keyboardModifiers()
                     & Qt.KeyboardModifier.ShiftModifier)
        if shift:
            # Tick the whole visual range from the last plain click to here.
            lo, hi = sorted((self._anchor_row, row))
            for r in range(lo, hi + 1):
                self._set_row_checked(r, True)
        else:
            self._set_row_checked(row, not self._row_checked(row))
            self._anchor_row = row
        self._update_found()

    # ------------------------------------------------------------------
    # grid population
    # ------------------------------------------------------------------

    @Slot()
    def _refresh(self, chip=None):
        if chip:
            self._initial_chip = chip
        self._request_discovery(force=True)

    @Slot()
    def _manual_refresh(self):
        self._request_discovery(force=True)

    @Slot()
    def rescan(self):
        """Page entry: immediately reuse a recent complete same-root list.

        Refresh always reads again. Auto entry/visible-page polling revalidates
        after 60 seconds, not a full traversal for every navigation click.
        """
        self._request_discovery()

    def showEvent(self, event):
        super().showEvent(event)
        self._freshness_timer.start()
        self.rescan()

    def hideEvent(self, event):
        self._freshness_timer.stop()
        super().hideEvent(event)

    def _sync_scope(self):
        scope = self._discovery_root() if self._discovery_root else None
        if scope == self._scope:
            return False
        self._scope = scope
        self._epoch += 1
        self._complete_at = None
        self._initial_chip = "recent"
        self._retry_timer.stop()
        self._deferred_discovery = None
        # Reset the new scope's UI before an in-flight scan/generation can
        # return early. No complete result exists for this root yet.
        self._list_state = "loading"
        self._list_error = ""
        self.empty_panel.hide()
        # Do not touch the table while report generation owns it. Its input
        # is already captured; clear obsolete rows when generation releases it.
        if not self._running:
            self._logs = []
            self._log_sizes = {}
            self._populate()
        self._update_found()
        return True

    def _request_discovery(self, force=False, reset_retry=True):
        changed = self._sync_scope()
        if self._running:
            self._after_generation = True
            self._trailing |= force or changed
            return
        if self._discovering:
            self._trailing |= force or changed
            self._entry_waiting |= not force
            return
        if reset_retry:
            self._retry_left = 1
        self._retry_timer.stop()
        if (not force and not changed and self._complete_at is not None
                and self._list_state not in ("failed", "partial")
                and time.monotonic() - self._complete_at < 60):
            self._update_found()
            return
        if changed:
            self._logs = []
            self._log_sizes = {}
            self._populate()
        self._list_state = "loading"
        self.empty_panel.hide()
        self._update_found()
        self._start_discover(preserve=None, chip=None)

    @Slot()
    def _retry_discovery(self):
        self._request_discovery(force=True, reset_retry=False)

    def _start_discover(self, preserve, chip):
        """One scoped worker; no source filesystem calls on the GUI thread."""
        if self._discovering:
            return
        self._discovering = True
        self._request_id += 1
        scope, epoch, request = self._scope, self._epoch, self._request_id
        discover, scoped = self._discover, self._discovery_root is not None
        emit = self.sig_logs_discovered.emit

        def _work():
            error = ""
            partial = False
            try:
                logs = list(discover(scope) if scoped else discover())
            except Exception as exc:
                logger.exception("Log discovery failed")
                logs = list(getattr(exc, "logs", ()))
                partial = bool(logs)
                error = str(exc) or type(exc).__name__
            sizes = {}
            for log in logs:
                try:
                    size = getattr(log, "size_bytes", None)
                    sizes[log.path] = (log.path.stat().st_size
                                       if size is None else size)
                except OSError as exc:
                    error = str(exc) or type(exc).__name__
                    partial = True
            result = dict(logs=logs, error=error, partial=partial,
                          scope=scope, epoch=epoch, request=request)
            try:
                emit(result, sizes, preserve, chip)
            except RuntimeError:
                pass  # widget destroyed while an uninterruptible share read finishes

        threading.Thread(target=_work, daemon=True,
                         name="fight-summary-discovery").start()

    @Slot(object, object, object, object)
    def _on_logs_discovered(self, result, sizes, preserve, chip):
        """Explicit queued GUI receiver; failed/partial scans never erase rows."""
        self._discovering = False
        self._sync_scope()
        if self._running:
            self._deferred_discovery = (result, sizes, preserve, chip)
            return
        obsolete = (result["scope"] != self._scope
                    or result["epoch"] != self._epoch)
        failed = bool(result["error"])
        if not obsolete:
            if failed:
                self._list_state = "partial" if result["partial"] else "failed"
                self._list_error = result["error"]
            else:
                checked = {log.path for log in self.selected_logs()}
                self._logs = result["logs"]
                self._log_sizes = sizes
                self._populate()
                if self._initial_chip:
                    self._apply_chip(self._initial_chip)
                    self._initial_chip = None
                else:
                    self._check_rows_for([log for log in self._logs
                                          if log.path in checked])
                self._complete_at = time.monotonic()
                self._list_state = "complete"
                self._list_error = ""
            self.empty_panel.setVisible(
                self._list_state == "complete" and not self._logs)
            self._update_found()
        trailing = self._trailing or obsolete
        entry_retry = failed and self._entry_waiting and self._retry_left > 0
        self._trailing = False
        self._entry_waiting = False
        if trailing or entry_retry:
            self._retry_left = 0  # one trailing attempt per coalesced burst
            self._request_discovery(force=True, reset_retry=False)
        elif failed and self._retry_left > 0:
            self._retry_left -= 1
            self._retry_timer.start()

    @Slot()
    def _populate(self):
        self._rows_scope = self._scope
        self.empty_panel.setVisible(
            self._list_state == "complete" and not self._logs)
        header = self._table.horizontalHeader()
        sort_col, sort_order = header.sortIndicatorSection(), header.sortIndicatorOrder()
        if self._complete_at is None:
            sort_col, sort_order = _COL_DATE, Qt.SortOrder.DescendingOrder
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        for log in self._logs:
            row = self._table.rowCount()
            self._table.insertRow(row)
            ts = log.timestamp

            # Display-only checkbox: clicks are handled by _on_cell_clicked
            # so Qt never double-toggles it.
            check_item = _SortableItem("")
            check_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            check_item.setCheckState(Qt.CheckState.Unchecked)
            check_item.setData(_ROLE_LOG, log)
            check_item.setData(_ROLE_SORT, ts.timestamp())
            self._table.setItem(row, _COL_CHECK, check_item)

            # Date column — Explorer-style "7/18/2026 7:18 PM"; sorts by
            # the real timestamp, never the text. Dash-modified strftime
            # flags are Linux-only and crash Windows; build it portably.
            hour12 = ts.hour % 12 or 12
            ampm = "AM" if ts.hour < 12 else "PM"
            date_str = (f"{ts.month}/{ts.day}/{ts.year} "
                        f"{hour12}:{ts.minute:02d} {ampm}")
            date_item = _SortableItem(date_str)
            date_item.setData(_ROLE_SORT, ts.timestamp())
            self._table.setItem(row, _COL_DATE, date_item)

            fight_item = QTableWidgetItem(log.path.stem)
            self._table.setItem(row, _COL_FIGHT, fight_item)

            # Sizes were gathered on the discover thread — no per-row
            # network stat on the GUI thread.
            size_bytes = self._log_sizes.get(log.path, 0)
            size_item = _SortableItem(
                f"{size_bytes / 1024 / 1024:.1f} MB")
            size_item.setData(_ROLE_SORT, size_bytes)
            self._table.setItem(row, _COL_SIZE, size_item)
        self._table.setSortingEnabled(True)
        # Newest-first initially; never reset a user's active sort on Refresh.
        self._table.sortItems(sort_col, sort_order)

    # ------------------------------------------------------------------
    # chips
    # ------------------------------------------------------------------

    def _apply_chip(self, mode: str):
        if self._complete_at is None:
            self._initial_chip = mode
        if mode == "all":
            for row in range(self._table.rowCount()):
                self._set_row_checked(row, True)
        elif mode == "none":
            for row in range(self._table.rowCount()):
                self._set_row_checked(row, False)
        elif mode == "recent":
            if self._select_recent is not None:
                self._check_rows_for(self._select_recent(self._logs))
            else:
                self._check_rows_for(self._select_today(self._logs))
        elif mode == "today":
            self._check_rows_for(self._select_today(self._logs))
        self._update_found()

    def _check_rows_for(self, wanted: list[LogInfo]):
        wanted_paths = {log.path for log in wanted}
        for row in range(self._table.rowCount()):
            item = self._table.item(row, _COL_CHECK)
            log = item.data(_ROLE_LOG) if item else None
            self._set_row_checked(
                row, log is not None and log.path in wanted_paths)

    @Slot()
    def _update_found(self):
        n = len(self._logs)
        s = len(self.selected_logs())
        counts = f"{n} fight{'s' if n != 1 else ''} found · {s} selected"
        retained = (f"Showing last complete list · {counts}"
                    if self._complete_at is not None else "Refresh to retry.")
        if self._list_state == "loading":
            text = "Loading fights…"
            if self._complete_at is not None:
                text += f" · {retained}"
        elif self._list_state in ("failed", "partial"):
            prefix = "Scan incomplete" if self._list_state == "partial" else "Refresh failed"
            text = f"{prefix} · {retained}"
        elif n:
            age = max(0, int(time.monotonic() - self._complete_at)) if self._complete_at else 0
            text = f"{counts} · Last checked {age}s ago"
        else:
            text = "No fights found."
        self._found_label.setText(text)
        self._found_label.setToolTip(self._list_error)

    # ------------------------------------------------------------------
    # big button
    # ------------------------------------------------------------------

    @Slot()
    def _on_make_report(self):
        selected = self.selected_logs()
        if not selected:
            self._set_status_normal(
                "No fights selected. Click fights in the list, or use "
                "the quick picks above."
            )
            return

        name = self.name_edit.text().strip()
        if not name:
            dt = selected[-1].timestamp.strftime("%Y-%m-%d")
            name = f"Combined Fight Log Summary {dt} ({len(selected)} fights)"

        self._start_generate(selected, name)

    def _start_generate(self, selected: list[LogInfo], name: str):
        self._set_running(True)
        self._show_details_btn.setVisible(False)
        self.status_label.setText("Getting things ready…")
        theme.set_state(self.status_label, "busy")

        def _run():
            try:
                runner = self._runner_factory()
                result = runner.generate(
                    selected, name,
                    progress=lambda stage, cur, tot, detail: (
                        self.sig_progress.emit(stage, cur, tot, detail)
                    ),
                )
                self.sig_done.emit(result)
            except Exception as exc:
                logger.exception("Report generation failed")
                self.sig_error.emit(str(exc))

        threading.Thread(target=_run, daemon=True).start()

    # ------------------------------------------------------------------
    # signal slots
    # ------------------------------------------------------------------

    @Slot(str, int, int, str)
    def _on_progress(self, stage: str, current: int, total: int,
                     detail: str):
        percent, msg = progress_state(stage, current, total)
        self.progress_bar.setValue(percent)
        if msg:
            self.status_label.setText(msg)

    @Slot(object)
    def _on_done(self, result: ReportResult):
        self.last_result = result
        self._set_running(False)
        failed = getattr(result, "failed_count", 0)
        if failed:
            theme.set_state(self.status_label, "warn")
            self.status_label.setText(
                f"Done! {result.name} is ready — but {failed} "
                f"fight{'s' if failed != 1 else ''} couldn't be read "
                f"and {'were' if failed != 1 else 'was'} left out."
            )
            self._error_details = (
                "These fights couldn't be read and are not in the report:\n\n"
                + "\n".join(getattr(result, "failed_names", ())))
            self._show_details_btn.setVisible(True)
        else:
            theme.set_state(self.status_label, "ok")
            self.status_label.setText(
                f"Done! {result.name} is ready."
            )
        self.open_report_btn.setEnabled(True)
        self.post_discord_btn.setEnabled(True)

    @Slot(str)
    def _on_error(self, message: str):
        self._set_running(False)
        self._error_details = message
        theme.set_state(self.status_label, "error")
        line = message.split("\n")[0][:120]
        self.status_label.setText(line)
        self._show_details_btn.setVisible(True)

    @Slot()
    def _on_show_error_details(self):
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Error details")
        dlg.setText(self._error_details)
        dlg.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        dlg.exec()
        dlg.deleteLater()

    # ------------------------------------------------------------------
    # publish
    # ------------------------------------------------------------------

    @Slot()
    def _on_publish(self):
        result = self.last_result
        if result is None:
            return

        self.post_discord_btn.setEnabled(False)
        self.open_report_btn.setEnabled(False)
        self._show_details_btn.setVisible(False)
        theme.set_state(self.status_label, "busy")
        self.status_label.setText("Sending to Discord…")

        def _run():
            try:
                self._publish_fn(result)
                self.sig_publish_done.emit(True, "Posted to Discord")
            except Exception as exc:
                logger.exception("Report publish failed")
                self.sig_publish_done.emit(
                    False,
                    f"Something went wrong sending to Discord: {exc}")

        threading.Thread(target=_run, daemon=True).start()

    @Slot(bool, str)
    def _on_publish_done(self, success: bool, message: str):
        self.post_discord_btn.setEnabled(True)
        self.open_report_btn.setEnabled(True)
        theme.set_state(self.status_label, "ok" if success else "error")
        self.status_label.setText(message)

    # ------------------------------------------------------------------
    # open report
    # ------------------------------------------------------------------

    @Slot()
    def _on_open_report(self):
        if self.last_result is not None:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.last_result.html_path))
            )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _set_status_normal(self, text: str):
        theme.set_state(self.status_label, None)
        self.status_label.setText(text)

    def _set_running(self, running: bool):
        self._running = running
        self.big_btn.setEnabled(not running)
        self._chip_recent.setEnabled(not running)
        self._chip_today.setEnabled(not running)
        self._chip_all.setEnabled(not running)
        self._chip_none.setEnabled(not running)
        self._table.setEnabled(not running)
        self.refresh_btn.setEnabled(not running)
        self.open_report_btn.setEnabled(
            not running and self.last_result is not None)
        self.post_discord_btn.setEnabled(
            not running and self.last_result is not None)
        self.progress_bar.setVisible(running)
        if not running:
            self.progress_bar.setValue(0)
            self._after_generation |= self._sync_scope()
            if self._rows_scope != self._scope:
                self._logs = []
                self._log_sizes = {}
                self._populate()
            if self._deferred_discovery is not None:
                args = self._deferred_discovery
                self._deferred_discovery = None
                self._on_logs_discovered(*args)
            if self._after_generation:
                self._after_generation = False
                force = self._trailing
                self._trailing = False
                self._request_discovery(force=force)
