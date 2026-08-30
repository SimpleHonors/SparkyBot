# Process Files

The **Process Files** sidebar page (`Ctrl+E`) manually processes individual log files without the file watcher. Each file runs through the full pipeline — parse, report, Discord — as a one-off. Use it for logs from another computer, logs recorded while SparkyBot wasn't running, or re-posting a specific fight.

## What's on this screen

- **Drop zone** — "Drag & drop .evtc / .zevtc files here". You can also drop files onto the [Home](home.md) page; they land in this queue automatically.
- **Browse Files...** — pick log files with a file dialog instead.
- **The queue list** — every file waiting to be processed. Duplicates are ignored. Hover a row to see its full path.
- **Remove Selected** — take the highlighted file out of the queue.
- **Clear All** — empty the queue.
- **Process Files** — run the queue. The status line counts through "Processing X of Y: filename" and finishes with "Done — processed N file(s)". Results also appear in the Home activity feed, and processed fights become pickable on the [Fight Summary](fight-log-summary.md) page.

## Common problems

- **The Process Files button is grayed out** — the queue is empty, or a run is already in progress. Add files first; the button enables itself.
- **A file won't add** — only `.evtc` and `.zevtc` fight logs are accepted; anything else in a drop is ignored.
- **Processed but nothing posted to Discord** — the fight may not have passed your posting filters (**Settings → Watcher & Parsing → Which fights get posted**), or Discord posting is off (**Settings → Discord**). Either way it still counts for fight summaries.
- **A file fails to process** — the log may be incomplete (game or ArcDPS crashed mid-fight). Check the activity feed on Home for the exact error line.
