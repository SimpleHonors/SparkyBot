"""Behavior/configVersion — the marker key future one-shot migrations gate on
(slice 2). Written on every save; absent-tolerated on load (an existing file
without it reads as version 1). NO other keys change in this slice.
"""

from core.config import Config


def test_new_config_loads_at_current_version(tmp_path):
    # No file on disk -> brand-new config, nothing to migrate
    cfg = Config(str(tmp_path / "config.properties"))
    assert cfg.is_new_config
    assert Config.CONFIG_VERSION == 2
    assert cfg.config_version == Config.CONFIG_VERSION


def test_existing_file_without_marker_reads_as_version_1(tmp_path):
    p = tmp_path / "settings.ini"
    p.write_text("[Behavior]\ncloseToTray = true\n")
    cfg = Config(str(p))
    assert cfg.config_version == 1
    # Loading is otherwise untouched
    assert cfg.close_to_tray is True


def test_save_stamps_marker_and_reload_reads_it(tmp_path):
    p = tmp_path / "settings.ini"
    p.write_text("[Discord]\n")
    cfg = Config(str(p))
    assert cfg.config_version == 1

    assert cfg.save(p) is True
    # configparser lowercases option names on write, same as every other key
    assert "configversion = 2" in p.read_text().lower()

    again = Config(str(p))
    assert again.config_version == 2


def test_marker_present_in_file_is_respected(tmp_path):
    p = tmp_path / "settings.ini"
    p.write_text("[Behavior]\nconfigVersion = 2\n")
    cfg = Config(str(p))
    assert cfg.config_version == 2


def test_malformed_marker_tolerated_as_pre_migration(tmp_path):
    # A garbage value must never crash load; falling back to 1 is the safe
    # assumption (migrations re-run rather than silently skip)
    p = tmp_path / "settings.ini"
    p.write_text("[Behavior]\nconfigVersion = banana\n")
    cfg = Config(str(p))
    assert cfg.config_version == 1
