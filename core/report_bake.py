"""Summarize and enrich standalone reports created by the upstream combiner."""

import base64
import json
import logging
import mimetypes
import os
import re
import tempfile
from pathlib import Path

from core.report_pack import is_packed, pack_html, unpack_html

logger = logging.getLogger(__name__)

_STORE_OPENER = (
    '<script class="tiddlywiki-tiddler-store" type="application/json">'
)

# Marker so the sticky-header style can be detected and never injected twice.
_STICKY_STYLE_MARKER = "data-sparkybot-sticky-headers"

# Authored by SparkyBot. Pins report table headers to the top of the viewport
# while scrolling so long fights keep their column labels visible. Verified
# against the live combiner DOM: report tables render their header row inside
# <thead> as <th> cells (Bootstrap "thead-dark" styling), so the fill matches
# that theme's header color (#343a40 / white text as computed on the real
# report) and must be solid or scrolling rows bleed through the pinned header.
# z-index stays below the viewer's sticky tiddler title bar (z-index 500).
_STICKY_HEADER_STYLE = """<style data-sparkybot-sticky-headers="1">
table th {
    position: sticky;
    top: 0;
    z-index: 2;
    background-color: #343a40;
    color: #ffffff;
    background-clip: padding-box;
}
</style>"""

# Marker so the guild-icon style can be detected and never injected twice.
_GUILD_ICON_STYLE_MARKER = "data-sparkybot-guild-icon"

# The data-URI tiddler ANY skin can read for the configured guild icon.
# Deliberately a plain-text tiddler holding a full data: URI so a skin can
# drop it straight into <img src=...> without knowing about SparkyBot.
_GUILD_ICON_TIDDLER_TITLE = "$:/sparkybot/guild-icon"

# Icons above this file size are skipped with a warning rather than baked
# into the report: base64 adds ~33% on top of the bytes and a report is a
# single offline file we do not want to bloat with a giant logo.
_GUILD_ICON_MAX_BYTES = 512 * 1024

# Authored by SparkyBot. Stamps the guild icon into the report header, in the
# logo slot the viewer's SiteTitle area renders: the live TiddlyWiki viewer
# transcludes $:/SiteTitle into <h1 class="tc-site-title"> inside
# <div class="tc-sidebar-header">, so the pseudo-element sits upper-left of
# the header above the title text. A pure-CSS placement (no script) applies
# whenever the viewer finishes rendering, whatever order that takes.
_GUILD_ICON_STYLE_TEMPLATE = """<style data-sparkybot-guild-icon="1">
.tc-sidebar-header::before {{
    content: "";
    display: block;
    width: 40px;
    height: 40px;
    margin-bottom: 8px;
    background-image: url("{data_uri}");
    background-repeat: no-repeat;
    background-position: left center;
    background-size: contain;
}}
</style>"""

_CLOSE_HEAD_RE = re.compile(r"</head\s*>", re.IGNORECASE)
_OPEN_HEAD_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)


def _inject_style_into_head(
    full_html: str,
    style_block: str,
    marker: str = _STICKY_STYLE_MARKER,
    what: str = "sticky table headers",
) -> str:
    """Splice a style block into a report document head, degrading to no-op.

    Insertion points are tried in order: just before ``</head>``, then just
    after the opening ``<head>`` tag. When neither is found the string comes
    back unchanged — a report whose structure has drifted upstream still
    generates, it simply ships without the styling. Injection is idempotent:
    the ``marker`` attribute is what tells an already-styled report apart.
    """
    if marker in full_html:
        return full_html

    match = _CLOSE_HEAD_RE.search(full_html)
    if match is not None:
        pos = match.start()
        return full_html[:pos] + style_block + full_html[pos:]

    match = _OPEN_HEAD_RE.search(full_html)
    if match is not None:
        pos = match.end()
        return full_html[:pos] + "\n" + style_block + full_html[pos:]

    logger.warning(
        "no <head> insertion point for %s — report will ship unstyled", what
    )
    return full_html


def _append_tiddler_block(content: str, tiddlers: list[dict]) -> str:
    last_store_start = content.rfind(_STORE_OPENER)
    if last_store_start == -1:
        raise ValueError("not a TiddlyWiki store-format HTML")
    close_tag = "</script>"
    last_close = content.find(close_tag, last_store_start)
    if last_close == -1:
        raise ValueError("tiddler store block in the viewer is never closed")

    serialized = json.dumps(tiddlers, ensure_ascii=False, separators=(",", ":"))
    safe_json = serialized.replace("<", "\\u003C")
    new_block = f'{_STORE_OPENER}{safe_json}</script>'
    insert_pos = last_close + len(close_tag)
    return content[:insert_pos] + new_block + content[insert_pos:]


