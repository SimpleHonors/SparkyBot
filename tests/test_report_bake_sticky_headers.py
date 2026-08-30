from datetime import datetime
from pathlib import Path

from core.raid_report import RaidReportRunner
from core.raid_session import LogInfo
from core.report_bake import (
    _inject_style_into_head,
    _STICKY_HEADER_STYLE,
    apply_sticky_table_headers,
)
from core.report_pack import is_packed, pack_html, unpack_html

MARKER = "data-sparkybot-sticky-headers"

REPORT_HTML = (
    "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
    "<title>Night Report</title>\n<style>body { background: #323232; }</style>\n"
    "</head>\n<body>\n<div class=\"tc-story-river\"></div>\n"
    '<script class="tiddlywiki-tiddler-store" type="application/json">'
    '[{"title":"Fight","text":"|thead-dark sortable|k\\n'
    '|!Name | !DPS|h\\n|A | 123|"}]</script>\n'
    "</body>\n</html>"
)


def _write_packed_report(tmp_path, name="report.html", html=REPORT_HTML):
    report = tmp_path / name
    report.write_text(pack_html(html), encoding="utf-8")
    return report


def test_injection_places_the_sticky_style_before_head_close():
    styled = _inject_style_into_head(REPORT_HTML, _STICKY_HEADER_STYLE)

    assert MARKER in styled
    assert "position: sticky;" in styled
    assert "top: 0;" in styled
    assert "background-color: #343a40;" in styled
    assert styled.find(MARKER) < styled.find("</head>")
    assert styled.endswith(REPORT_HTML[REPORT_HTML.find("</head>"):])


def test_injection_falls_back_to_the_opening_head_tag():
    broken = REPORT_HTML.replace("</head>", "")
    styled = _inject_style_into_head(broken, _STICKY_HEADER_STYLE)

    assert MARKER in styled
    assert styled.find(MARKER) > styled.find("<head>")
    assert styled.startswith(REPORT_HTML[: REPORT_HTML.find("<head>") + 6])


def test_injection_is_a_no_op_without_any_head_anchor():
    headless = "<html><body><table><thead><th>Name</th></thead></table></body></html>"
    assert _inject_style_into_head(headless, _STICKY_HEADER_STYLE) is headless


def test_injection_is_idempotent():
    once = _inject_style_into_head(REPORT_HTML, _STICKY_HEADER_STYLE)
    twice = _inject_style_into_head(once, _STICKY_HEADER_STYLE)
    assert twice.count(MARKER) == 1


def test_styled_compressed_report_stays_readable_and_styled(tmp_path):
    report = _write_packed_report(tmp_path)

    apply_sticky_table_headers(report)

    packed = report.read_text(encoding="utf-8")
    assert is_packed(packed)
    full = unpack_html(packed)
    assert MARKER in full
    assert full.count(MARKER) == 1
    assert "position: sticky;" in full
    assert "background-color: #343a40;" in full
    assert REPORT_HTML[: REPORT_HTML.find("</head>")] in full


def test_uncompressed_report_is_also_styled(tmp_path):
    report = tmp_path / "plain.html"
    report.write_text(REPORT_HTML, encoding="utf-8")

    apply_sticky_table_headers(report)

    content = report.read_text(encoding="utf-8")
    assert not is_packed(content)
    assert MARKER in content


def test_missing_insertion_point_degrades_to_unstyled_report(tmp_path):
    headless = "<html><body><p>fight data</p></body></html>"
    report = _write_packed_report(tmp_path, name="headless.html", html=headless)

    returned = apply_sticky_table_headers(report)

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


def _make_runner(tmp_path, stored_json, combiner_html):
    return RaidReportRunner(
        log_folder=tmp_path / "logs",
        cache=_HitCache(stored_json),
        parse_log=lambda path: None,
        ei_version="3.26.0",
        settings_fingerprint="fp",
        combiner=_FakeCombiner(combiner_html),
        viewer_html=Path("."),
        output_dir=tmp_path / "out",
    )


def test_runner_applies_sticky_headers_to_the_generated_report(tmp_path):
    stored = tmp_path / "cached.json"
    stored.write_text("[]", encoding="utf-8")
    log = LogInfo(
        path=tmp_path / "logs" / "20260829-213000-abc.log",
        timestamp=datetime.fromtimestamp(1756500000),
        source="filename",
    )
    runner = _make_runner(tmp_path, stored, pack_html(REPORT_HTML))

    result = runner.generate([log], report_name="Night", work_dir=tmp_path / "work")

    html = result.html_path.read_text(encoding="utf-8")
    assert is_packed(html)
    full = unpack_html(html)
    assert MARKER in full
    assert "position: sticky;" in full


def test_runner_still_ships_a_report_the_styling_cannot_touch(tmp_path):
    stored = tmp_path / "cached.json"
    stored.write_text("[]", encoding="utf-8")
    log = LogInfo(
        path=tmp_path / "logs" / "20260829-213000-abc.log",
        timestamp=datetime.fromtimestamp(1756500000),
        source="filename",
    )
    runner = _make_runner(
        tmp_path, stored, pack_html("<html><body><p>data</p></body></html>")
    )

    result = runner.generate([log], report_name="Night", work_dir=tmp_path / "work")

    html = result.html_path.read_text(encoding="utf-8")
    assert MARKER not in html
    assert unpack_html(html) == "<html><body><p>data</p></body></html>"
