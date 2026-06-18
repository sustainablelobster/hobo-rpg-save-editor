"""Bruno reputation format, mutation, and research helpers."""

from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence


BRUNO_KEYWORD = "bruno"
BRUNO_ARCHETYPE_ID = 28
DEFAULT_MIN_REPUTATION = -100
DEFAULT_MAX_REPUTATION = 100
MIN_WRITABLE_REPUTATION = 0
MAX_WRITABLE_REPUTATION = 100
REPUTATION_RECORD_SIZE = 8
# This order is stable across all six observed character saves. It identifies
# the top-level ReputationSaveData list without relying on an absolute offset.
OBSERVED_REPUTATION_ARCHETYPE_IDS = (
    7, 13, 15, 16, 110, 76, 111, 113, 106, 81, 104, 105, 22, 83, 82, 102,
    28, 91, 103, 73, 23, 24, 98, 101, 90, 100, 94, 96, 92, 89, 84, 27, 25,
    95, 87, 72, 97, 114, 115, 122, 125, 116, 117, 118, 119, 120, 121, 123,
    124, 126, 132, 133, 134, 135, 136, 137, 138, 139, 140, 141, 142, 143,
    144, 145, 146, 147, 148, 149, 150, 151, 152, 153, 154, 155, 156, 157,
    158, 159, 160, 161, 162, 163, 164, 165, 166, 167, 168, 169, 170, 171,
    172, 173, 174, 175, 176, 177, 178, 179, 180, 181, 11, 19, 21, 183, 184,
    185, 186, 188, 193, 41, 3, 4, 5,
)
OBSERVED_REPUTATION_COUNT = len(OBSERVED_REPUTATION_ARCHETYPE_IDS)
REPUTATION_ARCHETYPE_NAMES = {
    7: "Hobo_Furgrim",
    13: "Hobo_Medved",
    15: "Hobo_Valoun",
    16: "Hobo_Brekeke",
    110: "Hobo_Drax",
    76: "Hobo_Monty",
    111: "Hobo_Rejsek",
    113: "Hobo_Festr",
    106: "Inside_Charvat",
    81: "Inside_PolicistaVeSluzbe",
    104: "Inside_ZamestnanecEvropy2",
    105: "Inside_ZamestnanecKinaMetropol",
    22: "Specific_Anatoly",
    83: "Specific_BarmanPajzl",
    82: "Specific_BarmanShishaBar",
    102: "Specific_BratrMarek",
    28: "Specific_Bruno",
    91: "Specific_Chef",
    103: "Specific_DrKrasny",
    73: "Specific_Emka",
    23: "Specific_Hanicka",
    24: "Specific_IrenaSkrabisova",
    98: "Specific_Ivan",
    101: "Specific_Kadlec",
    90: "Specific_Kocour",
    100: "Specific_Kotler",
    94: "Specific_LiborPetula",
    96: "Specific_Loudova",
    92: "Specific_LubosHollzer",
    89: "Specific_MartinBach",
    84: "Specific_Master",
    27: "Specific_PaterBurian",
    25: "Specific_PorucikHajek",
    95: "Specific_SestraAnezka",
    87: "Specific_Smelar",
    72: "Specific_StandaGrznar",
    97: "Specific_ZichVedouci",
    114: "Dog_Rottweiler",
    115: "Dog_Shepherd",
    122: "Dog_Sheepdog",
    125: "Dog_Quest",
    116: "Hobo_Majsner",
    117: "Hobo_Vanga",
    118: "Hobo_Jolanda",
    119: "Hobo_Langos",
    120: "Hobo_Kentus",
    121: "Hobo_Koblih",
    123: "Inside_PaniSpurna",
    124: "Inside_PanSpurny",
    126: "Specific_PetrKubik",
    132: "Specific_Baron",
    133: "Specific_Zelinar",
    134: "Specific_Antonin",
    135: "Hobo_MasaPetrankova",
    136: "Hobo_HonzaKonecny",
    137: "Hobo_Sergej",
    138: "Hobo_Struk",
    139: "Hobo_Mahoney",
    140: "Hobo_Bigas",
    141: "Hobo_Princ",
    142: "Hobo_LiborPanika",
    143: "Hobo_AlesSmutny",
    144: "Hobo_Homola",
    145: "Hobo_Herdyn",
    146: "Hobo_Mara",
    147: "Hobo_Veverka",
    148: "Hobo_Zachy",
    149: "Hobo_Kardinal",
    150: "Hobo_Jankins",
    151: "Hobo_Pepic",
    152: "Hobo_Bazooka",
    153: "Hobo_Rocky",
    154: "Hobo_Ferguson",
    155: "Hobo_Dezon",
    156: "Hobo_Moiser",
    157: "Hobo_Ramsy",
    158: "Hobo_Starky",
    159: "Hobo_Satanista",
    160: "Hobo_Crazy",
    161: "Hobo_Kemr",
    162: "Hobo_Rasken",
    163: "Hobo_Rita",
    164: "Specific_Anton",
    165: "Hobo_Bond",
    166: "Hobo_Britva",
    167: "Hobo_Ferenz",
    168: "Hobo_Rizek",
    169: "Hobo_Leos",
    170: "Hobo_Bodom",
    171: "Hobo_Miky",
    172: "Hobo_Mazal",
    173: "Hobo_Viktor",
    174: "Hobo_Mako",
    175: "Specific_MartinSoucek",
    176: "Hobo_Fin",
    177: "Hobo_Pajour",
    178: "Hobo_Kroll",
    179: "Hobo_Ghaul",
    180: "Specific_MestskaHlidka",
    181: "Specific_PolicistaKvapil",
    11: "Hobo_Dedek",
    19: "Hobo_Cica",
    21: "Hobo_Ruda",
    183: "Hobo_Chicco",
    184: "Hobo_Henry",
    185: "Hobo_Frix",
    186: "Hobo_Kashee",
    188: "Hobo_Marty",
    193: "Hobo_Herold",
    41: "Hobo_Smrad",
    3: "Specific_Dory",
    4: "Specific_Ilona",
    5: "Specific_Hektor",
}


