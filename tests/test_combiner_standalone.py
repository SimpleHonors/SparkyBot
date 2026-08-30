import json
from pathlib import Path
from types import SimpleNamespace

from core.combiner_manager import CombinerManager
from core.report_pack import pack_html, unpack_html


def test_combiner_run_requests_compressed_standalone_report(
    tmp_path, monkeypatch
):
    manager = CombinerManager(tmp_path / "data")
    install = tmp_path / "data" / "combiner" / "1.8.9"
    install.mkdir(parents=True)
    entry = install / "tw5_top_stats.py"
    entry.write_text("# test entry\n", encoding="utf-8")
    (tmp_path / "data" / "combiner" / "meta.json").write_text(
        json.dumps({"version": "1.8.9", "entry": str(entry)}),
        encoding="utf-8",
    )

    input_dir = tmp_path / "parsed"
    input_dir.mkdir()
    run_dir = tmp_path / "run"
    viewer = tmp_path / "Top_Stats_Index.html"
    viewer.write_text("<html></html>", encoding="utf-8")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        summary = input_dir / "Drag_and_Drop_Log_Summary_20260829.json"
        summary.write_text("[]", encoding="utf-8")
        summary.with_suffix(".html").write_text(
            "<html>compressed report</html>", encoding="utf-8"
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("core.combiner_manager.is_frozen", lambda: False)
    monkeypatch.setattr("core.combiner_manager.subprocess.run", fake_run)

    manager.run(input_dir, run_dir, standalone_html_template=viewer)

    assert captured["command"][-2:] == ["-s", str(viewer.resolve())]
    config_text = (run_dir / "top_stats_config.ini").read_text(encoding="utf-8")
    assert "compress_standalone_html = true" in config_text


def test_sparkybot_report_repack_round_trips_and_shrinks_repetitive_html():
    original = "<html><title>Night Report</title><body>" + ("fight data " * 20_000) + "</body></html>"

    packed = pack_html(original)

    assert unpack_html(packed) == original
    assert len(packed.encode("utf-8")) < len(original.encode("utf-8"))
