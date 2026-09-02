import os
from pathlib import Path

from core.file_watcher import PollingFileWatcher
from core.raid_session import discover_logs


class _Config:
    def __init__(self, folder):
        self.folder = Path(folder)

    def get_log_folders(self):
        return [self.folder]


def test_discover_logs_walks_once_and_carries_sizes(tmp_path, monkeypatch):
    nested = tmp_path / "nested"
    nested.mkdir()
    first = tmp_path / "20260901-190001.zevtc"
    second = nested / "20260901-190002.evtc"
    first.write_bytes(b"first")
    second.write_bytes(b"second-file")
    (tmp_path / "ignore.txt").write_text("not a log")

    # Timestamped ArcDPS names must not trigger a separate Path.stat round
    # trip; scandir already has the metadata needed for the size column.
    monkeypatch.setattr(
        Path, "stat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("per-file Path.stat used")),
    )
    logs = discover_logs(tmp_path, include_size=True)

    assert [log.path for log in logs] == [first, second]
    assert [log.size_bytes for log in logs] == [5, 11]


def test_network_watcher_does_not_rescan_unchanged_history(tmp_path,
                                                            monkeypatch):
    existing = tmp_path / "20260901-190001.zevtc"
    existing.write_bytes(b"existing")
    delivered = []
    watcher = PollingFileWatcher(
        _Config(tmp_path), delivered.append,
        poll_interval=5, full_rescan_interval=60,
    )
    watcher._scan_existing_files()

    scans = 0
    real_scan = watcher._scan_all_folders

    def counted_scan():
        nonlocal scans
        scans += 1
        return real_scan()

    monkeypatch.setattr(watcher, "_scan_all_folders", counted_scan)
    monkeypatch.setattr(watcher, "_is_file_stable", lambda _path: True)

    watcher._check_for_new_files()
    assert scans == 0
    assert delivered == []

    new_log = tmp_path / "20260901-190002.zevtc"
    new_log.write_bytes(b"new")
    # Make the mutation deterministic on filesystems with coarse mtimes.
    previous = watcher._directory_mtimes[str(tmp_path)]
    os.utime(tmp_path, ns=(previous + 2_000_000_000,
                           previous + 2_000_000_000))

    watcher._check_for_new_files()
    assert scans == 1
    assert delivered == [new_log]

    watcher._check_for_new_files()
    assert scans == 1
    assert delivered == [new_log]


def test_network_watcher_retries_unstable_file_without_history_rescan(
        tmp_path, monkeypatch):
    watcher = PollingFileWatcher(
        _Config(tmp_path), lambda path: delivered.append(path),
        full_rescan_interval=60,
    )
    watcher._scan_existing_files()
    delivered = []
    new_log = tmp_path / "20260901-190003.zevtc"
    new_log.write_bytes(b"writing")
    previous = watcher._directory_mtimes[str(tmp_path)]
    os.utime(tmp_path, ns=(previous + 2_000_000_000,
                           previous + 2_000_000_000))

    stable = iter((False, True))
    monkeypatch.setattr(watcher, "_is_file_stable", lambda _path: next(stable))
    watcher._check_for_new_files()
    assert delivered == []
    assert str(new_log) in watcher._pending_files

    monkeypatch.setattr(
        watcher, "_scan_all_folders",
        lambda: (_ for _ in ()).throw(AssertionError("history rescanned")),
    )
    watcher._check_for_new_files()
    assert delivered == [new_log]
    assert watcher._pending_files == set()
