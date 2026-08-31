"""Regression tests for the Windows release packaging gates."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import zipfile

import pytest

from build import finalize_release, package_release, verify_release
from core.ei_updater import EIUpdater


def _write(path: Path, payload: bytes = b"fixture") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _fake_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist" / "SparkyBot"
    required = {
        "SparkyBot.exe": b"app",
        "SparkyBotUpdater.exe": b"updater",
        "_internal/assets/theme_dark.qss": b"qss",
        "_internal/assets/sbtray.ico": b"ico",
        "_internal/assets/sbtray.png": b"png",
        "_internal/prompts/sparky_system_v3.md": b"prompt",
        "_internal/PySide6/avcodec-61.dll": b"codec",
        "_internal/PySide6/plugins/multimedia/windowsmediaplugin.dll": b"media",
        "_internal/PySide6/plugins/multimedia/qtaudio_windows.dll": b"audio",
    }
    for relative, payload in required.items():
        _write(dist / relative, payload)
    return dist


def test_package_is_reproducible_and_excludes_runtime_state(tmp_path):
    dist = _fake_dist(tmp_path)
    _write(dist / "GW2EI" / "GuildWars2EliteInsights.exe")
    _write(dist / "config.properties", b"private")
    _write(dist / "sample.evtc", b"player data")
    _write(dist / "session-history" / "turns.json", b"private")
    _write(dist / "calibration.json", b"private")
    _write(dist / "state.jsonl", b"private")
    _write(dist / "vocabulary.json", b"private")
    _write(dist / "cooldown_state.json", b"private")
    _write(dist / "cache" / "response.bin", b"private")
    _write(dist / ".update_pending", b"private")

    first = package_release.create_release(
        dist_dir=dist,
        output_dir=tmp_path / "one",
        version="9.8.7",
    )
    second = package_release.create_release(
        dist_dir=dist,
        output_dir=tmp_path / "two",
        version="9.8.7",
    )

    assert first.archive_sha256 == second.archive_sha256
    assert first.manifest == second.manifest
    with zipfile.ZipFile(first.archive_path) as archive:
        names = archive.namelist()
    assert names == sorted(names)
    assert all(name.startswith("SparkyBot/") for name in names)
    assert all("\\" not in name for name in names)
    assert not any("GW2EI" in name for name in names)
    assert not any(name.endswith("config.properties") for name in names)
    assert not any(name.lower().endswith((".evtc", ".zevtc")) for name in names)
    forbidden = (
        "session-history",
        "calibration",
        ".jsonl",
        "vocabulary",
        "cooldown",
        "/cache/",
        ".update_pending",
    )
    assert not any(
        any(marker in name.casefold() for marker in forbidden) for name in names
    )


def test_filtered_staging_tree_matches_archive_inputs(tmp_path):
    dist = _fake_dist(tmp_path)
    _write(dist / "session_history" / "private.json")
    staging = tmp_path / "installer-input"
    release = package_release.create_release(
        dist_dir=dist,
        output_dir=tmp_path / "release",
        version="9.8.7",
        staging_dir=staging,
    )

    staged = sorted(
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file()
    )
    archived = sorted(
        entry["path"].removeprefix("SparkyBot/")
        for entry in release.manifest["files"]
    )
    assert staged == archived
    assert not (staging / "session_history").exists()


def test_missing_elite_insights_install_does_not_offer_update(tmp_path, monkeypatch):
    class Response:
        headers = {"Location": "/baaron4/GW2-Elite-Insights-Parser/releases/tag/v3.27.1.0"}

    monkeypatch.setattr(
        "core.ei_updater.requests.get", lambda *args, **kwargs: Response()
    )
    updater = EIUpdater(tmp_path / "GW2EI")

    assert updater.check_for_update() == (False, "3.27.1.0", "")


def test_verifier_rejects_dead_qt_dll(tmp_path):
    dist = _fake_dist(tmp_path)
    _write(dist / "_internal/PySide6/Qt6Quick.dll", b"dead weight")
    release = package_release.create_release(
        dist_dir=dist,
        output_dir=tmp_path / "release",
        version="9.8.7",
    )

    with pytest.raises(
        verify_release.ReleaseVerificationError,
        match="Qt6Quick.dll",
    ):
        verify_release.verify_archive(release.archive_path, "9.8.7")


def test_verifier_requires_runtime_files(tmp_path):
    dist = _fake_dist(tmp_path)
    (dist / "SparkyBotUpdater.exe").unlink()
    release = package_release.create_release(
        dist_dir=dist,
        output_dir=tmp_path / "release",
        version="9.8.7",
    )

    with pytest.raises(
        verify_release.ReleaseVerificationError,
        match="SparkyBotUpdater.exe",
    ):
        verify_release.verify_archive(release.archive_path, "9.8.7")


def test_repository_versions_and_installer_injection_are_in_sync():
    repo_root = Path(__file__).resolve().parents[1]
    version = verify_release.verify_repository_version(repo_root)

    assert version == "2.2.2"
    installer = (repo_root / "build/sparkybot.iss").read_text(encoding="utf-8")
    assert "#ifndef MyAppVersion" in installer
    assert '#define MyAppVersion "2.0.2"' not in installer


def test_pyinstaller_spec_filters_every_known_dead_runtime_dll():
    repo_root = Path(__file__).resolve().parents[1]
    spec = (repo_root / "build/sparkybot.spec").read_text(encoding="utf-8").casefold()
    expected = {
        "opengl32sw.dll",
        "qdirect2d.dll",
        "qt6pdf.dll",
        "qt6quick.dll",
        "qt6qml.dll",
        "qt6qmlmeta.dll",
        "qt6qmlmodels.dll",
        "qt6qmlworkerscript.dll",
    }

    missing = sorted(name for name in expected if name not in spec)
    assert not missing, f"PyInstaller dead-DLL filter is missing: {missing}"


def test_windows_release_driver_builds_both_artifacts_and_runs_verifier():
    repo_root = Path(__file__).resolve().parents[1]
    driver = (repo_root / "build/release_windows.bat").read_text(
        encoding="utf-8"
    ).casefold()
    build_driver = (repo_root / "build/build_windows.bat").read_text(
        encoding="utf-8"
    ).casefold()

    assert "package_release.py" in driver
    assert "verify_release.py" in driver
    assert "-m pytest" in driver
    assert "--require-tag" in driver
    assert "verify_authenticode.ps1" in driver
    assert "sparkybot_sign_command" in driver
    assert "--staging-dir dist\\release-staging" in driver
    assert driver.index("verify_authenticode.ps1") < driver.index("finalize_release")
    authenticode = (repo_root / "build/verify_authenticode.ps1").read_text(
        encoding="utf-8"
    ).casefold()
    assert "[switch] $allowunsigned" in authenticode
    assert "signaturestatus]::notsigned" in authenticode
    assert "mixed signed and unsigned" in authenticode
    assert "-m build.finalize_release" in driver
    assert "--checksums" in driver
    assert "iscc" in driver and "/dmyappversion=" in driver
    assert "sparkybot-v%app_version%-setup.exe" in driver
    assert "xcopy gw2ei" not in build_driver
    assert "zip -r" not in build_driver


def test_windows_dependencies_are_exactly_pinned_and_upx_is_disabled():
    repo_root = Path(__file__).resolve().parents[1]
    lock_lines = [
        line.strip()
        for line in (repo_root / "build/requirements-windows.lock")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert lock_lines
    assert all("==" in line for line in lock_lines)
    assert "pyinstaller==6.22.2" in [line.casefold() for line in lock_lines]
    assert "pyside6==6.11.2" in [line.casefold() for line in lock_lines]
    for spec_name in ("sparkybot.spec", "sparkybot_updater.spec"):
        spec = (repo_root / "build" / spec_name).read_text(encoding="utf-8")
        assert "upx=True" not in spec
        assert "upx=False" in spec


def test_written_manifest_matches_verified_archive(tmp_path):
    dist = _fake_dist(tmp_path)
    release = package_release.create_release(
        dist_dir=dist,
        output_dir=tmp_path / "release",
        version="9.8.7",
    )

    report = verify_release.verify_archive(release.archive_path, "9.8.7")
    stored = json.loads(release.manifest_path.read_text(encoding="utf-8"))

    assert stored == release.manifest
    assert report.file_count == len(stored["files"])
    assert stored["archive"]["sha256"] == release.archive_sha256


def test_final_checksums_cover_installer_and_detect_tampering(tmp_path):
    dist = _fake_dist(tmp_path)
    output_dir = tmp_path / "release"
    release = package_release.create_release(
        dist_dir=dist,
        output_dir=output_dir,
        version="9.8.7",
    )
    installer = output_dir / "SparkyBot-v9.8.7-Setup.exe"
    _write(installer, b"installer")

    checksums_path, digests = finalize_release.finalize_release(
        output_dir, "9.8.7"
    )
    artifacts = [release.archive_path, release.manifest_path, installer]
    assert set(digests) == {path.name for path in artifacts}
    verify_release.verify_checksums(checksums_path, artifacts)

    installer.write_bytes(b"tampered")
    with pytest.raises(
        verify_release.ReleaseVerificationError,
        match="digest mismatch",
    ):
        verify_release.verify_checksums(checksums_path, artifacts)


def test_exact_tag_gate_rejects_a_dirty_checkout(tmp_path):
    git = verify_release.find_git_executable()
    repo = tmp_path / "repo"
    _write(repo / "core/version.py", b'VERSION = "9.8.7"\n')
    _write(repo / "CHANGELOG.md", b"## [9.8.7] - test\n")
    _write(
        repo / "build/sparkybot.iss",
        b"#ifndef MyAppVersion\n#error inject version\n#endif\n",
    )
    subprocess.run([git, "init", "-q", repo], check=True)
    subprocess.run([git, "-C", repo, "add", "."], check=True)
    subprocess.run(
        [
            git, "-C", repo,
            "-c", "user.name=Release Test",
            "-c", "user.email=release@example.invalid",
            "commit", "-qm", "fixture",
        ],
        check=True,
    )
    subprocess.run([git, "-C", repo, "tag", "v9.8.7"], check=True)

    assert verify_release.verify_repository_version(repo, require_tag=True) == "9.8.7"
    _write(repo / "untracked.txt", b"dirty")
    with pytest.raises(verify_release.ReleaseVerificationError, match="source changes"):
        verify_release.verify_repository_version(repo, require_tag=True)
    assert verify_release.verify_repository_version(
        repo,
        require_tag=True,
        allow_untracked=True,
    ) == "9.8.7"

    (repo / "CHANGELOG.md").write_text("## [9.8.7] - changed\n", encoding="utf-8")
    with pytest.raises(verify_release.ReleaseVerificationError, match="source changes"):
        verify_release.verify_repository_version(
            repo,
            require_tag=True,
            allow_untracked=True,
        )


def test_exact_tag_gate_accepts_only_explicit_versioned_candidate_tag(tmp_path):
    git = verify_release.find_git_executable()
    repo = tmp_path / "repo"
    _write(repo / "core/version.py", b'VERSION = "9.8.7"\n')
    _write(repo / "CHANGELOG.md", b"## [9.8.7] - test\n")
    _write(
        repo / "build/sparkybot.iss",
        b"#ifndef MyAppVersion\n#error inject version\n#endif\n",
    )
    subprocess.run([git, "init", "-q", repo], check=True)
    subprocess.run([git, "-C", repo, "add", "."], check=True)
    subprocess.run(
        [
            git,
            "-C",
            repo,
            "-c",
            "user.name=Release Test",
            "-c",
            "user.email=release@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    subprocess.run([git, "-C", repo, "tag", "v9.8.7-build9"], check=True)

    assert verify_release.verify_repository_version(
        repo,
        require_tag=True,
        release_tag="v9.8.7-build9",
    ) == "9.8.7"
    with pytest.raises(verify_release.ReleaseVerificationError, match="candidate tag"):
        verify_release.verify_repository_version(
            repo,
            require_tag=True,
            release_tag="v9.8.7-rc1",
        )
