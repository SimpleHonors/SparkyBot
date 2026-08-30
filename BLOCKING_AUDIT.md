# UI Responsiveness Audit — ticket 6c8e08f9

Operator complaint: slow startup and intermittently laggy button clicks.
Root cause class: the configured log folder is a **network share (Z:)** — any
`stat`/`rglob`/`open` against it on the GUI thread stalls the whole UI for as
long as the share takes to answer (30+ s on a CIFS hiccup, seconds even when
healthy for a big log tree).

Every `requests.*`, `subprocess.*`, and share-touching call site in `core/` and
`main.py` was traced to the thread it actually runs on. Findings below, ranked
by user pain. FIXED items link to the commit that addresses them.

## Findings by class

| Class | GUI-thread offenders | Already threaded (verified) |
|---|---|---|
| 1. Sync network in UI thread | 0 | 14 call sites (see below) |
| 2. Network-share file I/O in UI thread | 5 (F1-F5, all fixed) | file_watcher scans (WatcherWorker QThread) |
| 3. subprocess in UI handlers | 1 (F6) | gw2ei/combiner (report + pipeline workers) |
| 4. Startup / construction work | 2 (F2, F3) + minor import cost | update checks, TTS init |

## Top findings by pain

### F1 — FIXED — periodic share scan on the GUI thread (the laggy clicks)
`core/main_window.py:887` (pre-fix) — `_refresh_run_counter` →
`_discover_run_logs` → `discover_logs()` = `rglob('*.zevtc'/'*.evtc')` + a
`stat()` per file over the SMB share, **on the GUI thread**.
Trigger: a 60-second QTimer while a run is open (`_run_scan_timer`,
main_window.py:167) **plus every file-processed signal** (main_window.py:1097).
Worst case: share hiccup = every click frozen for tens of seconds, once a
minute — exactly "intermittently laggy button clicks".
Fix: scan moved to a single-flight daemon thread; result lands on the GUI
thread via `sig_run_logs_scanned`.

### F2 — FIXED — share scan during MainWindow construction (the slow startup)
`core/main_window.py:720` (pre-fix) — `_init_run_state` ran the same
`discover_logs` scan synchronously while the window was being built (app shows
the window 500 ms after launch — main.py:1078). Worst case: window blank/frozen
for the duration of a share stall at every launch with an open run.
Fix: open-run panel shows immediately; the resume-vs-stale verdict computes on
the worker scan and lands via the same signal.

### F3 — FIXED — Fight Summary tab discovers + stats logs on the GUI thread
`core/raid_report_tab.py:314,325` (pre-fix) — `_refresh` (construction) and
`rescan` (page-open contract, fires on *every* page open) called
`self._discover()` (= `get_log_folders` + `discover_logs` over the share)
synchronously; `_populate` (raid_report_tab.py:366 pre-fix) then did another
`path.stat()` **per row** over the share for the size column.
Trigger: window construction and every Fight Summary page open.
Fix: discover + size gathering run on a daemon thread
(`sig_logs_discovered`); populate uses the prefetched sizes; ticked fights and
the initial "recent" quick-pick still apply when results land.

### F6 — FIXED — pip install blocks the setup wizard for up to 120 s
`core/setup_wizard.py:350` (pre-fix) — `DependenciesPage._check_and_install`
ran `subprocess.run([pip, install, ...], timeout=120)` on the GUI thread
(button slot + auto-run on page load). Worst case: first-run wizard frozen
2 minutes.
Fix: install runs on a daemon thread; result lands via `_sig_install_done`.
The importlib.metadata check phase stays sync (local, fast).

### F4 — FIXED (approved follow-up) — End Run click paths scanned the share synchronously
`core/main_window.py` — `_request_end_run` (two scans: pre-dialog count +
post-confirm selection), `_end_stale_run`, `_end_run_and_quit`.
Trigger: End Run button / stale banner / quit-with-open-run. Worst case was
one share-stall freeze per click.
Fix (per the approved proposal): the confirm dialog uses the cached
`_run_fight_count` (refreshed async every 60 s + every processed file) —
the pre-dialog scan is gone; after confirm, `session.end()` (local state
file) stays on the GUI thread and the selection scan runs on a worker
thread (`sig_end_scan_done`), continuing into the existing report worker or
the no-fights verdict on the GUI thread. The stale-banner flow scans
off-thread first and keeps all session end/discard decisions on the GUI
thread (`_finish_stale_run`). Behavior note: the dialog's fight count can
be up to 60 s stale; the definitive post-confirm selection is unchanged.

### F5 — FIXED (approved follow-up) — single network `stat` in scattered GUI slots
`core/config.py` — `get_log_folders()` did `os.path.exists(log_folder)`
(one SMB stat) on every call. GUI-thread callers: `core/gui_settings.py:194`
(`_browse_files`), `core/gui_settings.py:3532` (`_calib_import_logs`).
Fix: the existence check is TTL-cached (15 s) in Config, keyed by path so a
config change re-stats immediately. A share that answers slowly can still
stall the first call in a window; repeated calls no longer multiply it.

## Minor / no action

- **Startup import cost** (`main.py` imports the full GUI stack incl. 4.1k-line
  `gui_settings` at module import): pure local CPU, sub-second; restructuring
  to lazy imports is not worth the churn.
- `discover_logs` per-file `stat()` (core/raid_session.py:55) doubles share
  round trips during a scan; now always off-thread, so left as-is.

## Class-1 network call sites verified already off the GUI thread

- update checks: `core/update_flow.py:143,155,175,223,330` (threads at 127/215/314)
- EI updater: `core/ei_updater.py:42,55,164` (thread `gui_settings.py:2811`)
- version labels: `gui_settings.py:2666,2790` (threads at 2656/2780)
- TTS voices/preview/upload: `gui_settings.py:1608,1665` + `setup_wizard.py:2734,2780` (wrapping threads)
- AI model list `providers.py:117` / `fight_analyst.py:139` (threads `gui_settings.py:1795`, `setup_wizard.py:2220`)
- AI analyze `fight_analyst.py:541`, zingers `raid_report_wiring.py:265`,
  Discord posts `discord_bot.py:72,124,159` — pipeline QThread
  (`main.py:112 FileProcessorWorker`) or report worker threads
- combiner GitHub `combiner_manager.py:160,215` — inside `RaidReportRunner.generate`, threaded
- Twitch test `gui_settings.py:616` — threaded (`_test_twitch_connection`)

## Regression tests

`tests/test_ui_responsiveness.py` — four offscreen tests prove each fixed
entry point returns in well under a second while the slow dependency is blocked
on an event, and that the result (row population, quick-pick, preserved ticks,
fight count, install status) still lands correctly via signal afterwards.
