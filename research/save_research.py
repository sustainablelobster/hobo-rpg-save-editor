#!/usr/bin/env python3
"""Read-only helpers for comparing Hobo: Tough Life save generations."""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from hobo_rpg_save_editor import editor
from hobo_rpg_save_editor import npc
from hobo_rpg_save_editor import quests
from hobo_rpg_save_editor import reputation
from hobo_rpg_save_editor.inventory import (
    ITEM_CATEGORY_KEYS,
    CatalogError,
    EquipmentSlot,
    ItemCatalog,
    InventoryItem,
    InventoryFormatError,
    SavedBagSlot,
    load_item_catalog,
    parse_equipment,
    parse_inventory,
    read_inventory,
)


CHARACTER_GENERATION_SUFFIXES = ("_ls", "_lws", "_b1", "_b2")


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class DifferenceSpan:
    start: int
    end: int
    before: bytes
    after: bytes

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class FileComparison:
    before: FileSnapshot
    after: FileSnapshot
    differing_bytes: int
    spans: tuple[DifferenceSpan, ...]


@dataclass(frozen=True)
class RegionItemSummary:
    offset: int
    item_id: int
    name: str
    category: str
    serialize_type: int
    quantity: int
    raw: bytes


@dataclass(frozen=True)
class RegionSlotSummary:
    name: str
    offset: int
    item: Optional[RegionItemSummary]
    raw: bytes


@dataclass(frozen=True)
class CharacterRegionSnapshot:
    file: FileSnapshot
    equipment_start: int
    equipment_end: int
    saved_bags_count_offset: int
    inventory_count_offset: int
    inventory_start: int
    inventory_end: int
    saved_bag_count: int
    inventory_count: int
    slots: tuple[RegionSlotSummary, ...]
    saved_bag_slots: tuple[RegionSlotSummary, ...]
    saved_bags: tuple[RegionItemSummary, ...]
    items: tuple[RegionItemSummary, ...]
    data: bytes

    @property
    def combined_start(self) -> int:
        return self.equipment_start

    @property
    def combined_end(self) -> int:
        return self.inventory_end

    @property
    def equipment_bytes(self) -> bytes:
        return self.data[self.equipment_start:self.equipment_end]

    @property
    def inventory_bytes(self) -> bytes:
        return self.data[self.inventory_start:self.inventory_end]

    @property
    def combined_bytes(self) -> bytes:
        return self.data[self.combined_start:self.combined_end]


@dataclass(frozen=True)
class RegionComparison:
    before: CharacterRegionSnapshot
    after: CharacterRegionSnapshot
    file_comparison: FileComparison
    combined_offsets_match: bool
    combined_bytes_match: bool
    equipment_bytes_match: bool
    inventory_bytes_match: bool
    outside_spans: tuple[DifferenceSpan, ...]
    changed_slots: tuple[
        tuple[RegionSlotSummary, RegionSlotSummary],
        ...,
    ]
    changed_saved_bag_slots: tuple[
        tuple[RegionSlotSummary, RegionSlotSummary],
        ...,
    ]
    removed_saved_bags: tuple[RegionItemSummary, ...]
    added_saved_bags: tuple[RegionItemSummary, ...]
    removed_items: tuple[RegionItemSummary, ...]
    added_items: tuple[RegionItemSummary, ...]

    @property
    def outside_differing_bytes(self) -> int:
        return sum(span.length for span in self.outside_spans)


