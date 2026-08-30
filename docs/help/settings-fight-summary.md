# Settings: Fight Summary

Everything about the Combined Fight Log Summary — how it gets made, where it's saved, and what goes in it.

## What's on this page

### How reports get made

- **One-button runs (recommended)** — press **Start Run** when you begin playing; **End Run** builds the report and posts it. The run panel lives on the [Home](home.md) page.
- **I'll pick fights myself** — build reports on the [Fight Summary](fight-log-summary.md) page whenever you want; the run panel is hidden.
- **When I end a run, post the report to Discord automatically** — the remembered default for the End Run confirmation (you can still change it there each time). Grayed out in pick-myself mode.

### Output

- **Report output folder** — where finished report pages are saved. Leave blank to use the folder next to the stats viewer, falling back to the app directory.
- **Fast reports (reuse live fight data) — recommended** — SparkyBot already analyzes every fight seconds after it ends. With this on, it keeps each fight's analysis file (about 20 MB per fight) in a `RaidReportCache` folder instead of discarding it, so reports build from work already done — seconds instead of minutes — and only re-analyze fights SparkyBot missed. Files clean themselves up after 48 hours. Turn it off to save disk space; reports will re-analyze every log from scratch.
- **Always zip Discord uploads** — compress the report file every time it's posted, for guilds bumping into Discord's upload limit.
- **Include poison coverage page in the report** — adds the Poison Coverage tab (who kept the enemy's healing reduced) to every report. See [the report guide](fight-log-summary.md).

### Advanced

- **Stats viewer HTML** — the path to `Top_Stats_Index.html`, the viewer the report is built on. Leave blank to auto-detect. Only set this if you maintain your own viewer copy.

## Common problems

- **Start Run disappeared from Home** — this page's mode switch is on **I'll pick fights myself**; choose **One-button runs (recommended)** and apply.
- **Reports take minutes** — turn on **Fast reports (reuse live fight data)**.
- **Disk filling up** — the fast-reports cache holds about 20 MB per fight for 48 hours. Turn the option off if that's too much.
- **Can't find the report file** — set an explicit **Report output folder** so they always land in one place.
- **Report rejected by Discord for size** — tick **Always zip Discord uploads**.
