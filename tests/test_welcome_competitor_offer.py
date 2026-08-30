"""EZ pleb mode: the wizard detects installed log tools itself and leads
with a named one-click offer — the user never hunts for the import."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

import core.setup_wizard as sw
from core.config import Config


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def make_wizard(tmp_path, monkeypatch, findings):
    monkeypatch.setattr(
        sw, "discover_competitor_configs", lambda **kwargs: tuple(findings)
    )
    wizard = sw.SetupWizard(Config(tmp_path / "config.properties"))
    return wizard, wizard.page(sw.PAGE_WELCOME)


def test_single_detected_tool_becomes_named_one_click_offer(
    app, tmp_path, monkeypatch
):
    finding = SimpleNamespace(app="PlenBot Log Uploader")
    wizard, welcome = make_wizard(tmp_path, monkeypatch, [finding])

    welcome.initializePage()

    assert welcome.competitor_import_button.text() == (
        "Use PlenBot Log Uploader's Settings"
    )
    assert "Found PlenBot Log Uploader" in welcome.competitor_import_status.text()
    assert "hunt" in welcome.competitor_import_status.text()
    assert welcome._detected_findings == (finding,)


def test_multiple_detected_tools_named_with_count(app, tmp_path, monkeypatch):
    findings = [
        SimpleNamespace(app="MzFightReporter"),
        SimpleNamespace(app="PlenBot Log Uploader"),
    ]
    wizard, welcome = make_wizard(tmp_path, monkeypatch, findings)

    welcome.initializePage()

    assert welcome.competitor_import_button.text() == (
        "Use MzFightReporter's Settings"
    )
    assert "(and 1 more)" in welcome.competitor_import_status.text()


def test_no_detected_tools_keeps_generic_button(app, tmp_path, monkeypatch):
    wizard, welcome = make_wizard(tmp_path, monkeypatch, [])

    welcome.initializePage()

    assert welcome.competitor_import_button.text() == (
        "Import from Another Log Tool..."
    )
    assert welcome.competitor_import_status.text() == ""


def test_scan_runs_once_and_discovery_errors_stay_quiet(
    app, tmp_path, monkeypatch
):
    calls = []

    def exploding(**kwargs):
        calls.append(1)
        raise OSError("drive fell off")

    monkeypatch.setattr(sw, "discover_competitor_configs", exploding)
    wizard = sw.SetupWizard(Config(tmp_path / "config.properties"))
    welcome = wizard.page(sw.PAGE_WELCOME)

    welcome.initializePage()
    welcome.initializePage()

    assert calls == [1]  # once, and the failure never reached the user
    assert welcome.competitor_import_button.text() == (
        "Import from Another Log Tool..."
    )
