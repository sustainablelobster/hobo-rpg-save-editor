from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from hobo_rpg_save_editor import reputation


def reputation_table(
    *,
    bruno_value: int = 80,
    prefix: bytes = b"",
) -> bytes:
    records = bytearray()
    for archetype_id in reputation.OBSERVED_REPUTATION_ARCHETYPE_IDS:
        value = bruno_value if archetype_id == 28 else 10
        records.extend(archetype_id.to_bytes(4, "little", signed=True))
        records.extend(value.to_bytes(4, "little", signed=True))
    return (
        prefix
        + len(reputation.OBSERVED_REPUTATION_ARCHETYPE_IDS).to_bytes(
            4,
            "little",
            signed=True,
        )
        + records
    )


def named_int_entry(key: str, value: int) -> bytes:
    raw_key = key.encode("ascii")
    assert len(raw_key) < 0x80
    return bytes([len(raw_key)]) + raw_key + value.to_bytes(
        4,
        "little",
        signed=True,
    )


def named_int_run(*entries: tuple[str, int]) -> bytes:
    return b"".join(named_int_entry(key, value) for key, value in entries)


class BrunoNamedStateTests(unittest.TestCase):
    def test_finds_bruno_entries_in_named_integer_runs(self) -> None:
        data = (
            b"\xff\xff"
            + named_int_run(
                ("Alpha", 1),
                ("BrunoSpoluprace", 1),
                ("Other", 2),
                ("BrunoNasrani", 0),
            )
            + b"\0"
        )

        entries = reputation.bruno_named_state(data, min_run_entries=3)

        self.assertEqual(
            [(entry.key, entry.value) for entry in entries],
            [
                ("BrunoSpoluprace", 1),
                ("BrunoNasrani", 0),
            ],
        )

    def test_reports_bruno_named_state_changes_as_read_only(self) -> None:
        before = named_int_run(
            ("Alpha", 1),
            ("BrunoSpoluprace", 0),
            ("BrunoNasrani", 2),
        )
        after = named_int_run(
            ("Alpha", 1),
            ("BrunoSpoluprace", 1),
            ("BrunoNasrani", 2),
        )

        changes = reputation.bruno_named_state_changes(
            before,
            after,
            min_run_entries=3,
        )

        self.assertEqual(
            [(change.key, change.before, change.after) for change in changes],
            [
                ("BrunoSpoluprace", 0, 1),
            ],
        )

    def test_finds_length_prefixed_bruno_strings(self) -> None:
        data = b"\0" + bytes([5]) + b"Bruno" + bytes([7]) + b"NotThis"

        hits = reputation.find_serialized_strings(data)

        self.assertEqual(
            [(hit.offset, hit.text) for hit in hits],
            [
                (1, "Bruno"),
            ],
        )


class ReputationTableTests(unittest.TestCase):
    def test_finds_bruno_in_structurally_identified_table(self) -> None:
        data = reputation_table(prefix=b"\xff\x00\x7f")

        table = reputation.parse_reputation_table(data)
        bruno = table.get(reputation.BRUNO_ARCHETYPE_ID)

        self.assertEqual(table.count_offset, 3)
        self.assertEqual(len(table.records), 113)
        self.assertEqual(bruno.archetype_id, 28)
        self.assertEqual(bruno.value, 80)
        self.assertEqual(bruno.record_offset, 3 + 4 + 16 * 8)
        self.assertEqual(bruno.value_offset, bruno.record_offset + 4)

    def test_rejects_table_with_changed_archetype_order(self) -> None:
        data = bytearray(reputation_table())
        data[4:8] = (8).to_bytes(4, "little", signed=True)

        with self.assertRaisesRegex(
            reputation.ReputationResearchError,
            "found 0",
        ):
            reputation.parse_reputation_table(bytes(data))

    def test_rejects_multiple_structural_tables(self) -> None:
        table = reputation_table()

        with self.assertRaisesRegex(
            reputation.ReputationResearchError,
            "found 2",
        ):
            reputation.parse_reputation_table(table + b"\xff" + table)


