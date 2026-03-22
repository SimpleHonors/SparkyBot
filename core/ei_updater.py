"""Elite Insights Auto-Updater - Preserves user config files"""

import os
import shutil
import logging
import zipfile
import tempfile
from pathlib import Path
from typing import Optional, Tuple
import requests

logger = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com/repos/baaron4/GW2-Elite-Insights-Parser/releases/latest"
GITHUB_DOWNLOAD_URL = "https://github.com/baaron4/GW2-Elite-Insights-Parser/releases/download"


class EIUpdater:
    """Handles updating Elite Insights while preserving user config"""

    def __init__(self, gw2ei_folder: Path):
        self.gw2ei_folder = Path(gw2ei_folder)
        self.settings_folder = self.gw2ei_folder / "Settings"

    def check_for_update(self) -> Tuple[bool, str, str]:
        """Check if update is available

        Returns:
            (update_available, latest_version, download_url)
        """
        try:
            response = requests.get(GITHUB_API_URL, timeout=10)
            if response.status_code != 200:
                logger.error(f"GitHub API returned {response.status_code}")
                return False, "", ""

            data = response.json()
            latest_version = data.get("tag_name", "").lstrip("v")

            # Get current version
            current_version = self.get_current_version()
            if current_version and self._compare_versions(latest_version, current_version) <= 0:
                logger.info(f"Already on latest version: {current_version}")
                return False, latest_version, ""

            # Find the CLI release asset (we need CLI for parsing)
            # Order of preference: GW2EICLI.zip first, then GW2EI.zip
            cli_url = ""
            gui_url = ""
            for asset in data.get("assets", []):
                name = asset["name"].lower()
                if name.endswith(".zip") and "sig" not in name:
                    if name == "gw2eicli.zip":
                        cli_url = asset["browser_download_url"]
                    elif name.startswith("gw2ei"):
                        gui_url = asset["browser_download_url"]

            # Prefer CLI version for parsing
            download_url = cli_url if cli_url else gui_url

            if not download_url:
                logger.warning("No Windows download found in release")
                return False, latest_version, ""

            logger.info(f"Update available: {current_version} -> {latest_version}")
            return True, latest_version, download_url

        except requests.RequestException as e:
            logger.error(f"Failed to check for updates: {e}")
            return False, "", ""

    def get_current_version(self) -> str:
        """Get current installed version from executable"""
        cli_path = self.gw2ei_folder / "GuildWars2EliteInsights-CLI.exe"
        if not cli_path.exists():
            return ""

        # Try to get version from file properties
        try:
            import win32api  # pywin32
            info = win32api.GetFileVersionInfo(str(cli_path), '\\')
            version = f"{win32api.HIWORD(info['FileVersionMS'])}.{win32api.LOWORD(info['FileVersionMS'])}.{win32api.HIWORD(info['FileVersionLS'])}"
            return version
        except ImportError:
            pass

        # Fallback: try to parse from filename in folder
        # The release zips typically contain version info
        return ""

    def _compare_versions(self, v1: str, v2: str) -> int:
        """Compare versions. Returns 1 if v1 > v2, 0 if equal, -1 if v1 < v2"""
        def parse(v):
            return [int(x) for x in v.split('.')][:3]

        try:
            p1, p2 = parse(v1), parse(v2)
            for a, b in zip(p1, p2):
                if a > b:
                    return 1
                if a < b:
                    return -1
            return 0
        except:
            return 0

    def download_and_update(self, download_url: str, progress_callback=None) -> Tuple[bool, str]:
        """Download and install update, preserving Settings folder

        Returns:
            (success, message)
        """
        temp_dir = None

        try:
            logger.info(f"Downloading from {download_url}")

            # Download zip to temp file
            response = requests.get(download_url, stream=True, timeout=60)
            if response.status_code != 200:
                return False, f"Download failed: HTTP {response.status_code}"

            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0

            temp_dir = tempfile.mkdtemp()
            zip_path = Path(temp_dir) / "ei_update.zip"

            with open(zip_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size:
                            progress_callback(downloaded / total_size * 100)

            logger.info(f"Downloaded {downloaded} bytes")

            # Backup Settings folder
            settings_backup = None
            if self.settings_folder.exists():
                settings_backup = Path(temp_dir) / "Settings_backup"
                shutil.copytree(self.settings_folder, settings_backup)
                logger.info("Settings folder backed up")

            # Extract zip to temp location
            extract_dir = Path(temp_dir) / "extracted"
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)

            # Find the extracted folder (release typically extracts to a versioned folder)
            extracted_folder = extract_dir
            for item in extract_dir.iterdir():
                if item.is_dir() and "GW2" in item.name.upper():
                    extracted_folder = item
                    break

            logger.info(f"Extracted to {extracted_folder}")

            # Remove old installation (except Settings)
            if self.gw2ei_folder.exists():
                for item in self.gw2ei_folder.iterdir():
                    if item.name.lower() == "settings":
                        continue  # Don't delete Settings
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()

            # Copy new files (except Settings - keep old one)
            for item in extracted_folder.iterdir():
                if item.name.lower() == "settings":
                    # Skip Settings in new release
                    continue
                dest = self.gw2ei_folder / item.name
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)

            # Restore Settings folder
            if settings_backup and settings_backup.exists():
                if self.settings_folder.exists():
                    shutil.rmtree(self.settings_folder)
                shutil.copytree(settings_backup, self.settings_folder)
                logger.info("Settings folder restored")

            version = extracted_folder.name
            return True, f"Successfully updated to {version}"

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
                shutil.rmtree(temp_dir)

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
