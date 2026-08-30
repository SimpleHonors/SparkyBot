from pathlib import Path

import core.arcdps_config as arcdps_config
from core.arcdps_config import (
    _steam_library_roots,
    discover_arcdps_setups,
    discover_gw2_installations,
    read_configured_log_path,
    resolve_log_directory,
    select_wvw_log_directory,
)


def make_gw2(root: Path, configured_log: Path | None = None) -> Path:
    root.mkdir(parents=True)
    (root / "Gw2-64.exe").write_bytes(b"test")
    arcdps = root / "addons" / "arcdps"
    arcdps.mkdir(parents=True)
    value = str(configured_log) if configured_log is not None else ""
    (arcdps / "arcdps.ini").write_text(
        f"[session]\nboss_encounter_path={value}\n",
        encoding="utf-8",
    )
    return root


def test_custom_log_path_ignores_comments_and_uses_last_active_value(tmp_path):
    config = tmp_path / "arcdps.ini"
    config.write_text(
        ";boss_encounter_path=C:/ignored\n"
        "boss_encounter_path='first'\n"
        "boss_encounter_path=\"chosen\"\n",
        encoding="utf-8",
    )

    assert read_configured_log_path(config) == tmp_path / "chosen"


def test_custom_log_root_resolves_prefix_or_final_folder(tmp_path):
    prefix = tmp_path / "custom-logs"
    child = prefix / "arcdps.cbtlogs"
    child.mkdir(parents=True)

    assert resolve_log_directory(prefix) == child
    assert resolve_log_directory(child) == child

    direct = tmp_path / "direct-existing-folder"
    direct.mkdir()
    assert resolve_log_directory(direct) == direct


def test_wvw_selection_uses_encounter_one_and_never_guesses_other_numbers(tmp_path):
    base = tmp_path / "arcdps.cbtlogs"
    (base / "2").mkdir(parents=True)

    assert select_wvw_log_directory(base) == base / "1"
    assert select_wvw_log_directory(base / "2") == base / "2"


def test_gw2_discovery_requires_an_executable_and_keeps_multiple_installs(tmp_path):
    first = make_gw2(tmp_path / "Guild Wars 2")
    second = make_gw2(tmp_path / "Steam" / "Guild Wars 2")
    false_positive = tmp_path / "Steam-without-GW2"
    false_positive.mkdir()

    found = discover_gw2_installations(
        extra_candidates=(false_positive, second, first),
        include_system=False,
    )

    assert {item.directory for item in found} == {first, second}
    assert all(item.executable.name == "Gw2-64.exe" for item in found)


def test_steam_library_file_finds_custom_libraries_without_assuming_gw2(tmp_path):
    steam = tmp_path / "Steam"
    custom = tmp_path / "Games on D"
    (steam / "steamapps").mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders"\n{{\n  "1" {{ "path" "{custom}" }}\n}}\n',
        encoding="utf-8",
    )

    roots = _steam_library_roots(steam)

    assert roots == [steam, custom]
    assert not discover_gw2_installations(
        extra_candidates=(
            steam / "steamapps" / "common" / "Guild Wars 2",
            custom / "steamapps" / "common" / "Guild Wars 2",
        ),
        include_system=False,
    )


def test_steam_library_parser_handles_windows_escaped_paths(tmp_path):
    steam = tmp_path / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n  "1" { "path" "D:\\\\SteamLibrary" }\n}\n',
        encoding="utf-8",
    )

    assert Path(r"D:\SteamLibrary") in _steam_library_roots(steam)


def test_real_nonstandard_steam_gw2_library_vdf_shape(tmp_path):
    steam = tmp_path / "Program Files (x86)" / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n'
        "{\n"
        '  "0" { "path" "C:\\\\Program Files (x86)\\\\Steam" '
        '"apps" { "228980" "226264803" "1808500" "43084240722" } }\n'
        '  "1" { "path" "D:\\\\Games\\\\Steam" '
        '"apps" { "29720" "4175753850" "1284210" "0" } }\n'
        "}\n",
        encoding="utf-8",
    )

    roots = _steam_library_roots(steam)

    assert Path(r"C:\Program Files (x86)\Steam") in roots
    assert Path(r"D:\Games\Steam") in roots


