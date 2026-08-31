"""Packaged-default-width layout regressions for Fight Summary."""

from __future__ import annotations

import gc
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QApplication

from core.raid_report_tab import RaidReportTab


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def test_found_count_is_not_clipped_at_packaged_default_width(qt_app):
    tab = RaidReportTab(
        discover=lambda: [],
        select_session=lambda logs: logs,
        select_today=lambda logs: logs,
        select_recent=lambda logs: logs,
        runner_factory=lambda: None,
        publish=lambda result: None,
    )
    tab.resize(412, 500)
    tab.show()
    qt_app.processEvents()
    tab._update_found()
    qt_app.processEvents()

    needed = tab._found_label.fontMetrics().horizontalAdvance(
        tab._found_label.text()
    )
    assert tab._found_label.width() >= needed, (
        f"found-count label is clipped: {tab._found_label.width()}px available, "
        f"{needed}px needed"
    )
    tab.hide()
    del tab
    gc.collect()
    qt_app.processEvents()
