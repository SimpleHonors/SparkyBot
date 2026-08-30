import base64
import binascii
import json
import re
import struct
import zlib
from datetime import datetime
from pathlib import Path

from core.raid_report import RaidReportRunner
from core.raid_session import LogInfo
from core.report_bake import apply_guild_icon
from core.report_pack import is_packed, pack_html, unpack_html

TIDDLER_TITLE = "$:/sparkybot/guild-icon"
SLOT_TITLE = "index.png"
OVERRIDE_FIELD = "sparkybot-guild-icon"
OVERRIDE_MARKER = f'"{OVERRIDE_FIELD}":"override"'

# Mirrors the upstream combiner bake: the visible page header is a
# "Header Image" tiddler (tagged $:/tags/AboveStory) drawing the stock
# commander-tag logo — the image tiddler named "index.png" — at an 86px
# box beside the big report title. The guild icon takes over that slot by
# appending a same-titled image tiddler (last title wins in TiddlyWiki).
STOCK_LOGO_B64 = "c3RvY2stY29tbWFuZGVyLXRhZw=="
REPORT_HTML = (
    "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
    "<title>Night Report</title>\n<style>body { background: #323232; }</style>\n"
    "</head>\n<body>\n<div class=\"tc-story-river\"></div>\n"
    '<script class="tiddlywiki-tiddler-store" type="application/json">'
    '[{"title":"Fight","text":"|thead-dark sortable|k\\n'
    '|!Name | !DPS|h\\n|A | 123|"},'
    '{"title":"index.png","type":"image/png","text":"' + STOCK_LOGO_B64 + '"},'
    '{"title":"Header Image","tags":"$:/tags/AboveStory",'
    '"text":"|table-borderless|k\\n|[img height=86 [index.png]]|   '
    '\\u003Cfont size=\\"35\\">Top Stats\\u003C/font>|"}]</script>\n'
    "</body>\n</html>"
)


def _tiny_png_bytes(size=6):
    """A real PNG, hand-built: solid-colour RGB with CRC-carrying chunks."""
    def chunk(tag, payload):
        body = tag + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    row = b"\x00" + b"\x2F\x6B\xC1" * size
    return (
        b"\x89PNG\r\n\x1A\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(row * size, 9))
        + chunk(b"IEND", b"")
    )


PNG_BYTES = _tiny_png_bytes()
PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")
ICON_DATA_URI = "data:image/png;base64," + PNG_B64


def _icon_file(tmp_path, name="guild-icon.png", payload=PNG_BYTES):
    icon = tmp_path / name
    icon.write_bytes(payload)
    return icon


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


def test_packed_report_gets_the_slot_override_and_tiddler(tmp_path):
    icon = _icon_file(tmp_path)
    report = _write_packed_report(tmp_path)

    returned = apply_guild_icon(report, str(icon))

    assert returned == report
    packed = report.read_text(encoding="utf-8")
    assert is_packed(packed)
    full = unpack_html(packed)
    # The header-image slot override rides in an appended store block and
    # wins the "index.png" title, taking over the logo slot with the icon.
    assert full.count(OVERRIDE_MARKER) == 1
    winner = _winning_tiddler(full, SLOT_TITLE)
    assert winner[OVERRIDE_FIELD] == "override"
    assert winner["type"] == "image/png"
    assert winner["text"] == PNG_B64
    # ...and the readable contract tiddler rides along.
    assert full.count(f'"title":"{TIDDLER_TITLE}"') == 1
    # The baked report itself is untouched around the injections: the
    # original logo tiddler and header stay in place, merely out-voted.
    assert "<title>Night Report</title>" in full
    assert STOCK_LOGO_B64 in full
    assert any(t.get("title") == "Fight" for t in _store_tiddlers(full))
    assert any(t.get("title") == "Header Image" for t in _store_tiddlers(full))


