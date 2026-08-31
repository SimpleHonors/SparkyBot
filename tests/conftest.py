"""Shared pytest lifetime for the Qt application object.

PySide can tear down QApplication when a module-scoped fixture drops its last
Python reference.  On Windows, rapidly destroying/recreating it across the GUI
modules made a quiet full-suite run exit 255 after every test had passed.  Keep
one application alive for the whole session and flush deferred deletes once.
"""

from __future__ import annotations

import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_SESSION_QT_APP = None


@pytest.fixture(scope="session", autouse=True)
def _keep_qt_application_alive():
    global _SESSION_QT_APP
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:
        yield
        return

    _SESSION_QT_APP = QApplication.instance() or QApplication([])
    yield

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _SESSION_QT_APP.processEvents()
