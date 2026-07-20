# Raid Report — Operator Runbook

Last updated: 2026-07-19

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       RaidReportTab                         │
│  One big button → auto select → generate → Open / Post      │
│  Advanced (collapsed): log list, selectors, name field       │
└──────────────┬──────────────────────────────────────────────┘
               │ injects: discover, select_*, runner_factory, publish
               ▼
┌─────────────────────────────────────────────────────────────┐
│                  raid_report_wiring.py                       │
│  build_raid_report_tab(config)   run_headless_raid_report()  │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│                    RaidReportRunner                          │
│  select("recent") → generate(selected, name)                │
│  Stages: plan → parse → collect → combine → bake            │
└──────┬──────────┬──────────────┬────────────────────────────┘
       │          │              │
       ▼          ▼              ▼
┌──────────┐ ┌──────────┐ ┌──────────────┐
│raid_     │ │GW2EI     │ │combiner_     │
│session   │ │Invoker   │ │manager       │
│          │ │          │ │              │
│discover  │ │parse_file│ │ensure_instld │
│recent/   │ │cache_key │ │write_config  │
│today_logs│ │          │ │run           │
│plan_rprt │ │          │ │              │
└────┬─────┘ └────┬─────┘ └──────┬───────┘
     │ cache       │ parse        │ combine
     ▼             ▼              ▼
┌──────────┐ ┌──────────┐ ┌──────────────┐
│RaidReport│ │GW2EI .exe│ │Drevarr/GW2_EI│
│Cache     │ │(external)│ │log_combiner  │
│YYYYMMDD/ │ │          │ │(runtime dl)  │
└──────────┘ └──────────┘ └──────┬───────┘
                                 │ Drag_and_Drop_Log_Summary.json
                                 ▼
                    ┌──────────────────────┐
                    │    report_bake.py    │
                    │ bake_report()        │
                    │ TiddlyWiki injection │
                    └──────────┬───────────┘
                               │ standalone .html
                               ▼
                    ┌──────────────────────┐
                    │  report_publisher.py │
                    │  publish_report()    │
                    │  auto-zip at 9.3 MB  │
                    └──────────┬───────────┘
                               │ Discord webhook
                               ▼
                          Discord embed
```

### Module Map

| Module | Role |
|---|---|
| `core/raid_session.py` | Log discovery, time filters, parse cache |
| `core/raid_report.py` | Pipeline orchestrator — select, generate |
| `core/raid_report_tab.py` | GUI tab — one big button, signal-threaded |
| `core/raid_report_wiring.py` | Wires real deps into the tab; headless CLI entry |
| `core/raid_wrapup.py` | Best-of-raid summary embed |
| `core/combiner_manager.py` | Downloads/runs the external stats combiner |
| `core/report_bake.py` | Injects data into a TiddlyWiki viewer HTML |
| `core/report_publisher.py` | Sends HTML (or zip) to Discord webhook |
| `core/gw2ei_invoker.py` | Invokes GW2EI parser, manages parse configs |
| `core/config.py` | `[RaidReport]` section — paths, flags, retention |
| `core/apppaths.py` | LOCALAPPDATA vs share vs dev path resolution |

### Data Flow

1. `discover_logs(log_folder)` → `list[LogInfo]` (recursive, timestamped, sorted)
2. `recent_logs(logs, hours=12)` or `today_logs(logs)` → filtered selection
3. `RaidReportRunner.select("recent")` → calls the time filter
4. `plan_report(selected, cache, ...)` → splits into hits (cached) and needs (must parse)
5. GW2EI parses each needed log, results stored in `RaidReportCache`
6. All parsed JSON copied to a temp input dir, `CombinerManager.run()` produces `Drag_and_Drop_Log_Summary.json`
7. `bake_report(viewer_html, json, out_html)` → standalone `.html`
8. `publish_report(html_path, send_file)` → auto-zips if > 9.3 MB, posts to Discord

---

## Deploy to Share (NAS tar overlay)

The production share is a folder on NAS2. Apps are deployed as tarball overlays — extract against the share root so files land in the right subdirectories.

### Procedure

```bash
# 1. Build the tarball from the repo
cd /path/to/SparkyBot
git archive -o /tmp/sparkybot-raid-$(date +%Y%m%d).tar HEAD \
  -- core/raid_session.py    \
     core/raid_report.py     \
     core/raid_report_tab.py \
     core/raid_report_wiring.py \
     core/raid_wrapup.py     \
     core/report_bake.py     \
     core/report_publisher.py \
     core/combiner_manager.py \
     core/gw2ei_invoker.py   \
     core/apppaths.py        \
     core/config.py

# 2. Copy to NAS2
scp /tmp/sparkybot-raid-*.tar nas2:/mnt/user/appdata/sparkybot/

# 3. On NAS2, extract overlay (as the user that owns the share)
ssh nas2 "cd /mnt/user/appdata/sparkybot && tar xf sparkybot-raid-*.tar"
```

### Ownership Rules

- All files on the share must be owned by the user running SparkyBot (typically the share owner).
- After extraction: `chown -R <user>:<group> /mnt/user/appdata/sparkybot/core/`
- Never extract as root — breaks write access for the runtime user.

---

## Windows Build + Installer

### Build (PyInstaller)

```bash
# From the repo root on a Windows build machine
pyinstaller bootstrap.py \
  --name SparkyBot \
  --onefile \
  --windowed \
  --add-data "core;core" \
  --add-data "assets;assets" \
  --add-data "LICENSE;." \
  --hidden-import PySide6 \
  --hidden-import watchdog \
  --hidden-import requests \
  --collect-all PySide6

