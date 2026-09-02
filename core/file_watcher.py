"""File watcher using watchdog for efficient OS-native file monitoring
   Falls back to polling for network shares since OS events don't work over SMB
"""

import ctypes
import os
import time
import logging
import threading
from pathlib import Path
from typing import Set, Callable, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileMovedEvent

logger = logging.getLogger(__name__)

# Watcher self-test (user-initiated starts only): after writing a temp
# probe file into a folder the observer must see an event within this
# window, otherwise OS events are not arriving (e.g. a network folder the
# drive-type API reports as LOCAL) and we fall back to compatibility
# polling.
NATIVE_WATCH_PROBE_TIMEOUT = 2.0
NATIVE_PROBE_PREFIX = "SparkyBot-watch-probe"
COMPAT_STATUS_NOTE = ("Network folder detected — using compatibility "
                      "watching")

_LOG_EXTENSIONS = ('.evtc', '.zevtc')


def _scan_log_folder(folder: Path) -> tuple[Set[str], dict[str, int]]:
    """Return combat-log paths and directory mtimes in one scandir walk.

    ``Path.rglob`` plus ``Path.is_file`` turns a network-folder scan into a
    metadata round trip for every historical log.  ``os.scandir`` reuses the
    directory metadata returned by SMB and lets the polling watcher remember
    directory mtimes, so unchanged folders need only a cheap directory stat.
    """
    files: Set[str] = set()
    directory_mtimes: dict[str, int] = {}
    pending = [Path(folder)]

    while pending:
        current = pending.pop()
        try:
            mtime_before = current.stat().st_mtime_ns
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.name.lower().endswith(_LOG_EXTENSIONS):
                            files.add(str(Path(entry.path)))
                    except OSError:
                        logger.debug("Skipping unreadable path: %s", entry.path)
            mtime_after = current.stat().st_mtime_ns
            # If the directory changed while its entries were being read,
            # retain the old signature so the next poll immediately rescans.
            directory_mtimes[str(current)] = (
                mtime_before if mtime_before != mtime_after else mtime_after
            )
        except OSError:
            logger.debug("Skipping unreadable log folder: %s", current)

    return files, directory_mtimes


def is_network_path(path: Path) -> bool:
    """Check if a path is a network share (UNC path).

    Only the real UNC prefix counts. The old 'network'/'smb' substring
    heuristic false-positived on ordinary folder names and is gone.
    """
    return str(path).startswith('\\\\')


# GetDriveTypeW return values (winbase.h): 0 UNKNOWN, 1 NO_ROOT_DIR,
# 2 REMOVABLE, 3 FIXED, 4 REMOTE (mapped network drive), 5 CDROM, 6 RAMDISK.
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6


def _drive_type(drive_root: str) -> Optional[int]:
    """Return kernel32.GetDriveTypeW(drive_root), or None off-Windows.

    ``drive_root`` must be the 'X:\\' form. Seam for tests to mock ctypes.
    No argtypes/restype fiddling needed: ctypes maps a Python str to
    LPCWSTR and the default restype (c_int) compares fine against the
    DRIVE_* constants.
    """
    try:
        kernel32 = ctypes.windll.kernel32
        return kernel32.GetDriveTypeW(drive_root)
    except AttributeError:
        return None  # non-Windows: no windll / no kernel32


def check_remote_drive(path: Path) -> bool:
    """Check if a path is on a mapped network drive (Windows API).

    Asks kernel32.GetDriveTypeW directly instead of shelling out to
    PowerShell (no process spawn, no console flash, no 10s timeout).
    Off-Windows and drive-less paths keep the UNC-prefix-only behavior.
    """
    try:
        drive = str(path.drive).upper()
        if drive and _drive_type(drive + '\\') == DRIVE_REMOTE:
            return True
    except Exception:
        pass

    return is_network_path(path)


