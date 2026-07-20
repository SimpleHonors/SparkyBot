"""Tests for core/raid_report_tab.py — flat sortable checkbox table."""

import re
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

pytest.importorskip("PySide6", reason="PySide6 not installed in this environment")

from PySide6.QtWidgets import QApplication, QLabel, QWidget
from PySide6.QtCore import Qt

from core.raid_session import LogInfo
from core.raid_report import ReportResult
from core.raid_report_tab import (
    RaidReportTab, _ROLE_LOG, _ROLE_SORT,
    _COL_CHECK, _COL_DATE, _COL_FIGHT, _COL_SIZE,
)


# ---------------------------------------------------------------------------
# QApplication fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _log(year, month, day, hour, minute, fname=None):
    ts = datetime(year, month, day, hour, minute)
    if fname:
        path = Path(f"/fake/{fname}")
    else:
        path = Path(f"/fake/{ts.strftime('%Y%m%d-%H%M%S')}.zevtc")
    return LogInfo(path=path, timestamp=ts, source="filename")


def _result(name, fight_count, failed=0, failed_names=()):
    return ReportResult(
        name=name, fight_count=fight_count,
        html_path=Path("/fake/out.html"),
        json_path=Path("/fake/out.json"),
        span="22:10 \u2013 00:12",
        failed_count=failed, failed_names=tuple(failed_names),
    )


def _fake_runner(result=None, side_effect=None):
    class _Runner:
        def generate(self, selected, name, progress=None):
            if side_effect:
                raise side_effect
            if progress:
                progress("plan", 1, 3, "")
                progress("parse", 1, 2, "")
                progress("parse", 2, 2, "")
                progress("combine", 1, 1, "")
                progress("bake", 1, 1, "")
            return result or _result(name, len(selected))
    return _Runner()


