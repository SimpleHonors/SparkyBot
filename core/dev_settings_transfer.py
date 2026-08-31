"""Dev-mode FULL settings export/import — secrets included, loudly labeled.

Completely separate from core/shareable_config.py: that path is an
allowlist that deliberately strips secrets for sharing between guilds.
This path exists for developers and power users moving their OWN complete
setup (Discord webhooks, API keys, tokens, everything) between machines,
and it only exists at all when SparkyBot was launched with --dev.
"""

import logging
import os
from datetime import datetime, timezone
from configparser import ConfigParser
from pathlib import Path

logger = logging.getLogger(__name__)

DEV_MODE_ENV = "SPARKY_DEV_MODE"

# Marker section stamped into every export; imports refuse files without
# it so a guild setup file or random INI can't be swallowed by mistake.
MARKER_SECTION = "SparkyBotFullExport"

SUGGESTED_FILENAME = "sparkybot-FULL-settings-WITH-SECRETS.ini"

_HEADER = """\
# =====================================================================
# SPARKYBOT FULL SETTINGS EXPORT — THIS FILE CONTAINS SECRETS
# Discord webhook URLs, API keys, and tokens are all in here in plain
# text. Anyone holding this file can post to your Discord and spend
# your API credits. Do NOT share it, upload it, or attach it anywhere.
# To share safe settings with another guild, use File > Create Guild
# Setup File instead.
# =====================================================================
"""


def dev_mode_active() -> bool:
    """True only when the app was launched with the --dev flag."""
    return os.environ.get(DEV_MODE_ENV) == "1"


def export_full_settings(config, dest: Path) -> Path:
    """Write the complete live config — every section, secrets and all."""
    dest = Path(dest)
    snapshot = ConfigParser()
    snapshot.read_dict(config._config)
    snapshot.add_section(MARKER_SECTION)
    snapshot.set(MARKER_SECTION, "exportedAt",
                 datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    snapshot.set(MARKER_SECTION, "containsSecrets", "true")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(_HEADER)
        snapshot.write(f)
    logger.info(f"Dev full-settings export written to {dest}")
    return dest


def import_full_settings(config, source: Path) -> int:
    """Replace the live config wholesale from a full export.

    Returns the number of keys applied. Raises ValueError for anything
    that is not a SparkyBot full export.
    """
    source = Path(source)
    incoming = ConfigParser()
    try:
        read_ok = incoming.read(source, encoding="utf-8")
    except Exception as exc:
        raise ValueError(
            f"Could not read {source.name} as an INI file: {exc}") from exc
    if not read_ok:
        raise ValueError(f"Could not read {source.name} as an INI file.")
    if not incoming.has_section(MARKER_SECTION):
        raise ValueError(
            f"{source.name} is not a SparkyBot full settings export. "
            "(Guild setup files and other configs are refused here on "
            "purpose — this import replaces secrets too.)"
        )

    applied = 0
    for section in incoming.sections():
        if section == MARKER_SECTION:
            continue
        for key, value in incoming.items(section):
            if not config._config.has_section(section):
                config._config.add_section(section)
            config._config.set(section, key, value)
            applied += 1
    config.save()
    logger.info(f"Dev full-settings import applied {applied} keys "
                f"from {source}")
    return applied
