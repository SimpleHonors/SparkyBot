"""Evidence-based GW2, ArcDPS, and EVTC log-folder discovery.

Discovery is bounded to known locations, registry entries, Steam libraries,
and user-selected paths. It never scans an entire drive. Every valid install
is retained so the UI can ask which one the user wants.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Iterable


_MAX_INI_BYTES = 1024 * 1024
_PATH_KEY = "boss_encounter_path"
_GW2_EXECUTABLES = ("Gw2-64.exe", "Gw2.exe")
_VDF_PATH_RE = re.compile(r'"path"\s*"((?:[^"\\]|\\.)*)"', re.IGNORECASE)
_DRIVE_RELATIVE_SPOTS = (
    ("Guild Wars 2",),
    ("Games", "Guild Wars 2"),
    ("Program Files", "Guild Wars 2"),
    ("Program Files (x86)", "Guild Wars 2"),
    ("Steam", "steamapps", "common", "Guild Wars 2"),
    ("SteamLibrary", "steamapps", "common", "Guild Wars 2"),
    ("Games", "Steam", "steamapps", "common", "Guild Wars 2"),
)


@dataclass(frozen=True)
class GW2Installation:
    directory: Path
    executable: Path
    source: str
    rank: int


@dataclass(frozen=True)
class ArcDPSLogLocation:
    """Compatibility view for callers that only need config + log path."""

    path: Path
    config_file: Path | None


@dataclass(frozen=True)
class ArcDPSSetup:
    gw2_directory: Path | None
    arcdps_directory: Path
    config_file: Path | None
    configured_log_base: Path | None
    log_directory: Path
    log_source: str
    discovery_source: str
    rank: int
    config_modified_ns: int = 0


def _path_key(path: Path) -> str:
    value = os.path.normpath(str(path))
    return os.path.normcase(value) if os.name == "nt" else value


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = _path_key(path)
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _as_gw2_install(
    candidate: str | Path,
    *,
    source: str,
    rank: int,
) -> GW2Installation | None:
    path = Path(candidate)
    directory = path.parent if path.name.casefold() in {
        item.casefold() for item in _GW2_EXECUTABLES
    } else path
    for executable_name in _GW2_EXECUTABLES:
        executable = directory / executable_name
        if executable.is_file():
            return GW2Installation(directory, executable, source, rank)
    return None


def _steam_library_roots(steam_root: Path) -> list[Path]:
    roots = [steam_root]
    library_file = steam_root / "steamapps" / "libraryfolders.vdf"
    try:
        text = library_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return roots
    for match in _VDF_PATH_RE.finditer(text):
        roots.append(Path(match.group(1).replace("\\\\", "\\")))
    return _unique_paths(roots)


def _drive_roots() -> list[Path]:
    """Windows drive roots only; callers probe fixed children, never recurse."""
    if hasattr(os, "listdrives"):
        try:
            return [Path(item) for item in os.listdrives()]
        except OSError:
            pass
    if os.name != "nt":
        return []
    import string

    return [
        Path(f"{letter}:\\")
        for letter in string.ascii_uppercase
        if Path(f"{letter}:\\").exists()
    ]


def _registry_install_candidates() -> list[tuple[Path, str, int]]:
    candidates: list[tuple[Path, str, int]] = []
    try:
        import winreg
    except ImportError:
        return candidates

    access_modes = [winreg.KEY_READ]
    for flag_name in ("KEY_WOW64_64KEY", "KEY_WOW64_32KEY"):
        access_modes.append(winreg.KEY_READ | getattr(winreg, flag_name, 0))
    access_modes = list(dict.fromkeys(access_modes))

    # Standalone ArenaNet install. The Path value normally points at the exe.
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for access in access_modes:
            try:
                with winreg.OpenKey(
                    hive, r"Software\ArenaNet\Guild Wars 2", 0, access
                ) as key:
                    value, _kind = winreg.QueryValueEx(key, "Path")
            except OSError:
                continue
            candidates.append((Path(os.path.expandvars(str(value).strip('"'))), "Windows registry", 950))

    # Installer records cover installations that did not leave the ArenaNet
    # key behind. Enumeration is bounded to the Windows uninstall registry.
    uninstall_key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for access in access_modes:
            try:
                root = winreg.OpenKey(hive, uninstall_key, 0, access)
            except OSError:
                continue
            with root:
                index = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(root, subkey_name) as subkey:
                            display, _kind = winreg.QueryValueEx(subkey, "DisplayName")
                            if "guild wars 2" not in str(display).casefold():
                                continue
                            location, _kind = winreg.QueryValueEx(subkey, "InstallLocation")
                    except OSError:
                        continue
                    if location:
                        candidates.append((Path(str(location).strip('"')), "Windows installed apps", 925))

    # Steam itself may be on a custom drive; its VDF lists every library.
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam_path, _kind = winreg.QueryValueEx(key, "SteamPath")
    except OSError:
        return candidates
    for library in _steam_library_roots(Path(steam_path)):
        candidates.append((
            library / "steamapps" / "common" / "Guild Wars 2",
            "Steam library",
            900,
        ))
    return candidates


def _common_install_candidates() -> list[tuple[Path, str, int]]:
    candidates: list[tuple[Path, str, int]] = []
    explicit = os.environ.get("GW2_INSTALL_DIR")
    if explicit:
        candidates.append((Path(explicit), "GW2_INSTALL_DIR", 1000))
    for name in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(name)
        if not base:
            continue
        root = Path(base)
        candidates.extend((
            (root / "Guild Wars 2", f"{name} common location", 850),
            (
                root / "Steam" / "steamapps" / "common" / "Guild Wars 2",
                f"{name} Steam common location",
                825,
            ),
        ))
    for drive in _drive_roots():
        candidates.extend(
            (drive.joinpath(*parts), "common folder on local drive", 600)
            for parts in _DRIVE_RELATIVE_SPOTS
        )
    return candidates


def discover_gw2_installations(
    *,
    extra_candidates: Iterable[str | Path] = (),
    include_system: bool = True,
) -> tuple[GW2Installation, ...]:
    """Find every candidate that is proven by a real GW2 executable."""
    candidates: list[tuple[Path, str, int]] = [
        (Path(path), "user-selected location", 1100)
        for path in extra_candidates
    ]
    if include_system:
        # Cheap/common paths first, then registry and Steam custom libraries.
        candidates.extend(_common_install_candidates())
        candidates.extend(_registry_install_candidates())

    found: dict[str, GW2Installation] = {}
    for path, source, rank in candidates:
        installation = _as_gw2_install(path, source=source, rank=rank)
        if installation is None:
            continue
        key = _path_key(installation.directory)
        previous = found.get(key)
        if previous is None or installation.rank > previous.rank:
            found[key] = installation
    return tuple(sorted(found.values(), key=lambda item: (-item.rank, str(item.directory).casefold())))


def read_configured_log_path(config_file: str | Path) -> Path | None:
    """Read ArcDPS's active ``boss_encounter_path`` value, if one is set."""
    source = Path(config_file)
    try:
        with source.open("rb") as handle:
            raw = handle.read(_MAX_INI_BYTES + 1)
    except OSError:
        return None
    if len(raw) > _MAX_INI_BYTES:
        return None

    text = raw.decode("utf-8-sig", errors="replace")
    configured = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith((";", "#")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().casefold() == _PATH_KEY:
            configured = value.strip().strip('"').strip("'")

    if not configured:
        return None
    configured = os.path.expanduser(os.path.expandvars(configured))
    path = Path(configured)
    if path.is_absolute():
        return path

    # A normal config is <GW2>/addons/arcdps/arcdps.ini. For a separately
    # stored config, its own folder is the only defensible relative anchor.
    if (
        source.parent.name.casefold() == "arcdps"
        and source.parent.parent.name.casefold() == "addons"
    ):
        return source.parent.parent.parent / path
    return source.parent / path


def resolve_log_directory(configured_base: Path) -> Path:
    """Resolve ArcDPS's path-prefix/final-folder ambiguity using evidence."""
    base = configured_base
    if base.name.casefold() == "arcdps.cbtlogs":
        return base
    child = base / "arcdps.cbtlogs"
    if child.is_dir():
        return child
    if base.is_dir():
        return base
    # ArcDPS commonly treats the configured value as a prefix. The UI always
    # shows this choice and requires consent before it is saved.
    return child


def select_wvw_log_directory(log_directory: Path) -> Path:
    """Narrow an ArcDPS log root to WvW encounter folder 1 when evidenced."""
    base = Path(log_directory)
    if base.name == "1" or "wvw" in base.name.casefold():
        return base
    wvw = base / "1"
    if wvw.is_dir() or base.name.casefold() == "arcdps.cbtlogs":
        # Returning the conventional child before it exists is intentional:
        # setup then stays blocked until the user creates a real WvW log,
        # instead of accepting a PvE encounter folder or the all-mode root.
        return wvw
    try:
        named = sorted(
            child
            for child in base.iterdir()
            if child.is_dir() and "wvw" in child.name.casefold()
        )
    except OSError:
        named = []
    return named[0] if named else base


def _independent_config_candidates(documents: Path) -> list[tuple[Path, str, int]]:
    candidates = [
        (
            documents / "Guild Wars 2" / "addons" / "arcdps" / "arcdps.ini",
            "Documents ArcDPS settings",
            700,
        )
    ]
    for env_name in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if base:
            candidates.append((
                Path(base) / "Guild Wars 2" / "addons" / "arcdps" / "arcdps.ini",
                f"{env_name} ArcDPS settings",
                675,
            ))
    explicit = os.environ.get("ARCDPS_INSTALL_DIR")
    if explicit:
        path = Path(explicit)
        candidates.append((
            path if path.name.casefold() == "arcdps.ini" else path / "arcdps.ini",
            "ARCDPS_INSTALL_DIR",
            1000,
        ))
    return candidates


def _linked_gw2_directory(config_file: Path) -> Path | None:
    if (
        config_file.parent.name.casefold() == "arcdps"
        and config_file.parent.parent.name.casefold() == "addons"
    ):
        candidate = config_file.parent.parent.parent
        if any((candidate / name).is_file() for name in _GW2_EXECUTABLES):
            return candidate
    return None


def _arcdps_present(gw2_directory: Path) -> bool:
    """Bounded ArcDPS evidence for installs that have not written an INI."""
    return any((
        (gw2_directory / "addons" / "arcdps").is_dir(),
        (gw2_directory / "addons" / "arcdps" / "gw2addon_arcdps.dll").is_file(),
        (gw2_directory / "arcdps.log").is_file(),
        (gw2_directory / "d3d11.dll").is_file(),
        (gw2_directory / "bin64" / "d3d9.dll").is_file(),
    ))


def discover_arcdps_setups(
    documents: str | Path,
    *,
    gw2_installations: Iterable[GW2Installation] | None = None,
    extra_config_files: Iterable[str | Path] = (),
    include_system: bool = True,
) -> tuple[ArcDPSSetup, ...]:
    """Return all evidenced ArcDPS setups, ranked but never collapsed."""
    documents_path = Path(documents)
    installations = tuple(
        gw2_installations
        if gw2_installations is not None
        else discover_gw2_installations(include_system=include_system)
    )
    candidates: list[
        tuple[Path | None, GW2Installation | None, str, int]
    ] = []
    for installation in installations:
        config_file = installation.directory / "addons" / "arcdps" / "arcdps.ini"
        if config_file.is_file():
            candidates.append((
                config_file,
                installation,
                installation.source,
                installation.rank + 300,
            ))
        elif _arcdps_present(installation.directory):
            candidates.append((
                None,
                installation,
                f"{installation.source}; ArcDPS files found",
                installation.rank,
            ))
    candidates.extend(
        (Path(path), None, "user-selected ArcDPS settings", 1100)
        for path in extra_config_files
    )
    if include_system:
        candidates.extend(
            (path, None, source, rank)
            for path, source, rank in _independent_config_candidates(documents_path)
        )

    default_log = (
        documents_path / "Guild Wars 2" / "addons" / "arcdps" / "arcdps.cbtlogs"
    )
    found: dict[str, ArcDPSSetup] = {}
    for config_file, installation, source, rank in candidates:
        if config_file is not None and not config_file.is_file():
            continue
        if config_file is None and installation is None:
            continue
        configured_base = (
            read_configured_log_path(config_file)
            if config_file is not None
            else None
        )
        if configured_base is None:
            legacy_log = (
                installation.directory / "addons" / "arcdps" / "arcdps.cbtlogs"
                if installation is not None
                else None
            )
            if legacy_log is not None and legacy_log.is_dir():
                log_directory = legacy_log
                log_source = "ArcDPS log folder beside Guild Wars 2"
            else:
                log_directory = default_log
                log_source = "ArcDPS standard Documents folder"
        else:
            log_directory = resolve_log_directory(configured_base)
            log_source = "ArcDPS configured folder"
        if log_directory.is_dir():
            rank += 1000
        if configured_base is not None:
            rank += 200
        config_modified_ns = 0
        if config_file is not None:
            try:
                config_modified_ns = config_file.stat().st_mtime_ns
            except OSError:
                pass
        gw2_directory = (
            installation.directory if installation is not None
            else _linked_gw2_directory(config_file)  # type: ignore[arg-type]
        )
        arcdps_directory = (
            config_file.parent
            if config_file is not None
            else installation.directory / "addons" / "arcdps"  # type: ignore[union-attr]
        )
        setup = ArcDPSSetup(
            gw2_directory=gw2_directory,
            arcdps_directory=arcdps_directory,
            config_file=config_file,
            configured_log_base=configured_base,
            log_directory=log_directory,
            log_source=log_source,
            discovery_source=source,
            rank=rank,
            config_modified_ns=config_modified_ns,
        )
        key = _path_key(config_file or arcdps_directory)
        previous = found.get(key)
        if previous is None or setup.rank > previous.rank:
            found[key] = setup
    return tuple(sorted(
        found.values(),
        key=lambda item: (
            -item.rank,
            -item.config_modified_ns,
            str(item.config_file or item.arcdps_directory).casefold(),
        ),
    ))


def find_configured_log_location(
    documents: str | Path,
    *,
    extra_config_files: Iterable[str | Path] = (),
) -> ArcDPSLogLocation | None:
    """Compatibility helper returning the top-ranked detected setup."""
    setups = discover_arcdps_setups(
        documents,
        extra_config_files=extra_config_files,
    )
    if not setups:
        return None
    first = setups[0]
    return ArcDPSLogLocation(first.log_directory, first.config_file)
