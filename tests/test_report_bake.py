"""Tests for core/report_bake.py — TiddlyWiki report baking."""

import json
import os
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core.report_bake import bake_report, summarize_tiddlers


STORE_RE = re.compile(
    r'<script class="tiddlywiki-tiddler-store" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def _fake_viewer_html() -> str:
    return (
        "<html><head><title>Viewer</title></head><body>\n"
        '<script class="tiddlywiki-tiddler-store" type="application/json">'
        '[{"title":"$:/boot/some-tiddler","text":"I boot"}]'
        "</script>\n"
        '<script class="tiddlywiki-tiddler-store" type="application/json">[]</script>\n'
        "</body></html>"
    )


def _write_tiddler_json(path: Path, data) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


class TestBakeReport:
    def test_inserts_new_block_after_last_existing(self, tmp_path):
        viewer = tmp_path / "viewer.html"
        tiddlers_json = tmp_path / "tiddlers.json"
        out = tmp_path / "out.html"

        viewer.write_text(_fake_viewer_html(), encoding="utf-8")
        data = [{"title": "Fight_01", "text": "stats"}]
        _write_tiddler_json(tiddlers_json, data)

        result = bake_report(viewer, tiddlers_json, out, compress=False)

        assert result == out
        assert out.exists()

        content = out.read_text(encoding="utf-8")
        blocks = STORE_RE.findall(content)
        assert len(blocks) == 3  # two original + one new

        parsed = [json.loads(b) for b in blocks]
        assert parsed[0] == [{"title": "$:/boot/some-tiddler", "text": "I boot"}]
        assert parsed[1] == []
        assert parsed[2] == data

    def test_original_blocks_untouched(self, tmp_path):
        viewer = tmp_path / "viewer.html"
        tiddlers_json = tmp_path / "tiddlers.json"
        out = tmp_path / "out.html"

        original_html = _fake_viewer_html()
        viewer.write_text(original_html, encoding="utf-8")
        _write_tiddler_json(tiddlers_json, [{"title": "New"}] * 5)

        bake_report(viewer, tiddlers_json, out, compress=False)

        content = out.read_text(encoding="utf-8")
        blocks = STORE_RE.findall(content)
        assert blocks[0] == '[{"title":"$:/boot/some-tiddler","text":"I boot"}]'
        assert blocks[1] == "[]"

    def test_script_tag_roundtrip(self, tmp_path):
        viewer = tmp_path / "viewer.html"
        tiddlers_json = tmp_path / "tiddlers.json"
        out = tmp_path / "out.html"

        viewer.write_text(_fake_viewer_html(), encoding="utf-8")
        data = [{"title": "Malice", "text": "before</script>after"}]
        _write_tiddler_json(tiddlers_json, data)

        bake_report(viewer, tiddlers_json, out, compress=False)

        content = out.read_text(encoding="utf-8")
        blocks = STORE_RE.findall(content)
        assert len(blocks) == 3
        parsed = json.loads(blocks[2])
        assert parsed[0]["text"] == "before</script>after"

    def test_generated_tiddlers_win_title_collisions(self, tmp_path):
        viewer = tmp_path / "viewer.html"
        tiddlers_json = tmp_path / "tiddlers.json"
        out = tmp_path / "out.html"

        viewer.write_text(
            "<html><head><title>Viewer</title></head><body>\n"
            '<script class="tiddlywiki-tiddler-store" type="application/json">'
            '[{"title":"$:/SiteTitle","text":"Stock Viewer"}]'
            "</script>\n</body></html>",
            encoding="utf-8",
        )
        data = [{"title": "$:/SiteTitle", "text": "Friday Raid"}]
        _write_tiddler_json(tiddlers_json, data)

        bake_report(viewer, tiddlers_json, out, compress=False)

        blocks = STORE_RE.findall(out.read_text(encoding="utf-8"))
        # TiddlyWiki imports store blocks in document order: last one wins.
        # The generated copy must therefore be the LAST block.
        assert json.loads(blocks[-1]) == data
        assert "Stock Viewer" in blocks[0]

    def test_refuses_to_overwrite_viewer_template(self, tmp_path):
        viewer = tmp_path / "viewer.html"
        tiddlers_json = tmp_path / "tiddlers.json"

        viewer.write_text(_fake_viewer_html(), encoding="utf-8")
        _write_tiddler_json(tiddlers_json, [{"title": "Fight_01"}])

        with pytest.raises(ValueError, match="viewer template"):
            bake_report(viewer, tiddlers_json, viewer, compress=False)

        # The template must survive untouched.
        assert viewer.read_text(encoding="utf-8") == _fake_viewer_html()

    def test_raises_on_non_tw_html(self, tmp_path):
        viewer = tmp_path / "viewer.html"
        tiddlers_json = tmp_path / "tiddlers.json"
        out = tmp_path / "out.html"

        viewer.write_text("<html><body>just a page</body></html>", encoding="utf-8")
        _write_tiddler_json(tiddlers_json, [])

        with pytest.raises(ValueError, match="not a TiddlyWiki store-format HTML"):
            bake_report(viewer, tiddlers_json, out)

    def test_raises_named_error_on_unclosed_store_block(self, tmp_path):
        viewer = tmp_path / "viewer.html"
        tiddlers_json = tmp_path / "tiddlers.json"
        out = tmp_path / "out.html"

        # Truncated viewer: store opener present, closing tag missing.
        viewer.write_text(
            "<html><body>"
            '<script class="tiddlywiki-tiddler-store" type="application/json">[]',
            encoding="utf-8",
        )
        _write_tiddler_json(tiddlers_json, [])

        with pytest.raises(ValueError, match="never closed"):
            bake_report(viewer, tiddlers_json, out, compress=False)

    def test_raises_on_non_list_json(self, tmp_path):
        viewer = tmp_path / "viewer.html"
        tiddlers_json = tmp_path / "tiddlers.json"
        out = tmp_path / "out.html"

        viewer.write_text(_fake_viewer_html(), encoding="utf-8")
        tiddlers_json.write_text('{"not": "a list"}', encoding="utf-8")

        with pytest.raises(ValueError, match="tiddler JSON must be a list"):
            bake_report(viewer, tiddlers_json, out)

    def test_title_replaced(self, tmp_path):
        viewer = tmp_path / "viewer.html"
        tiddlers_json = tmp_path / "tiddlers.json"
        out = tmp_path / "out.html"

        viewer.write_text(_fake_viewer_html(), encoding="utf-8")
        _write_tiddler_json(tiddlers_json, [])

        bake_report(viewer, tiddlers_json, out, report_title="Raid Report 42")

        content = out.read_text(encoding="utf-8")
        assert "<title>Raid Report 42</title>" in content
        assert "<title>Viewer</title>" not in content

    def test_title_with_backslashes_stays_literal(self, tmp_path):
        viewer = tmp_path / "viewer.html"
        tiddlers_json = tmp_path / "tiddlers.json"
        out = tmp_path / "out.html"

        viewer.write_text(_fake_viewer_html(), encoding="utf-8")
        _write_tiddler_json(tiddlers_json, [])

        # "\1" is a group reference and "\U" a bad escape when handed to
        # re.sub as a replacement string -- both must land literally.
        name = r"Push Night \1 C:\Users"
        bake_report(viewer, tiddlers_json, out,
                    report_title=name, compress=False)

        content = out.read_text(encoding="utf-8")
        assert f"<title>{name}</title>" in content

    def test_title_markup_is_entity_encoded(self, tmp_path):
        viewer = tmp_path / "viewer.html"
        tiddlers_json = tmp_path / "tiddlers.json"
        out = tmp_path / "out.html"

        viewer.write_text(_fake_viewer_html(), encoding="utf-8")
        _write_tiddler_json(tiddlers_json, [])

        bake_report(viewer, tiddlers_json, out,
                    report_title="</title><script>x()</script>",
                    compress=False)

        content = out.read_text(encoding="utf-8")
        assert "<script>x()</script>" not in content
        assert "&lt;/title&gt;&lt;script&gt;x()&lt;/script&gt;" in content

    def test_title_not_changed_when_none(self, tmp_path):
        viewer = tmp_path / "viewer.html"
        tiddlers_json = tmp_path / "tiddlers.json"
        out = tmp_path / "out.html"

        viewer.write_text(_fake_viewer_html(), encoding="utf-8")
        _write_tiddler_json(tiddlers_json, [])

        bake_report(viewer, tiddlers_json, out, compress=False)

        content = out.read_text(encoding="utf-8")
        assert "<title>Viewer</title>" in content


class TestAtomicWrite:
    def _setup(self, tmp_path):
        viewer = tmp_path / "viewer.html"
        tiddlers_json = tmp_path / "tiddlers.json"
        viewer.write_text(_fake_viewer_html(), encoding="utf-8")
        _write_tiddler_json(tiddlers_json, [{"title": "Fight_01"}])
        return viewer, tiddlers_json, tmp_path / "out.html"

    def test_failed_write_leaves_no_output_and_no_temp(self, tmp_path,
                                                       monkeypatch):
        viewer, tiddlers_json, out = self._setup(tmp_path)

        def boom(src, dst):
            raise RuntimeError("disk full")

        monkeypatch.setattr("core.report_bake.os.replace", boom)

        with pytest.raises(RuntimeError, match="disk full"):
            bake_report(viewer, tiddlers_json, out, compress=False)

        assert not out.exists()
        assert not list(tmp_path.glob("*.part"))

    def test_cleanup_failure_never_masks_original_error(self, tmp_path,
                                                        monkeypatch):
        viewer, tiddlers_json, out = self._setup(tmp_path)

        def boom(src, dst):
            raise RuntimeError("disk full")

        real_unlink = os.unlink

        def stubborn_unlink(path, *args, **kwargs):
            if str(path).endswith(".part"):
                raise OSError("file is locked")
            return real_unlink(path, *args, **kwargs)

        monkeypatch.setattr("core.report_bake.os.replace", boom)
        monkeypatch.setattr("core.report_bake.os.unlink", stubborn_unlink)

        # The original failure must surface, not the cleanup's OSError.
        with pytest.raises(RuntimeError, match="disk full"):
            bake_report(viewer, tiddlers_json, out, compress=False)


class TestSummarizeTiddlers:
    def test_counts_fight_tiddlers(self, tmp_path):
        data = [
            {"title": "Fight_01", "tags": "2026-07-08-21:00:06"},
            {"title": "Fight_02", "tags": "2026-07-08-21:05:30"},
            {"title": "Fight_03", "tags": "2026-07-08-21:10:15"},
            {"title": "Some other tiddler"},
        ]
        path = tmp_path / "tiddlers.json"
        _write_tiddler_json(path, data)

        result = summarize_tiddlers(path)
        assert result["fight_count"] == 3

    def test_counts_real_combiner_titles(self, tmp_path):
        # Real combiner output date-prefixes every fight tiddler; the
        # old start-anchored match counted 0 fights on every live
        # report (Discord captions read "(0 fights)" until 2026-07-19).
        data = [
            {"title": "2026-07-08-21:00:06_Fight_01_Damage_Output_Review"},
            {"title": "2026-07-08-21:00:06_Fight_02_Damage_Output_Review"},
            {"title": "2026-07-08-21:00:06_Fight_02_Some_Other_Chart"},
            {"title": "Overall_Damage_Review"},
        ]
        path = tmp_path / "tiddlers.json"
        _write_tiddler_json(path, data)

        result = summarize_tiddlers(path)
        assert result["fight_count"] == 2

    def test_deduplicates_fight_numbers(self, tmp_path):
        data = [
            {"title": "Fight_01"},
            {"title": "Fight_01-v2"},
            {"title": "Fight_01"},
        ]
        path = tmp_path / "tiddlers.json"
        _write_tiddler_json(path, data)

        result = summarize_tiddlers(path)
        assert result["fight_count"] == 1

    def test_span_from_datetime_tags(self, tmp_path):
        data = [
            {"title": "Fight_01", "tags": "2026-07-08-21:00:06"},
            {"title": "Fight_02", "tags": "2026-07-08-23:45:00"},
        ]
        path = tmp_path / "tiddlers.json"
        _write_tiddler_json(path, data)

        result = summarize_tiddlers(path)
        assert result["span"] == "21:00\u201323:45"

    def test_span_empty_when_no_datetimes(self, tmp_path):
        data = [
            {"title": "Fight_01", "tags": "some-tag"},
            {"title": "Fight_02", "tags": "other-tag"},
        ]
        path = tmp_path / "tiddlers.json"
        _write_tiddler_json(path, data)

        result = summarize_tiddlers(path)
        assert result["span"] == ""

    def test_handles_invalid_json_gracefully(self, tmp_path):
        path = tmp_path / "not-json.txt"
        path.write_text("garbage", encoding="utf-8")

        result = summarize_tiddlers(path)
        assert result == {"fight_count": 0, "span": ""}

    def test_handles_non_list_gracefully(self, tmp_path):
        path = tmp_path / "tiddlers.json"
        path.write_text('{"oops": true}', encoding="utf-8")

        result = summarize_tiddlers(path)
        assert result == {"fight_count": 0, "span": ""}

    def test_datetimes_from_title_field(self, tmp_path):
        data = [
            {"title": "2026-07-08-21:00:06-intro"},
            {"title": "2026-07-08-22:30:15-outro"},
        ]
        path = tmp_path / "tiddlers.json"
        _write_tiddler_json(path, data)

        result = summarize_tiddlers(path)
        assert result["span"] == "21:00\u201322:30"
