import json
import re
from datetime import datetime
from pathlib import Path

from core.raid_report import RaidReportRunner
from core.raid_session import LogInfo
from core.report_bake import DEFAULT_REPORT_TITLE, apply_report_title
from core.report_pack import is_packed, pack_html, unpack_html

TITLE = DEFAULT_REPORT_TITLE
UPSTREAM = "Top Stats - Elite Insight Log Summary"
HEADER_TIDDLER = "Header Image"
SITE_TIDDLER = "$:/SiteTitle"
OVERRIDE_FIELD = "sparkybot-report-title"
OVERRIDE_MARKER = f'"{OVERRIDE_FIELD}":"override"'

# The icon cell beside the title cell, exactly as the upstream bake writes
# it — the rename may change the text cell but never this (and never the
# index.png image tiddler the guild-icon feature overrides).
ICON_CELL = "|[img height=86 [index.png]]|"
STOCK_LOGO_B64 = "c3RvY2stY29tbWFuZGVyLXRhZw=="

# Mirrors the verified live bake: the visible header is a one-row table in
# the "Header Image" tiddler (tagged $:/tags/AboveStory) carrying the stock
# upstream title beside the logo slot, $:/SiteTitle carries the same string,
# and so does the document's <title> tag.
REPORT_HTML = (
    "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
    "<title>" + UPSTREAM + "</title>\n<style>body { background: #323232; }</style>\n"
    "</head>\n<body>\n<div class=\"tc-story-river\"></div>\n"
    '<script class="tiddlywiki-tiddler-store" type="application/json">'
    '[{"title":"Fight","text":"|thead-dark sortable|k\\n'
    '|!Name | !DPS|h\\n|A | 123|"},'
    '{"title":"index.png","type":"image/png","text":"' + STOCK_LOGO_B64 + '"},'
    '{"title":"Header Image","tags":"$:/tags/AboveStory",'
    '"text":"|[img height=86 [index.png]]| '
    '\\u003Cfont size=\\"35\\">Top Stats - Elite Insight Log Summary'
    '\\u003C/font>|"},'
    '{"title":"$:/SiteTitle","text":"Top Stats - Elite Insight Log Summary"}]'
    "</script>\n</body>\n</html>"
)


def _write_packed_report(tmp_path, name="report.html", html=REPORT_HTML):
    report = tmp_path / name
    report.write_text(pack_html(html), encoding="utf-8")
    return report


def _store_tiddlers(full_html):
    tiddlers = []
    for block in re.findall(
        r'<script class="tiddlywiki-tiddler-store" type="application/json">'
        r'(.*?)</script>',
        full_html,
        re.DOTALL,
    ):
        tiddlers.extend(json.loads(block))
    return tiddlers


def _winning_tiddler(full_html, title):
    """The tiddler TiddlyWiki would render: last store entry with the title."""
    winner = None
    for t in _store_tiddlers(full_html):
        if t.get("title") == title:
            winner = t
    return winner


def _head_title(full_html):
    match = re.search(r"<title\b[^>]*>(.*?)</title>", full_html, re.DOTALL)
    return match.group(1) if match else None


def test_packed_report_gets_the_title_overrides(tmp_path):
    report = _write_packed_report(tmp_path)

    returned = apply_report_title(report)

    assert returned == report
    packed = report.read_text(encoding="utf-8")
    assert is_packed(packed)
    full = unpack_html(packed)
    # Header Image and $:/SiteTitle each get an override tiddler in an
    # appended store block, and the header's text cell now reads ours.
    assert full.count(OVERRIDE_MARKER) == 2
    header = _winning_tiddler(full, HEADER_TIDDLER)
    assert header[OVERRIDE_FIELD] == "override"
    assert header["text"] == (
        ICON_CELL + f' <font size="35">{TITLE}</font>|'
    )
    assert _winning_tiddler(full, SITE_TIDDLER)["text"] == TITLE
    # The browser-tab title rides along.
    assert _head_title(full) == TITLE
    # The baked report is untouched around the injections: the original
    # tiddlers stay in place, merely out-voted, and the stock string keeps
    # rendering for nothing but stays in the file as baked.
    assert full.count(f'"title":"{HEADER_TIDDLER}"') == 2
    assert STOCK_LOGO_B64 in full
    assert UPSTREAM in full
    assert any(t.get("title") == "Fight" for t in _store_tiddlers(full))


