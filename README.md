<<<<<<< HEAD
# SparkyBot
SparkyBot - A Python based WvW Discord Bot
=======
# SparkyBot v1.1.2

Guild Wars 2 fight log monitoring and Discord reporter. Monitors ArcDPS log folders and sends formatted fight reports to Discord via webhooks.

## Features

- **System Tray Integration** - Runs in background with system tray icon
- **Full GUI Settings** - Tabbed settings window with all configuration options
- **Network Share Support** - Polling fallback for monitoring over SMB/CIFS shares
- **Discord Webhook Reports** - Rich formatted fight reports matching MzFightReporter style
- **GW2EI Parsing** - Uses Guild Wars 2 Elite Insights CLI for log parsing
- **Auto-Update** - Built-in Elite Insights update feature (preserves Settings)

## Requirements

- Python 3.9+
- PyQt6 (for GUI and system tray)
- watchdog (for file watching)
- requests (for Discord webhooks)
- .NET 8.0 Runtime (for GW2EI)
- Guild Wars 2 Elite Insights CLI

## Quick Start

### GUI Mode (with system tray)
```bash
python main.py
```

### Headless CLI Mode
```bash
python main.py --headless
```

## Configuration

### Settings Window Tabs

| Tab | Description |
|-----|-------------|
| Discord | Webhook URLs (primary, secondary, tertiary), labels, guild icon picker, embed color picker |
| Paths | Log folder path (with Browse button), GW2EI executable name |
| Thresholds | Min fight duration, min downs, min total damage |
| Display | Toggle various combat stat sections |
| Behavior | Close/minimize to tray, memory limits, start watcher on startup |
| Updates | Check and install Elite Insights updates |
| About | Version info, credits |

### Command Line Options

| Option | Description |
|--------|-------------|
| `--verbose`, `-v` | Enable debug logging |
| `--headless` | Run without GUI (CLI only) |
| `--config PATH` | Custom config file path |

## Architecture

```
SparkyBot/
├── main.py                 # Entry point (PyQt6 app)
├── requirements.txt        # Dependencies
├── config.properties      # Configuration file
├── SparkyBot.bat          # Windows launcher
├── GW2EI/                 # Elite Insights parser (auto-updated)
├── core/
│   ├── __init__.py
│   ├── config.py          # Configuration management (INI format)
│   ├── file_watcher.py    # File watching (OS-native + polling fallback)
│   ├── discord_bot.py     # Discord webhook client
│   ├── fight_report.py    # GW2EI JSON → Discord embed formatter
│   ├── gw2ei_invoker.py   # GW2EI CLI wrapper
│   ├── ei_updater.py      # Elite Insights auto-updater
│   ├── tray_manager.py    # System tray integration
│   └── gui_settings.py    # PyQt6 settings window
└── README.md
```

## File Watching

SparkyBot uses efficient OS-native file events via `watchdog` when monitoring local folders. For network shares (mapped drives like `Z:` or UNC paths like `\\server\share`), it automatically falls back to polling mode since OS events don't work reliably over SMB.

- **Local folders**: Uses watchdog Observer for instant detection. Stability checks run on background threads so the watchdog event thread stays responsive.
- **Network shares**: Uses PollingFileWatcher with 5-second interval. Files seen are tracked; entries are only evicted when the file no longer exists on disk, preventing silent reprocessing.

Files are only processed once — existing files when the watcher starts are skipped. Background stability-check threads are tracked and joined on watcher stop.

## Discord Reports

Fight reports include:
- Map name with themed icon (author circle uses SparkyBot hosted icon; upper-right thumbnail uses user-selected guild icon)
- Commander, duration, time, recorded by
- ArcDPS and Elite Insights versions
- Squad Summary (total damage, DPS, downs, kills)
- Enemy Summary
- Damage & Down Contribution (top 10)
- Burst Damage, Strips, Cleanses, Heals, Defense, CCs, Downs/Kills
- Quick Report overview (KDR, Squad/Enemy downs/kills/deaths)

Reports are split across multiple Discord embeds if needed. Embed accent color is configurable (Jade Green default).

## Elite Insights Integration

SparkyBot downloads and manages GW2 Elite Insights CLI automatically. When updating:
- Settings folder is preserved (wvwupload.conf, etc.)
- Only executable files are replaced
- Check for updates via Settings → Updates tab

## Known Limitations

- Duplicate file names (e.g., `file.evtc` and `file - Copy.evtc`) are treated as separate files if they exist simultaneously
- GW2EI parsing on very large logs may take 60+ seconds
- Network share polling has 5-second latency

## History

SparkyBot is a Python port of [MzFightReporter](https://github.com/Swedemon/MzFightReporter) by Swedemon, rewritten for easier deployment and cross-platform compatibility.

## License

MIT License
>>>>>>> 8b0f113 (Initial commit)
