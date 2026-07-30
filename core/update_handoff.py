"""Launch the dedicated frozen updater from outside the install directory."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable


def launch_frozen_update_helper(
    *,
    app_dir: Path,
    executable: Path,
    process_id: int,
    app_args: Iterable[str] = (),
    temp_dir: Path | None = None,
) -> bool:
    app_dir = Path(app_dir).resolve()
    executable = Path(executable).resolve()
    source_helper = app_dir / "SparkyBotUpdater.exe"
    if not source_helper.is_file():
        return False

    destination_dir = Path(temp_dir) if temp_dir is not None else Path(tempfile.gettempdir())
    destination_dir.mkdir(parents=True, exist_ok=True)
    detached_helper = destination_dir / "SparkyBotUpdater.exe"
    shutil.copy2(source_helper, detached_helper)

    command = [
        str(detached_helper),
        "--app-dir",
        str(app_dir),
        "--executable",
        str(executable),
        "--wait-pid",
        str(process_id),
        "--app-args",
        *list(app_args),
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    subprocess.Popen(
        command,
        cwd=str(destination_dir),
        creationflags=creationflags,
        close_fds=True,
    )
    return True
