"""Invokes GW2EI CLI for parsing EVTC log files"""

import subprocess
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class GW2EIInvoker:
    """Invokes the GuildWars2EliteInsights CLI to parse log files"""

    def __init__(self, config):
        self.config = config
        self.home_dir = Path(__file__).parent.parent

    def get_gw2ei_path(self) -> Optional[Path]:
        """Find GW2EI CLI executable"""
        # Check in GW2EI subfolder
        gw2ei_subfolder = self.home_dir / "GW2EI"
        if gw2ei_subfolder.exists():
            gw2ei_path = gw2ei_subfolder / "GuildWars2EliteInsights-CLI.exe"
            if gw2ei_path.exists():
                return gw2ei_path

        # Check in current directory
        gw2ei_path = self.home_dir / self.config.gw2ei_exe
        if gw2ei_path.exists():
            return gw2ei_path

        return None

    def get_gw2ei_folder(self) -> Path:
        """Get the GW2EI folder path"""
        return self.home_dir / "GW2EI"

    def _ensure_parse_config(self) -> Path:
        """Return path to wvwupload.conf, always writing current settings."""
        conf_folder = self.home_dir / "GW2EI" / "Settings"
        conf_folder.mkdir(parents=True, exist_ok=True)
        conf_path = conf_folder / "wvwupload.conf"

        content = (
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
            "SaveAtOut=True\n"
            "SingleThreaded=False\n"
            "ParseMultipleLogs=False\n"
            "SkipFailedTries=False\n"
            "Anonymous=False\n"
            "HtmlExternalScripts=False\n"
            "HtmlCompressJson=False\n"
            "LightTheme=False\n"
            "CustomTooShort=2200\n"
        )

        conf_path.write_text(content, encoding="utf-8")
        logger.info(f"Wrote GW2EI config to: {conf_path}")
        logger.debug(f"Config content:\n{content}")

        return conf_path

    def parse_file(self, log_file: Path, timeout: int = 120) -> Optional[Path]:
        """Parse a log file using GW2EI CLI

        Returns:
            Path to generated JSON file, or None if parsing failed
        """
        gw2ei_path = self.get_gw2ei_path()
        if not gw2ei_path:
            logger.error("GW2EI executable not found")
            return None

        parse_config = self._ensure_parse_config()

        logger.info(f"Invoking GW2EI for {log_file.name} (timeout: {timeout}s)")

        try:
            cmd = [str(gw2ei_path), "-c", str(parse_config), str(log_file)]
            logger.info(f"GW2EI command: {' '.join(cmd)}")

            start_time = time.time()

            result = subprocess.run(
                cmd,
                cwd=str(self.home_dir),
                capture_output=True,
                text=True,
                timeout=timeout
            )

            elapsed = time.time() - start_time
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

            # Find generated JSON file
            json_file = self._find_generated_json(log_file, start_time)
            if json_file:
                if not self._wait_for_json_stable(json_file):
                    logger.error(f"JSON file never stabilized: {json_file.name}")
                    return None
                logger.info(f"Generated JSON: {json_file.name}")
                return json_file
            else:
                logger.warning("No JSON file generated")
                return None

        except subprocess.TimeoutExpired:
            logger.error(f"GW2EI timed out after {timeout}s")
            return None
        except Exception as e:
            logger.error(f"Failed to invoke GW2EI: {e}")
            return None

    def _find_generated_json(self, log_file: Path, start_time: float) -> Optional[Path]:
        """Find the JSON file generated from parsing the log file

        Args:
            log_file: The source EVTC log file
            start_time: Timestamp when parsing started (files modified after this are results)
        """
        base_path = log_file.parent / log_file.stem

        # Check for known WvW and GH kill variants first
        for suffix in ['_detailed_wvw_kill.json', '_detailed_gh_kill.json']:
            json_path = Path(str(base_path) + suffix)
            if json_path.exists() and json_path.stat().st_mtime >= start_time:
                return json_path

        # Fallback: search for any matching JSON created after start time
        # This is ambiguous when multiple logs share similar names; log a warning
        candidates = []
        for p in log_file.parent.glob(f"{log_file.stem}*.json"):
            try:
                if p.stat().st_mtime >= start_time:
                    candidates.append(p)
            except FileNotFoundError:
                pass
        if candidates:
            logger.warning(
                f"Ambiguous JSON detection for {log_file.name}: "
                f"found {len(candidates)} candidates, using first: {candidates[0].name}"
            )
            return candidates[0]

        return None

    def _wait_for_json_stable(self, json_file: Path, timeout: float = 30.0) -> bool:
        """Wait for GW2EI JSON output to finish writing.

        After GW2EI exits, the JSON file may still be flushing to disk.
        We wait until size is unchanged AND the last byte is '}' (valid JSON closing).
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
                    with open(json_file, 'rb') as f:
                        f.seek(-1, 2)
                        last_byte = f.read(1)
                    if last_byte == b'}':
                        return True
                except OSError:
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
                timeout=10
            )
            return "Microsoft.WindowsDesktop.App" in result.stdout or ".NET" in result.stdout
        except FileNotFoundError:
            logger.warning(".NET CLI not found in PATH")
            return False
        except subprocess.TimeoutExpired:
            logger.warning(".NET CLI timed out")
            return False
