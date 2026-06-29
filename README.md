# SparkyBot

### The unhinged AI shot-caller that watches your Guild Wars 2 WvW logs, does the math, and roasts your squad in real time — on Discord *and* Twitch, with a voice if you want one.

You fought. You died (a little). ArcDPS wrote a log. **Before you've finished typing "gg," SparkyBot has parsed the whole fight, found the one stat that actually mattered, and posted something meaner and funnier than your guildies were about to.**

Not affiliated with ArenaNet. Barely affiliated with good taste. Extremely good at its job.

> ⚠️ Sparky's default prompt talks trash. Loudly. It's competitive-gaming humor about a video game — and it roasts *your own squad* at least as hard as the enemy. Want it polite? Edit the system prompt in Settings. We won't judge. (We will, a little.)

---

![Discord & Twitch Integration](https://github.com/user-attachments/assets/bdc56ce6-0fff-401c-b3f7-d00d3c34aea9)
<br><br>
![AI Powered Fight Analysis](https://github.com/user-attachments/assets/2d243ab1-7f1e-434d-9006-05b2710d330c)

---

## 🔥 What's New in 1.8.0 — *"Grades on YOUR Curve"*

Sparky's performance tiers used to be carved from a stock corpus of 800-some fights. Fine — but that's not *your* guild. **Now you can retune the entire grading curve to your own server, right from the Settings window. No spreadsheet, no code, no asking nicely.**

Point the new **Calibration** tab at a pile of your `.evtc` logs, pick how many to grind through at once — up to **32 in parallel**, so a backlog that used to take *days* takes minutes — and when it's done it throws up a preview showing exactly how every tier moves: old → new, ↑ or ↓, color-coded, before you commit a thing. Run a sweat-lord guild? "Dominant" healing might jump +20% because your healers actually *heal*. Run a feeder comp? The bar drops to match reality. Either way it's **honest to a fault** — the numbers come straight out of your own fights, nothing invented, and a loud warning fires if you try to calibrate off too few of them. It even offers to recalibrate the *instant* an import finishes, because remembering to click a button is beneath you and we know it.

The bot stops grading you against strangers and starts grading you against the only people who matter: your own squad, on its best and worst nights.

---

## 🔥 What's New in 1.7.5 — *"It Remembers Its Own Tics"*

Sparky now holds a *grudge*. It keeps a permanent rap sheet of its own verbal crutches across all of history — lean on a pet phrase too many times and it gets blacklisted automatically (no human maintains the list; it narcs on itself). And a player on cooldown now gets their stats **deleted from what the model can even see**, so it can't gush about the same hero under a fake nickname. Their numbers still count toward squad totals — they're benched from the spotlight, not erased.

This builds on 1.7.0's **Anti-Slop Update**: an anti-repetition engine that benches any phrase, verb, or player name it reuses; a "Narrative Facts" prompt that hands the model curated truth instead of a JSON firehose; stochastic seeding to knock it off its favorite ruts; and a silent-failure guard that retries empty "I thought about it" responses. Overkill for jokes about a video game? Absolutely. Did we do it anyway? Obviously.

---

## What It Does

After a fight ends and ArcDPS writes a log, you get a full combat report in seconds:

- **AI Fight Commentary** (optional) — hype, unhinged narration from any OpenAI-compatible LLM, never repeating its own bits, with optional TTS and Discord audio
- **Quick Report** — KDR, duration, squad/enemy downs, kills, deaths
- **Squad & Enemy Summaries** — player counts by team color, total damage, DPS
- **Detailed Stats** — damage, burst, strips, cleanses, heals, defense, CCs, downs/kills
- **Boon Uptime** — defensive and offensive boons per subgroup
- **Enemy Intel** — top damage skills, composition by profession and color

Discord gets color-coded code blocks with configurable guild icons; Twitch gets a plain-text summary plus commentary. **Enemy players are never named — only their professions. We roast comps, not strangers.**

---

## Setup

### Prerequisites

1. **Python 3.9+** from [python.org](https://www.python.org/downloads/) — check **"Add Python to PATH"** during install (critical; Sparky won't launch without it, and it'll be sad, and so will you)
2. **.NET 8.0 Runtime** from [Microsoft](https://dotnet.microsoft.com/en-us/download/dotnet/8.0)
3. **ArcDPS** from [deltaconnected.com/arcdps](https://www.deltaconnected.com/arcdps/)

### Install

1. Download from the [Releases page](https://github.com/SimpleHonors/SparkyBot/releases)
2. Extract the zip anywhere (e.g. `C:\SparkyBot`)
3. Double-click **SparkyBot.bat**

On first launch it installs its own dependencies, runs a setup wizard, and starts watching. You don't need to be a programmer. You need to be able to double-click a file.

### Discord

Channel → gear icon (Edit Channel) → **Integrations** → **Webhooks** → **New Webhook** → **Copy Webhook URL**. Paste it into the setup wizard or Settings → Messaging.

### Twitch

1. Use (or make) a Twitch account for the bot
2. Get an Access Token at [twitchtokengenerator.com](https://twitchtokengenerator.com)
3. Settings → Messaging → enter Channel Name + Bot Token, check **Enable Twitch Bot**
4. Hit **Test Connection** — a test message should land in your chat

---

## Configuration

Everything lives in the GUI (right-click the system tray icon → Settings); the wizard covers the essentials on first launch. Highlights:

- **Messaging** — up to 3 Discord webhooks, Twitch channel/token, embed color, guild icon
- **Paths** — ArcDPS log folder, GW2EI CLI path, network poll interval
- **Thresholds** — min fight duration/downs/damage to filter trivial fights
- **Display** — toggle individual report sections
- **Behavior** — for hands-free: enable Start with Windows + Hide Console + Start Minimized + Start Watcher, then never think about it again

**AI Settings:** pick a **Provider** preset or set a custom **base URL**, drop in your **API Key** (blank for local models), choose a **Model**, and tweak the **System Prompt** in the full editor (with Reset to Default). Commentary posts as a separate embed *after* the fight report, so it **never delays your stats** — it retries on failure, and the silent-failure guard catches empty responses.

**TTS (optional):** reads commentary aloud and/or attaches audio to Discord. Choose `edge` (free Microsoft neural voices) or `elevenlabs` (API key, with voice/stability/style controls).

---

## AI Model Recommendations

Sparky works with **any** OpenAI-compatible API. Models were graded across real WvW fights on rule compliance, narrative quality, and variety.

- **Free & great:** Gemini 2.5 Flash — fast (~1.4s), perfect 20/20, free key at [Google AI Studio](https://aistudio.google.com/apikey)
- **Top quality:** GPT-5.4 Mini (20/20, ~1.4s) and Grok 4.20 (20/20, ~1.1s)
- **Cheapest good pick:** DeepSeek V3.2 — 20/20 at ~$0.09 per 100 fights
- **One key for all of them:** [OpenRouter](https://openrouter.ai/)

A typical 20-fight night runs **less than a penny** on Gemini, ~2 cents on DeepSeek, ~6 cents on GPT-5.4 Mini. A full night of professional-grade roasting costs less than the repair bill on one bad push.

---

## How It Works

- **Watching** — local folders use OS-native events (`watchdog`) for instant detection; network shares fall back to polling. Files at startup are skipped; each new one is processed once.
- **Parsing** — GW2 Elite Insights CLI turns ArcDPS `.evtc` logs into JSON.
- **Pre-analysis** — before a single token hits the model, Sparky buckets the fight into qualitative tags (calibrated against recorded fights), applies player/topic cooldowns, and builds the "Narrative Facts" it's allowed to talk about. The AI gets *curated truth*, not a firehose.
- **Posting** — Discord splits into batched embeds within API limits; Twitch sends plain-text within the 500-char cap, TLS by default.

**Command line:** `python bootstrap.py` accepts `--verbose`, `--headless`, `--config PATH`, and `--debug-ai-prompt`.

---

## How Sparky Knows You Balled Out

Here's the problem with "good": 4,000 DPS is a war crime in a 90-second gank and a nap in a 10-minute slugfest, and a healer's sheet looks nothing like a zerker's. So Sparky does **not** do hardcoded "good = big number" garbage. It grades you **on a curve against 800+ real WvW fights** — every number gets ranked against everyone who's actually thrown down. No vibes. Receipts.

Five tiers, by percentile. Clear the floor or Sparky doesn't even bring you up — no participation trophies in here:

| Tier | You beat… | Translation |
|------|:---------:|-------------|
| `solid` | 25% | you showed up and did a thing |
| `strong` | 50% | comfortably above the middle |
| `dominant` | 75% | you're carrying |
| `exceptional` | 90% | one of the best bodies on the field |
| `legendary` | 95% | the stat line screenshotted into guild chat |

And it grades **everything** independently — healing, cleanses, strips, hard CC, burst, downs, kills, stability, boon gen — all normalized per-second so the grind and the gank get a fair trial. *(For the nerds: clear ~5,500 DPS/sec and you're top-5% of every fight on record.)*

**But here's what lazy stat sheets miss.** Standing in the blob mashing `1` racks up "damage" — *winning* happens at the decisive moment. So Sparky stalks two killer axes: damage into **downed** enemies (finishing the kill before they rally) and healing into your own **downed** bodies (ripping a teammate off the floor mid-wipe). Go off on either and you get tagged **clutch** — the closer.

Then it fingerprints your stat shape against your class to figure out *what you actually were* — `burst evoker`, `rez druid`, `boon DPS` — and spotlights your single most unhinged number instead of parroting the damage chart.

> **And it's YOUR blood, not some stranger's.** Don't like being graded against a stock corpus? **Settings → Calibration**: feed it a batch of your guild's logs (32 at a time), watch a preview show exactly how every tier moves up or down, and apply. The curve becomes *your* server's meta — the bar rises where your crew is filthy and drops where it isn't. Honest numbers, your fights, one click. (See *What's New in 1.8.0* up top.)

---

## Troubleshooting

- **Logs not detected** — check the watcher is running and the folder path is right (usually a numbered subfolder in `arcdps.cbtlogs`). Network shares have up to 5s latency.
- **GW2EI parse fails** — install .NET 8.0; huge logs (50+ players, 20+ min) can take 60s+; check for GW2EI updates.
- **Discord/Twitch not posting** — verify the webhook/token, confirm the bot is enabled, check the console. Expired Twitch tokens regenerate at twitchtokengenerator.com; if TLS fails, disable secure connection.
- **AI cut off / times out** — raise Max Tokens or API Timeout in Settings → AI; some reasoning models burn tokens thinking. Try a faster model.
- **AI feels repetitive early** — the anti-repetition memory builds over a session, so the first couple fights have less history. Give it a few rounds; it gets meaner *and* more varied as the night goes.
- **Team colors show as "Enemy"** — unmapped team ID; check the console and [open an issue](https://github.com/SimpleHonors/SparkyBot/issues).

---

## Built on the Shoulders of Giants

- **[MzFightReporter](https://github.com/Swedemon/MzFightReporter)** by Swedemon (MIT) — the original Java WvW reporter that inspired this
- **[GW2 Elite Insights](https://github.com/baaron4/GW2-Elite-Insights-Parser)** by baaron4 — the parser powering all log analysis
- **[ArcDPS](https://www.deltaconnected.com/arcdps/)** by deltaconnected — the combat logging addon that makes it all possible
- **[PlenBot Log Uploader](https://github.com/Plenyx/PlenBotLogUploader)** by Plenyx and **[EVTC Parser](https://github.com/Drevarr/EVTC_parser)** by Drevarr

---

## License

MIT License — Copyright (c) 2025-2026 SimpleHonors. Provided "as is," without warranty of any kind. See [LICENSE](LICENSE) for full terms.

---

*SparkyBot is not affiliated with or endorsed by ArenaNet, NCSOFT, or their partners. Guild Wars 2 and all associated logos are trademarks of NCSOFT Corporation.*