class ReputationEditPlanTests(unittest.TestCase):
    def test_names_cover_every_serialized_reputation_record(self) -> None:
        self.assertEqual(
            set(reputation.REPUTATION_ARCHETYPE_NAMES),
            set(reputation.OBSERVED_REPUTATION_ARCHETYPE_IDS),
        )
        self.assertEqual(
            reputation.reputation_display_name(28),
            "Bruno",
        )
        self.assertEqual(
            reputation.reputation_display_name(160),
            "Crazy",
        )
        self.assertEqual(
            reputation.reputation_raw_name(160),
            "Hobo_Crazy",
        )

    def test_applies_multiple_changes_and_preserves_unrelated_bytes(
        self,
    ) -> None:
        original = reputation_table(prefix=b"\xff\x00\x7f")
        before = reputation.parse_reputation_table(original)
        changes = (
            reputation.ReputationChange(28, 80, 81),
            reputation.ReputationChange(160, 10, 75),
        )
        plan = reputation.reputation_edit_plan(original, changes)

        updated = reputation.apply_reputation_edit_plan(original, plan)

        after = reputation.parse_reputation_table(updated)
        self.assertEqual(after.get(28).value, 81)
        self.assertEqual(after.get(160).value, 75)
        allowed = {
            before.get(28).value_offset,
            before.get(160).value_offset,
        }
        self.assertEqual(
            {
                index
                for index, (old, new) in enumerate(zip(original, updated))
                if old != new
            },
            allowed,
        )

    def test_rejects_stale_duplicate_missing_out_of_range_and_noop(
        self,
    ) -> None:
        original = reputation_table()
        valid = reputation.reputation_edit_plan(
            original,
            (reputation.ReputationChange(28, 80, 81),),
        )
        with self.assertRaisesRegex(
            reputation.ReputationResearchError,
            "source changed",
        ):
            reputation.apply_reputation_edit_plan(
                original + b"\0",
                valid,
            )

        duplicate = reputation.ReputationEditPlan(
            source_sha256=hashlib.sha256(original).hexdigest(),
            changes=(
                reputation.ReputationChange(28, 80, 81),
                reputation.ReputationChange(28, 80, 82),
            ),
        )
        with self.assertRaisesRegex(
            reputation.ReputationResearchError,
            "duplicate",
        ):
            reputation.apply_reputation_edit_plan(original, duplicate)

        for change, message in (
            (reputation.ReputationChange(999, 10, 20), "archetype 999"),
            (reputation.ReputationChange(28, 79, 81), "expected Bruno"),
            (reputation.ReputationChange(28, 80, 101), "between 0 and 100"),
            (reputation.ReputationChange(28, 80, 80), "no-op"),
        ):
            with self.subTest(change=change):
                with self.assertRaisesRegex(
                    (reputation.ReputationResearchError, ValueError),
                    message,
                ):
                    reputation.reputation_edit_plan(original, (change,))

    def test_writer_creates_one_backup_for_multiple_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / "character_ls"
            original = reputation_table(prefix=b"\xff\x00\x7f")
            character.write_bytes(original)
            plan = reputation.reputation_edit_plan(
                original,
                (
                    reputation.ReputationChange(28, 80, 81),
                    reputation.ReputationChange(160, 10, 75),
                ),
            )

            backup = reputation.write_reputation_edit_plan(
                character,
                plan,
                now=datetime(2026, 6, 14, 13, 0, 0),
                backup_dir=root / "backups",
            )

            self.assertEqual(
                backup.name,
                "character_ls.bak-20260614-130000",
            )
            self.assertEqual(backup.read_bytes(), original)
            table = reputation.read_reputation_table(character)
            self.assertEqual(table.get(28).value, 81)
            self.assertEqual(table.get(160).value, 75)
            self.assertEqual(len(list((root / "backups").iterdir())), 1)


