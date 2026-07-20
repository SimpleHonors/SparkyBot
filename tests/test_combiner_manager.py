"""Unit tests for core/combiner_manager — all external calls mocked."""

import configparser
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import requests

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core.combiner_manager import (
    CombinerManager,
    CombinerNotInstalled,
    CombinerRunError,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_test_zip(entry_name: str = "tw5_top_stats.py") -> bytes:
    """Build an in-memory zip containing a dummy entry point."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(entry_name, "dummy content")
    buf.seek(0)
    return buf.getvalue()


def _mock_github_release(tag: str = "v2.0.0", asset_names=None) -> dict:
    if asset_names is None:
        asset_names = ["combiner_release.zip"]
    assets = [
        {
            "name": name,
            "browser_download_url": f"https://example.com/dl/{name}",
        }
        for name in asset_names
    ]
    return {"tag_name": tag, "assets": assets}


# ---------------------------------------------------------------------------
# installed_version / meta.json roundtrip
# ---------------------------------------------------------------------------


def test_installed_version_returns_none_when_no_meta(tmp_path):
    mgr = CombinerManager(data_dir=tmp_path)
    assert mgr.installed_version() is None


def test_installed_version_after_install(tmp_path):
    mgr = CombinerManager(data_dir=tmp_path)
    zip_bytes = _make_test_zip()

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.headers = {"content-length": str(len(zip_bytes))}
    mock_resp.iter_content.return_value = [zip_bytes]

    with patch("core.combiner_manager.requests.get", return_value=mock_resp):
        entry = mgr.download_and_install(
            "https://example.com/test.zip", "2.0.0"
        )

    assert mgr.installed_version() == "2.0.0"
    assert entry.exists() or Path(entry).exists()


def test_download_and_install_meta_fields(tmp_path):
    mgr = CombinerManager(data_dir=tmp_path)
    zip_bytes = _make_test_zip()

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.headers = {"content-length": str(len(zip_bytes))}
    mock_resp.iter_content.return_value = [zip_bytes]

    with patch("core.combiner_manager.requests.get", return_value=mock_resp):
        mgr.download_and_install("https://example.com/test.zip", "3.1.0")

    meta = json.loads((tmp_path / "combiner" / "meta.json").read_text())
    assert meta["version"] == "3.1.0"
    assert meta["url"] == "https://example.com/test.zip"
    assert "installed_at" in meta
    assert "entry" in meta


# ---------------------------------------------------------------------------
# entry detection (platform)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "platform,entry_name,expected_found",
    [
        ("win32", "TopStats.exe", True),
        ("win32", "tw5_top_stats.py", True),
        ("linux", "tw5_top_stats.py", True),
        ("linux", "TopStats.exe", False),
    ],
)
def test_entry_detection_per_platform(
    tmp_path, platform, entry_name, expected_found
):
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / entry_name).write_text("fake")

    with patch.object(sys, "platform", platform):
        result = CombinerManager._find_entry(install_dir)

    assert (result is not None) == expected_found


def test_entry_detection_prefers_exe_on_windows(tmp_path):
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "TopStats.exe").write_text("exe")
    (install_dir / "tw5_top_stats.py").write_text("py")

    with patch.object(sys, "platform", "win32"):
        result = CombinerManager._find_entry(install_dir)

    assert result is not None
    assert "TopStats.exe" in str(result)


# ---------------------------------------------------------------------------
# check_latest
# ---------------------------------------------------------------------------


def test_check_latest_parses_valid_release():
    mgr = CombinerManager(data_dir=Path("/tmp/mock-check-latest"))
    release = _mock_github_release("v1.5.0", ["GW2_EI_Log_Combiner.zip"])

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = release

    with patch("core.combiner_manager.requests.get", return_value=mock_resp):
        result = mgr.check_latest()

    assert result == ("1.5.0", "https://example.com/dl/GW2_EI_Log_Combiner.zip")


def test_check_latest_returns_none_on_no_zip_asset():
    mgr = CombinerManager(data_dir=Path("/tmp/mock-no-zip"))
    release = _mock_github_release("v1.0.0", ["Source.tar.gz"])

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = release

    with patch("core.combiner_manager.requests.get", return_value=mock_resp):
        result = mgr.check_latest()

    assert result is None


def test_check_latest_returns_none_on_request_failure():
    mgr = CombinerManager(data_dir=Path("/tmp/mock-fail"))

    with patch(
        "core.combiner_manager.requests.get",
        side_effect=requests.ConnectionError("offline"),
    ):
        result = mgr.check_latest()

    assert result is None


def test_check_latest_returns_none_on_http_error():
    mgr = CombinerManager(data_dir=Path("/tmp/mock-http-err"))

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 403

    with patch("core.combiner_manager.requests.get", return_value=mock_resp):
        result = mgr.check_latest()

    assert result is None


# ---------------------------------------------------------------------------
# write_run_config
# ---------------------------------------------------------------------------


def test_write_run_config_emits_parseable_ini(tmp_path):
    mgr = CombinerManager(data_dir=tmp_path / "data")
    run_dir = tmp_path / "runs"
    input_dir = tmp_path / "ei_output"
    input_dir.mkdir()

    config_path = mgr.write_run_config(
        run_dir=run_dir,
        input_dir=input_dir,
        guild_name="Test Guild",
        guild_id="ABC-123",
        api_key="key-456",
    )

    assert config_path.exists()
    assert config_path.name == "top_stats_config.ini"

    cp = configparser.ConfigParser()
    cp.read(str(config_path))

    assert cp.has_section("TopStatsCfg")
    assert cp["TopStatsCfg"]["guild_name"] == "Test Guild"
    assert cp["TopStatsCfg"]["guild_id"] == "ABC-123"
    assert cp["TopStatsCfg"]["api_key"] == "key-456"
    assert cp["TopStatsCfg"]["db_update"] == "true"
    assert cp["TopStatsCfg"]["write_all_data_to_json"] == "false"
    assert cp["TopStatsCfg"]["fight_data_charts"] == "true"
    assert cp["TopStatsCfg"]["write_excel"] == "false"

    # db_path must be the stable directory, not a temp path
    assert cp["TopStatsCfg"]["db_path"] == str(mgr.db_dir.resolve())

    # input_directory is absolute
    assert cp["TopStatsCfg"]["input_directory"] == str(input_dir.resolve())


def test_write_run_config_empty_fields_default_to_none(tmp_path):
    mgr = CombinerManager(data_dir=tmp_path / "data")
    run_dir = tmp_path / "runs"
    input_dir = tmp_path / "ei_output"
    input_dir.mkdir()

    config_path = mgr.write_run_config(run_dir=run_dir, input_dir=input_dir)

    cp = configparser.ConfigParser()
    cp.read(str(config_path))
    assert cp["TopStatsCfg"]["guild_name"] == "None"
    assert cp["TopStatsCfg"]["guild_id"] == "None"
    assert cp["TopStatsCfg"]["api_key"] == "None"


def test_write_run_config_db_path_is_stable_absolute(tmp_path):
    mgr = CombinerManager(data_dir=tmp_path / "data")
    run_dir = tmp_path / "runs"
    input_dir = tmp_path / "ei_output"
    input_dir.mkdir()

    config_path = mgr.write_run_config(run_dir=run_dir, input_dir=input_dir)

    cp = configparser.ConfigParser()
    cp.read(str(config_path))
    db_path = cp["TopStatsCfg"]["db_path"]
    assert Path(db_path).is_absolute()
    # stable across calls
    config_path2 = mgr.write_run_config(
        run_dir=tmp_path / "runs2", input_dir=input_dir
    )
    cp2 = configparser.ConfigParser()
    cp2.read(str(config_path2))
    assert cp2["TopStatsCfg"]["db_path"] == db_path


# Complete key/section inventory derived from the upstream combiner's
# unconditional reads. Every section+key below MUST appear in the emitted INI.
_COMBINER_EXPECTED_SECTIONS = {
    "TopStatsCfg": [
        "guild_name", "guild_id", "api_key", "input_directory",
        "output_filename", "json_output_filename", "db_output_filename",
        "db_path", "write_all_data_to_json", "db_update", "fight_data_charts",
        "write_excel", "excel_output_filename", "excel_path",
        "skill_casts_by_role_limit", "hide_columns", "boons_detailed",
        "offensive_detailed", "defenses_detailed", "support_detailed",
        "sort_mode", "chart_mode",
    ],
    "BlackList": ["accounts"],
    "Boon_Weights": [
        "aegis", "alacrity", "fury", "might", "protection", "quickness",
        "regeneration", "resistance", "resolution", "stability", "swiftness",
        "vigor", "superspeed",
    ],
    "Condition_Weights": [
        "bleeding", "burning", "confusion", "poison", "torment",
        "blind", "chilled", "crippled", "fear", "immobile", "slow",
        "taunt", "weakness", "vulnerability",
    ],
    "DiscordCfg": ["webhook_url", "discord_additional_notes"],
    "SupportProfs": ["firebrand", "chronomancer", "specter"],
}


def test_write_run_config_contains_all_upstream_sections_and_keys(tmp_path):
    mgr = CombinerManager(data_dir=tmp_path / "data")
    run_dir = tmp_path / "runs"
    input_dir = tmp_path / "ei_output"
    input_dir.mkdir()

    config_path = mgr.write_run_config(run_dir=run_dir, input_dir=input_dir)

    cp = configparser.ConfigParser()
    cp.read(str(config_path))

    for section, keys in _COMBINER_EXPECTED_SECTIONS.items():
        assert cp.has_section(section), f"Missing section [{section}]"
        for key in keys:
            assert cp.has_option(section, key), (
                f"Missing key '{key}' in section [{section}]"
            )


def test_blacklist_accounts_is_empty(tmp_path):
    mgr = CombinerManager(data_dir=tmp_path / "data")
    run_dir = tmp_path / "runs"
    input_dir = tmp_path / "ei_output"
    input_dir.mkdir()

    config_path = mgr.write_run_config(run_dir=run_dir, input_dir=input_dir)

    cp = configparser.ConfigParser()
    cp.read(str(config_path))
    assert cp["BlackList"]["accounts"] == ""


def test_discord_webhook_url_is_false(tmp_path):
    mgr = CombinerManager(data_dir=tmp_path / "data")
    run_dir = tmp_path / "runs"
    input_dir = tmp_path / "ei_output"
    input_dir.mkdir()

    config_path = mgr.write_run_config(run_dir=run_dir, input_dir=input_dir)

    cp = configparser.ConfigParser()
    cp.read(str(config_path))
    assert cp["DiscordCfg"]["webhook_url"] == "false"


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


def _install_dummy_combiner(mgr: CombinerManager, version: str = "1.0.0"):
    """Install a dummy combiner entry point so run() can locate it."""
    install_dir = mgr._combiner_root / version
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / "tw5_top_stats.py").write_text("dummy")
    mgr._write_meta(version, "https://example.com/fake.zip", str(install_dir / "tw5_top_stats.py"))


def test_run_success_returns_summary_file(tmp_path):
    data_dir = tmp_path / "data"
    mgr = CombinerManager(data_dir=data_dir)
    _install_dummy_combiner(mgr)

    input_dir = tmp_path / "ei_output"
    input_dir.mkdir()

    run_dir = tmp_path / "run"
    run_dir.mkdir()

    summary = input_dir / "Drag_and_Drop_Log_Summary_202501010000.json"
    summary.write_text('{"test": true}')

    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="ok", stderr=""
    )

    with patch("subprocess.run", return_value=completed):
        result = mgr.run(input_dir=input_dir, run_dir=run_dir)

    assert result == summary


def test_run_raises_on_nonzero_exit(tmp_path):
    data_dir = tmp_path / "data"
    mgr = CombinerManager(data_dir=data_dir)
    _install_dummy_combiner(mgr)

    input_dir = tmp_path / "ei_output"
    input_dir.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="something broke"
    )

    with patch("subprocess.run", return_value=completed):
        with pytest.raises(CombinerRunError, match="something broke"):
            mgr.run(input_dir=input_dir, run_dir=run_dir)


def test_run_raises_on_module_not_found_in_stderr(tmp_path):
    data_dir = tmp_path / "data"
    mgr = CombinerManager(data_dir=data_dir)
    _install_dummy_combiner(mgr)

    input_dir = tmp_path / "ei_output"
    input_dir.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    completed = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="ModuleNotFoundError: No module named 'xlsxwriter'",
    )

    with patch("subprocess.run", return_value=completed):
        with pytest.raises(CombinerRunError) as exc_info:
            mgr.run(input_dir=input_dir, run_dir=run_dir)

    msg = str(exc_info.value)
    assert "requests" in msg
    assert "glicko2" in msg
    assert "xlsxwriter" in msg


def test_run_raises_when_no_summary_file_produced(tmp_path):
    data_dir = tmp_path / "data"
    mgr = CombinerManager(data_dir=data_dir)
    _install_dummy_combiner(mgr)

    input_dir = tmp_path / "ei_output"
    input_dir.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="done", stderr=""
    )

    with patch("subprocess.run", return_value=completed):
        with pytest.raises(CombinerRunError, match="Drag_and_Drop"):
            mgr.run(input_dir=input_dir, run_dir=run_dir)


def test_run_raises_on_timeout(tmp_path):
    data_dir = tmp_path / "data"
    mgr = CombinerManager(data_dir=data_dir)
    _install_dummy_combiner(mgr)

    input_dir = tmp_path / "ei_output"
    input_dir.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with patch(
        "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=[], timeout=1)
    ):
        with pytest.raises(CombinerRunError, match="timed out"):
            mgr.run(input_dir=input_dir, run_dir=run_dir, timeout=1)


# ---------------------------------------------------------------------------
# ensure_installed
# ---------------------------------------------------------------------------


def test_ensure_installed_returns_existing_when_already_installed(tmp_path):
    mgr = CombinerManager(data_dir=tmp_path)
    zip_bytes = _make_test_zip()

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.headers = {"content-length": str(len(zip_bytes))}
    mock_resp.iter_content.return_value = [zip_bytes]

    with patch("core.combiner_manager.requests.get", return_value=mock_resp):
        mgr.download_and_install("https://example.com/test.zip", "2.0.0")

    # Should return existing without calling check_latest again
    entry = mgr.ensure_installed()
    assert entry is not None


def test_ensure_installed_downloads_when_none_installed(tmp_path):
    mgr = CombinerManager(data_dir=tmp_path)
    zip_bytes = _make_test_zip()

    release = _mock_github_release("v3.0.0", ["combiner.zip"])
    api_mock = MagicMock(spec=requests.Response)
    api_mock.status_code = 200
    api_mock.json.return_value = release

    dl_mock = MagicMock(spec=requests.Response)
    dl_mock.status_code = 200
    dl_mock.headers = {"content-length": str(len(zip_bytes))}
    dl_mock.iter_content.return_value = [zip_bytes]

    with patch(
        "core.combiner_manager.requests.get", side_effect=[api_mock, dl_mock]
    ):
        entry = mgr.ensure_installed()

    assert entry is not None
    assert mgr.installed_version() == "3.0.0"


def test_ensure_installed_raises_when_offline_and_nothing_installed(tmp_path):
    mgr = CombinerManager(data_dir=tmp_path)

    with patch(
        "core.combiner_manager.requests.get",
        side_effect=requests.ConnectionError("offline"),
    ):
        with pytest.raises(CombinerNotInstalled):
            mgr.ensure_installed()


# ---------------------------------------------------------------------------
# download_and_install HTTP errors
# ---------------------------------------------------------------------------


def test_download_and_install_raises_on_http_error(tmp_path):
    mgr = CombinerManager(data_dir=tmp_path)

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 404

    with patch("core.combiner_manager.requests.get", return_value=mock_resp):
        with pytest.raises(CombinerNotInstalled, match="HTTP 404"):
            mgr.download_and_install("https://example.com/bad.zip", "1.0.0")


def test_download_and_install_raises_when_no_entry_found(tmp_path):
    mgr = CombinerManager(data_dir=tmp_path)

    # Zip with no known entry point
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("README.txt", "nothing useful")
    buf.seek(0)
    zip_bytes = buf.getvalue()

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.headers = {"content-length": str(len(zip_bytes))}
    mock_resp.iter_content.return_value = [zip_bytes]

    with patch("core.combiner_manager.requests.get", return_value=mock_resp):
        with pytest.raises(CombinerNotInstalled, match="No entry point"):
            mgr.download_and_install("https://example.com/zip", "1.0.0")


# ---------------------------------------------------------------------------
# progress_callback
# ---------------------------------------------------------------------------


def test_download_progress_callback_invoked(tmp_path):
    mgr = CombinerManager(data_dir=tmp_path)
    zip_bytes = _make_test_zip()

    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.status_code = 200
    mock_resp.headers = {"content-length": str(len(zip_bytes))}
    chunk = zip_bytes[:100]
    mock_resp.iter_content.return_value = [chunk, zip_bytes[100:]]

    progress_values = []

    with patch("core.combiner_manager.requests.get", return_value=mock_resp):
        mgr.download_and_install(
            "https://example.com/test.zip",
            "1.0.0",
            progress_callback=lambda pct: progress_values.append(pct),
        )

    assert len(progress_values) >= 1
    assert progress_values[-1] > 0
