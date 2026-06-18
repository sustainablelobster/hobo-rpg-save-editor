#!/usr/bin/env python3
"""Terminal save editor for Hobo: Tough Life."""

from __future__ import annotations

import os
import re
import shutil
import struct
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Optional, Sequence


GAME_DIR_NAME = "Hobo Tough Life"
SAVE_DIR = Path("HoboRPG_Data") / "Save"
BACKUP_DIR_ENV = "HOBOTOUGHLIFE_BACKUP_DIR"
CASH_KEY = b"\x04cash"
MAX_CASH = (1 << 31) - 1
PRIMARY_PARAMETER_COUNT_OFFSET = 0xBC
PRIMARY_PARAMETER_RECORD_OFFSET = 0xC0
PRIMARY_PARAMETER_RECORD_SIZE = 12
SECONDARY_PARAMETER_RECORD_SIZE = 8

PRIMARY_PARAMETER_NAMES = {
    0: "Health",
    1: "Food",
    2: "Morale",
    3: "Energy",
    4: "Warm",
    5: "Wet",
    6: "Illness",
    7: "Toxicity",
    8: "Alcohol",
    9: "Bathroom Need",
    10: "Smell",
    20: "Stamina",
    21: "GearSmell",
    22: "Willpower",
}

SECONDARY_PARAMETER_NAMES = {
    11: "WetResistance",
    12: "WarmResistance",
    13: "ToxicityResistance",
    14: "SmellResistance",
    15: "GritMax",
    16: "Immunity",
    17: "Attack",
    18: "Defense",
    19: "Charism",
    23: "TemperatureInCelsius",
}


class SaveFormatError(ValueError):
    """Raised when a save file does not match the expected binary format."""


@dataclass(frozen=True)
class PrimaryParameter:
    parameter_type: int
    current_value: int
    maximum_value: int

    @property
    def display_name(self) -> str:
        return PRIMARY_PARAMETER_NAMES.get(
            self.parameter_type,
            f"Unknown ({self.parameter_type})",
        )


@dataclass(frozen=True)
class SecondaryParameter:
    parameter_type: int
    current_value: float

    @property
    def display_name(self) -> str:
        return SECONDARY_PARAMETER_NAMES.get(
            self.parameter_type,
            f"Unknown ({self.parameter_type})",
        )


@dataclass(frozen=True)
class CharacterParameters:
    primary: tuple[PrimaryParameter, ...]
    secondary: tuple[SecondaryParameter, ...]


@dataclass(frozen=True)
class SaveRecord:
    account_id: str
    slot_name: str
    save_id: str
    display_name: str
    saved_at: str
    slot_path: Path
    character_path: Path

    def current_cash(self) -> int:
        return read_cash(self.character_path)


class BinaryReader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def read(self, size: int) -> bytes:
        end = self.offset + size
        if size < 0 or end > len(self.data):
            raise SaveFormatError("Unexpected end of slot file")
        value = self.data[self.offset:end]
        self.offset = end
        return value

    def read_u8(self) -> int:
        return self.read(1)[0]

    def read_u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def read_u64(self) -> int:
        return struct.unpack("<Q", self.read(8))[0]

    def read_7bit_int(self) -> int:
        value = 0
        for shift in range(0, 35, 7):
            byte = self.read_u8()
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
        raise SaveFormatError("Invalid 7-bit encoded string length")

    def read_string(self) -> str:
        size = self.read_7bit_int()
        try:
            return self.read(size).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SaveFormatError("Invalid UTF-8 string in slot file") from exc