def _snapshot(path: Path, data: bytes) -> FileSnapshot:
    return FileSnapshot(
        path=path,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def compare_bytes(before: bytes, after: bytes) -> tuple[DifferenceSpan, ...]:
    """Return contiguous offset ranges whose bytes differ."""
    spans: list[DifferenceSpan] = []
    limit = max(len(before), len(after))
    offset = 0
    while offset < limit:
        before_byte = before[offset] if offset < len(before) else None
        after_byte = after[offset] if offset < len(after) else None
        if before_byte == after_byte:
            offset += 1
            continue

        start = offset
        offset += 1
        while offset < limit:
            before_byte = before[offset] if offset < len(before) else None
            after_byte = after[offset] if offset < len(after) else None
            if before_byte == after_byte:
                break
            offset += 1
        spans.append(
            DifferenceSpan(
                start=start,
                end=offset,
                before=before[start:min(offset, len(before))],
                after=after[start:min(offset, len(after))],
            )
        )
    return tuple(spans)


def compare_files(before_path: Path, after_path: Path) -> FileComparison:
    """Compare two files without changing either file."""
    if not before_path.is_file():
        raise FileNotFoundError(before_path)
    if not after_path.is_file():
        raise FileNotFoundError(after_path)
    before_data = before_path.read_bytes()
    after_data = after_path.read_bytes()
    return _compare_file_data(before_path, before_data, after_path, after_data)


def _compare_file_data(
    before_path: Path,
    before_data: bytes,
    after_path: Path,
    after_data: bytes,
) -> FileComparison:
    spans = compare_bytes(before_data, after_data)
    return FileComparison(
        before=_snapshot(before_path, before_data),
        after=_snapshot(after_path, after_data),
        differing_bytes=sum(span.length for span in spans),
        spans=spans,
    )


def _region_item_summary(item: InventoryItem) -> RegionItemSummary:
    return RegionItemSummary(
        offset=item.offset,
        item_id=item.definition.item_id,
        name=item.definition.name,
        category=item.definition.category,
        serialize_type=item.serialize_type,
        quantity=item.quantity,
        raw=item.raw,
    )


def _region_slot_summary(slot: EquipmentSlot) -> RegionSlotSummary:
    return RegionSlotSummary(
        name=slot.name,
        offset=slot.offset,
        item=_region_item_summary(slot.item) if slot.item else None,
        raw=slot.raw,
    )


def _region_saved_bag_slot_summary(
    position: int,
    slot: SavedBagSlot,
) -> RegionSlotSummary:
    return RegionSlotSummary(
        name=f"bag {position + 1}",
        offset=slot.offset,
        item=_region_item_summary(slot.item) if slot.item else None,
        raw=slot.raw,
    )


def parse_character_regions(
    character_path: Path,
    catalog: ItemCatalog,
) -> CharacterRegionSnapshot:
    """Parse equipment and carried inventory regions from one character file."""
    if not character_path.is_file():
        raise FileNotFoundError(character_path)
    data = character_path.read_bytes()
    inventory = parse_inventory(data, catalog)
    equipment = parse_equipment(data, catalog, inventory)
    return CharacterRegionSnapshot(
        file=_snapshot(character_path, data),
        equipment_start=equipment.start,
        equipment_end=equipment.end,
        saved_bags_count_offset=inventory.bags_count_offset,
        inventory_count_offset=inventory.inventory_count_offset,
        inventory_start=inventory.inventory_start,
        inventory_end=inventory.inventory_end,
        saved_bag_count=len(inventory.bag_slots),
        inventory_count=len(inventory.items),
        slots=tuple(_region_slot_summary(slot) for slot in equipment.slots),
        saved_bag_slots=tuple(
            _region_saved_bag_slot_summary(position, slot)
            for position, slot in enumerate(inventory.bag_slots)
        ),
        saved_bags=tuple(
            _region_item_summary(item) for item in inventory.bags
        ),
        items=tuple(_region_item_summary(item) for item in inventory.items),
        data=data,
    )


def _spans_outside_range(
    before_data: bytes,
    after_data: bytes,
    spans: Sequence[DifferenceSpan],
    start: int,
    end: int,
) -> tuple[DifferenceSpan, ...]:
    outside: list[DifferenceSpan] = []
    for span in spans:
        ranges = (
            (span.start, min(span.end, start)),
            (max(span.start, end), span.end),
        )
        for part_start, part_end in ranges:
            if part_start >= part_end:
                continue
            outside.append(
                DifferenceSpan(
                    start=part_start,
                    end=part_end,
                    before=before_data[
                        part_start:min(part_end, len(before_data))
                    ],
                    after=after_data[
                        part_start:min(part_end, len(after_data))
                    ],
                )
            )
    return tuple(outside)


def _raw_item_delta(
    before: Sequence[RegionItemSummary],
    after: Sequence[RegionItemSummary],
) -> tuple[tuple[RegionItemSummary, ...], tuple[RegionItemSummary, ...]]:
    remaining_after = list(after)
    removed: list[RegionItemSummary] = []
    for before_item in before:
        match_index = next(
            (
                index
                for index, after_item in enumerate(remaining_after)
                if after_item.raw == before_item.raw
            ),
            None,
        )
        if match_index is None:
            removed.append(before_item)
        else:
            del remaining_after[match_index]
    return tuple(removed), tuple(remaining_after)


def compare_character_regions(
    before_path: Path,
    after_path: Path,
    catalog: ItemCatalog,
) -> RegionComparison:
    """Compare parsed equipment and inventory regions between two files."""
    before = parse_character_regions(before_path, catalog)
    after = parse_character_regions(after_path, catalog)
    file_comparison = _compare_file_data(
        before_path,
        before.data,
        after_path,
        after.data,
    )
    offsets_match = (
        before.combined_start == after.combined_start
        and before.combined_end == after.combined_end
    )
    outside_spans = (
        _spans_outside_range(
            before.data,
            after.data,
            file_comparison.spans,
            before.combined_start,
            before.combined_end,
        )
        if offsets_match
        else ()
    )
    changed_slots = tuple(
        (before_slot, after_slot)
        for before_slot, after_slot in zip(before.slots, after.slots)
        if before_slot.raw != after_slot.raw
    )
    changed_bag_slots = tuple(
        (before_slot, after_slot)
        for before_slot, after_slot in zip(
            before.saved_bag_slots,
            after.saved_bag_slots,
        )
        if before_slot.raw != after_slot.raw
    )
    removed_bags, added_bags = _raw_item_delta(
        before.saved_bags,
        after.saved_bags,
    )
    removed, added = _raw_item_delta(before.items, after.items)
    return RegionComparison(
        before=before,
        after=after,
        file_comparison=file_comparison,
        combined_offsets_match=offsets_match,
        combined_bytes_match=before.combined_bytes == after.combined_bytes,
        equipment_bytes_match=before.equipment_bytes == after.equipment_bytes,
        inventory_bytes_match=before.inventory_bytes == after.inventory_bytes,
        outside_spans=outside_spans,
        changed_slots=changed_slots,
        changed_saved_bag_slots=changed_bag_slots,
        removed_saved_bags=removed_bags,
        added_saved_bags=added_bags,
        removed_items=removed,
        added_items=added,
    )


def discover_character_generations(
    character_path: Path,
) -> tuple[Path, ...]:
    """Find existing generations for one character UUID."""
    name = character_path.name
    base_name = next(
        (
            name[: -len(suffix)]
            for suffix in CHARACTER_GENERATION_SUFFIXES
            if name.endswith(suffix)
        ),
        None,
    )
    if base_name is None:
        raise ValueError(
            "Character filename must end with _ls, _lws, _b1, or _b2"
        )
    return tuple(
        candidate
        for suffix in CHARACTER_GENERATION_SUFFIXES
        if (candidate := character_path.with_name(base_name + suffix)).is_file()
    )


def compare_character_generations(
    character_path: Path,
) -> tuple[FileComparison, ...]:
    """Compare each available older generation with the active _ls file."""
    generations = discover_character_generations(character_path)
    active = next(
        (path for path in generations if path.name.endswith("_ls")),
        None,
    )
    if active is None:
        raise FileNotFoundError("Active _ls character file was not found")
    return tuple(
        compare_files(generation, active)
        for generation in generations
        if generation != active
    )


def _selected_record(game_dir: Path, slot_name: str) -> editor.SaveRecord:
    records, warnings = editor.scan_saves(game_dir)
    matches = [
        record
        for record in records
        if record.slot_name.casefold() == slot_name.casefold()
    ]
    if len(matches) != 1:
        details = f"; scan warnings: {'; '.join(warnings)}" if warnings else ""
        raise ValueError(
            f"Expected exactly one save named {slot_name}, "
            f"found {len(matches)}{details}"
        )
    return matches[0]


def _short_hex(data: bytes, limit: int = 16) -> str:
    displayed = data[:limit].hex(" ")
    return displayed + (" ..." if len(data) > limit else "")


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _item_summary(item: RegionItemSummary) -> str:
    return (
        f"{item.name} ID {item.item_id} | quantity {item.quantity} | "
        f"type {item.serialize_type} | offset 0x{item.offset:X}"
    )


def _slot_item_label(slot: RegionSlotSummary) -> str:
    if slot.item is None:
        return f"empty at 0x{slot.offset:X}"
    return _item_summary(slot.item)


def _generation_path(record: editor.SaveRecord, suffix: str) -> Path:
    if suffix not in CHARACTER_GENERATION_SUFFIXES:
        raise ValueError(
            "Generation must be one of "
            + ", ".join(CHARACTER_GENERATION_SUFFIXES)
        )
    return record.character_path.with_name(record.save_id + suffix)


def _catalog_command(game_dir: Path) -> int:
    catalog = load_item_catalog(game_dir)
    print(f"Bundle: {catalog.bundle_path}")
    print(f"Unity version: {catalog.unity_version or 'unknown'}")
    print(f"Max item ID: {catalog.max_item_id}")
    print(f"Item definitions: {len(catalog.items)}")
    counts = catalog.category_counts()
    for category in ITEM_CATEGORY_KEYS:
        print(f"  {category}: {counts[category]}")
    fallback_count = sum(
        item.name == f"Item {item.item_id}" for item in catalog.items
    )
    print(f"Numeric fallback names: {fallback_count}")
    return 0


def _compare_command(game_dir: Path, slot_name: str, max_spans: int) -> int:
    record = _selected_record(game_dir, slot_name)
    print(
        f"Slot: {record.slot_name} | {record.display_name} | "
        f"{record.save_id}"
    )
    comparisons = compare_character_generations(record.character_path)
    if not comparisons:
        print("No additional character generations were found.")
        return 0

    for comparison in comparisons:
        print()
        print(
            f"{comparison.before.path.name} -> "
            f"{comparison.after.path.name}"
        )
        print(
            f"  sizes: {comparison.before.size} -> "
            f"{comparison.after.size}"
        )
        print(f"  before sha256: {comparison.before.sha256}")
        print(f"  after sha256:  {comparison.after.sha256}")
        print(f"  differing bytes: {comparison.differing_bytes}")
        print(f"  difference spans: {len(comparison.spans)}")
        for span in comparison.spans[:max_spans]:
            print(
                f"    [0x{span.start:X}, 0x{span.end:X}) "
                f"length {span.length}"
            )
            print(f"      before: {_short_hex(span.before)}")
            print(f"      after:  {_short_hex(span.after)}")
        omitted = len(comparison.spans) - max_spans
        if omitted > 0:
            print(f"    ... {omitted} additional spans omitted")
    return 0


def _region_diff_paths(
    game_dir: Path,
    slot_name: Optional[str],
    before_path: Optional[Path],
    after_path: Optional[Path],
    before_generation: str,
    after_generation: str,
) -> tuple[Path, Path]:
    if before_path is not None or after_path is not None:
        if before_path is None or after_path is None:
            raise ValueError("--before and --after must be used together")
        return (
            before_path.expanduser().absolute(),
            after_path.expanduser().absolute(),
        )
    if slot_name is None:
        raise ValueError("region-diff requires --slot or --before and --after")
    record = _selected_record(game_dir, slot_name)
    return (
        _generation_path(record, before_generation),
        _generation_path(record, after_generation),
    )


def _region_diff_command(
    game_dir: Path,
    slot_name: Optional[str],
    before_path: Optional[Path],
    after_path: Optional[Path],
    before_generation: str,
    after_generation: str,
    max_spans: int,
) -> int:
    before_path, after_path = _region_diff_paths(
        game_dir,
        slot_name,
        before_path,
        after_path,
        before_generation,
        after_generation,
    )
    catalog = load_item_catalog(game_dir)
    comparison = compare_character_regions(before_path, after_path, catalog)
    before = comparison.before
    after = comparison.after

    print(f"{before.file.path.name} -> {after.file.path.name}")
    print(f"  sizes: {before.file.size} -> {after.file.size}")
    print(f"  before sha256: {before.file.sha256}")
    print(f"  after sha256:  {after.file.sha256}")
    print(
        f"  file differing bytes: {comparison.file_comparison.differing_bytes}"
    )
    print(f"  file difference spans: {len(comparison.file_comparison.spans)}")
    print()
    print("Equipment + inventory:")
    print(
        f"  before region: [0x{before.combined_start:X}, "
        f"0x{before.combined_end:X})"
    )
    print(
        f"  after region:  [0x{after.combined_start:X}, "
        f"0x{after.combined_end:X})"
    )
    print(
        "  combined offsets match: "
        f"{_yes_no(comparison.combined_offsets_match)}"
    )
    print(
        "  combined bytes match: "
        f"{_yes_no(comparison.combined_bytes_match)}"
    )
    print(
        "  equipment bytes match: "
        f"{_yes_no(comparison.equipment_bytes_match)}"
    )
    print(
        "  inventory bytes match: "
        f"{_yes_no(comparison.inventory_bytes_match)}"
    )
    print(
        f"  saved bag slots: {before.saved_bag_count} -> "
        f"{after.saved_bag_count}"
    )
    print(
        f"  inventory items: {before.inventory_count} -> "
        f"{after.inventory_count}"
    )
    print(
        f"  inventory region: [0x{before.inventory_start:X}, "
        f"0x{before.inventory_end:X}) -> [0x{after.inventory_start:X}, "
        f"0x{after.inventory_end:X})"
    )

    if comparison.combined_offsets_match:
        print()
        print("Outside parsed region:")
        print(f"  differing bytes: {comparison.outside_differing_bytes}")
        print(f"  difference spans: {len(comparison.outside_spans)}")
        for span in comparison.outside_spans[:max_spans]:
            print(
                f"    [0x{span.start:X}, 0x{span.end:X}) "
                f"length {span.length}"
            )
            print(f"      before: {_short_hex(span.before)}")
            print(f"      after:  {_short_hex(span.after)}")
        omitted = len(comparison.outside_spans) - max_spans
        if omitted > 0:
            print(f"    ... {omitted} additional spans omitted")
    else:
        print()
        print("Outside parsed region: unavailable because offsets moved")

    print()
    print("Equipment slot changes:")
    if not comparison.changed_slots:
        print("  (none)")
    for before_slot, after_slot in comparison.changed_slots:
        print(f"  {before_slot.name}:")
        print(f"    before: {_slot_item_label(before_slot)}")
        print(f"    after:  {_slot_item_label(after_slot)}")

    print()
    print("Saved-bag raw-record changes:")
    if comparison.changed_saved_bag_slots:
        print("  slot changes:")
    for before_slot, after_slot in comparison.changed_saved_bag_slots:
        print(f"    {before_slot.name}:")
        print(f"      before: {_slot_item_label(before_slot)}")
        print(f"      after:  {_slot_item_label(after_slot)}")
    if not comparison.removed_saved_bags and not comparison.added_saved_bags:
        if not comparison.changed_saved_bag_slots:
            print("  (none)")
    for item in comparison.removed_saved_bags:
        print(f"  removed: {_item_summary(item)}")
    for item in comparison.added_saved_bags:
        print(f"  added:   {_item_summary(item)}")

    print()
    print("Carried raw-record changes:")
    if not comparison.removed_items and not comparison.added_items:
        print("  (none)")
    for item in comparison.removed_items:
        print(f"  removed: {_item_summary(item)}")
    for item in comparison.added_items:
        print(f"  added:   {_item_summary(item)}")
    return 0


def _inventory_command(
    game_dir: Path,
    slot_name: str,
    show_items: bool,
) -> int:
    record = _selected_record(game_dir, slot_name)
    catalog = load_item_catalog(game_dir)
    snapshot = read_inventory(record.character_path, catalog)
    print(
        f"Slot: {record.slot_name} | {record.display_name} | "
        f"{record.save_id}"
    )
    print(f"Source sha256: {snapshot.source_sha256}")
    print(f"Saved bags count offset: 0x{snapshot.bags_count_offset:X}")
    print(
        "Inventory count offset: "
        f"0x{snapshot.inventory_count_offset:X}"
    )
    print(
        f"Inventory region: [0x{snapshot.inventory_start:X}, "
        f"0x{snapshot.inventory_end:X})"
    )
    print(f"Saved bag slots: {len(snapshot.bag_slots)}")
    print(f"Populated saved bags: {len(snapshot.bags)}")
    print(f"Inventory items: {len(snapshot.items)}")
    for category in ITEM_CATEGORY_KEYS:
        count = sum(
            item.definition.category == category
            for item in snapshot.items
        )
        print(f"  {category}: {count}")
    if show_items:
        print("Items:")
        for item in snapshot.items:
            print(
                f"  0x{item.offset:X} | {item.definition.item_id} | "
                f"{item.definition.name} | quantity {item.quantity} | "
                f"type {item.serialize_type}"
            )
    return 0


def _bruno_reputation_inspect_command(character_path: Path) -> int:
    character_path = character_path.expanduser().absolute()
    table = reputation.parse_reputation_table(character_path.read_bytes())
    bruno = table.get(reputation.BRUNO_ARCHETYPE_ID)

    print(character_path)
    print(
        f"  reputation count: {len(table.records)} "
        f"at 0x{table.count_offset:X}"
    )
    print(
        f"  reputation region: [0x{table.records_start:X}, "
        f"0x{table.records_end:X})"
    )
    print(f"  Bruno archetype ID: {bruno.archetype_id}")
    print(f"  Bruno record offset: 0x{bruno.record_offset:X}")
    print(f"  Bruno value offset: 0x{bruno.value_offset:X}")
    print(f"  Bruno trust: {bruno.value} / 100")
    return 0


def _npc_flags_inspect_command(
    character_path: Path,
    game_dir: Optional[Path],
    query: Optional[str],
    show_all: bool,
) -> int:
    character_path = character_path.expanduser().absolute()
    table = npc.parse_npc_flag_table(character_path.read_bytes())
    annotations: Optional[npc.NpcFlagAnnotationCatalog] = None
    if game_dir is not None:
        flag_keys = tuple(record.key for record in table.records)
        try:
            annotations = npc.load_npc_flag_annotations(
                game_dir.expanduser().absolute(),
                flag_keys,
            )
        except (
            OSError,
            CatalogError,
            npc.NpcAnnotationError,
            ValueError,
        ) as exc:
            annotations = npc.fallback_npc_flag_annotations(
                flag_keys,
                warning=(
                    "Installed English context could not be loaded: "
                    f"{exc}"
                ),
            )
    normalized_query = query.casefold() if query else None
    records = tuple(
        record
        for record in table.records
        if (
            (show_all or normalized_query is not None or record.value == 1)
            and (
                normalized_query is None
                or normalized_query
                in (
                    record.key
                    if annotations is None
                    else (
                        f"{record.key} "
                        f"{' '.join(annotations.get(record.key).associations)} "
                        f"{annotations.get(record.key).meaning}"
                    )
                ).casefold()
            )
        )
    )

    print(character_path)
    print(
        f"  NPC flag count: {len(table.records)} "
        f"at 0x{table.count_offset:X}"
    )
    print(
        f"  NPC flag region: [0x{table.records_start:X}, "
        f"0x{table.records_end:X})"
    )
    print(
        "  enabled flags: "
        f"{sum(record.value for record in table.records)}"
    )
    if query:
        print(f"  filter: {query}")
    if annotations is not None:
        for warning in annotations.warnings:
            print(f"  annotation warning: {warning}")
    print("Flags:")
    if not records:
        print("  (none)")
    for record in records:
        if annotations is None:
            print(
                f"  0x{record.value_offset:X} | "
                f"{record.value} | {record.key}"
            )
        else:
            annotation = annotations.get(record.key)
            print(
                f"  0x{record.value_offset:X} | {record.value} | "
                f"{record.key} | "
                f"{', '.join(annotation.associations)} | "
                f"{annotation.meaning}"
            )
    return 0


def _npc_flags_diff_command(
    before_path: Path,
    after_path: Path,
    query: Optional[str],
) -> int:
    before_path = before_path.expanduser().absolute()
    after_path = after_path.expanduser().absolute()
    if not before_path.is_file():
        raise FileNotFoundError(before_path)
    if not after_path.is_file():
        raise FileNotFoundError(after_path)
    changes = npc.compare_npc_flags(
        before_path.read_bytes(),
        after_path.read_bytes(),
    )
    normalized_query = query.casefold() if query else None
    filtered = tuple(
        change
        for change in changes
        if (
            normalized_query is None
            or normalized_query in change.key.casefold()
        )
    )

    print(f"{before_path.name} -> {after_path.name}")
    if query:
        print(f"  filter: {query}")
    print(f"  changed NPC flags: {len(filtered)}")
    if not filtered:
        print("  (none)")
    for change in filtered:
        print(f"  {change.key}: {change.before} -> {change.after}")
    return 0


def _quest_status_matches_filter(
    state: quests.QuestState,
    status_filter: str,
) -> bool:
    if status_filter == "all":
        return True
    if status_filter == "undiscovered":
        return state.progress.status == quests.QUEST_STATUS_NOT_STARTED
    if status_filter == "in-progress":
        return state.progress.status == quests.QUEST_STATUS_IN_PROGRESS
    if status_filter == "completed":
        return state.progress.status == quests.QUEST_STATUS_COMPLETED
    if status_filter == "no-flags":
        return state.progress.status == quests.QUEST_STATUS_NO_FLAGS
    if status_filter == "conflicting":
        return state.progress.status == quests.QUEST_STATUS_CONFLICTING
    if status_filter == "repeatable":
        return state.definition.is_repeatable
    raise ValueError(f"unknown quest status filter {status_filter!r}")


def _quest_character_path(
    game_dir: Path,
    slot_name: Optional[str],
    character_path: Optional[Path],
) -> Path:
    if (slot_name is None) == (character_path is None):
        raise ValueError("quests requires exactly one of --slot or --character")
    if character_path is not None:
        return character_path.expanduser().absolute()
    assert slot_name is not None
    return _selected_record(game_dir, slot_name).character_path


def _quests_command(
    game_dir: Path,
    slot_name: Optional[str],
    character_path: Optional[Path],
    query: Optional[str],
    status_filter: str,
) -> int:
    game_dir = game_dir.expanduser().absolute()
    selected_character = _quest_character_path(
        game_dir,
        slot_name,
        character_path,
    )
    table = npc.parse_npc_flag_table(selected_character.read_bytes())
    catalog = quests.load_quest_catalog(game_dir)
    states = quests.correlate_quests(catalog, table)
    normalized_query = query.casefold() if query else None
    visible = tuple(
        state
        for state in states
        if (
            _quest_status_matches_filter(state, status_filter)
            and (
                normalized_query is None
                or normalized_query in quests.quest_search_text(state)
            )
        )
    )

    print(f"Game: {game_dir}")
    print(f"Character: {selected_character}")
    print(f"Quest assets: {len(catalog.quests)}")
    print(f"NPC flag count: {len(table.records)}")
    if query:
        print(f"Filter: {query}")
    print(f"Status filter: {status_filter}")
    for warning in catalog.warnings:
        print(f"Quest warning: {warning}")
    print("Quests:")
    if not visible:
        print("  (none)")
    for state in visible:
        definition = state.definition
        progress = state.progress
        print(
            f"  {progress.status} | {progress.flag_summary} | "
            f"{definition.type_label} | {definition.title} | "
            f"{definition.asset_path}"
        )
        if progress.known_flags:
            print(f"    enabled: {', '.join(progress.enabled_flags) or '-'}")
            print(f"    disabled: {', '.join(progress.disabled_flags) or '-'}")
        if progress.missing_flags:
            print(f"    missing: {', '.join(progress.missing_flags)}")
    return 0


def _print_structural_bruno(path: Path, displayed_value: int) -> None:
    try:
        table = reputation.parse_reputation_table(path.read_bytes())
        bruno = table.get(reputation.BRUNO_ARCHETYPE_ID)
    except (OSError, reputation.ReputationResearchError) as exc:
        print(f"  {path.name}: unavailable ({exc})")
        return
    status = "matches" if bruno.value == displayed_value else "MISMATCH"
    print(
        f"  {path.name}: {bruno.value} at 0x{bruno.value_offset:X} "
        f"({status} displayed {displayed_value})"
    )


def _bruno_reputation_command(
    game_dir: Path,
    before_path: Path,
    after_path: Path,
    expected_delta: Optional[int],
    max_candidates: int,
    max_strings: int,
) -> int:
    before_path = before_path.expanduser().absolute()
    after_path = after_path.expanduser().absolute()
    if not before_path.is_file():
        raise FileNotFoundError(before_path)
    if not after_path.is_file():
        raise FileNotFoundError(after_path)

    before_data = before_path.read_bytes()
    after_data = after_path.read_bytes()
    exclude_ranges: tuple[tuple[int, int], ...] = ()
    region_comparison, region_error = _reputation_region_comparisons(
        game_dir,
        ((before_path, after_path),),
    )[0]

    if (
        region_comparison is not None
        and region_comparison.combined_offsets_match
    ):
        exclude_ranges = (
            (
                region_comparison.before.combined_start,
                region_comparison.before.combined_end,
            ),
        )

    comparison = reputation.compare_bruno_reputation_data(
        before_data,
        after_data,
        expected_delta=expected_delta,
        exclude_ranges=exclude_ranges,
    )

    print(f"{before_path.name} -> {after_path.name}")
    print(f"  before sha256: {comparison.before_sha256}")
    print(f"  after sha256:  {comparison.after_sha256}")
    if expected_delta is not None:
        print(f"  expected reputation delta: {expected_delta:+d}")

    print()
    print("Structurally identified ReputationSaveData:")
    for path in (before_path, after_path):
        try:
            table = reputation.parse_reputation_table(path.read_bytes())
            bruno = table.get(reputation.BRUNO_ARCHETYPE_ID)
        except (OSError, reputation.ReputationResearchError) as exc:
            print(f"  {path.name}: unavailable ({exc})")
        else:
            print(
                f"  {path.name}: Bruno {bruno.value} at "
                f"0x{bruno.value_offset:X}; table count "
                f"{len(table.records)} at 0x{table.count_offset:X}"
            )

    print()
    print("Equipment + inventory:")
    if region_comparison is None:
        print(f"  unavailable: {region_error}")
    else:
        print(
            "  combined offsets match: "
            f"{_yes_no(region_comparison.combined_offsets_match)}"
        )
        print(
            "  combined bytes match: "
            f"{_yes_no(region_comparison.combined_bytes_match)}"
        )
        print(
            "  equipment bytes match: "
            f"{_yes_no(region_comparison.equipment_bytes_match)}"
        )
        print(
            "  inventory bytes match: "
            f"{_yes_no(region_comparison.inventory_bytes_match)}"
        )
        if region_comparison.combined_offsets_match:
            print(
                "  outside parsed region differing bytes: "
                f"{region_comparison.outside_differing_bytes}"
            )

    print()
    print("Bruno named-state changes (read-only quest/progression state):")
    if not comparison.named_state_changes:
        print("  (none)")
    for change in comparison.named_state_changes:
        print(f"  {change.key}: {change.before} -> {change.after}")

    print()
    print("Candidate Bruno reputation integer changes:")
    if not comparison.integer_candidates:
        print("  (none)")
    for candidate in comparison.integer_candidates[:max_candidates]:
        print(
            f"  0x{candidate.offset:X}: {candidate.before} -> "
            f"{candidate.after} ({candidate.delta:+d})"
        )
    omitted = len(comparison.integer_candidates) - max_candidates
    if omitted > 0:
        print(f"  ... {omitted} additional candidates omitted")
    try:
        candidate = reputation.unique_integer_candidate(comparison)
    except reputation.ReputationResearchError as exc:
        print(f"  unique write target: no ({exc})")
    else:
        print(f"  unique write target: 0x{candidate.offset:X}")

    print()
    print("Serialized strings containing Bruno:")
    print("  before:")
    for hit in comparison.before_bruno_strings[:max_strings]:
        print(f"    0x{hit.offset:X}: {hit.text}")
    before_omitted = len(comparison.before_bruno_strings) - max_strings
    if before_omitted > 0:
        print(f"    ... {before_omitted} additional strings omitted")
    if not comparison.before_bruno_strings:
        print("    (none)")
    print("  after:")
    for hit in comparison.after_bruno_strings[:max_strings]:
        print(f"    0x{hit.offset:X}: {hit.text}")
    after_omitted = len(comparison.after_bruno_strings) - max_strings
    if after_omitted > 0:
        print(f"    ... {after_omitted} additional strings omitted")
    if not comparison.after_bruno_strings:
        print("    (none)")
    return 0


def _reputation_region_comparisons(
    game_dir: Path,
    path_pairs: Sequence[tuple[Path, Path]],
) -> tuple[tuple[Optional[RegionComparison], Optional[Exception]], ...]:
    try:
        catalog = load_item_catalog(game_dir)
    except (CatalogError, FileNotFoundError, OSError) as exc:
        return tuple((None, exc) for _pair in path_pairs)

    results: list[
        tuple[Optional[RegionComparison], Optional[Exception]]
    ] = []
    for before_path, after_path in path_pairs:
        try:
            comparison = compare_character_regions(
                before_path,
                after_path,
                catalog,
            )
        except (FileNotFoundError, InventoryFormatError, OSError) as exc:
            results.append((None, exc))
        else:
            results.append((comparison, None))
    return tuple(results)


def _region_exclude_ranges(
    comparison: Optional[RegionComparison],
) -> tuple[tuple[int, int], ...]:
    if comparison is None or not comparison.combined_offsets_match:
        return ()
    return (
        (
            comparison.before.combined_start,
            comparison.before.combined_end,
        ),
    )


def _print_reputation_region_summary(
    comparison: Optional[RegionComparison],
    error: Optional[Exception],
) -> None:
    if comparison is None:
        print(f"    unavailable: {error}")
        return
    print(
        "    combined offsets match: "
        f"{_yes_no(comparison.combined_offsets_match)}"
    )
    print(
        "    combined bytes match: "
        f"{_yes_no(comparison.combined_bytes_match)}"
    )
    if comparison.combined_offsets_match:
        print(
            "    outside parsed region differing bytes: "
            f"{comparison.outside_differing_bytes}"
        )


def _bruno_reputation_sequence_command(
    game_dir: Path,
    before_path: Path,
    middle_path: Path,
    after_path: Path,
    before_value: int,
    middle_value: int,
    after_value: int,
    max_candidates: int,
) -> int:
    paths = tuple(
        path.expanduser().absolute()
        for path in (before_path, middle_path, after_path)
    )
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)

    expected_values = (before_value, middle_value, after_value)
    deltas = tuple(
        after - before
        for before, after in zip(expected_values, expected_values[1:])
    )
    if deltas[0] == 0 or deltas[1] == 0:
        raise ValueError("both Bruno reputation transitions must be nonzero")
    if (deltas[0] > 0) == (deltas[1] > 0):
        raise ValueError(
            "Bruno reputation sequence must contain opposing changes"
        )

    path_pairs = tuple(zip(paths, paths[1:]))
    region_results = _reputation_region_comparisons(
        game_dir,
        path_pairs,
    )
    comparison = reputation.compare_bruno_reputation_sequence_data(
        tuple(path.read_bytes() for path in paths),
        expected_values,
        transition_exclude_ranges=tuple(
            _region_exclude_ranges(region_comparison)
            for region_comparison, _error in region_results
        ),
    )

    hashes = (
        comparison.transitions[0].before_sha256,
        *(transition.after_sha256 for transition in comparison.transitions),
    )
    print("Bruno reputation capture sequence:")
    for path, value, sha256 in zip(paths, expected_values, hashes):
        print(
            f"  {path.name}: displayed {value}, sha256 {sha256}"
        )
    print(f"  expected deltas: {deltas[0]:+d}, {deltas[1]:+d}")

    print()
    print("Structurally identified ReputationSaveData:")
    for path, value in zip(paths, expected_values):
        _print_structural_bruno(path, value)

    for index, (transition, region_result) in enumerate(
        zip(comparison.transitions, region_results),
        start=1,
    ):
        region_comparison, region_error = region_result
        print()
        print(
            f"Transition {index}: {paths[index - 1].name} -> "
            f"{paths[index].name}"
        )
        print("  Equipment + inventory:")
        _print_reputation_region_summary(
            region_comparison,
            region_error,
        )
        print("  Bruno named-state changes:")
        if not transition.named_state_changes:
            print("    (none)")
        for change in transition.named_state_changes:
            print(
                f"    {change.key}: {change.before} -> {change.after}"
            )
        print("  Delta-matching integer candidates:")
        if not transition.integer_candidates:
            print("    (none)")
        for candidate in transition.integer_candidates[:max_candidates]:
            print(
                f"    0x{candidate.offset:X}: {candidate.before} -> "
                f"{candidate.after} ({candidate.delta:+d})"
            )
        omitted = len(transition.integer_candidates) - max_candidates
        if omitted > 0:
            print(f"    ... {omitted} additional candidates omitted")

    print()
    print("Shared candidate series:")
    if not comparison.shared_integer_candidates:
        print("  (none)")
    for candidate in comparison.shared_integer_candidates[:max_candidates]:
        values = " -> ".join(str(value) for value in candidate.values)
        print(f"  0x{candidate.offset:X}: {values}")
    omitted = len(comparison.shared_integer_candidates) - max_candidates
    if omitted > 0:
        print(f"  ... {omitted} additional candidates omitted")

    print()
    print("Candidates matching all displayed values:")
    if not comparison.matching_integer_candidates:
        print("  (none)")
    for candidate in comparison.matching_integer_candidates[:max_candidates]:
        values = " -> ".join(str(value) for value in candidate.values)
        print(f"  0x{candidate.offset:X}: {values}")
    try:
        candidate = reputation.unique_sequence_candidate(comparison)
    except reputation.ReputationResearchError as exc:
        print(f"  unique validated target: no ({exc})")
    else:
        print(f"  unique validated target: 0x{candidate.offset:X}")
    return 0


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect the installed item catalog and compare save generations "
            "without modifying them."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    catalog_parser = subparsers.add_parser(
        "catalog",
        help="Validate and summarize the installed item catalog.",
    )
    catalog_parser.add_argument("--game-dir", type=Path, required=True)

    compare_parser = subparsers.add_parser(
        "compare",
        help="Compare one slot's active character file with its generations.",
    )
    compare_parser.add_argument("--game-dir", type=Path, required=True)
    compare_parser.add_argument("--slot", required=True)
    compare_parser.add_argument(
        "--max-spans",
        type=int,
        default=20,
        help="Maximum difference spans to print per comparison.",
    )

    region_parser = subparsers.add_parser(
        "region-diff",
        help=(
            "Compare parsed equipment and inventory regions between two "
            "character files."
        ),
    )
    region_parser.add_argument("--game-dir", type=Path, required=True)
    region_parser.add_argument(
        "--slot",
        help=(
            "Save slot to compare by generation. Defaults to _lws -> _ls "
            "when explicit paths are not provided."
        ),
    )
    region_parser.add_argument(
        "--before",
        type=Path,
        help="Explicit before character file path.",
    )
    region_parser.add_argument(
        "--after",
        type=Path,
        help="Explicit after character file path.",
    )
    region_parser.add_argument(
        "--before-generation",
        default="_lws",
        choices=CHARACTER_GENERATION_SUFFIXES,
        help="Generation used as the before file with --slot.",
    )
    region_parser.add_argument(
        "--after-generation",
        default="_ls",
        choices=CHARACTER_GENERATION_SUFFIXES,
        help="Generation used as the after file with --slot.",
    )
    region_parser.add_argument(
        "--max-spans",
        type=int,
        default=20,
        help="Maximum outside-region difference spans to print.",
    )

    inventory_parser = subparsers.add_parser(
        "inventory",
        help="Parse and summarize the observed inventory framing read-only.",
    )
    inventory_parser.add_argument("--game-dir", type=Path, required=True)
    inventory_parser.add_argument("--slot", required=True)
    inventory_parser.add_argument(
        "--items",
        action="store_true",
        help="Print every parsed item after the category summary.",
    )

    npc_flags_parser = subparsers.add_parser(
        "npc-flags-inspect",
        help="Read the structurally identified NPC flag table.",
    )
    npc_flags_parser.add_argument(
        "--character",
        type=Path,
        required=True,
        help="Character _ls file to inspect read-only.",
    )
    npc_flags_parser.add_argument(
        "--game-dir",
        type=Path,
        help=(
            "Installed game directory used to add English associations "
            "and meanings."
        ),
    )
    npc_flags_parser.add_argument(
        "--filter",
        help="Show only flag keys containing this text.",
    )
    npc_flags_parser.add_argument(
        "--all",
        action="store_true",
        help="Show disabled flags as well as enabled flags.",
    )

    npc_flags_diff_parser = subparsers.add_parser(
        "npc-flags-diff",
        help="Compare NPC flag values between two character captures.",
    )
    npc_flags_diff_parser.add_argument(
        "--before",
        type=Path,
        required=True,
        help="Earlier character capture.",
    )
    npc_flags_diff_parser.add_argument(
        "--after",
        type=Path,
        required=True,
        help="Later character capture.",
    )
    npc_flags_diff_parser.add_argument(
        "--filter",
        help="Show only changed flag keys containing this text.",
    )

    quests_parser = subparsers.add_parser(
        "quests",
        help="Correlate installed quest definitions with one save read-only.",
    )
    quests_parser.add_argument("--game-dir", type=Path, required=True)
    quests_parser.add_argument(
        "--slot",
        help="Save slot to inspect, such as slot5.",
    )
    quests_parser.add_argument(
        "--character",
        type=Path,
        help="Explicit character _ls file to inspect.",
    )
    quests_parser.add_argument(
        "--filter",
        help="Search quest title, asset, NPCs, flags, and evidence text.",
    )
    quests_parser.add_argument(
        "--status",
        default="all",
        choices=(
            "all",
            "undiscovered",
            "in-progress",
            "completed",
            "no-flags",
            "conflicting",
            "repeatable",
        ),
        help="Limit quests by inferred status or repeatable/temp type.",
    )

    bruno_inspect_parser = subparsers.add_parser(
        "bruno-reputation-inspect",
        help="Read Bruno's structurally identified reputation record.",
    )
    bruno_inspect_parser.add_argument(
        "--character",
        type=Path,
        required=True,
        help="Character _ls file to inspect read-only.",
    )

    bruno_parser = subparsers.add_parser(
        "bruno-reputation",
        help="Compare a controlled before/after Bruno reputation capture.",
    )
    bruno_parser.add_argument("--game-dir", type=Path, required=True)
    bruno_parser.add_argument(
        "--before",
        type=Path,
        required=True,
        help="Character file copied before the Bruno reputation action.",
    )
    bruno_parser.add_argument(
        "--after",
        type=Path,
        required=True,
        help="Character file saved after exactly one Bruno reputation action.",
    )
    bruno_parser.add_argument(
        "--expected-delta",
        type=int,
        help="Known reputation delta from the selected Bruno dialogue branch.",
    )
    bruno_parser.add_argument(
        "--max-candidates",
        type=int,
        default=20,
        help="Maximum candidate integer changes to print.",
    )
    bruno_parser.add_argument(
        "--max-strings",
        type=int,
        default=20,
        help="Maximum serialized Bruno strings to print per file.",
    )
    bruno_sequence_parser = subparsers.add_parser(
        "bruno-reputation-sequence",
        help=(
            "Validate one Bruno reputation field across two controlled "
            "opposing changes."
        ),
    )
    bruno_sequence_parser.add_argument("--game-dir", type=Path, required=True)
    bruno_sequence_parser.add_argument(
        "--before",
        type=Path,
        required=True,
        help="Character capture before the first Bruno reputation change.",
    )
    bruno_sequence_parser.add_argument(
        "--middle",
        type=Path,
        required=True,
        help="Character capture after the first Bruno reputation change.",
    )
    bruno_sequence_parser.add_argument(
        "--after",
        type=Path,
        required=True,
        help="Character capture after the opposing reputation change.",
    )
    bruno_sequence_parser.add_argument(
        "--before-value",
        type=int,
        required=True,
        help="Displayed Bruno reputation in the first capture.",
    )
    bruno_sequence_parser.add_argument(
        "--middle-value",
        type=int,
        required=True,
        help="Displayed Bruno reputation in the middle capture.",
    )
    bruno_sequence_parser.add_argument(
        "--after-value",
        type=int,
        required=True,
        help="Displayed Bruno reputation in the final capture.",
    )
    bruno_sequence_parser.add_argument(
        "--max-candidates",
        type=int,
        default=20,
        help="Maximum candidates to print for each result group.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        if args.command == "catalog":
            return _catalog_command(args.game_dir.expanduser().absolute())
        if args.command == "inventory":
            return _inventory_command(
                args.game_dir.expanduser().absolute(),
                args.slot,
                args.items,
            )
        if args.command == "npc-flags-inspect":
            return _npc_flags_inspect_command(
                args.character,
                args.game_dir,
                args.filter,
                args.all,
            )
        if args.command == "npc-flags-diff":
            return _npc_flags_diff_command(
                args.before,
                args.after,
                args.filter,
            )
        if args.command == "quests":
            return _quests_command(
                args.game_dir,
                args.slot,
                args.character,
                args.filter,
                args.status,
            )
        if args.command == "bruno-reputation-inspect":
            return _bruno_reputation_inspect_command(args.character)
        if hasattr(args, "max_spans") and args.max_spans < 0:
            raise ValueError("--max-spans cannot be negative")
        if getattr(args, "max_candidates", 0) < 0:
            raise ValueError("--max-candidates cannot be negative")
        if getattr(args, "max_strings", 0) < 0:
            raise ValueError("--max-strings cannot be negative")
        if args.command == "region-diff":
            return _region_diff_command(
                args.game_dir.expanduser().absolute(),
                args.slot,
                args.before,
                args.after,
                args.before_generation,
                args.after_generation,
                args.max_spans,
            )
        if args.command == "bruno-reputation":
            return _bruno_reputation_command(
                args.game_dir.expanduser().absolute(),
                args.before,
                args.after,
                args.expected_delta,
                args.max_candidates,
                args.max_strings,
            )
        if args.command == "bruno-reputation-sequence":
            return _bruno_reputation_sequence_command(
                args.game_dir.expanduser().absolute(),
                args.before,
                args.middle,
                args.after,
                args.before_value,
                args.middle_value,
                args.after_value,
                args.max_candidates,
            )
        return _compare_command(
            args.game_dir.expanduser().absolute(),
            args.slot,
            args.max_spans,
        )
    except (
        CatalogError,
        FileNotFoundError,
        InventoryFormatError,
        npc.NpcAnnotationError,
        npc.NpcFormatError,
        quests.QuestCatalogError,
        OSError,
        reputation.ReputationResearchError,
        ValueError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
