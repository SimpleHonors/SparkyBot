"""Tests for raid-report cache wiring: config defaults, cache_key, _cache_or_delete_json"""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from core.config import Config
from core.gw2ei_invoker import GW2EIInvoker, PARSE_CONFIG_CONTENT
from core.raid_session import RaidReportCache, settings_fingerprint


# ---------------------------------------------------------------------------
# Config defaults + parsing round-trip
# ---------------------------------------------------------------------------

def test_raidreport_config_defaults():
    config = Config()
    assert config.raidreport_cache_enabled is True
    assert config.raidreport_cache_dir == ""
    assert config.raidreport_cache_retention_hours == 48
    assert config.raidreport_viewer_html == ""
    assert config.raidreport_output_dir == ""
    assert config.raidreport_always_zip is False


def test_raidreport_get_cache_dir_default():
    config = Config()
    result = config.get_raidreport_cache_dir()
    assert result == config.home_dir / "RaidReportCache"


def test_raidreport_get_cache_dir_custom():
    config = Config()
    config.raidreport_cache_dir = "/custom/cache/path"
    result = config.get_raidreport_cache_dir()
    assert result == Path("/custom/cache/path")


def test_raidreport_get_output_dir_default():
    config = Config()
    result = config.get_raidreport_output_dir()
    assert result == Config.default_raidreport_output_dir()


def test_raidreport_get_output_dir_custom():
    config = Config()
    config.raidreport_output_dir = "/custom/output"
    result = config.get_raidreport_output_dir()
    assert result == Path("/custom/output")


def test_raidreport_get_output_dir_falls_back_to_viewer_parent(tmp_path):
    viewer = tmp_path / "report.html"
    viewer.write_text("")

    config = Config()
    config.raidreport_viewer_html = str(viewer)
    # output_dir is empty string -> fallback to viewer parent
    result = config.get_raidreport_output_dir()
    assert result == tmp_path


def test_raidreport_get_output_dir_viewer_nonexistent(tmp_path):
    viewer = tmp_path / "nope" / "report.html"

    config = Config()
    config.raidreport_viewer_html = str(viewer)
    # parent doesn't exist -> fallback to the temp-dir default
    result = config.get_raidreport_output_dir()
    assert result == Config.default_raidreport_output_dir()


def test_raidreport_config_round_trip(tmp_path):
    """Write config.properties, load it, verify RaidReport values."""
    props = tmp_path / "config.properties"
    props.write_text(
        "[RaidReport]\n"
        "raidreportCacheEnabled = false\n"
        "raidreportCacheDir = /my/cache\n"
        "raidreportCacheRetentionHours = 24\n"
        "raidreportViewerHtml = /my/viewer.html\n"
        "raidreportOutputDir = /my/output\n"
        "raidreportAlwaysZip = true\n"
    )

    config = Config(str(props))
    assert config.raidreport_cache_enabled is False
    assert config.raidreport_cache_dir == "/my/cache"
    assert config.raidreport_cache_retention_hours == 24
    assert config.raidreport_viewer_html == "/my/viewer.html"
    assert config.raidreport_output_dir == "/my/output"
    assert config.raidreport_always_zip is True

    assert config.get_raidreport_cache_dir() == Path("/my/cache")
    assert config.get_raidreport_output_dir() == Path("/my/output")


# ---------------------------------------------------------------------------
# _cache_or_delete_json behaviour (replicated logic with fakes)
# ---------------------------------------------------------------------------

class _FakeInvoker:
    def cache_key(self):
        return ("2.50.0", "abcd1234efgh")


def test_cache_or_delete_json_enabled_stores_and_moves(tmp_path):
    """Enabled: JSON moved into cache, source no longer exists."""
    log_file = tmp_path / "20240601-180000.zevtc"
    log_file.write_text("")
    json_file = tmp_path / "parsed.json"
    json_file.write_text('{"data": 1}')

    config = Config()
    config.raidreport_cache_enabled = True
    had_home_dir = config.home_dir
    cache_root = tmp_path / "cache"

    invoker = _FakeInvoker()

    # Logic mirroring _cache_or_delete_json in main.py
    if config.raidreport_cache_enabled:
        try:
            cache = RaidReportCache(cache_root)
            cache.store(log_file, json_file, *invoker.cache_key())
        except Exception:
            json_file.unlink(missing_ok=True)
    else:
        json_file.unlink(missing_ok=True)

    assert not json_file.exists()
    found = RaidReportCache(cache_root).lookup(log_file, "2.50.0", "abcd1234efgh")
    assert found is not None
    assert found.read_text() == '{"data": 1}'

    # Restore home_dir to avoid side effects
    config.home_dir = had_home_dir


