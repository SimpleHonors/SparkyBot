# Changelog

All notable changes to SparkyBot will be documented in this file.

## [Unreleased]

### Changed

- Raid Reports now use GW2 EI Log Combiner's supported compressed standalone
  HTML output instead of SparkyBot's private bake path.
- The combiner manager checks for newer releases and upgrades in place while
  retaining a usable installed version when GitHub is temporarily unavailable.

### Fixed

- Current Elite Insights logs containing non-numeric damage-modifier gains no
  longer strand SparkyBot on an old combiner release. The updater moves online
  installs to v1.8.3 or newer; v1.8.1 is the minimum safe offline fallback.

## [2.0.0] — 2026-07-20

### Added

- A complete dark desktop shell with Home, sidebar navigation, task-shaped
  Settings, status bar, keyboard shortcuts, and an activity feed.
- Start Run / End Run: remembers open runs across restarts, counts fights,
  builds the Raid Report, and optionally posts it to Discord.
- Drag-and-drop manual log processing from Home.
- First-run questions for AI opt-in and report workflow. Declining AI removes
  AI-only screens instead of leaving a graveyard of disabled controls.
- Native Windows installer and portable one-directory build.
- Final Raid Report pipeline: fast cached generation, manual fight picking,
  self-extracting compressed HTML, Discord publishing, persistent records,
  Poison Coverage, clearer progress, and copyable error details.
- Optional short names for all three Discord webhook destinations, plus
  independent selectors for fight reports and end-of-run Raid Reports.

### Changed

- Settings are grouped by jobs people recognize: Discord, Fight Reports,
  Watcher & Parsing, Raid Reports, Twitch, Application, and optional AI pages.
- Watcher notifications move into the visible activity feed while the window
  is open, so Windows stops waving duplicate balloons at you.
- Report output defaults to temporary storage and cleans itself up.

### Fixed

- Start Run records processing events, so copied or manually replayed logs
  count even when their filenames contain an older fight date. The event
  ledger survives application restarts and deduplicates repeated paths.
- Manual processing reports the real parse/skip/post result instead of
  marking every non-throwing pipeline return as successful.
- Hardened report titles, HTML packing, payload validation, collision
  ordering, and atomic output handling.
- Fixed fresh-install report output failures, incorrect fight counts,
  dishonest progress, long-fight Poison rates, frozen-app imports, and stray
  console windows.
- Fixed Raid Report and Calibration opening as completely blank sidebar pages
  after being promoted out of the old Settings tabs.
- AI connection testing is bounded instead of stacking several full
  three-retry commentary calls. Direct DeepSeek uses its official thinking
  switch, including when an older test saved the wrong generic strategy.
- Raid Report Discord captions now show the report-generation date in the
  user's machine-local time instead of scraping a misleading time range from
  generated report metadata.

## [1.8.15] — 2026-07-19

### Fixed
- The voice recap .mp3 and wrap-up summary could still post for
  existing installs: settings files written by older versions had the
  old "on" values saved in them, overriding the new defaults. The
  recap is now fully retired from the Discord post no matter what the
  settings file says — it returns in a future version as an
  intentional feature.

## [1.8.14] — 2026-07-19

### Changed
- Report files now land in the system temp folder by default and are
  cleaned up automatically after two days — they no longer pile up on
  the hard drive forever. Setting a report output folder in Settings
  keeps them permanently in that folder instead, which is never
  auto-cleaned.

## [1.8.13] — 2026-07-19

### Fixed
- Poison tab: Apps/min showed 0 for players with long nights (fight
  time over ~17 minutes) even when their poison hits were high. All
  players now show their real rate.

## [1.8.12] — 2026-07-19

### Changed
- Raid Report files are now about a third of their old size (a 10.8 MB
  night shrinks to ~3 MB), so they post to Discord as a plain .html —
  no more .zip. The report looks and works exactly the same when
  opened; it just unpacks itself in the browser first.
- The Discord post is now just the report: the wrap-up summary, its
  one-liners, and the voice recap no longer ship by default. They will
  return in a future version once the recap reports genuinely useful
  information. The Settings switches still turn them back on.

## [1.8.11] — 2026-07-19

### Fixed
- "Reading fight X of Y" now counts every selected fight (fights already
  read in a previous run count as done) instead of only the new ones —
  no more "4 of 4" when 9 fights are selected.

