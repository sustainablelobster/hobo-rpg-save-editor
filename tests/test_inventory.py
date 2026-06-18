from __future__ import annotations

import json
import hashlib
import struct
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from hobo_rpg_save_editor import inventory


def make_database(
    *records: dict[str, object],
    max_item_id: int = 10,
) -> str:
    database: dict[str, object] = {
        "MaxItemID": max_item_id,
        **{category: [] for category in inventory.ITEM_CATEGORY_KEYS},
    }
    for record in records:
        database_category = record.pop("_database_category", None)
        if database_category is None:
            database_category = record.pop("category")
        category = str(database_category)
        category_records = database[category]
        assert isinstance(category_records, list)
        category_records.append(record)
    return json.dumps(database)


def make_localization(
    entries: tuple[tuple[int, str], ...] = ((1, "Bandage"), (2, "Heals")),
) -> str:
    return json.dumps(
        {
            "keys": [key for key, _ in entries],
            "values": [value for _, value in entries],
        }
    )


def item_record(
    item_id: int = 3,
    *,
    category: str = "consumables",
    title_key: int = 1,
    index: int = 20000,
    **attributes: object,
) -> dict[str, object]:
    record: dict[str, object] = {
        "category": category,
        "id": item_id,
        "index": index,
        "titleKey": title_key,
        "descriptionKey": 2,
        "weight": 0.1,
    }
    if category == "gears":
        record["durabilityResistance"] = 50
    elif category == "weapons":
        record["maxDurability"] = 25
    record.update(attributes)
    return record


def serialized_item(
    item_id: int,
    serialize_type: int,
    quantity: int = 1,
    *,
    flag_a: int = 0,
    flag_b: int = 0,
) -> bytes:
    size = inventory.SERIALIZED_RECORD_SIZES[serialize_type]
    base = struct.pack(
        "<BiiiBB",
        1,
        item_id,
        serialize_type,
        quantity,
        flag_a,
        flag_b,
    )
    return base + bytes(size - len(base))


def make_inventory_catalog() -> inventory.ItemCatalog:
    records = [
        {
            **item_record(10, category="gears"),
            "_database_category": "gears",
            "category": 0,
        },
        {
            **item_record(11, category="gears"),
            "_database_category": "gears",
            "category": 1,
        },
        {
            **item_record(12, category="gears"),
            "_database_category": "gears",
            "category": 2,
        },
        {
            **item_record(13, category="gears"),
            "_database_category": "gears",
            "category": 3,
        },
        item_record(2, category="bags"),
        item_record(3, category="consumables", index=20000),
        item_record(4, category="hideoutInteriors"),
        item_record(5, category="scraps"),
        item_record(6, category="sleepingBags"),
        item_record(7, category="weapons"),
        item_record(14, category="weapons"),
        item_record(8, category="companions"),
        item_record(9, category="consumables", index=20013),
        item_record(1, category="consumables", index=20008),
    ]
    return inventory.parse_item_catalog(
        make_database(*records, max_item_id=14),
        make_localization(),
    )


def make_inventory_character() -> bytes:
    data = bytearray(b"\xff" * 0x200)
    data += struct.pack("<i", 1)
    data += serialized_item(2, 2)
    data += struct.pack("<i", 10)
    for item_id, serialize_type in (
        (14, 8),
        (10, 1),
        (2, 2),
        (3, 3),
        (1, 3),
        (9, 3),
        (4, 4),
        (5, 5),
        (6, 7),
        (8, 9),
    ):
        data += serialized_item(
            item_id,
            serialize_type,
            quantity=item_id,
            flag_a=0 if item_id == 1 else item_id % 2,
        )
    data += b"\0" * 64
    return bytes(data)


def make_equipment_character() -> bytes:
    base = make_inventory_character()
    equipment = bytearray()
    for item_id in (10, 11, 12, 13):
        equipment += serialized_item(item_id, 1)
    equipment += b"\0"
    equipment += serialized_item(7, 8)
    return base[:0x170] + bytes(equipment) + base[0x200:]


def make_gear_transfer_character(*, equipped: bool) -> bytes:
    data = bytearray(b"\xff" * 0x170)
    data += serialized_item(10, 1) if equipped else b"\0"
    for item_id in (11, 12, 13):
        data += serialized_item(item_id, 1)
    data += b"\0"
    data += serialized_item(7, 8)
    data += struct.pack("<i", 0)

    carried = [(14, 8)]
    if not equipped:
        carried.append((10, 1))
    carried.extend(((2, 2), (3, 3)))
    data += struct.pack("<i", len(carried))
    for item_id, serialize_type in carried:
        data += serialized_item(item_id, serialize_type)
    data += bytes(0x500 - len(data))
    return bytes(data)


def make_bag_transfer_catalog() -> inventory.ItemCatalog:
    records = [
        {
            **item_record(10, category="gears"),
            "_database_category": "gears",
            "category": 0,
        },
        {
            **item_record(11, category="gears"),
            "_database_category": "gears",
            "category": 1,
        },
        {
            **item_record(12, category="gears"),
            "_database_category": "gears",
            "category": 2,
        },
        {
            **item_record(13, category="gears"),
            "_database_category": "gears",
            "category": 3,
        },
        item_record(15, category="bags", index=100),
        item_record(16, category="bags", index=200),
        item_record(17, category="bags", index=300),
        item_record(3, category="consumables", index=20000),
        item_record(7, category="weapons"),
        item_record(14, category="weapons"),
    ]
    return inventory.parse_item_catalog(
        make_database(*records, max_item_id=17),
        make_localization(),
    )