def test_cache_or_delete_json_store_fails_deletes(tmp_path):
    """Store raises -> fallback delete (json is removed)."""
    log_file = tmp_path / "20240601-180000.zevtc"
    log_file.write_text("")
    json_file = tmp_path / "parsed.json"
    json_file.write_text('{"data": 1}')

    config = Config()
    config.raidreport_cache_enabled = True
    cache_root = tmp_path / "cache"

    invoker = _FakeInvoker()

    cache = RaidReportCache(cache_root)
    # Monkeypatch store to raise
    original_store = cache.store
    cache.store = MagicMock(side_effect=OSError("disk full"))

    if config.raidreport_cache_enabled:
        try:
            cache.store(log_file, json_file, *invoker.cache_key())
        except Exception:
            json_file.unlink(missing_ok=True)
    else:
        json_file.unlink(missing_ok=True)

    assert not json_file.exists()
    cache.store.assert_called_once()


def test_cache_or_delete_json_disabled_deletes(tmp_path):
    """Cache disabled -> file deleted, no cache write."""
    log_file = tmp_path / "20240601-180000.zevtc"
    log_file.write_text("")
    json_file = tmp_path / "parsed.json"
    json_file.write_text('{"data": 1}')

    config = Config()
    config.raidreport_cache_enabled = False
    cache_root = tmp_path / "cache"
    invoker = _FakeInvoker()

    cache = RaidReportCache(cache_root)
    cache.store = MagicMock()

    if config.raidreport_cache_enabled:
        try:
            cache.store(log_file, json_file, *invoker.cache_key())
        except Exception:
            json_file.unlink(missing_ok=True)
    else:
        json_file.unlink(missing_ok=True)

    assert not json_file.exists()
    cache.store.assert_not_called()


# ---------------------------------------------------------------------------
# cache_key
# ---------------------------------------------------------------------------

def test_cache_key_returns_tuple_of_two_strings():
    with patch('core.ei_updater.EIUpdater') as mock_ei:
        mock_updater = MagicMock()
        mock_updater.get_current_version.return_value = "2.50.0"
        mock_ei.return_value = mock_updater

        invoker = GW2EIInvoker(None)
        invoker.home_dir = Path("/fake")
        ei_ver, fp = invoker.cache_key()

        assert isinstance(ei_ver, str)
        assert isinstance(fp, str)
        assert ei_ver == "2.50.0"
        assert len(fp) == 12
        assert fp == settings_fingerprint(PARSE_CONFIG_CONTENT)


def test_cache_key_unknown_version():
    with patch('core.ei_updater.EIUpdater') as mock_ei:
        mock_updater = MagicMock()
        mock_updater.get_current_version.return_value = ""
        mock_ei.return_value = mock_updater

        invoker = GW2EIInvoker(None)
        invoker.home_dir = Path("/fake")
        ei_ver, fp = invoker.cache_key()

        assert ei_ver == "unknown"
        assert len(fp) == 12


# ---------------------------------------------------------------------------
# PARSE_CONFIG_CONTENT unchanged by _ensure_parse_config
# ---------------------------------------------------------------------------

def test_parse_config_content_unchanged_by_ensure_parse_config(tmp_path):
    invoker = GW2EIInvoker(None)
    invoker.home_dir = tmp_path

    gw2ei_folder = tmp_path / "GW2EI"
    Settings = gw2ei_folder / "Settings"
    Settings.mkdir(parents=True, exist_ok=True)

    # Monkey-patch get_gw2ei_folder to return our temp dir
    invoker.get_gw2ei_folder = lambda: gw2ei_folder

    config_path = invoker._ensure_parse_config("test_parse.conf")
    written = config_path.read_text(encoding="utf-8")

    assert written == PARSE_CONFIG_CONTENT


# ---------------------------------------------------------------------------
# Banned-words guard — "night" terminology must not appear in core/
# ---------------------------------------------------------------------------

def test_no_night_terminology_in_core():
    """The old feature name ("night report") must never creep back into
    core/ — the feature is called "raid report" everywhere. ONE exemption:
    the FINAL-DESIGN wizard question uses the operator-approved verbatim
    phrase "end-of-night" as plain-English timing (usage-mode page); that
    exact phrase is allowed, nothing else."""
    root = Path(__file__).resolve().parent.parent
    core_dir = root / "core"

    night_filenames = sorted(
        f.name for f in core_dir.iterdir()
        if f.is_file() and "night" in f.name.lower()
    )
    assert night_filenames == [], (
        f"Files with 'night' in name: {night_filenames}"
    )

    hits = []
    for source in sorted(core_dir.glob("*.py")):
        text = source.read_text(encoding="utf-8", errors="replace").lower()
        text = text.replace("end-of-night", "")   # the verbatim exemption
        if "night" in text:
            hits.append(source.name)
    assert hits == [], (
        f"Source files containing 'night': {hits}"
    )