def test_override_is_appended_after_the_stock_logo(tmp_path):
    # TiddlyWiki loads store blocks in order and the LAST tiddler with a
    # title wins, so the override only takes the slot if it comes later.
    icon = _icon_file(tmp_path)
    report = _write_packed_report(tmp_path)

    apply_guild_icon(report, str(icon))

    full = unpack_html(report.read_text(encoding="utf-8"))
    assert full.find(STOCK_LOGO_B64) < full.find(OVERRIDE_MARKER)
    assert _winning_tiddler(full, SLOT_TITLE)["text"] == PNG_B64


def test_guild_icon_tiddler_carries_the_full_data_uri(tmp_path):
    icon = _icon_file(tmp_path)
    report = _write_packed_report(tmp_path)

    apply_guild_icon(report, str(icon))

    full = unpack_html(report.read_text(encoding="utf-8"))
    stamped = [t for t in _store_tiddlers(full) if t.get("title") == TIDDLER_TITLE]
    assert len(stamped) == 1
    assert stamped[0]["text"] == ICON_DATA_URI
    assert stamped[0]["type"] == "text/plain"
    assert base64.b64decode(stamped[0]["text"].split(",", 1)[1]) == PNG_BYTES


def test_apply_is_idempotent(tmp_path):
    icon = _icon_file(tmp_path)
    report = _write_packed_report(tmp_path)

    apply_guild_icon(report, str(icon))
    once = report.read_text(encoding="utf-8")
    apply_guild_icon(report, str(icon))
    twice = report.read_text(encoding="utf-8")

    assert twice == once
    full = unpack_html(twice)
    assert full.count(OVERRIDE_MARKER) == 1
    assert full.count(f'"title":"{TIDDLER_TITLE}"') == 1


def test_uncompressed_report_gets_the_icon_too(tmp_path):
    icon = _icon_file(tmp_path)
    report = tmp_path / "plain.html"
    report.write_text(REPORT_HTML, encoding="utf-8")

    apply_guild_icon(report, str(icon))

    content = report.read_text(encoding="utf-8")
    assert not is_packed(content)
    assert OVERRIDE_MARKER in content
    assert f'"title":"{TIDDLER_TITLE}"' in content


def test_svg_icon_lands_as_decoded_svg_source(tmp_path):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect fill="#2F6BC1"/></svg>'
    icon = _icon_file(tmp_path, name="guild-icon.svg", payload=svg)
    report = _write_packed_report(tmp_path)

    apply_guild_icon(report, str(icon))

    full = unpack_html(report.read_text(encoding="utf-8"))
    winner = _winning_tiddler(full, SLOT_TITLE)
    # TiddlyWiki stores image/svg+xml tiddlers as raw SVG source, not base64.
    assert winner["type"] == "image/svg+xml"
    assert winner["text"] == svg.decode("utf-8")


def test_relative_icon_path_resolves_against_the_install_root(tmp_path):
    # The shipped default is 'assets/wvw_icon.png' relative to the install
    # root — the same resolution the Discord embed thumbnail uses.
    report = _write_packed_report(tmp_path)

    apply_guild_icon(report, "assets/wvw_icon.png")

    full = unpack_html(report.read_text(encoding="utf-8"))
    assert OVERRIDE_MARKER in full
    assert f'"title":"{TIDDLER_TITLE}"' in full


def test_report_without_the_header_slot_keeps_the_contract_tiddler(
    tmp_path, caplog
):
    # Upstream drift: no index.png slot to take over. The readable
    # $:/sparkybot/guild-icon tiddler still lands; no override is appended.
    slotless = REPORT_HTML.replace("index.png", "banner.png")
    report = _write_packed_report(tmp_path, name="slotless.html", html=slotless)
    icon = _icon_file(tmp_path)

    apply_guild_icon(report, str(icon))

    full = unpack_html(report.read_text(encoding="utf-8"))
    assert f'"title":"{TIDDLER_TITLE}"' in full
    assert OVERRIDE_MARKER not in full
    assert any(
        "header-image slot" in r.getMessage()
        for r in caplog.get_records("call")
    )


