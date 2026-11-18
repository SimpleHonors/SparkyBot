"""Invokes GW2EI CLI for parsing EVTC log files."""

import json
import shutil
import subprocess
import logging
import tempfile
import threading
import time
import uuid
import weakref
from pathlib import Path
from typing import Optional

from core.apppaths import app_dir, gw2ei_dir, no_window_kwargs
from core.fight_links import digest

logger = logging.getLogger(__name__)

_same_log_locks_guard = threading.Lock()
_same_log_locks = weakref.WeakValueDictionary()

PARSE_CONFIG_CONTENT = (
    "SaveOutJSON=True\n"
    "SaveOutHTML=False\n"
    "SaveOutCSV=False\n"
    "SaveOutTrace=False\n"
    "CompressRaw=False\n"
    "DetailledWvW=True\n"
    "ParseCombatReplay=True\n"
    "IndentJSON=False\n"
    "RawTimelineArrays=True\n"
    "ParsePhases=True\n"
    "ComputeDamageModifiers=True\n"
    "SaveAtOut=False\n"
    "SingleThreaded=False\n"
    "ParseMultipleLogs=False\n"
    "SkipFailedTries=False\n"
    "Anonymous=False\n"
    "HtmlExternalScripts=False\n"
    "HtmlCompressJson=False\n"
    "LightTheme=False\n"
    "CustomTooShort=2200\n"
)


def _is_network_share(path: Path) -> bool:
    drive = path.drive
    if not drive:
        return False
    if not drive.endswith(':'):
        return True
    try:
        import ctypes
        # GetDriveTypeW expects a root directory, including its trailing slash.
        return ctypes.windll.kernel32.GetDriveTypeW(drive + '\\') == 4
    except (AttributeError, OSError):
        return False


def _same_log_lock(log_file: Path):
    """Serialize EI writers to the same directory/stem output namespace."""
    key = str(log_file.parent.resolve() / log_file.stem).casefold()
    with _same_log_locks_guard:
        lock = _same_log_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _same_log_locks[key] = lock
        return lock