class ReputationResearchError(ValueError):
    """Raised when a reputation research result is not actionable."""


@dataclass(frozen=True)
class ReputationRecord:
    record_offset: int
    archetype_offset: int
    value_offset: int
    archetype_id: int
    value: int


@dataclass(frozen=True)
class ReputationTable:
    count_offset: int
    records_start: int
    records_end: int
    records: tuple[ReputationRecord, ...]

    def get(self, archetype_id: int) -> ReputationRecord:
        matches = tuple(
            record
            for record in self.records
            if record.archetype_id == archetype_id
        )
        if len(matches) != 1:
            raise ReputationResearchError(
                "expected exactly one reputation record for archetype "
                f"{archetype_id}, found {len(matches)}"
            )
        return matches[0]


@dataclass(frozen=True)
class ReputationChange:
    archetype_id: int
    expected_value: int
    new_value: int


@dataclass(frozen=True)
class ReputationEditPlan:
    source_sha256: str
    changes: tuple[ReputationChange, ...]


@dataclass(frozen=True)
class NamedIntEntry:
    entry_offset: int
    key_offset: int
    value_offset: int
    key: str
    value: int


@dataclass(frozen=True)
class NamedIntRun:
    start: int
    end: int
    entries: tuple[NamedIntEntry, ...]


@dataclass(frozen=True)
class NamedIntChange:
    key: str
    before: Optional[int]
    after: Optional[int]


@dataclass(frozen=True)
class IntegerCandidate:
    offset: int
    before: int
    after: int

    @property
    def delta(self) -> int:
        return self.after - self.before


@dataclass(frozen=True)
class IntegerCandidateSeries:
    offset: int
    values: tuple[int, ...]

    @property
    def deltas(self) -> tuple[int, ...]:
        return tuple(
            after - before
            for before, after in zip(self.values, self.values[1:])
        )


