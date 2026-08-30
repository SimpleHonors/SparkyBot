# Setup: Install the fight-log parser

A setup-wizard screen. SparkyBot needs GW2 Elite Insights (a free companion program) to turn game logs into readable reports. This screen installs it for you — no paths or technical choices are needed.

## What's on this screen

### Automatic setup (recommended)

- **Install Fight-Log Parser** — downloads the latest GW2 Elite Insights from GitHub and installs it in the SparkyBot program folder. A progress bar shows the download; on success the status reads "GW2EI v… installed successfully".
- If the parser is already installed, the button reads **Update GW2 Elite Insights** (when a newer version exists) or **Re-download GW2 Elite Insights** (when you're up to date), and the status line tells you which version you have.

### I already have the parser (advanced)

Click this toggle only if you want to point SparkyBot at a copy of Elite Insights you already have:

- **Path box** — the location of your existing `GuildWars2EliteInsights-CLI.exe`.
- **Browse...** — pick the file instead of typing the path.

Leave this blank after using automatic setup.

## Common problems

- **"The fight-log parser is required."** — you clicked Next without installing. Click **Install Fight-Log Parser**, or open the advanced section and choose an existing copy.
- **"Download failed: …"** — usually no internet connection or GitHub is briefly unavailable. Check your connection and click the button again.
- **You pointed at the wrong file** — the parser is the file named `GuildWars2EliteInsights-CLI.exe`, not the game itself. Use **Browse...** and pick that exact file, or just use the automatic install instead.
- **Updating later** — you never need to re-run setup for parser updates. **Settings → Updates** has a **Check for Elite Insights Update** button, and the wizard's install keeps your settings.
