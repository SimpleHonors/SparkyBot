"""ArcDPS writes a combat log under a temporary/extensionless name and then
renames it to the final .evtc/.zevtc. The extension only appears on the
rename, which watchdog delivers as a move event -- so the OS-native handler
must react to on_moved, not just on_created, or it never sees a log at all.

Regression guard for the "file watcher of zevtc is not working" report:
the created event carried '...\\20260618-213011' (no suffix), the suffix
check bailed, and only the subsequent rename revealed the real extension.
"""
import sys
import threading
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))


try:
    from watchdog.events import FileCreatedEvent, FileMovedEvent
except ModuleNotFoundError:
    watchdog = types.ModuleType("watchdog")
    observers = types.ModuleType("watchdog.observers")
    events = types.ModuleType("watchdog.events")

    class Observer:
        pass

    class FileSystemEventHandler:
        pass

    class FileCreatedEvent:
        def __init__(self, src_path):
            self.src_path = src_path
            self.is_directory = False

    class FileMovedEvent:
        def __init__(self, src_path, dest_path):
            self.src_path = src_path
            self.dest_path = dest_path
            self.is_directory = False

    observers.Observer = Observer
    events.FileSystemEventHandler = FileSystemEventHandler
    events.FileCreatedEvent = FileCreatedEvent
    events.FileMovedEvent = FileMovedEvent
    sys.modules["watchdog"] = watchdog
    sys.modules["watchdog.observers"] = observers
    sys.modules["watchdog.events"] = events

from core.file_watcher import LogFileHandler


def _make_handler():
    """Return (handler, seen_list, event) wired to record dispatched paths."""
    seen: list[Path] = []
    got = threading.Event()

    def callback(path: Path):
        seen.append(path)
        got.set()

    return LogFileHandler(callback), seen, got


def test_on_moved_dispatches_when_rename_reveals_extension(tmp_path):
    """The rename target ends in .zevtc -> the log must be dispatched."""
    placeholder = tmp_path / "20260618-213011"          # extensionless, as arcdps creates it
    final = tmp_path / "20260618-213011.zevtc"          # real name after rename
    final.write_bytes(b"combat log payload")            # non-empty + stable on disk

    handler, seen, got = _make_handler()
    try:
        handler.on_moved(FileMovedEvent(str(placeholder), str(final)))
        assert got.wait(timeout=15), "on_moved never dispatched the renamed .zevtc file"
        assert seen == [final]
    finally:
        handler.stop()


def test_on_created_with_extensionless_placeholder_is_ignored(tmp_path):
    """The bug: created fires for the extensionless placeholder; we must not
    dispatch it (and must not crash on the empty suffix)."""
    placeholder = tmp_path / "20260618-213011"
    placeholder.write_bytes(b"partial")

    handler, seen, got = _make_handler()
    try:
        handler.on_created(FileCreatedEvent(str(placeholder)))
        assert not got.wait(timeout=1.0)
        assert seen == []
    finally:
        handler.stop()


def test_on_moved_ignores_non_log_extensions(tmp_path):
    """A rename whose destination isn't .evtc/.zevtc (e.g. arcdps's own .tmp
    scratch) must be filtered out."""
    src = tmp_path / "scratch"
    dest = tmp_path / "scratch.tmp"
    dest.write_bytes(b"x")

    handler, seen, got = _make_handler()
    try:
        handler.on_moved(FileMovedEvent(str(src), str(dest)))
        assert not got.wait(timeout=1.0)
        assert seen == []
    finally:
        handler.stop()
