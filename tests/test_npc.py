from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from hobo_rpg_save_editor import npc
from hobo_rpg_save_editor import reputation


TEST_KEYS = ("AlphaFlag", "BrunoFlag", "FinalFlag")
TEST_DIGEST = npc.npc_flag_key_digest(TEST_KEYS)


def encode_7bit(value: int) -> bytes:
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def flag_table(
    values: tuple[int, ...] = (0, 1, 0),
    *,
    keys: tuple[str, ...] = TEST_KEYS,
) -> bytes:
    data = bytearray(struct.pack("<i", len(keys)))
    for key, value in zip(keys, values):
        raw_key = key.encode("ascii")
        data += encode_7bit(len(raw_key))
        data += raw_key
        data += struct.pack("<i", value)
    return bytes(data)


def reputation_table(*, bruno_value: int = 80) -> bytes:
    data = bytearray(
        struct.pack("<i", reputation.OBSERVED_REPUTATION_COUNT)
    )
    for archetype_id in reputation.OBSERVED_REPUTATION_ARCHETYPE_IDS:
        value = bruno_value if archetype_id == 28 else 10
        data += struct.pack("<ii", archetype_id, value)
    return bytes(data)


def npc_character(
    flag_values: tuple[int, ...] = (0, 1, 0),
    *,
    bruno_value: int = 80,
) -> bytes:
    return (
        b"\xffprefix"
        + reputation_table(bruno_value=bruno_value)
        + b"\xfevariable"
        + flag_table(flag_values)
        + b"\xfdsuffix"
    )


class NpcFlagParsingTests(unittest.TestCase):
    def parse(self, data: bytes) -> npc.NpcFlagTable:
        return npc.parse_npc_flag_table(
            data,
            expected_count=len(TEST_KEYS),
            expected_key_digest=TEST_DIGEST,
        )

    def test_parses_structural_table_at_moving_offset(self) -> None:
        first = self.parse(b"\0" + flag_table())
        second = self.parse(b"\0" * 41 + flag_table())

        self.assertEqual(first.count_offset, 1)
        self.assertEqual(second.count_offset, 41)
        self.assertEqual(
            [(record.key, record.value) for record in second.records],
            list(zip(TEST_KEYS, (0, 1, 0))),
        )

    def test_rejects_reordered_duplicate_missing_and_non_boolean_records(
        self,
    ) -> None:
        cases = (
            flag_table(keys=("BrunoFlag", "AlphaFlag", "FinalFlag")),
            flag_table(keys=("AlphaFlag", "AlphaFlag", "FinalFlag")),
            flag_table(keys=("AlphaFlag", "BrunoFlag")),
            flag_table(values=(0, 2, 0)),
            flag_table()[:-2],
        )
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaisesRegex(npc.NpcFormatError, "found 0"):
                    self.parse(data)

    def test_rejects_multiple_structural_tables(self) -> None:
        table = flag_table()

        with self.assertRaisesRegex(npc.NpcFormatError, "found 2"):
            self.parse(table + b"\xff" + table)


