"""
GW2_EI_log_combiner runtime manager — download-at-runtime, subprocess-only.

LICENSE BOUNDARY
----------------
The upstream tool (Drevarr/GW2_EI_log_combiner) is GPL-3.0.  SparkyBot is MIT.
This module therefore NEVER vendors, imports, links, or redistributes any
portion of the combiner.  It downloads the combiner onto the user's machine at
runtime (with the user's explicit consent, matching the existing EI updater in
core/ei_updater.py) and invokes it as a SEPARATE, unrelated subprocess.

No code, comments, template text, or configuration wording has been copied
from the combiner repository.  All config text emitted by this module is
original wording written specifically for SparkyBot.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import requests
from core.apppaths import is_frozen

logger = logging.getLogger(__name__)

_GITHUB_API_URL = (
    "https://api.github.com/repos/Drevarr/GW2_EI_log_combiner/releases/latest"
)
_GITHUB_REPO_URL = "https://github.com/Drevarr/GW2_EI_log_combiner"

_COMBINER_CONFIG_TEMPLATE = """\
[TopStatsCfg]
guild_name = {guild_name}
guild_id = {guild_id}
api_key = {api_key}
input_directory = {input_directory}
output_filename = None
json_output_filename = None
db_output_filename = Top_Stats.db
db_path = {db_path}
write_all_data_to_json = false
db_update = true
fight_data_charts = true
write_excel = false
excel_output_filename = Top_Stats.xlsx
excel_path = {db_path}
skill_casts_by_role_limit = 40
hide_columns = false
Boons_Detailed = true
Offensive_Detailed = true
Defenses_Detailed = true
Support_Detailed = true
Sort_Mode = Total
Chart_Mode = Bar

[BlackList]
accounts =

[Boon_Weights]
Aegis = 1
Alacrity = 1
Fury = 1
Might = 1
Protection = 1
Quickness = 1
Regeneration = 1
Resistance = 1
Resolution = 1
Stability = 1
Swiftness = 1
Vigor = 1
Superspeed = 1

[Condition_Weights]
Bleeding = 1
Burning = 1
Confusion = 1
Poison = 1
Torment = 1
Blind = 1
Chilled = 1
Crippled = 1
Fear = 1
Immobile = 1
Slow = 1
Taunt = 1
Weakness = 1
Vulnerability = 1

[DiscordCfg]
webhook_url = false
discord_additional_notes =

