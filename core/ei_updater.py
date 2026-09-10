"""Elite Insights Auto-Updater - Preserves user config files"""

import os
import shutil
import logging
import zipfile
import tempfile
import threading
import time
import re
from pathlib import Path
from typing import Optional, Tuple
import requests

logger = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com/repos/baaron4/GW2-Elite-Insights-Parser/releases/latest"
GITHUB_DOWNLOAD_URL = "https://github.com/baaron4/GW2-Elite-Insights-Parser/releases/download"
CLI_NAME = "GuildWars2EliteInsights-CLI.exe"
REQUIRED_FILES = tuple("GuildWars2EliteInsights-CLI" + suffix for suffix in
                       (".exe", ".dll", ".deps.json", ".runtimeconfig.json"))
_install_lock = threading.RLock()
_repair_failures = {}


class EIUpdater:
    """Handles updating Elite Insights while preserving user config"""

    def __init__(self, gw2ei_folder: Path):
        self.gw2ei_folder = Path(gw2ei_folder)
        self.settings_folder = self.gw2ei_folder / "Settings"

    def is_installed(self) -> bool:
        """A GUI-only, empty, or partial download cannot parse logs."""
        return all((self.gw2ei_folder / name).is_file()
                   and (self.gw2ei_folder / name).stat().st_size > 0
                   for name in REQUIRED_FILES)

    def latest_release(self) -> Tuple[str, str]:
        """Resolve the CLI package for setup, repair, and updates alike."""
        try:
            response = requests.get(
                "https://github.com/baaron4/GW2-Elite-Insights-Parser/releases/latest",
                allow_redirects=False, timeout=10)
            match = re.search(r"/tag/(v?[0-9]+(?:\.[0-9]+)*)$",
                              response.headers.get("Location", ""))
            if match:
                tag = match.group(1)
                return tag.lstrip("v"), f"{GITHUB_DOWNLOAD_URL}/{tag}/GW2EICLI.zip"
        except requests.RequestException:
            pass
        response = requests.get(GITHUB_API_URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        tag = data.get("tag_name", "")
        if not re.fullmatch(r"v?[0-9]+(?:\.[0-9]+)*", tag):
            raise ValueError("Could not identify the fight-log parser release")
        for asset in data.get("assets", []):
            if asset.get("name", "").lower() == "gw2eicli.zip":
                return tag.lstrip("v"), asset["browser_download_url"]
        raise ValueError("The release does not contain the Windows CLI parser")

    def check_for_update(self) -> Tuple[bool, str, str]:
        """Missing installs need a repair, not an 'already up to date' result."""
        try:
            latest_version, download_url = self.latest_release()
            current_version = self.get_current_version()
            if self.is_installed() and current_version and self._compare_versions(latest_version, current_version) <= 0:
                logger.info(f"Already on latest version: {current_version}")
                return False, latest_version, download_url
            logger.info(f"Update available: {current_version} -> {latest_version}")
            return True, latest_version, download_url
        except (requests.RequestException, ValueError, KeyError) as e:
            logger.error(f"Failed to check for updates: {e}")
            return False, "", ""

    def ensure_installed(self, progress_callback=None, *, retry=False) -> Tuple[bool, str]:
        """Repair once across startup, watcher, and concurrent report workers."""
        with _install_lock:
            if self.is_installed():
                return True, "Fight-log parser is ready"
            key = str(self.gw2ei_folder.resolve())
            failed = _repair_failures.get(key)
            if failed and not retry and time.monotonic() - failed[0] < 60:
                return False, failed[1]
            try:
                version, url = self.latest_release()
                result = self.download_and_update(url, version, progress_callback)
            except Exception as exc:
                result = False, f"Could not download the fight-log parser: {exc}"
            if result[0]:
                _repair_failures.pop(key, None)
            else:
                _repair_failures[key] = (time.monotonic(), result[1])
                logger.error("Automatic parser repair failed: %s", result[1])
            return result

    def get_current_version(self) -> str:
        """Get current installed version."""
        cli_path = self.gw2ei_folder / "GuildWars2EliteInsights-CLI.exe"
        if not self.is_installed():
            return ""

        # Method 1: Read from our version file (most reliable, no pywin32 needed)
        version_file = self.gw2ei_folder / ".ei_version"
        if version_file.exists():
            try:
                version = version_file.read_text().strip()
                if version:
                    return version
            except Exception:
                pass

        # Method 2: Try win32api (requires pywin32)
        try:
            import win32api
            info = win32api.GetFileVersionInfo(str(cli_path), '\\')
            version = f"{win32api.HIWORD(info['FileVersionMS'])}.{win32api.LOWORD(info['FileVersionMS'])}.{win32api.HIWORD(info['FileVersionLS'])}"
            # Save for future reads
            self._save_version(version)
            return version
        except Exception:
            pass

        return ""

    def _save_version(self, version: str):
        """Save installed EI version to a file."""
        version_file = self.gw2ei_folder / ".ei_version"
        try:
            version_file.write_text(version)
        except Exception:
            pass

    def _compare_versions(self, v1: str, v2: str) -> int:
        """Compare versions. Returns 1 if v1 > v2, 0 if equal, -1 if v1 < v2"""
        def parse(v):
            import re
            return [int(x) for x in re.findall(r"\d+", str(v))]

        try:
            p1, p2 = parse(v1), parse(v2)
            width = max(len(p1), len(p2))
            p1.extend([0] * (width - len(p1)))
            p2.extend([0] * (width - len(p2)))
            for a, b in zip(p1, p2):
                if a > b:
                    return 1
                if a < b:
                    return -1
            return 0
        except (TypeError, ValueError):
            return 0

    def download_and_update(self, download_url: str, version: str = "", progress_callback=None) -> Tuple[bool, str]:
        with _install_lock:
            return self._download_and_update(download_url, version, progress_callback)

    def _download_and_update(self, download_url: str, version: str, progress_callback=None) -> Tuple[bool, str]:
        """Download and install update, preserving Settings folder

        Returns:
            (success, message)
        """
        temp_dir = None
        backup = None
        replaced = False

        try:
            logger.info(f"Downloading from {download_url}")

            # Download zip to temp file
            response = requests.get(download_url, stream=True, timeout=60)
            if response.status_code != 200:
                return False, f"Download failed: HTTP {response.status_code}"

            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0

            self.gw2ei_folder.parent.mkdir(parents=True, exist_ok=True)
            # Stage beside the destination so the final directory rename stays
            # on one volume, including portable installs on another drive.
            temp_dir = tempfile.mkdtemp(prefix=".ei-install-", dir=self.gw2ei_folder.parent)
            zip_path = Path(temp_dir) / "ei_update.zip"

            with open(zip_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size:
                            progress_callback(downloaded / total_size * 100)

            logger.info(f"Downloaded {downloaded} bytes")

            # Extract zip to temp location
            extract_dir = Path(temp_dir) / "extracted"
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for member in zip_ref.infolist():
                    path = Path(member.filename.replace("\\", "/"))
                    if path.is_absolute() or ".." in path.parts or ":" in member.filename:
                        raise ValueError("Unsafe path in parser download")
                zip_ref.extractall(extract_dir)
            candidates = [p.parent for p in extract_dir.rglob(CLI_NAME)
                          if EIUpdater(p.parent).is_installed()]
            if len(candidates) != 1:
                raise ValueError("Download does not contain a complete Windows CLI parser")
            extracted_folder = candidates[0]
            logger.info(f"Extracted to {extracted_folder}")
            if self.settings_folder.is_dir():
                shutil.copytree(self.settings_folder, extracted_folder / "Settings", dirs_exist_ok=True)
            if version:
                (extracted_folder / ".ei_version").write_text(version, encoding="utf-8")
            if self.gw2ei_folder.exists():
                backup = Path(temp_dir) / "previous"
                self.gw2ei_folder.rename(backup)
            try:
                extracted_folder.rename(self.gw2ei_folder)
                replaced = True
            except Exception:
                if backup is not None:
                    backup.rename(self.gw2ei_folder)
                    backup = None
                raise
            logger.info("Fight-log parser installed and verified at %s", self.gw2ei_folder)
            return True, "Fight-log parser is ready"

        except requests.RequestException as e:
            logger.error(f"Download failed: {e}")
            return False, f"Download failed: {e}"
        except zipfile.BadZipFile as e:
            logger.error(f"Invalid zip file: {e}")
            return False, "Invalid download (corrupt zip)"
        except Exception as e:
            logger.error(f"Update failed: {e}")
            return False, f"Update failed: {e}"
        finally:
            # Cleanup temp directory
            if temp_dir and Path(temp_dir).exists():
                # Retain the previous install if rollback itself failed.
                if backup is not None and backup.exists() and not replaced:
                    logger.error("Previous parser preserved at %s", backup)
                else:
                    shutil.rmtree(temp_dir, ignore_errors=True)

        return False, "Unknown error"

    def get_current_info(self) -> dict:
        """Get current EI installation info"""
        info = {
            "folder": str(self.gw2ei_folder),
            "exists": self.gw2ei_folder.exists(),
            "has_cli": False,
            "has_settings": False,
            "settings_path": None
        }

        if info["exists"]:
            info["has_cli"] = (self.gw2ei_folder / "GuildWars2EliteInsights-CLI.exe").exists()
            info["has_settings"] = self.settings_folder.exists()
            info["settings_path"] = str(self.settings_folder) if info["has_settings"] else None

        return info
