import json

from core.night_model import build_night_model
from core.report_viewer import (
    REPORT_VIEWS,
    build_switchable_report,
    convert_report_file,
    unpack_classic_report,
)
from core.report_pack import pack_html


def _tiddlers():
    return [
        {
            "title": "2026-08-30-20:00:00-Tag_Stats",
            "caption": "Duke",
            "text": (
                "|!Name|!Fights|!DownedEnemy|!killed|!DownedAlly|!DeadAlly|!KDR|h\n"
                "|Totals|4|80|55|10|5|11.0|"
            ),
        },
        {
            "title": "2026-08-30-20:00:00-Overview",
            "text": (
                "|!#|!Time|!Duration|!Squad|!Allies|!Enemy|!DownedEnemy|!killed|h\n"
                "|1|20:00|5m 0s|40|40|45|20|12|"
            ),
        },
    ]


def test_switchable_report_carries_three_views_and_one_classic_payload():
    classic = (
        "<!doctype html><html><head><title>Night &amp; Logs</title></head>"
        "<body><h1>Classic sentinel</h1></body></html>"
    )
    model = build_night_model(_tiddlers())

    report = build_switchable_report(classic, model, default_view="sparky")

    assert REPORT_VIEWS == ("sparky", "simple", "classic")
    assert report.count('id="classic-payload"') == 1
    assert all(f'data-view="{view}"' in report for view in REPORT_VIEWS)
    assert '"defaultView":"sparky"' in report
    assert "Sparky" in report and "Simple" in report and "Classic" in report
    assert unpack_classic_report(report) == classic
    assert "https://" not in report and "http://" not in report


def test_switchable_report_embeds_skin_ready_model_without_script_breakout():
    classic = "<html><title>Report</title><body>classic</body></html>"
    model = build_night_model(_tiddlers())
    model["warnings"].append("</script><script>bad()</script> __PAYLOAD__")

    report = build_switchable_report(classic, model, default_view="simple")

    assert '"defaultView":"simple"' in report
    assert "</script><script>bad()" not in report
    assert "__PAYLOAD__" in report
    assert json.dumps(model["totals"], separators=(",", ":")) in report


def test_invalid_default_view_falls_back_to_sparky():
    report = build_switchable_report(
        "<html><title>Report</title></html>",
        build_night_model(_tiddlers()),
        default_view="definitely-not-a-view",
    )

    assert '"defaultView":"sparky"' in report


def test_convert_file_unpacks_upstream_loader_and_replaces_it_atomically(tmp_path):
    classic = "<html><head><title>Night</title></head><body>sentinel</body></html>"
    path = tmp_path / "night.html"
    path.write_text(pack_html(classic), encoding="utf-8")

    returned = convert_report_file(path, _tiddlers(), default_view="simple")
    switched = path.read_text(encoding="utf-8")

    assert returned == path
    assert 'data-sparkybot-report-viewer="1"' in switched
    assert '"defaultView":"simple"' in switched
    assert unpack_classic_report(switched) == classic
    assert not list(tmp_path.glob("*.part"))
