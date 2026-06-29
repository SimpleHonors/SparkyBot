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
    import stat
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

    def _force_writable(p):
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass

    def _apply_one(src, dst):
        """Replace dst with src, even if dst is locked/held by another process.

        On Windows network shares the SMB *server* shows no lock, yet a plain
        truncate-overwrite (shutil.copy2 -> open(dst,'wb')) still fails with
        PermissionError — the block is client-side (Defender real-time scan, the
        SMB redirector, or a read-only attribute). The robust technique is to
        rename the in-use file out of the way (a directory metadata op that
        succeeds even while the file is open) and then write the new file fresh.
        """
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            _force_writable(dst)
        # Fast path: straight copy when the file isn't held.
        try:
            shutil.copy2(src, dst)
            return True
        except PermissionError:
            pass
        # Held/locked: move the old file aside, then create a fresh one.
        try:
            if dst.exists():
                aside = dst.with_name(dst.name + ".old_update")
                try:
                    if aside.exists():
                        _force_writable(aside)
                        aside.unlink()
                except OSError:
                    pass
                os.replace(dst, aside)   # rename the open file out of the way
            shutil.copy2(src, dst)       # dst path is now free
            return True
        except (PermissionError, OSError):
            return False

    # A couple of retry rounds still help for a genuinely transient overlap.
    remaining = list(work)
    applied = 0
    MAX_ROUNDS = 12  # ~12s worst case
    for attempt in range(MAX_ROUNDS):
        still_locked = []
        for src, dst in remaining:
            try:
                if _apply_one(src, dst):
                    applied += 1
                else:
                    still_locked.append((src, dst))
            except Exception as e:
                print(f"WARNING: could not apply {dst.name}: {e}")
        remaining = still_locked
        if not remaining:
            break
        if attempt < MAX_ROUNDS - 1:
            print(f"  {len(remaining)} file(s) still held; retrying "
                  f"(try {attempt + 1}/{MAX_ROUNDS})...")
            time.sleep(1.0)

    # Best-effort cleanup of the renamed-aside originals.
    for old in app_dir.rglob("*.old_update"):
        try:
            _force_writable(old)
            old.unlink()
        except OSError:
            pass

    if remaining:
        # Keep .update_pending so a clean relaunch finishes the job. Do NOT
        # delete it — that is what triggers a re-download loop.
        print(f"WARNING: {len(remaining)} file(s) could not be replaced; update NOT "
              f"fully applied.\nClose SparkyBot completely and relaunch to finish.")
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
