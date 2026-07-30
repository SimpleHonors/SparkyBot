"""Standalone staged-update applier.

This module is also frozen as ``SparkyBotUpdater.exe``.  The updater runs from
the Windows temp directory so every file in the installed application,
including ``SparkyBot.exe`` and the bundled Python runtime, is closed before it
is replaced.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable


PROTECTED_ROOTS = {"config.properties", "GW2EI"}


def _pending_files(pending_dir: Path) -> Iterable[tuple[Path, Path]]:
    for source in pending_dir.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(pending_dir)
        if not relative.parts or relative.parts[0] in PROTECTED_ROOTS:
            continue
        if any(part in ("", ".", "..") for part in relative.parts):
            raise ValueError(f"Unsafe staged update path: {relative}")
        yield source, relative


def _make_writable(path: Path) -> None:
    try:
        path.chmod(stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _replace_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".sparkybot-new")
    try:
        if temporary.exists():
            _make_writable(temporary)
            temporary.unlink()
        shutil.copy2(source, temporary)
        if destination.exists():
            _make_writable(destination)
        os.replace(temporary, destination)
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


def apply_pending_tree(
    app_dir: Path,
    pending_dir: Path,
    *,
    attempts: int = 120,
    retry_delay: float = 0.5,
) -> int:
    """Atomically overlay a complete staged runtime onto a closed install."""

    app_dir = Path(app_dir).resolve()
    pending_dir = Path(pending_dir).resolve()
    expected_pending = app_dir / ".update_pending"
    if pending_dir != expected_pending:
        raise ValueError(f"Pending directory must be {expected_pending}")
    if not pending_dir.is_dir():
        return 0

    remaining = [(source, app_dir / relative) for source, relative in _pending_files(pending_dir)]
    applied = 0
    for attempt in range(max(1, attempts)):
        retry = []
        for source, destination in remaining:
            try:
                _replace_file(source, destination)
                applied += 1
            except OSError:
                retry.append((source, destination))
        remaining = retry
        if not remaining:
            break
        if attempt < attempts - 1 and retry_delay:
            time.sleep(retry_delay)

    if remaining:
        names = ", ".join(str(destination.relative_to(app_dir)) for _, destination in remaining[:8])
        raise RuntimeError(f"Could not replace {len(remaining)} update file(s): {names}")

    shutil.rmtree(pending_dir)
    return applied


def wait_for_process_exit(process_id: int, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(process_id, 0)
        except OSError:
            return
        time.sleep(0.25)


def _launch_application(executable: Path, app_dir: Path, app_args: list[str]) -> None:
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        [str(executable), *app_args],
        cwd=str(app_dir),
        creationflags=creationflags,
        close_fds=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", required=True, type=Path)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--wait-pid", required=True, type=int)
    parser.add_argument("--app-args", nargs=argparse.REMAINDER, default=[])
    args = parser.parse_args(argv)

    log_path = Path(tempfile.gettempdir()) / "SparkyBot-update.log"
    try:
        wait_for_process_exit(args.wait_pid)
        applied = apply_pending_tree(args.app_dir, args.app_dir / ".update_pending")
        log_path.write_text(f"Update applied successfully: {applied} files.\n", encoding="utf-8")
        _launch_application(args.executable, args.app_dir, args.app_args)
        return 0
    except Exception as exc:
        log_path.write_text(f"Update failed: {exc!r}\n", encoding="utf-8")
        return 1


if __name__ == "__main__":
    sys.exit(main())