def parse_slot(path: Path) -> SaveRecord:
    """Parse the known fields in an NFS_WorldConfigs/slot* file."""
    reader = BinaryReader(path.read_bytes())
    raw_save_id = reader.read_string()
    save_id = raw_save_id.removeprefix("$")
    try:
        save_id = str(uuid.UUID(save_id))
    except ValueError as exc:
        raise SaveFormatError(f"Invalid save UUID in {path}") from exc

    # These fields are stable in observed saves but their meanings are unknown.
    reader.read_u32()
    raw_name = reader.read_string()
    reader.read_u32()
    reader.read_u32()
    reader.read_u64()
    saved_at = reader.read_string()

    display_name = raw_name.strip(" /") or raw_name or "(unnamed)"
    account_dir = path.parent.parent
    character_path = account_dir / "NFS_Characters" / f"{save_id}_ls"
    return SaveRecord(
        account_id=account_dir.name,
        slot_name=path.name,
        save_id=save_id,
        display_name=display_name,
        saved_at=saved_at,
        slot_path=path,
        character_path=character_path,
    )


def _slot_sort_key(path: Path) -> tuple[int, str]:
    match = re.fullmatch(r"slot(\d+)", path.name, re.IGNORECASE)
    return (int(match.group(1)), path.name) if match else (sys.maxsize, path.name)


def scan_saves(game_dir: Path) -> tuple[list[SaveRecord], list[str]]:
    records: list[SaveRecord] = []
    warnings: list[str] = []
    save_root = game_dir / SAVE_DIR
    if not save_root.is_dir():
        return records, [f"Save directory not found: {save_root}"]

    for account_dir in sorted(path for path in save_root.iterdir() if path.is_dir()):
        configs = account_dir / "NFS_WorldConfigs"
        if not configs.is_dir():
            continue
        for slot_path in sorted(configs.glob("slot*"), key=_slot_sort_key):
            if not slot_path.is_file():
                continue
            try:
                records.append(parse_slot(slot_path))
            except (OSError, SaveFormatError) as exc:
                warnings.append(f"Could not parse {slot_path}: {exc}")
    return records, warnings


def _unescape_vdf(value: str) -> str:
    return value.replace(r"\\", "\\").replace(r"\"", '"')


def _vdf_tokens(text: str) -> Iterator[tuple[str, str]]:
    token_pattern = re.compile(r'"((?:\\.|[^"])*)"|([{}])')
    for match in token_pattern.finditer(text):
        if match.group(1) is not None:
            yield ("string", _unescape_vdf(match.group(1)))
        else:
            yield (match.group(2), match.group(2))


def _parse_vdf_mapping(
    tokens: Sequence[tuple[str, str]], index: int = 0, stop_at_brace: bool = False
) -> tuple[dict[str, object], int]:
    result: dict[str, object] = {}
    while index < len(tokens):
        kind, value = tokens[index]
        if kind == "}":
            if not stop_at_brace:
                raise ValueError("Unexpected closing brace")
            return result, index + 1
        if kind != "string":
            raise ValueError("Expected a VDF key")
        key = value
        index += 1
        if index >= len(tokens):
            raise ValueError("Missing VDF value")
        kind, value = tokens[index]
        if kind == "{":
            child, index = _parse_vdf_mapping(tokens, index + 1, True)
            result[key] = child
        elif kind == "string":
            result[key] = value
            index += 1
        else:
            raise ValueError("Invalid VDF value")
    if stop_at_brace:
        raise ValueError("Missing closing brace")
    return result, index


def parse_library_folders(path: Path) -> list[Path]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        parsed, _ = _parse_vdf_mapping(list(_vdf_tokens(text)))
    except (OSError, ValueError):
        return []

    folders = next(
        (
            value
            for key, value in parsed.items()
            if key.casefold() == "libraryfolders"
        ),
        parsed,
    )
    if not isinstance(folders, dict):
        return []

    paths: list[Path] = []
    for key, value in folders.items():
        raw_path: Optional[str] = None
        if isinstance(value, dict):
            raw_path = next(
                (
                    child
                    for child_key, child in value.items()
                    if child_key.casefold() == "path" and isinstance(child, str)
                ),
                None,
            )
        elif key.isdigit() and isinstance(value, str):
            raw_path = value
        if raw_path:
            paths.append(Path(raw_path).expanduser())
    return paths


