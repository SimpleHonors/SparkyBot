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

# The data-URI tiddler ANY skin can read for the configured guild icon.
# Deliberately a plain-text tiddler holding a full data: URI so a skin can
# drop it straight into <img src=...> without knowing about SparkyBot.
_GUILD_ICON_TIDDLER_TITLE = "$:/sparkybot/guild-icon"

# Icons above this file size are skipped with a warning rather than baked
# into the report: base64 adds ~33% on top of the bytes and a report is a
# single offline file we do not want to bloat with a giant logo.
_GUILD_ICON_MAX_BYTES = 512 * 1024

# The upstream combiner's visible page header is a "Header Image" tiddler
# tagged $:/tags/AboveStory whose wikitext renders the blue commander-tag
# logo beside the big report title:
#     |[img height=86 [index.png]]|   <font size="35">Top Stats - ...</font>|
# so the logo IS the image tiddler named "index.png", drawn at an 86px-high
# box in the upper-left. Overriding that tiddler by appending a same-titled
# image tiddler to the store (TiddlyWiki loads stores in order; the last
# tiddler with a title wins) swaps the configured guild icon straight into
# that slot at the same size — no CSS pseudo-element games, and when no
# guild icon is configured nothing is appended so the original commander
# logo keeps rendering.
_GUILD_ICON_SLOT_TITLE = "index.png"

# Extra field stamped onto the override tiddler. TiddlyWiki carries unknown
# fields around untouched, so this is both provenance ("SparkyBot put this
# here") and the idempotency marker that keeps re-runs from appending the
# override twice.
_GUILD_ICON_OVERRIDE_FIELD = "sparkybot-guild-icon"
_GUILD_ICON_OVERRIDE_MARKER = f'"{_GUILD_ICON_OVERRIDE_FIELD}":"override"'

# The upstream combiner hardcodes its own product name as the report title in
# three places: the visible "Header Image" tiddler's wikitext carries it
# beside the logo slot —
#     |[img height=86 [index.png]]| <font size="35">Top Stats - ...</font>|
# — and the same string rides in $:/SiteTitle and the document's <title>
# tag. All three are ours to rename: we append same-titled tiddlers to the
# store (last title wins) with ONLY the text cell swapped, so the icon cell —
# and the index.png tiddler the guild-icon feature owns — stay byte-for-byte
# as upstream baked them.
_UPSTREAM_REPORT_TITLE = "Top Stats - Elite Insight Log Summary"
DEFAULT_REPORT_TITLE = "SparkyBot \u2014 Combined Fight Log Summary"
_REPORT_TITLE_HEADER_TIDDLER = "Header Image"
_REPORT_TITLE_SITE_TIDDLER = "$:/SiteTitle"

# Provenance and idempotency stamp, same pattern as the guild-icon override.
_REPORT_TITLE_OVERRIDE_FIELD = "sparkybot-report-title"
_REPORT_TITLE_OVERRIDE_MARKER = f'"{_REPORT_TITLE_OVERRIDE_FIELD}":"override"'

_CLOSE_HEAD_RE = re.compile(r"</head\s*>", re.IGNORECASE)
_OPEN_HEAD_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
_HEAD_TITLE_RE = re.compile(
    r"<title\b[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL
)


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


def _guild_icon_override_tiddler(data_uri: str) -> dict:
    """Build the image tiddler that takes over the header logo slot.

    The tiddler mirrors how the upstream store carries its own images: for
    raster types the text is the bare base64 payload with the matching image
    ``type``; for SVG the text is the decoded SVG source, which is how
    TiddlyWiki stores image/svg+xml tiddlers.
    """
    header, encoded = data_uri.split(",", 1)
    mime = header[len("data:"):].split(";", 1)[0] or "image/png"
    if mime == "image/svg+xml":
        text = base64.b64decode(encoded).decode("utf-8")
    else:
        text = encoded
    return {
        "title": _GUILD_ICON_SLOT_TITLE,
        "type": mime,
        "text": text,
        _GUILD_ICON_OVERRIDE_FIELD: "override",
    }