## [1.8.10] — 2026-07-19

### Fixed
- No more console windows popping up during report runs — the fight
  reader and other helpers now run hidden in the installed app.
- If some fights can't be read, the report still completes but now
  shows a clear warning with the affected fights ("Show details"),
  instead of pretending everything worked.
- Setup helper voice screen now offers the Local speech server option
  (server URL, voice picker with Refresh) and an Edge voice picker,
  matching the full Settings window. Voice test uses your actual
  choices instead of a fixed voice.
- Setup helper's stats-reader check now looks in the right place in
  the installed app.

## [1.8.9] — 2026-07-19

### Fixed
- Setup helper no longer shows the "requirements.txt not found" screen
  in the installed app — that check only applies when running from
  source; the installed exe has everything built in.

## [1.8.8] — 2026-07-19

### Fixed
- Fresh installs crashed on first run ("No module named 'ai_helpers'")
  when the setup helper opened. All internal imports now use package
  paths so the installed exe loads every screen.

## [1.8.7] — 2026-07-19

### Changed
- Raid Report fight picker rebuilt: compact flat table, visible checkboxes,
  click-to-tick with shift-click range, sortable Date/Fight/Size columns
  with standard numeric dates.
- Progress bar now reflects real overall progress instead of pegging at
  100% mid-run; removed the incorrect "first time only" status message.
- Restoring the window from the tray icon now brings it to the foreground
  with focus.
- Installer now installs to C:\SparkyBot (classic location, one-time admin
  prompt) instead of the per-user AppData folder, and existing installs
  move there on upgrade.

## [1.8.6] — 2026-07-19

### Added

- **Raid Report** — end-of-night stat analysis and reporting
  - "Make my raid report" — one big button. Auto-discovers logs, auto-selects the last 12 hours, downloads the stats builder on first use, and bakes a stats page with zero friction beyond opening the tab
  - Raid Wrap-Up embed posted alongside the report: top player per category (Damage, Downs, Healing, Barrier, Cleanses, Strips, Poison Coverage) in a Discord embed; config toggle `raidreportWrapup`
  - Raid ends, click, done — no separate program, no re-parsing, no waiting; SparkyBot already analyzed every fight live during the raid
  - One-click Discord publish via existing webhooks; auto-zip for reports exceeding the 10 MB attachment limit; "Open it" button to preview in browser first
  - Parse-result cache reuses live-parse data (on by default, auto-cleans after 48 hours); fingerprint-invalidated on settings changes
  - Persistent records database for leaderboard history that accumulates across sessions
  - Found-count line shows "N fights found — ready" before you click
  - Advanced toggle (bottom-right) reveals manual fight picking, selection buttons, and optional report naming for power users
  - Errors shown in plain language on the tab — no stack traces; "Show details" button reveals the full copyable message
  - All errors and progress written to `sparkybot.log` next to the app (rotating 1 MB × 3 backups)
  - Feature renamed from Night Report to Raid Report; all user-facing text and settings updated
  - Raid Report settings tab in the GUI for viewer path, output folder, cache toggle, and Discord zip options
- **Windows build kit** — native `.exe` packaging (build pipeline complete; binary ships in this release)
- **File logging** — `sparkybot.log` with rotation (1 MB × 3 backups) next to the app; ensures errors survive beyond the console window

### Changed

- **PySide6 migration** — migrated GUI framework from PyQt6 (GPL) to PySide6 (LGPL), enabling license-compatible binary distribution
- **License compliance pack** — third-party license documentation bundled with binary builds for all dependencies

### Technical

- New modules: `core/night_session.py` (log discovery, session clustering, fight-data cache), `core/combiner_manager.py` (GW2 EI Log Combiner runtime manager), `core/report_bake.py` (TiddlyWiki HTML report baker), `core/report_publisher.py` (Discord attachment publisher)
- New `[NightReport]` config section: `nightreportCacheEnabled`, `nightreportCacheDir`, `nightreportCacheRetentionHours`, `nightreportViewerHtml`, `nightreportOutputDir`, `nightreportAlwaysZip`
- Cache pruning runs on each startup when caching is enabled
- GW2EI `_ensure_parse_config` content extracted to `PARSE_CONFIG_CONTENT` module constant for cache-key fingerprinting