@dataclass(frozen=True)
class SerializedStringHit:
    offset: int
    text: str


@dataclass(frozen=True)
class BrunoReputationComparison:
    before_sha256: str
    after_sha256: str
    before_named_state: tuple[NamedIntEntry, ...]
    after_named_state: tuple[NamedIntEntry, ...]
    named_state_changes: tuple[NamedIntChange, ...]
    integer_candidates: tuple[IntegerCandidate, ...]
    before_bruno_strings: tuple[SerializedStringHit, ...]
    after_bruno_strings: tuple[SerializedStringHit, ...]


@dataclass(frozen=True)
class BrunoReputationSequenceComparison:
    expected_values: tuple[int, ...]
    transitions: tuple[BrunoReputationComparison, ...]
    shared_integer_candidates: tuple[IntegerCandidateSeries, ...]
    matching_integer_candidates: tuple[IntegerCandidateSeries, ...]


def reputation_raw_name(archetype_id: int) -> str:
    """Return the verified enum name for a serialized reputation record."""
    try:
        return REPUTATION_ARCHETYPE_NAMES[archetype_id]
    except KeyError as exc:
        raise ReputationResearchError(
            f"unknown reputation archetype ID {archetype_id}"
        ) from exc


def reputation_display_name(archetype_id: int) -> str:
    """Return a compact user-facing name derived from the enum name."""
    raw_name = reputation_raw_name(archetype_id)
    _, _, name = raw_name.partition("_")
    name = name or raw_name
    name = name.replace("_", " ")
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)


def find_reputation_tables(
    data: bytes,
    *,
    minimum: int = DEFAULT_MIN_REPUTATION,
    maximum: int = DEFAULT_MAX_REPUTATION,
) -> tuple[ReputationTable, ...]:
    """Find structurally identified ReputationSaveData lists."""
    count_raw = struct.pack("<i", OBSERVED_REPUTATION_COUNT)
    records_size = OBSERVED_REPUTATION_COUNT * REPUTATION_RECORD_SIZE
    tables: list[ReputationTable] = []
    search_offset = 0
    while True:
        count_offset = data.find(count_raw, search_offset)
        if count_offset < 0:
            break
        records_start = count_offset + 4
        records_end = records_start + records_size
        search_offset = count_offset + 1
        if records_end > len(data):
            continue

        raw_values = struct.unpack_from(
            f"<{OBSERVED_REPUTATION_COUNT * 2}i",
            data,
            records_start,
        )
        archetype_ids = raw_values[::2]
        values = raw_values[1::2]
        if archetype_ids != OBSERVED_REPUTATION_ARCHETYPE_IDS:
            continue
        if any(value < minimum or value > maximum for value in values):
            continue

        records = tuple(
            ReputationRecord(
                record_offset=records_start + index * REPUTATION_RECORD_SIZE,
                archetype_offset=(
                    records_start + index * REPUTATION_RECORD_SIZE
                ),
                value_offset=(
                    records_start + index * REPUTATION_RECORD_SIZE + 4
                ),
                archetype_id=archetype_id,
                value=value,
            )
            for index, (archetype_id, value) in enumerate(
                zip(archetype_ids, values)
            )
        )
        tables.append(
            ReputationTable(
                count_offset=count_offset,
                records_start=records_start,
                records_end=records_end,
                records=records,
            )
        )
    return tuple(tables)


def parse_reputation_table(
    data: bytes,
    *,
    minimum: int = DEFAULT_MIN_REPUTATION,
    maximum: int = DEFAULT_MAX_REPUTATION,
) -> ReputationTable:
    """Return the only structurally identified reputation table."""
    tables = find_reputation_tables(
        data,
        minimum=minimum,
        maximum=maximum,
    )
    if len(tables) != 1:
        raise ReputationResearchError(
            "expected exactly one structurally identified reputation table, "
            f"found {len(tables)}"
        )
    return tables[0]


