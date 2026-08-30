# Setup: Python Dependencies

A setup-wizard screen that appears **only when you run SparkyBot from source code** (a Python checkout). The installed SparkyBot program bundles everything it needs, so this screen never appears for the normal download.

SparkyBot requires certain Python packages to run. This screen checks for them and installs anything missing.

## What's on this screen

- **Check & Install Dependencies** — the one button. When the page opens, a check runs automatically and shows what is already installed and what is missing; clicking the button then installs the missing packages. When everything is present the button reads **All Dependencies Installed** and turns off.
- **Status text** — shows the result: "All dependencies are installed", "N missing package(s) found…", "Installing N package(s)...", or an error.
- **Details list** — an "Installed:" / "Missing:" line for each package, so you can see exactly what was found.

## Common problems

- **"requirements.txt was not found"** — you're running from a folder that isn't a complete SparkyBot source checkout. Get the full source, or use the installed release instead.
- **"Installation failed"** — the error text below the status shows what pip reported. Usually a network problem or a permissions problem; try again, or run `pip install -r requirements.txt` yourself in the same Python environment.
- **"Installation timed out after 120 seconds"** — slow connection or a very large package. Click the button again; already-installed packages are skipped.
