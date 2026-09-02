"""Network-drive classification + watcher self-test behaviour.

Classification mocks ctypes (kernel32.GetDriveTypeW); the probe tests mock
the watchdog Observer so no real filesystem events are required.
"""

import ctypes
import threading
import time
import types
from pathlib import Path

import pytest

from core import file_watcher as fw


class FakePath:
    """Stands in for a Windows-parsed Path on POSIX test hosts."""

    def __init__(self, drive: str = '', text: str = ''):
        self.drive = drive
        self._text = text

    def __str__(self):
        return self._text


# ------------------------------------------------------- ctypes mocking


def test_drive_type_calls_get_drive_type_w(monkeypatch):
    calls = []

    def fake_get_drive_type(root):
        calls.append(root)
        return fw.DRIVE_REMOTE

    kernel32 = types.SimpleNamespace(GetDriveTypeW=fake_get_drive_type)
    monkeypatch.setattr(ctypes, 'windll',
                        types.SimpleNamespace(kernel32=kernel32),
                        raising=False)

    assert fw._drive_type('Y:\\') == fw.DRIVE_REMOTE
    assert calls == ['Y:\\']


def test_drive_type_is_none_off_windows(monkeypatch):
    monkeypatch.delattr(ctypes, 'windll', raising=False)
    assert fw._drive_type('C:\\') is None


# --------------------------------------------------------- classification


@pytest.mark.parametrize('drive_type,expected', [
    (fw.DRIVE_REMOTE, True),
    (fw.DRIVE_FIXED, False),
    (fw.DRIVE_REMOVABLE, False),
    (fw.DRIVE_UNKNOWN, False),
    (fw.DRIVE_FIXED, False),   # 3 = a normal local hard drive, never network
    (fw.DRIVE_CDROM, False),
    (fw.DRIVE_RAMDISK, False),
    (None, False),             # non-Windows: behavior unchanged
])
def test_mapped_drive_classification(monkeypatch, drive_type, expected):
    monkeypatch.setattr(fw, '_drive_type', lambda root: drive_type)
    p = FakePath(drive='y:', text='Y:/games/arcdps/Logs')
    assert fw.check_remote_drive(p) is expected


def test_driveless_path_falls_back_to_unc_check(monkeypatch):
    monkeypatch.setattr(fw, '_drive_type', lambda root: fw.DRIVE_FIXED)
    unc = FakePath(drive='', text='\\\\NAS\\gw2\\logs')
    assert fw.check_remote_drive(unc) is True
    local = FakePath(drive='', text='/home/user/logs')
    assert fw.check_remote_drive(local) is False


def test_windows_drive_lowercased_and_rooted(monkeypatch):
    seen = []

    def spy(root):
        seen.append(root)
        return fw.DRIVE_REMOTE

    monkeypatch.setattr(fw, '_drive_type', spy)
    assert fw.check_remote_drive(FakePath(drive='z:', text='Z:/x')) is True
    assert seen == ['Z:\\']


# -------------------------------------------- heuristic false-positives


def test_folder_named_network_or_smb_is_not_network():
    assert fw.is_network_path(Path('/data/network share/gw2')) is False
    assert fw.is_network_path(Path('/mnt/smb-arcade/saves')) is False
    assert fw.is_network_path(Path('/home/user/SMB log collection')) is False


def test_unc_prefix_still_detected():
    assert fw.is_network_path(Path('\\\\NAS\\logs')) is True
    assert fw.is_network_path(Path('C:/gw2/logs')) is False


# ------------------------------------------------------ self-test paths


class FakeEvent:
    def __init__(self, src_path):
        self.src_path = str(src_path)
        self.is_directory = False


class FakeObserver:
    """Records schedules. mode='fire' dispatches a creation event when the
    probe file appears; 'silent' never fires; 'first' fires only for the
    first-scheduled observer (mixed-result folders)."""

    instances = []
    mode = 'fire'

    def __init__(self):
        self.handlers = []
        self.started = False
        self.stopped = False
        FakeObserver.instances.append(self)

    def schedule(self, handler, path, recursive=False):
        self.handlers.append((handler, path))

    def start(self):
        self.started = True
        first = len(FakeObserver.instances) == 1
        if (FakeObserver.mode == 'fire'
                or (FakeObserver.mode == 'first' and first)):
            threading.Thread(target=self._emit_for_probe, daemon=True).start()

    def _emit_for_probe(self):
        folder = Path(self.handlers[0][1])
        deadline = time.time() + 3.0
        while time.time() < deadline:
            for p in folder.rglob('*'):
                if p.is_file() and p.name.startswith(fw.NATIVE_PROBE_PREFIX):
                    # watchdog's dispatcher calls on_any_event for every
                    # event, then routes to the specific on_* hooks.
                    handler = self.handlers[0][0]
                    handler.on_any_event(FakeEvent(p))
                    handler.on_created(FakeEvent(p))
                    return
            time.sleep(0.02)

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        pass


