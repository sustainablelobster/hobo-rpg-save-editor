from __future__ import annotations

import struct
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from hobo_rpg_save_editor import editor


SAVE_ID = "e1f4a340-ecbf-4f71-a80f-8f2ed6b26b3b"


def encode_7bit(value: int) -> bytes:
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def encode_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return encode_7bit(len(encoded)) + encoded


def make_slot(name: str = "////////food////////") -> bytes:
    data = bytearray()
    data += encode_string(f"${SAVE_ID}")
    data += struct.pack("<I", 0)
    data += encode_string(name)
    data += struct.pack("<IIQ", 0, 2, 0)
    data += encode_string("06/13/2026 01:11:26")
    data += encode_string("9qo9qTa9wS189eKm1ha5QQ==")
    return bytes(data).ljust(256, b"\0")


def make_character(cash: int) -> bytes:
    data = bytearray(b"\xaeG\xe1>" + b"\0" * 100)
    data += b"\x0cBrunoNasrani" + struct.pack("<i", 0)
    data += editor.CASH_KEY + struct.pack("<i", cash)
    data += b"\x14CleanStreetsProgress" + struct.pack("<i", 20)
    return bytes(data).ljust(1024, b"\0")


def make_parameter_character(
    primary: tuple[tuple[int, int, int], ...],
    secondary: tuple[tuple[int, float], ...],
    cash: int = 10000,
) -> bytes:
    data = bytearray(b"\0" * editor.PRIMARY_PARAMETER_COUNT_OFFSET)
    data += struct.pack("<i", len(primary))
    for parameter in primary:
        data += struct.pack("<iii", *parameter)
    data += struct.pack("<i", len(secondary))
    for parameter in secondary:
        data += struct.pack("<if", *parameter)
    data += b"\0" * 37
    data += editor.CASH_KEY + struct.pack("<i", cash)
    return bytes(data)


class SlotParsingTests(unittest.TestCase):
    def test_parse_slot_extracts_known_save_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            account = Path(temp) / "12345"
            configs = account / "NFS_WorldConfigs"
            configs.mkdir(parents=True)
            slot = configs / "slot5"
            slot.write_bytes(make_slot())

            record = editor.parse_slot(slot)

            self.assertEqual(record.save_id, SAVE_ID)
            self.assertEqual(record.display_name, "food")
            self.assertEqual(record.saved_at, "06/13/2026 01:11:26")
            self.assertEqual(record.account_id, "12345")
            self.assertEqual(
                record.character_path,
                account / "NFS_Characters" / f"{SAVE_ID}_ls",
            )

    def test_scan_saves_reports_bad_slots_without_hiding_good_ones(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp) / editor.GAME_DIR_NAME
            configs = game / editor.SAVE_DIR / "12345" / "NFS_WorldConfigs"
            configs.mkdir(parents=True)
            (configs / "slot0").write_bytes(make_slot("///valid///"))
            (configs / "slot1").write_bytes(b"not a slot")

            records, warnings = editor.scan_saves(game)

            self.assertEqual([record.display_name for record in records], ["valid"])
            self.assertEqual(len(warnings), 1)


