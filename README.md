# SparkyBot

**A Guild Wars 2 WvW fight log reporter for Discord.**

SparkyBot monitors your ArcDPS combat log folder, automatically parses new WvW fight logs using [GW2 Elite Insights](https://github.com/baaron4/GW2-Elite-Insights-Parser), and posts rich, detailed fight reports to your Discord server via webhooks — all without leaving the game.

---

## What It Does

After a WvW fight ends and ArcDPS writes a log file, SparkyBot picks it up within seconds and delivers a full combat report to Discord including:

- **Quick Report** — KDR, duration, squad/enemy downs, kills, and deaths at a glance
- **Squad & Enemy Summaries** — player counts by team color, total damage, DPS, downs, deaths
- **Damage & Down Contribution** — top 10 damage dealers with profession and downed damage
- **Burst Damage** — best 4-second and 2-second burst windows per player
- **Strips & Cleanses** — boon removal and condition cleanse leaderboards
- **Heals & Barrier** — healing output, barrier, and downed healing (requires ArcDPS healing addon)
- **Defense** — invulns, evades, and blocks
- **Outgoing CCs & Interrupts** — hard CC, soft CC, immobilize, and interrupt counts
- **Outgoing Downs & Kills** — who secured the most downs and kills
- **Boon Uptime by Party** — defensive and offensive boon uptime percentages per subgroup
- **Enemy Top Damage Skills** — what hit your squad the hardest
- **Enemy Breakdown** — enemy composition by profession and team color

Reports are formatted with uniform-width code blocks, team color detection (Red/Blue/Green), and a configurable guild icon and embed accent color.

---

## Setup

### Prerequisites

1. **Python 3.9 or newer** — download from [python.org](https://www.python.org/downloads/)
   - **Important:** during installation, check the box that says **"Add Python to PATH"**
2. **.NET 8.0 Runtime** — download from [Microsoft](https://dotnet.microsoft.com/en-us/download/dotnet/8.0) (required by GW2 Elite Insights)
3. **ArcDPS** — install in your Guild Wars 2 folder ([deltaconnected.com/arcdps](https://www.deltaconnected.com/arcdps/))

### Installation

1. Download SparkyBot from the [Releases page](https://github.com/SimpleHonors/SparkyBot/releases)
2. Extract the zip to any folder (e.g., `C:\SparkyBot`)
3. Double-click **SparkyBot.bat**

That's it. On first launch, SparkyBot will:
- Install any missing Python packages automatically
- Walk you through a setup wizard to configure everything
- Start monitoring for fight logs

### Creating a Discord Webhook

1. Open Discord and go to the channel where you want fight reports posted
2. Click the gear icon (Edit Channel) → **Integrations** → **Webhooks**
3. Click **New Webhook**, give it a name, and click **Copy Webhook URL**
4. Paste the URL into SparkyBot's setup wizard or Settings → Discord tab

### First-Run Wizard

The setup wizard walks you through four steps:

1. **Dependencies** — verifies and installs required Python packages
2. **GW2 Elite Insights** — downloads the parser automatically, or lets you point to an existing installation
3. **Log Folder** — auto-detects your ArcDPS log folder, or lets you browse manually
4. **Discord Webhook** — paste your webhook URL

After the wizard completes, click **Start Watcher** and you're done. SparkyBot minimizes to your system tray and posts fight reports automatically.

---

## Configuration

All settings are accessible through the GUI (right-click the system tray icon → Settings).

### Discord
| Setting | Description |
|---------|-------------|
| Primary / Secondary / Tertiary Webhook | Up to three Discord webhook URLs |
| Active Webhook | Which webhook receives reports |
| Webhook Label | Display name for the bot in Discord |
| Guild Icon | Local image shown as the embed thumbnail |
| Embed Color | Accent color for the embed sidebar |

### Paths
| Setting | Description |
|---------|-------------|
| Log Folder | Path to your ArcDPS WvW log folder |
| CLI Executable | Path to GW2EI CLI (auto-detected if installed via wizard) |

### Thresholds
| Setting | Description |
|---------|-------------|
| Min Fight Duration | Ignore fights shorter than this (seconds) |
| Min Fight Downs | Ignore fights with fewer total downs |
| Min Fight Total DMG | Ignore fights below this damage threshold |
| Max Upload Size | Maximum log file size to process (MB) |

### Display

Toggle individual report sections on or off: Quick Report, Damage, Heals, Defense, CCs, Cleanses, Downs/Kills, Burst Damage, Enemy Skills, Boon Uptimes, Enemy Breakdown.

### Behavior
| Setting | Description |
|---------|-------------|
| Close to System Tray | Closing the window hides to tray instead of quitting |
| Minimize to System Tray | Minimizing hides to tray |
| Start Minimized | Launch directly to system tray |
| Start Watcher on Startup | Begin monitoring as soon as SparkyBot opens |

### Updates

Check for and install updates to both SparkyBot and GW2 Elite Insights from the Settings → Updates tab. Elite Insights updates preserve your settings automatically.

---

## Command Line Options

```
python bootstrap.py [options]
```

| Option | Description |
|--------|-------------|
| `--verbose`, `-v` | Enable debug logging |
| `--headless` | Run without GUI (CLI mode, no system tray) |
| `--config PATH` | Use a custom config file path |

---

## How It Works

### File Watching
- **Local folders** — uses OS-native file system events for instant detection
- **Network shares** — automatically falls back to polling (5-second interval) since OS events don't work reliably over SMB/CIFS

Files are only processed once. Existing files when the watcher starts are skipped. New files are held until they stop growing before parsing begins.

### Parsing
SparkyBot invokes GW2 Elite Insights CLI to parse ArcDPS `.evtc` log files into detailed JSON. The JSON is then formatted into Discord embeds with code-block tables for consistent rendering across desktop and mobile.

### Discord Delivery
Reports are split across multiple Discord embeds using the fields API for uniform width. Embeds are batched to stay within Discord's API limits (6000 characters per embed, 10 embeds per message). The guild icon is attached as a local file; the author icon is hosted externally.

---

## Project Structure

```
SparkyBot/
├── bootstrap.py            # Entry point — installs deps, launches app
├── main.py                 # PyQt6 application
├── version.py              # Single source of truth for version number
├── requirements.txt        # Python dependencies
├── config.properties       # User configuration (auto-generated)
├── sbtray.png              # System tray and author icon
├── SparkyBot.bat           # Windows launcher
├── GW2EI/                  # Elite Insights parser (auto-managed)
│   └── Settings/
│       └── wvwupload.conf  # GW2EI parse settings
├── core/
│   ├── config.py           # Configuration management
│   ├── file_watcher.py     # File monitoring (native + polling)
│   ├── discord_bot.py      # Discord webhook client with batching
│   ├── fight_report.py     # GW2EI JSON → Discord embed formatter
│   ├── gw2ei_invoker.py    # GW2EI CLI wrapper
│   ├── ei_updater.py       # Elite Insights auto-updater
│   ├── tray_manager.py     # System tray integration
│   ├── gui_settings.py     # PyQt6 settings window
│   └── setup_wizard.py     # First-run setup wizard
└── README.md
```

---

## Troubleshooting

**SparkyBot doesn't detect new log files**
- Make sure the watcher is running (the button should say "Stop Watcher" in red)
- Verify the log folder path points to the correct ArcDPS subfolder (usually a numbered folder like `1` inside `arcdps.cbtlogs`)
- For network shares, expect up to 5 seconds of latency

**GW2EI parsing fails or times out**
- Very large logs (50+ players, 20+ minutes) can take over 60 seconds
- Make sure .NET 8.0 Runtime is installed
- Check the Updates tab to make sure GW2EI is current

**Discord reports aren't posting**
- Verify your webhook URL is correct and the webhook hasn't been deleted in Discord
- Check that "Enable Discord Bot" is checked in Settings → Discord
- Look at the SparkyBot console or log output for specific error messages

**Reports show "Enemy" instead of team colors (Red/Blue/Green)**
- This means a new WvW team ID was encountered that isn't in the mapping table yet
- Check the log for "Unmapped teamID" warnings and report them on GitHub so they can be added

**"Add Python to PATH" wasn't checked during Python install**
- Reinstall Python and make sure to check the PATH checkbox, or
- Manually add Python to your system PATH environment variable

---

## Built on the Shoulders of Giants

SparkyBot wouldn't exist without these incredible community projects:

- **[MzFightReporter](https://github.com/Swedemon/MzFightReporter)** by Swedemon — the original Java WvW fight reporter that inspired this project. SparkyBot's report format, field parsing, and team ID mapping are all derived from MzFightReporter's codebase.

- **[GW2 Elite Insights](https://github.com/baaron4/GW2-Elite-Insights-Parser)** by baaron4 — the parser that powers all log analysis. Elite Insights transforms raw ArcDPS `.evtc` files into rich JSON data that SparkyBot formats for Discord.

- **[ArcDPS](https://www.deltaconnected.com/arcdps/)** by deltaconnected — the combat logging addon for Guild Wars 2 that makes all of this possible.

SparkyBot is a community-built alternative for users who prefer a Python-based solution with a focus on reliability and ease of deployment.

---

## License

MIT License

Copyright (c) 2025-2026 SimpleHonors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

*SparkyBot is not affiliated with or endorsed by ArenaNet, NCSOFT, or any of their partners. Guild Wars 2 and all associated logos and designs are trademarks or registered trademarks of NCSOFT Corporation. All third-party trademarks are the property of their respective owners.*
