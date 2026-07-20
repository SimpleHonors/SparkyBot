"""Filesystem anchors that stay correct in dev and PyInstaller-frozen runs."""

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Directory user-visible files live in: config, state, caches.

    Frozen: the folder containing SparkyBot.exe.
    Dev: the repo root.
    """
    if is_frozen():
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """Directory bundled read-only data lives in (prompts/, assets/, GW2EI/).

    Frozen: PyInstaller's _MEIPASS/_internal.
    Dev: same as app_dir().
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return app_dir()


def local_machine_dir() -> Path:
    """Directory for machine-local writable data (avoids SMB/UNC shares).

    Resolution order:
      1. %LOCALAPPDATA%/SparkyBot (Windows standard)
      2. ~/.sparkybot/           (Linux / macOS fallback)
      3. app_dir()               (dev fallback — repo root)
    """
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        return Path(localappdata) / "SparkyBot"
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return Path(home) / ".sparkybot" if home else app_dir()


def gw2ei_dir() -> Path:
    """Directory the GW2EI folder lives in.

    Frozen: app_dir()/GW2EI if it exists there (the writable copy placed
    next to the exe by the build), otherwise the bundled copy under
    _MEIPASS/GW2EI.  Dev: same as bundle_dir().
    """
    candidate = app_dir() / "GW2EI"
    if is_frozen() and candidate.exists():
        return candidate
    return bundle_dir() / "GW2EI"


def no_window_kwargs() -> dict:
    """subprocess kwargs that stop child consoles flashing up on Windows.

    The frozen app is a windowed exe, so every spawned console program
    (GW2EI, the combiner, pip) opens its own console window unless
    CREATE_NO_WINDOW is set. No-op on other platforms.
    """
    import subprocess
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}