def bruno_reputation_record(data: bytes) -> ReputationRecord:
    """Return Bruno's structurally identified reputation record."""
    return parse_reputation_table(data).get(BRUNO_ARCHETYPE_ID)


def read_bruno_reputation(path: Path) -> ReputationRecord:
    """Read Bruno's reputation record from one character save."""
    if not path.is_file():
        raise FileNotFoundError(path)
    return bruno_reputation_record(path.read_bytes())


def read_reputation_table(path: Path) -> ReputationTable:
    """Read the structurally identified reputation table."""
    if not path.is_file():
        raise FileNotFoundError(path)
    return parse_reputation_table(path.read_bytes())


def reputation_edit_plan(
    data: bytes,
    changes: Sequence[ReputationChange],
) -> ReputationEditPlan:
    """Create a source-bound plan from validated reputation changes."""
    table = parse_reputation_table(data)
    planned = tuple(changes)
    if not planned:
        raise ReputationResearchError(
            "at least one reputation change is required"
        )
    seen_ids: set[int] = set()
    for change in planned:
        if change.archetype_id in seen_ids:
            raise ReputationResearchError(
                "duplicate reputation change for archetype "
                f"{change.archetype_id}"
            )
        seen_ids.add(change.archetype_id)
        record = table.get(change.archetype_id)
        if record.value != change.expected_value:
            raise ReputationResearchError(
                f"expected {reputation_display_name(change.archetype_id)} "
                f"reputation {change.expected_value}, found {record.value}"
            )
        if not (
            MIN_WRITABLE_REPUTATION
            <= change.new_value
            <= MAX_WRITABLE_REPUTATION
        ):
            raise ValueError(
                "Reputation values must be between "
                f"{MIN_WRITABLE_REPUTATION} and {MAX_WRITABLE_REPUTATION}"
            )
        if change.new_value == change.expected_value:
            raise ReputationResearchError(
                "reputation edit plan contains a no-op change for "
                f"{reputation_display_name(change.archetype_id)}"
            )
    return ReputationEditPlan(
        source_sha256=hashlib.sha256(data).hexdigest(),
        changes=planned,
    )


def apply_reputation_edit_plan(
    data: bytes,
    plan: ReputationEditPlan,
) -> bytes:
    """Apply a source-bound multi-record reputation edit plan."""
    source_sha256 = hashlib.sha256(data).hexdigest()
    if source_sha256 != plan.source_sha256:
        raise ReputationResearchError(
            "character source changed after reputation edits were staged"
        )
    if not plan.changes:
        raise ReputationResearchError(
            "at least one reputation change is required"
        )

    table = parse_reputation_table(data)
    seen_ids: set[int] = set()
    changed_offsets: list[int] = []
    updated = bytearray(data)
    for change in plan.changes:
        if change.archetype_id in seen_ids:
            raise ReputationResearchError(
                "duplicate reputation change for archetype "
                f"{change.archetype_id}"
            )
        seen_ids.add(change.archetype_id)
        if not (
            MIN_WRITABLE_REPUTATION
            <= change.new_value
            <= MAX_WRITABLE_REPUTATION
        ):
            raise ValueError(
                "Reputation values must be between "
                f"{MIN_WRITABLE_REPUTATION} and {MAX_WRITABLE_REPUTATION}"
            )
        record = table.get(change.archetype_id)
        if record.value != change.expected_value:
            raise ReputationResearchError(
                f"expected {reputation_display_name(change.archetype_id)} "
                f"reputation {change.expected_value}, found {record.value}"
            )
        if change.new_value == record.value:
            raise ReputationResearchError(
                "reputation edit plan contains a no-op change for "
                f"{reputation_display_name(change.archetype_id)}"
            )
        struct.pack_into("<i", updated, record.value_offset, change.new_value)
        changed_offsets.append(record.value_offset)

    updated_bytes = bytes(updated)
    if len(updated_bytes) != len(data):
        raise ReputationResearchError(
            "reputation update changed the character file size"
        )
    allowed_offsets = {
        offset + byte_index
        for offset in changed_offsets
        for byte_index in range(4)
    }
    if any(
        old != new and offset not in allowed_offsets
        for offset, (old, new) in enumerate(zip(data, updated_bytes))
    ):
        raise ReputationResearchError(
            "reputation update changed unrelated bytes"
        )

    verified = parse_reputation_table(updated_bytes)
    if (
        verified.count_offset != table.count_offset
        or verified.records_start != table.records_start
        or verified.records_end != table.records_end
    ):
        raise ReputationResearchError(
            "reputation table moved during update verification"
        )
    for change in plan.changes:
        if verified.get(change.archetype_id).value != change.new_value:
            raise ReputationResearchError(
                "reputation update verification failed for "
                f"{reputation_display_name(change.archetype_id)}"
            )
    return updated_bytes


