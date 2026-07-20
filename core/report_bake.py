"""Bake a TiddlyWiki raid report into a standalone HTML file."""

import html
import json
import logging
import os
import re
import tempfile
from pathlib import Path

from core.report_pack import pack_html

logger = logging.getLogger(__name__)


def bake_report(viewer_html: Path, tiddler_json: Path, out_html: Path,
                report_title: str | None = None,
                compress: bool = True) -> Path:
    """Embed the tiddlers from *tiddler_json* into *viewer_html* and write
    the finished report to *out_html*.

    Collision ordering: the generated store block is appended AFTER every
    store block the viewer ships with.  TiddlyWiki loads store blocks in
    document order, so when a generated tiddler shares a title with one
    built into the viewer (``$:/SiteTitle`` is the common case) the
    generated copy wins -- the same outcome as manually drag-and-dropping
    the JSON onto the viewer.  A ``$:/SiteTitle`` tiddler in the data
    therefore controls the title rendered inside the wiki, while
    *report_title* only rewrites the page shell's raw ``<title>`` element.
    """
    if out_html.exists() and viewer_html.samefile(out_html):
        # Writing the report over its own template silently destroys the
        # viewer for every later bake.  Refuse up front; samefile() also
        # catches symlink/alias spellings of the same path.
        raise ValueError("output file must not be the viewer template itself")
    content = viewer_html.read_text(encoding="utf-8")

    store_opener = '<script class="tiddlywiki-tiddler-store" type="application/json">'
    first_idx = content.find(store_opener)
    if first_idx == -1:
        raise ValueError("not a TiddlyWiki store-format HTML")

    raw_tiddlers = tiddler_json.read_text(encoding="utf-8")
    tiddlers = json.loads(raw_tiddlers)
    if not isinstance(tiddlers, list):
        raise ValueError("tiddler JSON must be a list")

    serialized = json.dumps(tiddlers, ensure_ascii=False, separators=(",", ":"))
    safe_json = serialized.replace("<", "\\u003C")
    new_block = f'{store_opener}{safe_json}</script>'

    last_store_start = content.rfind(store_opener)
    close_tag = "</script>"
    last_close = content.find(close_tag, last_store_start)
    if last_close == -1:
        # A truncated viewer used to surface as a bare "substring not
        # found" ValueError from str.index -- name the real problem.
        raise ValueError("tiddler store block in the viewer is never closed")

    insert_pos = last_close + len(close_tag)
    output = content[:insert_pos] + new_block + content[insert_pos:]

    if report_title is not None:
        # The title is the user-typed report name.  Entity-encode it so it
        # cannot smuggle markup into the page, and splice it in through a
        # callable: a plain re.sub replacement string would interpret
        # backslashes and group references ("\1", "\g<0>", "C:\Users")
        # instead of keeping them literal.
        safe_title = html.escape(report_title, quote=False)
        output = re.sub(
            r"<title>.*?</title>",
            lambda _match: f"<title>{safe_title}</title>",
            output,
            count=1,
        )

    if compress:
        output = pack_html(output)

    # Write to a sibling temp file and rename into place, so an interrupted
    # bake never leaves a half-written .html where the finished report should
    # be.  If anything fails, remove the temp file -- but never let that
    # cleanup raise over the error that actually broke the bake.
    fd, part_path = tempfile.mkstemp(
        dir=str(out_html.parent), prefix=out_html.name + ".", suffix=".part"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(output)
        os.replace(part_path, out_html)
        part_path = None
    finally:
        if part_path is not None:
            try:
                os.unlink(part_path)
            except OSError:
                logger.warning("could not remove partial report %s", part_path)
    return out_html


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
