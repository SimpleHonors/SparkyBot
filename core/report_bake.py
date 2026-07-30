"""Summarize and enrich standalone reports created by the upstream combiner."""

import json
import logging
import os
import re
import tempfile
from pathlib import Path

from core.report_pack import pack_html, unpack_html

logger = logging.getLogger(__name__)

_STORE_OPENER = (
    '<script class="tiddlywiki-tiddler-store" type="application/json">'
)


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
