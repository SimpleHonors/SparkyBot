# Home

The first page in the sidebar and the one you'll live on. It has the run panel (in one-button mode), the watcher toggle, and a live activity feed showing what SparkyBot just did. You can also drag fight-log files (`.evtc` / `.zevtc`) anywhere onto this page — they're sent straight to the [Process Files](process-files.md) queue.

## The Run panel (one-button mode only)

Shown only when **Settings → Fight Summary → How reports get made** is set to **One-button runs (recommended)**.

- **Start Run** — press when you start playing. It starts the watcher too, and SparkyBot counts every fight from this moment. While a run is open the button turns into **End Run…** and the status line shows how long the run has been open, how many fights it has, and when it started.
- **End Run…** — opens one confirmation showing the fight count and elapsed time. Tick or untick **Post it to Discord when done** (your choice is remembered), then click **End Run & Make Report**. SparkyBot builds the Combined Fight Log Summary with an inline progress bar ("Reading fight 3 of 12…", "Crunching the numbers…", "Building your page…") and posts it if asked.
- Fights that were skipped for Discord posting still count for fight summaries.
- **Long-open run hint** — if a run stays open a very long time, a quiet "forgot to end it?" note appears. Nothing happens without you.
- **Stale run banner** — if you quit with a run open and come back much later, a banner offers **End run & make report** (ends at the last fight, so newer logs aren't swept in) or **Discard**.

Closing or quitting with a run open always asks first: **Keep running in tray**, **End run without posting & quit**, or **End run, post to Discord & quit**.

## The watcher row

- **Start Watcher / Stop Watcher** — the small toggle for automatic fight posting without a run. While watching, SparkyBot processes each new fight log as it appears and posts the ones that pass your filters. The status bar at the bottom mirrors this ("Watcher running" / "Watcher stopped" with a colored dot).

## Activity

A live feed of what the bot just did — one line per event: "Fight posted — …", "… — did not pass the posting filters", "Run started", "Fight summary posted — …", errors, and so on. Right-click a line and choose **Copy line** to copy it. The bottom status bar echoes the latest post ("Last: fight posted 8:14 PM").

## Update banner

If **Check for updates on launch** is on and a new version exists, a quiet **Update available — Install v…** button appears in the status bar. Clicking it starts the update; ignoring it costs nothing.

## The menus

- **File** — Start/Stop Watcher; **Use Guild Setup File...** / **Create Guild Setup File...** and **Import from Another Log Tool...** / **Create Setup for Another Log Tool...** (see [Guild setup files and other log tools](guild-setup-files.md)); **Settings...** (`Ctrl+,`); **Exit**.
- **Tools** — jump to any sidebar page (**Fight Summary** `Ctrl+R`, **Process Files** `Ctrl+E`), plus **Settings**.
- **Help** — **Other WvW Log Tools & Credits...** and **About SparkyBot**.

## Common problems

- **No Start Run button** — you're in "I'll pick fights myself" mode. Switch it in **Settings → Fight Summary → How reports get made**.
- **"Run ended — no fights were recorded, nothing was posted."** — no fight logs appeared during the run window. Check the log folder in **Settings → Watcher & Parsing** and that ArcDPS logging is on.
- **"End Run report failed — …"** — the summary could not be made or posted. Your fights are safe; you can build the report any time on the [Fight Summary](fight-log-summary.md) page. The error dialog shows the details.
- **Fights show as skipped in the feed** — they didn't pass your posting filters (minimum duration, downs, damage). Adjust them in **Settings → Watcher & Parsing → Which fights get posted**. Skipped fights still make it into fight summaries.
- **Calibration missing from the sidebar** — it only exists while AI features are enabled (**Settings → Application → AI features**).