def test_icon_cell_and_slot_survive_byte_exact(tmp_path):
    report = _write_packed_report(tmp_path)

    apply_report_title(report)

    full = unpack_html(report.read_text(encoding="utf-8"))
    header = _winning_tiddler(full, HEADER_TIDDLER)
    # Only the text cell changed: the icon cell is verbatim upstream and
    # the tag that puts the tiddler above the story river is kept, or the
    # skin would stop rendering the header at all.
    assert header["text"].startswith(ICON_CELL)
    assert header["tags"] == "$:/tags/AboveStory"
    # The title feature never touches the image tiddler — with no guild
    # icon configured it still wins the slot with the stock logo.
    assert _winning_tiddler(full, "index.png")["text"] == STOCK_LOGO_B64


def test_overrides_are_appended_after_the_stock_tiddlers(tmp_path):
    # TiddlyWiki loads store blocks in order and the LAST tiddler with a
    # title wins, so the overrides only take effect if they come later.
    report = _write_packed_report(tmp_path)

    apply_report_title(report)

    full = unpack_html(report.read_text(encoding="utf-8"))
    assert full.find(f'"title":"{SITE_TIDDLER}"') < full.find(OVERRIDE_MARKER)
    assert _winning_tiddler(full, SITE_TIDDLER)["text"] == TITLE


def test_head_title_can_carry_the_session_date(tmp_path):
    report = _write_packed_report(tmp_path)

    apply_report_title(report, session_date="2026-08-10")

    full = unpack_html(report.read_text(encoding="utf-8"))
    assert _head_title(full) == f"{TITLE} \u2014 2026-08-10"


def test_custom_title_replaces_everywhere(tmp_path):
    report = _write_packed_report(tmp_path)

    apply_report_title(report, title="Our Guild Fight Log")

    full = unpack_html(report.read_text(encoding="utf-8"))
    assert _winning_tiddler(full, HEADER_TIDDLER)["text"] == (
        ICON_CELL + ' <font size="35">Our Guild Fight Log</font>|'
    )
    assert _winning_tiddler(full, SITE_TIDDLER)["text"] == "Our Guild Fight Log"
    assert _head_title(full) == "Our Guild Fight Log"


def test_apply_is_idempotent(tmp_path):
    report = _write_packed_report(tmp_path)

    apply_report_title(report)
    once = report.read_text(encoding="utf-8")
    apply_report_title(report)
    twice = report.read_text(encoding="utf-8")

    assert twice == once
    full = unpack_html(twice)
    assert full.count(OVERRIDE_MARKER) == 2
    assert full.count(f'"title":"{HEADER_TIDDLER}"') == 2
    assert full.count("<title>") == 1


def test_uncompressed_report_gets_the_title_too(tmp_path):
    report = tmp_path / "plain.html"
    report.write_text(REPORT_HTML, encoding="utf-8")

    apply_report_title(report)

    content = report.read_text(encoding="utf-8")
    assert not is_packed(content)
    assert OVERRIDE_MARKER in content
    assert f"<title>{TITLE}</title>" in content


def test_drifted_tiddlers_are_left_alone(tmp_path, caplog):
    # Upstream drift: the header and site tiddlers no longer carry the
    # known string. They are not clobbered; the <title> rename (which the
    # ticket owns outright) still lands.
    drifted = (
        REPORT_HTML.replace(
            "Top Stats - Elite Insight Log Summary\\u003C/font>",
            "Whatever Combiner Six Says\\u003C/font>",
        )
        .replace(
            '{"title":"$:/SiteTitle","text":"Top Stats - Elite Insight '
            'Log Summary"}',
            '{"title":"$:/SiteTitle","text":"Combiner Six"}',
        )
    )
    report = _write_packed_report(tmp_path, name="drifted.html", html=drifted)

    apply_report_title(report)

    full = unpack_html(report.read_text(encoding="utf-8"))
    assert OVERRIDE_MARKER not in full
    assert "Whatever Combiner Six Says" in _winning_tiddler(
        full, HEADER_TIDDLER
    )["text"]
    assert _winning_tiddler(full, HEADER_TIDDLER)["text"].startswith(ICON_CELL)
    assert _winning_tiddler(full, SITE_TIDDLER)["text"] == "Combiner Six"
    assert _head_title(full) == TITLE
    warnings = caplog.get_records("call")
    assert any("no longer carries" in r.getMessage() for r in warnings)


def test_report_without_the_tiddlers_still_renames_head_title(tmp_path, caplog):
    drifted = REPORT_HTML.replace(
        f'"title":"{HEADER_TIDDLER}"', '"title":"Unrelated Header"'
    ).replace(f'"title":"{SITE_TIDDLER}"', '"title":"$:/SiteOther"')
    report = _write_packed_report(tmp_path, name="noheader.html", html=drifted)

    apply_report_title(report)

    full = unpack_html(report.read_text(encoding="utf-8"))
    assert OVERRIDE_MARKER not in full
    assert _head_title(full) == TITLE
    warnings = caplog.get_records("call")
    assert any(f"no {HEADER_TIDDLER}" in r.getMessage() for r in warnings)


