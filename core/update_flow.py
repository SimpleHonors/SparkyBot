"""Window-free SparkyBot update engine.

Owns the whole update pipeline — the silent launch check, the manual check
behind the Updates tab button, the release download, and staging into
`.update_pending/` (bootstrap.py applies staged files on the NEXT launch,
before anything app-side is imported, when nothing is locked).

Lives on the app controller (SparkyBotApp) with ZERO widget dependency, so
every path works when no window was ever constructed — start-minimized
sessions used to fail silently here because the old flow routed through
private SettingsWindow methods. UI surfaces (the Settings Updates tab today,
a status-bar banner later) subscribe to the Qt signals below and stay thin.
"""

import logging
import re
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

from core.version import VERSION

logger = logging.getLogger(__name__)


def parse_version(version_str: str) -> tuple:
    """Parse version string like 'v1.5' or '1.12.3' into a comparable tuple of ints."""
    clean = (version_str or "").strip().lstrip('v')
    parts = []
    for part in clean.split('.'):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


class UpdateFlow(QObject):
    """Update pipeline controller (SparkyBot + Elite Insights launch check).

    Threading: the public verbs spawn daemon threads (codebase convention for
    ad-hoc network work) and return immediately; the *_sync internals do the
    actual work and report results exclusively via the signals, so connected
    slots run on the GUI thread through Qt's queued delivery.
    """

    # A newer SparkyBot exists — manual check (Updates tab morphing button)
    sig_available = Signal(str, object)          # latest_version, release_data
    # A newer SparkyBot exists — silent launch check (update prompt today,
    # status-bar banner in the v2.0 shell)
    sig_launch_available = Signal(str, object)   # latest_version, release_data
    # A newer Elite Insights exists (launch check only, and only when
    # SparkyBot itself is already current)
    sig_ei_launch_available = Signal(str, str, str)  # current, latest, url
    # Manual check finished without an update to install (terminal, no error)
    sig_not_available = Signal(str)              # human-readable status
    # Stage text during checks/downloads
    sig_progress = Signal(str)
    # Update fully staged into .update_pending/ — a restart applies it
    sig_staged = Signal(str)                     # version
    # Check/download/staging failure (terminal)
    sig_error = Signal(str)
    sig_parser_status = Signal(str)
    sig_parser_ready = Signal(bool, str)

    RELEASES_LATEST_URL = "https://github.com/SimpleHonors/SparkyBot/releases/latest"
    API_LATEST_URL = "https://api.github.com/repos/SimpleHonors/SparkyBot/releases/latest"

    # Never staged over the live tree — user data bootstrap.py must not touch
    PROTECTED_PATHS = {'config.properties', 'GW2EI'}
    # Repo-only files that have no business in an install
    SKIP_PATHS = {'.github', 'CODE_OF_CONDUCT.md', 'CONTRIBUTING.md',
                  'SECURITY.md', 'LICENSE', '.gitignore'}

    def __init__(self, config, app_root: Optional[Path] = None, parent=None):
        super().__init__(parent)
        self.config = config
        # Live install tree (where main.py / bootstrap.py sit). Injectable so
        # tests can stage into a scratch directory.
        if app_root is not None:
            self._app_root = Path(app_root)
        else:
            from core.apppaths import app_dir

            self._app_root = app_dir()

    # ------------------------------------------------------------------
    # Staging locations
    # ------------------------------------------------------------------

    @property
    def app_root(self) -> Path:
        return self._app_root

    def pending_dir(self) -> Path:
        return self._app_root / '.update_pending'

    def has_pending_update(self) -> bool:
        return self.pending_dir().is_dir()

    # ------------------------------------------------------------------
    # Launch check (silent)
    # ------------------------------------------------------------------

    def repair_parser_on_launch(self):
        """Required dependency repair is independent of optional update checks."""
        threading.Thread(target=self._repair_parser_sync, daemon=True).start()

    def _repair_parser_sync(self):
        from core.ei_updater import EIUpdater
        from core.apppaths import gw2ei_dir
        updater = EIUpdater(gw2ei_dir())
        if updater.is_installed():
            self.sig_parser_ready.emit(True, "Fight-log parser is ready")
            return
        self.sig_parser_status.emit("Preparing the fight-log parser automatically...")
        try:
            success, message = updater.ensure_installed(
                lambda pct: self.sig_parser_status.emit(f"Downloading fight-log parser: {int(pct)}%"),
                retry=True)
        except Exception as exc:
            success, message = False, str(exc)
        self.sig_parser_ready.emit(success, message)

    def check_on_launch(self):
        """Check for updates on startup if enabled.

        Emits sig_launch_available / sig_ei_launch_available only — never
        progress or errors; a failed silent check is a log line, not UI.
        """
        if not self.config.check_updates_on_launch:
            return

        # If an update is already staged but not yet applied, do NOT re-check.
        # Re-checking here re-downloads + re-stages the same release every launch,
        # which is exactly what produced the infinite upgrade loop. Bootstrap applies
        # the staged update at startup; if it's still here, the apply hasn't completed
        # (e.g. files were still locked by the closing process) — a clean relaunch
        # finishes it. Either way: don't prompt or download again.
        if self.has_pending_update():
            logger.info(
                "Update already staged in .update_pending/; skipping update check. "
                "Fully close and relaunch SparkyBot to finish installing."
            )
            return

        threading.Thread(target=self._check_on_launch_sync, daemon=True).start()

    def _check_on_launch_sync(self):
        """Background body of the launch check."""
        try:
            import requests

            # Resolve the latest version WITHOUT the GitHub API. The API caps
            # unauthenticated clients at 60 req/hr/IP and returns 403 when
            # exhausted (which silently looked like "no update"). The plain
            # github.com /releases/latest endpoint just 302-redirects to the
            # newest /tag/vX.Y.Z and is NOT rate-limited, so the check keeps
            # working under throttling.
            latest = ""
            release_data = None
            try:
                r = requests.get(
                    self.RELEASES_LATEST_URL, allow_redirects=False, timeout=10
                )
                m = re.search(r"/tag/v?([0-9][0-9.]*)", r.headers.get("Location", ""))
                if m:
                    latest = m.group(1)
            except Exception as e:
                logger.debug(f"redirect version check failed: {e}")

            # Fall back to the API only if the redirect gave us nothing.
            if not latest:
                try:
                    resp = requests.get(self.API_LATEST_URL, timeout=10)
                    if resp.status_code == 200:
                        release_data = resp.json()
                        latest = release_data.get("tag_name", "").lstrip("v").strip()
                    elif resp.status_code == 403:
                        logger.info(
                            "Update check skipped: GitHub API rate limit (403). "
                            "Resets within the hour."
                        )
                except Exception as e:
                    logger.debug(f"API version check failed: {e}")

            sparkybot_needs_update = False
            if latest and parse_version(latest) > parse_version(VERSION):
                # We need the asset URL to download. Try the API for full
                # release data; if it's throttled, synthesize it from our
                # release naming convention — the download URL lives on
                # github.com (not the rate-limited API), so it still works.
                if release_data is None:
                    try:
                        resp = requests.get(self.API_LATEST_URL, timeout=10)
                        if resp.status_code == 200:
                            release_data = resp.json()
                    except Exception:
                        release_data = None
                if release_data is None:
                    release_data = {
                        "tag_name": f"v{latest}",
                        "assets": [{
                            "name": f"SparkyBot-v{latest}.zip",
                            "browser_download_url": (
                                "https://github.com/SimpleHonors/SparkyBot/"
                                f"releases/download/v{latest}/SparkyBot-v{latest}.zip"
                            ),
                        }],
                    }
                self.sig_launch_available.emit(latest, release_data)
                sparkybot_needs_update = True

            # Only check EI if SparkyBot is already up to date
            if not sparkybot_needs_update:
                from core.ei_updater import EIUpdater
                from core.gw2ei_invoker import GW2EIInvoker
                invoker = GW2EIInvoker(self.config)
                ei = EIUpdater(invoker.get_gw2ei_folder())
                available, version, url = ei.check_for_update()
                if available and url:
                    current = ei.get_current_version()
                    if not current:
                        return
                    self.sig_ei_launch_available.emit(current, version, url)

        except Exception as e:
            logger.debug(f"Launch update check failed: {e}")

    # ------------------------------------------------------------------
    # Manual check (Updates tab)
    # ------------------------------------------------------------------

    def check_now(self):
        """Interactive check: emits progress, then exactly one of
        sig_available / sig_not_available / sig_error."""
        threading.Thread(target=self._check_now_sync, daemon=True).start()

    def _check_now_sync(self):
        """Background body of the manual check."""
        try:
            import requests
            self.sig_progress.emit("Checking GitHub for updates...")

            response = requests.get(
                self.API_LATEST_URL,
                headers={"User-Agent": "SparkyBot"},
                timeout=10
            )

            if response.status_code == 404:
                self.sig_error.emit("No releases found on GitHub yet.")
                return

            if response.status_code != 200:
                self.sig_error.emit(f"GitHub API returned {response.status_code}")
                return

            data = response.json()
            # Try tag_name first (most consistent from GitHub), fall back to release name
            raw_version = data.get("tag_name", "") or data.get("name", "")
            match = re.search(r'(\d+\.\d+(?:\.\d+)*)', raw_version)
            latest_version = match.group(1) if match else ""

            # Validate it looks like a version number (digits and dots)
            if not re.match(r'^\d+\.\d+', latest_version):
                self.sig_error.emit("Could not parse version from GitHub API.")
                return

            latest_tuple = parse_version(latest_version)
            current_tuple = parse_version(VERSION)

            if latest_tuple == current_tuple:
                self.sig_not_available.emit(
                    f"You have the latest SparkyBot (v{VERSION})."
                )
                return
            if latest_tuple < current_tuple:
                # Current is newer than latest release (dev/pre-release build)
                self.sig_not_available.emit(
                    f"v{VERSION} is newer than latest release (v{latest_version})"
                )
                return

            # Update available — find the download URL
            download_url = self.resolve_download_url(data)

            # Log what we found for debugging
            logger.info(
                f"SparkyBot update: assets={len(data.get('assets', []))}, "
                f"zipball_url={data.get('zipball_url')}, download_url={download_url}"
            )

            if not download_url:
                self.sig_error.emit(
                    f"Update available: v{VERSION} → v{latest_version}\n"
                    f"Could not find download URL. Visit GitHub manually."
                )
                return

            self.sig_available.emit(latest_version, data)

        except Exception as e:
            self.sig_error.emit(f"Error: {e}")

    # ------------------------------------------------------------------
    # Download + stage
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_download_url(release_data: dict) -> Optional[str]:
        """Pick the release artifact to install: first .zip asset, else zipball."""
        assets = (release_data or {}).get("assets", [])
        download_url = None
        for asset in assets:
            if asset.get("name", "").endswith(".zip"):
                download_url = asset.get("browser_download_url")
                break
        if not download_url:
            download_url = (release_data or {}).get("zipball_url")
        if not download_url or download_url == "None":
            return None
        return download_url

    def start_update(self, release_data: dict, version: str):
        """Resolve the download URL from release data and stage the update."""
        url = self.resolve_download_url(release_data)
        if not url:
            logger.warning("No download URL found for SparkyBot update")
            self.sig_error.emit("No download URL found for SparkyBot update")
            return
        self.download_and_stage(url, version)

    def download_and_stage(self, url: str, version: str):
        """Download the release zip and stage it into .update_pending/."""
        threading.Thread(
            target=self._download_and_stage_sync, args=(url, version), daemon=True
        ).start()

    def _download_and_stage_sync(self, url: str, version: str):
        """Background body of download + staging."""
        try:
            import requests
            import shutil
            import tempfile
            import zipfile

            logger.info(f"Starting SparkyBot update download from: {url}")
            self.sig_progress.emit("Downloading update...")

            # Download to temp file
            response = requests.get(url, stream=True, timeout=60)
            response.raise_for_status()

            with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
                tmp_path = Path(tmp.name)
                for chunk in response.iter_content(chunk_size=8192):
                    tmp.write(chunk)

            self.sig_progress.emit("Staging update...")

            # --- STAGE the update; do NOT overwrite live files here ---
            # On Windows (and especially over a network share), the OS locks the
            # .py files the running app has already imported, so an in-place
            # overwrite of main.py / core/*.py fails with PermissionError. Instead
            # we extract into a `.update_pending/` staging folder (always writable
            # — these files aren't loaded), and bootstrap.py applies it on the next
            # launch, BEFORE importing the app, when nothing is locked.
            staging = self.pending_dir()
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)

            staged = 0
            with zipfile.ZipFile(tmp_path, 'r') as zf:
                names = zf.namelist()
                logger.info(f"Zip contains {len(names)} entries; staging to {staging}")
                for member in names:
                    # Strip the zip's top-level directory entry
                    parts = member.split('/', 1)
                    if len(parts) < 2 or not parts[1]:
                        continue
                    relative_path = parts[1]
                    top_level = relative_path.split('/')[0]
                    if top_level in self.PROTECTED_PATHS:
                        continue  # never stage over user config/data
                    if top_level in self.SKIP_PATHS or member in self.SKIP_PATHS:
                        continue  # repo-only files
                    if member.endswith('/'):
                        continue
                    target = staging / relative_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target, 'wb') as dst:
                        dst.write(src.read())
                    staged += 1

            logger.info(f"Staged {staged} files to {staging}; will apply on next launch")
            tmp_path.unlink()

            self.sig_staged.emit(version)

        except Exception as e:
            logger.error(f"SparkyBot update failed: {e}")
            self.sig_error.emit(f"Update failed: {e}")