# Output: dist/SparkyBot.exe
```

### Installer (Inno Setup or NSIS)

- Package `dist/SparkyBot.exe` + `README.md` + `LICENSE`
- Install to `%LOCALAPPDATA%\Programs\SparkyBot\`
- Create Start Menu shortcut
- Do NOT install to `Program Files` — app writes config/logs/cache next to itself
- Sign the `.exe` to avoid SmartScreen warnings (see Troubleshooting)

### SmartScreen Note

Unsigned executables trigger Windows SmartScreen ("Windows protected your PC"). Users must click "More info" → "Run anyway" on first launch. Code-signing the `.exe` eliminates this. Until then, the README should include a screenshot of the SmartScreen bypass.

---

## Cache & Locations

### Paths Table

| What | Default Location | Config Key |
|---|---|---|
| Config file | `<app_dir>/config.properties` | — |
| Log file | `<app_dir>/sparkybot.log` | — |
| Combiner install | `%LOCALAPPDATA%/SparkyBot/RaidReportData/combiner/<version>/` | — (derived from `local_machine_dir()`) |
| Combiner DB | `%LOCALAPPDATA%/SparkyBot/RaidReportData/topstats_db/` | — (durable across runs) |
| Parse cache | `~/RaidReportCache/YYYYMMDD/` | `raidreportCacheDir` |
| Output reports | Viewer HTML parent dir or `~/` | `raidreportOutputDir` |
| Viewer HTML | Auto-detected or manual | `raidreportViewerHtml` |
| GW2EI parser | `<app_dir>/GW2EI/` | `gw2eiExe` (Paths section) |
| Log folder | ArcDPS `arcdps.cbtlogs/<subfolder>` | `logFolder` (Paths section) |

### Cache Behavior

- Parse cache is on by default (`raidreportCacheEnabled = true`)
- Entries are stored as `YYYYMMDD/<logstem>__<ei_version>__<fingerprint>.json`
- Fingerprint changes when GW2EI version or parse config changes → auto-invalidation
- Pruning runs on startup: removes entries older than `raidreportCacheRetentionHours` (default 48h)
- Cache is per-machine; do NOT put it on a network share (parse latency will kill performance)

### Share vs LOCALAPPDATA

- **`app_dir()`**: In frozen builds, the folder containing `SparkyBot.exe`. On dev, the repo root. This is where `config.properties` and logs live. CAN be on a network share.
- **`local_machine_dir()`**: `%LOCALAPPDATA%/SparkyBot` on Windows, `~/.sparkybot/` on Linux. Always local disk. Used for combiner installs and downloaded data. NEVER on a network share.
- If `app_dir()` is on a share and `local_machine_dir()` falls through to `app_dir()` (no LOCALAPPDATA), the combiner will install to the share. This is slow but functional.

---

## Troubleshooting

### Where are the logs?

`sparkybot.log` in the same directory as `SparkyBot.exe` (or the repo root in dev).

All Raid Report thread errors are logged with full tracebacks via `logger.exception()`. Grep for:
```
Report generation failed
Report publish failed
```

### Show Details Button

When a report fails, the status line shows the actual error text (first line, truncated to ~120 chars). Next to it, a **"Show details"** button opens a copyable dialog with the full exception. Tell the user to:
1. Click **Show details**
2. Select all text (Ctrl+A)
3. Copy (Ctrl+C)
4. Paste into the bug report

### Common Failures

| Symptom | Cause | Fix |
|---|---|---|
| "No fights found yet" | Log folder empty or wrong path | Check Settings → Paths → Log Folder |
| "Getting the stats builder" hangs | First-time combiner download; GitHub rate-limited | Wait 60s; it retries. Check firewall. |
| "All N selected logs failed to parse" | GW2EI missing, wrong .NET version, or corrupted logs | Install .NET 8.0 Runtime; check `sparkybot.log` for GW2EI error output |
| "The Raid Report could not find its stats page" | Viewer HTML not found | Clear the path in Settings → Raid Report, or re-run to trigger combiner install |
| Combiner subprocess fails silently | Missing Python deps in combiner venv | On Linux: `pip install requests glicko2 xlsxwriter` in the combiner's venv |
| Report posts as .zip | HTML exceeded 9.3 MB (10+ fights, large raid) | Normal — Discord attachment limit is 10 MB |
| "webhook down" | Discord webhook invalid or rate-limited | Verify webhook URL in Settings → Messaging |
| SmartScreen blocks .exe | Unsigned binary | Click "More info" → "Run anyway"; consider code-signing |

### Terminology & Versioning

- **Raid Report** — the end-of-session stats feature. One button, one HTML page.
- **Combiner** — the external GW2 EI Log Combiner (Drevarr/GW2_EI_log_combiner). Downloaded at runtime with user consent. GPL-3.0 — never vendored or redistributed with SparkyBot (MIT).
- **Cache** — the parse-result cache. Silent, automatic, auto-pruned. Not user-visible.
- **Bake** — the final step: injecting tiddler JSON into the viewer HTML template to produce a standalone report.

Versioning follows the SparkyBot release tag (e.g., v2.0.0-raid). The Raid Report subsystem has no independent version — it ships with the main release.

### Config Keys Reference

```
[RaidReport]
raidreportCacheEnabled = true
raidreportCacheDir =                   # empty = ~/RaidReportCache
raidreportCacheRetentionHours = 48
raidreportViewerHtml =                 # empty = auto-detect from combiner install
raidreportOutputDir =                  # empty = alongside viewer, or ~/
raidreportAlwaysZip = false
raidreportPoisonTab = true
raidreportWrapup = true
```