def default_steam_roots(
    home: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    platform: Optional[str] = None,
) -> list[Path]:
    home = home or Path.home()
    env = env or os.environ
    platform = platform or sys.platform
    roots: list[Path] = []

    if env.get("STEAM_DIR"):
        roots.append(Path(env["STEAM_DIR"]).expanduser())

    if platform.startswith("win"):
        for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
            if env.get(variable):
                roots.append(Path(env[variable]) / "Steam")
        if env.get("LOCALAPPDATA"):
            roots.append(Path(env["LOCALAPPDATA"]) / "Steam")
    elif platform == "darwin":
        roots.append(home / "Library" / "Application Support" / "Steam")
    else:
        roots.extend(
            [
                home / ".steam" / "steam",
                home / ".steam" / "root",
                home / ".steam" / "debian-installation",
                home / ".local" / "share" / "Steam",
                home
                / ".var"
                / "app"
                / "com.valvesoftware.Steam"
                / ".local"
                / "share"
                / "Steam",
            ]
        )
    return _unique_paths(roots)


def default_backup_dir(
    home: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    platform: Optional[str] = None,
) -> Path:
    home = home or Path.home()
    env = os.environ if env is None else env
    platform = platform or sys.platform

    if platform.startswith("win"):
        data_root = (
            Path(env["LOCALAPPDATA"])
            if env.get("LOCALAPPDATA")
            else home / "AppData" / "Local"
        )
        return data_root / "Hobo Save Editor" / "Backups"
    if platform == "darwin":
        return (
            home
            / "Library"
            / "Application Support"
            / "Hobo Save Editor"
            / "Backups"
        )

    data_root = (
        Path(env["XDG_DATA_HOME"])
        if env.get("XDG_DATA_HOME")
        else home / ".local" / "share"
    )
    return data_root / "hobo-save-editor" / "backups"


def resolve_backup_dir(
    override: Optional[Path] = None,
    home: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    platform: Optional[str] = None,
) -> Path:
    env = os.environ if env is None else env
    configured = override
    if configured is None and env.get(BACKUP_DIR_ENV):
        configured = Path(env[BACKUP_DIR_ENV])
    if configured is None:
        configured = default_backup_dir(home=home, env=env, platform=platform)
    return configured.expanduser().absolute()


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        normalized = path.expanduser().absolute()
        key = os.path.normcase(str(normalized))
        if key not in seen:
            seen.add(key)
            unique.append(normalized)
    return unique


def _game_candidates(path: Path) -> Iterator[Path]:
    yield path
    yield path / "steamapps" / "common" / GAME_DIR_NAME
    yield path / "common" / GAME_DIR_NAME


def discover_game_installs(
    extra_paths: Iterable[Path] = (),
    steam_roots: Optional[Iterable[Path]] = None,
) -> list[Path]:
    direct_paths = list(extra_paths)
    configured = os.environ.get("HOBOTOUGHLIFE_DIR")
    if configured:
        direct_paths.append(Path(configured))

    if steam_roots is None:
        cwd = Path.cwd().absolute()
        for path in (cwd, *cwd.parents):
            if path.name.casefold() == GAME_DIR_NAME.casefold():
                direct_paths.append(path)
                break

    roots = list(steam_roots) if steam_roots is not None else default_steam_roots()
    library_roots: list[Path] = list(roots)
    for root in roots:
        config = root / "steamapps" / "libraryfolders.vdf"
        library_roots.extend(parse_library_folders(config))

    candidates: list[Path] = []
    for path in direct_paths:
        candidates.extend(_game_candidates(path))
    for root in library_roots:
        candidates.extend(_game_candidates(root))

    installs = []
    for candidate in _unique_paths(candidates):
        if (candidate / "HoboRPG_Data").is_dir():
            installs.append(candidate)
    return installs


def _read_parameter_count(data: bytes, offset: int, label: str) -> int:
    if offset + 4 > len(data):
        raise SaveFormatError(
            f"Character file is missing the {label} parameter count"
        )
    count = struct.unpack_from("<i", data, offset)[0]
    if count < 0:
        raise SaveFormatError(f"{label.capitalize()} parameter count is negative")
    return count