class GW2EIInvoker:
    """Invokes the GuildWars2EliteInsights CLI to parse log files"""

    def __init__(self, config):
        self.config = config
        self.home_dir = app_dir()

    def get_gw2ei_path(self) -> Optional[Path]:
        """Use only the complete parser managed by SparkyBot."""
        from core.ei_updater import EIUpdater, CLI_NAME
        if EIUpdater(gw2ei_dir()).is_installed():
            return gw2ei_dir() / CLI_NAME
        return None

    def get_gw2ei_folder(self) -> Path:
        """Get the GW2EI folder path"""
        return gw2ei_dir()

    def _ensure_parse_config(self, config_name: str = "wvwupload.conf") -> Path:
        """Return path to the EI parse config, always writing current settings.

        `config_name` lets concurrent invocations each use their OWN config file
        instead of sharing the single `wvwupload.conf`. The config content is
        identical every time, but a shared file is still rewritten (truncate +
        write) on every call, so two simultaneous parses could otherwise have one
        EI process read the file mid-rewrite. A per-job name removes that race.
        """
        conf_folder = gw2ei_dir() / "Settings"
        conf_folder.mkdir(parents=True, exist_ok=True)
        conf_path = conf_folder / config_name

        conf_path.write_text(PARSE_CONFIG_CONTENT, encoding="utf-8")
        logger.info(f"Wrote GW2EI config to: {conf_path}")
        logger.debug(f"Config content:\n{PARSE_CONFIG_CONTENT}")

        return conf_path

    def cache_key(self) -> tuple[str, str]:
        """(ei_version, settings_fingerprint) identifying current parse output."""
        from core.ei_updater import EIUpdater
        from core.raid_session import settings_fingerprint
        ei_version = EIUpdater(self.get_gw2ei_folder()).get_current_version() or "unknown"
        fingerprint = settings_fingerprint(PARSE_CONFIG_CONTENT)
        return ei_version, fingerprint

    def parse_file(self, log_file: Path, timeout: int = 120,
                   config_name: Optional[str] = None) -> Optional[Path]:
        """Parse a log file using GW2EI CLI

        Args:
            log_file: the .evtc/.zevtc to parse.
            timeout: subprocess timeout in seconds.
            config_name: optional per-invocation EI config filename. Pass a unique
                name (e.g. one per concurrent import job) so parallel parses don't
                share/clobber the single `wvwupload.conf`. When given, the config
                is treated as a temp file and removed after the run. When None,
                the default shared `wvwupload.conf` is used (single-file behavior,
                unchanged).

        Returns:
            Path to generated JSON file, or None if parsing failed.

        The live watcher and End Run worker can reach this method through
        separate ``GW2EIInvoker`` instances in the same application. Same-log
        and same-directory/stem calls are serialized until the deterministic
        output has been moved to a unique local claim path.
        """
        gw2ei_path = self.get_gw2ei_path()
        if not gw2ei_path:
            from core.ei_updater import EIUpdater
            logger.info("Preparing the fight-log parser automatically...")
            ready, message = EIUpdater(self.get_gw2ei_folder()).ensure_installed()
            if not ready:
                raise RuntimeError(
                    "SparkyBot could not prepare its fight-log parser. "
                    "Check your internet connection and reopen SparkyBot to retry. " + message)
            gw2ei_path = self.get_gw2ei_path()

        log_file = Path(log_file)
        parse_lock = _same_log_lock(log_file)
        parse_lock.acquire()
        is_temp_config = config_name is not None
        parse_config = None
        staging = None

        try:
            working_file = log_file
            if _is_network_share(log_file):
                # EI writes beside its input. Isolate equal basenames from different shares.
                staging = tempfile.TemporaryDirectory(prefix="SparkyBot-EI-")
                working_file = Path(staging.name) / log_file.name
                selected_hash = digest(log_file)
                shutil.copy2(log_file, working_file)
                if digest(working_file) != selected_hash or digest(log_file) != selected_hash:
                    logger.warning("EI staged copy does not match the selected raw log")
                    return None
            # In a shared directory only the exact EI names are attributable.
            # Do not let this job overwrite a previous owner's result.
            local_outputs = [working_file.with_name(working_file.stem + suffix) for suffix in
                             ('_detailed_wvw_kill.json', '_detailed_gh_kill.json')]
            if staging is None and any(p.exists() or p.is_symlink() for p in local_outputs):
                logger.warning("EI output already exists beside local input")
                return None
            parse_config = self._ensure_parse_config(
                config_name or "wvwupload.conf"
            )
            logger.info(
                f"Invoking GW2EI for {log_file.name} (timeout: {timeout}s)"
            )
            cmd = [str(gw2ei_path), "-c", str(parse_config), str(working_file)]
            logger.info(f"GW2EI command: {' '.join(cmd)}")

            start_time = time.monotonic()

            result = subprocess.run(
                cmd,
                cwd=str(self.home_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                **no_window_kwargs(),
            )

            elapsed = time.monotonic() - start_time
            logger.info(f"GW2EI completed in {elapsed:.1f}s with exit code {result.returncode}")

            if result.stdout:
                logger.debug(f"GW2EI stdout: {result.stdout[:1000]}")
            if result.stderr:
                logger.debug(f"GW2EI stderr: {result.stderr[:1000]}")
            if result.returncode != 0:
                logger.error(f"GW2EI failed with exit code {result.returncode}")
                if result.stderr:
                    logger.error(f"GW2EI stderr: {result.stderr[:500]}")
                return None

            # The private staging directory contained only our raw at launch.
            outputs = (list(working_file.parent.glob('*.json')) if staging is not None
                       else [p for p in local_outputs if p.exists()])
            if len(outputs) != 1 or outputs[0].is_symlink() or not outputs[0].is_file():
                logger.warning("No uniquely attributable EI output")
                return None
            json_file = outputs[0]
            if not self._wait_for_json_stable(json_file):
                logger.error(f"JSON file never stabilized: {json_file.name}")
                return None
            if staging is not None and (digest(working_file) != selected_hash
                                        or digest(log_file) != selected_hash):
                logger.warning("Raw log changed during EI parsing")
                return None
            claimed_file = self._claim_generated_json(json_file)
            logger.info(f"Generated JSON: {claimed_file.name}")
            return claimed_file

        except subprocess.TimeoutExpired:
            logger.error(f"GW2EI timed out after {timeout}s")
            return None
        except Exception as e:
            logger.error(f"Failed to invoke GW2EI: {e}")
            return None
        finally:
            # Clean up a per-invocation temp config so concurrent imports don't
            # litter GW2EI/Settings. The shared default config is left in place.
            if is_temp_config and parse_config is not None:
                try:
                    parse_config.unlink()
                except OSError:
                    logger.warning("Could not clean up EI job config")
            try:
                if staging is not None:
                    staging.cleanup()
            except OSError:
                # A successful claim is already outside staging and belongs to
                # the caller. Cleanup must not replace that path (or failure).
                logger.warning("Could not clean up EI staging directory")
            finally:
                parse_lock.release()

    def _claim_generated_json(self, json_file: Path) -> Path:
        """Move a stable shared GW2EI output to a unique local path."""
        claims_dir = Path(tempfile.gettempdir()) / "SparkyBot" / "GW2EIClaims"
        claims_dir.mkdir(parents=True, exist_ok=True)
        claimed = claims_dir / (
            f"{json_file.stem}-{uuid.uuid4().hex}{json_file.suffix}"
        )
        try:
            shutil.move(str(json_file), str(claimed))
        except Exception:
            claimed.unlink(missing_ok=True)
            raise
        return claimed

    def _wait_for_json_stable(self, json_file: Path, timeout: float = 30.0) -> bool:
        """Wait for GW2EI JSON output to finish writing.

        After GW2EI exits, the JSON file may still be flushing to disk.
        We wait until size is unchanged and the complete document parses. A
        trailing ``}`` alone cannot prove that another internal worker did not
        copy or rewrite the deterministic output path mid-document.
        """
        interval = 0.5
        elapsed = 0.0
        last_size = -1

        while elapsed < timeout:
            try:
                size = json_file.stat().st_size
            except FileNotFoundError:
                return False

            if size == last_size and size > 0:
                try:
                    with open(json_file, "r", encoding="utf-8") as handle:
                        parsed = json.load(handle)
                    if isinstance(parsed, dict):
                        return True
                except (json.JSONDecodeError, OSError, UnicodeError):
                    pass

            last_size = size
            time.sleep(interval)
            elapsed += interval

        logger.warning(f"JSON file still changing after {timeout}s: {json_file.name}")
        return False

    def check_dotnet(self) -> bool:
        """Check if .NET 8.0 runtime is available (required for GW2EI)"""
        try:
            result = subprocess.run(
                ["dotnet", "--list-runtimes"],
                capture_output=True,
                text=True,
                timeout=10,
                **no_window_kwargs(),
            )
            return "Microsoft.WindowsDesktop.App" in result.stdout or ".NET" in result.stdout
        except FileNotFoundError:
            logger.warning(".NET CLI not found in PATH")
            return False
        except subprocess.TimeoutExpired:
            logger.warning(".NET CLI timed out")
            return False