def write_reputation_edit_plan(
    path: Path,
    plan: ReputationEditPlan,
    *,
    now: Optional[datetime] = None,
    backup_dir: Optional[Path] = None,
) -> Path:
    """Back up and atomically apply staged reputation changes."""
    from . import editor

    original = path.read_bytes()
    updated = apply_reputation_edit_plan(original, plan)
    backup_path = editor._write_updated_character(
        path,
        original,
        updated,
        now=now,
        backup_dir=backup_dir,
    )
    verified = read_reputation_table(path)
    for change in plan.changes:
        if verified.get(change.archetype_id).value != change.new_value:
            raise ReputationResearchError(
                "reputation file verification failed after replacement"
            )
    return backup_path


def apply_bruno_reputation(
    data: bytes,
    new_value: int,
    *,
    expected_old_value: int,
    expected_source_sha256: Optional[str] = None,
) -> tuple[bytes, ReputationRecord]:
    """Return bytes with only Bruno's structurally located value changed."""
    source_sha256 = hashlib.sha256(data).hexdigest()
    if expected_source_sha256 is not None and (
        source_sha256 != expected_source_sha256
    ):
        raise ReputationResearchError(
            "character source changed after the reputation edit was staged"
        )
    before = bruno_reputation_record(data)
    try:
        plan = reputation_edit_plan(
            data,
            (
                ReputationChange(
                    archetype_id=BRUNO_ARCHETYPE_ID,
                    expected_value=expected_old_value,
                    new_value=new_value,
                ),
            ),
        )
        return apply_reputation_edit_plan(data, plan), before
    except ReputationResearchError as exc:
        message = str(exc).replace(
            "expected Bruno reputation",
            "expected Bruno trust",
        )
        if message == str(exc):
            raise
        raise ReputationResearchError(message) from exc


def set_bruno_reputation(
    path: Path,
    new_value: int,
    *,
    expected_old_value: int,
    expected_source_sha256: Optional[str] = None,
    now: Optional[datetime] = None,
    backup_dir: Optional[Path] = None,
) -> tuple[ReputationRecord, Path]:
    """Back up and atomically change Bruno's structurally located value."""
    original = path.read_bytes()
    before = bruno_reputation_record(original)
    if expected_source_sha256 is not None and (
        hashlib.sha256(original).hexdigest() != expected_source_sha256
    ):
        raise ReputationResearchError(
            "character source changed after the reputation edit was staged"
        )
    plan = reputation_edit_plan(
        original,
        (
            ReputationChange(
                archetype_id=BRUNO_ARCHETYPE_ID,
                expected_value=expected_old_value,
                new_value=new_value,
            ),
        ),
    )
    backup_path = write_reputation_edit_plan(
        path,
        plan,
        now=now,
        backup_dir=backup_dir,
    )
    return before, backup_path


def _read_7bit_int(data: bytes, offset: int) -> Optional[tuple[int, int]]:
    value = 0
    shift = 0
    for size in range(1, 6):
        if offset + size > len(data):
            return None
        byte = data[offset + size - 1]
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, size
        shift += 7
    return None


def _ascii_text(raw: bytes) -> Optional[str]:
    if any(byte < 0x20 or byte > 0x7E for byte in raw):
        return None
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        return None


