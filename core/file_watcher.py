"""File watcher using watchdog for efficient OS-native file monitoring
   Falls back to polling for network shares since OS events don't work over SMB
"""

import subprocess
import time
import logging
import threading
from pathlib import Path
from typing import Set, Callable, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent

logger = logging.getLogger(__name__)


def is_network_path(path: Path) -> bool:
    """Check if a path is a network share (UNC path)"""
    path_str = str(path)
    # UNC paths start with \\
    if path_str.startswith('\\\\'):
        return True
    # Check if path contains common network share patterns
    if 'network' in path_str.lower() or 'smb' in path_str.lower():
        return True
    return False


def check_remote_drive(path: Path) -> bool:
    """Check if a path is on a remote/mapped drive"""
    try:
        drive = str(path.drive).upper()
        if not drive:
            return is_network_path(path)

        # Use PowerShell to get network drives and check if our drive is one of them
        result = subprocess.run(
            ['powershell', '-c',
             '[System.IO.DriveInfo]::GetDrives() | Where-Object { $_.DriveType -eq [System.IO.DriveType]::Network } | ForEach-Object { $_.Name }'],
            capture_output=True, text=True, timeout=10
        )
        network_drives = result.stdout.strip().split('\n')
        # network_drives will be like ['Y:\\', 'Z:\\', '']
        drive_letter = drive[0] + ':\\'
        if drive_letter in network_drives:
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
        """Called when a new file is created"""
        if event.is_directory:
            return

        file_path = Path(event.src_path)

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

    MAX_PROCESSED_FILES = 10000

    def __init__(self, config, on_new_file: Callable[[Path], None], poll_interval: float = 5.0):
        self.config = config
        self.on_new_file = on_new_file
        self.poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # Files that existed when the watcher started (never evicted - prevents reprocessing)
        self._initial_files: Set[str] = set()
        # All files ever seen by this watcher (only evicted when file no longer exists on disk)
        self._seen_files: Set[str] = set()

    def _scan_existing_files(self):
        """Scan for existing files to build initial state"""
        self._initial_files.clear()
        self._seen_files.clear()
        for folder in self.config.get_log_folders():
            if folder.exists():
                for file_path in folder.rglob('*'):
                    if file_path.is_file() and file_path.suffix.lower() in ('.evtc', '.zevtc'):
                        self._initial_files.add(str(file_path))
                        self._seen_files.add(str(file_path))
                        logger.debug(f"Existing file found: {file_path}")

    def start(self, initial_files: Optional[Set[str]] = None):
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
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info(f"Polling watcher started (interval: {self.poll_interval}s)")

    def _poll_loop(self):
        """Main polling loop"""
        while self._running:
            try:
                # Occasionally clean up entries for files that no longer exist on disk
                self._cleanup_missing_files()
                self._check_for_new_files()
            except Exception as e:
                logger.error(f"Error in polling loop: {e}")

            time.sleep(self.poll_interval)

    def _cleanup_missing_files(self):
        """Remove entries for files that no longer exist on disk.

        This prevents unbounded growth when log files are archived or deleted.
        """
        if len(self._seen_files) <= self.MAX_PROCESSED_FILES:
            return

        to_remove = set()
        for path_str in self._seen_files:
            if not Path(path_str).exists():
                to_remove.add(path_str)

        for p in to_remove:
            self._seen_files.discard(p)

        # If still over cap after cleanup, log a warning (shouldn't happen in normal use)
        if len(self._seen_files) > self.MAX_PROCESSED_FILES:
            logger.warning(
                f"_seen_files has {len(self._seen_files)} entries and exceeds cap; "
                "log files may need manual cleanup"
            )

    def _check_for_new_files(self):
        """Check folders for new files"""
        for folder in self.config.get_log_folders():
            if not folder.exists():
                continue

            for file_path in folder.rglob('*'):
                if not file_path.is_file():
                    continue

                if file_path.suffix.lower() not in ('.evtc', '.zevtc'):
                    continue

                path_str = str(file_path)

                # Skip if already seen
                if path_str in self._seen_files:
                    continue

                # Skip initial files (existing when watcher started)
                if path_str in self._initial_files:
                    logger.debug(f"Skipping existing file: {file_path.name}")
                    self._seen_files.add(path_str)
                    continue

                # Check if file is stable (not being written)
                if not self._is_file_stable(file_path):
                    continue

                # New file!
                logger.info(f"New file detected (polling): {file_path.name}")

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

                size = file_path.stat().st_size
                mtime = file_path.stat().st_mtime

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
        self._running = False
        self._use_polling = False
        self._is_network: Optional[bool] = None  # Instance-level cache

    def _scan_existing_files(self):
        """Scan for existing files to skip them initially"""
        self._initial_files.clear()
        for folder in self.config.get_log_folders():
            if folder.exists():
                for file_path in folder.rglob('*'):
                    if file_path.is_file() and file_path.suffix.lower() in ('.evtc', '.zevtc'):
                        self._initial_files.add(str(file_path))
                        logger.debug(f"Existing file found: {file_path}")

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

    def start(self):
        """Start watching for new log files"""
        self._scan_existing_files()

        # Check if we need polling for network shares
        if self._is_network_share():
            logger.info("Network share detected - using polling watcher")
            self._use_polling = True
            self._polling_watcher = PollingFileWatcher(
                self.config,
                self._on_new_file,
                self.poll_interval
            )
            self._polling_watcher.start(self._initial_files)
        else:
            # Use efficient OS-native events
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