class LogFileHandler(FileSystemEventHandler):
    """Handles file system events for GW2 log files"""

    def __init__(self, callback: Callable[[Path], None], extensions: tuple = ('.evtc', '.zevtc')):
        super().__init__()
        self.callback = callback
        self.extensions = extensions
        # Guards all access to _processed_files and _processing_files
        self._lock = threading.Lock()
        # Files that have been fully processed
        self._processed_files: Set[str] = set()
        # Files currently being stability-checked by a background thread
        self._processing_files: Set[str] = set()
        # Background threads for active stability checks (for join on stop)
        self._active_threads: list[threading.Thread] = []
        self._stopping = False

    def on_created(self, event: FileCreatedEvent):
        """Called when a new file is created (arcdps versions that write the
        final .evtc/.zevtc name directly)."""
        if event.is_directory:
            return
        self._maybe_dispatch(Path(event.src_path))

    def on_moved(self, event: FileMovedEvent):
        """Called when a file is renamed into place.

        ArcDPS writes the log under a temporary/extensionless name (e.g.
        '20260618-213011') and then renames it to the final '.evtc'/'.zevtc'.
        The extension only appears on the rename's destination, which watchdog
        delivers as a move event -- so without this handler the OS-native
        watcher never sees a file whose suffix matches. Dispatch on the
        destination path; the shared dedup set keeps this from double-firing
        with on_created when both events carry the final name.
        """
        if event.is_directory:
            return
        self._maybe_dispatch(Path(event.dest_path))

    def _maybe_dispatch(self, file_path: Path):
        """Filter by extension, reserve the path, and kick off a stability check."""
        # Only process our log file types
        if file_path.suffix.lower() not in self.extensions:
            return

        path_str = str(file_path)

        # Atomically check-and-reserve this file to prevent duplicate processing
        with self._lock:
            if path_str in self._processed_files or path_str in self._processing_files:
                return
            self._processing_files.add(path_str)

        logger.info(f"New file detected: {file_path.name}")

        # Check file stability on a background thread so watchdog can keep processing events
        t = threading.Thread(daemon=True)
        t.run = lambda: self._check_and_dispatch(file_path, path_str, t)
        with self._lock:
            self._active_threads.append(t)
        t.start()

    def _check_and_dispatch(self, file_path: Path, path_str: str, thread: threading.Thread):
        """Check file stability on a background thread then dispatch to callback"""
        try:
            if self._wait_for_file_stable(file_path):
                with self._lock:
                    self._processed_files.add(path_str)
                self.callback(file_path)
            else:
                logger.warning(f"File became unstable during wait: {file_path.name}")
        finally:
            with self._lock:
                self._processing_files.discard(path_str)
                if thread in self._active_threads:
                    self._active_threads.remove(thread)

    def _wait_for_file_stable(self, file_path: Path, timeout: float = 100.0, interval: float = 0.5, stable_count: int = 3) -> bool:
        """Wait for file to stop changing (finished writing)"""
        if not file_path.exists():
            return False

        try:
            consecutive_stable = 0
            last_size = -1
            elapsed = 0.0

            while elapsed < timeout:
                with self._lock:
                    if self._stopping:
                        return False

                if not file_path.exists():
                    logger.info(f"File was removed: {file_path.name}")
                    return False

                current_size = file_path.stat().st_size

                if current_size == last_size and current_size > 0:
                    consecutive_stable += 1
                    if consecutive_stable >= stable_count:
                        return True
                else:
                    consecutive_stable = 0

                last_size = current_size
                time.sleep(interval)
                elapsed += interval

            logger.warning(f"File still changing after {timeout}s: {file_path.name}")
            return False

        except OSError as e:
            logger.error(f"Error waiting for file stability: {e}")
            return False

    def stop(self):
        """Stop all background threads"""
        with self._lock:
            self._stopping = True

        # Join all active threads
        threads = []
        with self._lock:
            threads = list(self._active_threads)

        for t in threads:
            t.join(timeout=5)