def _parse_named_int_entry(
    data: bytes,
    offset: int,
    *,
    max_key_length: int,
) -> Optional[tuple[NamedIntEntry, int]]:
    read = _read_7bit_int(data, offset)
    if read is None:
        return None
    key_length, length_size = read
    if not 1 <= key_length <= max_key_length:
        return None
    key_offset = offset + length_size
    key_end = key_offset + key_length
    value_offset = key_end
    next_offset = value_offset + 4
    if next_offset > len(data):
        return None
    key = _ascii_text(data[key_offset:key_end])
    if key is None:
        return None
    value = int.from_bytes(
        data[value_offset:next_offset],
        "little",
        signed=True,
    )
    return (
        NamedIntEntry(
            entry_offset=offset,
            key_offset=key_offset,
            value_offset=value_offset,
            key=key,
            value=value,
        ),
        next_offset,
    )


def find_named_int_runs(
    data: bytes,
    *,
    min_entries: int = 8,
    max_key_length: int = 80,
) -> tuple[NamedIntRun, ...]:
    """Find contiguous length-prefixed ASCII string + int32 runs."""
    runs: list[NamedIntRun] = []
    offset = 0
    while offset < len(data):
        start = offset
        current = offset
        entries: list[NamedIntEntry] = []
        while True:
            parsed = _parse_named_int_entry(
                data,
                current,
                max_key_length=max_key_length,
            )
            if parsed is None:
                break
            entry, current = parsed
            entries.append(entry)
        if len(entries) >= min_entries:
            runs.append(
                NamedIntRun(
                    start=start,
                    end=current,
                    entries=tuple(entries),
                )
            )
            offset = current
        else:
            offset = start + 1
    return tuple(runs)


def bruno_named_state(
    data: bytes,
    *,
    min_run_entries: int = 8,
) -> tuple[NamedIntEntry, ...]:
    """Return read-only named integer entries whose keys mention Bruno."""
    matches: list[NamedIntEntry] = []
    for run in find_named_int_runs(data, min_entries=min_run_entries):
        matches.extend(
            entry
            for entry in run.entries
            if BRUNO_KEYWORD in entry.key.casefold()
        )
    return tuple(matches)


def _entries_by_key(
    entries: Sequence[NamedIntEntry],
) -> dict[str, NamedIntEntry]:
    by_key: dict[str, NamedIntEntry] = {}
    for entry in entries:
        by_key[entry.key] = entry
    return by_key


def bruno_named_state_changes(
    before_data: bytes,
    after_data: bytes,
    *,
    min_run_entries: int = 8,
) -> tuple[NamedIntChange, ...]:
    """Compare Bruno-named integer state without treating it as reputation."""
    before = _entries_by_key(
        bruno_named_state(before_data, min_run_entries=min_run_entries)
    )
    after = _entries_by_key(
        bruno_named_state(after_data, min_run_entries=min_run_entries)
    )
    changes: list[NamedIntChange] = []
    for key in sorted(set(before) | set(after)):
        before_value = before[key].value if key in before else None
        after_value = after[key].value if key in after else None
        if before_value != after_value:
            changes.append(NamedIntChange(key, before_value, after_value))
    return tuple(changes)


def _overlaps(offset: int, size: int, ranges: Sequence[tuple[int, int]]) -> bool:
    end = offset + size
    return any(
        offset < range_end and range_start < end
        for range_start, range_end in ranges
    )