class ReputationMutationTests(unittest.TestCase):
    def test_changes_only_bruno_value_and_reparses_result(self) -> None:
        original = reputation_table(prefix=b"\xff\x00\x7f")
        source_sha256 = hashlib.sha256(original).hexdigest()
        before = reputation.bruno_reputation_record(original)

        updated, changed = reputation.apply_bruno_reputation(
            original,
            81,
            expected_old_value=80,
            expected_source_sha256=source_sha256,
        )

        expected = bytearray(original)
        expected[before.value_offset:before.value_offset + 4] = (
            81
        ).to_bytes(4, "little", signed=True)
        self.assertEqual(updated, bytes(expected))
        self.assertEqual(changed, before)
        self.assertEqual(
            reputation.bruno_reputation_record(updated).value,
            81,
        )

    def test_rejects_stale_source_unexpected_value_and_bounds(self) -> None:
        original = reputation_table()

        with self.assertRaisesRegex(
            reputation.ReputationResearchError,
            "source changed",
        ):
            reputation.apply_bruno_reputation(
                original,
                81,
                expected_old_value=80,
                expected_source_sha256=hashlib.sha256(b"stale").hexdigest(),
            )
        with self.assertRaisesRegex(
            reputation.ReputationResearchError,
            "expected Bruno trust 79",
        ):
            reputation.apply_bruno_reputation(
                original,
                81,
                expected_old_value=79,
            )
        with self.assertRaisesRegex(ValueError, "between 0 and 100"):
            reputation.apply_bruno_reputation(
                original,
                101,
                expected_old_value=80,
            )

    def test_writes_verified_backup_and_exact_value_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / "character_ls"
            backup_dir = root / "backups"
            original = reputation_table(prefix=b"\xff\x00\x7f")
            character.write_bytes(original)

            before, backup = reputation.set_bruno_reputation(
                character,
                81,
                expected_old_value=80,
                now=datetime(2026, 6, 14, 12, 0, 0),
                backup_dir=backup_dir,
            )

            self.assertEqual(before.value, 80)
            self.assertEqual(
                backup.name,
                "character_ls.bak-20260614-120000",
            )
            self.assertEqual(backup.read_bytes(), original)
            updated = character.read_bytes()
            self.assertEqual(
                reputation.bruno_reputation_record(updated).value,
                81,
            )
            differing = [
                index
                for index, (old, new) in enumerate(zip(original, updated))
                if old != new
            ]
            self.assertEqual(differing, [before.value_offset])

    def test_backup_failure_leaves_character_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / "character_ls"
            original = reputation_table()
            character.write_bytes(original)
            invalid_backup_dir = root / "occupied"
            invalid_backup_dir.write_text("not a directory", encoding="utf-8")

            with self.assertRaises(OSError):
                reputation.set_bruno_reputation(
                    character,
                    81,
                    expected_old_value=80,
                    backup_dir=invalid_backup_dir,
                )

            self.assertEqual(character.read_bytes(), original)


