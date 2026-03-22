"""Bootstrap script for SparkyBot.

Checks and installs Python dependencies BEFORE importing anything
that requires them. Uses only Python standard library.
This file is the new entry point: python bootstrap.py
"""

import subprocess
import sys
import importlib.metadata
from pathlib import Path


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
    if not check_and_install():
        sys.exit(1)

    # Now it's safe to import and run the real app
    from main import main
    main()
