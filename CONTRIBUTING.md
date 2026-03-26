# Contributing to SparkyBot

Thanks for your interest in contributing to SparkyBot! This project is maintained by a small team and we welcome bug reports, feature suggestions, and code contributions from the GW2 WvW community.

## Ways to Contribute

### Report Bugs

Found something broken? [Open a bug report](https://github.com/SimpleHonors/SparkyBot/issues/new?template=bug_report.yml) using the issue template. The more detail you provide (version, logs, steps to reproduce), the faster we can fix it.

Run SparkyBot with `--verbose` to capture detailed logs:
```
python bootstrap.py --verbose
```

**Always redact API keys, webhook URLs, and tokens before pasting logs.**

### Report Unmapped Team IDs

See "Unmapped teamID" in the console? [Report it](https://github.com/SimpleHonors/SparkyBot/issues/new?template=unmapped_team_id.yml) so we can add the mapping. Include the team ID number and the expected color (Red/Blue/Green).

### Suggest Features

Have an idea? [Open a feature request](https://github.com/SimpleHonors/SparkyBot/issues/new?template=feature_request.yml). We're especially interested in:

- New report sections or stats
- AI prompt improvements that produce better commentary
- Quality-of-life improvements for the GUI
- Integration ideas (DPS Reports, other services)

### Contribute Code

We accept pull requests! Here's how:

1. **Fork the repository** and clone your fork locally
2. **Create a branch** for your change: `git checkout -b feature/your-feature-name`
3. **Make your changes** — see the development setup below
4. **Test your changes** by processing at least one `.evtc` file end-to-end
5. **Commit** with a clear message describing what you changed and why
6. **Push** to your fork and open a pull request against `main`

### Share AI Prompts

If you've tuned the system prompt to produce better WvW commentary for your guild, share it! Open a feature request describing what you changed and why it works well. The best improvements may get incorporated into the default prompt.

## Development Setup

### Prerequisites

- Python 3.9 or newer
- Guild Wars 2 with ArcDPS installed (for generating test logs)
- GW2 Elite Insights CLI (SparkyBot can auto-install this)

### Getting Started

```bash
git clone https://github.com/YOUR_USERNAME/SparkyBot.git
cd SparkyBot
pip install -r requirements.txt
python bootstrap.py --verbose
```

### Project Structure

```
SparkyBot/
├── bootstrap.py            # Entry point — installs deps, launches app
├── main.py                 # PyQt6 application, file processing pipeline
├── core/
│   ├── version.py          # Version number (single source of truth)
│   ├── config.py           # Configuration management
│   ├── file_watcher.py     # Log file monitoring
│   ├── fight_report.py     # GW2EI JSON → report formatting
│   ├── discord_bot.py      # Discord webhook client
│   ├── twitch_bot.py       # Twitch IRC client
│   ├── ai_analyst.py       # AI fight commentary
│   ├── gw2ei_invoker.py    # GW2EI CLI wrapper
│   ├── ei_updater.py       # Elite Insights auto-updater
│   ├── gui_settings.py     # PyQt6 settings window
│   ├── tray_manager.py     # System tray integration
│   └── setup_wizard.py     # First-run wizard
└── assets/                 # Icons and images
```

### Key Files for Common Contributions

| I want to... | Edit this file |
|---|---|
| Add a new report section | `core/fight_report.py` |
| Change Discord embed formatting | `core/fight_report.py` |
| Improve the AI prompt | `core/ai_analyst.py` → `_default_system_prompt()` |
| Add a new GUI setting | `core/gui_settings.py` + `core/config.py` |
| Fix file detection issues | `core/file_watcher.py` |
| Add a new team ID mapping | `core/fight_report.py` → team ID dict |

### Debugging Tips

- **`--verbose`** flag enables debug-level logging for all components
- **`--debug-ai-prompt`** flag saves the full AI prompt and fight data to a JSON file for each fight
- **Process Files tab** lets you reprocess old `.evtc` files without waiting for new fights
- **Test Connection** buttons in the Messaging and AI tabs verify your configuration

## Code Style

- Python 3.9+ compatible — no walrus operators or newer syntax
- Follow existing patterns in the codebase
- Use `logging` module, not `print()` statements
- GUI work uses PyQt6 — signals and slots for thread-safe communication
- Keep external dependencies minimal — check `requirements.txt` before adding new packages

## What We Won't Accept

- Changes that add mandatory external dependencies for core functionality
- Code copied from GPL-licensed projects without proper licensing compliance
- Changes to the AI prompt that make commentary personally toxic toward named squad players (roasting PUGs and enemy compositions is fine — targeting individuals is not)
- Features that interact with the GW2 game client directly (SparkyBot only reads ArcDPS log files)

## License

By contributing to SparkyBot, you agree that your contributions will be licensed under the [MIT License](LICENSE).

## Questions?

Not sure if your idea fits? Open a discussion in [Issues](https://github.com/SimpleHonors/SparkyBot/issues) and we'll help you figure out the best approach.
