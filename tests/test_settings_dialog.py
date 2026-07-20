"""Settings dialog (slice 4): modal category dialog over the legacy engine.

Contract under test:
- category list: 6 base categories AI-off; separator + AI Commentary /
  Voice / Vocabulary appear only when AI/enableAiAnalysis is true at open;
- real dirty tracking: Apply enables only when something changed, Cancel
  discards, OK persists, closing with unsaved changes prompts;
- every control is re-homed onto its task-shaped page while its config key
  stays exactly where it always was (zero renames);
- pages build lazily; the Application page's first view fires the one-time
  GitHub update checks (construction never does);
- RaidReport/runMode round-trips through the "How reports get made" stub;
- the word "Thresholds" (and emoji) never appear in dialog UI text.
"""

import configparser
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

pytest.importorskip("PySide6", reason="PySide6 not installed in this environment")

from PySide6.QtWidgets import (
    QApplication, QCheckBox, QGroupBox, QLabel, QPushButton, QRadioButton,
    QWidget,
)

from core.settings_dialog import (
    SettingsDialog, BASE_CATEGORIES, AI_CATEGORIES,
    CAT_DISCORD, CAT_FIGHT_REPORTS, CAT_WATCHER, CAT_RAID_REPORTS,
    CAT_TWITCH, CAT_APPLICATION, CAT_AI, CAT_VOICE, CAT_VOCABULARY,
    _ROLE_CATEGORY,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture()
def config(tmp_path):
    """Real Config isolated to tmp_path: home_dir is redirected so save()
    and the vocabulary JSON never touch the repo root."""
    from core.config import Config
    cfg = Config(tmp_path / "config.properties")
    cfg.home_dir = tmp_path
    return cfg


@pytest.fixture()
def engine(qapp, config, monkeypatch):
    """Headless SettingsWindow engine with the GitHub checks stubbed out.
    The recorded check calls are exposed as engine._test_check_log."""
    from core.gui_settings import SettingsWindow
    checks = []
    monkeypatch.setattr(SettingsWindow, "_check_sparkybot_status",
                        lambda self: checks.append("sparkybot"))
    monkeypatch.setattr(SettingsWindow, "_check_ei_status",
                        lambda self: checks.append("ei"))
    eng = SettingsWindow(config)
    eng._test_check_log = checks
    return eng


@pytest.fixture()
def dialog(engine):
    dlg = SettingsDialog(engine)
    yield dlg
    if dlg.isVisible():
        dlg.reject()


def _saved_value(config, section, key):
    """Read a value straight from the saved config.properties file."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config.home_dir / "config.properties")
    return parser.get(section, key)


def _build_all_pages(dlg):
    for row in range(dlg.category_list.count()):
        if dlg.category_list.item(row).data(_ROLE_CATEGORY):
            dlg.category_list.setCurrentRow(row)


# ---------------------------------------------------------------------------
# categories & AI gating at open
# ---------------------------------------------------------------------------

def test_ai_off_hides_ai_categories(dialog):
    dialog.open_dialog()
    assert dialog.visible_categories() == list(BASE_CATEGORIES)
    # The separator row is hidden along with the AI block
    sep_row = dialog.category_list.row(dialog._separator_item)
    assert dialog.category_list.isRowHidden(sep_row)


def test_ai_on_adds_ai_categories_after_separator(dialog, config):
    config.update('AI', 'enableAiAnalysis', 'true')
    assert config.save()
    dialog.open_dialog()
    assert dialog.visible_categories() == \
        list(BASE_CATEGORIES) + list(AI_CATEGORIES)
    sep_row = dialog.category_list.row(dialog._separator_item)
    assert not dialog.category_list.isRowHidden(sep_row)
    # The separator sits between the base block and the AI block
    assert sep_row == len(BASE_CATEGORIES)


def test_ai_visibility_reevaluated_each_open(dialog, config):
    dialog.open_dialog()
    assert CAT_AI not in dialog.visible_categories()
    dialog.reject()
    config.update('AI', 'enableAiAnalysis', 'true')
    assert config.save()
    dialog.open_dialog()
    assert CAT_AI in dialog.visible_categories()


# ---------------------------------------------------------------------------
# lazy pages + page-open hooks
# ---------------------------------------------------------------------------

def test_pages_build_lazily(dialog):
    dialog.open_dialog()
    assert set(dialog._pages) == {CAT_DISCORD}
    row = next(r for r in range(dialog.category_list.count())
               if dialog.category_list.item(r).data(_ROLE_CATEGORY) == CAT_WATCHER)
    dialog.category_list.setCurrentRow(row)
    assert set(dialog._pages) == {CAT_DISCORD, CAT_WATCHER}


def test_application_page_first_view_runs_update_checks_once(dialog, engine):
    dialog.open_dialog()
    assert engine._test_check_log == []
    app_row = next(r for r in range(dialog.category_list.count())
                   if dialog.category_list.item(r).data(_ROLE_CATEGORY) == CAT_APPLICATION)
    dialog.category_list.setCurrentRow(app_row)
    assert sorted(engine._test_check_log) == ["ei", "sparkybot"]
    # Revisits — same open and a fresh open — never re-check
    dialog.category_list.setCurrentRow(0)
    dialog.category_list.setCurrentRow(app_row)
    dialog.reject()
    dialog.open_dialog()
    dialog.category_list.setCurrentRow(app_row)
    assert sorted(engine._test_check_log) == ["ei", "sparkybot"]


# ---------------------------------------------------------------------------
# dirty tracking: Apply / Cancel / OK / close prompt
# ---------------------------------------------------------------------------

def test_edit_enables_apply(dialog, engine):
    dialog.open_dialog()
    assert not dialog.apply_button.isEnabled()
    engine.discord_webhook.setText("https://discord.com/api/webhooks/1/x")
    assert dialog.is_dirty()
    assert dialog.apply_button.isEnabled()
    # Reverting the edit disables Apply again (value-compare, not touched-flag)
    engine.discord_webhook.setText("")
    assert not dialog.apply_button.isEnabled()


def test_apply_persists_and_disables(dialog, engine, config):
    dialog.open_dialog()
    engine.min_duration.setValue(42)
    assert dialog.apply_button.isEnabled()
    dialog.apply_button.click()
    assert _saved_value(config, 'Thresholds', 'minFightDuration') == '42'
    assert not dialog.apply_button.isEnabled()
    assert dialog.isVisible()          # Apply never closes


def test_cancel_discards(dialog, engine, config):
    dialog.open_dialog()
    engine.discord_webhook.setText("https://discord.com/api/webhooks/2/y")
    dialog.cancel_button.click()
    assert not dialog.isVisible()
    # Nothing was written...
    assert not (config.home_dir / "config.properties").exists() \
        or _saved_value(config, 'Discord', 'discordWebhook') == ''
    assert config.discord_webhook == ''
    # ...and reopening reloads the stored value into the widget
    dialog.open_dialog()
    assert engine.discord_webhook.text() == ''
    assert not dialog.apply_button.isEnabled()


def test_ok_persists_and_closes(dialog, engine, config):
    dialog.open_dialog()
    engine.discord_webhook.setText("https://discord.com/api/webhooks/3/z")
    dialog.ok_button.click()
    assert not dialog.isVisible()
    assert _saved_value(config, 'Discord', 'discordWebhook') \
        == "https://discord.com/api/webhooks/3/z"
    assert config.discord_webhook == "https://discord.com/api/webhooks/3/z"


def test_close_with_unsaved_changes_prompts(dialog, engine, config, monkeypatch):
    dialog.open_dialog()
    engine.twitch_channel.setText("myguild")

    # Cancel keeps the dialog open with the edit intact
    monkeypatch.setattr(dialog, "_confirm_unsaved", lambda: "cancel")
    assert dialog.close() is False
    assert dialog.isVisible()
    assert engine.twitch_channel.text() == "myguild"

    # Save persists then closes
    monkeypatch.setattr(dialog, "_confirm_unsaved", lambda: "save")
    assert dialog.close() is True
    assert _saved_value(config, 'Twitch', 'twitchChannelName') == "myguild"

    # Discard closes without writing
    dialog.open_dialog()
    engine.twitch_channel.setText("otherguild")
    monkeypatch.setattr(dialog, "_confirm_unsaved", lambda: "discard")
    assert dialog.close() is True
    assert _saved_value(config, 'Twitch', 'twitchChannelName') == "myguild"


def test_clean_close_never_prompts(dialog, monkeypatch):
    dialog.open_dialog()
    prompts = []
    monkeypatch.setattr(dialog, "_confirm_unsaved",
                        lambda: prompts.append(1) or "cancel")
    assert dialog.close() is True
    assert prompts == []


# ---------------------------------------------------------------------------
# control re-homing: category page mapping + key round-trips
# ---------------------------------------------------------------------------

# attr on the engine -> category page that must now host it
_EXPECTED_HOMES = {
    # Discord (incl. uploads out of the old Thresholds tab)
    "enable_discord": CAT_DISCORD,
    "discord_webhook": CAT_DISCORD,
    "discord_webhook_label": CAT_DISCORD,
    "active_webhook": CAT_DISCORD,
    "max_upload": CAT_DISCORD,
    "large_upload_after": CAT_DISCORD,
    # Fight Reports (branding + embed sections)
    "guild_icon": CAT_FIGHT_REPORTS,
    "color_preview": CAT_FIGHT_REPORTS,
    "show_quick_report": CAT_FIGHT_REPORTS,
    "show_damage": CAT_FIGHT_REPORTS,
    "show_defensive_boons": CAT_FIGHT_REPORTS,
    # Watcher & Parsing (paths + fight filters + parse memory)
    "log_folder": CAT_WATCHER,
    "poll_interval": CAT_WATCHER,
    "gw2ei_exe": CAT_WATCHER,
    "max_parse_memory": CAT_WATCHER,
    "min_duration": CAT_WATCHER,
    "min_downs": CAT_WATCHER,
    "min_damage": CAT_WATCHER,
    # Raid Reports (5 settings + run-mode stub)
    "raidreport_viewer_html": CAT_RAID_REPORTS,
    "raidreport_output_dir": CAT_RAID_REPORTS,
    "raidreport_cache_enabled": CAT_RAID_REPORTS,
    "raidreport_poison_tab": CAT_RAID_REPORTS,
    "raidreport_always_zip": CAT_RAID_REPORTS,
    "runmode_run_button": CAT_RAID_REPORTS,
    "runmode_manual": CAT_RAID_REPORTS,
    # Twitch
    "enable_twitch": CAT_TWITCH,
    "twitch_channel": CAT_TWITCH,
    "twitch_token": CAT_TWITCH,
    "twitch_test_btn": CAT_TWITCH,
    # Application (behavior + updates + AI master switch + about)
    "close_to_tray": CAT_APPLICATION,
    "start_with_windows": CAT_APPLICATION,
    "check_updates_on_launch": CAT_APPLICATION,
    "update_sparkybot_button": CAT_APPLICATION,
    "update_ei_button": CAT_APPLICATION,
    "enable_ai": CAT_APPLICATION,
    "about_widget": CAT_APPLICATION,
    # AI-on pages
    "ai_provider": CAT_AI,
    "ai_test_btn": CAT_AI,
    "enable_tts": CAT_VOICE,
    "tts_provider": CAT_VOICE,
    "ai_vocab_shock": CAT_VOCABULARY,
}


def test_controls_re_homed_to_expected_pages(dialog, engine, config):
    config.update('AI', 'enableAiAnalysis', 'true')
    assert config.save()
    dialog.open_dialog()
    _build_all_pages(dialog)
    misplaced = {}
    for attr, expected in _EXPECTED_HOMES.items():
        actual = dialog.page_of(getattr(engine, attr))
        if actual != expected:
            misplaced[attr] = (expected, actual)
    assert not misplaced, f"controls on the wrong page: {misplaced}"


def test_re_homed_controls_keep_their_config_keys(dialog, engine, config):
    """Presentational regroup only — the keys stay in their old sections."""
    dialog.open_dialog()
    _build_all_pages(dialog)
    engine.max_upload.setValue(123)                # Discord page now...
    engine.max_parse_memory.setValue(2048)         # Watcher & Parsing page...
    engine.min_downs.setValue(7)
    engine.show_quick_report.setChecked(False)
    engine.check_updates_on_launch.setChecked(False)
    assert dialog._apply()
    assert _saved_value(config, 'Thresholds', 'maxUploadSize') == '123'
    assert _saved_value(config, 'Behavior', 'maxParseMemory') == '2048'
    assert _saved_value(config, 'Thresholds', 'minFightDowns') == '7'
    assert _saved_value(config, 'UI', 'showQuickReport') == 'False'
    assert _saved_value(config, 'Behavior', 'checkUpdatesOnLaunch') == 'False'


# ---------------------------------------------------------------------------
# RaidReport/runMode stub
# ---------------------------------------------------------------------------

def test_runmode_defaults_to_run_button(config):
    assert config.raidreport_run_mode == 'run-button'


def test_runmode_round_trip(dialog, engine, config, tmp_path):
    from core.config import Config
    dialog.open_dialog()
    _build_all_pages(dialog)
    assert engine.runmode_run_button.isChecked()
    assert not engine.runmode_manual.isChecked()

    engine.runmode_manual.setChecked(True)
    assert dialog.apply_button.isEnabled()
    assert dialog._apply()
    assert _saved_value(config, 'RaidReport', 'runMode') == 'manual'
    assert config.raidreport_run_mode == 'manual'
    # A fresh Config load sees it too
    assert Config(tmp_path / "config.properties").raidreport_run_mode == 'manual'

    # Reopen reflects the stored choice; flipping back round-trips
    dialog.reject()
    dialog.open_dialog()
    assert engine.runmode_manual.isChecked()
    engine.runmode_run_button.setChecked(True)
    assert dialog._apply()
    assert _saved_value(config, 'RaidReport', 'runMode') == 'run-button'


# ---------------------------------------------------------------------------
# copy rules: no "Thresholds", no emoji, in any dialog UI text
# ---------------------------------------------------------------------------

def _dialog_texts(dlg):
    texts = []
    for w in dlg.findChildren(QWidget):
        for getter in ("text", "title", "placeholderText"):
            fn = getattr(w, getter, None)
            if callable(fn):
                try:
                    value = fn()
                except TypeError:
                    continue
                if isinstance(value, str) and value:
                    texts.append(value)
        tip = w.toolTip()
        if tip:
            texts.append(tip)
    texts.append(dlg.windowTitle())
    return texts


def test_dialog_text_never_says_thresholds(dialog, config):
    config.update('AI', 'enableAiAnalysis', 'true')
    assert config.save()
    dialog.open_dialog()
    _build_all_pages(dialog)
    offenders = [t for t in _dialog_texts(dialog) if "hreshold" in t]
    assert offenders == [], f"'Thresholds' leaked into UI text: {offenders}"


def test_dialog_text_has_no_emoji(dialog, config):
    config.update('AI', 'enableAiAnalysis', 'true')
    assert config.save()
    dialog.open_dialog()
    _build_all_pages(dialog)
    banned = ("⚡", "⚠", "\U0001F4CA", "✓", "✗", "✅")
    offenders = [t for t in _dialog_texts(dialog)
                 if any(glyph in t for glyph in banned)]
    assert offenders == [], f"emoji leaked into UI text: {offenders}"


# ---------------------------------------------------------------------------
# structural guards
# ---------------------------------------------------------------------------

def test_engine_no_longer_builds_process_files(engine):
    """The shell owns the only Process Files queue now."""
    assert not hasattr(engine, "process_files_widget")
    titles = [engine.tab_widget.tabText(i)
              for i in range(engine.tab_widget.count())]
    assert "Process Files" not in titles


def test_buttons_are_right_aligned_box(dialog):
    from PySide6.QtWidgets import QDialogButtonBox
    box = dialog.findChild(QDialogButtonBox)
    assert box is not None
    labels = {b.text().replace("&", "") for b in box.buttons()}
    assert {"OK", "Cancel", "Apply"} <= labels


def test_twitch_tls_note_only_when_tls_off(dialog, engine):
    dialog.open_dialog()
    row = next(r for r in range(dialog.category_list.count())
               if dialog.category_list.item(r).data(_ROLE_CATEGORY) == CAT_TWITCH)
    dialog.category_list.setCurrentRow(row)
    engine.twitch_use_tls.setChecked(True)
    assert engine.twitch_tls_note.isHidden()
    engine.twitch_use_tls.setChecked(False)
    assert not engine.twitch_tls_note.isHidden()
