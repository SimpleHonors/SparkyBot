"""Fail-closed verification for SparkyBot Windows release artifacts."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any
import zipfile


_ARCHIVE_RE = re.compile(r"^SparkyBot-v(?P<version>[^/]+)\.zip$")
_CHANGELOG_RE = re.compile(r"^## \[(?P<version>[^]]+)]", re.MULTILINE)
_LITERAL_ISS_VERSION_RE = re.compile(
    r'^\s*#define\s+MyAppVersion\s+"(?P<version>[^"]+)"',
    re.MULTILINE,
)
_REQUIRED_FILES = {
    "SparkyBot/SparkyBot.exe",
    "SparkyBot/SparkyBotUpdater.exe",
    "SparkyBot/_internal/assets/theme_dark.qss",
    "SparkyBot/_internal/assets/sbtray.ico",
    "SparkyBot/_internal/assets/sbtray.png",
}
_REQUIRED_GLOBS = {
    "SparkyBot/_internal/prompts/*",
}
_REQUIRED_DLL_GLOBS = {
    "avcodec-*.dll",
    "windowsmediaplugin.dll",
}
_FORBIDDEN_DLLS = {
    "opengl32sw.dll",
    "qdirect2d.dll",
    "qt6pdf.dll",
    "qt6quick.dll",
    "qt6qml.dll",
    "qt6qmlmeta.dll",
    "qt6qmlmodels.dll",
    "qt6qmlworkerscript.dll",
}
_FORBIDDEN_NAMES = {
    ".env",
    "config.properties",
}


class ReleaseVerificationError(RuntimeError):
    """Raised when a release artifact violates a shipping invariant."""


@dataclass(frozen=True)
class VerificationReport:
    archive_path: Path
    version: str
    file_count: int
    archive_sha256: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fail(problems: list[str]) -> None:
    if problems:
        rendered = "\n - ".join(problems)
        raise ReleaseVerificationError(f"release verification failed:\n - {rendered}")


def _source_version(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "VERSION" for t in node.targets):
            continue
        value = ast.literal_eval(node.value)
        if isinstance(value, str):
            return value
    raise ReleaseVerificationError(f"VERSION string not found in {path}")


def verify_repository_version(
    repo_root: Path,
    *,
    expected_version: str | None = None,
    require_tag: bool = False,
) -> str:
    repo_root = Path(repo_root).resolve()
    source_version = _source_version(repo_root / "core/version.py")
    problems: list[str] = []
    if expected_version and source_version != expected_version:
        problems.append(
            f"core/version.py is {source_version}, expected {expected_version}"
        )

    changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
    match = _CHANGELOG_RE.search(changelog)
    changelog_version = match.group("version") if match else None
    if changelog_version != source_version:
        problems.append(
            f"CHANGELOG top version is {changelog_version!r}, "
            f"core/version.py is {source_version!r}"
        )

    installer = (repo_root / "build/sparkybot.iss").read_text(encoding="utf-8")
    literal = _LITERAL_ISS_VERSION_RE.search(installer)
    if literal:
        problems.append(
            "build/sparkybot.iss hard-codes MyAppVersion "
            f"{literal.group('version')}; inject it from core/version.py"
        )
    if "#ifndef MyAppVersion" not in installer:
        problems.append(
            "build/sparkybot.iss does not require injected MyAppVersion"
        )

    if require_tag:
        completed = subprocess.run(
            ["git", "tag", "--points-at", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        tags = set(completed.stdout.split())
        expected_tag = f"v{source_version}"
        if expected_tag not in tags:
            problems.append(
                f"HEAD is not tagged {expected_tag}; tags at HEAD: {sorted(tags)}"
            )

    _fail(problems)
    return source_version


def _archive_manifest(archive: zipfile.ZipFile) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        digest = hashlib.sha256()
        with archive.open(info) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        result.append(
            {"path": info.filename, "sha256": digest.hexdigest(), "size": info.file_size}
        )
    return result


def verify_archive(
    archive_path: Path,
    expected_version: str,
    *,
    manifest_path: Path | None = None,
) -> VerificationReport:
    archive_path = Path(archive_path).resolve()
    problems: list[str] = []
    name_match = _ARCHIVE_RE.fullmatch(archive_path.name)
    archive_version = name_match.group("version") if name_match else None
    if archive_version != expected_version:
        problems.append(
            f"archive filename version is {archive_version!r}, expected {expected_version!r}"
        )
    if not archive_path.is_file():
        problems.append(f"archive is missing: {archive_path}")
        _fail(problems)

    with zipfile.ZipFile(archive_path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        names = [info.filename for info in infos]
        if names != sorted(names):
            problems.append("archive entries are not sorted deterministically")
        if len(names) != len(set(names)):
            problems.append("archive contains duplicate entries")
        for name in names:
            path = PurePosixPath(name)
            if "\\" in name:
                problems.append(f"archive entry uses a backslash: {name}")
            if path.is_absolute() or ".." in path.parts:
                problems.append(f"archive entry is unsafe: {name}")
            if not path.parts or path.parts[0] != "SparkyBot":
                problems.append(f"archive entry is outside SparkyBot/: {name}")

        name_set = set(names)
        for required in sorted(_REQUIRED_FILES):
            if required not in name_set:
                problems.append(f"required file is missing: {required}")
        for pattern in sorted(_REQUIRED_GLOBS):
            if not any(fnmatch.fnmatch(name, pattern) for name in names):
                problems.append(f"required content is missing: {pattern}")

        basenames = {PurePosixPath(name).name.casefold() for name in names}
        for pattern in sorted(_REQUIRED_DLL_GLOBS):
            if not any(fnmatch.fnmatch(name, pattern) for name in basenames):
                problems.append(f"required runtime DLL is missing: {pattern}")
        for forbidden in sorted(_FORBIDDEN_DLLS):
            if forbidden in basenames:
                offending = next(
                    name
                    for name in names
                    if PurePosixPath(name).name.casefold() == forbidden
                )
                problems.append(f"unused DLL must not ship: {offending}")

        for name in names:
            path = PurePosixPath(name)
            lower_parts = tuple(part.casefold() for part in path.parts)
            basename = lower_parts[-1]
            if "gw2ei" in lower_parts:
                problems.append(f"downloaded GW2EI content must not ship: {name}")
            if basename in _FORBIDDEN_NAMES:
                problems.append(f"runtime configuration must not ship: {name}")
            if basename.endswith((".evtc", ".zevtc")):
                problems.append(f"player log data must not ship: {name}")

        bad_crc = archive.testzip()
        if bad_crc:
            problems.append(f"archive CRC check failed: {bad_crc}")
        computed_manifest = _archive_manifest(archive)

    archive_sha256 = _sha256_file(archive_path)
    if manifest_path is not None:
        stored = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        if stored.get("version") != expected_version:
            problems.append(
                f"manifest version is {stored.get('version')!r}, "
                f"expected {expected_version!r}"
            )
        if stored.get("files") != computed_manifest:
            problems.append("manifest file list or hashes do not match the archive")
        archive_record = stored.get("archive", {})
        if archive_record.get("sha256") != archive_sha256:
            problems.append("manifest archive SHA-256 does not match the archive")
        if archive_record.get("size") != archive_path.stat().st_size:
            problems.append("manifest archive size does not match the archive")

    _fail(problems)
    return VerificationReport(
        archive_path=archive_path,
        version=expected_version,
        file_count=len(computed_manifest),
        archive_sha256=archive_sha256,
    )


def compare_manifests(previous_path: Path, current_path: Path) -> dict[str, Any]:
    previous = json.loads(Path(previous_path).read_text(encoding="utf-8"))
    current = json.loads(Path(current_path).read_text(encoding="utf-8"))
    old = {entry["path"]: entry for entry in previous.get("files", [])}
    new = {entry["path"]: entry for entry in current.get("files", [])}
    return {
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "changed": sorted(
            path for path in new.keys() & old.keys() if new[path] != old[path]
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--version")
    parser.add_argument("--require-tag", action="store_true")
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--diff-output", type=Path)
    args = parser.parse_args(argv)

    version = verify_repository_version(
        args.repo_root,
        expected_version=args.version,
        require_tag=args.require_tag,
    )
    report = verify_archive(
        args.archive,
        version,
        manifest_path=args.manifest,
    )
    print(f"PASS version={report.version}")
    print(f"PASS files={report.file_count}")
    print(f"PASS sha256={report.archive_sha256}")

    if args.previous_manifest:
        if not args.manifest:
            parser.error("--previous-manifest requires --manifest")
        diff = compare_manifests(args.previous_manifest, args.manifest)
        rendered = json.dumps(diff, indent=2, sort_keys=True) + "\n"
        if args.diff_output:
            args.diff_output.write_text(rendered, encoding="utf-8", newline="\n")
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
