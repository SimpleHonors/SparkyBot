import sys
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True

"""Bootstrap script for SparkyBot.

Checks and installs Python dependencies BEFORE importing anything
that requires them. Uses only Python standard library.
This file is the new entry point: python bootstrap.py
"""

import subprocess
import sys
import importlib.metadata
from pathlib import Path


def apply_pending_update():
    """Apply a staged update (.update_pending/) BEFORE any app module is imported.

    The in-app updater downloads new files into .update_pending/ instead of
    overwriting live files, because the OS (Windows, and especially network
    shares) locks the .py files the running app has imported. Here at startup
    nothing app-side is imported yet, so the swap always succeeds.
    """
    import shutil
    import time
    app_dir = Path(__file__).parent
    pending = app_dir / ".update_pending"
    if not pending.is_dir():
        return

    # Build the work list first so we can retry the stragglers.
    work = []
    for src in pending.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(pending)
        # Never replace the running bootstrap script or protected user files
        if rel.parts[0] in ("bootstrap.py", "config.properties", "GW2EI"):
            continue
        work.append((src, app_dir / rel))

    if not work:
        shutil.rmtree(pending, ignore_errors=True)
        return

    print("Applying pending SparkyBot update...")

    # On Windows + network shares, os.execv leaves the OLD process briefly alive
    # while the new one starts, so it still locks main.py / core/*.py for a moment.
    # A short initial wait + per-file retry waits that dying process out instead of
    # failing on the first PermissionError (which is what caused the upgrade loop).
    time.sleep(1.0)
    remaining = list(work)
    applied = 0
    MAX_ROUNDS = 20  # ~20s total worst case
    for attempt in range(MAX_ROUNDS):
        still_locked = []
        for src, dst in remaining:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                applied += 1
            except PermissionError:
                still_locked.append((src, dst))
            except Exception as e:
                # Non-lock error: don't spin on it, just report and skip.
                print(f"WARNING: could not apply {dst.name}: {e}")
        remaining = still_locked
        if not remaining:
            break
        if attempt < MAX_ROUNDS - 1:
            print(f"  {len(remaining)} file(s) still locked by the closing app; "
                  f"waiting (try {attempt + 1}/{MAX_ROUNDS})...")
            time.sleep(1.0)

    if remaining:
        # Keep .update_pending so a clean manual relaunch (old process fully dead)
        # finishes the job. Do NOT delete it — that is what triggers a re-download loop.
        print(f"WARNING: {len(remaining)} file(s) still locked; update NOT fully applied.\n"
              f"Fully close SparkyBot and relaunch to finish installing.")
    else:
        shutil.rmtree(pending, ignore_errors=True)
        print(f"Update applied: {applied} files updated. Continuing startup...\n")


def check_and_install():
    """Check requirements.txt and install missing packages."""
    req_file = Path(__file__).parent / "requirements.txt"
    if not req_file.exists():
        print("requirements.txt not found, skipping dependency check")
        return True

    missing = []
    for line in req_file.read_text().strip().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        pkg_name = line.split('>=')[0].split('==')[0].split('<')[0].split('>')[0].strip()
        try:
            importlib.metadata.version(pkg_name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(line)

    if not missing:
        return True

    print(f"Missing {len(missing)} required package(s): {', '.join(missing)}")
    print("Installing...")

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install"] + missing,
        capture_output=False,  # Show pip output in console
        timeout=180
    )

    if result.returncode != 0:
        print("\nERROR: Failed to install dependencies.")
        print("Try running manually: pip install -r requirements.txt")
        input("Press Enter to exit...")
        return False

    print("All dependencies installed successfully.\n")
    return True


if __name__ == "__main__":
    # Apply any staged update FIRST — before importing app modules, while
    # nothing is locked (works on local disks AND network shares).
    apply_pending_update()

    if not check_and_install():
        sys.exit(1)

    # Now it's safe to import and run the real app
    from main import main
    main()
