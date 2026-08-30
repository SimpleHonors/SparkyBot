"""Ask-first autodetect: the wizard probes nothing until the user presses
the lead "Set me up automatically" button — the CLICK is the permission —
and then the named one-click offer (consent preview, apply) continues
exactly as before."""

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


def test_lead_button_leads_and_nothing_is_probed_before_the_click(
    app, tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        sw,
        "discover_competitor_configs",
        lambda **kwargs: calls.append(kwargs) or (),
    )
    wizard = sw.SetupWizard(Config(tmp_path / "config.properties"))
    welcome = wizard.page(sw.PAGE_WELCOME)

    # Page entry (initializePage) must stay silent — no unprompted hunt.
    welcome.initializePage()

    assert calls == []
    assert welcome.competitor_offer.isHidden()
    assert not welcome.autodetect_button.isHidden()
    assert welcome.autodetect_button.text() == "Set me up automatically"
    assert "fight-log tools" in welcome.autodetect_subtext.text()
    assert "or click Next to set up by hand" in welcome.autodetect_manual_hint.text()
    assert welcome.autodetect_none_hint.isHidden()


def test_clicking_the_lead_button_is_what_starts_the_scan(
    app, tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        sw,
        "discover_competitor_configs",
        lambda **kwargs: calls.append(kwargs) or (),
    )
    wizard = sw.SetupWizard(Config(tmp_path / "config.properties"))
    welcome = wizard.page(sw.PAGE_WELCOME)

    welcome.autodetect_button.click()

    assert len(calls) == 1


def test_single_detected_tool_becomes_named_one_click_offer(
    app, tmp_path, monkeypatch
):
    finding = SimpleNamespace(app="PlenBot Log Uploader")
    wizard, welcome = make_wizard(tmp_path, monkeypatch, [finding])

    welcome.autodetect_button.click()

    assert welcome.competitor_import_button.text() == (
        "Found PlenBot Log Uploader — set me up from it"
    )
    assert "found PlenBot Log Uploader" in welcome.competitor_import_status.text()
    assert not welcome.competitor_offer.isHidden()
    assert welcome.import_button.text() == "I Also Have a Guild File"
    assert "Start with PlenBot Log Uploader above" in welcome.guild_file_intro.text()
    assert welcome._detected_findings == (finding,)
    # The offer is the lead now — the permission button retires.
    assert welcome.autodetect_lead.isHidden()


def test_multiple_detected_tools_offer_to_combine_not_pick_one(
    app, tmp_path, monkeypatch
):
    findings = [
        SimpleNamespace(app="MzFightReporter"),
        SimpleNamespace(app="PlenBot Log Uploader"),
    ]
    wizard, welcome = make_wizard(tmp_path, monkeypatch, findings)

    welcome.autodetect_button.click()

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
    welcome.autodetect_button.click()

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

    welcome.autodetect_button.click()

    assert welcome.competitor_offer.isHidden()
    assert welcome.competitor_import_button.text() == ""
    assert welcome.competitor_import_status.text() == ""
    # A dry search retires the lead quietly — no error, no nag, just the
    # honest hint that Next sets things up by hand.
    assert welcome.autodetect_button.isHidden()
    assert welcome.autodetect_subtext.isHidden()
    assert not welcome.autodetect_none_hint.isHidden()
    # The guild file offer stays a quiet flat button at the bottom, since
    # most people never receive one.
    assert not welcome.easy_path_intro.isHidden()
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

    assert calls == []  # nothing hunted before the click

    welcome.autodetect_button.click()
    welcome.autodetect_button.click()

    assert calls == [1]  # once, and the failure never reached the user
    assert welcome.competitor_offer.isHidden()
    assert welcome.competitor_import_button.text() == ""
    assert welcome.autodetect_button.isHidden()
    assert not welcome.autodetect_none_hint.isHidden()


def test_primary_offer_always_previews_named_tool_and_then_gently_asks_for_guild(
    app, tmp_path, monkeypatch
):
    # Single detected tool → direct preview, no picker. (The multi-tool
    # combine+picker flow is covered by its own test.)
    findings = [
        SimpleNamespace(app="MzFightReporter"),
    ]
    wizard, welcome = make_wizard(tmp_path, monkeypatch, findings)
    welcome.autodetect_button.click()
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