def make_bag_transfer_character(*, equipped: bool) -> bytes:
    data = bytearray(b"\xff" * 0x170)
    for item_id in (10, 11, 12, 13):
        data += serialized_item(item_id, 1)
    data += b"\0"
    data += serialized_item(7, 8)
    data += struct.pack("<i", 2)
    data += serialized_item(16, 2) if equipped else b"\0"
    data += serialized_item(15, 2)

    carried = [(14, 8), (15, 2)]
    if not equipped:
        carried.append((16, 2))
    carried.extend(((17, 2), (3, 3)))
    data += struct.pack("<i", len(carried))
    for item_id, serialize_type in carried:
        data += serialized_item(item_id, serialize_type)
    data += bytes(0x500 - len(data))
    return bytes(data)


class DefaultItemRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = make_inventory_catalog()

    def test_builds_full_durability_payloads_for_gear_and_weapons(self) -> None:
        gear_raw = inventory.default_item_record(self.catalog, 11, 2)
        gear = inventory.parse_item_record(gear_raw, self.catalog)
        self.assertEqual(gear.quantity, 2)
        self.assertEqual(struct.unpack("<i", gear.payload[:4])[0], 50)
        self.assertEqual(gear.payload[4:], b"\0" * 12)

        weapon_raw = inventory.default_item_record(self.catalog, 7)
        weapon = inventory.parse_item_record(weapon_raw, self.catalog)
        self.assertEqual(struct.unpack("<i", weapon.payload)[0], 25)

        interior_raw = inventory.default_item_record(self.catalog, 4)
        interior = inventory.parse_item_record(interior_raw, self.catalog)
        self.assertEqual(interior.payload, b"\0" * 4)

    def test_rejects_durable_defaults_without_integer_catalog_value(self) -> None:
        bad_gear = {
            **item_record(
                10,
                category="gears",
                durabilityResistance="full",
            ),
            "_database_category": "gears",
            "category": 0,
        }
        gear_catalog = inventory.parse_item_catalog(
            make_database(bad_gear),
            make_localization(),
        )
        with self.assertRaisesRegex(
            inventory.InventoryMutationError,
            "durabilityResistance",
        ):
            inventory.default_item_record(gear_catalog, 10)

        bad_weapon = item_record(7, category="weapons")
        del bad_weapon["maxDurability"]
        weapon_catalog = inventory.parse_item_catalog(
            make_database(bad_weapon),
            make_localization(),
        )
        with self.assertRaisesRegex(
            inventory.InventoryMutationError,
            "maxDurability",
        ):
            inventory.default_item_record(weapon_catalog, 7)


class CatalogParsingTests(unittest.TestCase):
    def test_parses_categories_localization_and_raw_attributes(self) -> None:
        catalog = inventory.parse_item_catalog(
            make_database(
                item_record(),
                item_record(4, category="scraps", title_key=0),
            ),
            make_localization(),
            unity_version="2020.3.35f1",
        )

        self.assertEqual(catalog.max_item_id, 10)
        self.assertEqual(len(catalog.items), 2)
        self.assertEqual(catalog.get(3).name, "Bandage")
        self.assertEqual(catalog.get(3).description, "Heals")
        self.assertEqual(catalog.get(3).attributes["weight"], 0.1)
        self.assertEqual(catalog.get(4).name, "Item 4")
        self.assertEqual(catalog.category_counts()["consumables"], 1)
        self.assertEqual(catalog.category_counts()["scraps"], 1)
        self.assertEqual(catalog.unity_version, "2020.3.35f1")

    def test_rejects_duplicate_item_ids_across_categories(self) -> None:
        with self.assertRaisesRegex(
            inventory.CatalogError,
            "duplicate item ID 3",
        ):
            inventory.parse_item_catalog(
                make_database(
                    item_record(),
                    item_record(3, category="scraps"),
                ),
                make_localization(),
            )

    def test_rejects_missing_category_and_bad_localization_shape(self) -> None:
        database = json.loads(make_database(item_record()))
        del database["weapons"]
        with self.assertRaisesRegex(inventory.CatalogError, "weapons"):
            inventory.parse_item_catalog(
                json.dumps(database),
                make_localization(),
            )

        with self.assertRaisesRegex(inventory.CatalogError, "different lengths"):
            inventory.parse_item_catalog(
                make_database(item_record()),
                json.dumps({"keys": [1], "values": []}),
            )

    def test_rejects_ids_outside_declared_range(self) -> None:
        with self.assertRaisesRegex(inventory.CatalogError, "outside"):
            inventory.parse_item_catalog(
                make_database(item_record(11)),
                make_localization(),
            )


class CatalogLoadingTests(unittest.TestCase):
    def test_loads_exact_text_assets_through_pinned_unitypy(self) -> None:
        database_text = make_database(item_record())
        localization_text = make_localization()

        class FakeReader:
            type = SimpleNamespace(name="TextAsset")
            assets_file = SimpleNamespace(unity_version="2020.3.35f1")

            def __init__(self, script: str) -> None:
                self.script = script

            def parse_as_object(self) -> object:
                return SimpleNamespace(m_Script=self.script)

        class FakePointer:
            def __init__(self, script: str) -> None:
                self.reader = FakeReader(script)

            def deref(self) -> FakeReader:
                return self.reader

        environment = SimpleNamespace(
            container={
                inventory.ITEM_DATABASE_ASSET_PATH: FakePointer(database_text),
                inventory.ENGLISH_ITEMS_ASSET_PATH: FakePointer(
                    localization_text
                ),
            }
        )
        unitypy = SimpleNamespace(
            __version__="1.25.0",
            load=mock.Mock(return_value=environment),
        )

        with tempfile.TemporaryDirectory() as temp:
            game_dir = Path(temp)
            bundle_path = game_dir / inventory.RESOURCE_BUNDLE_PATH
            bundle_path.parent.mkdir(parents=True)
            bundle_path.write_bytes(b"synthetic bundle")

            with mock.patch.dict(sys.modules, {"UnityPy": unitypy}):
                catalog = inventory.load_item_catalog(game_dir)

        unitypy.load.assert_called_once_with(str(bundle_path.absolute()))
        self.assertEqual(catalog.get(3).name, "Bandage")
        self.assertEqual(catalog.bundle_path, bundle_path.absolute())

    def test_rejects_unpinned_unitypy_minor_version(self) -> None:
        unitypy = SimpleNamespace(__version__="1.26.0")
        with mock.patch.dict(sys.modules, {"UnityPy": unitypy}):
            with self.assertRaisesRegex(
                inventory.CatalogError,
                "expected 1.25.x",
            ):
                inventory._unitypy_module()