def parse_character_parameters(data: bytes) -> CharacterParameters:
    """Parse bounded primary and secondary parameter records from a character."""
    primary_count = _read_parameter_count(
        data,
        PRIMARY_PARAMETER_COUNT_OFFSET,
        "primary",
    )
    primary_end = (
        PRIMARY_PARAMETER_RECORD_OFFSET
        + primary_count * PRIMARY_PARAMETER_RECORD_SIZE
    )
    if primary_end > len(data):
        raise SaveFormatError(
            "Primary parameter records extend beyond the character file"
        )

    primary = tuple(
        PrimaryParameter(*struct.unpack_from("<iii", data, offset))
        for offset in range(
            PRIMARY_PARAMETER_RECORD_OFFSET,
            primary_end,
            PRIMARY_PARAMETER_RECORD_SIZE,
        )
    )

    secondary_count = _read_parameter_count(data, primary_end, "secondary")
    secondary_start = primary_end + 4
    secondary_end = (
        secondary_start
        + secondary_count * SECONDARY_PARAMETER_RECORD_SIZE
    )
    if secondary_end > len(data):
        raise SaveFormatError(
            "Secondary parameter records extend beyond the character file"
        )

    secondary = tuple(
        SecondaryParameter(*struct.unpack_from("<if", data, offset))
        for offset in range(
            secondary_start,
            secondary_end,
            SECONDARY_PARAMETER_RECORD_SIZE,
        )
    )
    return CharacterParameters(primary=primary, secondary=secondary)


def read_character_parameters(path: Path) -> CharacterParameters:
    """Read primary and secondary parameter records from a character file."""
    if not path.is_file():
        raise SaveFormatError(f"Character file not found: {path}")
    return parse_character_parameters(path.read_bytes())


def locate_primary_parameter(
    data: bytes,
    parameter_type: int,
) -> tuple[int, PrimaryParameter]:
    """Locate one known primary parameter and its current-value offset."""
    if parameter_type not in PRIMARY_PARAMETER_NAMES:
        raise ValueError(f"Unknown primary parameter type: {parameter_type}")

    primary_count = _read_parameter_count(
        data,
        PRIMARY_PARAMETER_COUNT_OFFSET,
        "primary",
    )
    primary_end = (
        PRIMARY_PARAMETER_RECORD_OFFSET
        + primary_count * PRIMARY_PARAMETER_RECORD_SIZE
    )
    if primary_end > len(data):
        raise SaveFormatError(
            "Primary parameter records extend beyond the character file"
        )

    matches: list[tuple[int, PrimaryParameter]] = []
    for offset in range(
        PRIMARY_PARAMETER_RECORD_OFFSET,
        primary_end,
        PRIMARY_PARAMETER_RECORD_SIZE,
    ):
        parameter = PrimaryParameter(
            *struct.unpack_from("<iii", data, offset)
        )
        if parameter.parameter_type == parameter_type:
            matches.append((offset + 4, parameter))

    if len(matches) != 1:
        name = PRIMARY_PARAMETER_NAMES[parameter_type]
        raise SaveFormatError(
            f"Expected exactly one {name} parameter, found {len(matches)}"
        )
    return matches[0]


def locate_cash(data: bytes) -> tuple[int, int]:
    offsets: list[int] = []
    start = 0
    while True:
        key_offset = data.find(CASH_KEY, start)
        if key_offset < 0:
            break
        offsets.append(key_offset + len(CASH_KEY))
        start = key_offset + 1

    valid_offsets = [offset for offset in offsets if offset + 4 <= len(data)]
    if len(valid_offsets) != 1:
        raise SaveFormatError(
            f"Expected exactly one cash field, found {len(valid_offsets)}"
        )
    value_offset = valid_offsets[0]
    return value_offset, struct.unpack_from("<i", data, value_offset)[0]


def read_cash(path: Path) -> int:
    if not path.is_file():
        raise SaveFormatError(f"Character file not found: {path}")
    return locate_cash(path.read_bytes())[1]


def _next_backup_path(
    path: Path, backup_dir: Path, now: Optional[datetime] = None
) -> Path:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    base = backup_dir / f"{path.name}.bak-{stamp}"
    if not base.exists():
        return base
    for number in range(1, 1000):
        candidate = backup_dir / f"{path.name}.bak-{stamp}-{number}"
        if not candidate.exists():
            return candidate
    raise OSError("Could not choose a unique backup filename")