def _atomic_write_text(path: Path, content: str) -> None:
    fd, part_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".part"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(part_path, path)
        part_path = None
    finally:
        if part_path is not None:
            try:
                os.unlink(part_path)
            except OSError:
                logger.warning("could not remove partial report %s", part_path)


def merge_augmented_tiddlers(
    standalone_html: Path,
    original_tiddlers: list[dict],
    augmented_tiddlers: list[dict],
) -> Path:
    """Append SparkyBot-only tiddler changes to an upstream standalone report.

    The combiner builds its standalone HTML before SparkyBot can add optional
    poison coverage tiddlers. Append only new or changed titles so the
    upstream report remains the canonical bake without duplicating its full
    summary payload.
    """
    if not isinstance(original_tiddlers, list) or not isinstance(
        augmented_tiddlers, list
    ):
        raise ValueError("tiddler JSON must be a list")

    original_by_title = {
        item.get("title"): item
        for item in original_tiddlers
        if isinstance(item, dict) and item.get("title") is not None
    }
    changed = [
        item
        for item in augmented_tiddlers
        if isinstance(item, dict)
        and (
            item.get("title") is None
            or original_by_title.get(item.get("title")) != item
        )
    ]
    if not changed:
        return Path(standalone_html)

    report_path = Path(standalone_html)
    packed = report_path.read_text(encoding="utf-8")
    full_html = unpack_html(packed)
    full_html = _append_tiddler_block(full_html, changed)
    _atomic_write_text(report_path, pack_html(full_html))
    return report_path


def apply_sticky_table_headers(standalone_html: Path) -> Path:
    """Make an upstream standalone report's table headers sticky on scroll.

    Extends the poison-tiddler augmentation path: unpack the compressed
    report, splice the sticky-header style into the document head, and repack
    it atomically. Unpacking is skipped for reports that were never compressed.
    Any failure (missing insertion point, corrupt payload, unreadable file) is
    logged and swallowed so the report still ships exactly as the combiner
    baked it — styling must never fail a report.
    """
    report_path = Path(standalone_html)
    try:
        packed = report_path.read_text(encoding="utf-8")
        was_packed = is_packed(packed)
        full_html = unpack_html(packed) if was_packed else packed
        styled = _inject_style_into_head(full_html, _STICKY_HEADER_STYLE)
        if styled == full_html:
            return report_path
        _atomic_write_text(
            report_path, pack_html(styled) if was_packed else styled
        )
    except Exception:
        logger.warning(
            "Sticky table-header styling failed — "
            "report will ship unstyled",
            exc_info=True,
        )
    return report_path


_URL_RE = re.compile(r"https?://", re.IGNORECASE)


