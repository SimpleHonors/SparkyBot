# WvW Log Tool Interoperability

SparkyBot should be the tool people choose, never the tool they are trapped in.
The WvW log community already had years of good work before SparkyBot existed.
We have used these tools, learned from them, and were directly influenced by
them—especially MzFightReporter, Elite Insights, the Drevarr stats stack, and
the broader TopStats workflow.

The setup screen therefore supports explicit, one-time moves in both
directions:

- **Import from Another Log Tool** reads only compatible log paths, parser
  paths, and Discord webhook destinations. It never runs the other program or
  imports bot tokens, API keys, Twitch credentials, account data, or history.
- **Create Setup for Another Log Tool** writes that tool's documented settings
  format. Existing settings are patched narrowly, backed up, and restored if a
  multi-file update fails.
- The confirmation screen names every value before it moves. Webhook URLs are
  never exposed in previews.
- Continuous background sync is deliberately not part of this release. Two
  programs silently rewriting one another's settings is surprising and unsafe;
  migration remains an explicit user action.

## The tools we celebrate

Different jobs deserve different tools. These are not endorsements, rankings,
or claims of affiliation.

| Project | Where it may be the better fit | Relationship to SparkyBot |
|---|---|---|
| [ArcDPS](https://www.deltaconnected.com/arcdps/) | It creates the EVTC combat logs that make the entire ecosystem possible. | ArcDPS settings and its chosen log directory remain the source of truth. |
| [GW2 Elite Insights](https://github.com/baaron4/GW2-Elite-Insights-Parser) | The authoritative parser has encounter depth and raw-log expertise a reporting front end should not reinvent. | Its MIT CLI powers SparkyBot parsing and is credited as a bundled dependency. |
| [MzFightReporter](https://github.com/Swedemon/MzFightReporter) | Proven, compact battle-first WvW reports and an optional Twitch workflow. | We used it and openly credit it as SparkyBot's original inspiration. Import and export supported. |
| [PlenBot Log Uploader](https://github.com/Plenyx/PlenBotLogUploader) | Mature general-purpose uploading with detailed team, encounter, and webhook filters. | Import and export supported. |
| [AxiBridge](https://github.com/darkharasho/axibridge) | A polished WvW-native visual experience, graphical fight summaries, and web publishing. | Original format-only adapters support switching both ways; no GPL code or assets are copied. |
| [TopStatsAIO](https://github.com/darkharasho/TopStatsAIO) | A focused, hands-on all-in-one interface for deep top-stat and session-analysis workflows. | Its raw ArcDPS folder moves both ways. Generated EI output is never mistaken for raw logs. |
| [WvW Insights](https://github.com/Retherichus/wvw-insights) | In-game Nexus integration, batch uploads, and session management without leaving Guild Wars 2. | Format-only import and export; no source or assets reused. |
| [EVTC_parser](https://github.com/Drevarr/EVTC_parser) | A direct, understandable watcher-and-webhook workflow that is easy to inspect and automate. | Raw-log path and fight webhook move both ways through original adapter code. |
| [GW2 EI Log Combiner](https://github.com/Drevarr/GW2_EI_log_combiner) | Deep, configurable whole-session aggregation over Elite Insights JSON, including compressed standalone HTML. | SparkyBot runs a user-approved download separately and enables the supported compressed-report path by default. Its [v1.8.0 release](https://github.com/Drevarr/GW2_EI_log_combiner/releases/tag/v1.8.0) credits SimpleHonors for submitting that capability. |
| [arcdps_top_stats_parser](https://github.com/Drevarr/arcdps_top_stats_parser) | Highly configurable top-stat analysis for groups that want direct control over the report model. | Workflow recognized; no code reuse and no false import of its generated-output directory. |
| [GW2-WVW-Teams](https://github.com/Drevarr/GW2-WVW-Teams) | Purpose-built roster, alliance, and team-composition embeds from CSV, API, or Sheets data. | Adjacent roster tooling, not an EVTC log reporter. Its similarly named `config.ini` webhook is never imported as Logspam. |
| [TopStatsDash](https://github.com/Drevarr/TopStatsDash) | Interactive exploration and custom-formula analysis of generated SQLite session databases. | Adjacent analysis UI with no compatible raw-log config to import or export. |

## Format support

“Import” means a real, tested adapter exists. “Export” means SparkyBot can
create or safely patch files the other tool actually reads. A dash is honest:
the project may be a parser, service, or workflow with no compatible persistent
setting to write.

| Tool | Import | Export | Compatible values |
|---|:---:|:---:|---|
| [AxiBridge / ArcBridge](https://github.com/darkharasho/axibridge) | Yes | Yes | Raw logs, named fight webhooks, named report/nightly webhooks |
| [TopStatsAIO](https://github.com/darkharasho/TopStatsAIO) | Yes | Yes | Raw-log folder; per-run webhook is not falsely presented as persistent |
| [PlenBot](https://github.com/Plenyx/PlenBotLogUploader) | Yes | Yes | Raw logs, GW2 path, named active webhooks |
| [MzFightReporter](https://github.com/Swedemon/MzFightReporter) | Yes | Yes | Raw logs, three named webhooks, active route, adjacent EI executable |
| [WvW Insights](https://github.com/Retherichus/wvw-insights) | Yes | Yes | Raw logs and saved webhooks |
| [EVTC_parser](https://github.com/Drevarr/EVTC_parser) | Yes | Yes | Raw logs and fight webhook |
| [GW2 EI Log Combiner](https://github.com/Drevarr/GW2_EI_log_combiner) | Yes | Yes | Nightly webhook only; `input_directory` is generated EI JSON and is never treated as raw logs |
| [GW2 Manny Uploader](https://github.com/LoganWal/GW2-MannyUploader) | Yes | — | Raw logs; separate credentials intentionally ignored |
| [GW2Scratch Log Manager](https://github.com/gw2scratch/evtc) | Yes | — | One or more raw-log roots |
| [Nexus Wingman Uploader](https://github.com/belst/nexus-wingman-uploader) | Yes | — | Raw-log folder |
| [WvW Log Uploader](https://github.com/Haxshoo/WvW-Log-Uploader) | Yes | — | Raw logs and fight webhook |
| [GW2 Commanders Watch](https://github.com/theextendedname/GW2_Commanders_Watch) | Yes | — | Watched raw-log folder |
| [L0G-101086](https://github.com/jacob-keller/L0G-101086) | Yes | — | Raw logs and named multi-guild webhooks |
| [arclog](https://github.com/konradgj/arclog) | Yes | — | Raw-log folder; user token ignored |
| [toxic-elitist](https://github.com/kenogs/toxic-elitist) | Yes | — | Raw logs; Discord bot token/channel IDs ignored |
| [LogUploader2](https://github.com/ProfBits/LogUploader2) | Yes | — | Raw logs; protected webhook storage left untouched |
| [arcdps-uploader](https://github.com/nbarrios/arcdps-uploader) | Yes | — | WvW webhook rows read from its local database; history ignored |
| [AxiPulse](https://github.com/darkharasho/axipulse) | Yes | — | Raw-log folder |
| [GW2-WVW-Teams](https://github.com/Drevarr/GW2-WVW-Teams) | — | — | Roster/alliance webhook is intentionally not a fight-log destination |
| [TopStatsDash](https://github.com/Drevarr/TopStatsDash) | — | — | Reads generated SQLite analysis databases; no raw-log settings |

## Where SparkyBot differs today

SparkyBot invokes the combiner's `--standalone-html` option and sets
`compress_standalone_html = true` by default. The result is one smaller,
self-contained end-of-night report that opens in a current browser. On large
nights, that often brings the file under Discord's standard unpaid attachment
limit instead of requiring paid upload headroom. This is **report
compression**, not compression or deletion of the original `.evtc` fight
logs.

The capability is upstream and available to anyone using the combiner
directly. At the revisions reviewed on 2026-08-29, TopStatsAIO downloaded the
current combiner but its app workflow invoked the normal input/config path and
produced JSON/TID files for viewer import; it did not invoke the standalone
report option. None of the other reviewed direct tools referenced the option
or its config key. SparkyBot is therefore the only reviewed end-user app we
confirmed enables it automatically. That is a current workflow difference,
not an exclusivity claim, and neighboring tools may adopt it at any time.

Manual file selection remains available when a portable or nonstandard install
cannot be found. Automatic discovery is bounded to known AppData/addon paths and
common portable-app locations; it never scans an entire drive.

## License boundary

The adapters are original SparkyBot code based on publicly visible settings
schemas. They do not copy algorithms, source files, artwork, documentation, or
binaries from another application. Interoperating with a file format does not
bundle that project's implementation.

| Project family | Upstream license at reviewed revision | SparkyBot posture |
|---|---|---|
| MzFightReporter, PlenBot, TopStatsAIO, Elite Insights | MIT | Linked and credited; only Elite Insights is bundled, with its license text. |
| AxiBridge / AxiPulse | GPL-3.0 | Format-only interoperability; no source or binary reuse. |
| EVTC_parser, GW2 EI Log Combiner, arcdps_top_stats_parser, GW2-WVW-Teams, TopStatsDash | GPL-3.0 | Original format adapters where compatible; adjacent projects are linked and credited without code, asset, or settings reuse. The combiner remains a separately downloaded/executed program. |
| WvW Insights | No license file published at the reviewed revision | Format-only interoperability and zero code/asset reuse. |
| L0G-101086 | BSD-3-Clause | Linked and credited; settings format only. |
| Wingman uploader, GW2Scratch, Commanders Watch, arclog, LogUploader2, arcdps-uploader | MIT | Linked and credited; settings format only. |
| Manny Uploader, WvW Log Uploader, toxic-elitist | No license file found at the reviewed revision | Format-only interoperability and zero code/asset reuse. |

License labels should be rechecked when an adapter changes because upstream
projects can relicense. SparkyBot's definitive bundled-component compliance
record remains [LICENSES.md](../LICENSES.md).