def find_integer_change_candidates(
    before_data: bytes,
    after_data: bytes,
    *,
    expected_delta: Optional[int] = None,
    minimum: int = DEFAULT_MIN_REPUTATION,
    maximum: int = DEFAULT_MAX_REPUTATION,
    exclude_ranges: Sequence[tuple[int, int]] = (),
) -> tuple[IntegerCandidate, ...]:
    """Find small int32 changes that could be a controlled reputation delta."""
    limit = min(len(before_data), len(after_data)) - 3
    candidates: list[IntegerCandidate] = []
    for offset in range(max(limit, 0)):
        if _overlaps(offset, 4, exclude_ranges):
            continue
        before_raw = before_data[offset:offset + 4]
        after_raw = after_data[offset:offset + 4]
        if before_raw == after_raw:
            continue
        before_value = int.from_bytes(before_raw, "little", signed=True)
        after_value = int.from_bytes(after_raw, "little", signed=True)
        if not minimum <= before_value <= maximum:
            continue
        if not minimum <= after_value <= maximum:
            continue
        if (
            expected_delta is not None
            and after_value - before_value != expected_delta
        ):
            continue
        candidates.append(
            IntegerCandidate(
                offset=offset,
                before=before_value,
                after=after_value,
            )
        )
    return tuple(candidates)


def find_serialized_strings(
    data: bytes,
    *,
    contains: str = BRUNO_KEYWORD,
    max_length: int = 80,
) -> tuple[SerializedStringHit, ...]:
    """Find length-prefixed ASCII strings containing a case-insensitive term."""
    term = contains.casefold()
    hits: list[SerializedStringHit] = []
    for offset in range(len(data)):
        read = _read_7bit_int(data, offset)
        if read is None:
            continue
        length, length_size = read
        if not 1 <= length <= max_length:
            continue
        start = offset + length_size
        end = start + length
        if end > len(data):
            continue
        text = _ascii_text(data[start:end])
        if text is not None and term in text.casefold():
            hits.append(SerializedStringHit(offset=offset, text=text))
    return tuple(hits)


def compare_bruno_reputation_data(
    before_data: bytes,
    after_data: bytes,
    *,
    expected_delta: Optional[int] = None,
    minimum: int = DEFAULT_MIN_REPUTATION,
    maximum: int = DEFAULT_MAX_REPUTATION,
    exclude_ranges: Sequence[tuple[int, int]] = (),
    min_run_entries: int = 8,
) -> BrunoReputationComparison:
    """Compare two captures for a controlled Bruno reputation action."""
    before_state = bruno_named_state(
        before_data,
        min_run_entries=min_run_entries,
    )
    after_state = bruno_named_state(
        after_data,
        min_run_entries=min_run_entries,
    )
    return BrunoReputationComparison(
        before_sha256=hashlib.sha256(before_data).hexdigest(),
        after_sha256=hashlib.sha256(after_data).hexdigest(),
        before_named_state=before_state,
        after_named_state=after_state,
        named_state_changes=bruno_named_state_changes(
            before_data,
            after_data,
            min_run_entries=min_run_entries,
        ),
        integer_candidates=find_integer_change_candidates(
            before_data,
            after_data,
            expected_delta=expected_delta,
            minimum=minimum,
            maximum=maximum,
            exclude_ranges=exclude_ranges,
        ),
        before_bruno_strings=find_serialized_strings(before_data),
        after_bruno_strings=find_serialized_strings(after_data),
    )


def compare_bruno_reputation_files(
    before_path: Path,
    after_path: Path,
    *,
    expected_delta: Optional[int] = None,
    minimum: int = DEFAULT_MIN_REPUTATION,
    maximum: int = DEFAULT_MAX_REPUTATION,
    exclude_ranges: Sequence[tuple[int, int]] = (),
) -> BrunoReputationComparison:
    """Read and compare two character files for Bruno reputation research."""
    if not before_path.is_file():
        raise FileNotFoundError(before_path)
    if not after_path.is_file():
        raise FileNotFoundError(after_path)
    return compare_bruno_reputation_data(
        before_path.read_bytes(),
        after_path.read_bytes(),
        expected_delta=expected_delta,
        minimum=minimum,
        maximum=maximum,
        exclude_ranges=exclude_ranges,
    )