def _guild_icon_data_uri(icon_source: str) -> str | None:
    """Turn a configured guild-icon source into a data URI, or None to skip.

    Mirrors the Discord-embed resolution in Config.get_thumbnail_path: a
    relative path is taken against the install root, with the legacy
    assets/-folder fallback. URLs are skipped outright — a report is a
    single offline file and must not reference anything fetched at view
    time (nor leak where the report came from). Files above the cap are
    skipped rather than downscaled: the base64 payload would bloat every
    shipped report for one logo.
    """
    if not icon_source or not icon_source.strip():
        return None

    source = icon_source.strip()
    if _URL_RE.match(source):
        logger.warning(
            "guild icon is configured as a URL (%s) — skipped: "
            "reports must build offline",
            source,
        )
        return None

    icon_path = Path(source)
    if not icon_path.is_absolute():
        home_dir = Path(__file__).parent.parent
        icon_path = home_dir / source
        if not icon_path.exists() and not source.startswith('assets'):
            icon_path = home_dir / "assets" / source

    if not icon_path.exists():
        logger.warning(
            "guild icon file not found (%s) — report will ship without the icon",
            icon_path,
        )
        return None

    try:
        size = icon_path.stat().st_size
        if size > _GUILD_ICON_MAX_BYTES:
            logger.warning(
                "guild icon is %d bytes (cap %d) — report will ship "
                "without the icon",
                size,
                _GUILD_ICON_MAX_BYTES,
            )
            return None
        raw = icon_path.read_bytes()
    except OSError:
        logger.warning(
            "guild icon could not be read (%s) — report will ship "
            "without the icon",
            icon_path,
            exc_info=True,
        )
        return None

    guessed = mimetypes.guess_type(icon_path.name)
    mime = guessed[0] if guessed else None
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _inject_guild_icon(full_html: str, data_uri: str) -> str:
    """Add the guild-icon tiddler and the header style to a report document.

    Two things land in the report, in whichever state it arrives:
    1. A new tiddler store block with the $:/sparkybot/guild-icon tiddler —
       a text tiddler whose text is the full data URI, so ANY skin can read
       it (drop it straight into an <img src>) without knowing SparkyBot
       authored it.
    2. The header style pinning the icon into the SiteTitle area's logo slot.
    Injection is idempotent; a report that already carries either part is
    left as it is.
    """
    if (
        _GUILD_ICON_STYLE_MARKER in full_html
        and f'"title":"{_GUILD_ICON_TIDDLER_TITLE}"' in full_html
    ):
        return full_html

    styled = full_html
    if _GUILD_ICON_STYLE_MARKER not in styled:
        style_block = _GUILD_ICON_STYLE_TEMPLATE.format(data_uri=data_uri)
        styled = _inject_style_into_head(
            styled,
            style_block,
            marker=_GUILD_ICON_STYLE_MARKER,
            what="the guild icon",
        )

    if f'"title":"{_GUILD_ICON_TIDDLER_TITLE}"' not in styled:
        tiddler = {
            "title": _GUILD_ICON_TIDDLER_TITLE,
            "text": data_uri,
            "type": "text/plain",
        }
        styled = _append_tiddler_block(styled, [tiddler])

    return styled


def apply_guild_icon(standalone_html: Path, icon_source: str | None) -> Path:
    """Stamp the configured guild icon onto an upstream standalone report.

    Same augmentation path as the sticky table headers: resolve the icon,
    unpack the compressed report, inject the $:/sparkybot/guild-icon tiddler
    plus the header style, and repack atomically. Unpacking is skipped for
    reports that were never compressed. Any failure — unreadable or oversized
    icon, URL source, corrupt payload, no store block to append to — is
    logged and swallowed so the report still ships exactly as the combiner
    baked it: the icon must never fail a report.
    """
    report_path = Path(standalone_html)
    try:
        data_uri = _guild_icon_data_uri(icon_source or "")
        if data_uri is None:
            return report_path
        packed = report_path.read_text(encoding="utf-8")
        was_packed = is_packed(packed)
        full_html = unpack_html(packed) if was_packed else packed
        enriched = _inject_guild_icon(full_html, data_uri)
        if enriched == full_html:
            return report_path
        _atomic_write_text(
            report_path, pack_html(enriched) if was_packed else enriched
        )
    except Exception:
        logger.warning(
            "Guild icon injection failed — "
            "report will ship without the icon",
            exc_info=True,
        )
    return report_path


def summarize_tiddlers(tiddler_json: Path) -> dict:
    try:
        tiddlers = json.loads(tiddler_json.read_text(encoding="utf-8"))
    except Exception:
        return {"fight_count": 0, "span": ""}

    if not isinstance(tiddlers, list):
        return {"fight_count": 0, "span": ""}

    fight_nums = set()
    datetimes = []
    dt_re = re.compile(r"\d{4}-\d{2}-\d{2}-\d{2}:\d{2}:\d{2}")

    for t in tiddlers:
        if not isinstance(t, dict):
            continue
        title = t.get("title", "")
        # Real combiner titles are date-prefixed
        # ("2026-07-08-21:00:06_Fight_01_Damage_Output_Review"), so the
        # fight number must be found mid-title, not anchored at the start.
        m = re.search(r"(?:^|_)Fight_(\d+)", title)
        if m:
            fight_nums.add(int(m.group(1)))

        for field in ("title", "tags"):
            val = t.get(field)
            candidates = []
            if isinstance(val, str):
                candidates = [val]
            elif isinstance(val, list):
                candidates = [str(v) for v in val]
            for c in candidates:
                found = dt_re.findall(c)
                if found:
                    datetimes.extend(found)

    span = ""
    if datetimes:
        times = []
        for dt in datetimes:
            try:
                times.append(dt.split("-")[-1])  # "HH:MM:SS" part
            except Exception:
                pass
        if times:
            times.sort()
            span = f"{times[0][:5]}\u2013{times[-1][:5]}"

    return {"fight_count": len(fight_nums), "span": span}
