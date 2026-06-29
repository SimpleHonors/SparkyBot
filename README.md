# SparkyBot

### The unhinged AI shot-caller that watches your Guild Wars 2 WvW logs, does the math, and roasts your squad in real time — on Discord *and* Twitch, with a voice if you want one.

You fought. You died (a little). ArcDPS wrote a log. **Before you've finished typing "gg" SparkyBot has already parsed the entire fight, found the one stat that actually mattered, and posted commentary meaner and funnier than anything your guildies were about to say.**

It is not affiliated with ArenaNet. It is barely affiliated with good taste. It is, however, *extremely* good at its job.

> ⚠️ SparkyBot's default prompt talks trash. Loudly. This is competitive-gaming humor about a video game, not personal attacks — and it roasts *your own squad* at least as hard as the enemy. Want it polite? Edit the system prompt in Settings. We won't judge. (We will, a little.)

---

![Discord & Twitch Integration](https://github.com/user-attachments/assets/bdc56ce6-0fff-401c-b3f7-d00d3c34aea9)
<br><br>
![Report Display Settings](https://github.com/user-attachments/assets/110f02ef-8fa0-40a9-b7a3-007eff019b1e)
<br><br>
![SparkyBot Behavior Settings](https://github.com/user-attachments/assets/fff4b5c4-f879-42b5-b8b8-fa9a4866a158)
<br><br>
![AI Powered Fight Analysis](https://github.com/user-attachments/assets/2d243ab1-7f1e-434d-9006-05b2710d330c)

---

## 🔥 What's New in 1.7.5 — *"It Remembers Its Own Tics"*

1.7.0 gave Sparky a short memory. 1.7.5 gives it a *grudge* — and patches the two ways it was quietly cheating its own anti-slop rules.

- **🧠 It now remembers every phrase it's ever leaned on.** The old engine only looked back ~10 fights, so a pet phrase ("poor stomp discipline," "from the dirt") would quietly age out, slink back in, and live forever in rotation. Now Sparky keeps a permanent rap sheet of its own verbal tics across *all* of history. Reach for the same crutch too many times and it's blacklisted — no human maintains the list, it narcs on itself.
- **🙈 It can't credit the same hero by a fake name anymore.** Old trick: told not to keep naming a player, the model would just drop the name and call them "the power amalgam… again." Cute. So now a cooled-down player's stats are **deleted from what the model can even see** — you can't gush about a stat line that isn't on the page. Their numbers still count toward the squad totals, so nobody's getting erased from history, just from the spotlight.

---

## 🔥 What's New in 1.7.0 — *"The Anti-Slop Update"*

Most AI bots have one fatal tell: they repeat themselves. Same three adjectives. Same "that's not a fight, that's a slaughter" sentence shape. Same poor bastard named MVP nine fights in a row. It reads like a bot because it *is* a bot phoning it in.

**SparkyBot 1.7.0 declared war on that.** Here's the arsenal — and every word of this is real, it's in the code, go read it:

- **🧠 The Anti-Repetition Engine.** SparkyBot remembers what it just said. Every 2-to-4-word phrase, every punchy verb ("shredded," "vaporized," "obliterated"), and every player name it spotlights gets tracked across your session. **Lean on any of them twice and it's benched for at least the next five fights.** Your commentary stops sounding like a broken Mad Lib and starts sounding like someone who was actually paying attention.
- **📜 The "Narrative Facts" prompt engine (v3).** Instead of dumping a wall of raw JSON at the model and praying, SparkyBot pre-chews the fight into a handful of clean, factual sentences — with player and topic cooldowns baked in *before the text is even built*. The model literally cannot over-name your commander, because the name isn't in front of it to abuse.
- **🎲 Stochastic seeding.** A high-entropy random (noun, register) pair gets slipped into every prompt to knock the model off its favorite rut. Translation: it gets bored of its own clichés so you don't have to.
- **🩺 Silent-failure guard + auto-retry.** Reasoning models that "think" themselves into an empty response get caught and retried instead of posting nothing. The stats *never* wait on the AI — they post in seconds regardless.
- **🧱 Fully modular rewrite.** The old 3,000-line brain got dismantled into focused modules (`vocabulary_tracker`, `narrative_facts`, `fight_analyst`, `freshness_engine`, `callout_cooldown`, and friends). Easier to read, easier to hack, harder to break.

Is it overkill for posting jokes about a video game? **Absolutely.** Did we do it anyway? **Obviously.**

---

## What It Does

After a WvW fight ends and ArcDPS writes a log, SparkyBot delivers a full combat report within seconds:

- **AI Fight Commentary** (optional): hype, unhinged analyst narration from any OpenAI-compatible LLM, now with the anti-repetition engine above so it never repeats its own bits — plus optional TTS and Discord audio
- **Quick Report**: KDR, duration, squad/enemy downs, kills, deaths
- **Squad & Enemy Summaries**: player counts by team color, total damage, DPS
- **Detailed Stats**: damage contribution, burst windows, strips, cleanses, heals, defense, CCs, downs/kills
- **Boon Uptime**: defensive and offensive boon uptime per subgroup
- **Enemy Intel**: top damage skills, composition by profession and team color

Reports use Discord code blocks with team color detection and configurable guild icons. Twitch receives a plain-text summary and AI commentary. Enemy players are never named — only their professions. We roast comps, not strangers.

---

## Setup

### Prerequisites

1. **Python 3.9+** from [python.org](https://www.python.org/downloads/) (check **"Add Python to PATH"** during install)
2. **.NET 8.0 Runtime** from [Microsoft](https://dotnet.microsoft.com/en-us/download/dotnet/8.0)
3. **ArcDPS** from [deltaconnected.com/arcdps](https://www.deltaconnected.com/arcdps/)

### Installation

1. **Install Python 3.9+** from [python.org](https://www.python.org/downloads/). During installation, check the box that says **"Add Python to PATH"** (this is critical; SparkyBot will not launch without it, and it will be sad, and so will you).
2. Download SparkyBot from the [Releases page](https://github.com/SimpleHonors/SparkyBot/releases)
3. Extract the zip to any folder (e.g., `C:\SparkyBot`)
4. Double-click **SparkyBot.bat**

On first launch, SparkyBot installs its own dependencies, runs a setup wizard, and starts monitoring. You do not need to be a programmer. You need to be able to double-click a file.

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

**Behavior**: auto-start with Windows, system tray options, auto-watcher, update checking. For hands-free operation, enable Start with Windows + Hide Console + Start Minimized + Start Watcher, then never think about it again.

**Updates**: check for SparkyBot and Elite Insights updates from Settings → Updates.

### AI Settings

| Setting | Description |
|---------|-------------|
| Provider | Preset API configurations (OpenAI, Google, xAI, DeepSeek, OpenRouter, MiniMax, Groq, Together, Mistral, local Ollama/LM Studio) |
| API Key | Your provider API key (blank for local models) |
| Model | Select from dropdown or type manually |
| System Prompt | Custom instructions; click "Edit" for full editor with Reset to Default |

AI commentary posts as a separate embed after the fight report, **never delaying stats**. Retries up to 2 times on failure, and the silent-failure guard catches "thought about it, said nothing" responses before they reach you.

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

SparkyBot works with **any** OpenAI-compatible API. Models below were tested across real WvW fights and graded on rule compliance, narrative quality, and variety.

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

A typical 20-fight WvW night costs **less than a penny** on Gemini, 2 cents on DeepSeek, 6 cents on GPT-5.4 Mini. Yes — a full night of professional-grade roasting costs less than the repair bill on a single bad push. All models above are available through [OpenRouter](https://openrouter.ai/) with a single API key.

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

**Pre-analysis**: before a single token hits the model, SparkyBot buckets the fight into qualitative tags (calibrated against a corpus of recorded fights), applies player/topic cooldowns, and builds the "Narrative Facts" the model is allowed to talk about. The AI gets *curated truth*, not a firehose.

**Discord**: reports split across batched embeds within API limits (6000 chars, 10 embeds per message). AI commentary follows as a separate message.

**Twitch**: plain-text quick report + AI commentary, each within the 500-char limit. TLS by default, 3-second delay between messages.

---

## How Sparky Knows You Balled Out

Here's the problem with "good": 4,000 DPS is a war crime in a 90-second gank and a nap in a 10-minute slugfest, and a healer's stat sheet looks nothing like a zerker's. So Sparky does **not** do hardcoded "good = big number" garbage. It grades you **on a curve against 800+ real WvW fights** — every number you post gets ranked against everyone who's ever actually thrown down. No vibes. Receipts.

Five tiers, by percentile. Clear the floor or Sparky doesn't even bring you up — there are no participation trophies in here:

| Tier | You beat… | Translation |
|------|:---------:|-------------|
| `solid` | 25% | you showed up and did a thing |
| `strong` | 50% | comfortably above the middle of the pack |
| `dominant` | 75% | you're carrying *(this tier was literally named `carried` once)* |
| `exceptional` | 90% | one of the best bodies on the field |
| `legendary` | 95% | the stat line that gets screenshotted into guild chat |

And it judges **everything** independently — not just damage. Healing, cleanses, boon strips, hard CC, burst, downs, kills, stability uptime, boon gen — all normalized per-second so the grind and the gank get a fair trial. *(For the nerds: clear ~5,500 DPS/sec and you're top-5% of every fight on record. Flex accordingly.)*

**But here's the part lazy stat sheets miss.** Standing in the biggest blob and mashing `1` racks up "damage." *Winning* happens at the decisive moment — so Sparky stalks two killer axes: damage into **downed** enemies (finishing the kill before they rally) and healing into your own **downed** bodies (ripping a teammate off the floor mid-wipe). Go off on either and you get tagged **clutch** — the closer. That's a sharper read than "who pressed buttons hardest."

Then it fingerprints your stat shape against your class to work out *what you actually were* — `burst evoker`, `rez druid`, `boon DPS` — and spotlights your single most unhinged number instead of parroting the damage chart. You get credit for the thing you actually did.

> **Tuned on real blood, not vibes.** Every threshold is recalibrated from recorded fights (`tools/recalc_thresholds.py`). Feed it your guild's logs and "what good looks like" reshapes itself to *your* server's meta.

---

## Project Structure

```
SparkyBot/
├── bootstrap.py                  # Entry point / self-updater
├── main.py                       # PyQt6 application
├── SparkyBot.bat                 # Windows launcher
├── prompts/
│   ├── sparky_system_v2.md       # Legacy "translate buckets to voice" prompt
│   └── sparky_system_v3.md       # Calibrated Narrative Facts prompt
├── core/
│   ├── ai_analyst.py             # Back-compat shim (re-exports the split modules)
│   ├── fight_analyst.py          # Orchestrates a fight → LLM call → post-processing
│   ├── narrative_facts.py        # v3: builds curated factual sentences for the prompt
│   ├── pre_digester.py           # Raw EI metrics → qualitative fight buckets
│   ├── performance_buckets.py    # Per-player performance tiering + build inference
│   ├── vocabulary_config.py      # Vocabulary palette (dice-rolled per call)
│   ├── vocabulary_tracker.py     # ⭐ Anti-repetition engine (phrases/verbs/players)
│   ├── freshness_engine.py       # Cross-fight freshness hints
│   ├── stochastic_seeds.py       # High-entropy prompt conditioning
│   ├── callout_cooldown.py       # Commander/topic callout pacing
│   ├── session_history.py        # Win/loss streak + session context
│   ├── ai_helpers.py             # Shared helpers, regexes, tag-distance grading
│   ├── response_post_processor.py# Strips slop, label tics, and thinking traces
│   ├── silent_failure_guard.py   # Detects empty "I thought about it" responses
│   ├── providers.py              # Provider presets (base URLs)
│   ├── config.py                 # Configuration
│   ├── discord_bot.py            # Discord webhook client
│   ├── twitch_bot.py             # Twitch IRC client
│   ├── tts.py                    # TTS (edge-tts + ElevenLabs)
│   ├── fight_report.py           # Log → embed formatter
│   ├── file_watcher.py           # File monitoring
│   ├── gw2ei_invoker.py          # GW2EI CLI wrapper
│   ├── ei_updater.py             # Elite Insights updater
│   ├── gui_settings.py           # Settings window
│   ├── setup_wizard.py           # First-run wizard
│   ├── tray_manager.py           # System tray
│   └── version.py                # Version number
├── assets/                       # Icons and images
└── GW2EI/                        # Elite Insights (auto-managed, not in repo)
```

---

## Troubleshooting

**Logs not detected**: verify the watcher is running and the log folder path is correct (usually a numbered subfolder inside `arcdps.cbtlogs`). Network shares have up to 5s latency.

**GW2EI parse fails**: ensure .NET 8.0 is installed. Very large logs (50+ players, 20+ min) can take over 60s. Check for GW2EI updates.

**Discord not posting**: verify webhook URL, check "Enable Discord Bot" is on, check console for errors.

**Twitch not posting**: verify channel name and token, click "Test Connection". Expired tokens need regeneration at twitchtokengenerator.com. If TLS fails, try disabling secure connection in settings.

**AI cut off mid-sentence**: raise Max Tokens in Settings → AI. Some reasoning models consume tokens on internal thinking before producing output.

**AI times out**: raise API Timeout (default 30s). SparkyBot retries twice. Try a faster model if timeouts persist.

**AI keeps repeating itself**: it shouldn't — that's literally what 1.7.0 fixed — but the anti-repetition memory builds over a session, so the first couple of fights have less history to work with. Give it a few rounds. It gets meaner *and* more varied the longer the night goes.

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
