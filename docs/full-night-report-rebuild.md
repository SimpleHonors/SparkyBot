# Full-night report rebuild

Use this procedure when validating a report against archived ArcDPS logs rather
than the live watcher.

## Selection gate

1. Inventory raw `.zevtc` files by calendar date.
2. Prefer a real session with roughly 15–30 files and about two hours between
   the first and last filename timestamps.
3. Parse every selected file before judging the night. Call it successful only
   after the combined fight outcomes support that claim; log count and duration
   alone do not prove a good night.

## Elite Insights gate

- Use a separate copied config per parallel parser process.
- Do not trust the CLI process exit code by itself. Elite Insights can exit `0`
  while its `Processed` result says `parsed: false` and no detailed JSON exists.
- Success means one `*_detailed_wvw_kill.json` file for every selected raw log.
- A duplicate `AgentItem` exception has occurred in detailed WvW parsing. If the
  current parser fails a log this way, retry only the failed logs with the
  immediately preceding official release and retain the failure/recovery audit.
  Do not silently omit those fights.

## Combiner gate

- Give TopStats a clean input directory containing only the detailed fight JSON
  files. It discovers JSON broadly and may try to parse diagnostic JSON files as
  combat logs.
- Require exactly one combined summary JSON and one standalone Classic HTML.
- Build the Sparky views with the same detailed JSON paths so player weapons,
  rotations, consumables, unique trait procs, and enemy evidence are retained.
- Before exposing comparison candidates, exclude Elite Insights `players`
  records marked `notInSquad` or `friendlyNPC`. Count and report the filtered
  squad identities; the raw `players` collection is not itself a squad roster.

## Delivery gate

- Confirm selected logs, modeled fights, and first/last fight timestamps agree.
- Run the complete tests, then exercise the generated file in real Chrome:
  every sortable header in both directions, desktop and narrow table layout,
  player comparison, labeled skill-chart popup, dialog backdrop close, dialog
  scroll reset, and rendered player/enemy build evidence.
- Exercise every Player Compare option. Each must have real damage-share or
  cast-share evidence, and the picker must sort Class, Elite Spec, then player.
  Enemy subgroup slots must not turn profession-level evidence into a claimed
  per-slot role.
- Stage a regular HTML file on the SMB-accessible project share and record its
  byte size and SHA-256. Report generation must not imply publishing or deploy.
