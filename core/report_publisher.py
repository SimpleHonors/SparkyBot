"""Publish a baked Raid Report HTML to Discord via a send_file callback."""

import zipfile
from dataclasses import dataclass
from pathlib import Path

DISCORD_WEBHOOK_LIMIT = 10 * 1024 * 1024
RAW_ATTACH_THRESHOLD = 9_300_000


@dataclass
class PublishResult:
    posted_path: Path
    zipped: bool
    size_bytes: int


def publish_report(html_path: Path, send_file, caption: str = "",
                   always_zip: bool = False, embed=None,
                   extra_files=None) -> PublishResult:
    file_size = html_path.stat().st_size

    if not always_zip and file_size <= RAW_ATTACH_THRESHOLD:
        if not send_file(html_path, caption, embed=embed,
                         extra_files=extra_files):
            raise RuntimeError("Discord upload failed")
        return PublishResult(posted_path=html_path, zipped=False,
                             size_bytes=file_size)

    zip_path = html_path.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(html_path, arcname=html_path.name)

    zip_size = zip_path.stat().st_size
    if zip_size > DISCORD_WEBHOOK_LIMIT:
        raise ValueError(
            f"Compressed report ({zip_size:,} bytes) exceeds "
            f"Discord's 10 MB webhook limit"
        )

    if not send_file(zip_path, caption, embed=embed,
                     extra_files=extra_files):
        raise RuntimeError("Discord upload failed")

    return PublishResult(posted_path=zip_path, zipped=True,
                         size_bytes=zip_size)