class CharacterParameterParsingTests(unittest.TestCase):
    def test_parses_named_unknown_and_unsorted_parameter_records(self) -> None:
        data = make_parameter_character(
            primary=(
                (0, -5, 100),
                (77, 12, 20),
                (20, 86, 86),
            ),
            secondary=(
                (18, 12.5),
                (16, 21.0),
                (99, -3.25),
            ),
        )

        parameters = editor.parse_character_parameters(data)

        self.assertEqual(
            parameters.primary,
            (
                editor.PrimaryParameter(0, -5, 100),
                editor.PrimaryParameter(77, 12, 20),
                editor.PrimaryParameter(20, 86, 86),
            ),
        )
        self.assertEqual(
            [parameter.display_name for parameter in parameters.primary],
            ["Health", "Unknown (77)", "Stamina"],
        )
        self.assertEqual(
            [parameter.parameter_type for parameter in parameters.secondary],
            [18, 16, 99],
        )
        self.assertEqual(
            [parameter.display_name for parameter in parameters.secondary],
            ["Defense", "Immunity", "Unknown (99)"],
        )
        self.assertAlmostEqual(parameters.secondary[0].current_value, 12.5)
        self.assertAlmostEqual(parameters.secondary[2].current_value, -3.25)

    def test_reads_parameters_from_character_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            character = Path(temp) / f"{SAVE_ID}_ls"
            character.write_bytes(
                make_parameter_character(
                    primary=((1, 78, 100),),
                    secondary=(),
                )
            )

            parameters = editor.read_character_parameters(character)

            self.assertEqual(parameters.primary[0].display_name, "Food")
            self.assertEqual(parameters.secondary, ())

    def test_rejects_negative_primary_count(self) -> None:
        data = (
            b"\0" * editor.PRIMARY_PARAMETER_COUNT_OFFSET
            + struct.pack("<i", -1)
        )

        with self.assertRaisesRegex(
            editor.SaveFormatError,
            "Primary parameter count is negative",
        ):
            editor.parse_character_parameters(data)

    def test_rejects_missing_primary_count(self) -> None:
        data = b"\0" * editor.PRIMARY_PARAMETER_COUNT_OFFSET

        with self.assertRaisesRegex(
            editor.SaveFormatError,
            "missing the primary parameter count",
        ):
            editor.parse_character_parameters(data)

    def test_rejects_truncated_primary_records(self) -> None:
        data = (
            b"\0" * editor.PRIMARY_PARAMETER_COUNT_OFFSET
            + struct.pack("<i", 2)
            + struct.pack("<iii", 0, 50, 100)
        )

        with self.assertRaisesRegex(
            editor.SaveFormatError,
            "Primary parameter records extend",
        ):
            editor.parse_character_parameters(data)

    def test_rejects_missing_secondary_count(self) -> None:
        data = (
            b"\0" * editor.PRIMARY_PARAMETER_COUNT_OFFSET
            + struct.pack("<i", 1)
            + struct.pack("<iii", 0, 50, 100)
        )

        with self.assertRaisesRegex(
            editor.SaveFormatError,
            "missing the secondary parameter count",
        ):
            editor.parse_character_parameters(data)

    def test_rejects_negative_secondary_count(self) -> None:
        data = (
            b"\0" * editor.PRIMARY_PARAMETER_COUNT_OFFSET
            + struct.pack("<ii", 0, -1)
        )

        with self.assertRaisesRegex(
            editor.SaveFormatError,
            "Secondary parameter count is negative",
        ):
            editor.parse_character_parameters(data)

    def test_rejects_truncated_secondary_records(self) -> None:
        data = (
            b"\0" * editor.PRIMARY_PARAMETER_COUNT_OFFSET
            + struct.pack("<ii", 0, 2)
            + struct.pack("<if", 16, 21.0)
        )

        with self.assertRaisesRegex(
            editor.SaveFormatError,
            "Secondary parameter records extend",
        ):
            editor.parse_character_parameters(data)

    def test_malformed_parameters_do_not_prevent_cash_editing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            character = Path(temp) / f"{SAVE_ID}_ls"
            malformed = bytearray(make_character(634))
            struct.pack_into(
                "<i",
                malformed,
                editor.PRIMARY_PARAMETER_COUNT_OFFSET,
                -1,
            )
            character.write_bytes(malformed)

            with self.assertRaises(editor.SaveFormatError):
                editor.read_character_parameters(character)

            old_value, _ = editor.set_cash(
                character,
                5000,
                backup_dir=Path(temp) / "backups",
            )

            self.assertEqual(old_value, 634)
            self.assertEqual(editor.read_cash(character), 5000)


