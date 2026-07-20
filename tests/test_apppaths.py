"""Unit tests for core/apppaths — dev and frozen modes."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))


# ---------------------------------------------------------------------------
# dev mode (no sys.frozen)
# ---------------------------------------------------------------------------

def test_dev_app_dir_is_repo_root():
    # Force-reload to get clean state after any prior monkeypatching
    import core.apppaths
    import importlib
    importlib.reload(core.apppaths)

    from core.apppaths import app_dir
    root = app_dir()
    assert (root / "core" / "apppaths.py").exists()
    assert (root / "bootstrap.py").exists()
    assert "site-packages" not in str(root)


def test_dev_bundle_dir_equals_app_dir():
    import core.apppaths
    import importlib
    importlib.reload(core.apppaths)
    from core.apppaths import app_dir, bundle_dir
    assert bundle_dir() == app_dir()


def test_dev_is_frozen_false():
    import core.apppaths
    import importlib
    importlib.reload(core.apppaths)
    from core.apppaths import is_frozen
    assert is_frozen() is False


# ---------------------------------------------------------------------------
# local_machine_dir
# ---------------------------------------------------------------------------

def test_local_machine_dir_uses_localappdata_when_set(tmp_path, monkeypatch):
    import core.apppaths
    import importlib
    local = tmp_path / "AppData" / "Local"
    local.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    importlib.reload(core.apppaths)
    from core.apppaths import local_machine_dir
    assert local_machine_dir() == local / "SparkyBot"


def test_local_machine_dir_falls_back_to_home_dir(tmp_path, monkeypatch):
    import core.apppaths
    import importlib
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    importlib.reload(core.apppaths)
    from core.apppaths import local_machine_dir
    assert local_machine_dir() == tmp_path / ".sparkybot"


def test_local_machine_dir_falls_back_to_expanduser(tmp_path, monkeypatch):
    import core.apppaths
    import importlib
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    with patch("os.path.expanduser", return_value=str(tmp_path)):
        importlib.reload(core.apppaths)
        from core.apppaths import local_machine_dir
        assert local_machine_dir() == tmp_path / ".sparkybot"


def test_local_machine_dir_precedence(tmp_path, monkeypatch):
    import core.apppaths
    import importlib
    local = tmp_path / "AppData" / "Local"
    local.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("HOME", str(tmp_path))
    importlib.reload(core.apppaths)
    from core.apppaths import local_machine_dir
    # LOCALAPPDATA wins over HOME
    assert local_machine_dir() == local / "SparkyBot"


# ---------------------------------------------------------------------------
# frozen mode simulation
# ---------------------------------------------------------------------------

def test_frozen_app_dir_is_exe_parent(tmp_path):
    import core.apppaths
    import importlib
    exe_dir = tmp_path / "dist" / "SparkyBot"
    exe_dir.mkdir(parents=True)
    exe = exe_dir / "SparkyBot.exe"
    exe.write_text("fake")

    with patch.object(core.apppaths.sys, "frozen", True, create=True), \
         patch.object(core.apppaths.sys, "executable", str(exe)):
        importlib.reload(core.apppaths)
        from core.apppaths import app_dir
        assert app_dir() == exe_dir


def test_frozen_bundle_dir_is_meipass(tmp_path):
    import core.apppaths
    import importlib
    exe_dir = tmp_path / "dist" / "SparkyBot"
    exe_dir.mkdir(parents=True)
    meipass = tmp_path / "_internal"
    meipass.mkdir()

    with patch.object(core.apppaths.sys, "frozen", True, create=True), \
         patch.object(core.apppaths.sys, "executable", str(exe_dir / "SparkyBot.exe")), \
         patch.object(core.apppaths.sys, "_MEIPASS", str(meipass), create=True):
        importlib.reload(core.apppaths)
        from core.apppaths import bundle_dir
        assert bundle_dir() == meipass


def test_frozen_gw2ei_dir_prefers_app_dir_copy(tmp_path):
    import core.apppaths
    import importlib
    exe_dir = tmp_path / "dist" / "SparkyBot"
    exe_dir.mkdir(parents=True)
    (exe_dir / "GW2EI").mkdir()
    (exe_dir / "SparkyBot.exe").write_text("fake")

    meipass = tmp_path / "_internal"
    meipass.mkdir()
    (meipass / "GW2EI").mkdir()

    with patch.object(core.apppaths.sys, "frozen", True, create=True), \
         patch.object(core.apppaths.sys, "executable", str(exe_dir / "SparkyBot.exe")), \
         patch.object(core.apppaths.sys, "_MEIPASS", str(meipass), create=True):
        importlib.reload(core.apppaths)
        from core.apppaths import gw2ei_dir
        assert gw2ei_dir() == exe_dir / "GW2EI"


def test_frozen_gw2ei_dir_falls_back_to_bundle(tmp_path):
    import core.apppaths
    import importlib
    exe_dir = tmp_path / "dist" / "SparkyBot"
    exe_dir.mkdir(parents=True)
    (exe_dir / "SparkyBot.exe").write_text("fake")
    # No GW2EI next to exe

    meipass = tmp_path / "_internal"
    meipass.mkdir()
    (meipass / "GW2EI").mkdir()

    with patch.object(core.apppaths.sys, "frozen", True, create=True), \
         patch.object(core.apppaths.sys, "executable", str(exe_dir / "SparkyBot.exe")), \
         patch.object(core.apppaths.sys, "_MEIPASS", str(meipass), create=True):
        importlib.reload(core.apppaths)
        from core.apppaths import gw2ei_dir
        assert gw2ei_dir() == meipass / "GW2EI"


# ---------------------------------------------------------------------------
# call-site integration: state files resolve under app_dir
# ---------------------------------------------------------------------------

def test_config_config_path_uses_app_dir(tmp_path):
    import importlib

    with patch("core.apppaths.app_dir", return_value=tmp_path):
        import core.config
        importlib.reload(core.config)
        cfg = core.config.Config()
        assert cfg.home_dir == tmp_path


def test_vocabulary_tracker_path_uses_app_dir(tmp_path):
    import importlib

    (tmp_path / "sparkybot_vocabulary.json").write_text("{}")

    with patch("core.apppaths.app_dir", return_value=tmp_path):
        import core.vocabulary_config
        import core.vocabulary_tracker
        importlib.reload(core.vocabulary_config)
        importlib.reload(core.vocabulary_tracker)
        vc = core.vocabulary_config.VocabularyConfig()
        vt = core.vocabulary_tracker.VocabularyTracker(vocab_config=vc)
        assert vt.store_path.parent == tmp_path


def test_session_history_path_uses_app_dir(tmp_path):
    import importlib

    with patch("core.apppaths.app_dir", return_value=tmp_path):
        import core.session_history
        importlib.reload(core.session_history)
        sht = core.session_history.SessionHistoryTracker()
        assert sht.store_path.parent == tmp_path