class FakePolling:
    instances = []

    def __init__(self, config, callback, poll_interval=5.0):
        self.callback = callback
        FakePolling.instances.append(self)

    def start(self, initial_files=None, initial_directory_mtimes=None):
        self.started_with = initial_files
        self.started_with_directory_mtimes = initial_directory_mtimes

    def stop(self):
        self.stopped = True


class FakeConfig:
    def __init__(self, folders):
        self._folders = folders

    def get_log_folders(self):
        return list(self._folders)


@pytest.fixture
def clean_fakes(monkeypatch):
    FakeObserver.instances = []
    FakePolling.instances = []
    FakeObserver.mode = 'fire'
    monkeypatch.setattr(fw, 'Observer', FakeObserver)
    monkeypatch.setattr(fw, 'PollingFileWatcher', FakePolling)
    # The temp folders are local; force the fast-path checks to agree
    # so only the self-test decides the outcome.
    monkeypatch.setattr(fw, 'is_network_path', lambda p: False)
    monkeypatch.setattr(fw, 'check_remote_drive', lambda p: False)


@pytest.fixture
def log_folder(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    return folder


def _make(folder):
    return fw.FileWatcher(FakeConfig([folder]), lambda p: None,
                          poll_interval=0.1)


def test_selftest_success_keeps_native_watching(clean_fakes, log_folder):
    w = _make(log_folder)
    w.start(selftest=True, probe_timeout=1.0)
    try:
        assert w._use_polling is False
        assert w.status_note is None
        assert w._observer.started is True
        assert FakePolling.instances == []
    finally:
        w.stop()


def test_selftest_silence_falls_back_to_polling(clean_fakes, log_folder):
    FakeObserver.mode = 'silent'
    w = _make(log_folder)
    w.start(selftest=True, probe_timeout=0.2)
    try:
        assert w._use_polling is True
        assert w.status_note == fw.COMPAT_STATUS_NOTE
        assert FakePolling.instances
        assert FakePolling.instances[0].started_with == set()
        assert not [p for p in log_folder.rglob('*')
                    if p.name.startswith(fw.NATIVE_PROBE_PREFIX)]
    finally:
        w.stop()


def test_auto_start_does_not_selftest(clean_fakes, log_folder):
    FakeObserver.mode = 'silent'
    w = _make(log_folder)
    w.start()
    try:
        # Even with a silent observer, the automatic path never probes.
        assert w._use_polling is False
        assert FakePolling.instances == []
        assert w.status_note is None
    finally:
        w.stop()


def test_probe_true_when_events_arrive(clean_fakes, log_folder):
    w = _make(log_folder)
    assert w._probe_native_events(log_folder, timeout=1.0) is True
    assert not list(log_folder.rglob('*'))  # probe file cleaned up


def test_probe_false_on_silence_and_cleans_up(clean_fakes, log_folder):
    FakeObserver.mode = 'silent'
    w = _make(log_folder)
    assert w._probe_native_events(log_folder, timeout=0.2) is False
    assert not list(log_folder.rglob('*'))


def test_one_silent_folder_triggers_fallback(clean_fakes, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    FakeObserver.mode = 'first'
    w = fw.FileWatcher(FakeConfig([a, b]), lambda p: None,
                       poll_interval=0.1)
    w.start(selftest=True, probe_timeout=0.3)
    try:
        assert w._use_polling is True
        assert w.status_note == fw.COMPAT_STATUS_NOTE
    finally:
        w.stop()


def test_winbase_numeric_truth():
    """winbase.h: DRIVE_FIXED == 3, DRIVE_REMOTE == 4. A flipped mapping
    classifies every local disk as a network share — pin the numbers."""
    import core.file_watcher as fw
    assert fw.DRIVE_FIXED == 3
    assert fw.DRIVE_REMOTE == 4
    assert fw.DRIVE_CDROM == 5
