"""Item catalog and research-gated inventory format support."""

from __future__ import annotations

import json
import hashlib
import struct
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Optional, Sequence, Union


UNITYPY_MAJOR_MINOR = (1, 25)
RESOURCE_BUNDLE_PATH = (
    Path("HoboRPG_Data")
    / "StreamingAssets"
    / "Resources"
    / "ResourcesBundle"
)
ITEM_DATABASE_ASSET_PATH = (
    "assets/hobothor/itemmanager/itemdatabasejson.json"
)
ENGLISH_ITEMS_ASSET_PATH = (
    "assets/hobothor/strings/_translatedjson_/en/strings_items.json"
)
ITEM_CATEGORY_KEYS = (
    "bags",
    "companions",
    "consumables",
    "gears",
    "hideoutInteriors",
    "scraps",
    "sleepingBags",
    "weapons",
)
SERIALIZE_TYPE_CATEGORIES = {
    1: "gears",
    2: "bags",
    3: "consumables",
    4: "hideoutInteriors",
    5: "scraps",
    7: "sleepingBags",
    8: "weapons",
    9: "companions",
}
CATEGORY_SERIALIZE_TYPES = {
    category: serialize_type
    for serialize_type, category in SERIALIZE_TYPE_CATEGORIES.items()
}
INVENTORY_TYPE_ORDER = (8, 1, 2, 3, 4, 5, 7, 9)
SERIALIZED_RECORD_SIZES = {
    1: 31,
    2: 15,
    3: 15,
    4: 19,
    5: 15,
    7: 19,
    8: 19,
    9: 15,
}
BASE_ITEM_RECORD_SIZE = 15
QUANTITY_FIELD_OFFSET = 9
MAX_SERIALIZED_QUANTITY = (1 << 31) - 1
INVENTORY_SEARCH_START = 0x180
INVENTORY_SEARCH_END = 0x500
MAX_SAVED_BAGS = 16
MAX_INVENTORY_ITEMS = 4096
VALIDATED_CONSUMABLE_INCREASE_LIMITS = {
    307: 8,  # Scrap food, observed game-generated range 6 through 8.
}
VALIDATED_REMOVAL_ITEM_IDS = {1}  # Roll, observed quantity-one depletion.
VALIDATED_ADDITION_RECORDS = {
    1: struct.pack("<BiiiBB", 1, 1, 3, 1, 0, 0),
}
VALIDATED_WEAPON_SWAP_PAIRS = {frozenset({521, 532})}
VALIDATED_GEAR_TRANSFERS = {("hat", 167)}
VALIDATED_BAG_TRANSFERS = {291}
EQUIPMENT_SLOT_SPECS = (
    ("hat", 1, 0),
    ("jacket", 1, 1),
    ("trousers", 1, 2),
    ("shoes", 1, 3),
    ("companion", 9, None),
    ("weapon", 8, None),
)


class CatalogError(ValueError):
    """Raised when the installed item catalog cannot be trusted."""


class InventoryFormatError(ValueError):
    """Raised when the read-only inventory framing is not unambiguous."""


class InventoryMutationError(ValueError):
    """Raised when a staged inventory mutation cannot be applied safely."""


@dataclass(frozen=True)
class ItemDefinition:
    item_id: int
    category: str
    index: int
    name: str
    description: str
    title_key: int
    description_key: int
    attributes: Mapping[str, object]


@dataclass(frozen=True)
class ItemCatalog:
    max_item_id: int
    items: tuple[ItemDefinition, ...]
    localization: Mapping[int, str]
    bundle_path: Optional[Path] = None
    unity_version: Optional[str] = None
    _items_by_id: Mapping[int, ItemDefinition] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )

    def get(self, item_id: int) -> Optional[ItemDefinition]:
        return self._items_by_id.get(item_id)

    def category_counts(self) -> Mapping[str, int]:
        counts = {
            category: sum(
                item.category == category for item in self.items
            )
            for category in ITEM_CATEGORY_KEYS
        }
        return MappingProxyType(counts)


@dataclass(frozen=True)
class InventoryItem:
    offset: int
    definition: ItemDefinition
    serialize_type: int
    quantity: int
    flag_a: bool
    flag_b: bool
    payload: bytes
    raw: bytes


@dataclass(frozen=True)
class SavedBagSlot:
    offset: int
    raw: bytes
    item: Optional[InventoryItem]


@dataclass(frozen=True)
class InventorySnapshot:
    source_sha256: str
    bags_count_offset: int
    inventory_count_offset: int
    inventory_start: int
    inventory_end: int
    bag_slots: tuple[SavedBagSlot, ...]
    bags: tuple[InventoryItem, ...]
    items: tuple[InventoryItem, ...]


@dataclass(frozen=True)
class EquipmentSlot:
    name: str
    offset: int
    raw: bytes
    item: Optional[InventoryItem]


@dataclass(frozen=True)
class EquipmentSnapshot:
    source_sha256: str
    start: int
    end: int
    slots: tuple[EquipmentSlot, ...]

    def get(self, name: str) -> EquipmentSlot:
        matches = [slot for slot in self.slots if slot.name == name]
        if len(matches) != 1:
            raise InventoryFormatError(
                f"Expected exactly one equipment slot {name!r}"
            )
        return matches[0]


@dataclass(frozen=True)
class InventoryQuantityChange:
    item_offset: int
    item_id: int
    expected_quantity: int
    new_quantity: int


@dataclass(frozen=True)
class InventoryItemRemoval:
    item_offset: int
    item_id: int
    expected_raw: bytes


@dataclass(frozen=True)
class InventoryItemAddition:
    item_id: int
    insertion_offset: int
    raw: bytes
    previous_item_id: int
    next_item_id: int


@dataclass(frozen=True)
class EquipmentWeaponSwap:
    equipment_offset: int
    inventory_offset: int
    equipped_item_id: int
    carried_item_id: int
    equipped_raw: bytes
    carried_raw: bytes


@dataclass(frozen=True)
class EquipmentGearTransfer:
    action: str
    slot_name: str
    equipment_offset: int
    inventory_offset: int
    item_id: int
    raw: bytes


@dataclass(frozen=True)
class SavedBagTransfer:
    action: str
    slot_index: int
    slot_offset: int
    inventory_offset: int
    item_id: int
    raw: bytes


@dataclass(frozen=True)
class FullInventoryEditPlan:
    source_sha256: str
    equipment_slot_raws: tuple[bytes, ...]
    saved_bag_slot_raws: tuple[bytes, ...]
    carried_item_raws: tuple[bytes, ...]
    descriptions: tuple[str, ...] = ()


InventoryChange = Union[
    InventoryQuantityChange,
    InventoryItemRemoval,
    InventoryItemAddition,
]


