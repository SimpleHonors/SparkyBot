# Settings: Watcher & Parsing

Where SparkyBot looks for fight logs, how they get parsed, and which fights are worth posting.

## What's on this page

### Log source

- **Log folder** — the folder the watcher monitors for new `.evtc`/`.zevtc` files (path box plus **Browse...**). Normally set once by the setup wizard.
- **Poll interval** (seconds) — how often to check for new files **when the folder is on a network share**. Local folders react instantly and ignore this.

### Elite Insights parser

- **CLI executable** — the path to `GuildWars2EliteInsights-CLI.exe`, the program that reads game logs (path box plus **Browse...**).
- **Max parse memory** (MB) — a memory cap for parsing, so a huge log can't eat all your RAM.
- Installing and updating Elite Insights lives on the **Updates** page.

### Which fights get posted

A fight must pass **all** of these to be posted to Discord. Skipped fights still count for fight summaries.

- **Min duration** (seconds) — fights shorter than this are skipped (filters out tiny skirmishes).
- **Min downs** — fights with fewer downed players than this are skipped.
- **Min total damage** — fights with less total damage than this are skipped.

## Common problems

- **New fights aren't noticed** — check the **Log folder** is the exact folder ArcDPS writes to (it often has a `WvW` subfolder), and that the watcher is running (Home page status).
- **Everything gets skipped** — your filters are too strict. Lower **Min duration**, **Min downs**, or **Min total damage**. The Home activity feed names the exact filter each skipped fight failed.
- **Every tiny scuffle gets posted** — raise the same filters.
- **Parsing fails on very large fights** — raise **Max parse memory**.
- **Network-share folder reacts slowly** — lower **Poll interval** (it only applies to network shares).