class _InlineThread:
    def __init__(self, target=None, daemon=None, args=(), kwargs=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


# ---------------------------------------------------------------------------
# dummy fakes
# ---------------------------------------------------------------------------

def _fake_discover():
    return [
        _log(2026, 7, 18, 22, 10, "first.zevtc"),
        _log(2026, 7, 18, 23, 40, "second.zevtc"),
        _log(2026, 7, 19,  0, 12, "third.zevtc"),
    ]


def _fake_select_session(logs):
    return [log for log in logs if log.timestamp.hour >= 22]


def _fake_select_recent(logs):
    return [log for log in logs if log.timestamp.hour >= 22]


def _fake_select_today(logs):
    return logs


def _fake_publish(result):
    return None


# ---------------------------------------------------------------------------
# grid + chips
# ---------------------------------------------------------------------------

def test_columns_and_date_format(qapp):
    """Sortable Date/Fight/Size columns; Explorer-style numeric date."""
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    assert tab._table.columnCount() == 4
    headers = [tab._table.horizontalHeaderItem(i).text() for i in range(4)]
    assert headers == ["", "Date", "Fight", "Size"]
    assert tab._table.isSortingEnabled()
    # Newest first: row 0 is "third" from 2026-07-19 00:12
    assert tab._table.item(0, _COL_DATE).text() == "7/19/2026 12:12 AM"
    assert tab._table.item(0, _COL_FIGHT).text() == "third"
    assert "MB" in tab._table.item(0, _COL_SIZE).text()


def test_date_sorts_by_timestamp_not_text(qapp):
    """Sorting must use the real timestamp, not the display string."""
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    tab._table.sortItems(_COL_DATE, Qt.SortOrder.AscendingOrder)
    stamps = [tab._table.item(r, _COL_DATE).data(_ROLE_SORT)
              for r in range(tab._table.rowCount())]
    assert stamps == sorted(stamps)


def test_grid_populated_on_construction(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    assert tab._table.rowCount() == 3


def test_chip_recent_pre_checks(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    checked = tab.selected_logs()
    assert len(checked) == 2


def test_chip_today_checks_all(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    tab._apply_chip("today")
    assert len(tab.selected_logs()) == 3


def test_chip_all_checks_all(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    tab._apply_chip("none")
    tab._apply_chip("all")
    assert len(tab.selected_logs()) == 3


def test_chip_none_unchecks_all(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    tab._apply_chip("none")
    assert tab.selected_logs() == []


def test_chip_labels(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    assert tab._chip_recent.text() == "Last 12 hours"
    assert tab._chip_today.text() == "Today"
    assert tab._chip_all.text() == "All"
    assert tab._chip_none.text() == "None"


def test_found_count_label(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    text = tab._found_label.text()
    assert "3 fights found" in text
    assert "2 selected" in text


def test_found_count_empty(qapp):
    tab = RaidReportTab(
        discover=lambda: [],
        select_session=_fake_select_session,
        select_recent=lambda logs: [],
        select_today=lambda logs: [],
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    assert tab._found_label.text() == "No fights found."
    assert not tab.empty_panel.isHidden()
    assert tab.add_fights_btn.text() == "Add fight files..."


def test_empty_action_requests_process_files(qapp):
    tab = RaidReportTab(
        discover=lambda: [],
        select_session=_fake_select_session,
        select_recent=lambda logs: [],
        select_today=lambda logs: [],
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    requested = []
    tab.sig_process_files_requested.connect(lambda: requested.append(True))
    tab.add_fights_btn.click()
    assert requested == [True]


def test_no_selection_highlight_layer(qapp):
    """Checkboxes are the only state — no separate selection highlight."""
    from PySide6.QtWidgets import QAbstractItemView
    from core import theme
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    assert (tab._table.selectionMode()
            == QAbstractItemView.SelectionMode.NoSelection)
    # Quiet-table styling now lives in the central QSS, keyed off the
    # "quiet" dynamic property instead of a per-widget stylesheet.
    assert tab._table.property("quiet") is True
    qss = theme.load_stylesheet()
    assert 'QTableWidget[quiet="true"]::item:hover' in qss
    assert "transparent" in qss


def test_status_states_use_dynamic_property(qapp):
    """Status colors come from the theme QSS via the `state` property, not
    per-call setStyleSheet swaps (the old hex state machine)."""
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    tab._on_error("boom")
    assert tab.status_label.property("state") == "error"
    tab._on_publish_done(True, "Posted to Discord")
    assert tab.status_label.property("state") == "ok"
    tab._on_publish_done(False, "failed")
    assert tab.status_label.property("state") == "error"
    tab._set_status_normal("hello")
    assert tab.status_label.property("state") is None
    # The widget itself must never carry an inline stylesheet.
    assert tab.status_label.styleSheet() == ""


def test_click_ticks_row_and_toggles_once(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    tab._apply_chip("none")
    assert tab.selected_logs() == []
    item = tab._table.item(0, _COL_CHECK)
    # Display-only checkbox: Qt must not flip it natively (double-toggle bug)
    assert not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable)

    tab._table.cellClicked.emit(0, _COL_FIGHT)
    assert len(tab.selected_logs()) == 1
    assert "1 selected" in tab._found_label.text()

    tab._table.cellClicked.emit(0, _COL_CHECK)
    assert tab.selected_logs() == []


def test_shift_click_ticks_range(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    tab._apply_chip("none")
    tab._table.cellClicked.emit(0, _COL_FIGHT)  # anchor at row 0
    with patch("core.raid_report_tab.QApplication.keyboardModifiers",
               return_value=Qt.KeyboardModifier.ShiftModifier):
        tab._table.cellClicked.emit(2, _COL_FIGHT)
    assert len(tab.selected_logs()) == 3


def test_sorted_newest_first(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    stamps = [tab._table.item(r, _COL_DATE).data(_ROLE_SORT)
              for r in range(tab._table.rowCount())]
    assert stamps == sorted(stamps, reverse=True)


def test_selected_logs_chronological(qapp):
    """Grid shows newest first, but the runner gets logs oldest-first."""
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    tab._apply_chip("all")
    stamps = [log.timestamp for log in tab.selected_logs()]
    assert stamps == sorted(stamps)


# ---------------------------------------------------------------------------
# big button
# ---------------------------------------------------------------------------

def test_big_button_label(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    assert tab.big_btn.text() == "Make raid report"


def test_big_button_flow(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(
            result=_result("Test Report", 2)
        ),
        publish=_fake_publish,
    )
    with patch("core.raid_report_tab.threading.Thread", new=_InlineThread):
        tab.big_btn.click()

    assert "Done! Test Report is ready." in tab.status_label.text()
    assert tab.open_report_btn.isEnabled()
    assert tab.post_discord_btn.isEnabled()


def test_big_button_no_selection(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    tab._apply_chip("none")
    tab.big_btn.click()
    assert "No fights selected" in tab.status_label.text()


def test_big_button_error(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(
            side_effect=RuntimeError("boom")
        ),
        publish=_fake_publish,
    )
    with patch("core.raid_report_tab.threading.Thread", new=_InlineThread):
        tab.big_btn.click()

    assert "boom" in tab.status_label.text()
    assert not tab._show_details_btn.isHidden()


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------

def test_publish_success(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(
            result=_result("R", 2)
        ),
        publish=_fake_publish,
    )
    with patch("core.raid_report_tab.threading.Thread", new=_InlineThread):
        tab.big_btn.click()
    with patch("core.raid_report_tab.threading.Thread", new=_InlineThread):
        tab.post_discord_btn.click()

    assert "Posted to Discord" in tab.status_label.text()


def test_publish_failure(qapp):
    def _failing(result):
        raise RuntimeError("webhook down")

    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(
            result=_result("R", 2)
        ),
        publish=_failing,
    )
    with patch("core.raid_report_tab.threading.Thread", new=_InlineThread):
        tab.big_btn.click()
    with patch("core.raid_report_tab.threading.Thread", new=_InlineThread):
        tab.post_discord_btn.click()

    assert "webhook down" in tab.status_label.text()


# ---------------------------------------------------------------------------
# button labels
# ---------------------------------------------------------------------------

def test_post_success_button_labels(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    assert tab.open_report_btn.text() == "Open the report"
    assert tab.post_discord_btn.text() == "Post to Discord"


# ---------------------------------------------------------------------------
# banned jargon
# ---------------------------------------------------------------------------

_BANNED = re.compile(
    r'\b(viewer|html\b|combiner|parsing|parsed|cache|log\s*folder|'
    r'\bei\b|json|tiddler|night|tonight)\b',
    re.IGNORECASE,
)


def _visible_strings(widget):
    found = []
    if isinstance(widget, QLabel):
        text = widget.text()
        if text:
            found.append(text)
    if hasattr(widget, 'placeholderText'):
        text = widget.placeholderText()
        if text:
            found.append(text)
    if hasattr(widget, 'toolTip'):
        text = widget.toolTip()
        if text:
            found.append(text)
    for child in widget.findChildren(QWidget):
        found.extend(_visible_strings(child))
    return found


def test_no_banned_jargon(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    for text in _visible_strings(tab):
        m = _BANNED.search(text)
        assert m is None, f"banned word '{m.group()}' in: {text}"


def test_no_platform_specific_strftime_flags():
    """%-X and %#X strftime flags crash on Windows; ban them repo-wide."""
    import pathlib, re
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for p in list((root / "core").glob("*.py")) + [root / "main.py"]:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"%[-#][a-zA-Z]", line) and "strftime-ban" not in line:
                offenders.append(f"{p.name}:{i}")
    assert not offenders, f"platform-specific strftime flags: {offenders}"


def test_progress_never_hits_100_midway(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    tab._on_progress("combine", 1, 1, "")
    assert tab.progress_bar.value() < 100
    tab._on_progress("bake", 1, 1, "")
    assert tab.progress_bar.value() < 100


def test_no_first_time_only_claim(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    with patch("core.raid_report_tab.threading.Thread", new=_InlineThread):
        tab.big_btn.click()
    import core.raid_report_tab as mod
    import inspect
    assert "first time only" not in inspect.getsource(mod)


def test_failed_fights_warning_nonblocking(qapp):
    """Report completes but the user is told when fights were left out."""
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(
            result=_result("R", 2, failed=3,
                           failed_names=("a", "b", "c"))
        ),
        publish=_fake_publish,
    )
    with patch("core.raid_report_tab.threading.Thread", new=_InlineThread):
        tab.big_btn.click()
    text = tab.status_label.text()
    assert "R is ready" in text
    assert "3 fights couldn't be read" in text
    assert tab.open_report_btn.isEnabled()          # not blocked
    assert not tab._show_details_btn.isHidden()     # details available


def test_rescan_preserves_ticks_and_finds_new_logs(qapp):
    """Page-open contract: the fight list rescans the folder on every open,
    keeping the user's ticked fights while new fights arrive unticked."""
    state = {"logs": [
        _log(2026, 7, 18, 22, 10, "first.zevtc"),
        _log(2026, 7, 18, 23, 40, "second.zevtc"),
    ]}
    tab = RaidReportTab(
        discover=lambda: list(state["logs"]),
        select_session=_fake_select_session,
        select_recent=lambda logs: [],
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    for row in range(tab._table.rowCount()):
        if tab._table.item(row, _COL_FIGHT).text() == "first":
            tab._set_row_checked(row, True)
    assert [log.path.name for log in tab.selected_logs()] == ["first.zevtc"]

    state["logs"].append(_log(2026, 7, 19, 0, 12, "third.zevtc"))
    tab.rescan()
    assert tab._table.rowCount() == 3
    # The tick survived the rescan; the new fight is present but unticked
    assert [log.path.name for log in tab.selected_logs()] == ["first.zevtc"]
    assert "3 fights found" in tab._found_label.text()


def test_rescan_noop_while_running(qapp):
    tab = RaidReportTab(
        discover=_fake_discover,
        select_session=_fake_select_session,
        select_recent=_fake_select_recent,
        select_today=_fake_select_today,
        runner_factory=lambda: _fake_runner(),
        publish=_fake_publish,
    )
    tab._running = True
    before = tab._table.rowCount()
    tab.rescan()
    assert tab._table.rowCount() == before