class PollingFileWatcher:
    """Fallback file watcher using polling - works with network shares"""

    FULL_RESCAN_INTERVAL = 60.0

    def __init__(self, config, on_new_file: Callable[[Path], None],
                 poll_interval: float = 5.0,
                 full_rescan_interval: float = FULL_RESCAN_INTERVAL):
        self.config = config
        self.on_new_file = on_new_file
        self.poll_interval = poll_interval
        self.full_rescan_interval = full_rescan_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # Files that existed when the watcher started (never evicted - prevents reprocessing)
        self._initial_files: Set[str] = set()
        # All files seen during this watcher process.  A set of paths is far
        # cheaper than periodically issuing one network exists() call per log.
        self._seen_files: Set[str] = set()
        # Files noticed while ArcDPS is still writing them.  These are checked
        # on every poll without walking the history again.
        self._pending_files: Set[str] = set()
        self._directory_mtimes: dict[str, int] = {}
        self._last_full_scan = 0.0

    def _scan_all_folders(self) -> tuple[Set[str], dict[str, int]]:
        files: Set[str] = set()
        directory_mtimes: dict[str, int] = {}
        for folder in self.config.get_log_folders():
            if not folder.exists():
                continue
            found, mtimes = _scan_log_folder(folder)
            files.update(found)
            directory_mtimes.update(mtimes)
        return files, directory_mtimes

    def _scan_existing_files(self):
        """Scan for existing files to build initial state"""
        self._initial_files.clear()
        self._seen_files.clear()
        self._pending_files.clear()
        files, self._directory_mtimes = self._scan_all_folders()
        self._initial_files.update(files)
        self._seen_files.update(files)
        self._last_full_scan = time.monotonic()

    def start(self, initial_files: Optional[Set[str]] = None,
              initial_directory_mtimes: Optional[dict[str, int]] = None):
        """Start polling for new files

        Args:
            initial_files: Pre-scanned set of existing file paths to skip.
                          If None, scans the folders itself.
        """
        if initial_files is None:
            self._scan_existing_files()
        else:
            self._initial_files = initial_files.copy()
            self._seen_files = initial_files.copy()
            if initial_directory_mtimes is None:
                _, self._directory_mtimes = self._scan_all_folders()
            else:
                self._directory_mtimes = initial_directory_mtimes.copy()
            self._last_full_scan = time.monotonic()
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info(f"Polling watcher started (interval: {self.poll_interval}s)")

    def _poll_loop(self):
        """Main polling loop"""
        while self._running:
            try:
                self._check_for_new_files()
            except Exception as e:
                logger.error(f"Error in polling loop: {e}")

            self._stop_event.wait(self.poll_interval)

    def _directories_changed(self) -> bool:
        """Cheap poll: one stat per known directory, not per historical log."""
        configured = {
            str(folder) for folder in self.config.get_log_folders()
            if folder.exists()
        }
        known_roots = {
            str(folder) for folder in self.config.get_log_folders()
            if str(folder) in self._directory_mtimes
        }
        if configured != known_roots:
            return True

        for path_str, old_mtime in self._directory_mtimes.items():
            try:
                if Path(path_str).stat().st_mtime_ns != old_mtime:
                    return True
            except OSError:
                return True
        return False

    def _check_for_new_files(self):
        """Check folders for new files"""
        now = time.monotonic()
        rescan_due = now - self._last_full_scan >= self.full_rescan_interval
        if self._directories_changed() or rescan_due:
            files, mtimes = self._scan_all_folders()
            self._directory_mtimes = mtimes
            self._last_full_scan = now
            for path_str in sorted(files - self._seen_files):
                if path_str in self._initial_files:
                    self._seen_files.add(path_str)
                    continue
                self._pending_files.add(path_str)

        self._check_pending_files()

    def _check_pending_files(self):
        for path_str in tuple(self._pending_files):
            file_path = Path(path_str)
            if not file_path.exists():
                self._pending_files.discard(path_str)
                continue
            if not self._is_file_stable(file_path):
                continue

            logger.info(f"New file detected (polling): {file_path.name}")
            self._pending_files.discard(path_str)
            self._seen_files.add(path_str)
            self.on_new_file(file_path)

    def _is_file_stable(self, file_path: Path, check_count: int = 3) -> bool:
        """Check if file is stable (size/mtime not changing)"""
        try:
            last_size = -1
            last_mtime = -1

            for i in range(check_count):
                if not file_path.exists():
                    return False

                stat_result = file_path.stat()
                size = stat_result.st_size
                mtime = stat_result.st_mtime

                if last_size != -1 and (size != last_size or mtime != last_mtime):
                    # File is still changing
                    return False

                last_size = size
                last_mtime = mtime
                # Only sleep between checks, not after the last one
                if i < check_count - 1:
                    time.sleep(0.5)

            return True

        except OSError:
            return False

    def stop(self):
        """Stop polling"""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                logger.warning(f"Polling thread still alive {self._thread.name} after join timeout")
        logger.info("Polling watcher stopped")

    def is_running(self) -> bool:
        return self._running