def intersect_integer_candidates(
    comparisons: Sequence[BrunoReputationComparison],
) -> tuple[IntegerCandidateSeries, ...]:
    """Return candidate offsets that form a continuous value series."""
    if not comparisons:
        raise ReputationResearchError(
            "at least one reputation comparison is required"
        )

    candidates_by_transition = [
        {candidate.offset: candidate for candidate in comparison.integer_candidates}
        for comparison in comparisons
    ]
    common_offsets = set(candidates_by_transition[0])
    for candidates in candidates_by_transition[1:]:
        common_offsets.intersection_update(candidates)

    series: list[IntegerCandidateSeries] = []
    for offset in sorted(common_offsets):
        candidates = [
            candidates[offset] for candidates in candidates_by_transition
        ]
        if any(
            before.after != after.before
            for before, after in zip(candidates, candidates[1:])
        ):
            continue
        series.append(
            IntegerCandidateSeries(
                offset=offset,
                values=(
                    candidates[0].before,
                    *(candidate.after for candidate in candidates),
                ),
            )
        )
    return tuple(series)


def compare_bruno_reputation_sequence_data(
    captures: Sequence[bytes],
    expected_values: Sequence[int],
    *,
    minimum: int = DEFAULT_MIN_REPUTATION,
    maximum: int = DEFAULT_MAX_REPUTATION,
    transition_exclude_ranges: Sequence[
        Sequence[tuple[int, int]]
    ] = (),
    min_run_entries: int = 8,
) -> BrunoReputationSequenceComparison:
    """Compare sequential captures against visible Bruno reputation values."""
    capture_values = tuple(expected_values)
    if len(captures) != len(capture_values):
        raise ReputationResearchError(
            "capture and expected-value counts must match"
        )
    if len(captures) < 2:
        raise ReputationResearchError(
            "at least two reputation captures are required"
        )
    if any(
        not minimum <= value <= maximum for value in capture_values
    ):
        raise ReputationResearchError(
            "expected reputation values must be within "
            f"{minimum} through {maximum}"
        )
    if any(
        before == after
        for before, after in zip(capture_values, capture_values[1:])
    ):
        raise ReputationResearchError(
            "adjacent expected reputation values must differ"
        )

    if transition_exclude_ranges:
        if len(transition_exclude_ranges) != len(captures) - 1:
            raise ReputationResearchError(
                "exclude-range count must match the number of transitions"
            )
        exclude_ranges = transition_exclude_ranges
    else:
        exclude_ranges = ((),) * (len(captures) - 1)

    transitions = tuple(
        compare_bruno_reputation_data(
            before_data,
            after_data,
            expected_delta=after_value - before_value,
            minimum=minimum,
            maximum=maximum,
            exclude_ranges=ranges,
            min_run_entries=min_run_entries,
        )
        for before_data, after_data, before_value, after_value, ranges in zip(
            captures,
            captures[1:],
            capture_values,
            capture_values[1:],
            exclude_ranges,
        )
    )
    shared_candidates = intersect_integer_candidates(transitions)
    matching_candidates = tuple(
        candidate
        for candidate in shared_candidates
        if candidate.values == capture_values
    )
    return BrunoReputationSequenceComparison(
        expected_values=capture_values,
        transitions=transitions,
        shared_integer_candidates=shared_candidates,
        matching_integer_candidates=matching_candidates,
    )


def unique_integer_candidate(
    comparison: BrunoReputationComparison,
) -> IntegerCandidate:
    """Return the only candidate, or explain why no write target is known."""
    if len(comparison.integer_candidates) != 1:
        raise ReputationResearchError(
            "expected exactly one Bruno reputation candidate, found "
            f"{len(comparison.integer_candidates)}"
        )
    return comparison.integer_candidates[0]


def unique_sequence_candidate(
    comparison: BrunoReputationSequenceComparison,
) -> IntegerCandidateSeries:
    """Return the only candidate matching every displayed reputation value."""
    if len(comparison.matching_integer_candidates) != 1:
        raise ReputationResearchError(
            "expected exactly one Bruno reputation sequence candidate, found "
            f"{len(comparison.matching_integer_candidates)}"
        )
    return comparison.matching_integer_candidates[0]
