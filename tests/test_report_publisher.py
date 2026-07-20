"""Tests for core/report_publisher.py — Discord report publishing."""

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core.report_publisher import (
    publish_report,
    PublishResult,
    DISCORD_WEBHOOK_LIMIT,
    RAW_ATTACH_THRESHOLD,
)


def _fake_send_file(return_value=True):
    """Create a fake send_file that records calls including embed."""
    calls = []

    def fake(path: Path, caption: str, embed=None, extra_files=None) -> bool:
        calls.append((path, caption, embed, extra_files))
        return return_value

    return fake, calls


class TestPublishReport:
    def test_small_file_posted_raw(self, tmp_path):
        html = tmp_path / "report.html"
        html.write_text("<html></html>" * 10, encoding="utf-8")
        fake, calls = _fake_send_file()

        result = publish_report(html, fake, caption="Raid Report")

        assert result == PublishResult(posted_path=html, zipped=False,
                                       size_bytes=html.stat().st_size)
        assert len(calls) == 1
        assert calls[0][0] == html
        assert calls[0][1] == "Raid Report"
        assert calls[0][2] is None

    def test_large_file_posted_as_zip(self, tmp_path):
        html = tmp_path / "report.html"
        chunk = "x" * 1024 * 1024
        with open(html, "w", encoding="utf-8") as f:
            for _ in range(11):
                f.write(chunk)
        assert html.stat().st_size > RAW_ATTACH_THRESHOLD

        fake, calls = _fake_send_file()

        result = publish_report(html, fake, caption="Big Report")

        zip_path = html.with_suffix(".zip")
        assert result.posted_path == zip_path
        assert result.zipped is True
        assert result.size_bytes == zip_path.stat().st_size
        assert zip_path.exists()
        assert len(calls) == 1
        assert calls[0][0] == zip_path
        assert calls[0][1] == "Big Report"

    def test_embed_passed_through_raw(self, tmp_path):
        html = tmp_path / "report.html"
        html.write_text("<html></html>" * 10, encoding="utf-8")
        fake, calls = _fake_send_file()
        test_embed = {"title": "Test", "color": 42}

        publish_report(html, fake, embed=test_embed)

        assert calls[0][2] == test_embed

    def test_embed_passed_through_zip(self, tmp_path):
        html = tmp_path / "report.html"
        chunk = "x" * 1024 * 1024
        with open(html, "w", encoding="utf-8") as f:
            for _ in range(11):
                f.write(chunk)
        fake, calls = _fake_send_file()
        test_embed = {"title": "Big", "fields": []}

        publish_report(html, fake, embed=test_embed)

        assert calls[0][2] == test_embed

    def test_zip_member_name_is_html_filename(self, tmp_path):
        import zipfile

        html = tmp_path / "report.html"
        with open(html, "w", encoding="utf-8") as f:
            for _ in range(11):
                f.write("x" * 1024 * 1024)

        fake, _ = _fake_send_file()
        publish_report(html, fake)

        zip_path = html.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
        assert names == ["report.html"]

    def test_always_zip_zips_small_files(self, tmp_path):
        html = tmp_path / "report.html"
        html.write_text("<html></html>" * 10, encoding="utf-8")
        fake, calls = _fake_send_file()

        result = publish_report(html, fake, always_zip=True)

        assert result.zipped is True
        assert len(calls) == 1
        assert calls[0][0].suffix == ".zip"

    def test_send_file_false_raises_runtime_error(self, tmp_path):
        html = tmp_path / "report.html"
        html.write_text("<html></html>" * 10, encoding="utf-8")
        fake, _ = _fake_send_file(return_value=False)

        with pytest.raises(RuntimeError, match="Discord upload failed"):
            publish_report(html, fake)

    def test_zip_exceeding_discord_limit_raises(self, tmp_path, monkeypatch):
        html = tmp_path / "huge.html"
        with open(html, "w", encoding="utf-8") as f:
            for _ in range(11):
                f.write("x" * 1024 * 1024)

        monkeypatch.setattr(
            "core.report_publisher.DISCORD_WEBHOOK_LIMIT", 1000,
        )

        fake, _ = _fake_send_file()

        with pytest.raises(ValueError, match="exceeds Discord"):
            publish_report(html, fake)
