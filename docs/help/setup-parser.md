# Setup: Install the fight-log parser

A setup-wizard screen. SparkyBot needs GW2 Elite Insights (a free companion program) to turn game logs into readable reports. This screen installs it for you — no paths or technical choices are needed.

## What's on this screen

### Automatic setup

- Opening this screen automatically downloads the required parser into the SparkyBot program folder. No file or folder selection is needed. Setup checks the installed files before allowing you to continue.
- **Install Fight-Log Parser** — retries the download if the first attempt failed. A progress bar shows the download.
- If the parser is already installed, the button reads **Update GW2 Elite Insights** (when a newer version exists) or **Re-download GW2 Elite Insights** (when you're up to date), and the status line tells you which version you have.

## Common problems

- **"The fight-log parser is required."** — wait for installation to finish. If it failed, click **Install Fight-Log Parser** to retry.
- **"Download failed: …"** — usually no internet connection or GitHub is briefly unavailable. Check your connection and click the button again.
- **An older SparkyBot install cannot find the parser** — update SparkyBot and restart. It repairs the missing parser automatically, without changing your combat logs.
- **Updating later** — you never need to re-run setup for parser updates. **Settings → Updates** has a **Check for Elite Insights Update** button, and the wizard's install keeps your settings.