def test_missing_icon_path_degrades_to_a_plain_report(tmp_path, caplog):
    report = _write_packed_report(tmp_path)
    before = report.read_text(encoding="utf-8")

    returned = apply_guild_icon(report, str(tmp_path / "gone.png"))

    assert returned == report
    assert report.read_text(encoding="utf-8") == before
    assert any("not found" in r.getMessage() for r in caplog.get_records("call"))


def test_oversized_icon_degrades_with_a_warning(tmp_path, caplog):
    oversized = b"\x89PNG\r\n\x1A\n" + b"x" * (512 * 1024 + 1)
    icon = _icon_file(tmp_path, name="huge.png", payload=oversized)
    report = _write_packed_report(tmp_path)
    before = report.read_text(encoding="utf-8")

    apply_guild_icon(report, str(icon))

    assert report.read_text(encoding="utf-8") == before
    assert any(
        "guild icon" in r.getMessage() and "cap" in r.getMessage()
        for r in caplog.get_records("call")
    )


def test_url_icon_is_skipped_with_a_warning(tmp_path, caplog):
    report = _write_packed_report(tmp_path)
    before = report.read_text(encoding="utf-8")

    apply_guild_icon(report, "https://guilds.example.com/icon.png")

    assert report.read_text(encoding="utf-8") == before
    warnings = caplog.get_records("call")
    assert any("offline" in r.getMessage() for r in warnings)


def test_empty_icon_source_keeps_the_stock_logo(tmp_path, caplog):
    report = _write_packed_report(tmp_path)
    before = report.read_text(encoding="utf-8")

    apply_guild_icon(report, "")
    apply_guild_icon(report, None)

    assert report.read_text(encoding="utf-8") == before
    full = unpack_html(before)
    # No override appended: the original commander-tag logo still wins.
    assert _winning_tiddler(full, SLOT_TITLE)["text"] == STOCK_LOGO_B64
    assert caplog.get_records("call") == []


def test_report_without_a_store_block_ships_unchanged(tmp_path):
    headless = "<html><body><p>fight data index.png</p></body></html>"
    report = _write_packed_report(tmp_path, name="nostore.html", html=headless)
    icon = _icon_file(tmp_path)

    returned = apply_guild_icon(report, str(icon))

    assert returned == report
    assert unpack_html(report.read_text(encoding="utf-8")) == headless


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


def test_runner_stamps_the_guild_icon_on_the_generated_report(tmp_path):
    stored = tmp_path / "cached.json"
    stored.write_text("[]", encoding="utf-8")
    icon = _icon_file(tmp_path)
    runner = _make_runner(
        tmp_path, stored, pack_html(REPORT_HTML), guild_icon=str(icon)
    )

    result = runner.generate(
        [_make_log(tmp_path)], report_name="Night", work_dir=tmp_path / "work"
    )

    full = unpack_html(result.html_path.read_text(encoding="utf-8"))
    assert OVERRIDE_MARKER in full
    assert f'"title":"{TIDDLER_TITLE}"' in full
    assert _winning_tiddler(full, SLOT_TITLE)["text"] == PNG_B64
    assert "position: sticky;" in full  # sticky headers still ride along


def test_runner_without_a_configured_icon_ships_a_plain_report(tmp_path):
    stored = tmp_path / "cached.json"
    stored.write_text("[]", encoding="utf-8")
    runner = _make_runner(tmp_path, stored, pack_html(REPORT_HTML))

    result = runner.generate(
        [_make_log(tmp_path)], report_name="Night", work_dir=tmp_path / "work"
    )

    full = unpack_html(result.html_path.read_text(encoding="utf-8"))
    assert OVERRIDE_MARKER not in full
    assert f'"title":"{TIDDLER_TITLE}"' not in full
    assert _winning_tiddler(full, SLOT_TITLE)["text"] == STOCK_LOGO_B64