def test_bounded_drive_probes_find_nonstandard_common_folder(tmp_path, monkeypatch):
    drive = tmp_path / "D-drive"
    gw2 = make_gw2(drive / "Games" / "Guild Wars 2")
    monkeypatch.setattr(arcdps_config, "_drive_roots", lambda: [drive])
    for name in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        monkeypatch.delenv(name, raising=False)

    found = discover_gw2_installations(include_system=True)

    assert gw2 in {item.directory for item in found}


def test_arcdps_discovery_keeps_every_valid_install_and_its_own_log_path(tmp_path):
    documents = tmp_path / "Documents"
    first_base = tmp_path / "logs-one"
    first_logs = first_base / "arcdps.cbtlogs"
    first_logs.mkdir(parents=True)
    second_logs = tmp_path / "logs-two"
    second_logs.mkdir()
    first = make_gw2(tmp_path / "Standalone GW2", first_base)
    second = make_gw2(tmp_path / "Steam GW2", second_logs)
    installations = discover_gw2_installations(
        extra_candidates=(first, second),
        include_system=False,
    )

    setups = discover_arcdps_setups(
        documents,
        gw2_installations=installations,
        include_system=False,
    )

    assert len(setups) == 2
    by_gw2 = {setup.gw2_directory: setup for setup in setups}
    assert by_gw2[first].log_directory == first_logs
    assert by_gw2[second].log_directory == second_logs
    assert all(setup.config_file.is_file() for setup in setups)


def test_separate_arcdps_install_is_supported_and_uses_default_when_unset(tmp_path):
    documents = tmp_path / "Documents"
    config = tmp_path / "ArcDPS Somewhere Else" / "arcdps.ini"
    config.parent.mkdir()
    config.write_text("[session]\nboss_encounter_path=\n", encoding="utf-8")

    setups = discover_arcdps_setups(
        documents,
        gw2_installations=(),
        extra_config_files=(config,),
        include_system=False,
    )

    assert len(setups) == 1
    assert setups[0].gw2_directory is None
    assert setups[0].arcdps_directory == config.parent
    assert setups[0].log_directory == (
        documents / "Guild Wars 2" / "addons" / "arcdps" / "arcdps.cbtlogs"
    )


def test_arcdps_files_without_ini_are_kept_and_legacy_logs_win(tmp_path):
    documents = tmp_path / "Documents"
    gw2 = make_gw2(tmp_path / "GW2")
    (gw2 / "addons" / "arcdps" / "arcdps.ini").unlink()
    legacy = gw2 / "addons" / "arcdps" / "arcdps.cbtlogs"
    legacy.mkdir()
    installations = discover_gw2_installations(
        extra_candidates=(gw2,), include_system=False
    )

    setups = discover_arcdps_setups(
        documents,
        gw2_installations=installations,
        include_system=False,
    )

    assert len(setups) == 1
    assert setups[0].config_file is None
    assert setups[0].log_directory == legacy


def test_existing_configured_logs_rank_above_a_missing_higher_source(tmp_path):
    documents = tmp_path / "Documents"
    missing_logs = tmp_path / "missing"
    live_logs = tmp_path / "live"
    live_logs.mkdir()
    first = make_gw2(tmp_path / "A", missing_logs)
    second = make_gw2(tmp_path / "B", live_logs)
    installations = list(discover_gw2_installations(
        extra_candidates=(first, second), include_system=False
    ))
    installations[0] = type(installations[0])(
        installations[0].directory,
        installations[0].executable,
        installations[0].source,
        1500,
    )

    setups = discover_arcdps_setups(
        documents,
        gw2_installations=installations,
        include_system=False,
    )

    assert setups[0].log_directory == live_logs