class NpcFlagAnnotationTests(unittest.TestCase):
    def test_builds_game_derived_associations_and_meanings(self) -> None:
        assets = {
            (
                "assets/hobothor/conversations/json/inside/"
                "specific_bruno.json"
            ): json.dumps(
                {
                    "opts": [
                        {
                            "c": "bool_BrunoSpoluprace_0",
                            "id": "ID_1",
                            "t": "raw option",
                        }
                    ],
                    "reacts": [
                        {
                            "rews": ["bool_BrunoSpoluprace_1"],
                            "id": "ID_2",
                            "t": "raw response",
                        }
                    ],
                }
            ),
            (
                "assets/hobothor/conversations/_translatedjson_/en/"
                "specific_bruno.json"
            ): json.dumps(
                {
                    "keys": ["ID_1", "ID_2"],
                    "values": [
                        "I talked to Anton and decided to work for you.",
                        "Good. Welcome aboard.",
                    ],
                }
            ),
            "assets/hobothor/quests/json/kdojebruno.json": json.dumps(
                {
                    "questTitle": "Who's Bruno?",
                    "qnodes": [{"aC": {"arch": 28}}],
                    "reacts": [
                        {
                            "rews": ["bool_VisKdoJeBruno_1"],
                            "t": (
                                "Go around back and see Anton for your "
                                "instructions."
                            ),
                        }
                    ],
                }
            ),
        }

        catalog = npc.build_npc_flag_annotations(
            ("BrunoSpoluprace", "VisKdoJeBruno"),
            assets,
        )

        cooperation = catalog.get("BrunoSpoluprace")
        self.assertEqual(cooperation.associations, ("Bruno",))
        self.assertEqual(
            cooperation.meaning,
            (
                'Controls dialogue: "I talked to Anton and decided to '
                'work for you."'
            ),
        )
        self.assertFalse(cooperation.inferred)

        introduction = catalog.get("VisKdoJeBruno")
        self.assertEqual(
            introduction.associations,
            ("Bruno", "Quest: Who's Bruno?"),
        )
        self.assertIn("Set after:", introduction.meaning)
        self.assertFalse(introduction.inferred)

    def test_reports_multiple_characters_and_clear_operations(self) -> None:
        assets = {}
        for character in ("anton", "bruno"):
            path = (
                "assets/hobothor/conversations/json/inside/"
                f"specific_{character}.json"
            )
            assets[path] = json.dumps(
                {
                    "reacts": [
                        {
                            "rews": ["bool_SharedFlag_0"],
                            "t": "This arrangement is over now.",
                        }
                    ]
                }
            )

        annotation = npc.build_npc_flag_annotations(
            ("SharedFlag",),
            assets,
        ).get("SharedFlag")

        self.assertEqual(annotation.associations, ("Anton", "Bruno"))
        self.assertEqual(
            annotation.meaning,
            'Cleared after: "This arrangement is over now."',
        )

    def test_uses_marked_key_inference_for_unhelpful_or_missing_context(
        self,
    ) -> None:
        assets = {
            "assets/hobothor/quests/json/architekt.json": json.dumps(
                {
                    "questTitle": "Architect",
                    "opts": [
                        {
                            "rews": ["bool_ArchitektDone_1"],
                            "t": "_item_57_ [_hint_57_]",
                        }
                    ],
                }
            ),
            "assets/hobothor/quests/json/broken.json": "{not json",
        }

        catalog = npc.build_npc_flag_annotations(
            ("ArchitektDone", "BrunoKolo1"),
            assets,
        )

        architect = catalog.get("ArchitektDone")
        self.assertEqual(architect.associations, ("Quest: Architect",))
        self.assertEqual(
            architect.meaning,
            "Architect completed (inferred from key)",
        )
        self.assertTrue(architect.inferred)
        self.assertIn("could not be parsed", catalog.warnings[0])

        bruno = catalog.get("BrunoKolo1")
        self.assertEqual(bruno.associations, ("Bruno (inferred)",))
        self.assertEqual(
            bruno.meaning,
            "Bruno bike 1 (inferred from key)",
        )

    def test_translates_common_czech_flag_tokens(self) -> None:
        self.assertEqual(
            npc.infer_npc_flag_meaning("AnatolyProdalPrsten"),
            "Anatoly sold ring (inferred from key)",
        )
        self.assertEqual(
            npc.infer_npc_flag_meaning("PotravinovyKontejnerNalezen"),
            "Food container found (inferred from key)",
        )

    def test_hides_generic_gender_prefix_from_conversation_name(self) -> None:
        assets = {
            (
                "assets/hobothor/conversations/json/"
                "npc_m_asertivni.json"
            ): json.dumps(
                {
                    "reacts": [
                        {
                            "rews": ["bool_AsertivniPruzkumTrhu_1"],
                            "t": "The survey is complete.",
                        }
                    ]
                }
            )
        }

        annotation = npc.build_npc_flag_annotations(
            ("AsertivniPruzkumTrhu",),
            assets,
        ).get("AsertivniPruzkumTrhu")

        self.assertEqual(annotation.associations, ("Asertivni",))

    def test_fallback_catalog_never_requires_game_assets(self) -> None:
        catalog = npc.fallback_npc_flag_annotations(
            ("BrunoSpoluprace", "EarlyAccess"),
            warning="bundle unavailable",
        )

        self.assertEqual(catalog.warnings, ("bundle unavailable",))
        self.assertEqual(
            catalog.get("BrunoSpoluprace").associations,
            ("Bruno (inferred)",),
        )
        self.assertEqual(
            catalog.get("EarlyAccess").associations,
            ("Global / quest state",),
        )