class FileWatcher:
    """File watcher with automatic detection of network shares
       Uses OS-native events for local, polling for network
    """

    def __init__(self, config, on_new_file: Callable[[Path], None], poll_interval: float = 5.0):
        self.config = config
        self.on_new_file = on_new_file
        self.poll_interval = poll_interval
        self._observer: Optional[Observer] = None
        self._event_handler: Optional[LogFileHandler] = None
        self._polling_watcher: Optional[PollingFileWatcher] = None
        self._initial_files: Set[str] = set()
        self._initial_directory_mtimes: dict[str, int] = {}
        self._running = False
        self._use_polling = False
        self._is_network: Optional[bool] = None  # Instance-level cache
        # Set when a self-test downgraded us to compatibility polling, so the
        # UI can surface a plain status note. None otherwise.
        self.status_note: Optional[str] = None

    def _scan_existing_files(self):
        """Scan for existing files to skip them initially"""
        self._initial_files.clear()
        self._initial_directory_mtimes.clear()
        for folder in self.config.get_log_folders():
            if folder.exists():
                files, mtimes = _scan_log_folder(folder)
                self._initial_files.update(files)
                self._initial_directory_mtimes.update(mtimes)

    def _is_network_share(self) -> bool:
        """Check if any log folder is on a network share (cached per instance)"""
        if self._is_network is not None:
            return self._is_network

        for folder in self.config.get_log_folders():
            if folder.exists():
                if is_network_path(folder) or check_remote_drive(folder):
                    self._is_network = True
                    return True

        self._is_network = False
        return False

    def _probe_native_events(self, folder: Path,
                             timeout: float = NATIVE_WATCH_PROBE_TIMEOUT) -> bool:
        """Return True if the OS really delivers events for this folder.

        Writes a temp probe file and waits ``timeout`` seconds for a
        watchdog event on a throwaway observer. Used only by the
        user-initiated start path.
        """
        fired = threading.Event()

        class _Probe(FileSystemEventHandler):
            def on_any_event(self, event):
                if not event.is_directory:
                    fired.set()

        probe = folder / f"{NATIVE_PROBE_PREFIX}-" \
                        f"{int(time.time() * 1000)}.tmp"
        observer = Observer()
        try:
            observer.schedule(_Probe(), str(folder), recursive=True)
            observer.start()
            try:
                probe.write_bytes(b"")
                return fired.wait(timeout)
            finally:
                observer.stop()
                observer.join(timeout)
        except Exception as e:
            logger.debug(f"Native watch probe error in {folder}: {e}")
            return False
        finally:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass

    def _start_polling(self):
        """Start the compatibility polling watcher (network-safe path)."""
        logger.info("Network share detected - using polling watcher")
        self._use_polling = True
        self._polling_watcher = PollingFileWatcher(
            self.config,
            self._on_new_file,
            self.poll_interval
        )
        self._polling_watcher.start(
            self._initial_files,
            self._initial_directory_mtimes,
        )

    def _start_native(self):
        """Start the OS-native event watcher."""
        logger.info("Local folder detected - using OS-native events")
        self._use_polling = False
        self._event_handler = LogFileHandler(self._on_new_file)
        self._observer = Observer()

        for folder in self.config.get_log_folders():
            if folder.exists():
                logger.info(f"Watching folder: {folder}")
                self._observer.schedule(self._event_handler, str(folder), recursive=True)
            else:
                logger.warning(f"Folder does not exist, skipping: {folder}")

        self._observer.start()

    def start(self, selftest: bool = False,
              probe_timeout: float = NATIVE_WATCH_PROBE_TIMEOUT):
        """Start watching for new log files.

        Args:
            selftest: verify the OS event watcher actually fires before
                trusting it, falling back to compatibility polling on
                silence. Only pass True from user-initiated flows (start
                button / tray toggle); automatic launches keep the plain
                startup behavior.
        """
        self._scan_existing_files()

        # Check if we need polling for network shares
        if self._is_network_share():
            self._start_polling()
        else:
            if selftest:
                folders = [f for f in self.config.get_log_folders()
                           if f.exists()]
                silent = [f for f in folders
                          if not self._probe_native_events(f, probe_timeout)]
                if folders and silent:
                    logger.info(COMPAT_STATUS_NOTE)
                    self.status_note = COMPAT_STATUS_NOTE
                    self._start_polling()
                else:
                    self._start_native()
            else:
                self._start_native()

        self._running = True
        logger.info("File watcher started")

    def _on_new_file(self, file_path: Path):
        """Handle new file event"""
        # Skip if this was an existing file
        if str(file_path) in self._initial_files:
            logger.debug(f"Skipping existing file: {file_path.name}")
            return

        self.on_new_file(file_path)

    def stop(self):
        """Stop watching"""
        self._running = False
        if self._observer:
            self._observer.stop()
            self._observer.join()
        if self._event_handler:
            self._event_handler.stop()
            self._event_handler = None
        if self._polling_watcher:
            self._polling_watcher.stop()
        logger.info("File watcher stopped")

    def is_running(self) -> bool:
        """Check if watcher is running"""
        return self._running

    def run_until_stopped(self):
        """Run the watcher blocking until stopped"""
        self.start()
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass  # Let main.py's handler take care of stopping
