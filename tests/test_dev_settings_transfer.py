"""Dev-mode FULL settings export/import.

The surface only exists behind --dev (menu actions ABSENT otherwise, not
grayed), exports self-label as secret-bearing, imports refuse anything
that is not a SparkyBot full export, and the safe shareable guild config
path is never involved.
"""

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import dev_settings_transfer as devst
from core.config import Config


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _config_with_secrets(tmp_path, name="a"):
    config = Config(tmp_path / f"config-{name}.properties")
    config.update("Discord", "discordWebhook",
                  "https://discord.com/api/webhooks/123/secret-token")
    config.update("AI", "aiApiKey", "sk-super-secret")
    config.update("Thresholds", "minFightDuration", "42")
    config.save()
    # Reload so the typed attributes (config.discord_webhook, ...) match
    # the parser state — update() only touches the parser.
    return Config(tmp_path / f"config-{name}.properties")


# ------------------------------------------------------------- dev gate

def test_dev_mode_follows_the_env_flag(monkeypatch):
    monkeypatch.delenv(devst.DEV_MODE_ENV, raising=False)
    assert not devst.dev_mode_active()
    monkeypatch.setenv(devst.DEV_MODE_ENV, "1")
    assert devst.dev_mode_active()


# --------------------------------------------------------------- export

def test_export_writes_marker_header_and_secrets(tmp_path):
    config = _config_with_secrets(tmp_path)
    dest = tmp_path / devst.SUGGESTED_FILENAME
    out = devst.export_full_settings(config, dest)
    text = out.read_text(encoding="utf-8")
    assert "CONTAINS SECRETS" in text.splitlines()[1]
    assert f"[{devst.MARKER_SECTION}]" in text
    assert "containssecrets = true" in text.lower()
    assert "secret-token" in text          # secrets are the whole point
    assert "sk-super-secret" in text


# --------------------------------------------------------------- import

def test_import_round_trips_everything_and_saves(tmp_path):
    exported = devst.export_full_settings(
        _config_with_secrets(tmp_path), tmp_path / "full.ini")

    target = Config(tmp_path / "config-b.properties")
    assert target._config.get("AI", "aiApiKey") == ""
    applied = devst.import_full_settings(target, exported)
    assert applied > 10  # whole config, not a slice
    assert target._config.get("Discord", "discordWebhook") \
        == "https://discord.com/api/webhooks/123/secret-token"
    assert target._config.get("AI", "aiApiKey") == "sk-super-secret"
    assert target._config.get("Thresholds", "minFightDuration") == "42"
    # persisted, not just in memory
    reloaded = Config(tmp_path / "config-b.properties")
    assert reloaded._config.get("AI", "aiApiKey") == "sk-super-secret"
    # the marker never leaks into the live config
    assert not target._config.has_section(devst.MARKER_SECTION)


def test_import_refuses_files_without_the_marker(tmp_path):
    impostor = tmp_path / "innocent.ini"
    impostor.write_text("[Discord]\ndiscordWebhook = x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a SparkyBot full settings"):
        devst.import_full_settings(_config_with_secrets(tmp_path), impostor)


def test_import_refuses_garbage(tmp_path):
    garbage = tmp_path / "garbage.ini"
    garbage.write_bytes(b"\x00\x01 not ini at all")
    with pytest.raises(ValueError):
        devst.import_full_settings(_config_with_secrets(tmp_path), garbage)


# ------------------------------------------------------------- menu gate

def _make_window(tmp_path, name):
    from core.main_window import MainWindow
    config = Config(tmp_path / f"mw-{name}.properties")
    return MainWindow(config)


def test_menu_actions_absent_without_dev_mode(qt_app, tmp_path, monkeypatch):
    monkeypatch.delenv(devst.DEV_MODE_ENV, raising=False)
    window = _make_window(tmp_path, "nodev")
    try:
        assert not hasattr(window, "action_dev_export_full")
        assert not hasattr(window, "action_dev_import_full")
    finally:
        window.close()


def test_menu_actions_present_in_dev_mode(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv(devst.DEV_MODE_ENV, "1")
    window = _make_window(tmp_path, "dev")
    try:
        assert window.action_dev_export_full.text().lower().count("secret")
        assert window.action_dev_import_full is not None
    finally:
        window.close()


def test_shareable_guild_config_still_strips_secrets(tmp_path):
    """The dev path must not loosen the safe path's allowlist."""
    from core.shareable_config import bundle_from_config
    config = _config_with_secrets(tmp_path)
    bundle = str(bundle_from_config(config))
    assert "sk-super-secret" not in bundle