class NpcMutationTests(unittest.TestCase):
    def plan(
        self,
        data: bytes,
        reputation_changes=(),
        flag_changes=(),
    ) -> npc.NpcEditPlan:
        return npc.npc_edit_plan(
            data,
            reputation_changes,
            flag_changes,
            expected_flag_count=len(TEST_KEYS),
            expected_flag_key_digest=TEST_DIGEST,
        )

    def apply(self, data: bytes, plan: npc.NpcEditPlan) -> bytes:
        return npc.apply_npc_edit_plan(
            data,
            plan,
            expected_flag_count=len(TEST_KEYS),
            expected_flag_key_digest=TEST_DIGEST,
        )

    def test_applies_reputation_and_flags_as_one_verified_change(self) -> None:
        original = npc_character()
        before = npc.parse_npc_data(
            original,
            expected_flag_count=len(TEST_KEYS),
            expected_flag_key_digest=TEST_DIGEST,
        )
        plan = self.plan(
            original,
            (reputation.ReputationChange(28, 80, 81),),
            (
                npc.NpcFlagChange("AlphaFlag", 0, 1),
                npc.NpcFlagChange("BrunoFlag", 1, 0),
            ),
        )

        updated = self.apply(original, plan)

        after = npc.parse_npc_data(
            updated,
            expected_flag_count=len(TEST_KEYS),
            expected_flag_key_digest=TEST_DIGEST,
        )
        self.assertEqual(after.reputation.get(28).value, 81)
        self.assertEqual(after.flags.get("AlphaFlag").value, 1)
        self.assertEqual(after.flags.get("BrunoFlag").value, 0)
        allowed = {
            before.reputation.get(28).value_offset,
            before.flags.get("AlphaFlag").value_offset,
            before.flags.get("BrunoFlag").value_offset,
        }
        self.assertEqual(
            {
                index
                for index, (old, new) in enumerate(zip(original, updated))
                if old != new
            },
            allowed,
        )

    def test_rejects_stale_duplicate_unknown_invalid_and_noop_changes(
        self,
    ) -> None:
        original = npc_character()
        valid = self.plan(
            original,
            flag_changes=(npc.NpcFlagChange("AlphaFlag", 0, 1),),
        )
        stale = npc.NpcEditPlan(
            source_sha256=hashlib.sha256(b"stale").hexdigest(),
            reputation_changes=valid.reputation_changes,
            flag_changes=valid.flag_changes,
        )
        with self.assertRaisesRegex(npc.NpcFormatError, "source changed"):
            self.apply(original, stale)

        cases = (
            (
                (
                    npc.NpcFlagChange("AlphaFlag", 0, 1),
                    npc.NpcFlagChange("AlphaFlag", 0, 1),
                ),
                "duplicate",
            ),
            ((npc.NpcFlagChange("Missing", 0, 1),), "Missing"),
            ((npc.NpcFlagChange("AlphaFlag", 1, 0),), "expected"),
            ((npc.NpcFlagChange("AlphaFlag", 0, 2),), "0 or 1"),
            ((npc.NpcFlagChange("AlphaFlag", 0, 0),), "no-op"),
        )
        for changes, message in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(
                    (npc.NpcFormatError, ValueError),
                    message,
                ):
                    self.plan(original, flag_changes=changes)

    def test_writer_creates_one_backup_for_combined_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / "character_ls"
            original = npc_character()
            character.write_bytes(original)
            plan = self.plan(
                original,
                (reputation.ReputationChange(28, 80, 81),),
                (npc.NpcFlagChange("FinalFlag", 0, 1),),
            )

            backup = npc.write_npc_edit_plan(
                character,
                plan,
                now=datetime(2026, 6, 14, 15, 0, 0),
                backup_dir=root / "backups",
                expected_flag_count=len(TEST_KEYS),
                expected_flag_key_digest=TEST_DIGEST,
            )

            self.assertEqual(
                backup.name,
                "character_ls.bak-20260614-150000",
            )
            self.assertEqual(backup.read_bytes(), original)
            parsed = npc.read_npc_data(
                character,
                expected_flag_count=len(TEST_KEYS),
                expected_flag_key_digest=TEST_DIGEST,
            )
            self.assertEqual(parsed.reputation.get(28).value, 81)
            self.assertEqual(parsed.flags.get("FinalFlag").value, 1)
            self.assertEqual(len(list((root / "backups").iterdir())), 1)

    def test_backup_failure_leaves_character_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / "character_ls"
            original = npc_character()
            character.write_bytes(original)
            invalid_backup_dir = root / "occupied"
            invalid_backup_dir.write_text("not a directory", encoding="utf-8")
            plan = self.plan(
                original,
                flag_changes=(npc.NpcFlagChange("FinalFlag", 0, 1),),
            )

            with self.assertRaises(OSError):
                npc.write_npc_edit_plan(
                    character,
                    plan,
                    backup_dir=invalid_backup_dir,
                    expected_flag_count=len(TEST_KEYS),
                    expected_flag_key_digest=TEST_DIGEST,
                )

            self.assertEqual(character.read_bytes(), original)


class NpcFlagComparisonTests(unittest.TestCase):
    def test_reports_only_changed_flags_in_table_order(self) -> None:
        changes = npc.compare_npc_flags(
            flag_table((0, 1, 0)),
            flag_table((1, 1, 1)),
            expected_flag_count=len(TEST_KEYS),
            expected_flag_key_digest=TEST_DIGEST,
        )

        self.assertEqual(
            [(change.key, change.before, change.after) for change in changes],
            [
                ("AlphaFlag", 0, 1),
                ("FinalFlag", 0, 1),
            ],
        )


if __name__ == "__main__":
    unittest.main()
