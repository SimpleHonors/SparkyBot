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
        "Found PlenBot Log Uploader — set me up from it"
    )
    assert "found PlenBot Log Uploader" in welcome.competitor_import_status.text()
    assert not welcome.competitor_offer.isHidden()
    assert welcome.import_button.text() == "I Also Have a Guild File"
    assert "Start with PlenBot Log Uploader above" in welcome.guild_file_intro.text()
    assert welcome._detected_findings == (finding,)


def test_multiple_detected_tools_offer_to_combine_not_pick_one(
    app, tmp_path, monkeypatch
):
    findings = [
        SimpleNamespace(app="MzFightReporter"),
        SimpleNamespace(app="PlenBot Log Uploader"),
    ]
    wizard, welcome = make_wizard(tmp_path, monkeypatch, findings)

    welcome.initializePage()

    # No tool is buried behind "and N more" — the offer combines them all.
    assert welcome.competitor_import_button.text() == "Set me up from my log tools"
    status = welcome.competitor_import_status.text()
    assert "and 1 more" not in status
    assert "MzFightReporter" in status and "PlenBot Log Uploader" in status
    assert "combine" in status


def test_multiple_detected_merges_via_picker_then_shared_consent(
    app, tmp_path, monkeypatch
):
    findings = [
        SimpleNamespace(app="MzFightReporter"),
        SimpleNamespace(app="PlenBot Log Uploader"),
    ]
    wizard, welcome = make_wizard(tmp_path, monkeypatch, findings)
    welcome.initializePage()

    merged = SimpleNamespace(app="MzFightReporter + PlenBot Log Uploader")
    plan = SimpleNamespace(finding=merged)
    merge_calls = []
    previewed = []
    applied = []

    monkeypatch.setattr(
        sw, "merge_competitor_findings",
        lambda f, *, primary_index: merge_calls.append(primary_index) or merged,
    )
    monkeypatch.setattr(
        sw, "preview_competitor_finding",
        lambda item, parent: previewed.append(item) or plan,
    )
    # Pick the SECOND tool as primary — must map to index 1, not 0.
    monkeypatch.setattr(
        sw.QInputDialog, "getItem",
        staticmethod(lambda *a, **k: ("PlenBot Log Uploader", True)),
    )
    monkeypatch.setattr(wizard, "use_competitor_import", applied.append)

    welcome.competitor_import_button.click()

    assert merge_calls == [1]          # chosen primary index, not defaulted to 0
    assert previewed == [merged]       # merged finding went through shared consent
    assert applied == [plan]           # single atomic apply
    assert welcome.competitor_import_button.isHidden()
    assert "combined settings from 2 tools" in welcome.competitor_import_status.text()


def test_no_detected_tools_show_no_neighbor_feature_at_all(
    app, tmp_path, monkeypatch
):
    wizard, welcome = make_wizard(tmp_path, monkeypatch, [])

    welcome.initializePage()

    assert welcome.competitor_offer.isHidden()
    assert welcome.competitor_import_button.text() == ""
    assert welcome.competitor_import_status.text() == ""
    # Default screen leads with the walk-through; the guild file offer is a
    # quiet flat button at the bottom, since most people never receive one.
    assert not welcome.easy_path_intro.isHidden()
    assert "Click Next to begin" in welcome.easy_path_intro.text()
    assert welcome.import_button.text() == "Use a Guild Setup File..."
    assert welcome.import_button.isFlat()
    assert welcome.guild_file_help.isHidden()
    assert welcome.manual_help.isHidden()
    assert welcome.advanced_options.isHidden()
    assert welcome.advanced_toggle.text() == "Advanced"


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
    assert welcome.competitor_offer.isHidden()
    assert welcome.competitor_import_button.text() == ""


def test_primary_offer_always_previews_named_tool_and_then_gently_asks_for_guild(
    app, tmp_path, monkeypatch
):
    # Single detected tool → direct preview, no picker. (The multi-tool
    # combine+picker flow is covered by its own test.)
    findings = [
        SimpleNamespace(app="MzFightReporter"),
    ]
    wizard, welcome = make_wizard(tmp_path, monkeypatch, findings)
    welcome.initializePage()
    previewed = []
    applied = []
    next_calls = []
    plan = SimpleNamespace(finding=findings[0])

    def preview(item, parent):
        previewed.append((item, parent))
        return plan

    monkeypatch.setattr(sw, "preview_competitor_finding", preview)
    monkeypatch.setattr(
        sw,
        "choose_manual_competitor_import",
        lambda *_args, **_kwargs: pytest.fail("primary offer opened the picker"),
    )
    monkeypatch.setattr(wizard, "use_competitor_import", applied.append)
    monkeypatch.setattr(wizard, "next", lambda: next_calls.append(True))

    welcome.competitor_import_button.click()

    assert previewed == [(findings[0], welcome)]
    assert applied == [plan]
    assert next_calls == []
    assert welcome.competitor_import_button.isHidden()
    assert welcome.import_button.text() == "Add My Guild's File"
    assert "your base setup is ready" in welcome.guild_file_intro.text()
    assert "will stay" in welcome.guild_file_help.text()


def test_advanced_manual_path_stays_buried_until_user_opens_it(
    app, tmp_path, monkeypatch
):
    wizard, welcome = make_wizard(tmp_path, monkeypatch, [])
    welcome.initializePage()
    calls = []
    monkeypatch.setattr(
        sw,
        "choose_manual_competitor_import",
        lambda parent, **kwargs: calls.append((parent, kwargs)) or None,
    )

    assert welcome.advanced_options.isHidden()
    welcome.advanced_toggle.click()
    assert not welcome.advanced_options.isHidden()
    assert "did not find" in welcome.advanced_options.findChild(
        sw.QLabel
    ).text()

    welcome.advanced_competitor_button.click()
    assert calls and calls[0][0] is welcome
    assert welcome.competitor_offer.isHidden()
