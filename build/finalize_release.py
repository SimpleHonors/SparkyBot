"""Finalize checksums after every SparkyBot release artifact exists."""

from __future__ import annotations

import argparse
from pathlib import Path

from build.package_release import write_checksums


def finalize_release(output_dir: Path, version: str) -> tuple[Path, dict[str, str]]:
    output_dir = Path(output_dir).resolve()
    artifacts = (
        output_dir / f"SparkyBot-v{version}.zip",
        output_dir / f"SparkyBot-v{version}.manifest.json",
        output_dir / f"SparkyBot-v{version}-Setup.exe",
    )
    checksums_path = output_dir / "SHA256SUMS"
    digests = write_checksums(artifacts, checksums_path)
    return checksums_path, digests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("dist/release"))
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)

    checksums_path, digests = finalize_release(args.output_dir, args.version)
    print(f"checksums={checksums_path}")
    print(f"files={len(digests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
