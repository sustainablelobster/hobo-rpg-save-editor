from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from hobo_rpg_save_editor import npc
from hobo_rpg_save_editor import quests
from hobo_rpg_save_editor import reputation
from research import save_research as research
from tests.test_inventory import (
    make_equipment_character,
    make_gear_transfer_character,
    make_inventory_catalog,
)


class ByteComparisonTests(unittest.TestCase):
    def test_finds_contiguous_changes_and_shorter_tail(self) -> None:
        spans = research.compare_bytes(b"abcXXdefZ", b"abcYYdef")

        self.assertEqual(
            [(span.start, span.end) for span in spans],
            [(3, 5), (8, 9)],
        )
        self.assertEqual(spans[0].before, b"XX")
        self.assertEqual(spans[0].after, b"YY")
        self.assertEqual(spans[1].before, b"Z")
        self.assertEqual(spans[1].after, b"")

    def test_equal_bytes_have_no_spans(self) -> None:
        self.assertEqual(research.compare_bytes(b"same", b"same"), ())

    def test_compare_files_reports_hashes_sizes_and_changed_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            before = Path(temp) / "before"
            after = Path(temp) / "after"
            before.write_bytes(b"abc")
            after.write_bytes(b"axc!")

            comparison = research.compare_files(before, after)

        self.assertEqual(comparison.before.size, 3)
        self.assertEqual(comparison.after.size, 4)
        self.assertEqual(
            comparison.before.sha256,
            hashlib.sha256(b"abc").hexdigest(),
        )
        self.assertEqual(comparison.differing_bytes, 2)
        self.assertEqual(
            [(span.start, span.end) for span in comparison.spans],
            [(1, 2), (3, 4)],
        )


class GenerationDiscoveryTests(unittest.TestCase):
    def test_discovers_only_existing_generations_in_stable_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            active = root / "character_ls"
            active.write_bytes(b"active")
            (root / "character_b1").write_bytes(b"backup")
            (root / "other_lws").write_bytes(b"other")

            generations = research.discover_character_generations(active)

        self.assertEqual(
            [path.name for path in generations],
            ["character_ls", "character_b1"],
        )

    def test_compares_available_generations_to_active_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            active = root / "character_ls"
            active.write_bytes(b"new")
            old = root / "character_lws"
            old.write_bytes(b"old")

            comparisons = research.compare_character_generations(active)

        self.assertEqual(len(comparisons), 1)
        self.assertEqual(comparisons[0].before.path.name, "character_lws")
        self.assertEqual(comparisons[0].after.path.name, "character_ls")

    def test_rejects_unknown_character_filename(self) -> None:
        with self.assertRaisesRegex(ValueError, "must end"):
            research.discover_character_generations(Path("character.dat"))


