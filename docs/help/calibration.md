# Calibration

The **Calibration** sidebar page tunes SparkyBot's performance tiers to your own guild's fights. The AI commentary grades players against these tiers — calibrating means "great damage" is judged by *your* squad's normal, not a stranger's.

This page only exists while AI features are enabled (**Settings → Application → AI features**). Fights are collected automatically as the watcher runs; you can also import old logs.

## What's on this screen

### Collected Fights

A counter of how many fights SparkyBot has gathered so far ("N fights collected"). More fights make a better calibration.

### Import Logs

- **Import speed (parallel files)** — how many log files Elite Insights parses at once (1–32). Higher is faster but uses more CPU and memory — 16/32 can spike both. Start at 4.
- **Add Fight Logs...** — select `.evtc`/`.zevtc` files to run through Elite Insights and add to the collected fights. A progress bar tracks the import.

### Apply Calibration

- **Recalibrate** — computes new tiers from the collected fights and shows a **Recalibration Preview** first: each metric's tier levels, green for up, red for down, gray for unchanged. Nothing is overwritten until you click **Confirm & Apply**.
- **Reset to Defaults** — discards your calibration and reverts to SparkyBot's built-in tiers.

## Common problems

- **Calibration missing from the sidebar** — AI features are off. Turn them on in **Settings → Application**, save, and the entry appears immediately.
- **"0 fights collected"** — run the watcher through some play sessions, or use **Add Fight Logs...** to import a batch of old logs.
- **Import is slow or the PC struggles** — lower **Import speed (parallel files)** to 1 or 2 and try again.
- **Commentary feels too harsh or too generous after applying** — recalibrate after collecting more (and more typical) fights, or use **Reset to Defaults** to go back to the built-ins.
