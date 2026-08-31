"""Upload a combat log to the public dps.report service for a permalink.

Strictly additive to the fight pipeline: our own Elite Insights parsing
stays the data source, and every failure here — network, rate limit,
malformed response — is logged and swallowed so a fight post can never be
lost to an upload problem.
"""

import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

UPLOAD_URL = "https://dps.report/uploadContent"

# dps.report parses server-side anyway; detailedwvw asks for the full WvW
# player breakdown on the hosted page.
UPLOAD_PARAMS = {"json": "1", "generator": "ei", "detailedwvw": "true"}

# Config values for DpsReport/dpsReportTiming. Enabling the feature is only
# honored when the user has explicitly picked one of these.
TIMING_LINK_LATER = "link_later"
TIMING_TOGETHER = "together"
VALID_TIMINGS = (TIMING_LINK_LATER, TIMING_TOGETHER)


def links_active(config) -> bool:
    """True only when the option is on AND a timing mode was chosen."""
    return bool(
        getattr(config, "dpsreport_links_enabled", False)
        and getattr(config, "dpsreport_timing", "") in VALID_TIMINGS
    )


def upload_log(log_file: Path, timeout: int = 120) -> str | None:
    """Upload one .evtc/.zevtc and return the report permalink, or None.

    dps.report enforces a rate limit and reports errors inside the JSON
    body, so both transport errors and in-band errors map to None.
    """
    try:
        with open(log_file, "rb") as f:
            response = requests.post(
                UPLOAD_URL,
                params=UPLOAD_PARAMS,
                files={"file": (log_file.name, f)},
                timeout=timeout,
            )
    except OSError as err:
        logger.warning(f"dps.report upload failed to read {log_file.name}: {err}")
        return None
    except requests.RequestException as err:
        logger.warning(f"dps.report upload failed for {log_file.name}: {err}")
        return None

    if response.status_code != 200:
        logger.warning(
            f"dps.report upload for {log_file.name} returned HTTP "
            f"{response.status_code}"
        )
        return None

    try:
        payload = response.json()
    except ValueError:
        logger.warning(f"dps.report returned non-JSON for {log_file.name}")
        return None

    if isinstance(payload, dict) and payload.get("error"):
        logger.warning(
            f"dps.report refused {log_file.name}: {payload['error']}"
        )
        return None

    permalink = payload.get("permalink") if isinstance(payload, dict) else None
    if not isinstance(permalink, str) or not permalink.startswith("http"):
        logger.warning(f"dps.report response had no permalink for {log_file.name}")
        return None

    logger.info(f"dps.report link for {log_file.name}: {permalink}")
    return permalink
