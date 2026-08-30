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
        "Creates the EVTC combat logs that make this entire ecosystem possible.",
        "SparkyBot detects its settings and log folder; ArcDPS remains the source of truth.",
    ),
    InteropProject(
        "GW2 Elite Insights",
        "https://github.com/baaron4/GW2-Elite-Insights-Parser",
        "MIT",
        "The authoritative, deeply capable parser with encounter coverage no reporting front end should try to reinvent.",
        "It powers SparkyBot's parsing and is credited and licensed as a bundled third-party component.",
    ),
    InteropProject(
        "MzFightReporter",
        "https://github.com/Swedemon/MzFightReporter",
        "MIT",
        "Excellent compact, battle-first WvW reports with a proven live Discord and optional Twitch workflow.",
        "We used it, learned from it, and openly credit it as the original inspiration for SparkyBot.",
    ),
    InteropProject(
        "PlenBot Log Uploader",
        "https://github.com/Plenyx/PlenBotLogUploader",
        "MIT",
        "A mature general-purpose upload workflow with strong team, encounter, and webhook filtering controls.",
        "SparkyBot can import from and export to its documented settings formats.",
    ),
    InteropProject(
        "AxiBridge",
        "https://github.com/darkharasho/axibridge",
        "GPL-3.0",
        "A polished WvW-native visual experience with strong web publishing and readable graphical fight reports.",
        "SparkyBot provides original format-only interoperability; no AxiBridge code or assets are copied or bundled.",
    ),
    InteropProject(
        "TopStatsAIO",
        "https://github.com/darkharasho/TopStatsAIO",
        "MIT",
        "A focused, hands-on all-in-one interface for deep top-stat and session-analysis workflows.",
        "SparkyBot can move its raw-log location both ways and never mistakes generated EI output for raw logs.",
    ),
    InteropProject(
        "WvW Insights",
        "https://github.com/Retherichus/wvw-insights",
        "no license file published at reviewed revision",
        "Convenient in-game Nexus integration, batch uploads, and session management without leaving Guild Wars 2.",
        "Format-only interoperability; no source, assets, or binaries are reused.",
    ),
    InteropProject(
        "EVTC_parser",
        "https://github.com/Drevarr/EVTC_parser",
        "GPL-3.0",
        "A direct, understandable watcher-and-webhook workflow that is easy to inspect and automate.",
        "SparkyBot can move the raw-log path and fight webhook both ways using original adapter code.",
    ),
    InteropProject(
        "GW2 EI Log Combiner",
        "https://github.com/Drevarr/GW2_EI_log_combiner",
        "GPL-3.0",
        "Deep, configurable whole-session aggregation across Elite Insights JSON output, including compressed standalone reports.",
        "SparkyBot invokes a user-approved runtime copy separately and enables that path by default so big night reports can fit Discord's standard unpaid attachment limit. Its v1.8.0 release credits SimpleHonors for the submitted capability.",
    ),
    InteropProject(
        "arcdps_top_stats_parser",
        "https://github.com/Drevarr/arcdps_top_stats_parser",
        "GPL-3.0",
        "Powerful and highly configurable top-stat analysis for groups that want direct control over the report model.",
        "SparkyBot recognizes the workflow but does not copy its code or misread its generated-output folders.",
    ),
    InteropProject(
        "GW2-WVW-Teams",
        "https://github.com/Drevarr/GW2-WVW-Teams",
        "GPL-3.0",
        "Purpose-built roster, alliance, and team-composition embeds from CSV, API, or Google Sheets data.",
        "It is adjacent roster tooling, not an EVTC log reporter. SparkyBot credits it but never imports its roster webhook as Logspam.",
    ),
    InteropProject(
        "TopStatsDash",
        "https://github.com/Drevarr/TopStatsDash",
        "GPL-3.0",
        "Interactive exploration and custom-formula analysis of the SQLite session databases produced by the Drevarr stats workflow.",
        "It consumes generated analysis databases rather than raw ArcDPS settings, so there is no honest config adapter to offer.",
    ),
)


INTEROP_PLAIN_PROMISE = (
    "SparkyBot should be the tool people choose, never the tool they are trapped in. "
    "Imports and exports are explicit one-time actions. Other tools' files stay "
    "theirs, existing settings are preserved and backed up, and credentials that "
    "do not map safely are left alone."
)
