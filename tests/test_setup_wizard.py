"""First-run wizard (slice 8): themed flow with AI opt-in and usage-mode
questions up front.

Contract under test:
- explicit page IDs; page 2 is the AI opt-in question (design-A verbatim
  wording, decline is the default), page 3 the usage-mode question
  (FINAL-DESIGN verbatim, run-button is the default);
- declining AI removes the AI setup and voice pages from the flow
  ENTIRELY (nextId skip — absent, and the wizard looks complete without
  them); accepting keeps the full flow;
- Finish writes AI/enableAiAnalysis from the opt-in answer (single
  writer — the AI page's own enable checkbox is gone) and
  RaidReport/runMode from the usage answer; declined runs never write the
  AI/TTS field values;
- the Dependencies and Twitch pages are CUT PROPOSALS pending operator
  sign-off: still registered, skip-conditions unchanged;
- no emoji and no "simple"/"advanced" user-labeling in the new pages'
  copy (the opt-in page's verbatim "keep it simple" names the SETUP, not
  the user, per design-A).
"""

import configparser
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

pytest.importorskip("PySide6", reason="PySide6 not installed in this environment")

from PySide6.QtWidgets import QApplication, QWidget

from core.setup_wizard import (
    SetupWizard, AIOptInPage, UsageModePage,
    PAGE_WELCOME, PAGE_AI_OPTIN, PAGE_USAGE_MODE, PAGE_DEPENDENCIES,
    PAGE_GW2EI, PAGE_LOG_FOLDER, PAGE_DISCORD, PAGE_TWITCH, PAGE_AI_SETUP,
    PAGE_TTS_VOICE, PAGE_BEHAVIOR, PAGE_COMPLETE,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture()
def config(tmp_path):
    """Real Config isolated to tmp_path (Config.home_dir is app_dir() —
    the repo — unless redirected; save() must never touch the repo root)."""
    from core.config import Config
    cfg = Config(tmp_path / "config.properties")
    cfg.home_dir = tmp_path
    return cfg


@pytest.fixture()
def wizard(qapp, config):
    return SetupWizard(config)


def _saved(config, section, key):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config.home_dir / "config.properties")
    return parser.get(section, key)


def _flow_ids(wizard) -> list:
    """Page IDs actually visited, following each page's nextId from the
    start — the same routing QWizard uses for the Next button."""
    ids = [wizard.startId()]
    while True:
        next_id = wizard.page(ids[-1]).nextId()
        if next_id == -1:
            return ids
        ids.append(next_id)


def _page_texts(page) -> list:
    texts = [page.title(), page.subTitle()]
    for w in page.findChildren(QWidget):
        for getter in ("text", "title", "placeholderText"):
            fn = getattr(w, getter, None)
            if callable(fn):
                try:
                    value = fn()
                except TypeError:
                    continue
                if isinstance(value, str) and value:
                    texts.append(value)
        if w.toolTip():
            texts.append(w.toolTip())
    return [t for t in texts if t]


# ---------------------------------------------------------------------------
# structure: explicit IDs, question pages up front, cut proposals intact
# ---------------------------------------------------------------------------

def test_page_order_questions_up_front(wizard):
    assert wizard.startId() == PAGE_WELCOME
    assert isinstance(wizard.page(PAGE_AI_OPTIN), AIOptInPage)
    assert isinstance(wizard.page(PAGE_USAGE_MODE), UsageModePage)
    # Welcome -> AI question -> usage question, before any plumbing
    assert _flow_ids(wizard)[:3] == [PAGE_WELCOME, PAGE_AI_OPTIN,
                                     PAGE_USAGE_MODE]


def test_cut_proposal_pages_still_registered(wizard):
    """Dependencies + Twitch are CUT PROPOSALS pending operator sign-off —
    they stay functional in place (Dependencies is registered here because
    tests run from source, its unchanged skip-condition)."""
    ids = wizard.pageIds()
    assert PAGE_DEPENDENCIES in ids
    assert PAGE_TWITCH in ids
    assert wizard.twitch_page.skip_check is not None


def test_defaults_decline_ai_and_run_button_mode(wizard):
    assert wizard.ai_optin_page.decline_radio.isChecked()
    assert not wizard.ai_opted_in()
    assert wizard.usage_mode_page.run_button_radio.isChecked()
    assert wizard.usage_mode_page.selected_mode() == "run-button"


def test_primary_choices_are_full_clickable_options(wizard):
    choices = (
        wizard.ai_optin_page.decline_radio,
        wizard.ai_optin_page.accept_radio,
        wizard.usage_mode_page.run_button_radio,
        wizard.usage_mode_page.manual_radio,
    )
    assert all(choice.property("class") == "option" for choice in choices)


def test_local_tts_voice_can_be_added_during_setup(wizard):
    page = wizard.tts_page
    assert page.tts_local_upload_btn.text() == "Add Voice..."
    assert page.tts_local_upload_btn.toolTip()
    page.tts_provider.setCurrentText("local")
    assert not page.local_fields_widget.isHidden()
    assert not page.tts_local_upload_btn.isHidden()


# ---------------------------------------------------------------------------
# flow: declined AI removes the AI/TTS pages entirely
# ---------------------------------------------------------------------------