class PrimaryParameterEditingTests(unittest.TestCase):
    def test_verified_primary_names_use_user_facing_labels(self) -> None:
        self.assertEqual(editor.PrimaryParameter(3, 1, 2).display_name, "Energy")
        self.assertEqual(
            editor.PrimaryParameter(9, 1, 2).display_name,
            "Bathroom Need",
        )
        self.assertEqual(
            editor.PrimaryParameter(22, 1, 2).display_name,
            "Willpower",
        )

    def test_locates_parameter_by_type_in_unsorted_records(self) -> None:
        data = make_parameter_character(
            primary=((20, 40, 80), (3, 55, 100), (0, 70, 100)),
            secondary=(),
        )

        value_offset, parameter = editor.locate_primary_parameter(data, 3)

        self.assertEqual(parameter, editor.PrimaryParameter(3, 55, 100))
        self.assertEqual(
            struct.unpack_from("<i", data, value_offset)[0],
            55,
        )

    def test_rejects_unknown_missing_and_duplicate_parameter_types(self) -> None:
        data = make_parameter_character(
            primary=((0, 70, 100), (0, 80, 100)),
            secondary=(),
        )

        with self.assertRaisesRegex(ValueError, "Unknown primary parameter"):
            editor.locate_primary_parameter(data, 99)
        with self.assertRaisesRegex(
            editor.SaveFormatError,
            "exactly one Food parameter, found 0",
        ):
            editor.locate_primary_parameter(data, 1)
        with self.assertRaisesRegex(
            editor.SaveFormatError,
            "exactly one Health parameter, found 2",
        ):
            editor.locate_primary_parameter(data, 0)

    def test_set_primary_creates_backup_and_changes_only_current_value(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / f"{SAVE_ID}_ls"
            backup_dir = root / "backups"
            original = make_parameter_character(
                primary=((20, 40, 80), (3, 55, 100), (0, 70, 100)),
                secondary=((18, 12.5),),
                cash=345,
            )
            character.write_bytes(original)
            value_offset, _ = editor.locate_primary_parameter(original, 3)

            old_value, backup = editor.set_primary_parameter(
                character,
                3,
                90,
                now=datetime(2026, 6, 13, 2, 30, 0),
                backup_dir=backup_dir,
            )

            self.assertEqual(old_value, 55)
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(
                backup.name,
                f"{SAVE_ID}_ls.bak-20260613-023000",
            )
            expected = bytearray(original)
            struct.pack_into("<i", expected, value_offset, 90)
            self.assertEqual(character.read_bytes(), expected)
            self.assertEqual(editor.read_cash(character), 345)

    def test_accepts_zero_and_stored_maximum_boundaries(self) -> None:
        for new_value in (0, 100):
            with self.subTest(new_value=new_value):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    character = root / f"{SAVE_ID}_ls"
                    character.write_bytes(
                        make_parameter_character(
                            primary=((0, 70, 100),),
                            secondary=(),
                        )
                    )

                    editor.set_primary_parameter(
                        character,
                        0,
                        new_value,
                        backup_dir=root / "backups",
                    )

                    parameters = editor.read_character_parameters(character)
                    self.assertEqual(
                        parameters.primary[0].current_value,
                        new_value,
                    )
                    self.assertEqual(
                        parameters.primary[0].maximum_value,
                        100,
                    )

    def test_rejects_out_of_range_and_negative_maximum(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / f"{SAVE_ID}_ls"
            backup_dir = root / "backups"
            original = make_parameter_character(
                primary=((0, 70, 100),),
                secondary=(),
            )
            character.write_bytes(original)

            with self.assertRaises(ValueError):
                editor.set_primary_parameter(
                    character,
                    0,
                    101,
                    backup_dir=backup_dir,
                )

            character.write_bytes(
                make_parameter_character(
                    primary=((0, 70, -1),),
                    secondary=(),
                )
            )
            with self.assertRaisesRegex(
                editor.SaveFormatError,
                "negative maximum",
            ):
                editor.set_primary_parameter(
                    character,
                    0,
                    0,
                    backup_dir=backup_dir,
                )
            self.assertFalse(backup_dir.exists())

    def test_primary_backup_collision_uses_numeric_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / f"{SAVE_ID}_ls"
            backup_dir = root / "backups"
            backup_dir.mkdir()
            character.write_bytes(
                make_parameter_character(
                    primary=((0, 70, 100),),
                    secondary=(),
                )
            )
            stamp = datetime(2026, 6, 13, 2, 30, 0)
            base = backup_dir / f"{SAVE_ID}_ls.bak-20260613-023000"
            base.write_bytes(b"existing")

            _, backup = editor.set_primary_parameter(
                character,
                0,
                80,
                now=stamp,
                backup_dir=backup_dir,
            )

            self.assertEqual(backup.name, f"{base.name}-1")
            self.assertEqual(base.read_bytes(), b"existing")

    def test_primary_backup_failure_leaves_save_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / f"{SAVE_ID}_ls"
            original = make_parameter_character(
                primary=((0, 70, 100),),
                secondary=(),
            )
            character.write_bytes(original)
            invalid_backup_dir = root / "not-a-directory"
            invalid_backup_dir.write_text("occupied", encoding="utf-8")

            with self.assertRaises(OSError):
                editor.set_primary_parameter(
                    character,
                    0,
                    80,
                    backup_dir=invalid_backup_dir,
                )

            self.assertEqual(character.read_bytes(), original)

    def test_verification_failure_restores_original_primary_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / f"{SAVE_ID}_ls"
            original = make_parameter_character(
                primary=((0, 70, 100),),
                secondary=(),
            )
            character.write_bytes(original)
            real_read_bytes = Path.read_bytes
            character_reads = 0

            def unreliable_read_bytes(path: Path) -> bytes:
                nonlocal character_reads
                data = real_read_bytes(path)
                if path == character:
                    character_reads += 1
                    if character_reads == 4:
                        return data + b"verification mismatch"
                return data

            with mock.patch.object(
                Path,
                "read_bytes",
                new=unreliable_read_bytes,
            ):
                with self.assertRaisesRegex(OSError, "Verification failed"):
                    editor.set_primary_parameter(
                        character,
                        0,
                        80,
                        backup_dir=root / "backups",
                    )

            self.assertEqual(character.read_bytes(), original)


class AtomicWriteSafetyTests(unittest.TestCase):
    def test_rejects_backup_directory_inside_game_installation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp) / editor.GAME_DIR_NAME
            character = (
                game
                / editor.SAVE_DIR
                / "12345"
                / "NFS_Characters"
                / f"{SAVE_ID}_ls"
            )
            character.parent.mkdir(parents=True)
            original = make_character(634)
            character.write_bytes(original)
            backup_dir = game / "unsafe-backups"

            with self.assertRaisesRegex(
                ValueError,
                "outside the game installation",
            ):
                editor.set_cash(
                    character,
                    5000,
                    backup_dir=backup_dir,
                )

            self.assertEqual(character.read_bytes(), original)
            self.assertFalse(backup_dir.exists())

    def test_rejects_stale_original_before_creating_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / f"{SAVE_ID}_ls"
            current = make_character(634)
            character.write_bytes(current)
            backup_dir = root / "backups"

            with self.assertRaisesRegex(
                editor.SaveFormatError,
                "changed after it was read",
            ):
                editor._write_updated_character(
                    character,
                    b"stale original",
                    b"updated",
                    backup_dir=backup_dir,
                )

            self.assertEqual(character.read_bytes(), current)
            self.assertFalse(backup_dir.exists())


class CashEditingTests(unittest.TestCase):
    def test_set_cash_creates_backup_and_changes_only_the_integer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character_dir = root / "game" / "NFS_Characters"
            character_dir.mkdir(parents=True)
            character = character_dir / f"{SAVE_ID}_ls"
            backup_dir = root / "backups"
            original = make_character(634)
            character.write_bytes(original)

            old_value, backup = editor.set_cash(
                character,
                5000,
                now=datetime(2026, 6, 13, 1, 30, 0),
                backup_dir=backup_dir,
            )

            self.assertEqual(old_value, 634)
            self.assertEqual(editor.read_cash(character), 5000)
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(backup.parent, backup_dir)
            self.assertEqual(backup.name, f"{SAVE_ID}_ls.bak-20260613-013000")
            self.assertEqual(list(character_dir.glob("*.bak-*")), [])

            changed = character.read_bytes()
            offset, _ = editor.locate_cash(original)
            expected = bytearray(original)
            struct.pack_into("<i", expected, offset, 5000)
            self.assertEqual(changed, expected)

    def test_rejects_ambiguous_cash_fields(self) -> None:
        data = make_character(1) + editor.CASH_KEY + struct.pack("<i", 2)
        with self.assertRaises(editor.SaveFormatError):
            editor.locate_cash(data)

    def test_rejects_out_of_range_cash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            character = Path(temp) / f"{SAVE_ID}_ls"
            character.write_bytes(make_character(634))
            with self.assertRaises(ValueError):
                editor.set_cash(character, editor.MAX_CASH + 1)

    def test_set_cash_uses_numeric_suffix_for_backup_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / f"{SAVE_ID}_ls"
            backup_dir = root / "backups"
            backup_dir.mkdir()
            character.write_bytes(make_character(634))
            stamp = datetime(2026, 6, 13, 1, 30, 0)
            base = backup_dir / f"{SAVE_ID}_ls.bak-20260613-013000"
            base.write_bytes(b"existing")

            _, backup = editor.set_cash(
                character,
                5000,
                now=stamp,
                backup_dir=backup_dir,
            )

            self.assertEqual(backup.name, f"{base.name}-1")
            self.assertEqual(base.read_bytes(), b"existing")

    def test_backup_directory_failure_leaves_save_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / f"{SAVE_ID}_ls"
            original = make_character(634)
            character.write_bytes(original)
            invalid_backup_dir = root / "not-a-directory"
            invalid_backup_dir.write_text("occupied", encoding="utf-8")

            with self.assertRaises(OSError):
                editor.set_cash(
                    character,
                    5000,
                    backup_dir=invalid_backup_dir,
                )

            self.assertEqual(character.read_bytes(), original)


class BackupDirectoryTests(unittest.TestCase):
    def test_default_backup_directories_follow_platform_conventions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "test_home"
            xdg_data_home = Path(temp) / "var" / "data" / "test"

            self.assertEqual(
                editor.default_backup_dir(
                    home=Path("C:/Users/Test"),
                    env={"LOCALAPPDATA": "C:/Users/Test/AppData/Local"},
                    platform="win32",
                ),
                Path("C:/Users/Test/AppData/Local/Hobo Save Editor/Backups"),
            )
            self.assertEqual(
                editor.default_backup_dir(home=home, env={}, platform="darwin"),
                home
                / "Library"
                / "Application Support"
                / "Hobo Save Editor"
                / "Backups",
            )
            self.assertEqual(
                editor.default_backup_dir(
                    home=home,
                    env={"XDG_DATA_HOME": str(xdg_data_home)},
                    platform="linux",
                ),
                xdg_data_home / "hobo-save-editor" / "backups",
            )
            self.assertEqual(
                editor.default_backup_dir(home=home, env={}, platform="linux"),
                home / ".local/share/hobo-save-editor/backups",
            )

    def test_cli_backup_directory_takes_precedence_over_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cli_dir = root / "cli"
            env_dir = root / "env"

            selected = editor.resolve_backup_dir(
                cli_dir,
                env={editor.BACKUP_DIR_ENV: str(env_dir)},
            )

            self.assertEqual(selected, cli_dir.absolute())

    def test_environment_backup_directory_overrides_platform_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env_dir = Path(temp) / "env"

            selected = editor.resolve_backup_dir(
                env={editor.BACKUP_DIR_ENV: str(env_dir)},
            )

            self.assertEqual(selected, env_dir.absolute())


class DiscoveryTests(unittest.TestCase):
    def test_discovers_game_from_modern_libraryfolders_vdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            steam = root / "Steam"
            library = root / "Games"
            game = library / "steamapps" / "common" / editor.GAME_DIR_NAME
            (game / "HoboRPG_Data").mkdir(parents=True)
            config = steam / "steamapps" / "libraryfolders.vdf"
            config.parent.mkdir(parents=True)
            config.write_text(
                '"libraryfolders"\n'
                "{\n"
                '  "0"\n'
                "  {\n"
                f'    "path" "{library}"\n'
                "  }\n"
                "}\n",
                encoding="utf-8",
            )

            installs = editor.discover_game_installs(steam_roots=[steam])

            self.assertEqual(installs, [game.absolute()])

    def test_parses_legacy_libraryfolders_vdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "libraryfolders.vdf"
            games_dir = Path(temp) / "games" / "SteamLibrary"
            config.write_text(
                '"LibraryFolders"\n'
                "{\n"
                '  "TimeNextStatsReport" "123"\n'
                f'  "1" "{games_dir.as_posix()}"\n'
                "}\n",
                encoding="utf-8",
            )

            self.assertEqual(
                editor.parse_library_folders(config),
                [games_dir],
            )

    def test_default_linux_roots_include_flatpak_steam(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "test_home"
            roots = editor.default_steam_roots(
                home=home, env={}, platform="linux"
            )
            self.assertIn(
                home
                / ".var"
                / "app"
                / "com.valvesoftware.Steam"
                / ".local"
                / "share"
                / "Steam",
                roots,
            )


if __name__ == "__main__":
    unittest.main()
