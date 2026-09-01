"""Credits and respectful positioning for the WvW log-tool ecosystem."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InteropProject:
    name: str
    url: str
    license: str
    celebrates: str
    relationship: str


# These are links and original descriptions, not vendored code or assets.
# License labels were checked against each repository at the reviewed revision;
# "no license file" is intentionally explicit rather than an inferred grant.
INTEROP_PROJECTS = (
    InteropProject(
        "ArcDPS",
        "https://www.deltaconnected.com/arcdps/",
        "upstream terms",
        "Creates the EVTC fight logs used across the WvW reporting ecosystem.",
        "SparkyBot can detect ArcDPS and reuse its configured log folder.",
    ),
    InteropProject(
        "GW2 Elite Insights",
        "https://github.com/baaron4/GW2-Elite-Insights-Parser",
        "MIT",
        "Deep combat parsing and detailed reports across many game modes.",
        "Elite Insights is the parser SparkyBot uses for fight data.",
    ),
    InteropProject(
        "MzFightReporter",
        "https://github.com/Swedemon/MzFightReporter",
        "MIT",
        "Compact WvW Discord reports and an optional Twitch workflow.",
        "MzFightReporter directly inspired SparkyBot's live fight-report workflow; settings can be moved both ways.",
    ),
    InteropProject(
        "PlenBot Log Uploader",
        "https://github.com/Plenyx/PlenBotLogUploader",
        "MIT",
        "Mature upload filtering by team, encounter, and webhook.",
        "SparkyBot can import from and export to its documented settings format.",
    ),
    InteropProject(
        "AxiBridge",
        "https://github.com/darkharasho/axibridge",
        "GPL-3.0",
        "Polished graphical WvW reports and web publishing.",
        "SparkyBot can import its local setup; the adapter is original code and copies no AxiBridge assets.",
    ),
    InteropProject(
        "TopStatsAIO",
        "https://github.com/darkharasho/TopStatsAIO",
        "MIT",
        "A hands-on desktop interface for deep top-stat and session analysis.",
        "SparkyBot can move the raw-log location both ways.",
    ),
    InteropProject(
        "WvW Insights",
        "https://github.com/Retherichus/wvw-insights",
        "no license file published at reviewed revision",
        "In-game Nexus integration, batch uploads, and session management.",
        "SparkyBot can import its settings format; no WvW Insights code or assets are reused.",
    ),
    InteropProject(
        "EVTC_parser",
        "https://github.com/Drevarr/EVTC_parser",
        "GPL-3.0",
        "A direct watcher-and-webhook workflow that is easy to automate.",
        "SparkyBot can move the log path and fight webhook both ways.",
    ),
    InteropProject(
        "GW2 EI Log Combiner",
        "https://github.com/Drevarr/GW2_EI_log_combiner",
        "GPL-3.0",
        "Configurable whole-session reports built from Elite Insights JSON.",
        "SparkyBot uses the Combiner for nightly reports and its compressed standalone-HTML option for smaller files.",
    ),
    InteropProject(
        "arcdps_top_stats_parser",
        "https://github.com/Drevarr/arcdps_top_stats_parser",
        "GPL-3.0",
        "Highly configurable top-stat analysis with direct control of its report model.",
        "SparkyBot recognizes its log workflow but does not copy its code or treat generated output as raw logs.",
    ),
)


# Keep in-app credits narrow: these are dependencies or direct inspiration.
# Everything else belongs in the clearly separate neighboring-tools catalog.
CREDIT_PROJECT_NAMES = frozenset(
    {"ArcDPS", "GW2 Elite Insights", "MzFightReporter", "GW2 EI Log Combiner"}
)
CREDIT_PROJECTS = tuple(
    project for project in INTEROP_PROJECTS if project.name in CREDIT_PROJECT_NAMES
)


INTEROP_PLAIN_PROMISE = (
    "SparkyBot should be the tool people choose, never the tool they are trapped in. "
    "Imports and exports are explicit one-time actions. Imports leave the source "
    "tool's files untouched. Exports back up an existing target before updating "
    "it, and credentials that do not map safely are left alone."
)
