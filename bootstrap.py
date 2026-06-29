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
    app_dir = Path(__file__).parent
    pending = app_dir / ".update_pending"
    if not pending.is_dir():
        return
    try:
        print("Applying pending SparkyBot update...")
        count = 0
        for src in pending.rglob("*"):
            if src.is_dir():
                continue
            rel = src.relative_to(pending)
            # Never replace the running bootstrap script or protected user files
            if rel.parts[0] in ("bootstrap.py", "config.properties", "GW2EI"):
                continue
            dst = app_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            count += 1
        shutil.rmtree(pending, ignore_errors=True)
        print(f"Update applied: {count} files updated. Continuing startup...\n")
    except Exception as e:
        print(f"WARNING: could not apply pending update: {e}")


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