def test_report_without_a_store_block_ships_unchanged(tmp_path):
    headless = "<html><body><p>fight data Header Image $:/SiteTitle</p></body></html>"
    report = _write_packed_report(tmp_path, name="nostore.html", html=headless)
    before = report.read_text(encoding="utf-8")

    returned = apply_report_title(report)

    assert returned == report
    assert report.read_text(encoding="utf-8") == before


class _HitCache:
    def __init__(self, stored_json):
        self._stored = Path(stored_json)

    def lookup(self, log_path, ei_version, fingerprint):
        return self._stored if self._stored.exists() else None


class _FakeCombiner:
    def __init__(self, html):
        self._html = html

    def ensure_installed(self):
        pass

    def write_run_config(self, run_dir, input_dir, guild_name, guild_id, api_key):
        run_dir.mkdir(parents=True, exist_ok=True)

    def run(self, input_dir, run_dir, standalone_html_template=None):
        summary = input_dir / "Drag_and_Drop_Log_Summary_20260829.json"
        summary.write_text("[]", encoding="utf-8")
        summary.with_suffix(".html").write_text(self._html, encoding="utf-8")
        return summary


def _make_runner(tmp_path, stored_json, combiner_html, guild_icon=""):
    return RaidReportRunner(
        log_folder=tmp_path / "logs",
        cache=_HitCache(stored_json),
        parse_log=lambda path: None,
        ei_version="3.26.0",
        settings_fingerprint="fp",
        combiner=_FakeCombiner(combiner_html),
        viewer_html=Path("."),
        output_dir=tmp_path / "out",
        guild_icon=guild_icon,
    )


def _make_log(tmp_path):
    return LogInfo(
        path=tmp_path / "logs" / "20260829-213000-abc.log",
        timestamp=datetime.fromtimestamp(1756500000),
        source="filename",
    )


def test_runner_sticky_icon_and_title_coexist(tmp_path):
    import base64
    import binascii
    import struct
    import zlib

    def chunk(tag, payload):
        body = tag + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 6, 6, 8, 2, 0, 0, 0)
    row = b"\x00" + b"\x2F\x6B\xC1" * 6
    png_bytes = (
        b"\x89PNG\r\n\x1A\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(row * 6, 9))
        + chunk(b"IEND", b"")
    )
    png_b64 = base64.b64encode(png_bytes).decode("ascii")
    icon = tmp_path / "guild-icon.png"
    icon.write_bytes(png_bytes)

    stored = tmp_path / "cached.json"
    stored.write_text("[]", encoding="utf-8")
    runner = _make_runner(
        tmp_path, stored, pack_html(REPORT_HTML), guild_icon=str(icon)
    )

    result = runner.generate(
        [_make_log(tmp_path)], report_name="Night", work_dir=tmp_path / "work"
    )

    full = unpack_html(result.html_path.read_text(encoding="utf-8"))
    # Sticky headers still ride along...
    assert "position: sticky;" in full
    # ...the guild icon still takes the logo slot...
    assert '"sparkybot-guild-icon":"override"' in full
    assert _winning_tiddler(full, "index.png")["text"] == png_b64
    # ...and the title rename wins the header text cell beside it, with
    # the icon cell in the wikitext still pointing at the (now overridden)
    # index.png slot.
    header = _winning_tiddler(full, HEADER_TIDDLER)
    assert header[OVERRIDE_FIELD] == "override"
    assert header["text"] == ICON_CELL + f' <font size="35">{TITLE}</font>|'
    assert _winning_tiddler(full, SITE_TIDDLER)["text"] == TITLE
    # The runner passes the session date, so the tab title carries it.
    head = _head_title(full)
    assert head.startswith(f"{TITLE} \u2014 ")


def test_runner_renames_the_header_without_a_configured_icon(tmp_path):
    stored = tmp_path / "cached.json"
    stored.write_text("[]", encoding="utf-8")
    runner = _make_runner(tmp_path, stored, pack_html(REPORT_HTML))

    result = runner.generate(
        [_make_log(tmp_path)], report_name="Night", work_dir=tmp_path / "work"
    )

    full = unpack_html(result.html_path.read_text(encoding="utf-8"))
    assert OVERRIDE_MARKER in full
    assert _winning_tiddler(full, HEADER_TIDDLER)["text"] == (
        ICON_CELL + f' <font size="35">{TITLE}</font>|'
    )
    # No icon configured: the stock logo keeps the slot.
    assert _winning_tiddler(full, "index.png")["text"] == STOCK_LOGO_B64