def _inject_guild_icon(full_html: str, data_uri: str) -> str:
    """Add the guild-icon tiddlers to a report document.

    Two tiddlers land in a fresh store block, in whichever state the report
    arrives:
    1. The $:/sparkybot/guild-icon tiddler — a text tiddler whose text is
       the full data URI, so ANY skin can read it (drop it straight into an
       <img src>) without knowing SparkyBot authored it.
    2. An override of the upstream header-image tiddler ("index.png", the
       blue commander-tag logo beside the big report title). Appended last,
       it wins the title and the header renders the guild icon in the same
       86px upper-left slot the original logo occupied.
    Injection is idempotent; a report that already carries both parts is
    left as it is. A report whose header no longer references the slot
    tiddler still gets the readable $:/sparkybot/guild-icon tiddler, with a
    warning that the icon will not be visible.
    """
    has_contract = f'"title":"{_GUILD_ICON_TIDDLER_TITLE}"' in full_html
    has_override = _GUILD_ICON_OVERRIDE_MARKER in full_html
    if has_contract and has_override:
        return full_html

    new_tiddlers = []
    if not has_contract:
        new_tiddlers.append(
            {
                "title": _GUILD_ICON_TIDDLER_TITLE,
                "text": data_uri,
                "type": "text/plain",
            }
        )
    if not has_override:
        if _GUILD_ICON_SLOT_TITLE in full_html:
            new_tiddlers.append(_guild_icon_override_tiddler(data_uri))
        else:
            logger.warning(
                "report has no %s header-image slot — the guild icon "
                "tiddler is stored but will not be visible",
                _GUILD_ICON_SLOT_TITLE,
            )

    if not new_tiddlers:
        return full_html
    return _append_tiddler_block(full_html, new_tiddlers)


