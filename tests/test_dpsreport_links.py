"""dps.report links: uploader contract, pipeline wiring, settings surface.

The feature is additive-only and off by default: no upload may ever run
unless the user both enabled the option AND picked a timing mode, and no
upload failure may ever cost a fight post.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import dpsreport


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- uploader

class _FakeResponse:
    def __init__(self, status_code=200, payload=None, body_is_json=True):
        self.status_code = status_code
        self._payload = payload
        self._body_is_json = body_is_json

    def json(self):
        if not self._body_is_json:
            raise ValueError("not json")
        return self._payload


def _log_file(tmp_path):
    p = tmp_path / "20260830-210000.zevtc"
    p.write_bytes(b"EVTC fake")
    return p


def test_upload_returns_permalink_and_hits_the_documented_endpoint(
        tmp_path, monkeypatch):
    calls = {}

    def fake_post(url, params=None, files=None, timeout=None):
        calls["url"] = url
        calls["params"] = params
        calls["filename"] = files["file"][0]
        return _FakeResponse(payload={
            "id": "abcd-20260830-210000",
            "permalink": "https://dps.report/abcd-20260830-210000_wvw",
        })

    monkeypatch.setattr(dpsreport.requests, "post", fake_post)
    link = dpsreport.upload_log(_log_file(tmp_path))
    assert link == "https://dps.report/abcd-20260830-210000_wvw"
    assert calls["url"] == "https://dps.report/uploadContent"
    assert calls["params"]["json"] == "1"
    assert calls["params"]["generator"] == "ei"
    assert calls["filename"].endswith(".zevtc")


@pytest.mark.parametrize("response", [
    _FakeResponse(status_code=403, payload={}),
    _FakeResponse(payload={"error": "rate limited"}),
    _FakeResponse(payload={"id": "x"}),          # no permalink
    _FakeResponse(payload={"permalink": 5}),      # wrong type
    _FakeResponse(body_is_json=False),
])
def test_upload_swallows_every_bad_response(tmp_path, monkeypatch, response):
    monkeypatch.setattr(dpsreport.requests, "post",
                        lambda *a, **k: response)
    assert dpsreport.upload_log(_log_file(tmp_path)) is None


def test_upload_swallows_transport_errors(tmp_path, monkeypatch):
    import requests as real_requests

    def boom(*a, **k):
        raise real_requests.ConnectionError("offline")

    monkeypatch.setattr(dpsreport.requests, "post", boom)
    assert dpsreport.upload_log(_log_file(tmp_path)) is None


def test_upload_swallows_unreadable_file(tmp_path):
    assert dpsreport.upload_log(tmp_path / "missing.zevtc") is None


# ------------------------------------------------------------ activation

def _cfg(enabled, timing):
    return SimpleNamespace(dpsreport_links_enabled=enabled,
                           dpsreport_timing=timing)


def test_links_only_active_with_enabled_plus_valid_timing():
    assert dpsreport.links_active(_cfg(True, "link_later"))
    assert dpsreport.links_active(_cfg(True, "together"))
    assert not dpsreport.links_active(_cfg(True, ""))
    assert not dpsreport.links_active(_cfg(True, "sometime"))
    assert not dpsreport.links_active(_cfg(False, "together"))
    assert not dpsreport.links_active(_cfg(False, ""))


def test_config_defaults_are_off_and_unpicked(tmp_path, monkeypatch):
    from core.config import Config
    monkeypatch.chdir(tmp_path)
    config = Config(str(tmp_path / "settings.ini"))
    assert config.dpsreport_links_enabled is False
    assert config.dpsreport_timing == ""
    assert not dpsreport.links_active(config)


# ------------------------------------------------------- pipeline wiring

class _FakeDiscord:
    """Records send_to_all calls in order."""

    def __init__(self):
        self.calls = []

    def send_to_all(self, message="", embeds=None, icon_path=None,
                    audio_bytes=None, audio_filename=None):
        self.calls.append({"message": message, "embeds": embeds})
        return 1


def _run_pipeline(tmp_path, monkeypatch, *, enabled, timing,
                  permalink="https://dps.report/fake_wvw"):
    """Drive main.process_log_file with everything but dpsreport faked."""
    import main as main_mod

    log = _log_file(tmp_path)
    json_file = tmp_path / (log.stem + ".json")
    json_file.write_text("{}")

    uploads = []

    def fake_upload(path, timeout=120):
        uploads.append(Path(path))
        return permalink

    monkeypatch.setattr(dpsreport, "upload_log", fake_upload)

    class _FakeReport:
        zone = "Eternal Battlegrounds"
        duration_ms = 300_000
        total_downs = 30
        total_damage = 5_000_000
        callout_cooldown = None

        def __init__(self, data):
            pass

        def set_embed_color(self, c):
            pass

        def get_ai_summary(self):
            return None

        def get_discord_embeds(self, display_config, icon_filename=None):
            return [{"title": "fight"}]

    monkeypatch.setattr(main_mod, "FightReport", _FakeReport)
    monkeypatch.setattr(main_mod, "_cache_or_delete_json",
                        lambda *a, **k: None)

    config = SimpleNamespace(
        min_fight_duration=10, min_fight_downs=5, min_fight_total_dmg=50_000,
        enable_discord_bot=True, embed_color=0x00A86B,
        show_damage=True, show_burst_dmg=True, show_strips=True,
        show_cleanses=True, show_heals=True, show_defense=True,
        show_ccs=True, show_downs_kills=True, show_quick_report=True,
        show_offensive_boons=True, show_defensive_boons=True,
        show_top_enemy_skills=True, show_enemy_breakdown=True,
        enable_ai_analysis=False, ai_base_url="", ai_model="",
        enable_twitch=False, twitch_token="", twitch_channel="",
        tts_enabled=False, tts_discord_attach=False,
        dpsreport_links_enabled=enabled, dpsreport_timing=timing,
        get_thumbnail_path=lambda: None,
    )
    gw2ei = SimpleNamespace(parse_file=lambda p, **k: json_file)
    discord = _FakeDiscord()
    result = main_mod.process_log_file(log, config, gw2ei, discord)
    return result, discord, uploads


def test_disabled_never_uploads(tmp_path, monkeypatch):
    _, discord, uploads = _run_pipeline(
        tmp_path, monkeypatch, enabled=False, timing="together")
    assert uploads == []
    assert len(discord.calls) == 1  # just the fight post


def test_enabled_without_timing_never_uploads(tmp_path, monkeypatch):
    _, discord, uploads = _run_pipeline(
        tmp_path, monkeypatch, enabled=True, timing="")
    assert uploads == []
    assert len(discord.calls) == 1


def test_together_mode_bakes_link_into_the_fight_post(tmp_path, monkeypatch):
    _, discord, uploads = _run_pipeline(
        tmp_path, monkeypatch, enabled=True, timing="together")
    assert len(uploads) == 1
    assert len(discord.calls) == 1
    fields = discord.calls[0]["embeds"][0]["fields"]
    assert fields[0]["name"] == "dps.report"
    assert fields[0]["value"].startswith("https://dps.report/")


def test_link_later_mode_posts_fight_first_then_link(tmp_path, monkeypatch):
    _, discord, uploads = _run_pipeline(
        tmp_path, monkeypatch, enabled=True, timing="link_later")
    assert len(uploads) == 1
    assert len(discord.calls) == 2
    # fight post first, untouched
    assert discord.calls[0]["embeds"] == [{"title": "fight"}]
    assert "fields" not in discord.calls[0]["embeds"][0]
    # link follow-up second
    assert "https://dps.report/" in discord.calls[1]["message"]
    assert "Eternal Battlegrounds" in discord.calls[1]["message"]


def test_failed_upload_never_costs_the_fight_post(tmp_path, monkeypatch):
    for timing in ("together", "link_later"):
        _, discord, _ = _run_pipeline(
            tmp_path, monkeypatch, enabled=True, timing=timing,
            permalink=None)
        assert len(discord.calls) == 1
        assert discord.calls[0]["embeds"][0].get("fields") in (None, [])


# ------------------------------------------------------- settings surface

def test_settings_requires_timing_choice_before_save(qt_app, tmp_path,
                                                     monkeypatch):
    monkeypatch.chdir(tmp_path)
    from core.config import Config
    from core.gui_settings import SettingsWindow

    config = Config(str(tmp_path / "settings.ini"))
    engine = SettingsWindow(config)
    engine.run_update_checks_once = lambda: None
    try:
        # Keep the earlier Discord-webhook validation out of the way — this
        # test is about the dps.report timing requirement.
        engine.enable_discord.setChecked(False)
        engine.dpsreport_enabled.setChecked(True)
        saved, _relaunch = engine._save_settings()
        assert saved is False
        assert "timing" in engine._last_save_error

        engine.dpsreport_link_later.setChecked(True)
        saved, _relaunch = engine._save_settings()
        assert saved is True
        assert config.dpsreport_links_enabled is True
        assert config.dpsreport_timing == dpsreport.TIMING_LINK_LATER
    finally:
        engine.close()


def test_settings_dialog_surfaces_dpsreport_group_on_fight_reports_page(
        qt_app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from core.config import Config
    from core.gui_settings import SettingsWindow
    from core.settings_dialog import CAT_FIGHT_REPORTS, SettingsDialog

    config = Config(str(tmp_path / "settings.ini"))
    engine = SettingsWindow(config)
    engine.run_update_checks_once = lambda: None
    dialog = SettingsDialog(engine)
    try:
        dialog.open_dialog()
        qt_app.processEvents()
        dialog._select_category(CAT_FIGHT_REPORTS)
        qt_app.processEvents()
        page = dialog._pages[CAT_FIGHT_REPORTS]

        # The engine's group moved wholesale onto the Fight Reports page.
        assert page.isAncestorOf(engine.dpsreport_group_box)
        assert page.isAncestorOf(engine.dpsreport_link_later)

        # Radios stay disabled until the feature is switched on, and no
        # timing is pre-picked for the user.
        assert not engine.dpsreport_link_later.isEnabled()
        engine.dpsreport_enabled.setChecked(True)
        assert engine.dpsreport_link_later.isEnabled()
        assert not engine.dpsreport_link_later.isChecked()
        assert not engine.dpsreport_together.isChecked()
    finally:
        # Leave the dialog clean so closeEvent's unsaved-changes prompt (a
        # modal box that would hang offscreen) never fires.
        engine.dpsreport_enabled.setChecked(False)
        dialog.close()
        engine.close()