class BrunoReputationCandidateTests(unittest.TestCase):
    def test_finds_unique_expected_integer_delta(self) -> None:
        before = bytearray(b"\0" * 80)
        after = bytearray(before)
        before[40:44] = (35).to_bytes(4, "little", signed=True)
        after[40:44] = (40).to_bytes(4, "little", signed=True)

        comparison = reputation.compare_bruno_reputation_data(
            bytes(before),
            bytes(after),
            expected_delta=5,
        )
        candidate = reputation.unique_integer_candidate(comparison)

        self.assertEqual(candidate.offset, 40)
        self.assertEqual(candidate.before, 35)
        self.assertEqual(candidate.after, 40)

    def test_rejects_ambiguous_integer_candidates(self) -> None:
        before = bytearray(b"\0" * 100)
        after = bytearray(before)
        for offset, value in ((20, 10), (60, 70)):
            before[offset:offset + 4] = value.to_bytes(
                4,
                "little",
                signed=True,
            )
            after[offset:offset + 4] = (value + 5).to_bytes(
                4,
                "little",
                signed=True,
            )

        comparison = reputation.compare_bruno_reputation_data(
            bytes(before),
            bytes(after),
            expected_delta=5,
        )

        self.assertEqual(
            [candidate.offset for candidate in comparison.integer_candidates],
            [20, 60],
        )
        with self.assertRaisesRegex(
            reputation.ReputationResearchError,
            "exactly one",
        ):
            reputation.unique_integer_candidate(comparison)

    def test_excludes_known_regions_from_candidate_search(self) -> None:
        before = bytearray(b"\0" * 100)
        after = bytearray(before)
        before[20:24] = (10).to_bytes(4, "little", signed=True)
        after[20:24] = (15).to_bytes(4, "little", signed=True)
        before[60:64] = (30).to_bytes(4, "little", signed=True)
        after[60:64] = (35).to_bytes(4, "little", signed=True)

        candidates = reputation.find_integer_change_candidates(
            bytes(before),
            bytes(after),
            expected_delta=5,
            exclude_ranges=((0, 40),),
        )

        self.assertEqual(
            [(candidate.offset, candidate.before, candidate.after)
             for candidate in candidates],
            [
                (60, 30, 35),
            ],
        )


class BrunoReputationSequenceTests(unittest.TestCase):
    def test_finds_candidate_matching_opposing_displayed_changes(self) -> None:
        before = bytearray(b"\x7f" * 140)
        middle = bytearray(before)
        after = bytearray(before)
        for data, value in zip((before, middle, after), (30, 25, 32)):
            data[40:44] = value.to_bytes(4, "little", signed=True)
        for data, value in zip((before, middle, after), (10, 5, 12)):
            data[80:84] = value.to_bytes(4, "little", signed=True)

        comparison = reputation.compare_bruno_reputation_sequence_data(
            (bytes(before), bytes(middle), bytes(after)),
            (30, 25, 32),
        )
        candidate = reputation.unique_sequence_candidate(comparison)

        self.assertEqual(
            [
                (item.offset, item.values, item.deltas)
                for item in comparison.shared_integer_candidates
            ],
            [
                (40, (30, 25, 32), (-5, 7)),
                (80, (10, 5, 12), (-5, 7)),
            ],
        )
        self.assertEqual(candidate.offset, 40)
        self.assertEqual(candidate.values, (30, 25, 32))

    def test_intersection_ignores_candidates_not_in_every_transition(
        self,
    ) -> None:
        before = bytearray(b"\x7f" * 140)
        middle = bytearray(before)
        after = bytearray(before)
        for data, value in zip((before, middle, after), (30, 25, 32)):
            data[40:44] = value.to_bytes(4, "little", signed=True)
        before[80:84] = (10).to_bytes(4, "little", signed=True)
        middle[80:84] = (5).to_bytes(4, "little", signed=True)
        middle[100:104] = (20).to_bytes(4, "little", signed=True)
        after[100:104] = (27).to_bytes(4, "little", signed=True)

        comparison = reputation.compare_bruno_reputation_sequence_data(
            (bytes(before), bytes(middle), bytes(after)),
            (30, 25, 32),
        )

        self.assertEqual(
            [item.offset for item in comparison.shared_integer_candidates],
            [40],
        )

    def test_rejects_mismatched_capture_and_value_counts(self) -> None:
        with self.assertRaisesRegex(
            reputation.ReputationResearchError,
            "counts must match",
        ):
            reputation.compare_bruno_reputation_sequence_data(
                (b"before", b"middle", b"after"),
                (10, 5),
            )


if __name__ == "__main__":
    unittest.main()
