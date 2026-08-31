"""Create a deterministic, privacy-safe SparkyBot release archive.

This script packages the PyInstaller one-directory output.  It deliberately
does not ship downloaded GW2EI files or any runtime/user state that may have
landed beside the executable during smoke testing.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from collections.abc import Iterable
from typing import Any
import zipfile


_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_EXCLUDED_DIRS = {
    "__pycache__",
    "gw2ei",
    "logs",
    "player-data",
    "player_data",
}
_EXCLUDED_NAMES = {
    ".env",
    "config.properties",
    "config.properties.bak",
}
_EXCLUDED_SUFFIXES = {
    ".evtc",
    ".zevtc",
    ".log",
    ".pyc",
}


@dataclass(frozen=True)
class ReleaseArtifacts:
    archive_path: Path
    manifest_path: Path
    checksums_path: Path
    archive_sha256: str
    manifest: dict[str, Any]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksums(paths: Iterable[Path], checksums_path: Path) -> dict[str, str]:
    """Write deterministic GNU-style SHA-256 lines for regular release files."""
    candidates = [Path(path) for path in paths]
    if not candidates:
        raise ValueError("at least one release artifact is required")

    names = [path.name for path in candidates]
    if len(names) != len({name.casefold() for name in names}):
        raise ValueError("release artifact filenames must be unique")

    for path in candidates:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"release artifact is not a regular file: {path}")

    digests = {
        path.name: _sha256_file(path)
        for path in sorted(candidates, key=lambda item: item.name.casefold())
    }
    checksums_path = Path(checksums_path)
    checksums_path.write_text(
        "".join(f"{digest}  {name}\n" for name, digest in digests.items()),
        encoding="ascii",
        newline="\n",
    )
    return digests


def _is_runtime_state(relative: Path) -> bool:
    lowered_parts = tuple(part.casefold() for part in relative.parts)
    if any(part in _EXCLUDED_DIRS for part in lowered_parts[:-1]):
        return True
    name = lowered_parts[-1]
    if name in _EXCLUDED_NAMES:
        return True
    return any(name.endswith(suffix) for suffix in _EXCLUDED_SUFFIXES)


def _source_files(dist_dir: Path) -> list[tuple[Path, PurePosixPath]]:
    files: list[tuple[Path, PurePosixPath]] = []
    for source in dist_dir.rglob("*"):
        relative = source.relative_to(dist_dir)
        if source.is_symlink():
            raise ValueError(f"release input contains a symbolic link: {relative}")
        if not source.is_file() or _is_runtime_state(relative):
            continue
        archive_name = PurePosixPath("SparkyBot", *relative.parts)
        files.append((source, archive_name))
    return sorted(files, key=lambda item: item[1].as_posix())


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.flag_bits |= 0x800
    return info


def create_release(
    *,
    dist_dir: Path,
    output_dir: Path,
    version: str,
) -> ReleaseArtifacts:
    """Package *dist_dir* and return paths plus a content manifest."""
    dist_dir = Path(dist_dir).resolve()
    output_dir = Path(output_dir).resolve()
    if not _VERSION_RE.fullmatch(version):
        raise ValueError(f"invalid release version: {version!r}")
    if not dist_dir.is_dir():
        raise FileNotFoundError(f"PyInstaller output is missing: {dist_dir}")

    sources = _source_files(dist_dir)
    if not sources:
        raise ValueError(f"PyInstaller output contains no packageable files: {dist_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"SparkyBot-v{version}.zip"
    manifest_path = output_dir / f"SparkyBot-v{version}.manifest.json"
    checksums_path = output_dir / "SHA256SUMS"

    manifest_files: list[dict[str, Any]] = []
    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for source, archive_name in sources:
            payload = source.read_bytes()
            name = archive_name.as_posix()
            archive.writestr(_zip_info(name), payload, compresslevel=9)
            manifest_files.append(
                {
                    "path": name,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                }
            )

    archive_sha256 = _sha256_file(archive_path)
    manifest: dict[str, Any] = {
        "schema": 1,
        "product": "SparkyBot",
        "version": version,
        "root": "SparkyBot/",
        "files": manifest_files,
        "archive": {
            "filename": archive_path.name,
            "sha256": archive_sha256,
            "size": archive_path.stat().st_size,
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    write_checksums((archive_path, manifest_path), checksums_path)
    return ReleaseArtifacts(
        archive_path=archive_path,
        manifest_path=manifest_path,
        checksums_path=checksums_path,
        archive_sha256=archive_sha256,
        manifest=manifest,
    )


def _read_source_version(repo_root: Path) -> str:
    path = repo_root / "core/version.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "VERSION" for t in node.targets):
            continue
        version = ast.literal_eval(node.value)
        if isinstance(version, str):
            return version
    raise ValueError("core/version.py does not define a string VERSION")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=Path("dist/SparkyBot"))
    parser.add_argument("--output-dir", type=Path, default=Path("dist/release"))
    parser.add_argument("--version")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    version = args.version or _read_source_version(args.repo_root)
    result = create_release(
        dist_dir=args.dist_dir,
        output_dir=args.output_dir,
        version=version,
    )
    print(f"archive={result.archive_path}")
    print(f"manifest={result.manifest_path}")
    print(f"sha256={result.archive_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