[SupportProfs]
Firebrand = b1122, b717, b26980, b740, b1187
Chronomancer = b1122, b717, b740, b725
Specter = b1122, b717, b740, b725, b743
"""


class CombinerNotInstalled(RuntimeError):
    """The combiner is not installed and cannot be fetched (e.g. offline)."""


class CombinerRunError(RuntimeError):
    """The combiner subprocess exited with a non-zero status or produced no output."""


class CombinerManager:
    """Arm's-length runtime manager for the GW2_EI_log_combiner.

    The combiner is downloaded at runtime into *data_dir*/combiner/<version>/
    and invoked exclusively as a subprocess.  No GPL code is ever vendored,
    imported, or shipped with SparkyBot.
    """

    def __init__(self, data_dir: Path):
        self._data_dir = Path(data_dir)
        self._combiner_root = self._data_dir / "combiner"
        self._meta_path = self._combiner_root / "meta.json"
        self.db_dir = self._data_dir / "topstats_db"
        self._meta_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # installed_version
    # ------------------------------------------------------------------

    def installed_version(self) -> Optional[str]:
        """Return the currently installed combiner version, or *None*."""
        try:
            meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
            return meta.get("version")
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return None

    # ------------------------------------------------------------------
    # check_latest
    # ------------------------------------------------------------------

    def check_latest(self) -> Optional[tuple[str, str]]:
        """Query the GitHub API for the latest release.

        Returns:
            ``(version, asset_download_url)`` on success, or *None* on any
            network or parsing failure.
        """
        try:
            response = requests.get(_GITHUB_API_URL, timeout=15)
            if response.status_code != 200:
                logger.warning(
                    "GitHub API returned %s for combiner release", response.status_code
                )
                return None
            data = response.json()
        except requests.RequestException as exc:
            logger.warning("Failed to reach GitHub API for combiner: %s", exc)
            return None

        tag = data.get("tag_name", "")
        version = tag.lstrip("v")
        if not version:
            logger.warning("No tag_name in combiner release JSON")
            return None

        download_url = ""
        for asset in data.get("assets", []):
            name = asset.get("name", "").lower()
            if name.endswith(".zip"):
                download_url = asset.get("browser_download_url", "")
                break

        if not download_url:
            logger.warning("No .zip asset found in combiner release v%s", version)
            return None

        return (version, download_url)

    # ------------------------------------------------------------------
    # download_and_install
    # ------------------------------------------------------------------

    def download_and_install(
        self,
        url: str,
        version: str,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> Path:
        """Stream-download the release zip, extract it, and record metadata.

        The version directory is ``data_dir/combiner/<version>/``.  The entry
        point is ``TopStats.exe`` on Windows and ``tw5_top_stats.py`` on other
        platforms.

        Returns the path to the installed entry-point file.
        """
        install_dir = self._combiner_root / version
        if install_dir.exists():
            shutil.rmtree(install_dir)
        install_dir.mkdir(parents=True)

        temp_dir = None
        try:
            response = requests.get(url, stream=True, timeout=120)
            if response.status_code != 200:
                raise CombinerNotInstalled(
                    f"Download failed: HTTP {response.status_code}"
                )

            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0

            temp_dir = Path(tempfile.mkdtemp())
            zip_path = temp_dir / "combiner_release.zip"

            with open(zip_path, "wb") as fh:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size:
                            progress_callback(downloaded / total_size * 100)

            logger.info("Downloaded %d bytes for combiner v%s", downloaded, version)

            extract_dir = temp_dir / "extracted"
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            self._copy_extracted(extract_dir, install_dir)

            entry = self._find_entry(install_dir)
            if entry is None:
                raise CombinerNotInstalled(
                    f"No entry point (TopStats.exe / tw5_top_stats.py) found "
                    f"in extracted combiner v{version}"
                )

            self._write_meta(version, url, entry)

            logger.info("Combiner v%s installed at %s", version, install_dir)
            return Path(entry)

        finally:
            if temp_dir is not None and temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # ensure_installed
    # ------------------------------------------------------------------

    def ensure_installed(
        self,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> Path:
        """Return the path to the installed entry point, downloading if needed.

        Raises :exc:`CombinerNotInstalled` when no version is installed and
        the latest release cannot be fetched (offline, network error, etc.).
        """
        version = self.installed_version()
        if version is not None:
            install_dir = self._combiner_root / version
            entry = self._find_entry(install_dir)
            if entry is not None:
                return Path(entry)

        latest = self.check_latest()
        if latest is None:
            raise CombinerNotInstalled(
                "No combiner installed and unable to reach GitHub to download one.  "
                "See %s for manual installation." % _GITHUB_REPO_URL
            )

        ver, url = latest
        return self.download_and_install(url, ver, progress_callback)

    # ------------------------------------------------------------------
    # write_run_config
    # ------------------------------------------------------------------

    def write_run_config(
        self,
        run_dir: Path,
        input_dir: Path,
        guild_name: str = "",
        guild_id: str = "",
        api_key: str = "",
    ) -> Path:
        """Emit a ``top_stats_config.ini`` file into *run_dir*.

        ``db_path`` is always set to the stable directory ``self.db_dir`` so
        the combiner's history database survives across runs.
        """
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        db_path = str(self.db_dir)
        input_dir_str = str(Path(input_dir).resolve())

        body = _COMBINER_CONFIG_TEMPLATE.format(
            guild_name=guild_name or "None",
            guild_id=guild_id or "None",
            api_key=api_key or "None",
            input_directory=input_dir_str,
            db_path=db_path,
        )

        config_path = run_dir / "top_stats_config.ini"
        config_path.write_text(body, encoding="utf-8")
        return config_path

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------

    def run(
        self,
        input_dir: Path,
        run_dir: Path,
        timeout: int = 900,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Path:
        """Run the combiner as a subprocess.

        The combiner is invoked with ``-c <config.ini>`` and the working
        directory set to *run_dir*.  On Windows a hidden window flag is used.

        Returns:
            The path to the ``Drag_and_Drop_Log_Summary_*.json`` file that the
            combiner created inside *input_dir*.

        Raises:
            CombinerRunError: non-zero exit, or no summary file produced.
        """
        input_dir = Path(input_dir).resolve()
        run_dir = Path(run_dir).resolve()

        entry = self._find_entry(self._combiner_root / (self.installed_version() or ""))
        if entry is None:
            raise CombinerNotInstalled("No installed combiner entry point found")

        config_path = self.write_run_config(run_dir, input_dir)

        kwargs: dict = {
            "capture_output": True,
            "text": True,
            "timeout": timeout,
            "cwd": str(run_dir),
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        if entry.endswith(".py"):
            if is_frozen():
                raise CombinerRunError(
                    "Cannot run the combiner in source mode (tw5_top_stats.py) "
                    "from a frozen SparkyBot build.  The bundled .exe build "
                    "requires the TopStats.exe asset."
                )
            cmd = [sys.executable, entry, "-c", str(config_path)]
        else:
            cmd = [entry, "-c", str(config_path)]

        logger.info("Running combiner: %s", cmd)

        try:
            result = subprocess.run(cmd, **kwargs)
        except subprocess.TimeoutExpired as exc:
            raise CombinerRunError(
                f"Combiner timed out after {timeout} seconds"
            ) from exc

        if progress_callback:
            progress_callback("Combiner finished with exit code %d" % result.returncode)

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()

            if "ModuleNotFoundError" in stderr:
                raise CombinerRunError(
                    "The combiner source run failed because a required package is "
                    "missing.  Please install the dependencies with:\n"
                    "    pip install requests glicko2 xlsxwriter\n"
                    "Original error:\n%s"
                    % (stderr[-2000:] if len(stderr) > 2000 else stderr)
                )

            tail = stderr or stdout
            tail = tail[-2000:] if len(tail) > 2000 else tail
            raise CombinerRunError(
                "Combiner exited with code %d.\n--- stderr/stdout tail ---\n%s"
                % (result.returncode, tail)
            )

        summary_files = sorted(
            input_dir.glob("Drag_and_Drop_Log_Summary_*.json"),
            key=lambda p: p.stat().st_mtime,
        )
        if not summary_files:
            raise CombinerRunError(
                "Combiner reported success but no "
                "Drag_and_Drop_Log_Summary_*.json was found in %s" % input_dir
            )

        return summary_files[-1]

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _copy_extracted(self, extract_dir: Path, install_dir: Path):
        """Copy all extracted content into *install_dir*, recursing into a
        single top-level directory if the zip wrapped everything in one."""
        children = list(extract_dir.iterdir())
        if len(children) == 1 and children[0].is_dir():
            source = children[0]
        else:
            source = extract_dir

        for item in source.iterdir():
            dest = install_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

    @staticmethod
    def _find_entry(install_dir: Path) -> Optional[str]:
        """Locate the combiner entry-point file inside *install_dir*.

        On Windows we prefer ``TopStats.exe``; on other platforms we look for
        ``tw5_top_stats.py`` recursively.
        """
        if sys.platform == "win32":
            exe = install_dir / "TopStats.exe"
            if exe.exists():
                return str(exe)
            # Fall back to .py even on Windows if .exe is absent
            for candidate in install_dir.rglob("tw5_top_stats.py"):
                return str(candidate)
        else:
            for candidate in install_dir.rglob("tw5_top_stats.py"):
                return str(candidate)
        return None

    def _write_meta(self, version: str, url: str, entry: str):
        self._meta_path.write_text(
            json.dumps(
                {
                    "version": version,
                    "url": url,
                    "installed_at": datetime.now(timezone.utc).isoformat(),
                    "entry": entry,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