def _load_json_object(text: str, label: str) -> dict[str, object]:
    try:
        value = json.loads(text.lstrip("\ufeff"))
    except (TypeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise CatalogError(f"{label} must contain a JSON object")
    return value


def _require_int(
    mapping: Mapping[str, object],
    key: str,
    label: str,
) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CatalogError(f"{label} field {key!r} must be an integer")
    return value


def _parse_localization(text: str) -> Mapping[int, str]:
    source = _load_json_object(text, "English item localization")
    keys = source.get("keys")
    values = source.get("values")
    if not isinstance(keys, list) or not isinstance(values, list):
        raise CatalogError(
            "English item localization must contain keys and values lists"
        )
    if len(keys) != len(values):
        raise CatalogError(
            "English item localization keys and values have different lengths"
        )

    localization: dict[int, str] = {}
    for position, (key, value) in enumerate(zip(keys, values)):
        if isinstance(key, bool) or not isinstance(key, int):
            raise CatalogError(
                "English item localization key at position "
                f"{position} is not an integer"
            )
        if not isinstance(value, str):
            raise CatalogError(
                "English item localization value at position "
                f"{position} is not a string"
            )
        if key in localization:
            raise CatalogError(
                f"English item localization contains duplicate key {key}"
            )
        localization[key] = value
    return MappingProxyType(localization)


def parse_item_catalog(
    database_text: str,
    localization_text: str,
    *,
    bundle_path: Optional[Path] = None,
    unity_version: Optional[str] = None,
) -> ItemCatalog:
    """Parse and validate the item database and English localization."""
    database = _load_json_object(database_text, "Item database")
    max_item_id = _require_int(database, "MaxItemID", "Item database")
    if max_item_id < 0:
        raise CatalogError("Item database MaxItemID cannot be negative")

    localization = _parse_localization(localization_text)
    items: list[ItemDefinition] = []
    seen_ids: set[int] = set()
    for category in ITEM_CATEGORY_KEYS:
        records = database.get(category)
        if not isinstance(records, list):
            raise CatalogError(
                f"Item database category {category!r} must be a list"
            )
        for position, raw_record in enumerate(records):
            label = f"{category}[{position}]"
            if not isinstance(raw_record, dict):
                raise CatalogError(f"{label} must be an object")
            item_id = _require_int(raw_record, "id", label)
            index = _require_int(raw_record, "index", label)
            title_key = _require_int(raw_record, "titleKey", label)
            description_key = _require_int(
                raw_record,
                "descriptionKey",
                label,
            )
            if item_id < 0 or item_id > max_item_id:
                raise CatalogError(
                    f"{label} item ID {item_id} is outside 0..{max_item_id}"
                )
            if item_id in seen_ids:
                raise CatalogError(
                    f"Item database contains duplicate item ID {item_id}"
                )
            seen_ids.add(item_id)

            name = localization.get(title_key) or f"Item {item_id}"
            description = localization.get(description_key, "")
            items.append(
                ItemDefinition(
                    item_id=item_id,
                    category=category,
                    index=index,
                    name=name,
                    description=description,
                    title_key=title_key,
                    description_key=description_key,
                    attributes=MappingProxyType(dict(raw_record)),
                )
            )

    items.sort(key=lambda item: (item.index, item.item_id))
    return ItemCatalog(
        max_item_id=max_item_id,
        items=tuple(items),
        localization=localization,
        bundle_path=bundle_path,
        unity_version=unity_version,
        _items_by_id=MappingProxyType(
            {item.item_id: item for item in items}
        ),
    )


def _unitypy_module():
    try:
        import UnityPy
    except ImportError as exc:
        raise CatalogError(
            "UnityPy is required to read the installed item catalog"
        ) from exc

    version = getattr(UnityPy, "__version__", "")
    try:
        major, minor = (int(part) for part in version.split(".")[:2])
    except (TypeError, ValueError):
        major, minor = (-1, -1)
    if (major, minor) != UNITYPY_MAJOR_MINOR:
        expected = ".".join(str(part) for part in UNITYPY_MAJOR_MINOR)
        raise CatalogError(
            f"Unsupported UnityPy version {version or '(unknown)'}; "
            f"expected {expected}.x"
        )
    return UnityPy


def _text_asset(
    environment: object,
    asset_path: str,
) -> tuple[str, Optional[str]]:
    container = getattr(environment, "container", None)
    items = getattr(container, "items", None)
    if not callable(items):
        raise CatalogError("ResourcesBundle has no readable asset container")

    matches = [
        pointer
        for path, pointer in items()
        if isinstance(path, str) and path.casefold() == asset_path.casefold()
    ]
    if len(matches) != 1:
        raise CatalogError(
            f"Expected exactly one catalog asset {asset_path}, "
            f"found {len(matches)}"
        )

    try:
        reader = matches[0].deref()
        if reader.type.name != "TextAsset":
            raise CatalogError(f"Catalog asset {asset_path} is not a TextAsset")
        asset = reader.parse_as_object()
        script = asset.m_Script
        unity_version = getattr(reader.assets_file, "unity_version", None)
    except CatalogError:
        raise
    except Exception as exc:
        raise CatalogError(
            f"Could not read catalog asset {asset_path}: {exc}"
        ) from exc
    if not isinstance(script, str):
        raise CatalogError(f"Catalog asset {asset_path} is not text")
    return script, unity_version


def load_item_catalog(game_dir: Path) -> ItemCatalog:
    """Load the installed game's item database without modifying the bundle."""
    bundle_path = game_dir.expanduser().absolute() / RESOURCE_BUNDLE_PATH
    if not bundle_path.is_file():
        raise CatalogError(f"ResourcesBundle not found: {bundle_path}")

    UnityPy = _unitypy_module()
    try:
        environment = UnityPy.load(str(bundle_path))
    except Exception as exc:
        raise CatalogError(f"Could not open ResourcesBundle: {exc}") from exc

    database_text, database_version = _text_asset(
        environment,
        ITEM_DATABASE_ASSET_PATH,
    )
    localization_text, localization_version = _text_asset(
        environment,
        ENGLISH_ITEMS_ASSET_PATH,
    )
    if (
        database_version
        and localization_version
        and database_version != localization_version
    ):
        raise CatalogError(
            "Catalog assets report different Unity versions: "
            f"{database_version} and {localization_version}"
        )
    return parse_item_catalog(
        database_text,
        localization_text,
        bundle_path=bundle_path,
        unity_version=database_version or localization_version,
    )


def _read_count(data: bytes, offset: int, label: str) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise InventoryFormatError(f"Missing {label} count")
    return struct.unpack_from("<i", data, offset)[0]


def _parse_inventory_items(
    data: bytes,
    offset: int,
    count: int,
    catalog: ItemCatalog,
    *,
    expected_serialize_type: Optional[int] = None,
) -> tuple[tuple[InventoryItem, ...], int]:
    items: list[InventoryItem] = []
    current = offset
    for position in range(count):
        if current + BASE_ITEM_RECORD_SIZE > len(data):
            raise InventoryFormatError(
                f"Inventory item {position} is truncated"
            )
        present, item_id, serialize_type, quantity, flag_a, flag_b = (
            struct.unpack_from("<BiiiBB", data, current)
        )
        if present != 1:
            raise InventoryFormatError(
                f"Inventory item {position} has presence marker {present}"
            )
        definition = catalog.get(item_id)
        if definition is None:
            raise InventoryFormatError(
                f"Inventory item {position} has unknown item ID {item_id}"
            )
        expected_category = SERIALIZE_TYPE_CATEGORIES.get(serialize_type)
        if expected_category is None:
            raise InventoryFormatError(
                "Inventory item "
                f"{position} has unknown serialization type {serialize_type}"
            )
        if expected_serialize_type is not None:
            if serialize_type != expected_serialize_type:
                raise InventoryFormatError(
                    f"Saved bag {position} has serialization type "
                    f"{serialize_type}"
                )
        if definition.category != expected_category:
            raise InventoryFormatError(
                f"Item ID {item_id} belongs to {definition.category}, "
                f"not serialization type {serialize_type}"
            )
        if quantity <= 0:
            raise InventoryFormatError(
                f"Inventory item {position} has quantity {quantity}"
            )
        if flag_a not in (0, 1) or flag_b not in (0, 1):
            raise InventoryFormatError(
                f"Inventory item {position} has non-boolean flags"
            )

        record_size = SERIALIZED_RECORD_SIZES[serialize_type]
        end = current + record_size
        if end > len(data):
            raise InventoryFormatError(
                f"Inventory item {position} payload is truncated"
            )
        raw = data[current:end]
        items.append(
            InventoryItem(
                offset=current,
                definition=definition,
                serialize_type=serialize_type,
                quantity=quantity,
                flag_a=bool(flag_a),
                flag_b=bool(flag_b),
                payload=raw[BASE_ITEM_RECORD_SIZE:],
                raw=raw,
            )
        )
        current = end
    return tuple(items), current


def _parse_saved_bag_slots(
    data: bytes,
    offset: int,
    count: int,
    catalog: ItemCatalog,
) -> tuple[tuple[SavedBagSlot, ...], int]:
    slots: list[SavedBagSlot] = []
    current = offset
    for position in range(count):
        if current >= len(data):
            raise InventoryFormatError(
                f"Saved bag slot {position} is truncated"
            )
        if data[current] == 0:
            slots.append(
                SavedBagSlot(
                    offset=current,
                    raw=b"\0",
                    item=None,
                )
            )
            current += 1
            continue

        items, current = _parse_inventory_items(
            data,
            current,
            1,
            catalog,
            expected_serialize_type=2,
        )
        slots.append(
            SavedBagSlot(
                offset=items[0].offset,
                raw=items[0].raw,
                item=items[0],
            )
        )
    return tuple(slots), current


def _inventory_candidate(
    data: bytes,
    bags_count_offset: int,
    catalog: ItemCatalog,
) -> InventorySnapshot:
    bags_count = _read_count(data, bags_count_offset, "saved bags")
    if not 0 <= bags_count <= MAX_SAVED_BAGS:
        raise InventoryFormatError(
            f"Saved bags count {bags_count} is outside the supported range"
        )
    bag_slots, inventory_count_offset = _parse_saved_bag_slots(
        data,
        bags_count_offset + 4,
        bags_count,
        catalog,
    )
    bags = tuple(
        slot.item for slot in bag_slots if slot.item is not None
    )
    inventory_count = _read_count(
        data,
        inventory_count_offset,
        "inventory",
    )
    if not 1 <= inventory_count <= MAX_INVENTORY_ITEMS:
        raise InventoryFormatError(
            f"Inventory count {inventory_count} is outside the supported range"
        )
    inventory_start = inventory_count_offset + 4
    items, inventory_end = _parse_inventory_items(
        data,
        inventory_start,
        inventory_count,
        catalog,
    )
    return InventorySnapshot(
        source_sha256=hashlib.sha256(data).hexdigest(),
        bags_count_offset=bags_count_offset,
        inventory_count_offset=inventory_count_offset,
        inventory_start=inventory_start,
        inventory_end=inventory_end,
        bag_slots=bag_slots,
        bags=bags,
        items=items,
    )


def parse_inventory(
    data: bytes,
    catalog: ItemCatalog,
) -> InventorySnapshot:
    """Locate and parse the observed bags/inventory framing read-only."""
    candidates: dict[int, InventorySnapshot] = {}
    search_end = min(INVENTORY_SEARCH_END, len(data) - 7)
    for offset in range(INVENTORY_SEARCH_START, max(search_end, 0)):
        try:
            candidate = _inventory_candidate(data, offset, catalog)
        except InventoryFormatError:
            continue
        previous = candidates.get(candidate.inventory_count_offset)
        if previous is None or len(candidate.bags) > len(previous.bags):
            candidates[candidate.inventory_count_offset] = candidate

    with_saved_bags = [
        candidate for candidate in candidates.values() if candidate.bags
    ]
    if with_saved_bags:
        candidates = {
            candidate.inventory_count_offset: candidate
            for candidate in with_saved_bags
        }
    if len(candidates) != 1:
        raise InventoryFormatError(
            "Expected exactly one bounded inventory region, found "
            f"{len(candidates)}"
        )
    return next(iter(candidates.values()))


def read_inventory(
    path: Path,
    catalog: ItemCatalog,
) -> InventorySnapshot:
    """Read a character file and parse its observed inventory framing."""
    if not path.is_file():
        raise InventoryFormatError(f"Character file not found: {path}")
    return parse_inventory(path.read_bytes(), catalog)


def parse_equipment(
    data: bytes,
    catalog: ItemCatalog,
    inventory: Optional[InventorySnapshot] = None,
) -> EquipmentSnapshot:
    """Parse six nullable equipped-item slots ending at saved bags."""
    inventory = inventory or parse_inventory(data, catalog)
    end = inventory.bags_count_offset
    slots_reversed: list[EquipmentSlot] = []
    for name, serialize_type, gear_category in reversed(
        EQUIPMENT_SLOT_SPECS
    ):
        record_size = SERIALIZED_RECORD_SIZES[serialize_type]
        record_start = end - record_size
        item: Optional[InventoryItem] = None
        if record_start >= 0:
            try:
                parsed, parsed_end = _parse_inventory_items(
                    data,
                    record_start,
                    1,
                    catalog,
                    expected_serialize_type=serialize_type,
                )
            except InventoryFormatError:
                parsed = ()
                parsed_end = -1
            if parsed and parsed_end == end:
                candidate = parsed[0]
                if (
                    gear_category is not None
                    and candidate.definition.attributes.get("category")
                    != gear_category
                ):
                    raise InventoryFormatError(
                        f"equipped {name} has the wrong gear category"
                    )
                item = candidate

        if item is not None:
            slot = EquipmentSlot(
                name=name,
                offset=item.offset,
                raw=item.raw,
                item=item,
            )
            end = item.offset
        elif end > 0 and data[end - 1] == 0:
            slot = EquipmentSlot(
                name=name,
                offset=end - 1,
                raw=b"\0",
                item=None,
            )
            end -= 1
        else:
            raise InventoryFormatError(
                f"Could not parse equipped {name} slot"
            )
        slots_reversed.append(slot)

    slots_reversed.reverse()
    return EquipmentSnapshot(
        source_sha256=inventory.source_sha256,
        start=end,
        end=inventory.bags_count_offset,
        slots=tuple(slots_reversed),
    )


def read_equipment(
    path: Path,
    catalog: ItemCatalog,
) -> EquipmentSnapshot:
    """Read the supported equipped-item block from a character file."""
    if not path.is_file():
        raise InventoryFormatError(f"Character file not found: {path}")
    data = path.read_bytes()
    inventory = parse_inventory(data, catalog)
    return parse_equipment(data, catalog, inventory)


def _validate_quantity(quantity: int) -> None:
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise InventoryMutationError("Inventory quantity must be an integer")
    if not 1 <= quantity <= MAX_SERIALIZED_QUANTITY:
        raise InventoryMutationError(
            "Inventory quantity must be between 1 and "
            f"{MAX_SERIALIZED_QUANTITY}"
        )


def _validate_supported_quantity_change(
    item: InventoryItem,
    new_quantity: int,
) -> None:
    if item.serialize_type != 3:
        raise InventoryMutationError(
            "Only controlled consumable quantity changes are supported"
        )
    if new_quantity <= item.quantity:
        return

    validated_limit = VALIDATED_CONSUMABLE_INCREASE_LIMITS.get(
        item.definition.item_id
    )
    if validated_limit is None:
        raise InventoryMutationError(
            "Quantity increases are not validated for item "
            f"{item.definition.item_id}"
        )
    if new_quantity != item.quantity + 1:
        raise InventoryMutationError(
            "Validated quantity increases must add exactly one item"
        )
    if new_quantity > validated_limit:
        raise InventoryMutationError(
            f"{item.definition.name} increase exceeds validated quantity "
            f"{validated_limit}"
        )


def stage_quantity_change(
    snapshot: InventorySnapshot,
    item_offset: int,
    new_quantity: int,
) -> InventoryQuantityChange:
    """Stage a quantity change for one exact carried-inventory record."""
    _validate_quantity(new_quantity)
    matches = [
        item for item in snapshot.items if item.offset == item_offset
    ]
    if len(matches) != 1:
        raise InventoryMutationError(
            "Expected exactly one carried item at offset "
            f"0x{item_offset:X}, found {len(matches)}"
        )
    item = matches[0]
    if new_quantity == item.quantity:
        raise InventoryMutationError(
            f"Item {item.definition.item_id} already has quantity "
            f"{new_quantity}"
        )
    _validate_supported_quantity_change(item, new_quantity)
    return InventoryQuantityChange(
        item_offset=item.offset,
        item_id=item.definition.item_id,
        expected_quantity=item.quantity,
        new_quantity=new_quantity,
    )


def stage_item_removal(
    snapshot: InventorySnapshot,
    item_offset: int,
) -> InventoryItemRemoval:
    """Stage removal of one exact, validated quantity-one record."""
    matches = [
        item for item in snapshot.items if item.offset == item_offset
    ]
    if len(matches) != 1:
        raise InventoryMutationError(
            "Expected exactly one carried item at offset "
            f"0x{item_offset:X}, found {len(matches)}"
        )
    item = matches[0]
    if item.definition.item_id not in VALIDATED_REMOVAL_ITEM_IDS:
        raise InventoryMutationError(
            f"Removal is not validated for item {item.definition.item_id}"
        )
    if item.serialize_type != 3 or item.quantity != 1:
        raise InventoryMutationError(
            "Validated item removal requires a quantity-one consumable"
        )
    return InventoryItemRemoval(
        item_offset=item.offset,
        item_id=item.definition.item_id,
        expected_raw=item.raw,
    )


def _validated_addition(
    snapshot: InventorySnapshot,
    catalog: ItemCatalog,
    item_id: int,
) -> InventoryItemAddition:
    raw = VALIDATED_ADDITION_RECORDS.get(item_id)
    if raw is None:
        raise InventoryMutationError(
            f"Addition is not validated for item {item_id}"
        )
    definition = catalog.get(item_id)
    if definition is None:
        raise InventoryMutationError(f"Unknown catalog item {item_id}")
    if definition.category != "consumables":
        raise InventoryMutationError(
            "Validated addition must be a consumable"
        )
    if any(item.definition.item_id == item_id for item in snapshot.items):
        raise InventoryMutationError(
            f"Item {item_id} already exists in carried inventory"
        )

    preceding = [
        item
        for item in snapshot.items
        if item.serialize_type == 3
        and item.definition.index < definition.index
    ]
    following = [
        item
        for item in snapshot.items
        if item.serialize_type == 3
        and item.definition.index > definition.index
    ]
    if not preceding or not following:
        raise InventoryMutationError(
            "Validated addition requires known neighboring consumables"
        )
    previous = max(preceding, key=lambda item: item.definition.index)
    next_item = min(following, key=lambda item: item.definition.index)
    if previous.offset + len(previous.raw) != next_item.offset:
        raise InventoryMutationError(
            "Consumable ordering is not contiguous at insertion point"
        )
    return InventoryItemAddition(
        item_id=item_id,
        insertion_offset=next_item.offset,
        raw=raw,
        previous_item_id=previous.definition.item_id,
        next_item_id=next_item.definition.item_id,
    )


def stage_item_addition(
    snapshot: InventorySnapshot,
    catalog: ItemCatalog,
    item_id: int,
) -> InventoryItemAddition:
    """Stage one exact validated default record at its catalog order."""
    return _validated_addition(snapshot, catalog, item_id)


def stage_weapon_swap(
    equipment: EquipmentSnapshot,
    inventory: InventorySnapshot,
    carried_offset: int,
) -> EquipmentWeaponSwap:
    """Stage an exact swap between equipped and carried weapon records."""
    if equipment.source_sha256 != inventory.source_sha256:
        raise InventoryMutationError(
            "Equipment and inventory snapshots have different sources"
        )
    weapon_slot = equipment.get("weapon")
    if weapon_slot.item is None:
        raise InventoryMutationError(
            "Validated weapon swap requires an equipped weapon"
        )
    matches = [
        item
        for item in inventory.items
        if item.offset == carried_offset
    ]
    if len(matches) != 1:
        raise InventoryMutationError(
            "Expected exactly one carried item at offset "
            f"0x{carried_offset:X}, found {len(matches)}"
        )
    carried = matches[0]
    if carried.serialize_type != 8:
        raise InventoryMutationError("Carried item is not a weapon")
    pair = frozenset(
        {
            weapon_slot.item.definition.item_id,
            carried.definition.item_id,
        }
    )
    if pair not in VALIDATED_WEAPON_SWAP_PAIRS:
        raise InventoryMutationError(
            "Weapon swap is not validated for item IDs "
            f"{sorted(pair)}"
        )
    if len(weapon_slot.raw) != len(carried.raw):
        raise InventoryMutationError(
            "Weapon records have different serialized sizes"
        )
    return EquipmentWeaponSwap(
        equipment_offset=weapon_slot.offset,
        inventory_offset=carried.offset,
        equipped_item_id=weapon_slot.item.definition.item_id,
        carried_item_id=carried.definition.item_id,
        equipped_raw=weapon_slot.raw,
        carried_raw=carried.raw,
    )


def apply_weapon_swap(
    data: bytes,
    catalog: ItemCatalog,
    change: EquipmentWeaponSwap,
    *,
    expected_source_sha256: str,
) -> bytes:
    """Exchange one validated equipped/carried weapon pair in place."""
    source_sha256 = hashlib.sha256(data).hexdigest()
    if source_sha256 != expected_source_sha256:
        raise InventoryMutationError(
            "Inventory source changed after staging; refresh and try again"
        )
    inventory = parse_inventory(data, catalog)
    equipment = parse_equipment(data, catalog, inventory)
    expected = stage_weapon_swap(
        equipment,
        inventory,
        change.inventory_offset,
    )
    if change != expected:
        raise InventoryMutationError(
            "Staged weapon swap no longer matches the source"
        )

    updated = bytearray(data)
    updated[
        change.equipment_offset:
        change.equipment_offset + len(change.equipped_raw)
    ] = change.carried_raw
    updated[
        change.inventory_offset:
        change.inventory_offset + len(change.carried_raw)
    ] = change.equipped_raw
    updated_bytes = bytes(updated)

    after_inventory = parse_inventory(updated_bytes, catalog)
    after_equipment = parse_equipment(
        updated_bytes,
        catalog,
        after_inventory,
    )
    after_weapon = after_equipment.get("weapon")
    after_carried = next(
        (
            item
            for item in after_inventory.items
            if item.offset == change.inventory_offset
        ),
        None,
    )
    if (
        after_weapon.raw != change.carried_raw
        or after_carried is None
        or after_carried.raw != change.equipped_raw
    ):
        raise InventoryMutationError("Weapon swap verification failed")
    return updated_bytes


def write_weapon_swap(
    path: Path,
    catalog: ItemCatalog,
    change: EquipmentWeaponSwap,
    *,
    expected_source_sha256: str,
    now: Optional[datetime] = None,
    backup_dir: Optional[Path] = None,
) -> Path:
    """Write a validated weapon swap through the atomic backup path."""
    from . import editor

    original = path.read_bytes()
    updated = apply_weapon_swap(
        original,
        catalog,
        change,
        expected_source_sha256=expected_source_sha256,
    )
    return editor._write_updated_character(
        path,
        original,
        updated,
        now=now,
        backup_dir=backup_dir,
    )


def _gear_slot_category(slot_name: str) -> int:
    matches = [
        gear_category
        for name, serialize_type, gear_category in EQUIPMENT_SLOT_SPECS
        if name == slot_name and serialize_type == 1
    ]
    if len(matches) != 1 or matches[0] is None:
        raise InventoryMutationError(
            f"Equipment slot {slot_name!r} is not a gear slot"
        )
    return matches[0]


def _validate_transfer_gear(
    item: InventoryItem,
    slot_name: str,
) -> None:
    if (slot_name, item.definition.item_id) not in VALIDATED_GEAR_TRANSFERS:
        raise InventoryMutationError(
            "Gear transfer is not validated for "
            f"{slot_name} item {item.definition.item_id}"
        )
    if item.serialize_type != 1 or item.quantity != 1:
        raise InventoryMutationError(
            "Validated gear transfer requires one serialized gear item"
        )
    if item.definition.attributes.get("category") != _gear_slot_category(
        slot_name
    ):
        raise InventoryMutationError(
            f"Gear item {item.definition.item_id} is incompatible with "
            f"the {slot_name} slot"
        )


def _gear_insertion_offset(snapshot: InventorySnapshot) -> int:
    if any(item.serialize_type == 1 for item in snapshot.items):
        raise InventoryMutationError(
            "Validated gear unequip requires no carried gear records"
        )
    first_non_weapon = next(
        (
            position
            for position, item in enumerate(snapshot.items)
            if item.serialize_type != 8
        ),
        None,
    )
    if first_non_weapon is None or first_non_weapon == 0:
        raise InventoryMutationError(
            "Validated gear ordering requires a carried weapon prefix"
        )
    following = snapshot.items[first_non_weapon]
    if following.serialize_type != 2:
        raise InventoryMutationError(
            "Validated gear ordering requires bags after carried weapons"
        )
    if any(
        item.serialize_type == 8
        for item in snapshot.items[first_non_weapon:]
    ):
        raise InventoryMutationError(
            "Carried weapon records are not in the validated order"
        )
    return following.offset


def _validate_carried_gear_position(
    snapshot: InventorySnapshot,
    carried: InventoryItem,
) -> None:
    gear_items = [
        item for item in snapshot.items if item.serialize_type == 1
    ]
    if gear_items != [carried]:
        raise InventoryMutationError(
            "Validated gear equip requires exactly one carried gear record"
        )
    position = snapshot.items.index(carried)
    if position == 0 or position + 1 >= len(snapshot.items):
        raise InventoryMutationError(
            "Carried gear is not in the validated inventory position"
        )
    if any(
        item.serialize_type != 8 for item in snapshot.items[:position]
    ):
        raise InventoryMutationError(
            "Validated carried gear must follow the weapon prefix"
        )
    if snapshot.items[position + 1].serialize_type != 2:
        raise InventoryMutationError(
            "Validated carried gear must immediately precede bags"
        )


def stage_gear_transfer(
    equipment: EquipmentSnapshot,
    inventory: InventorySnapshot,
    slot_name: str,
    carried_offset: Optional[int] = None,
) -> EquipmentGearTransfer:
    """Stage the exact validated transfer of gear to or from one slot."""
    if equipment.source_sha256 != inventory.source_sha256:
        raise InventoryMutationError(
            "Equipment and inventory snapshots have different sources"
        )
    slot = equipment.get(slot_name)
    if slot.item is not None:
        if carried_offset is not None:
            raise InventoryMutationError(
                "Unequipping gear does not accept a carried-item offset"
            )
        _validate_transfer_gear(slot.item, slot_name)
        insertion_offset = _gear_insertion_offset(inventory)
        return EquipmentGearTransfer(
            action="unequip",
            slot_name=slot_name,
            equipment_offset=slot.offset,
            inventory_offset=insertion_offset,
            item_id=slot.item.definition.item_id,
            raw=slot.raw,
        )

    if carried_offset is None:
        raise InventoryMutationError(
            f"Equipping the empty {slot_name} slot requires carried gear"
        )
    matches = [
        item for item in inventory.items if item.offset == carried_offset
    ]
    if len(matches) != 1:
        raise InventoryMutationError(
            "Expected exactly one carried item at offset "
            f"0x{carried_offset:X}, found {len(matches)}"
        )
    carried = matches[0]
    _validate_transfer_gear(carried, slot_name)
    _validate_carried_gear_position(inventory, carried)
    return EquipmentGearTransfer(
        action="equip",
        slot_name=slot_name,
        equipment_offset=slot.offset,
        inventory_offset=carried.offset,
        item_id=carried.definition.item_id,
        raw=carried.raw,
    )


def _verify_gear_transfer(
    before_data: bytes,
    after_data: bytes,
    catalog: ItemCatalog,
    before_inventory: InventorySnapshot,
    before_equipment: EquipmentSnapshot,
    change: EquipmentGearTransfer,
) -> None:
    if len(after_data) != len(before_data):
        raise InventoryMutationError(
            "Gear transfer changed the character file size"
        )
    after_inventory = parse_inventory(after_data, catalog)
    after_equipment = parse_equipment(
        after_data,
        catalog,
        after_inventory,
    )
    before_slots = {
        slot.name: slot.raw for slot in before_equipment.slots
    }
    after_slots = {
        slot.name: slot.raw for slot in after_equipment.slots
    }
    expected_slot_raw = b"\0" if change.action == "unequip" else change.raw
    for name, raw in before_slots.items():
        expected_raw = expected_slot_raw if name == change.slot_name else raw
        if after_slots.get(name) != expected_raw:
            raise InventoryMutationError(
                f"Unexpected equipped {name} bytes changed"
            )

    before_items = [item.raw for item in before_inventory.items]
    after_items = [item.raw for item in after_inventory.items]
    equipment_shift = len(change.raw) - 1
    if change.action == "unequip":
        insertion_position = next(
            position
            for position, item in enumerate(before_inventory.items)
            if item.offset == change.inventory_offset
        )
        expected_items = list(before_items)
        expected_items.insert(insertion_position, change.raw)
        expected_offsets = (
            before_inventory.bags_count_offset - equipment_shift,
            before_inventory.inventory_count_offset - equipment_shift,
            before_inventory.inventory_start - equipment_shift,
            before_inventory.inventory_end + 1,
        )
    else:
        removal_position = next(
            position
            for position, item in enumerate(before_inventory.items)
            if item.offset == change.inventory_offset
        )
        expected_items = list(before_items)
        del expected_items[removal_position]
        expected_offsets = (
            before_inventory.bags_count_offset + equipment_shift,
            before_inventory.inventory_count_offset + equipment_shift,
            before_inventory.inventory_start + equipment_shift,
            before_inventory.inventory_end - 1,
        )
    if after_items != expected_items:
        raise InventoryMutationError(
            "Carried inventory does not match the staged gear transfer"
        )
    actual_offsets = (
        after_inventory.bags_count_offset,
        after_inventory.inventory_count_offset,
        after_inventory.inventory_start,
        after_inventory.inventory_end,
    )
    if actual_offsets != expected_offsets:
        raise InventoryMutationError(
            "Inventory framing does not match the staged gear transfer"
        )
    if after_data[:change.equipment_offset] != before_data[
        :change.equipment_offset
    ]:
        raise InventoryMutationError(
            "Bytes before the gear slot changed during transfer"
        )


def apply_gear_transfer(
    data: bytes,
    catalog: ItemCatalog,
    change: EquipmentGearTransfer,
    *,
    expected_source_sha256: str,
) -> bytes:
    """Apply one validated gear transfer with exact structural checks."""
    source_sha256 = hashlib.sha256(data).hexdigest()
    if source_sha256 != expected_source_sha256:
        raise InventoryMutationError(
            "Inventory source changed after staging; refresh and try again"
        )
    before_inventory = parse_inventory(data, catalog)
    before_equipment = parse_equipment(
        data,
        catalog,
        before_inventory,
    )
    carried_offset = (
        change.inventory_offset if change.action == "equip" else None
    )
    expected = stage_gear_transfer(
        before_equipment,
        before_inventory,
        change.slot_name,
        carried_offset,
    )
    if change != expected:
        raise InventoryMutationError(
            "Staged gear transfer no longer matches the source"
        )
    if data[-1:] != b"\0":
        raise InventoryMutationError(
            "Character file lacks validated trailing zero padding"
        )

    updated = bytearray(data)
    if change.action == "unequip":
        contraction = len(change.raw) - 1
        updated[
            change.equipment_offset:
            change.equipment_offset + len(change.raw)
        ] = b"\0"
        count_offset = before_inventory.inventory_count_offset - contraction
        struct.pack_into(
            "<i",
            updated,
            count_offset,
            len(before_inventory.items) + 1,
        )
        del updated[-1:]
        insertion_offset = change.inventory_offset - contraction
        updated[insertion_offset:insertion_offset] = change.raw
    elif change.action == "equip":
        expansion = len(change.raw) - 1
        updated[
            change.equipment_offset:change.equipment_offset + 1
        ] = change.raw
        count_offset = before_inventory.inventory_count_offset + expansion
        struct.pack_into(
            "<i",
            updated,
            count_offset,
            len(before_inventory.items) - 1,
        )
        inventory_offset = change.inventory_offset + expansion
        del updated[inventory_offset:inventory_offset + len(change.raw)]
        updated.extend(b"\0")
    else:
        raise InventoryMutationError(
            f"Unknown staged gear action {change.action!r}"
        )

    updated_bytes = bytes(updated)
    _verify_gear_transfer(
        data,
        updated_bytes,
        catalog,
        before_inventory,
        before_equipment,
        change,
    )
    return updated_bytes


def write_gear_transfer(
    path: Path,
    catalog: ItemCatalog,
    change: EquipmentGearTransfer,
    *,
    expected_source_sha256: str,
    now: Optional[datetime] = None,
    backup_dir: Optional[Path] = None,
) -> Path:
    """Write a validated gear transfer through the atomic backup path."""
    from . import editor

    original = path.read_bytes()
    updated = apply_gear_transfer(
        original,
        catalog,
        change,
        expected_source_sha256=expected_source_sha256,
    )
    return editor._write_updated_character(
        path,
        original,
        updated,
        now=now,
        backup_dir=backup_dir,
    )


def _validate_transfer_bag(item: InventoryItem) -> None:
    if item.definition.item_id not in VALIDATED_BAG_TRANSFERS:
        raise InventoryMutationError(
            "Bag transfer is not validated for item "
            f"{item.definition.item_id}"
        )
    if item.serialize_type != 2 or item.quantity != 1:
        raise InventoryMutationError(
            "Validated bag transfer requires one serialized bag item"
        )


def _carried_bag_positions(snapshot: InventorySnapshot) -> list[int]:
    positions = [
        position
        for position, item in enumerate(snapshot.items)
        if item.serialize_type == 2
    ]
    if not positions:
        raise InventoryMutationError(
            "Validated bag transfer requires carried bag records"
        )
    expected = list(range(positions[0], positions[-1] + 1))
    if positions != expected:
        raise InventoryMutationError(
            "Carried bag records are not in the validated order"
        )
    return positions


def _inventory_position_for_offset(
    snapshot: InventorySnapshot,
    offset: int,
) -> int:
    for position, item in enumerate(snapshot.items):
        if item.offset == offset:
            return position
    if offset == snapshot.inventory_end:
        return len(snapshot.items)
    raise InventoryMutationError(
        f"Offset 0x{offset:X} is not a carried inventory boundary"
    )


def _bag_sort_key(item: InventoryItem) -> tuple[int, int]:
    return (item.definition.index, item.definition.item_id)


def _bag_insertion_offset(
    snapshot: InventorySnapshot,
    bag: InventoryItem,
) -> int:
    bag_positions = _carried_bag_positions(snapshot)
    carried_bags = [snapshot.items[position] for position in bag_positions]
    if any(
        item.definition.item_id == bag.definition.item_id
        for item in carried_bags
    ):
        raise InventoryMutationError(
            "Validated bag unequip requires no matching carried bag"
        )

    target_key = _bag_sort_key(bag)
    for carried in carried_bags:
        if _bag_sort_key(carried) > target_key:
            return carried.offset
    last_bag = carried_bags[-1]
    return last_bag.offset + len(last_bag.raw)


def _validate_carried_bag_position(
    snapshot: InventorySnapshot,
    carried: InventoryItem,
) -> None:
    _carried_bag_positions(snapshot)
    if carried.serialize_type != 2:
        raise InventoryMutationError("Carried item is not a bag")
    _validate_transfer_bag(carried)


def stage_bag_transfer(
    inventory: InventorySnapshot,
    slot_index: int,
    carried_offset: Optional[int] = None,
) -> SavedBagTransfer:
    """Stage the exact validated transfer of a bag to or from one slot."""
    if slot_index < 0 or slot_index >= len(inventory.bag_slots):
        raise InventoryMutationError(
            f"Saved bag slot {slot_index} does not exist"
        )
    slot = inventory.bag_slots[slot_index]
    if slot.item is not None:
        if carried_offset is not None:
            raise InventoryMutationError(
                "Unequipping a bag does not accept a carried-item offset"
            )
        _validate_transfer_bag(slot.item)
        insertion_offset = _bag_insertion_offset(inventory, slot.item)
        return SavedBagTransfer(
            action="unequip",
            slot_index=slot_index,
            slot_offset=slot.offset,
            inventory_offset=insertion_offset,
            item_id=slot.item.definition.item_id,
            raw=slot.raw,
        )

    if carried_offset is None:
        raise InventoryMutationError(
            f"Equipping empty bag slot {slot_index} requires a carried bag"
        )
    matches = [
        item for item in inventory.items if item.offset == carried_offset
    ]
    if len(matches) != 1:
        raise InventoryMutationError(
            "Expected exactly one carried item at offset "
            f"0x{carried_offset:X}, found {len(matches)}"
        )
    carried = matches[0]
    _validate_carried_bag_position(inventory, carried)
    return SavedBagTransfer(
        action="equip",
        slot_index=slot_index,
        slot_offset=slot.offset,
        inventory_offset=carried.offset,
        item_id=carried.definition.item_id,
        raw=carried.raw,
    )


def _verify_bag_transfer(
    before_data: bytes,
    after_data: bytes,
    catalog: ItemCatalog,
    before_inventory: InventorySnapshot,
    change: SavedBagTransfer,
) -> None:
    if len(after_data) != len(before_data):
        raise InventoryMutationError(
            "Bag transfer changed the character file size"
        )
    before_equipment = parse_equipment(
        before_data,
        catalog,
        before_inventory,
    )
    after_inventory = parse_inventory(after_data, catalog)
    after_equipment = parse_equipment(
        after_data,
        catalog,
        after_inventory,
    )
    before_equipment_raw = [slot.raw for slot in before_equipment.slots]
    after_equipment_raw = [slot.raw for slot in after_equipment.slots]
    if after_equipment_raw != before_equipment_raw:
        raise InventoryMutationError(
            "Standard equipment slots changed during bag transfer"
        )

    before_slots = [slot.raw for slot in before_inventory.bag_slots]
    after_slots = [slot.raw for slot in after_inventory.bag_slots]
    expected_slots = list(before_slots)
    expected_slots[change.slot_index] = (
        b"\0" if change.action == "unequip" else change.raw
    )
    if after_slots != expected_slots:
        raise InventoryMutationError(
            "Saved-bag slots do not match the staged transfer"
        )

    before_items = [item.raw for item in before_inventory.items]
    after_items = [item.raw for item in after_inventory.items]
    slot_shift = len(change.raw) - 1
    if change.action == "unequip":
        insertion_position = _inventory_position_for_offset(
            before_inventory,
            change.inventory_offset,
        )
        expected_items = list(before_items)
        expected_items.insert(insertion_position, change.raw)
        expected_offsets = (
            before_inventory.bags_count_offset,
            before_inventory.inventory_count_offset - slot_shift,
            before_inventory.inventory_start - slot_shift,
            before_inventory.inventory_end + 1,
        )
    else:
        removal_position = _inventory_position_for_offset(
            before_inventory,
            change.inventory_offset,
        )
        expected_items = list(before_items)
        del expected_items[removal_position]
        expected_offsets = (
            before_inventory.bags_count_offset,
            before_inventory.inventory_count_offset + slot_shift,
            before_inventory.inventory_start + slot_shift,
            before_inventory.inventory_end - 1,
        )
    if after_items != expected_items:
        raise InventoryMutationError(
            "Carried inventory does not match the staged bag transfer"
        )
    actual_offsets = (
        after_inventory.bags_count_offset,
        after_inventory.inventory_count_offset,
        after_inventory.inventory_start,
        after_inventory.inventory_end,
    )
    if actual_offsets != expected_offsets:
        raise InventoryMutationError(
            "Inventory framing does not match the staged bag transfer"
        )
    if after_data[:before_inventory.bags_count_offset] != before_data[
        :before_inventory.bags_count_offset
    ]:
        raise InventoryMutationError(
            "Bytes before saved bags changed during transfer"
        )


def apply_bag_transfer(
    data: bytes,
    catalog: ItemCatalog,
    change: SavedBagTransfer,
    *,
    expected_source_sha256: str,
) -> bytes:
    """Apply one validated saved-bag transfer with exact checks."""
    source_sha256 = hashlib.sha256(data).hexdigest()
    if source_sha256 != expected_source_sha256:
        raise InventoryMutationError(
            "Inventory source changed after staging; refresh and try again"
        )
    before_inventory = parse_inventory(data, catalog)
    carried_offset = (
        change.inventory_offset if change.action == "equip" else None
    )
    expected = stage_bag_transfer(
        before_inventory,
        change.slot_index,
        carried_offset,
    )
    if change != expected:
        raise InventoryMutationError(
            "Staged bag transfer no longer matches the source"
        )
    if data[-1:] != b"\0":
        raise InventoryMutationError(
            "Character file lacks validated trailing zero padding"
        )

    updated = bytearray(data)
    if change.action == "unequip":
        contraction = len(change.raw) - 1
        updated[change.slot_offset:change.slot_offset + len(change.raw)] = (
            b"\0"
        )
        count_offset = before_inventory.inventory_count_offset - contraction
        struct.pack_into(
            "<i",
            updated,
            count_offset,
            len(before_inventory.items) + 1,
        )
        del updated[-1:]
        insertion_offset = change.inventory_offset - contraction
        updated[insertion_offset:insertion_offset] = change.raw
    elif change.action == "equip":
        expansion = len(change.raw) - 1
        updated[change.slot_offset:change.slot_offset + 1] = change.raw
        count_offset = before_inventory.inventory_count_offset + expansion
        struct.pack_into(
            "<i",
            updated,
            count_offset,
            len(before_inventory.items) - 1,
        )
        inventory_offset = change.inventory_offset + expansion
        del updated[inventory_offset:inventory_offset + len(change.raw)]
        updated.extend(b"\0")
    else:
        raise InventoryMutationError(
            f"Unknown staged bag action {change.action!r}"
        )

    updated_bytes = bytes(updated)
    _verify_bag_transfer(
        data,
        updated_bytes,
        catalog,
        before_inventory,
        change,
    )
    return updated_bytes


def write_bag_transfer(
    path: Path,
    catalog: ItemCatalog,
    change: SavedBagTransfer,
    *,
    expected_source_sha256: str,
    now: Optional[datetime] = None,
    backup_dir: Optional[Path] = None,
) -> Path:
    """Write a validated saved-bag transfer through the atomic backup path."""
    from . import editor

    original = path.read_bytes()
    updated = apply_bag_transfer(
        original,
        catalog,
        change,
        expected_source_sha256=expected_source_sha256,
    )
    return editor._write_updated_character(
        path,
        original,
        updated,
        now=now,
        backup_dir=backup_dir,
    )


def _record_fields(raw: bytes) -> tuple[int, int, int, int, int, int]:
    if len(raw) < BASE_ITEM_RECORD_SIZE:
        raise InventoryMutationError("Serialized item record is truncated")
    return struct.unpack_from("<BiiiBB", raw, 0)


def _record_item_id(raw: bytes) -> int:
    return _record_fields(raw)[1]


def _record_serialize_type(raw: bytes) -> int:
    return _record_fields(raw)[2]


def _record_quantity(raw: bytes) -> int:
    return _record_fields(raw)[3]


def record_with_quantity(raw: bytes, quantity: int) -> bytes:
    """Return one serialized item record with only quantity changed."""
    _validate_quantity(quantity)
    updated = bytearray(raw)
    struct.pack_into("<i", updated, QUANTITY_FIELD_OFFSET, quantity)
    return bytes(updated)


def parse_item_record(
    raw: bytes,
    catalog: ItemCatalog,
    *,
    expected_serialize_type: Optional[int] = None,
) -> InventoryItem:
    """Parse and validate one standalone serialized item record."""
    items, end = _parse_inventory_items(
        raw,
        0,
        1,
        catalog,
        expected_serialize_type=expected_serialize_type,
    )
    if end != len(raw):
        raise InventoryMutationError(
            "Serialized item record has unexpected trailing bytes"
    )
    return items[0]


def _catalog_non_negative_int32(
    definition: ItemDefinition,
    key: str,
) -> int:
    value = definition.attributes.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InventoryMutationError(
            f"Catalog item {definition.item_id} is missing integer {key!r}"
        )
    if value < 0 or value > MAX_SERIALIZED_QUANTITY:
        raise InventoryMutationError(
            f"Catalog item {definition.item_id} has unsupported {key!r} "
            f"value {value}"
        )
    return value


def _default_item_payload(
    definition: ItemDefinition,
    serialize_type: int,
    payload_size: int,
) -> bytes:
    if payload_size == 0:
        return b""
    durability_key = {
        1: "durabilityResistance",
        8: "maxDurability",
    }.get(serialize_type)
    if durability_key is None:
        return bytes(payload_size)
    if payload_size < 4:
        raise InventoryMutationError(
            f"Durable catalog item {definition.item_id} has no payload space"
        )
    durability = _catalog_non_negative_int32(definition, durability_key)
    return struct.pack("<i", durability) + bytes(payload_size - 4)


def default_item_record(
    catalog: ItemCatalog,
    item_id: int,
    quantity: int = 1,
) -> bytes:
    """Build the conservative default serialized record for a catalog item."""
    _validate_quantity(quantity)
    definition = catalog.get(item_id)
    if definition is None:
        raise InventoryMutationError(f"Unknown catalog item {item_id}")
    serialize_type = CATEGORY_SERIALIZE_TYPES.get(definition.category)
    if serialize_type is None:
        raise InventoryMutationError(
            f"Unsupported catalog category {definition.category!r}"
        )
    record_size = SERIALIZED_RECORD_SIZES[serialize_type]
    base = struct.pack(
        "<BiiiBB",
        1,
        item_id,
        serialize_type,
        quantity,
        0,
        0,
    )
    raw = base + _default_item_payload(
        definition,
        serialize_type,
        record_size - len(base),
    )
    parse_item_record(raw, catalog, expected_serialize_type=serialize_type)
    return raw


def _type_order_index(serialize_type: int) -> int:
    try:
        return INVENTORY_TYPE_ORDER.index(serialize_type)
    except ValueError as exc:
        raise InventoryMutationError(
            f"Unsupported inventory serialization type {serialize_type}"
        ) from exc


def _record_sort_key(raw: bytes, catalog: ItemCatalog) -> tuple[int, int, int]:
    item = parse_item_record(raw, catalog)
    return (
        _type_order_index(item.serialize_type),
        item.definition.index,
        item.definition.item_id,
    )


def sort_carried_item_raws(
    raws: Sequence[bytes],
    catalog: ItemCatalog,
) -> tuple[bytes, ...]:
    """Sort carried records in the observed inventory type/catalog order."""
    return tuple(sorted(raws, key=lambda raw: _record_sort_key(raw, catalog)))


def merge_carried_item_raw(
    raws: Sequence[bytes],
    raw: bytes,
    catalog: ItemCatalog,
) -> tuple[bytes, ...]:
    """Add one carried record, merging by item ID and serialize type."""
    incoming = parse_item_record(raw, catalog)
    merged: list[bytes] = []
    did_merge = False
    for existing_raw in raws:
        existing = parse_item_record(existing_raw, catalog)
        if (
            not did_merge
            and existing.definition.item_id == incoming.definition.item_id
            and existing.serialize_type == incoming.serialize_type
        ):
            quantity = existing.quantity + incoming.quantity
            if quantity > MAX_SERIALIZED_QUANTITY:
                raise InventoryMutationError(
                    "Merged inventory quantity exceeds the serialized limit"
                )
            merged.append(record_with_quantity(existing_raw, quantity))
            did_merge = True
        else:
            merged.append(existing_raw)
    if not did_merge:
        merged.append(raw)
    return sort_carried_item_raws(merged, catalog)


def remove_one_from_carried_raw(
    raws: Sequence[bytes],
    position: int,
    catalog: ItemCatalog,
) -> tuple[bytes, bytes]:
    """Remove one item from a carried stack and return the single raw item."""
    if position < 0 or position >= len(raws):
        raise InventoryMutationError("Carried item selection is out of range")
    selected_raw = raws[position]
    selected = parse_item_record(selected_raw, catalog)
    single_raw = record_with_quantity(selected_raw, 1)
    updated = list(raws)
    if selected.quantity == 1:
        del updated[position]
    else:
        updated[position] = record_with_quantity(
            selected_raw,
            selected.quantity - 1,
        )
    return sort_carried_item_raws(updated, catalog), single_raw


def _equipment_slot_spec(
    slot_name: str,
) -> tuple[str, int, Optional[int]]:
    matches = [spec for spec in EQUIPMENT_SLOT_SPECS if spec[0] == slot_name]
    if len(matches) != 1:
        raise InventoryMutationError(f"Unknown equipment slot {slot_name!r}")
    return matches[0]


def equipment_slot_accepts(
    slot_name: str,
    definition: ItemDefinition,
) -> bool:
    """Return whether a catalog item can be equipped in a standard slot."""
    _, serialize_type, gear_category = _equipment_slot_spec(slot_name)
    item_serialize_type = CATEGORY_SERIALIZE_TYPES.get(definition.category)
    if item_serialize_type != serialize_type:
        return False
    if gear_category is None:
        return True
    return definition.attributes.get("category") == gear_category


def _validate_equipment_slot_raw(
    slot_name: str,
    raw: bytes,
    catalog: ItemCatalog,
) -> None:
    _, serialize_type, gear_category = _equipment_slot_spec(slot_name)
    if raw == b"\0":
        return
    item = parse_item_record(
        raw,
        catalog,
        expected_serialize_type=serialize_type,
    )
    if item.quantity != 1:
        raise InventoryMutationError(
            f"Equipped {slot_name} item must have quantity one"
        )
    if (
        gear_category is not None
        and item.definition.attributes.get("category") != gear_category
    ):
        raise InventoryMutationError(
            f"Item {item.definition.item_id} is incompatible with "
            f"the {slot_name} slot"
        )


def _validate_saved_bag_slot_raw(raw: bytes, catalog: ItemCatalog) -> None:
    if raw == b"\0":
        return
    item = parse_item_record(raw, catalog, expected_serialize_type=2)
    if item.quantity != 1:
        raise InventoryMutationError(
            "Equipped saved-bag item must have quantity one"
        )


def inventory_edit_plan_from_snapshots(
    equipment: EquipmentSnapshot,
    inventory: InventorySnapshot,
) -> FullInventoryEditPlan:
    """Create an editable final-state plan from parsed snapshots."""
    if equipment.source_sha256 != inventory.source_sha256:
        raise InventoryMutationError(
            "Equipment and inventory snapshots have different sources"
        )
    return FullInventoryEditPlan(
        source_sha256=inventory.source_sha256,
        equipment_slot_raws=tuple(slot.raw for slot in equipment.slots),
        saved_bag_slot_raws=tuple(slot.raw for slot in inventory.bag_slots),
        carried_item_raws=tuple(item.raw for item in inventory.items),
    )


def _validate_full_inventory_plan(
    plan: FullInventoryEditPlan,
    catalog: ItemCatalog,
    expected_saved_bag_slots: int,
) -> None:
    if len(plan.equipment_slot_raws) != len(EQUIPMENT_SLOT_SPECS):
        raise InventoryMutationError(
            "Inventory edit plan has the wrong equipment slot count"
        )
    for raw, (slot_name, _, _) in zip(
        plan.equipment_slot_raws,
        EQUIPMENT_SLOT_SPECS,
    ):
        _validate_equipment_slot_raw(slot_name, raw, catalog)

    if len(plan.saved_bag_slot_raws) != expected_saved_bag_slots:
        raise InventoryMutationError(
            "Inventory edit plan changed the saved-bag slot count"
        )
    for raw in plan.saved_bag_slot_raws:
        _validate_saved_bag_slot_raw(raw, catalog)

    if not 1 <= len(plan.carried_item_raws) <= MAX_INVENTORY_ITEMS:
        raise InventoryMutationError(
            "Inventory edit plan has an unsupported carried item count"
        )
    for raw in plan.carried_item_raws:
        parse_item_record(raw, catalog)
    if (
        tuple(plan.carried_item_raws)
        != sort_carried_item_raws(plan.carried_item_raws, catalog)
    ):
        raise InventoryMutationError(
            "Carried inventory records are not in the supported order"
        )


def _verify_full_inventory_edit(
    before_data: bytes,
    after_data: bytes,
    catalog: ItemCatalog,
    before_equipment: EquipmentSnapshot,
    before_inventory: InventorySnapshot,
    plan: FullInventoryEditPlan,
) -> None:
    if len(after_data) != len(before_data):
        raise InventoryMutationError(
            "Inventory edit changed the character file size"
        )
    after_inventory = parse_inventory(after_data, catalog)
    after_equipment = parse_equipment(
        after_data,
        catalog,
        after_inventory,
    )
    if tuple(slot.raw for slot in after_equipment.slots) != (
        plan.equipment_slot_raws
    ):
        raise InventoryMutationError(
            "Reparsed equipment slots do not match the staged inventory edit"
        )
    if tuple(slot.raw for slot in after_inventory.bag_slots) != (
        plan.saved_bag_slot_raws
    ):
        raise InventoryMutationError(
            "Reparsed saved-bag slots do not match the staged inventory edit"
        )
    if tuple(item.raw for item in after_inventory.items) != (
        plan.carried_item_raws
    ):
        raise InventoryMutationError(
            "Reparsed carried inventory does not match the staged edit"
        )
    if after_data[:before_equipment.start] != before_data[
        :before_equipment.start
    ]:
        raise InventoryMutationError(
            "Bytes before the inventory region changed"
        )


def apply_full_inventory_edit(
    data: bytes,
    catalog: ItemCatalog,
    plan: FullInventoryEditPlan,
) -> bytes:
    """Rebuild the editable inventory/equipment region from a final state."""
    source_sha256 = hashlib.sha256(data).hexdigest()
    if source_sha256 != plan.source_sha256:
        raise InventoryMutationError(
            "Inventory source changed after staging; refresh and try again"
        )
    before_inventory = parse_inventory(data, catalog)
    before_equipment = parse_equipment(
        data,
        catalog,
        before_inventory,
    )
    _validate_full_inventory_plan(
        plan,
        catalog,
        len(before_inventory.bag_slots),
    )

    new_region = b"".join(plan.equipment_slot_raws)
    new_region += struct.pack("<i", len(plan.saved_bag_slot_raws))
    new_region += b"".join(plan.saved_bag_slot_raws)
    new_region += struct.pack("<i", len(plan.carried_item_raws))
    new_region += b"".join(plan.carried_item_raws)

    old_region = data[before_equipment.start:before_inventory.inventory_end]
    prefix = data[:before_equipment.start]
    suffix = data[before_inventory.inventory_end:]
    growth = len(new_region) - len(old_region)
    if growth > 0:
        if len(suffix) < growth or data[-growth:] != bytes(growth):
            raise InventoryMutationError(
                "Character file lacks validated trailing zero padding"
            )
        adjusted_suffix = suffix[:-growth]
    elif growth < 0:
        adjusted_suffix = suffix + bytes(-growth)
    else:
        adjusted_suffix = suffix

    updated = prefix + new_region + adjusted_suffix
    _verify_full_inventory_edit(
        data,
        updated,
        catalog,
        before_equipment,
        before_inventory,
        plan,
    )
    return updated


def write_full_inventory_edit(
    path: Path,
    catalog: ItemCatalog,
    plan: FullInventoryEditPlan,
    *,
    now: Optional[datetime] = None,
    backup_dir: Optional[Path] = None,
) -> Path:
    """Write a full inventory edit through the atomic backup path."""
    from . import editor

    original = path.read_bytes()
    updated = apply_full_inventory_edit(original, catalog, plan)
    return editor._write_updated_character(
        path,
        original,
        updated,
        now=now,
        backup_dir=backup_dir,
    )


def _verify_quantity_mutation(
    before: InventorySnapshot,
    after: InventorySnapshot,
    changes: Mapping[int, InventoryQuantityChange],
) -> None:
    framing_before = (
        before.bags_count_offset,
        before.inventory_count_offset,
        before.inventory_start,
        before.inventory_end,
    )
    framing_after = (
        after.bags_count_offset,
        after.inventory_count_offset,
        after.inventory_start,
        after.inventory_end,
    )
    if framing_after != framing_before:
        raise InventoryMutationError(
            "Inventory framing changed during quantity verification"
        )
    if before.bag_slots != after.bag_slots:
        raise InventoryMutationError(
            "Saved-bag slots changed during quantity verification"
        )
    if len(before.items) != len(after.items):
        raise InventoryMutationError(
            "Inventory count changed during quantity verification"
        )

    for old_item, new_item in zip(before.items, after.items):
        if old_item.offset != new_item.offset:
            raise InventoryMutationError(
                "Inventory record offsets changed during verification"
            )
        change = changes.get(old_item.offset)
        expected_raw = bytearray(old_item.raw)
        if change is not None:
            struct.pack_into(
                "<i",
                expected_raw,
                QUANTITY_FIELD_OFFSET,
                change.new_quantity,
            )
        if new_item.raw != bytes(expected_raw):
            raise InventoryMutationError(
                "Unexpected inventory bytes changed at record "
                f"0x{old_item.offset:X}"
            )


def _verify_inventory_mutation(
    before_data: bytes,
    after_data: bytes,
    catalog: ItemCatalog,
    before: InventorySnapshot,
    quantity_changes: Mapping[int, InventoryQuantityChange],
    removals: Mapping[int, InventoryItemRemoval],
) -> None:
    after = parse_inventory(after_data, catalog)
    if before.bag_slots != after.bag_slots:
        raise InventoryMutationError(
            "Saved-bag slots changed during inventory verification"
        )
    if (
        before.bags_count_offset != after.bags_count_offset
        or before.inventory_count_offset != after.inventory_count_offset
        or before.inventory_start != after.inventory_start
    ):
        raise InventoryMutationError(
            "Inventory start framing changed during verification"
        )
    if len(after.items) != len(before.items) - len(removals):
        raise InventoryMutationError(
            "Inventory count does not match staged removals"
        )

    expected_items = [
        item for item in before.items if item.offset not in removals
    ]
    if len(expected_items) != len(after.items):
        raise InventoryMutationError(
            "Inventory removal verification lost an unexpected record"
        )
    for old_item, new_item in zip(expected_items, after.items):
        expected_raw = bytearray(old_item.raw)
        change = quantity_changes.get(old_item.offset)
        if change is not None:
            struct.pack_into(
                "<i",
                expected_raw,
                QUANTITY_FIELD_OFFSET,
                change.new_quantity,
            )
        if new_item.raw != bytes(expected_raw):
            raise InventoryMutationError(
                "Unexpected inventory bytes changed for item "
                f"{old_item.definition.item_id}"
            )

    removed_bytes = sum(
        len(removal.expected_raw) for removal in removals.values()
    )
    if after.inventory_end != before.inventory_end - removed_bytes:
        raise InventoryMutationError(
            "Inventory end does not match staged removals"
        )
    old_suffix = before_data[before.inventory_end:]
    new_suffix = after_data[after.inventory_end:]
    if new_suffix[:len(old_suffix)] != old_suffix:
        raise InventoryMutationError(
            "Serialized data after inventory changed during reconstruction"
        )
    if new_suffix[len(old_suffix):] != bytes(removed_bytes):
        raise InventoryMutationError(
            "Inventory reconstruction did not restore zero padding"
        )


def _verify_inventory_addition(
    before_data: bytes,
    after_data: bytes,
    catalog: ItemCatalog,
    before: InventorySnapshot,
    addition: InventoryItemAddition,
) -> None:
    after = parse_inventory(after_data, catalog)
    if before.bag_slots != after.bag_slots:
        raise InventoryMutationError(
            "Saved-bag slots changed during addition verification"
        )
    if (
        before.bags_count_offset != after.bags_count_offset
        or before.inventory_count_offset != after.inventory_count_offset
        or before.inventory_start != after.inventory_start
    ):
        raise InventoryMutationError(
            "Inventory start framing changed during addition verification"
        )
    if len(after.items) != len(before.items) + 1:
        raise InventoryMutationError(
            "Inventory count does not match staged addition"
        )
    expected_raw = (
        before_data[before.inventory_start:addition.insertion_offset]
        + addition.raw
        + before_data[addition.insertion_offset:before.inventory_end]
    )
    if after_data[after.inventory_start:after.inventory_end] != expected_raw:
        raise InventoryMutationError(
            "Inventory records do not match the staged addition"
        )
    if after.inventory_end != before.inventory_end + len(addition.raw):
        raise InventoryMutationError(
            "Inventory end does not match the staged addition"
        )
    old_suffix = before_data[before.inventory_end:]
    new_suffix = after_data[after.inventory_end:]
    if old_suffix[:-len(addition.raw)] != new_suffix:
        raise InventoryMutationError(
            "Serialized suffix changed during addition reconstruction"
        )


def apply_inventory_changes(
    data: bytes,
    catalog: ItemCatalog,
    changes: Sequence[InventoryChange],
    *,
    expected_source_sha256: str,
) -> bytes:
    """Apply verified in-place quantity changes to an exact save snapshot."""
    source_sha256 = hashlib.sha256(data).hexdigest()
    if source_sha256 != expected_source_sha256:
        raise InventoryMutationError(
            "Inventory source changed after staging; refresh and try again"
        )
    before = parse_inventory(data, catalog)
    if not changes:
        raise InventoryMutationError("No inventory changes were staged")

    additions = [
        change
        for change in changes
        if isinstance(change, InventoryItemAddition)
    ]
    if additions and len(changes) != 1:
        raise InventoryMutationError(
            "Validated additions cannot be combined with other changes"
        )
    if len(additions) > 1:
        raise InventoryMutationError(
            "Only one validated addition may be staged"
        )

    items_by_offset = {item.offset: item for item in before.items}
    changes_by_offset: dict[int, InventoryQuantityChange] = {}
    removals_by_offset: dict[int, InventoryItemRemoval] = {}
    updated = bytearray(data)
    for change in changes:
        if isinstance(change, InventoryItemAddition):
            expected = _validated_addition(
                before,
                catalog,
                change.item_id,
            )
            if change != expected:
                raise InventoryMutationError(
                    "Staged addition no longer matches inventory ordering"
                )
            continue
        if (
            change.item_offset in changes_by_offset
            or change.item_offset in removals_by_offset
        ):
            raise InventoryMutationError(
                "Multiple inventory changes target record "
                f"0x{change.item_offset:X}"
            )
        item = items_by_offset.get(change.item_offset)
        if item is None:
            raise InventoryMutationError(
                "No carried item exists at staged offset "
                f"0x{change.item_offset:X}"
            )
        if isinstance(change, InventoryItemRemoval):
            if item.definition.item_id != change.item_id:
                raise InventoryMutationError(
                    f"Staged item ID {change.item_id} does not match "
                    f"{item.definition.item_id} at 0x{item.offset:X}"
                )
            if item.raw != change.expected_raw:
                raise InventoryMutationError(
                    "Staged removal record no longer matches the source"
                )
            if item.definition.item_id not in VALIDATED_REMOVAL_ITEM_IDS:
                raise InventoryMutationError(
                    f"Removal is not validated for item "
                    f"{item.definition.item_id}"
                )
            if item.serialize_type != 3 or item.quantity != 1:
                raise InventoryMutationError(
                    "Validated item removal requires a "
                    "quantity-one consumable"
                )
            removals_by_offset[change.item_offset] = change
            continue

        _validate_quantity(change.expected_quantity)
        _validate_quantity(change.new_quantity)
        if item.definition.item_id != change.item_id:
            raise InventoryMutationError(
                f"Staged item ID {change.item_id} does not match "
                f"{item.definition.item_id} at 0x{item.offset:X}"
            )
        if item.quantity != change.expected_quantity:
            raise InventoryMutationError(
                f"Staged quantity {change.expected_quantity} does not match "
                f"{item.quantity} at 0x{item.offset:X}"
            )
        if change.new_quantity == item.quantity:
            raise InventoryMutationError(
                f"Item {item.definition.item_id} already has quantity "
                f"{change.new_quantity}"
            )
        _validate_supported_quantity_change(item, change.new_quantity)
        changes_by_offset[change.item_offset] = change
        struct.pack_into(
            "<i",
            updated,
            change.item_offset + QUANTITY_FIELD_OFFSET,
            change.new_quantity,
        )

    removed_bytes = sum(
        len(removal.expected_raw)
        for removal in removals_by_offset.values()
    )
    if removed_bytes:
        if data[-removed_bytes:] != bytes(removed_bytes):
            raise InventoryMutationError(
                "Character file lacks validated trailing zero padding"
            )
        struct.pack_into(
            "<i",
            updated,
            before.inventory_count_offset,
            len(before.items) - len(removals_by_offset),
        )
        for offset in sorted(removals_by_offset, reverse=True):
            removal = removals_by_offset[offset]
            del updated[offset:offset + len(removal.expected_raw)]
        updated.extend(bytes(removed_bytes))

    if additions:
        addition = additions[0]
        record_size = len(addition.raw)
        if data[-record_size:] != bytes(record_size):
            raise InventoryMutationError(
                "Character file lacks validated trailing zero padding"
            )
        struct.pack_into(
            "<i",
            updated,
            before.inventory_count_offset,
            len(before.items) + 1,
        )
        del updated[-record_size:]
        updated[
            addition.insertion_offset:addition.insertion_offset
        ] = addition.raw

    updated_bytes = bytes(updated)
    if additions:
        _verify_inventory_addition(
            data,
            updated_bytes,
            catalog,
            before,
            additions[0],
        )
    elif removals_by_offset:
        _verify_inventory_mutation(
            data,
            updated_bytes,
            catalog,
            before,
            changes_by_offset,
            removals_by_offset,
        )
    else:
        after = parse_inventory(updated_bytes, catalog)
        _verify_quantity_mutation(before, after, changes_by_offset)
        allowed_offsets = {
            offset
            for change in changes_by_offset.values()
            for offset in range(
                change.item_offset + QUANTITY_FIELD_OFFSET,
                change.item_offset + QUANTITY_FIELD_OFFSET + 4,
            )
        }
        unexpected_offsets = [
            offset
            for offset, (old_byte, new_byte) in enumerate(
                zip(data, updated_bytes)
            )
            if old_byte != new_byte and offset not in allowed_offsets
        ]
        if unexpected_offsets:
            raise InventoryMutationError(
                "Bytes outside staged quantity fields changed"
            )
    return updated_bytes


def write_inventory_changes(
    path: Path,
    catalog: ItemCatalog,
    changes: Sequence[InventoryChange],
    *,
    expected_source_sha256: str,
    now: Optional[datetime] = None,
    backup_dir: Optional[Path] = None,
) -> Path:
    """Apply quantity changes with the editor's backup and atomic-write path."""
    from . import editor

    original = path.read_bytes()
    updated = apply_inventory_changes(
        original,
        catalog,
        changes,
        expected_source_sha256=expected_source_sha256,
    )
    return editor._write_updated_character(
        path,
        original,
        updated,
        now=now,
        backup_dir=backup_dir,
    )
