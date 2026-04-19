# SparkyBot

**A Guild Wars 2 WvW AI-Powered fight log reporter for Discord and Twitch.**

SparkyBot monitors your ArcDPS combat log folder, parses new WvW fight logs using GW2 Elite Insights, and posts rich fight reports to Discord and Twitch with optional AI commentary and TTS voice playback. Python is required.

SparkyBot's default prompt includes strong language and WvW trash talk. This is competitive gaming humor, not personal attacks. Users who prefer milder output can edit the system prompt in Settings.

---

![Discord & Twitch Integration](https://github.com/user-attachments/assets/bdc56ce6-0fff-401c-b3f7-d00d3c34aea9)
<br><br>
![Report Display Settings](https://github.com/user-attachments/assets/110f02ef-8fa0-40a9-b7a3-007eff019b1e)
<br><br>
![SparkyBot Behavior Settings](https://github.com/user-attachments/assets/fff4b5c4-f889-42b5-b8b8-fa9a4866a158)
<br><br>
![AI Powered Fight Analysis](https://github.com/user-attachments/assets/2d243ab1-7f1e-434d-9006-05b2710d330c)

## What It Does

After a WvW fight ends and ArcDPS writes a log, SparkyBot delivers a full combat report within seconds:

- **AI Fight Commentary** (optional): hype, unhinged analyst narration from any OpenAI-compatible LLM, with optional TTS and Discord audio
- **Quick Report**: KDR, duration, squad/enemy downs, kills, deaths
- **Squad & Enemy Summaries**: player counts by team color, total damage, DPS
- **Detailed Stats**: damage contribution, burst windows, strips, cleanses, heals, defense, CCs, downs/kills
- **Boon Uptime**: defensive and offensive boon uptime per subgroup
- **Enemy Intel**: top damage skills, composition by profession and team color

Reports use Discord code blocks with team color detection and configurable guild icons. Twitch receives a plain-text summary and AI commentary.

---

## Setup

### Prerequisites

1. **Python 3.9+** from [python.org](https://www.python.org/downloads/) (check **"Add Python to PATH"** during install)
2. **.NET 8.0 Runtime** from [Microsoft](https://dotnet.microsoft.com/en-us/download/dotnet/8.0)
3. **ArcDPS** from [deltaconnected.com/arcdps](https://www.deltaconnected.com/arcdps/)

### Installation

1. **Install Python 3.9+** from [python.org](https://www.python.org/downloads/). During installation, check the box that says **"Add Python to PATH"** (this is critical; SparkyBot will not launch without it).
2. Download SparkyBot from the [Releases page](https://github.com/SimpleHonors/SparkyBot/releases)
3. Extract the zip to any folder (e.g., `C:\SparkyBot`)
4. Double-click **SparkyBot.bat**

On first launch, SparkyBot installs dependencies, runs a setup wizard, and starts monitoring.

### Discord Webhook

1. Open Discord and go to the channel where you want fight reports posted
2. Click the gear icon (Edit Channel) → **Integrations** → **Webhooks**
3. Click **New Webhook**, give it a name, and click **Copy Webhook URL**
4. Paste the URL into SparkyBot's setup wizard or Settings → Messaging tab

### Twitch

1. Create a Twitch account for the bot (or use an existing account)
2. Go to [twitchtokengenerator.com](https://twitchtokengenerator.com)
3. Authorize with Twitch and copy the Access Token
4. In SparkyBot Settings → Messaging tab, enter the Channel Name and Bot Token
5. Check "Enable Twitch Bot"
6. Click "Test Connection" to verify — you should see a test message appear in your Twitch chat

---

## Configuration

All settings are in the GUI (right-click system tray icon → Settings). The setup wizard covers the essentials on first launch; everything below is available for later tuning.

**Messaging**: up to 3 Discord webhooks, Twitch channel/token, embed color and guild icon.

**Paths**: ArcDPS log folder, GW2EI CLI path, network poll interval (for SMB shares).

**Thresholds**: minimum fight duration, downs, damage, and max upload size to filter trivial fights.

**Display**: toggle individual report sections (damage, heals, defense, CCs, strips, etc.).

**Behavior**: auto-start with Windows, system tray options, auto-watcher, update checking. For hands-free operation, enable Start with Windows + Hide Console + Start Minimized + Start Watcher.

**Updates**: check for SparkyBot and Elite Insights updates from Settings → Updates.

### AI Settings

| Setting | Description |
|---------|-------------|
| Provider | Preset API configurations (OpenAI, Google, xAI, DeepSeek, OpenRouter, local) |
| API Key | Your provider API key (blank for local models) |
| Model | Select from dropdown or type manually |
| System Prompt | Custom instructions; click "Edit" for full editor with Reset to Default |

AI commentary posts as a separate embed after the fight report, never delaying stats. Retries up to 2 times on failure.

### TTS Settings

Optional text-to-speech reads AI commentary aloud and can attach audio to Discord.

| Setting | Description |
|---------|-------------|
| Provider | `edge` (free Microsoft neural voices) or `elevenlabs` (API key required) |
| Play through speakers | Local TTS playback |
| Attach to Discord | Upload audio as a Discord attachment |

ElevenLabs users can configure voice ID, stability, similarity, style, and speed. Audio is generated once and reused for both local playback and Discord.

---

## AI Model Recommendations

SparkyBot works with any OpenAI-compatible API. Models tested across real WvW fights, graded on rule compliance, narrative quality, and variety.

### Best Free

| Model | Speed | Setup |
|-------|-------|-------|
| Gemini 2.5 Flash | ~1.4s | Free at [Google AI Studio](https://aistudio.google.com/apikey). Provider: Google Gemini. 20/20. |
| Gemini 3 Flash Preview | ~2s | Same setup. 19/20. Currently preview. |

### Best Quality

| Model | Score | Speed | Cost/100 fights |
|-------|-------|-------|-----------------|
| GPT-5.4 Mini | 20/20 | ~1.4s | $0.29 |
| Grok 4.20 | 20/20 | ~1.1s | $0.84 |
| DeepSeek V3.2 | 20/20 | ~4s | $0.09 |
| Gemini 2.5 Flash | 20/20 | ~1.4s | Free |
| DeepSeek R1 | 20/20 | ~16s | $0.31 |

### Best Value (via [OpenRouter](https://openrouter.ai/))

| Model | Score | $/100 fights |
|-------|-------|-------------|
| Gemini 2.0 Flash | 19/20 | $0.04 |
| Gemini 2.5 Flash | 20/20 | $0.06 |
| DeepSeek V3.2 | 20/20 | $0.09 |
| GPT-5.4 Mini | 20/20 | $0.29 |
| Grok 4.20 | 20/20 | $0.84 |

A typical 20-fight WvW night costs less than a penny on Gemini, 2 cents on DeepSeek, 6 cents on GPT-5.4 Mini. All models above are available through [OpenRouter](https://openrouter.ai/) with a single API key.

---

## Command Line Options

```
python bootstrap.py [options]
```

| Option | Description |
|--------|-------------|
| `--verbose`, `-v` | Debug logging |
| `--headless` | CLI mode, no GUI or system tray |
| `--config PATH` | Custom config file |
| `--debug-ai-prompt` | Save AI prompts to JSON for debugging |

---

## How It Works

**File watching**: local folders use OS-native events via `watchdog` for instant detection. Network shares fall back to polling (configurable interval). Files are processed once; existing files at startup are skipped.

**Parsing**: GW2 Elite Insights CLI converts ArcDPS `.evtc` logs to JSON. Parse settings are written fresh each invocation.

**Discord**: reports split across batched embeds within API limits (6000 chars, 10 embeds per message). AI commentary follows as a separate message.

**Twitch**: plain-text quick report + AI commentary, each within the 500-char limit. TLS by default, 3-second delay between messages.

---

## Project Structure

```
SparkyBot/
├── bootstrap.py            # Entry point
├── main.py                 # PyQt6 application
├── SparkyBot.bat           # Windows launcher
├── core/
│   ├── ai_analyst.py       # AI fight commentator
│   ├── config.py           # Configuration
│   ├── discord_bot.py      # Discord webhook client
│   ├── fight_report.py     # Log → embed formatter
│   ├── file_watcher.py     # File monitoring
│   ├── gw2ei_invoker.py    # GW2EI CLI wrapper
│   ├── ei_updater.py       # Elite Insights updater
│   ├── gui_settings.py     # Settings window
│   ├── setup_wizard.py     # First-run wizard
│   ├── tray_manager.py     # System tray
│   ├── tts.py              # TTS (edge-tts + ElevenLabs)
│   ├── twitch_bot.py       # Twitch IRC client
│   └── version.py          # Version number
├── assets/                 # Icons and images
└── GW2EI/                  # Elite Insights (auto-managed)
```

---

## Troubleshooting

**Logs not detected**: verify the watcher is running and the log folder path is correct (usually a numbered subfolder inside `arcdps.cbtlogs`). Network shares have up to 5s latency.

**GW2EI parse fails**: ensure .NET 8.0 is installed. Very large logs (50+ players, 20+ min) can take over 60s. Check for GW2EI updates.

**Discord not posting**: verify webhook URL, check "Enable Discord Bot" is on, check console for errors.

**Twitch not posting**: verify channel name and token, click "Test Connection". Expired tokens need regeneration at twitchtokengenerator.com. If TLS fails, try disabling secure connection in settings.

**AI cut off mid-sentence**: raise Max Tokens in Settings → AI. Some reasoning models consume tokens on internal thinking before producing output.

**AI times out**: raise API Timeout (default 30s). SparkyBot retries twice. Try a faster model if timeouts persist.

**Team colors show as "Enemy"**: unmapped team ID. Check console for warnings and [open an issue](https://github.com/SimpleHonors/SparkyBot/issues).

---

## Built on the Shoulders of Giants

- **[MzFightReporter](https://github.com/Swedemon/MzFightReporter)** by Swedemon (MIT) — the original Java WvW fight reporter that inspired this project
- **[GW2 Elite Insights](https://github.com/baaron4/GW2-Elite-Insights-Parser)** by baaron4 — the parser powering all log analysis
- **[ArcDPS](https://www.deltaconnected.com/arcdps/)** by deltaconnected — the combat logging addon that makes it all possible
- **[PlenBot Log Uploader](https://github.com/Plenyx/PlenBotLogUploader)** by Plenyx — a widely-used GW2 log uploader and Discord reporter
- **[EVTC Parser](https://github.com/Drevarr/EVTC_parser)** by Drevarr — Python EVTC parser and WvW stats aggregator

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

*SparkyBot is not affiliated with or endorsed by ArenaNet, NCSOFT, or any of their partners. Guild Wars 2 and all associated logos and designs are trademarks or registered trademarks of NCSOFT Corporation.*