def apply_guild_icon(standalone_html: Path, icon_source: str | None) -> Path:
    """Stamp the configured guild icon onto an upstream standalone report.

    Same augmentation path as the sticky table headers: resolve the icon,
    unpack the compressed report, append the $:/sparkybot/guild-icon tiddler
    plus the header-image slot override, and repack atomically ("takeover":
    the guild icon replaces the stock commander-tag logo beside the report
    title at the slot's own ~86px size). Unpacking is skipped for
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


def _store_tiddlers_by_title(content: str) -> dict:
    """Map each tiddler title to the tiddler TiddlyWiki would render.

    Store blocks load in document order and the LAST tiddler with a title
    wins, so this collects them in that same order. The override builder
    starts from the tiddler the report actually renders and keeps its tags
    and fields. An unparsable block contributes nothing rather than making
    the whole rename guess at the report's structure.
    """
    winners = {}
    pos = 0
    while True:
        start = content.find(_STORE_OPENER, pos)
        if start == -1:
            return winners
        body_start = start + len(_STORE_OPENER)
        end = content.find("</script>", body_start)
        if end == -1:
            raise ValueError("tiddler store block in the viewer is never closed")
        pos = end + len("</script>")
        block = content[body_start:end].strip()
        if not block:
            continue
        try:
            batch = json.loads(block)
        except Exception:
            logger.warning("skipping an unparsable tiddler store block")
            continue
        if not isinstance(batch, list):
            continue
        for item in batch:
            if isinstance(item, dict) and item.get("title") is not None:
                winners[item["title"]] = item


def _title_override_tiddler(original: dict, text: str) -> dict:
    """Clone an existing tiddler with new text plus the override stamp.

    Every other field (tags, type, anything upstream carries) is copied
    verbatim, so the override renders exactly like the tiddler it out-votes:
    the Header Image rename keeps its $:/tags/AboveStory tag and its icon
    cell untouched.
    """
    override = dict(original)
    override["text"] = text
    override[_REPORT_TITLE_OVERRIDE_FIELD] = "override"
    return override


def _title_rename_tiddlers(full_html: str, title: str) -> list[dict]:
    """Build the Header Image / $:/SiteTitle overrides carrying our title.

    Only the known upstream title is ever replaced, and only inside the
    tiddlers' own text; a tiddler that drifted upstream away from that
    string is left alone (with a warning) rather than clobbered. A tiddler
    already renamed by a previous run is skipped, which is what keeps
    re-applying idempotent.
    """
    winners = _store_tiddlers_by_title(full_html)
    new_tiddlers = []
    for tiddler_title in (
        _REPORT_TITLE_HEADER_TIDDLER,
        _REPORT_TITLE_SITE_TIDDLER,
    ):
        original = winners.get(tiddler_title)
        text = original.get("text") if isinstance(original, dict) else None
        if not isinstance(text, str):
            logger.warning(
                "report has no %s tiddler to rename — it keeps the "
                "upstream title",
                tiddler_title,
            )
            continue
        if _UPSTREAM_REPORT_TITLE not in text:
            if title in text and (
                original.get(_REPORT_TITLE_OVERRIDE_FIELD) == "override"
            ):
                continue  # already carries our title
            logger.warning(
                "the %s tiddler no longer carries the known upstream "
                "title — left as the report baked it",
                tiddler_title,
            )
            continue
        new_tiddlers.append(
            _title_override_tiddler(
                original, text.replace(_UPSTREAM_REPORT_TITLE, title)
            )
        )
    return new_tiddlers


def _patch_head_title(
    full_html: str, title: str, session_date: str | None
) -> str:
    """Replace the document's first <title> tag text, idempotent.

    The browser tab shows this string, so it gets the same rename; with a
    session date it reads "SparkyBot — Combined Fight Log Summary —
    2026-08-10". A document without a <title> tag ships unchanged here with
    a warning — a missing head is upstream drift, not a reason to bloat a
    hand-written one into an unknown structure.
    """
    display = f"{title} \u2014 {session_date}" if session_date else title
    match = _HEAD_TITLE_RE.search(full_html)
    if match is None:
        logger.warning(
            "no <title> tag in the report head — the browser tab keeps "
            "the title the viewer template shipped"
        )
        return full_html
    if match.group(1) == display:
        return full_html
    return (
        full_html[:match.start(1)] + display + full_html[match.end(1):]
    )


def apply_report_title(
    standalone_html: Path,
    title: str = DEFAULT_REPORT_TITLE,
    session_date: str | None = None,
) -> Path:
    """Stamp SparkyBot's own report title onto an upstream standalone report.

    Same augmentation path as the guild icon: unpack the compressed report,
    append same-titled overrides for the "Header Image" and $:/SiteTitle
    tiddlers (TiddlyWiki loads stores in order, last title wins) with only
    the text cell swapped — the icon cell beside it, and the index.png
    tiddler the guild-icon feature owns, are never touched — then rename the
    document's <title> tag, optionally suffixed with the session date for
    the browser tab, and repack atomically. Unpacking is skipped for
    reports that were never compressed. Any failure — no store block,
    drifted tiddlers, corrupt payload — is logged and swallowed so the
    report still ships exactly as the combiner baked it: the title must
    never fail a report.
    """
    report_path = Path(standalone_html)
    try:
        packed = report_path.read_text(encoding="utf-8")
        was_packed = is_packed(packed)
        full_html = unpack_html(packed) if was_packed else packed
        renamed_tiddlers = _title_rename_tiddlers(full_html, title)
        enriched = full_html
        if renamed_tiddlers:
            enriched = _append_tiddler_block(enriched, renamed_tiddlers)
        enriched = _patch_head_title(enriched, title, session_date)
        if enriched == full_html:
            return report_path
        _atomic_write_text(
            report_path, pack_html(enriched) if was_packed else enriched
        )
    except Exception:
        logger.warning(
            "Report title override failed — "
            "report will ship with the combiner's own title",
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
