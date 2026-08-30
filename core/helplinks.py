"""Screen -> help-page mapping for the in-app Help buttons.

One place that knows where the per-screen help pages live: the docs/help/
folder of the public repository (docs/help/README.md is the human-readable
index; its kebab-case file names are the slugs used here). Help opening is
always QDesktopServices.openUrl — the browser decides what happens offline,
this module never touches the network.

Keys come from the three screens the app can be showing: the sidebar page
keys (core.main_window.NAV_ENTRIES), the settings category names
(core.settings_dialog.BASE_CATEGORIES / AI_CATEGORIES) and the setup-wizard
page ids (core.setup_wizard, range(12) constants, duplicated here as plain
ints to keep this module import-light for tests). An unknown screen falls
back to README.md, so a Help button can never dead-end.
"""

from pathlib import Path

HELP_BASE = "https://github.com/SimpleHonors/SparkyBot/blob/main/docs/help/"

README_SLUG = "README.md"

# docs/help/ in this checkout — used by tests to prove every mapped slug
# is a real page; the app itself never reads it.
HELP_DOCS_DIR = Path(__file__).resolve().parent.parent / "docs" / "help"

# Sidebar page keys -> docs/help slug.
NAV_HELP_SLUGS = {
    "home": "home.md",
    "raid-report": "fight-log-summary.md",
    "process-files": "process-files.md",
    "calibration": "calibration.md",
    "settings": README_SLUG,  # the entry opens the dialog: land on the index
}

# Settings dialog category names -> docs/help slug.
SETTINGS_HELP_SLUGS = {
    "Discord": "settings-discord.md",
    "Fight Reports": "settings-fight-reports.md",
    "Watcher & Parsing": "settings-watcher-parsing.md",
    "Fight Summary": "settings-fight-summary.md",
    "Twitch": "settings-twitch.md",
    "Application": "settings-application.md",
    "Updates": "settings-updates.md",
    "About": "settings-about.md",
    "AI Commentary": "settings-ai-commentary.md",
    "Voice": "settings-voice.md",
    "Vocabulary": "settings-vocabulary.md",
}

# Setup-wizard page ids -> docs/help slug (id order mirrors setup_wizard's
# PAGE_WELCOME..PAGE_COMPLETE = range(12)).
WIZARD_HELP_SLUGS = {
    0: "welcome-setup.md",
    1: "setup-ai-optin.md",
    2: "setup-usage-mode.md",
    3: "setup-dependencies.md",
    4: "setup-parser.md",
    5: "setup-log-folder.md",
    6: "setup-discord.md",
    7: "setup-twitch.md",
    8: "setup-ai-commentary.md",
    9: "setup-voice.md",
    10: "setup-behavior.md",
    11: "setup-complete.md",
}


def help_slug(key) -> str:
    """The docs/help file name for a screen key (page key, category name
    or wizard page id); anything unknown falls back to the README index."""
    for mapping in (NAV_HELP_SLUGS, SETTINGS_HELP_SLUGS, WIZARD_HELP_SLUGS):
        if key in mapping:
            return mapping[key]
    return README_SLUG


def help_url(key) -> str:
    """Full GitHub URL of the help page for a screen key."""
    return HELP_BASE + help_slug(key)
