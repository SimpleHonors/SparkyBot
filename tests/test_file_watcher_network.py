"""Network-drive classification + watcher self-test behaviour.

Classification mocks ctypes (kernel32.GetDriveTypeW); the probe tests mock
the watchdog Observer so no real filesystem events are required.
"""

import ctypes
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
    (fw.DRIVE_LOCAL, False),
    (fw.DRIVE_REMOVABLE, False),
    (fw.DRIVE_UNKNOWN, False),
    (fw.DRIVE_CDROM, False),   # 4 is CDROM, NOT remote — the ticket trap
    (fw.DRIVE_RAMDISK, False),
    (None, False),             # non-Windows: behavior unchanged
])
def test_mapped_drive_classification(monkeypatch, drive_type, expected):
    monkeypatch.setattr(fw, '_drive_type', lambda root: drive_type)
    p = FakePath(drive='y:', text='Y:/games/arcdps/Logs')
    assert fw.check_remote_drive(p) is expected


def test_driveless_path_falls_back_to_unc_check(monkeypatch):
    monkeypatch.setattr(fw, '_drive_type', lambda root: fw.DRIVE_LOCAL)
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