class InventoryParsingTests(unittest.TestCase):
    def make_catalog(self) -> inventory.ItemCatalog:
        return make_inventory_catalog()

    def make_character(self) -> bytes:
        return make_inventory_character()

    def test_locates_bounded_inventory_and_preserves_raw_payloads(self) -> None:
        data = self.make_character()
        snapshot = inventory.parse_inventory(data, self.make_catalog())

        self.assertEqual(snapshot.bags_count_offset, 0x200)
        self.assertEqual(snapshot.inventory_count_offset, 0x213)
        self.assertEqual(len(snapshot.bag_slots), 1)
        self.assertEqual(len(snapshot.bags), 1)
        self.assertEqual(len(snapshot.items), 10)
        self.assertEqual(snapshot.items[0].definition.category, "weapons")
        self.assertEqual(snapshot.items[0].quantity, 14)
        self.assertEqual(len(snapshot.items[1].payload), 16)
        self.assertEqual(len(snapshot.items[6].payload), 4)
        self.assertEqual(
            snapshot.items[1].raw,
            data[
                snapshot.items[1].offset:
                snapshot.items[1].offset
                + inventory.SERIALIZED_RECORD_SIZES[1]
            ],
        )

    def test_parses_empty_saved_bag_slots(self) -> None:
        data = bytearray(b"\xff" * 0x200)
        data += struct.pack("<i", 3)
        data += serialized_item(2, 2)
        data += b"\0"
        data += serialized_item(2, 2, quantity=2)
        data += struct.pack("<i", 1)
        data += serialized_item(3, 3)
        data += b"\0" * 64

        snapshot = inventory.parse_inventory(bytes(data), self.make_catalog())

        self.assertEqual(snapshot.bags_count_offset, 0x200)
        self.assertEqual(len(snapshot.bag_slots), 3)
        self.assertEqual(len(snapshot.bags), 2)
        self.assertIsNotNone(snapshot.bag_slots[0].item)
        self.assertIsNone(snapshot.bag_slots[1].item)
        self.assertEqual(snapshot.bag_slots[1].offset, 0x213)
        self.assertEqual(snapshot.bag_slots[2].item.quantity, 2)
        self.assertEqual(snapshot.inventory_count_offset, 0x223)
        self.assertEqual(snapshot.inventory_start, 0x227)

    def test_rejects_unknown_or_truncated_inventory_records(self) -> None:
        data = bytearray(self.make_character())
        first_item = 0x217
        struct.pack_into("<i", data, first_item + 5, 6)
        with self.assertRaisesRegex(
            inventory.InventoryFormatError,
            "exactly one",
        ):
            inventory.parse_inventory(bytes(data), self.make_catalog())

        with self.assertRaises(inventory.InventoryFormatError):
            inventory.parse_inventory(
                self.make_character()[:0x220],
                self.make_catalog(),
            )

    def test_rejects_ambiguous_inventory_regions(self) -> None:
        first = self.make_character()
        second = self.make_character()[0x200:]
        data = first + b"\xff" * 16 + second

        with self.assertRaisesRegex(
            inventory.InventoryFormatError,
            "found 2",
        ):
            inventory.parse_inventory(data, self.make_catalog())


class EquipmentParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = make_inventory_catalog()
        self.data = make_equipment_character()
        self.inventory = inventory.parse_inventory(
            self.data,
            self.catalog,
        )

    def test_parses_six_slots_backwards_from_saved_bags(self) -> None:
        equipment = inventory.parse_equipment(
            self.data,
            self.catalog,
            self.inventory,
        )

        self.assertEqual(equipment.start, 0x170)
        self.assertEqual(equipment.end, 0x200)
        self.assertEqual(
            [slot.name for slot in equipment.slots],
            [
                "hat",
                "jacket",
                "trousers",
                "shoes",
                "companion",
                "weapon",
            ],
        )
        self.assertEqual(
            [
                slot.item.definition.item_id if slot.item else None
                for slot in equipment.slots
            ],
            [10, 11, 12, 13, None, 7],
        )
        self.assertEqual(equipment.get("companion").raw, b"\0")

    def test_rejects_wrong_slot_category(self) -> None:
        data = bytearray(self.data)
        struct.pack_into("<i", data, 0x170 + 1, 11)

        with self.assertRaisesRegex(
            inventory.InventoryFormatError,
            "equipped hat",
        ):
            inventory.parse_equipment(
                bytes(data),
                self.catalog,
            )


class InventoryMutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = make_inventory_catalog()
        self.original = make_inventory_character()
        self.snapshot = inventory.parse_inventory(
            self.original,
            self.catalog,
        )
        self.target = next(
            item
            for item in self.snapshot.items
            if item.definition.item_id == 3
        )

    def test_stages_and_applies_only_the_quantity_field(self) -> None:
        change = inventory.stage_quantity_change(
            self.snapshot,
            self.target.offset,
            2,
        )

        updated = inventory.apply_inventory_changes(
            self.original,
            self.catalog,
            [change],
            expected_source_sha256=self.snapshot.source_sha256,
        )

        expected = bytearray(self.original)
        struct.pack_into(
            "<i",
            expected,
            self.target.offset + inventory.QUANTITY_FIELD_OFFSET,
            2,
        )
        self.assertEqual(updated, bytes(expected))
        reparsed = inventory.parse_inventory(updated, self.catalog)
        changed_item = next(
            item
            for item in reparsed.items
            if item.offset == self.target.offset
        )
        self.assertEqual(changed_item.quantity, 2)
        self.assertEqual(changed_item.payload, self.target.payload)

    def test_applies_multiple_quantities_without_changing_framing(self) -> None:
        second = next(
            item
            for item in self.snapshot.items
            if item.definition.item_id == 9
        )
        changes = [
            inventory.stage_quantity_change(
                self.snapshot,
                self.target.offset,
                2,
            ),
            inventory.stage_quantity_change(
                self.snapshot,
                second.offset,
                8,
            ),
        ]

        updated = inventory.apply_inventory_changes(
            self.original,
            self.catalog,
            changes,
            expected_source_sha256=self.snapshot.source_sha256,
        )
        reparsed = inventory.parse_inventory(updated, self.catalog)

        self.assertEqual(
            (
                reparsed.bags_count_offset,
                reparsed.inventory_count_offset,
                reparsed.inventory_start,
                reparsed.inventory_end,
            ),
            (
                self.snapshot.bags_count_offset,
                self.snapshot.inventory_count_offset,
                self.snapshot.inventory_start,
                self.snapshot.inventory_end,
            ),
        )
        self.assertEqual(
            {
                item.offset: item.quantity
                for item in reparsed.items
                if item.offset in {self.target.offset, second.offset}
            },
            {self.target.offset: 2, second.offset: 8},
        )

    def test_rejects_invalid_noop_duplicate_and_stale_changes(self) -> None:
        for new_quantity in (0, -1, inventory.MAX_SERIALIZED_QUANTITY + 1):
            with self.subTest(new_quantity=new_quantity):
                with self.assertRaises(inventory.InventoryMutationError):
                    inventory.stage_quantity_change(
                        self.snapshot,
                        self.target.offset,
                        new_quantity,
                    )

        with self.assertRaisesRegex(
            inventory.InventoryMutationError,
            "already has quantity",
        ):
            inventory.stage_quantity_change(
                self.snapshot,
                self.target.offset,
                self.target.quantity,
            )

        change = inventory.stage_quantity_change(
            self.snapshot,
            self.target.offset,
            2,
        )
        with self.assertRaisesRegex(
            inventory.InventoryMutationError,
            "Multiple inventory changes",
        ):
            inventory.apply_inventory_changes(
                self.original,
                self.catalog,
                [change, change],
                expected_source_sha256=self.snapshot.source_sha256,
            )
        with self.assertRaisesRegex(
            inventory.InventoryMutationError,
            "source changed",
        ):
            inventory.apply_inventory_changes(
                self.original,
                self.catalog,
                [change],
                expected_source_sha256=hashlib.sha256(b"stale").hexdigest(),
            )
        with self.assertRaisesRegex(
            inventory.InventoryMutationError,
            "not validated",
        ):
            inventory.stage_quantity_change(
                self.snapshot,
                self.target.offset,
                self.target.quantity + 1,
            )
        with self.assertRaisesRegex(
            inventory.InventoryMutationError,
            "consumable",
        ):
            inventory.stage_quantity_change(
                self.snapshot,
                self.snapshot.items[0].offset,
                self.snapshot.items[0].quantity - 1,
            )

    def test_allows_only_validated_single_scrap_food_increase(self) -> None:
        catalog = inventory.parse_item_catalog(
            make_database(
                item_record(10, category="gears"),
                item_record(2, category="bags"),
                item_record(3, category="consumables"),
                item_record(4, category="hideoutInteriors"),
                item_record(5, category="scraps"),
                item_record(6, category="sleepingBags"),
                item_record(7, category="weapons"),
                item_record(14, category="weapons"),
                item_record(8, category="companions"),
                item_record(9, category="consumables"),
                item_record(1, category="consumables"),
                item_record(307, category="consumables"),
                max_item_id=307,
            ),
            make_localization(),
        )
        data = bytearray(self.original)
        struct.pack_into("<i", data, self.target.offset + 1, 307)
        struct.pack_into(
            "<i",
            data,
            self.target.offset + inventory.QUANTITY_FIELD_OFFSET,
            7,
        )
        snapshot = inventory.parse_inventory(bytes(data), catalog)
        target = next(
            item
            for item in snapshot.items
            if item.definition.item_id == 307
        )

        change = inventory.stage_quantity_change(
            snapshot,
            target.offset,
            8,
        )
        updated = inventory.apply_inventory_changes(
            bytes(data),
            catalog,
            [change],
            expected_source_sha256=snapshot.source_sha256,
        )
        changed = inventory.parse_inventory(updated, catalog)
        self.assertEqual(
            next(
                item.quantity
                for item in changed.items
                if item.definition.item_id == 307
            ),
            8,
        )

        limit_data = bytearray(data)
        struct.pack_into(
            "<i",
            limit_data,
            target.offset + inventory.QUANTITY_FIELD_OFFSET,
            8,
        )
        limit_snapshot = inventory.parse_inventory(
            bytes(limit_data),
            catalog,
        )
        with self.assertRaisesRegex(
            inventory.InventoryMutationError,
            "exceeds validated quantity 8",
        ):
            inventory.stage_quantity_change(
                limit_snapshot,
                target.offset,
                9,
            )
        with self.assertRaisesRegex(
            inventory.InventoryMutationError,
            "exactly one",
        ):
            inventory.stage_quantity_change(
                snapshot,
                target.offset,
                9,
            )

    def test_removes_only_validated_quantity_one_roll_and_shifts_suffix(
        self,
    ) -> None:
        roll = next(
            item
            for item in self.snapshot.items
            if item.definition.item_id == 1
        )
        removal = inventory.stage_item_removal(
            self.snapshot,
            roll.offset,
        )
        expected = bytearray(self.original)
        struct.pack_into(
            "<i",
            expected,
            self.snapshot.inventory_count_offset,
            len(self.snapshot.items) - 1,
        )
        del expected[
            roll.offset:
            roll.offset + len(roll.raw)
        ]
        expected.extend(bytes(len(roll.raw)))

        updated = inventory.apply_inventory_changes(
            self.original,
            self.catalog,
            [removal],
            expected_source_sha256=self.snapshot.source_sha256,
        )

        self.assertEqual(updated, bytes(expected))
        reparsed = inventory.parse_inventory(updated, self.catalog)
        self.assertEqual(len(reparsed.items), len(self.snapshot.items) - 1)
        self.assertNotIn(
            roll.definition.item_id,
            [item.definition.item_id for item in reparsed.items],
        )
        self.assertEqual(
            reparsed.inventory_end,
            self.snapshot.inventory_end - len(roll.raw),
        )
        self.assertEqual(len(updated), len(self.original))

    def test_rejects_unvalidated_or_non_quantity_one_removal(self) -> None:
        unvalidated = next(
            item
            for item in self.snapshot.items
            if item.definition.item_id == 9
        )
        with self.assertRaisesRegex(
            inventory.InventoryMutationError,
            "not validated",
        ):
            inventory.stage_item_removal(
                self.snapshot,
                unvalidated.offset,
            )

        roll = next(
            item
            for item in self.snapshot.items
            if item.definition.item_id == 1
        )
        data = bytearray(self.original)
        struct.pack_into(
            "<i",
            data,
            roll.offset + inventory.QUANTITY_FIELD_OFFSET,
            2,
        )
        snapshot = inventory.parse_inventory(bytes(data), self.catalog)
        with self.assertRaisesRegex(
            inventory.InventoryMutationError,
            "quantity-one",
        ):
            inventory.stage_item_removal(
                snapshot,
                roll.offset,
            )

    def test_adds_validated_roll_at_catalog_order_and_shifts_suffix(
        self,
    ) -> None:
        roll = next(
            item
            for item in self.snapshot.items
            if item.definition.item_id == 1
        )
        removal = inventory.stage_item_removal(
            self.snapshot,
            roll.offset,
        )
        without_roll = inventory.apply_inventory_changes(
            self.original,
            self.catalog,
            [removal],
            expected_source_sha256=self.snapshot.source_sha256,
        )
        absent = inventory.parse_inventory(without_roll, self.catalog)

        addition = inventory.stage_item_addition(
            absent,
            self.catalog,
            1,
        )
        restored = inventory.apply_inventory_changes(
            without_roll,
            self.catalog,
            [addition],
            expected_source_sha256=absent.source_sha256,
        )

        self.assertEqual(restored, self.original)
        self.assertEqual(addition.insertion_offset, roll.offset)
        self.assertEqual(addition.previous_item_id, 3)
        self.assertEqual(addition.next_item_id, 9)

    def test_rejects_unvalidated_duplicate_and_mixed_additions(self) -> None:
        with self.assertRaisesRegex(
            inventory.InventoryMutationError,
            "already exists",
        ):
            inventory.stage_item_addition(
                self.snapshot,
                self.catalog,
                1,
            )
        with self.assertRaisesRegex(
            inventory.InventoryMutationError,
            "not validated",
        ):
            inventory.stage_item_addition(
                self.snapshot,
                self.catalog,
                3,
            )

        roll = next(
            item
            for item in self.snapshot.items
            if item.definition.item_id == 1
        )
        without_roll = inventory.apply_inventory_changes(
            self.original,
            self.catalog,
            [inventory.stage_item_removal(self.snapshot, roll.offset)],
            expected_source_sha256=self.snapshot.source_sha256,
        )
        absent = inventory.parse_inventory(without_roll, self.catalog)
        addition = inventory.stage_item_addition(
            absent,
            self.catalog,
            1,
        )
        quantity = inventory.stage_quantity_change(
            absent,
            absent.items[3].offset,
            2,
        )
        with self.assertRaisesRegex(
            inventory.InventoryMutationError,
            "cannot be combined",
        ):
            inventory.apply_inventory_changes(
                without_roll,
                self.catalog,
                [addition, quantity],
                expected_source_sha256=absent.source_sha256,
            )

    def test_write_creates_verified_backup_and_rejects_stale_source(
        self,
    ) -> None:
        change = inventory.stage_quantity_change(
            self.snapshot,
            self.target.offset,
            2,
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / "character_ls"
            backup_dir = root / "backups"
            character.write_bytes(self.original)

            backup = inventory.write_inventory_changes(
                character,
                self.catalog,
                [change],
                expected_source_sha256=self.snapshot.source_sha256,
                now=datetime(2026, 6, 13, 15, 0, 0),
                backup_dir=backup_dir,
            )

            self.assertEqual(backup.read_bytes(), self.original)
            self.assertEqual(
                backup.name,
                "character_ls.bak-20260613-150000",
            )
            self.assertEqual(
                inventory.read_inventory(
                    character,
                    self.catalog,
                ).items[3].quantity,
                2,
            )

            stale_snapshot = inventory.read_inventory(
                character,
                self.catalog,
            )
            character.write_bytes(self.original)
            stale_change = inventory.stage_quantity_change(
                stale_snapshot,
                self.target.offset,
                1,
            )
            with self.assertRaisesRegex(
                inventory.InventoryMutationError,
                "source changed",
            ):
                inventory.write_inventory_changes(
                    character,
                    self.catalog,
                    [stale_change],
                    expected_source_sha256=stale_snapshot.source_sha256,
                    backup_dir=backup_dir,
                )
            self.assertEqual(character.read_bytes(), self.original)
            self.assertEqual(len(list(backup_dir.iterdir())), 1)


class EquipmentMutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = make_inventory_catalog()
        self.original = make_equipment_character()
        self.inventory = inventory.parse_inventory(
            self.original,
            self.catalog,
        )
        self.equipment = inventory.parse_equipment(
            self.original,
            self.catalog,
            self.inventory,
        )
        self.carried = next(
            item
            for item in self.inventory.items
            if item.definition.item_id == 14
        )

    def test_swaps_complete_weapon_records_in_place(self) -> None:
        with mock.patch.object(
            inventory,
            "VALIDATED_WEAPON_SWAP_PAIRS",
            {frozenset({7, 14})},
        ):
            change = inventory.stage_weapon_swap(
                self.equipment,
                self.inventory,
                self.carried.offset,
            )
            updated = inventory.apply_weapon_swap(
                self.original,
                self.catalog,
                change,
                expected_source_sha256=self.inventory.source_sha256,
            )

        expected = bytearray(self.original)
        weapon = self.equipment.get("weapon")
        expected[
            weapon.offset:weapon.offset + len(weapon.raw)
        ] = self.carried.raw
        expected[
            self.carried.offset:
            self.carried.offset + len(self.carried.raw)
        ] = weapon.raw
        self.assertEqual(updated, bytes(expected))

        updated_inventory = inventory.parse_inventory(
            updated,
            self.catalog,
        )
        updated_equipment = inventory.parse_equipment(
            updated,
            self.catalog,
            updated_inventory,
        )
        self.assertEqual(
            updated_equipment.get("weapon").item.definition.item_id,
            14,
        )
        self.assertEqual(updated_inventory.items[0].definition.item_id, 7)

    def test_rejects_unvalidated_and_stale_weapon_swaps(self) -> None:
        with self.assertRaisesRegex(
            inventory.InventoryMutationError,
            "not validated",
        ):
            inventory.stage_weapon_swap(
                self.equipment,
                self.inventory,
                self.carried.offset,
            )

        with mock.patch.object(
            inventory,
            "VALIDATED_WEAPON_SWAP_PAIRS",
            {frozenset({7, 14})},
        ):
            change = inventory.stage_weapon_swap(
                self.equipment,
                self.inventory,
                self.carried.offset,
            )
            with self.assertRaisesRegex(
                inventory.InventoryMutationError,
                "source changed",
            ):
                inventory.apply_weapon_swap(
                    self.original,
                    self.catalog,
                    change,
                    expected_source_sha256=hashlib.sha256(b"stale").hexdigest(),
                )

    def test_write_weapon_swap_creates_verified_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / "character_ls"
            character.write_bytes(self.original)
            with mock.patch.object(
                inventory,
                "VALIDATED_WEAPON_SWAP_PAIRS",
                {frozenset({7, 14})},
            ):
                change = inventory.stage_weapon_swap(
                    self.equipment,
                    self.inventory,
                    self.carried.offset,
                )
                backup = inventory.write_weapon_swap(
                    character,
                    self.catalog,
                    change,
                    expected_source_sha256=self.inventory.source_sha256,
                    now=datetime(2026, 6, 13, 21, 0, 0),
                    backup_dir=root / "backups",
                )

            self.assertEqual(backup.read_bytes(), self.original)
            self.assertEqual(
                backup.name,
                "character_ls.bak-20260613-210000",
            )
            updated_inventory = inventory.read_inventory(
                character,
                self.catalog,
            )
            updated_equipment = inventory.read_equipment(
                character,
                self.catalog,
            )
            self.assertEqual(
                updated_equipment.get("weapon").item.definition.item_id,
                14,
            )
            self.assertEqual(
                updated_inventory.items[0].definition.item_id,
                7,
            )


class GearTransferMutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = make_inventory_catalog()
        self.equipped = make_gear_transfer_character(equipped=True)
        self.unequipped = make_gear_transfer_character(equipped=False)

    def snapshots(
        self,
        data: bytes,
    ) -> tuple[inventory.InventorySnapshot, inventory.EquipmentSnapshot]:
        carried = inventory.parse_inventory(data, self.catalog)
        equipment = inventory.parse_equipment(
            data,
            self.catalog,
            carried,
        )
        return carried, equipment

    def test_unequips_and_reequips_complete_gear_record(self) -> None:
        carried, equipment = self.snapshots(self.equipped)
        with mock.patch.object(
            inventory,
            "VALIDATED_GEAR_TRANSFERS",
            {("hat", 10)},
        ):
            unequip = inventory.stage_gear_transfer(
                equipment,
                carried,
                "hat",
            )
            updated = inventory.apply_gear_transfer(
                self.equipped,
                self.catalog,
                unequip,
                expected_source_sha256=carried.source_sha256,
            )
            self.assertEqual(updated, self.unequipped)

            carried, equipment = self.snapshots(updated)
            scarf = next(
                item
                for item in carried.items
                if item.definition.item_id == 10
            )
            equip = inventory.stage_gear_transfer(
                equipment,
                carried,
                "hat",
                scarf.offset,
            )
            restored = inventory.apply_gear_transfer(
                updated,
                self.catalog,
                equip,
                expected_source_sha256=carried.source_sha256,
            )

        self.assertEqual(restored, self.equipped)

    def test_rejects_unvalidated_order_and_stale_source(self) -> None:
        carried, equipment = self.snapshots(self.equipped)
        with self.assertRaisesRegex(
            inventory.InventoryMutationError,
            "not validated",
        ):
            inventory.stage_gear_transfer(
                equipment,
                carried,
                "hat",
            )

        with mock.patch.object(
            inventory,
            "VALIDATED_GEAR_TRANSFERS",
            {("hat", 10)},
        ):
            change = inventory.stage_gear_transfer(
                equipment,
                carried,
                "hat",
            )
            with self.assertRaisesRegex(
                inventory.InventoryMutationError,
                "source changed",
            ):
                inventory.apply_gear_transfer(
                    self.equipped,
                    self.catalog,
                    change,
                    expected_source_sha256=hashlib.sha256(b"stale").hexdigest(),
                )

            bad_order = bytearray(self.equipped)
            inventory_start = carried.inventory_start
            weapon_size = inventory.SERIALIZED_RECORD_SIZES[8]
            bag_size = inventory.SERIALIZED_RECORD_SIZES[2]
            weapon = bad_order[
                inventory_start:inventory_start + weapon_size
            ]
            bag = bad_order[
                inventory_start + weapon_size:
                inventory_start + weapon_size + bag_size
            ]
            bad_order[
                inventory_start:
                inventory_start + weapon_size + bag_size
            ] = bag + weapon
            bad_carried, bad_equipment = self.snapshots(bytes(bad_order))
            with self.assertRaisesRegex(
                inventory.InventoryMutationError,
                "weapon prefix",
            ):
                inventory.stage_gear_transfer(
                    bad_equipment,
                    bad_carried,
                    "hat",
                )

    def test_write_gear_transfer_creates_verified_backup(self) -> None:
        carried, equipment = self.snapshots(self.equipped)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / "character_ls"
            character.write_bytes(self.equipped)
            with mock.patch.object(
                inventory,
                "VALIDATED_GEAR_TRANSFERS",
                {("hat", 10)},
            ):
                change = inventory.stage_gear_transfer(
                    equipment,
                    carried,
                    "hat",
                )
                backup = inventory.write_gear_transfer(
                    character,
                    self.catalog,
                    change,
                    expected_source_sha256=carried.source_sha256,
                    now=datetime(2026, 6, 13, 22, 0, 0),
                    backup_dir=root / "backups",
                )

            self.assertEqual(backup.read_bytes(), self.equipped)
            self.assertEqual(
                backup.name,
                "character_ls.bak-20260613-220000",
            )
            self.assertEqual(character.read_bytes(), self.unequipped)


class BagTransferMutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = make_bag_transfer_catalog()
        self.equipped = make_bag_transfer_character(equipped=True)
        self.unequipped = make_bag_transfer_character(equipped=False)

    def snapshot(self, data: bytes) -> inventory.InventorySnapshot:
        return inventory.parse_inventory(data, self.catalog)

    def test_unequips_and_reequips_complete_saved_bag_record(self) -> None:
        carried = self.snapshot(self.equipped)
        with mock.patch.object(inventory, "VALIDATED_BAG_TRANSFERS", {16}):
            unequip = inventory.stage_bag_transfer(carried, 0)
            updated = inventory.apply_bag_transfer(
                self.equipped,
                self.catalog,
                unequip,
                expected_source_sha256=carried.source_sha256,
            )
            self.assertEqual(updated, self.unequipped)

            carried = self.snapshot(updated)
            bag = next(
                item for item in carried.items if item.definition.item_id == 16
            )
            equip = inventory.stage_bag_transfer(carried, 0, bag.offset)
            restored = inventory.apply_bag_transfer(
                updated,
                self.catalog,
                equip,
                expected_source_sha256=carried.source_sha256,
            )

        self.assertEqual(restored, self.equipped)

    def test_rejects_unvalidated_matching_stack_and_stale_source(self) -> None:
        carried = self.snapshot(self.equipped)
        with self.assertRaisesRegex(
            inventory.InventoryMutationError,
            "not validated",
        ):
            inventory.stage_bag_transfer(carried, 0)

        with mock.patch.object(inventory, "VALIDATED_BAG_TRANSFERS", {16}):
            change = inventory.stage_bag_transfer(carried, 0)
            with self.assertRaisesRegex(
                inventory.InventoryMutationError,
                "source changed",
            ):
                inventory.apply_bag_transfer(
                    self.equipped,
                    self.catalog,
                    change,
                    expected_source_sha256=hashlib.sha256(b"stale").hexdigest(),
                )

            matching_stack = bytearray(self.equipped)
            bag_start = carried.items[1].offset
            matching_stack[
                bag_start:bag_start
            ] = serialized_item(16, 2)
            struct.pack_into(
                "<i",
                matching_stack,
                carried.inventory_count_offset,
                len(carried.items) + 1,
            )
            del matching_stack[-inventory.SERIALIZED_RECORD_SIZES[2]:]
            with self.assertRaisesRegex(
                inventory.InventoryMutationError,
                "matching carried bag",
            ):
                inventory.stage_bag_transfer(
                    self.snapshot(bytes(matching_stack)),
                    0,
                )

    def test_write_bag_transfer_creates_verified_backup(self) -> None:
        carried = self.snapshot(self.equipped)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / "character_ls"
            character.write_bytes(self.equipped)
            with mock.patch.object(inventory, "VALIDATED_BAG_TRANSFERS", {16}):
                change = inventory.stage_bag_transfer(carried, 0)
                backup = inventory.write_bag_transfer(
                    character,
                    self.catalog,
                    change,
                    expected_source_sha256=carried.source_sha256,
                    now=datetime(2026, 6, 13, 23, 0, 0),
                    backup_dir=root / "backups",
                )

            self.assertEqual(backup.read_bytes(), self.equipped)
            self.assertEqual(
                backup.name,
                "character_ls.bak-20260613-230000",
            )
            self.assertEqual(character.read_bytes(), self.unequipped)


class FullInventoryEditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = make_inventory_catalog()
        self.original = make_equipment_character()
        self.inventory = inventory.parse_inventory(
            self.original,
            self.catalog,
        )
        self.equipment = inventory.parse_equipment(
            self.original,
            self.catalog,
            self.inventory,
        )

    def test_rebuilds_general_inventory_equipment_and_stacks(self) -> None:
        plan = inventory.inventory_edit_plan_from_snapshots(
            self.equipment,
            self.inventory,
        )
        carried = list(plan.carried_item_raws)
        carried = [
            inventory.record_with_quantity(raw, 44)
            if inventory.parse_item_record(raw, self.catalog).definition.item_id
            == 3
            else raw
            for raw in carried
            if inventory.parse_item_record(raw, self.catalog).definition.item_id
            != 1
        ]

        bag = inventory.default_item_record(self.catalog, 2, 3)
        carried = list(
            inventory.merge_carried_item_raw(carried, bag, self.catalog)
        )
        gear = inventory.default_item_record(self.catalog, 11)
        carried = list(
            inventory.merge_carried_item_raw(carried, gear, self.catalog)
        )
        weapon_position = next(
            position
            for position, raw in enumerate(carried)
            if inventory.parse_item_record(raw, self.catalog).definition.item_id
            == 14
        )
        carried, equipped_weapon = inventory.remove_one_from_carried_raw(
            carried,
            weapon_position,
            self.catalog,
        )

        equipment_slots = list(plan.equipment_slot_raws)
        displaced_weapon = equipment_slots[5]
        equipment_slots[5] = equipped_weapon
        carried = list(
            inventory.merge_carried_item_raw(
                carried,
                displaced_weapon,
                self.catalog,
            )
        )
        updated_plan = inventory.FullInventoryEditPlan(
            source_sha256=plan.source_sha256,
            equipment_slot_raws=tuple(equipment_slots),
            saved_bag_slot_raws=plan.saved_bag_slot_raws,
            carried_item_raws=tuple(carried),
        )

        updated = inventory.apply_full_inventory_edit(
            self.original,
            self.catalog,
            updated_plan,
        )

        updated_inventory = inventory.parse_inventory(updated, self.catalog)
        updated_equipment = inventory.parse_equipment(
            updated,
            self.catalog,
            updated_inventory,
        )
        self.assertEqual(len(updated), len(self.original))
        self.assertEqual(
            updated_equipment.get("weapon").item.definition.item_id,
            14,
        )
        quantities = {
            item.definition.item_id: item.quantity
            for item in updated_inventory.items
        }
        self.assertNotIn(1, quantities)
        self.assertEqual(quantities[2], 5)
        self.assertEqual(quantities[3], 44)
        self.assertEqual(quantities[7], 1)
        carried_gear = next(
            item
            for item in updated_inventory.items
            if item.definition.item_id == 11
        )
        self.assertEqual(
            struct.unpack("<i", carried_gear.payload[:4])[0],
            50,
        )

    def test_write_full_inventory_edit_creates_backup_and_rejects_stale(
        self,
    ) -> None:
        plan = inventory.inventory_edit_plan_from_snapshots(
            self.equipment,
            self.inventory,
        )
        carried = tuple(
            inventory.record_with_quantity(raw, 12)
            if inventory.parse_item_record(raw, self.catalog).definition.item_id
            == 5
            else raw
            for raw in plan.carried_item_raws
        )
        updated_plan = inventory.FullInventoryEditPlan(
            source_sha256=plan.source_sha256,
            equipment_slot_raws=plan.equipment_slot_raws,
            saved_bag_slot_raws=plan.saved_bag_slot_raws,
            carried_item_raws=carried,
        )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / "character_ls"
            character.write_bytes(self.original)
            backup = inventory.write_full_inventory_edit(
                character,
                self.catalog,
                updated_plan,
                now=datetime(2026, 6, 14, 12, 0, 0),
                backup_dir=root / "backups",
            )
            self.assertEqual(backup.read_bytes(), self.original)
            self.assertEqual(
                backup.name,
                "character_ls.bak-20260614-120000",
            )
            reparsed = inventory.read_inventory(character, self.catalog)
            scrap = next(
                item for item in reparsed.items if item.definition.item_id == 5
            )
            self.assertEqual(scrap.quantity, 12)

            character.write_bytes(self.original)
            stale_plan = inventory.FullInventoryEditPlan(
                source_sha256=hashlib.sha256(b"stale").hexdigest(),
                equipment_slot_raws=plan.equipment_slot_raws,
                saved_bag_slot_raws=plan.saved_bag_slot_raws,
                carried_item_raws=carried,
            )
            with self.assertRaisesRegex(
                inventory.InventoryMutationError,
                "source changed",
            ):
                inventory.write_full_inventory_edit(
                    character,
                    self.catalog,
                    stale_plan,
                    backup_dir=root / "backups",
                )