def _game_installation_for_character(path: Path) -> Optional[Path]:
    for parent in path.expanduser().absolute().parents:
        if parent.name.casefold() == "hoborpg_data":
            return parent.parent
    return None


def _validate_backup_location(path: Path, backup_dir: Path) -> None:
    game_dir = _game_installation_for_character(path)
    if game_dir is None:
        return
    resolved_backup = backup_dir.resolve(strict=False)
    resolved_game = game_dir.resolve(strict=False)
    try:
        resolved_backup.relative_to(resolved_game)
    except ValueError:
        return
    raise ValueError(
        "Backup directory must be outside the game installation: "
        f"{resolved_backup}"
    )


def _write_verified_backup(
    backup_path: Path,
    original: bytes,
) -> None:
    with backup_path.open("xb") as backup_file:
        backup_file.write(original)
        backup_file.flush()
        os.fsync(backup_file.fileno())
    if backup_path.read_bytes() != original:
        raise OSError("Backup verification failed; the save was not changed")


def _write_updated_character(
    path: Path,
    original: bytes,
    updated: bytes,
    now: Optional[datetime] = None,
    backup_dir: Optional[Path] = None,
) -> Path:
    """Back up, atomically replace, and verify a character file."""
    backup_dir = resolve_backup_dir(backup_dir)
    _validate_backup_location(path, backup_dir)
    if path.read_bytes() != original:
        raise SaveFormatError(
            "Character file changed after it was read; refresh and try again"
        )
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = _next_backup_path(path, backup_dir, now)
    _write_verified_backup(backup_path, original)

    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(updated)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        shutil.copystat(path, temp_path)
        if path.read_bytes() != original:
            raise SaveFormatError(
                "Character file changed while the update was staged; "
                "the save was not changed"
            )
        os.replace(temp_path, path)
        temp_path = None
        if path.read_bytes() != updated:
            shutil.copy2(backup_path, path)
            raise OSError("Verification failed; the original save was restored")
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    return backup_path


def set_cash(
    path: Path,
    new_value: int,
    now: Optional[datetime] = None,
    backup_dir: Optional[Path] = None,
) -> tuple[int, Path]:
    if not 0 <= new_value <= MAX_CASH:
        raise ValueError(f"Cash must be between 0 and {MAX_CASH}")

    original = path.read_bytes()
    value_offset, old_value = locate_cash(original)
    updated = bytearray(original)
    struct.pack_into("<i", updated, value_offset, new_value)
    backup_path = _write_updated_character(
        path,
        original,
        bytes(updated),
        now=now,
        backup_dir=backup_dir,
    )
    return old_value, backup_path


def set_primary_parameter(
    path: Path,
    parameter_type: int,
    new_value: int,
    now: Optional[datetime] = None,
    backup_dir: Optional[Path] = None,
) -> tuple[int, Path]:
    """Set one known primary current value without changing its maximum."""
    original = path.read_bytes()
    value_offset, parameter = locate_primary_parameter(
        original,
        parameter_type,
    )
    if parameter.maximum_value < 0:
        raise SaveFormatError(
            f"{parameter.display_name} has a negative maximum value"
        )
    if not 0 <= new_value <= parameter.maximum_value:
        raise ValueError(
            f"{parameter.display_name} must be between 0 and "
            f"{parameter.maximum_value}"
        )

    updated = bytearray(original)
    struct.pack_into("<i", updated, value_offset, new_value)
    backup_path = _write_updated_character(
        path,
        original,
        bytes(updated),
        now=now,
        backup_dir=backup_dir,
    )
    return parameter.current_value, backup_path


def run_tui(
    game_dir: Optional[Path] = None,
    backup_dir: Optional[Path] = None,
) -> int:
    """Launch the Textual application without coupling it to domain logic."""
    from .tui import run_app

    return run_app(game_dir=game_dir, backup_dir=backup_dir)
