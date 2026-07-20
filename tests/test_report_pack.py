"""Tests for core/report_pack.py — self-extracting report packing."""

import json
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core.report_bake import bake_report
from core.report_pack import is_packed, pack_html, unpack_html


def _report_html(title="Raid Report 2026-07-18 (34 fights)", body="x" * 50_000):
    return (
        f"<html><head><title>{title}</title></head><body>\n"
        '<script class="tiddlywiki-tiddler-store" type="application/json">'
        f'[{{"title":"big","text":"{body}"}}]'
        "</script>\n</body></html>"
    )


class TestPackHtml:
    def test_round_trips_byte_identical(self):
        original = _report_html()
        assert unpack_html(pack_html(original)) == original

    def test_output_is_smaller_for_report_sized_input(self):
        original = _report_html()
        assert len(pack_html(original)) < len(original)

    def test_preserves_title(self):
        packed = pack_html(_report_html(title="Friday Fights (5 fights)"))
        m = re.search(r"<title>(.*?)</title>", packed)
        assert m and m.group(1) == "Friday Fights (5 fights)"

    def test_escapes_title_markup(self):
        packed = pack_html(_report_html(title="A <b>& B"))
        m = re.search(r"<title>(.*?)</title>", packed)
        assert m and m.group(1) == "A &lt;b&gt;&amp; B"

    def test_entity_encoded_title_is_not_double_escaped(self):
        # A real viewer <title> arrives entity-encoded; the loader page must
        # show "Tom & Jerry", not "Tom &amp; Jerry".
        packed = pack_html(_report_html(title="Tom &amp; Jerry"))
        m = re.search(r"<title>(.*?)</title>", packed)
        assert m and m.group(1) == "Tom &amp; Jerry"
        assert "&amp;amp;" not in packed.split('<script id="z"')[0]

    def test_no_external_references(self):
        packed = pack_html(_report_html())
        head = packed.split('<script id="z"')[0]
        tail = packed.split("</script>", 1)[1] if "</script>" in packed else ""
        for chunk in (head, tail):
            assert "http://" not in chunk
            assert "https://" not in chunk
            assert "src=" not in chunk

    def test_uses_native_decompression_no_libraries(self):
        packed = pack_html(_report_html())
        assert "DecompressionStream" in packed

    def test_loader_scopes_decode_buffers_in_helper(self):
        # Regression canary for loader memory retention: the decode
        # intermediates must live inside a function scope, not as top-level
        # vars that survive until the page is rewritten.
        packed = pack_html(_report_html())
        assert "function payloadBlob" in packed
        assert unpack_html(packed) == _report_html()

    def test_is_packed_detects_both_forms(self):
        original = _report_html()
        assert is_packed(pack_html(original))
        assert not is_packed(original)

    def test_unpack_rejects_plain_html(self):
        with pytest.raises(ValueError):
            unpack_html(_report_html())

    def test_unpack_rejects_non_gzip_payload(self):
        shell = pack_html(_report_html())
        broken = re.sub(
            r'(<script id="z" type="text/plain">)[A-Za-z0-9+/=]+',
            r"\g<1>QUJDRA==",  # valid base64 ("ABCD"), not a gzip stream
            shell,
        )
        with pytest.raises(ValueError, match="corrupt"):
            unpack_html(broken)

    def test_unpack_rejects_truncated_payload(self):
        packed = pack_html(_report_html())
        m = re.search(
            r'<script id="z" type="text/plain">([A-Za-z0-9+/=]+)</script>',
            packed,
        )
        payload = m.group(1)
        # Cut to half, kept a multiple of 4 so the base64 itself stays valid.
        cut = payload[: len(payload) // 8 * 4]
        with pytest.raises(ValueError, match="corrupt"):
            unpack_html(packed.replace(payload, cut))


class TestBakeReportCompression:
    def _bake(self, tmp_path, **kwargs):
        viewer = tmp_path / "viewer.html"
        viewer.write_text(
            "<html><head><title>Viewer</title></head><body>\n"
            '<script class="tiddlywiki-tiddler-store" type="application/json">'
            '[{"title":"$:/boot","text":"boot"}]'
            "</script>\n</body></html>",
            encoding="utf-8",
        )
        tiddlers = tmp_path / "tiddlers.json"
        tiddlers.write_text(
            json.dumps([{"title": "Fight_1", "text": "y" * 20_000}]),
            encoding="utf-8",
        )
        out = tmp_path / "out.html"
        return bake_report(viewer, tiddlers, out,
                           report_title="My Report", **kwargs)

    def test_bake_compresses_by_default(self, tmp_path):
        out = self._bake(tmp_path)
        packed = out.read_text(encoding="utf-8")
        assert is_packed(packed)
        inner = unpack_html(packed)
        assert "Fight_1" in inner
        assert "<title>My Report</title>" in inner

    def test_packed_output_keeps_title_on_loader_page(self, tmp_path):
        out = self._bake(tmp_path)
        m = re.search(r"<title>(.*?)</title>",
                      out.read_text(encoding="utf-8"))
        assert m and m.group(1) == "My Report"

    def test_bake_compress_false_writes_plain_html(self, tmp_path):
        out = self._bake(tmp_path, compress=False)
        content = out.read_text(encoding="utf-8")
        assert not is_packed(content)
        assert "tiddlywiki-tiddler-store" in content
