# Fight Summary — making and reading the Combined Fight Log Summary

The **Fight Summary** sidebar page (`Ctrl+R`) builds a Combined Fight Log Summary: one click turns your latest fights into a stats page you can share with your guild. Big reports shrink automatically to help them fit Discord's free upload limit.

In one-button mode you'll rarely need this page — **End Run** on [Home](home.md) builds the same report for you. Come here when you want to hand-pick fights or rebuild an older session.

## Picking fights

- **Quick pick chips** — **Last 12 hours**, **Today**, **All**, **None** tick the matching fights in one click.
- **The fight list** — every processed fight in your log folder, with **Date**, **Fight**, and **Size** columns. Click a fight to tick it; hold **Shift** and click to tick a whole range. Click a column heading to sort. A counter shows "N fights found · M selected".
- **No fights here yet** — if the list is empty, add your `.evtc` or `.zevtc` files first; SparkyBot will process them, then they show up here. The **Add fight files...** button jumps to [Process Files](process-files.md).

## Building the report

- **Report name (optional)** — leave blank for the default: `Combined Fight Log Summary <date> (<n> fights)`.
- **Make fight summary** — the big button. Progress shows as "Reading fight X of Y…", "Crunching the numbers…", "Building your page…", ending with "Done! … is ready."
- **Open the report** — opens the finished page in your browser.
- **Post to Discord** — sends it to the channel chosen under **Settings → Discord → Fight Summary**.
- **Show details** — appears after a problem; shows the full error text (selectable, so you can copy it).

## Reading the report

The report is a single web page — open it locally or download it from Discord; it works offline.

- **Report style tabs** *(in development — newer reports only)*: a bar at the top of the page lets each viewer switch styles. **Simple** is the headline view — big cards (Fights, Enemies Killed, Enemies Downed, Our Deaths, Our Downs, K/D Ratio) plus the main leaderboards. **Classic** is the full stats viewer with every table and chart. The page remembers each viewer's last choice.
- **The Classic view** groups stats per fight and for the whole session: overview, damage output, support, defense, leaderboards, and more. Use the tab menu inside the page to move around.
- **Poison Coverage tab** — included when **Settings → Fight Summary → Include poison coverage page in the report** is on. It shows who kept poison on the enemy (poison reduces enemy healing by a third): a bubble chart plus a sortable table of every applier with **Hits**, **Apps/min**, and **Output/sec**. Note: Relic of the Demon Queen is not tracked by the parser and is excluded.

## Common problems

- **"No fights selected."** — tick fights in the list or use the quick picks, then press the big button again.
- **"Done! … — but N fights couldn't be read and were left out."** — a few logs were unreadable (usually cut-off files from a crash). **Show details** lists exactly which; the rest of the report is fine.
- **"Something went wrong sending to Discord"** — check the webhook under **Settings → Discord** and your connection, then press **Post to Discord** again. The report file itself is already built and safe.
- **Report too big for Discord** — reports shrink automatically, and **Settings → Fight Summary → Always zip Discord uploads** squeezes further. You can always share the file from the report output folder instead.
- **Reports build slowly** — turn on **Settings → Fight Summary → Fast reports (reuse live fight data)**; reports then reuse analysis SparkyBot already did after each fight.
- **No Poison Coverage tab** — enable it in **Settings → Fight Summary**, then build a new report; it isn't added to already-built pages.
