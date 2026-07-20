"""Raid Wrap-Up: best-of-raid rollup embed posted alongside the report file."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from core.poison_tab import _cells, _prof_from_cell, build_model

CATEGORIES = [
    ("Damage",   "\U0001F5E1\uFE0F", "-Offensive", r"\bDamage\b"),
    ("Downs",    "\U0001F480",         None,          r"\bDown"),
    ("Healing",  "\U0001F49A",         "-Support",    r"\bHeal"),
    ("Barrier",  "\U0001F6E1\uFE0F",   "-Defenses",   r"\bBarrier"),
    ("Cleanses", "\u2728",             "-Support",    r"\bCleans"),
    ("Strips",   "\U0001F528",         "-Support",    r"\bStrip"),
]


@dataclass
class CategoryLeader:
    category: str
    emoji: str
    name: str
    profession: str
    value: float
    display: str


def _format_number(n: float) -> str:
    if n >= 1_000_000:
        s = f"{n / 1_000_000:.2f}"
        s = s.rstrip("0").rstrip(".")
        return f"{s}M"
    if n >= 1_000:
        s = f"{n / 1_000:.1f}"
        s = s.rstrip("0").rstrip(".")
        return f"{s}K"
    return str(int(n))


def _parse_table_rows(text: str) -> list[dict]:
    lines = text.splitlines()
    hdr_line = None
    for line in lines:
        if "!Party" in line:
            hdr_line = line
            break
    if not hdr_line:
        return []

    hdr_cells = _cells(hdr_line)
    col_names = [re.sub(r"[!{}]", "", c).strip() for c in hdr_cells]

    rows = []
    for line in lines:
        m = re.search(r'data-tooltip="([^"]+)"', line)
        if not m:
            continue
        account = m.group(1)
        cells = _cells(line)
        if len(cells) < 3:
            continue

        name = re.sub(r"<[^>]+>", "", cells[2]).strip()
        prof = _prof_from_cell(cells[3]) if len(cells) > 3 else ""

        row = {"account": account, "name": name, "prof": prof}
        for i, c in enumerate(cells):
            if i < len(col_names):
                col = col_names[i]
                try:
                    val = float(c.replace(",", ""))
                    row[col] = val
                except (ValueError, IndexError):
                    pass
        rows.append(row)
    return rows


def _find_in_category(tiddlers, title_suffix, col_re, category, emoji):
    candidates = tiddlers
    if title_suffix:
        candidates = [t for t in tiddlers
                      if t.get("title", "").endswith(title_suffix)]

    for t in candidates:
        rows = _parse_table_rows(t.get("text", ""))
        if not rows:
            continue

        target_col = None
        for row in rows:
            for col in row:
                if col in ("account", "name", "prof"):
                    continue
                if re.search(col_re, col, re.IGNORECASE):
                    target_col = col
                    break
            if target_col:
                break
        if not target_col:
            continue

        best_row = None
        best_val = -1.0
        for row in rows:
            val = row.get(target_col, 0.0)
            if val > best_val:
                best_val = val
                best_row = row

        if best_row and best_val > 0:
            return CategoryLeader(
                category=category,
                emoji=emoji,
                name=best_row["name"],
                profession=best_row["prof"],
                value=best_val,
                display=_format_number(best_val),
            )
    return None


def _poison_leader(tiddlers):
    rows = build_model(tiddlers)
    if not rows:
        return None
    r = rows[0]
    return CategoryLeader(
        category="Poison Coverage",
        emoji="\u2620\uFE0F",
        name=r.name,
        profession=r.prof,
        value=float(r.apps),
        display=str(r.apps),
    )


def build_wrapup(tiddlers: list[dict]) -> dict:
    leaders: list[CategoryLeader] = []

    for category, emoji, suffix, col_re in CATEGORIES:
        leader = _find_in_category(tiddlers, suffix, col_re, category, emoji)
        if leader:
            leaders.append(leader)

    poison = _poison_leader(tiddlers)
    if poison:
        leaders.append(poison)

    fight_count = 0
    span = ""
    dt_re = re.compile(r"\d{4}-\d{2}-\d{2}-\d{2}:\d{2}:\d{2}")
    fight_nums = set()

    for t in tiddlers:
        if not isinstance(t, dict):
            continue
        title = t.get("title", "")
        m = re.match(r"Fight_(\d+)", title)
        if m:
            fight_nums.add(int(m.group(1)))
        dt_found = dt_re.findall(title)
        if not dt_found:
            tags = t.get("tags", "")
            if isinstance(tags, str):
                dt_found = dt_re.findall(tags)
        if dt_found:
            try:
                times = [d.split("-")[-1] for d in dt_found]
                times.sort()
                span = f"{times[0][:5]}\u2013{times[-1][:5]}"
            except Exception:
                pass

    fight_count = len(fight_nums)

    text_lines = []
    for l in leaders:
        text_lines.append(f"{l.emoji} {l.category}: **{l.name}** ({l.profession}) — {l.display}")
    text = "\n".join(text_lines) if text_lines else "No data available."

    embed = {
        "title": None,
        "fields": [
            {
                "name": f"{l.emoji} {l.category}",
                "value": f"**{l.name}** ({l.profession}) — {l.display}",
                "inline": True,
            }
            for l in leaders
        ],
        "footer": {"text": "SparkyBot Raid Report"},
        "color": 0x4CAF50,
    }

    return {
        "fight_count": fight_count,
        "span": span,
        "leaders": leaders,
        "text": text,
        "embed": embed,
    }


def build_zinger_prompt(leaders: list[CategoryLeader]) -> str:
    lines = []
    for l in leaders:
        lines.append(f"{l.category}: {l.name} ({l.profession}) {l.display}")
    return "\n".join(lines)


def apply_zingers_to_embed(embed: dict, zingers: list[str]) -> dict:
    fields = embed.get("fields", [])
    for i, field in enumerate(fields):
        if i < len(zingers):
            field["value"] = f"{field['value']}\n*{zingers[i]}*"
    return embed


def compose_recap_script(report_name: str, leaders: list[CategoryLeader],
                         zingers: list[str] | None = None) -> str:
    parts = [f"That's a wrap on {report_name}."]
    for i, l in enumerate(leaders):
        line = f"{l.category}: {l.name}"
        if zingers and i < len(zingers) and zingers[i].strip():
            line += f". {zingers[i].strip()}"
        parts.append(line)
    parts.append("SparkyBot out. See you next raid.")
    return "\n".join(parts)
