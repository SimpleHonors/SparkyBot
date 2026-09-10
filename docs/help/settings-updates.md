# Settings: Updates

Keeping SparkyBot and the Elite Insights parser current. Opening this page runs the version checks (they never run behind your back at dialog-open elsewhere).

## What's on this page

- **Check for updates on launch** — the same automatic-check switch as on the Application page: look for new versions each time the app starts. Found updates show as a quiet status-bar button — nothing installs itself.

### SparkyBot

- **Current Version** / **Latest Version** — what you're running versus the newest release on GitHub.
- **Check for SparkyBot Update** — checks now. When an update exists the button becomes **Download & Install Update**; after installing it reads **Restart Required** — restart SparkyBot to finish. When you're current it reads **Already Up to Date**.

### Elite Insights Parser

- **Installed Version** / **Latest Version** — your parser version versus the newest release.
- **Check for Elite Insights Update** — checks and, if newer, downloads and installs the parser into SparkyBot's GW2EI folder. Your settings are preserved.

## Common problems

- **"Checking GitHub..." never resolves / errors** — no internet or GitHub briefly unavailable. Try the check button again later.
- **Installed an update but the version didn't change** — restart SparkyBot; the **Restart Required** button means the new version loads on next launch.
- **"GuildWars2EliteInsights-CLI.exe not found" / "Not installed" in an older version** — update SparkyBot and restart. SparkyBot automatically downloads the correct parser into its own folder and retries in the background if the connection fails. No separate parser setup is needed.
- **Worried an update will wipe your setup** — parser updates keep your settings ("Settings preserved"), and SparkyBot updates never touch your configuration.
