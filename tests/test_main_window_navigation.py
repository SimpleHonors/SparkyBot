"""Operator-facing sidebar navigation regressions."""

from __future__ import annotations

import gc

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.config import Config
from core.main_window import MainWindow, PAGE_HOME, PAGE_RAID_REPORT


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def _click_nav(window: MainWindow, key: str) -> None:
    row = window._nav_row_of(key)
    assert row >= 0
    item = window.sidebar.item(row)
    point = window.sidebar.visualItemRect(item).center()
    QTest.mouseClick(
        window.sidebar.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        point,
    )


def test_real_sidebar_click_switches_from_home_to_fight_summary(
    qt_app, tmp_path
):
    """A real click must switch both the highlight and visible page."""
    window = MainWindow(Config(tmp_path / "config.properties"))
    try:
        window.show()
        qt_app.processEvents()
        assert window.current_page == PAGE_HOME
        assert window.stack.currentWidget() is window._containers[PAGE_HOME]

        _click_nav(window, PAGE_RAID_REPORT)
        qt_app.processEvents()

        assert window.current_page == PAGE_RAID_REPORT
        assert window.stack.currentWidget() is window._containers[PAGE_RAID_REPORT]
        assert window._raid_report_page is not None
        assert window._raid_report_page.isVisible()
    finally:
        window.hide()
        del window
        gc.collect()
        qt_app.processEvents()