def test_ai_declined_skips_ai_and_tts_pages(wizard):
    ids = _flow_ids(wizard)
    assert PAGE_AI_SETUP not in ids
    assert PAGE_TTS_VOICE not in ids
    # The flow is still complete: Twitch hops straight to Behavior + Finish
    assert ids == [PAGE_WELCOME, PAGE_AI_OPTIN, PAGE_USAGE_MODE,
                   PAGE_DEPENDENCIES, PAGE_GW2EI, PAGE_LOG_FOLDER,
                   PAGE_DISCORD, PAGE_TWITCH, PAGE_BEHAVIOR, PAGE_COMPLETE]


def test_ai_accepted_keeps_full_flow(wizard):
    wizard.ai_optin_page.accept_radio.setChecked(True)
    ids = _flow_ids(wizard)
    assert ids == [PAGE_WELCOME, PAGE_AI_OPTIN, PAGE_USAGE_MODE,
                   PAGE_DEPENDENCIES, PAGE_GW2EI, PAGE_LOG_FOLDER,
                   PAGE_DISCORD, PAGE_TWITCH, PAGE_AI_SETUP,
                   PAGE_TTS_VOICE, PAGE_BEHAVIOR, PAGE_COMPLETE]


def test_complete_page_mentions_no_ai(wizard):
    """The wizard must look complete without the AI pages — the closing
    page never references them."""
    words = re.compile(r"\b(AI|commentary|voice|TTS)\b", re.IGNORECASE)
    offenders = [t for t in _page_texts(wizard.page(PAGE_COMPLETE))
                 if words.search(t)]
    assert offenders == []


# ---------------------------------------------------------------------------
# Finish: single-writer master switch + usage mode, both existing keys
# ---------------------------------------------------------------------------

def test_accept_declined_writes_false_and_no_ai_fields(wizard, config):
    # Poke AI/TTS widget values that must NOT be persisted when declined
    wizard.ai_page.ai_api_key.setText("sk-should-never-persist")
    wizard.accept()
    assert _saved(config, 'AI', 'enableAiAnalysis') == 'false'
    assert config.enable_ai_analysis is False
    assert _saved(config, 'RaidReport', 'runMode') == 'run-button'
    assert config.raidreport_run_mode == 'run-button'
    # Declined: AI/TTS field values stay at their defaults
    assert _saved(config, 'AI', 'aiApiKey') == ''
    assert _saved(config, 'TTS', 'enableTts') == 'false'


def test_accept_opted_in_writes_true_and_ai_fields(wizard, config):
    wizard.ai_optin_page.accept_radio.setChecked(True)
    wizard.ai_page.ai_api_key.setText("sk-test-key")
    wizard.accept()
    assert _saved(config, 'AI', 'enableAiAnalysis') == 'true'
    assert config.enable_ai_analysis is True
    assert _saved(config, 'AI', 'aiApiKey') == 'sk-test-key'


def test_accept_manual_mode_written(wizard, config):
    wizard.usage_mode_page.manual_radio.setChecked(True)
    wizard.accept()
    assert _saved(config, 'RaidReport', 'runMode') == 'manual'
    assert config.raidreport_run_mode == 'manual'


def test_ai_page_has_no_master_switch(wizard):
    """The opt-in page is the single writer of AI/enableAiAnalysis — the
    AI setup page's old enable checkbox is gone."""
    assert not hasattr(wizard.ai_page, "enable_ai")


# ---------------------------------------------------------------------------
# copy rules for the new pages
# ---------------------------------------------------------------------------

def test_opt_in_page_uses_design_a_wording(wizard):
    texts = "\n".join(_page_texts(wizard.ai_optin_page))
    assert "Want AI commentary?" in texts
    assert "AI hype-commentator blurb" in texts
    assert "free local options work" in texts
    assert "is complete without it" in texts
    assert wizard.ai_optin_page.decline_radio.text() \
        == "No thanks — keep it simple"
    assert wizard.ai_optin_page.accept_radio.text() \
        == "Yes — set up AI commentary"
    assert "change your mind any time" in texts


def test_usage_page_verbatim_and_never_labels_the_user(wizard):
    page = wizard.usage_mode_page
    assert page.title() \
        == "How do you want to make end-of-night raid reports?"
    assert page.run_button_radio.text() == (
        "One-button runs — press Start Run when the raid starts; "
        "End Run builds the report and posts it. (recommended)")
    assert page.manual_radio.text() == (
        "I'll pick fights myself — build reports on the Raid Report "
        "page whenever you want.")
    # Workflows, not skill levels: the words are banned on this page
    banned = re.compile(r"\b(simple|advanced)\b", re.IGNORECASE)
    offenders = [t for t in _page_texts(page) if banned.search(t)]
    assert offenders == []


def test_new_pages_have_no_emoji(wizard):
    banned = ("⚡", "⚠", "\U0001F4CA", "✓", "✗", "✅", "⬇", "↻")
    texts = _page_texts(wizard.ai_optin_page) \
        + _page_texts(wizard.usage_mode_page)
    offenders = [t for t in texts if any(g in t for g in banned)]
    assert offenders == []