class BrunoReputationCommandTests(unittest.TestCase):
    def test_inspect_command_reports_structural_bruno_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            character = Path(temp) / "character_ls"
            records = bytearray()
            for archetype_id in (
                reputation.OBSERVED_REPUTATION_ARCHETYPE_IDS
            ):
                value = 80 if archetype_id == 28 else 10
                records.extend(
                    archetype_id.to_bytes(4, "little", signed=True)
                )
                records.extend(value.to_bytes(4, "little", signed=True))
            character.write_bytes(
                b"\xff\x00\x7f"
                + reputation.OBSERVED_REPUTATION_COUNT.to_bytes(
                    4,
                    "little",
                    signed=True,
                )
                + records
            )

            output = io.StringIO()
            with redirect_stdout(output):
                result = research.main(
                    [
                        "bruno-reputation-inspect",
                        "--character",
                        str(character),
                    ]
                )

        self.assertEqual(result, 0)
        self.assertIn("reputation count: 113 at 0x3", output.getvalue())
        self.assertIn("Bruno archetype ID: 28", output.getvalue())
        self.assertIn("Bruno value offset: 0x8B", output.getvalue())
        self.assertIn("Bruno trust: 80 / 100", output.getvalue())

    def test_sequence_command_reports_unique_displayed_value_match(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = (
                root / "before-shakedown_ls",
                root / "after-shakedown_ls",
                root / "after-crazy_ls",
            )
            for path, value in zip(paths, (30, 25, 32)):
                data = bytearray(b"\x7f" * 120)
                data[40:44] = value.to_bytes(
                    4,
                    "little",
                    signed=True,
                )
                path.write_bytes(data)

            output = io.StringIO()
            with redirect_stdout(output):
                result = research.main(
                    [
                        "bruno-reputation-sequence",
                        "--game-dir",
                        str(root),
                        "--before",
                        str(paths[0]),
                        "--middle",
                        str(paths[1]),
                        "--after",
                        str(paths[2]),
                        "--before-value",
                        "30",
                        "--middle-value",
                        "25",
                        "--after-value",
                        "32",
                    ]
                )

        self.assertEqual(result, 0)
        self.assertIn("expected deltas: -5, +7", output.getvalue())
        self.assertIn(
            "unique validated target: 0x28",
            output.getvalue(),
        )


class NpcFlagCommandTests(unittest.TestCase):
    def test_inspect_command_filters_structural_flags(self) -> None:
        table = npc.NpcFlagTable(
            count_offset=10,
            records_start=14,
            records_end=80,
            records=(
                npc.NpcFlagRecord(14, 15, 25, "BrunoFlag", 1),
                npc.NpcFlagRecord(29, 30, 40, "OtherFlag", 0),
            ),
        )
        with tempfile.TemporaryDirectory() as temp:
            character = Path(temp) / "character_ls"
            character.write_bytes(b"data")
            output = io.StringIO()
            with (
                mock.patch.object(
                    research.npc,
                    "parse_npc_flag_table",
                    return_value=table,
                ),
                redirect_stdout(output),
            ):
                result = research.main(
                    [
                        "npc-flags-inspect",
                        "--character",
                        str(character),
                        "--filter",
                        "bruno",
                    ]
                )

        self.assertEqual(result, 0)
        self.assertIn("NPC flag count: 2 at 0xA", output.getvalue())
        self.assertIn("1 | BrunoFlag", output.getvalue())
        self.assertNotIn("OtherFlag", output.getvalue())

    def test_inspect_command_adds_and_searches_english_annotations(
        self,
    ) -> None:
        table = npc.NpcFlagTable(
            count_offset=10,
            records_start=14,
            records_end=80,
            records=(
                npc.NpcFlagRecord(14, 15, 25, "BrunoFlag", 0),
                npc.NpcFlagRecord(29, 30, 40, "OtherFlag", 0),
            ),
        )
        annotations = npc.NpcFlagAnnotationCatalog(
            annotations={
                "BrunoFlag": npc.NpcFlagAnnotation(
                    key="BrunoFlag",
                    associations=("Bruno", "Quest: Who's Bruno?"),
                    meaning="Controls dialogue about working for Bruno.",
                    inferred=False,
                    evidence=("specific_bruno.json",),
                ),
                "OtherFlag": npc.NpcFlagAnnotation(
                    key="OtherFlag",
                    associations=("Global / quest state",),
                    meaning="Other flag (inferred from key)",
                    inferred=True,
                    evidence=(),
                ),
            }
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / "character_ls"
            character.write_bytes(b"data")
            output = io.StringIO()
            with (
                mock.patch.object(
                    research.npc,
                    "parse_npc_flag_table",
                    return_value=table,
                ),
                mock.patch.object(
                    research.npc,
                    "load_npc_flag_annotations",
                    return_value=annotations,
                ),
                redirect_stdout(output),
            ):
                result = research.main(
                    [
                        "npc-flags-inspect",
                        "--character",
                        str(character),
                        "--game-dir",
                        str(root),
                        "--filter",
                        "working",
                    ]
                )

        self.assertEqual(result, 0)
        self.assertIn(
            "BrunoFlag | Bruno, Quest: Who's Bruno? | "
            "Controls dialogue about working for Bruno.",
            output.getvalue(),
        )
        self.assertNotIn("OtherFlag", output.getvalue())

    def test_diff_command_reports_filtered_changes(self) -> None:
        changes = (
            npc.NpcFlagValueChange("BrunoFlag", 0, 1),
            npc.NpcFlagValueChange("OtherFlag", 1, 0),
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before = root / "before_ls"
            after = root / "after_ls"
            before.write_bytes(b"before")
            after.write_bytes(b"after")
            output = io.StringIO()
            with (
                mock.patch.object(
                    research.npc,
                    "compare_npc_flags",
                    return_value=changes,
                ),
                redirect_stdout(output),
            ):
                result = research.main(
                    [
                        "npc-flags-diff",
                        "--before",
                        str(before),
                        "--after",
                        str(after),
                        "--filter",
                        "bruno",
                    ]
                )

        self.assertEqual(result, 0)
        self.assertIn("changed NPC flags: 1", output.getvalue())
        self.assertIn("BrunoFlag: 0 -> 1", output.getvalue())
        self.assertNotIn("OtherFlag", output.getvalue())


class QuestCommandTests(unittest.TestCase):
    def test_quest_command_filters_and_reports_status_evidence(self) -> None:
        table = npc.NpcFlagTable(
            count_offset=10,
            records_start=14,
            records_end=80,
            records=(
                npc.NpcFlagRecord(14, 15, 25, "BrunoFlag", 1),
                npc.NpcFlagRecord(29, 30, 40, "OtherFlag", 0),
            ),
        )
        catalog = quests.QuestCatalog(
            quests=(
                quests.QuestDefinition(
                    asset_path="assets/hobothor/quests/json/bruno.json",
                    asset_name="bruno",
                    title="Who's Bruno?",
                    quest_id=None,
                    type_label=quests.QUEST_TYPE_QUEST,
                    associations=("Bruno",),
                    references=(
                        quests.QuestFlagReference(
                            key="BrunoFlag",
                            value=1,
                            role="sets",
                            text="Find out who Bruno is.",
                        ),
                        quests.QuestFlagReference(
                            key="OtherFlag",
                            value=1,
                            role="sets",
                            text="Finish the other errand.",
                        ),
                    ),
                    evidence=("Find out who Bruno is.",),
                ),
                quests.QuestDefinition(
                    asset_path="assets/hobothor/quests/json/repeat.json",
                    asset_name="repeat",
                    title="Temp Work",
                    quest_id=None,
                    type_label=quests.QUEST_TYPE_REPEATABLE,
                    associations=(),
                    references=(),
                    evidence=(),
                ),
            )
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / "character_ls"
            character.write_bytes(b"data")
            output = io.StringIO()
            with (
                mock.patch.object(
                    research.npc,
                    "parse_npc_flag_table",
                    return_value=table,
                ),
                mock.patch.object(
                    research.quests,
                    "load_quest_catalog",
                    return_value=catalog,
                ),
                redirect_stdout(output),
            ):
                result = research.main(
                    [
                        "quests",
                        "--game-dir",
                        str(root),
                        "--character",
                        str(character),
                        "--filter",
                        "bruno",
                        "--status",
                        "in-progress",
                    ]
                )

        self.assertEqual(result, 0)
        self.assertIn("Quest assets: 2", output.getvalue())
        self.assertIn(
            "In progress | 1/2 | Quest | Who's Bruno?",
            output.getvalue(),
        )
        self.assertIn("enabled: BrunoFlag", output.getvalue())
        self.assertIn("disabled: OtherFlag", output.getvalue())
        self.assertNotIn("Temp Work", output.getvalue())


class RegionComparisonTests(unittest.TestCase):
    def test_reports_identical_regions_and_outside_changes(self) -> None:
        catalog = make_inventory_catalog()
        before_data = make_equipment_character()
        after_data = bytearray(before_data)
        after_data[0x10] ^= 0xFF
        after_data[-1] = 1

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before = root / "before_ls"
            after = root / "after_ls"
            before.write_bytes(before_data)
            after.write_bytes(after_data)

            comparison = research.compare_character_regions(
                before,
                after,
                catalog,
            )

        self.assertTrue(comparison.combined_offsets_match)
        self.assertTrue(comparison.combined_bytes_match)
        self.assertTrue(comparison.equipment_bytes_match)
        self.assertTrue(comparison.inventory_bytes_match)
        self.assertEqual(len(comparison.outside_spans), 2)
        self.assertEqual(comparison.outside_differing_bytes, 2)
        self.assertEqual(comparison.changed_slots, ())
        self.assertEqual(comparison.changed_saved_bag_slots, ())
        self.assertEqual(comparison.removed_saved_bags, ())
        self.assertEqual(comparison.added_saved_bags, ())
        self.assertEqual(comparison.removed_items, ())
        self.assertEqual(comparison.added_items, ())

    def test_reports_slot_and_carried_record_changes(self) -> None:
        catalog = make_inventory_catalog()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before = root / "before_ls"
            after = root / "after_ls"
            before.write_bytes(make_gear_transfer_character(equipped=True))
            after.write_bytes(make_gear_transfer_character(equipped=False))

            comparison = research.compare_character_regions(
                before,
                after,
                catalog,
            )

        self.assertFalse(comparison.combined_offsets_match)
        self.assertFalse(comparison.combined_bytes_match)
        slot_changes = [
            (
                old.name,
                old.item.item_id if old.item else None,
                new.item.item_id if new.item else None,
            )
            for old, new in comparison.changed_slots
        ]
        self.assertEqual(
            slot_changes,
            [
                ("hat", 10, None),
            ],
        )
        self.assertEqual(comparison.removed_items, ())
        self.assertEqual(
            [
                (item.item_id, item.category)
                for item in comparison.added_items
            ],
            [
                (10, "gears"),
            ],
        )

    def test_reports_saved_bag_record_changes(self) -> None:
        catalog = make_inventory_catalog()
        before_data = bytearray(make_equipment_character())
        after_data = bytearray(before_data)
        before_bag = before_data[0x200 + 4:0x200 + 19]
        after_bag = bytearray(before_bag)
        after_bag[9:13] = (2).to_bytes(4, "little")
        after_data[0x200 + 4:0x200 + 19] = after_bag

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before = root / "before_ls"
            after = root / "after_ls"
            before.write_bytes(before_data)
            after.write_bytes(after_data)

            comparison = research.compare_character_regions(
                before,
                after,
                catalog,
            )

        self.assertEqual(
            [item.item_id for item in comparison.removed_saved_bags],
            [2],
        )
        self.assertEqual(
            [item.item_id for item in comparison.added_saved_bags],
            [2],
        )
        self.assertEqual(
            [
                (
                    before_slot.name,
                    before_slot.item.quantity
                    if before_slot.item
                    else None,
                    after_slot.item.quantity
                    if after_slot.item
                    else None,
                )
                for before_slot, after_slot
                in comparison.changed_saved_bag_slots
            ],
            [
                ("bag 1", 1, 2),
            ],
        )
        self.assertEqual(comparison.removed_items, ())
        self.assertEqual(comparison.added_items, ())

    def test_reports_empty_saved_bag_slot_changes(self) -> None:
        catalog = make_inventory_catalog()
        prefix = make_equipment_character()[:0x200]
        bag = make_equipment_character()[0x200 + 4:0x200 + 19]
        suffix = (1).to_bytes(4, "little") + bytes(
            [
                1, 3, 0, 0, 0, 3, 0, 0,
                0, 1, 0, 0, 0, 0, 0,
            ]
        )
        before_data = prefix + (2).to_bytes(4, "little") + bag + b"\0" + suffix
        after_data = prefix + (2).to_bytes(4, "little") + b"\0" + bag + suffix

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before = root / "before_ls"
            after = root / "after_ls"
            before.write_bytes(before_data)
            after.write_bytes(after_data)

            comparison = research.compare_character_regions(
                before,
                after,
                catalog,
            )

        self.assertEqual(
            [
                (
                    before_slot.name,
                    before_slot.item.item_id
                    if before_slot.item
                    else None,
                    after_slot.item.item_id
                    if after_slot.item
                    else None,
                )
                for before_slot, after_slot
                in comparison.changed_saved_bag_slots
            ],
            [
                ("bag 1", 2, None),
                ("bag 2", None, 2),
            ],
        )
