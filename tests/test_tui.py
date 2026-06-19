from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path
from typing import Optional

from textual.widgets import (
    Button,
    DataTable,
    Input,
    ListView,
    Static,
    TabbedContent,
)

from hobo_rpg_save_editor import editor
from hobo_rpg_save_editor import inventory
from hobo_rpg_save_editor import npc
from hobo_rpg_save_editor import quests
from hobo_rpg_save_editor import reputation
from hobo_rpg_save_editor.tui import (
    ConfirmationModal,
    EditableValueListItem,
    EditValueModal,
    HoboSaveEditorApp,
    InstallationScreen,
    IntegerEditModal,
    InventoryApplyConfirmationModal,
    InventoryQuantityModal,
    InventoryScreen,
    MessageModal,
    NpcScreen,
    PathEntryModal,
    QuestScreen,
)
from tests.test_inventory import (
    make_bag_transfer_catalog,
    make_bag_transfer_character,
)


SAVE_IDS = (
    "e1f4a340-ecbf-4f71-a80f-8f2ed6b26b3b",
    "7ca4f521-22ba-4ceb-839f-84cfdadeb4ea",
)
NPC_FLAG_KEYS = (
    "BrunoSpoluprace",
    "BrunoNasrani",
    *(f"NpcFlag{index:03}" for index in range(786)),
)
NPC_FLAG_DIGEST = npc.npc_flag_key_digest(NPC_FLAG_KEYS)


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


def make_slot(save_id: str, name: str) -> bytes:
    data = bytearray()
    data += encode_string(f"${save_id}")
    data += struct.pack("<I", 0)
    data += encode_string(name)
    data += struct.pack("<IIQ", 0, 2, 0)
    data += encode_string("06/13/2026 01:11:26")
    return bytes(data).ljust(256, b"\0")


def make_character(
    cash: int,
    primary: tuple[tuple[int, int, int], ...] = (
        (0, 86, 100),
        (3, 75, 100),
        (9, 20, 100),
        (20, 86, 86),
        (22, 4, 5),
        (77, -2, 10),
    ),
    secondary: tuple[tuple[int, float], ...] = (
        (18, 12.0),
        (16, 21.0),
    ),
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
    return bytes(data).ljust(1024, b"\0")


def with_reputation_table(
    data: bytes,
    values: Optional[dict[int, int]] = None,
) -> bytes:
    values = values or {}
    records = bytearray()
    for archetype_id in reputation.OBSERVED_REPUTATION_ARCHETYPE_IDS:
        records += struct.pack(
            "<ii",
            archetype_id,
            values.get(archetype_id, 10),
        )
    return data + struct.pack(
        "<i",
        reputation.OBSERVED_REPUTATION_COUNT,
    ) + records


def with_npc_flags(
    data: bytes,
    values: Optional[dict[str, int]] = None,
) -> bytes:
    values = values or {}
    records = bytearray()
    for key in NPC_FLAG_KEYS:
        raw_key = key.encode("ascii")
        records += encode_7bit(len(raw_key))
        records += raw_key
        records += struct.pack("<i", values.get(key, 0))
    return data + struct.pack("<i", len(NPC_FLAG_KEYS)) + records


def parse_test_npc_data(data: bytes) -> npc.NpcData:
    return npc.parse_npc_data(
        data,
        expected_flag_count=len(NPC_FLAG_KEYS),
        expected_flag_key_digest=NPC_FLAG_DIGEST,
    )


def write_test_npc_plan(path, plan, **kwargs):
    return npc.write_npc_edit_plan(
        path,
        plan,
        expected_flag_count=len(NPC_FLAG_KEYS),
        expected_flag_key_digest=NPC_FLAG_DIGEST,
        **kwargs,
    )


def load_test_npc_annotations(
    game_dir: Path,
    keys: tuple[str, ...],
) -> npc.NpcFlagAnnotationCatalog:
    del game_dir
    annotations = dict(
        npc.fallback_npc_flag_annotations(keys).annotations
    )
    annotations["BrunoSpoluprace"] = npc.NpcFlagAnnotation(
        key="BrunoSpoluprace",
        associations=("Bruno", "Quest: Who's Bruno?"),
        meaning=(
            'Controls dialogue: "I decided to work for Bruno after '
            'talking to Anton."'
        ),
        inferred=False,
        evidence=("specific_bruno.json",),
    )
    annotations["BrunoNasrani"] = npc.NpcFlagAnnotation(
        key="BrunoNasrani",
        associations=("Bruno",),
        meaning="Bruno is angry with the player (inferred from key)",
        inferred=True,
        evidence=(),
    )
    return npc.NpcFlagAnnotationCatalog(annotations=annotations)


def load_test_quest_catalog(game_dir: Path) -> quests.QuestCatalog:
    del game_dir
    return quests.QuestCatalog(
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
                        key="BrunoSpoluprace",
                        value=1,
                        role="sets",
                        text="Work with Bruno after talking to Anton.",
                    ),
                    quests.QuestFlagReference(
                        key="BrunoNasrani",
                        value=1,
                        role="sets",
                        text="Make Bruno angry.",
                    ),
                ),
                evidence=("Work with Bruno after talking to Anton.",),
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


def create_game(
    root: Path,
    saves: tuple[tuple[str, str, int], ...] = (),
) -> Path:
    game = root / editor.GAME_DIR_NAME
    data_dir = game / "HoboRPG_Data"
    data_dir.mkdir(parents=True)
    account = game / editor.SAVE_DIR / "12345"
    configs = account / "NFS_WorldConfigs"
    characters = account / "NFS_Characters"
    configs.mkdir(parents=True, exist_ok=True)
    characters.mkdir(parents=True, exist_ok=True)
    for index, (save_id, name, cash) in enumerate(saves):
        (configs / f"slot{index}").write_bytes(make_slot(save_id, name))
        (characters / f"{save_id}_ls").write_bytes(make_character(cash))
    return game


class TextualStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_zero_discoveries_requests_and_validates_manual_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = HoboSaveEditorApp(
                backup_dir=root / "backups",
                discover_installs=lambda: [],
            )
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                self.assertIsInstance(app.screen, PathEntryModal)

                path_input = app.screen.query_one("#game-path", Input)
                path_input.value = str(root / "missing")
                await pilot.click("#accept-path")
                await pilot.pause()

                error = app.screen.query_one("#path-error", Static)
                self.assertIn("does not contain HoboRPG_Data", str(error.render()))
                await pilot.press("q")

    async def test_one_discovery_loads_editor_and_shows_empty_warning_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = create_game(root)
            app = HoboSaveEditorApp(
                backup_dir=root / "backups",
                discover_installs=lambda: [game],
                scan_saves=lambda _: ([], ["bad slot"]),
            )
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()

                self.assertEqual(app.game_dir, game.absolute())
                self.assertTrue(
                    app.query_one("#empty-state", Static).has_class("visible")
                )
                warnings = app.query_one("#warnings", Static)
                self.assertTrue(warnings.has_class("visible"))
                self.assertIn("bad slot", str(warnings.render()))

    async def test_multiple_discoveries_support_keyboard_selection(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = create_game(root / "one")
            second = create_game(root / "two")
            app = HoboSaveEditorApp(
                backup_dir=root / "backups",
                discover_installs=lambda: [first, second],
            )
            async with app.run_test() as pilot:
                await pilot.pause()
                self.assertIsInstance(app.screen, InstallationScreen)

                await pilot.press("down", "enter")
                await pilot.pause()

                self.assertEqual(app.game_dir, second.absolute())

    async def test_invalid_startup_path_is_corrected_inside_tui(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = create_game(root / "valid")
            app = HoboSaveEditorApp(
                game_dir=root / "invalid",
                backup_dir=root / "backups",
            )
            async with app.run_test() as pilot:
                await pilot.pause()
                self.assertIsInstance(app.screen, PathEntryModal)

                app.screen.query_one("#game-path", Input).value = str(game)
                await pilot.press("enter")
                await pilot.pause()

                self.assertEqual(app.game_dir, game.absolute())


class TextualEditorTests(unittest.IsolatedAsyncioTestCase):
    async def test_displays_character_parameters_inline(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = create_game(
                root,
                ((SAVE_IDS[0], "First", 100),),
            )
            app = HoboSaveEditorApp(
                game_dir=game,
                backup_dir=root / "backups",
            )
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()

                details = str(
                    app.query_one("#save-details", Static).render()
                )
                self.assertIn("Primary parameters:", details)
                self.assertIn("Health: 86 / 100", details)
                self.assertIn("Energy: 75 / 100", details)
                self.assertIn("Bathroom Need: 20 / 100", details)
                self.assertIn("Stamina: 86 / 86", details)
                self.assertIn("Willpower: 4 / 5", details)
                self.assertIn("Unknown (77): -2 / 10", details)
                self.assertIn(
                    "Secondary parameters (read-only, unverified):",
                    details,
                )
                self.assertIn("Defense: 12", details)
                self.assertIn("Immunity: 21", details)
                self.assertNotIn("WetResistance", details)

    async def test_parameter_failure_keeps_cash_edit_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = create_game(
                root,
                ((SAVE_IDS[0], "First", 100),),
            )
            character = (
                game
                / editor.SAVE_DIR
                / "12345"
                / "NFS_Characters"
                / f"{SAVE_IDS[0]}_ls"
            )
            data = bytearray(character.read_bytes())
            struct.pack_into(
                "<i",
                data,
                editor.PRIMARY_PARAMETER_COUNT_OFFSET,
                -1,
            )
            character.write_bytes(data)
            app = HoboSaveEditorApp(
                game_dir=game,
                backup_dir=root / "backups",
            )
            async with app.run_test() as pilot:
                await pilot.pause()

                details = str(
                    app.query_one("#save-details", Static).render()
                )
                self.assertIn("Cash: 100", details)
                self.assertIn(
                    "Parameters unavailable: "
                    "Primary parameter count is negative",
                    details,
                )
                self.assertFalse(
                    app.query_one("#edit-value", Button).disabled
                )

                await pilot.press("e")
                await pilot.pause()
                self.assertIsInstance(app.screen, EditValueModal)

    async def test_selection_and_refresh_preserve_save_uuid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = create_game(
                root,
                (
                    (SAVE_IDS[0], "First", 100),
                    (SAVE_IDS[1], "Second", 200),
                ),
            )
            records, warnings = editor.scan_saves(game)
            self.assertFalse(warnings)
            scan_count = 0

            def changing_scan(_: Path):
                nonlocal scan_count
                scan_count += 1
                return (
                    records if scan_count == 1 else list(reversed(records)),
                    [],
                )

            app = HoboSaveEditorApp(
                game_dir=game,
                backup_dir=root / "backups",
                scan_saves=changing_scan,
            )
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("down")
                await pilot.pause()
                self.assertEqual(app.selected_record.save_id, SAVE_IDS[1])

                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, EditValueModal)
                await pilot.press("escape")
                await pilot.pause()

                await pilot.press("r")
                await pilot.pause()

                self.assertEqual(app.selected_record.save_id, SAVE_IDS[1])
                self.assertEqual(
                    app.query_one("#save-list", ListView).index,
                    0,
                )

    async def test_cash_flow_validates_cancels_then_reports_backup(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = create_game(
                root,
                ((SAVE_IDS[0], "First", 100),),
            )
            character = (
                game
                / editor.SAVE_DIR
                / "12345"
                / "NFS_Characters"
                / f"{SAVE_IDS[0]}_ls"
            )
            app = HoboSaveEditorApp(
                game_dir=game,
                backup_dir=root / "backups",
            )
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await pilot.press("e")
                await pilot.pause()
                self.assertIsInstance(app.screen, EditValueModal)
                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, IntegerEditModal)

                value_input = app.screen.query_one("#value-input", Input)
                value_input.value = str(editor.MAX_CASH + 1)
                await pilot.pause()
                self.assertTrue(
                    app.screen.query_one("#accept-value", Button).disabled
                )
                self.assertIn(
                    "whole number",
                    str(
                        app.screen.query_one(
                            "#value-error",
                            Static,
                        ).render()
                    ),
                )

                value_input.value = "5000"
                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, ConfirmationModal)
                await pilot.press("escape")
                await pilot.pause()
                self.assertEqual(editor.read_cash(character), 100)

                await pilot.press("e")
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                app.screen.query_one("#value-input", Input).value = "5000"
                await pilot.press("enter")
                await pilot.pause()
                await pilot.click("#confirm-change")
                await pilot.pause()

                self.assertEqual(editor.read_cash(character), 5000)
                self.assertIsNotNone(app.last_backup_path)
                self.assertTrue(app.last_backup_path.is_file())
                result = app.query_one("#result", Static)
                self.assertTrue(result.has_class("visible"))
                self.assertIn(str(app.last_backup_path), str(result.render()))

    async def test_primary_edit_uses_mouse_and_refreshes_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = create_game(
                root,
                ((SAVE_IDS[0], "First", 100),),
            )
            character = (
                game
                / editor.SAVE_DIR
                / "12345"
                / "NFS_Characters"
                / f"{SAVE_IDS[0]}_ls"
            )
            app = HoboSaveEditorApp(
                game_dir=game,
                backup_dir=root / "backups",
            )
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.query_one("#edit-value").scroll_visible(animate=False)
                await pilot.pause()
                await pilot.click("#edit-value")
                await pilot.pause()
                self.assertIsInstance(app.screen, EditValueModal)

                value_list = app.screen.query_one(
                    "#edit-value-list",
                    ListView,
                )
                health_item = value_list.children[1]
                self.assertIsInstance(health_item, EditableValueListItem)
                await pilot.click(health_item)
                await pilot.pause()
                self.assertIsInstance(app.screen, IntegerEditModal)

                value_input = app.screen.query_one("#value-input", Input)
                value_input.value = "101"
                await pilot.pause()
                self.assertTrue(
                    app.screen.query_one("#accept-value", Button).disabled
                )
                value_input.value = "95"
                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, ConfirmationModal)
                self.assertIn(
                    "Change Health from 86 to 95?",
                    str(
                        app.screen.query_one(
                            "#confirmation-message",
                            Static,
                        ).render()
                    ),
                )
                await pilot.click("#confirm-change")
                await pilot.pause()

                parameters = editor.read_character_parameters(character)
                health = next(
                    parameter
                    for parameter in parameters.primary
                    if parameter.parameter_type == 0
                )
                self.assertEqual(health.current_value, 95)
                self.assertEqual(health.maximum_value, 100)
                details = str(
                    app.query_one("#save-details", Static).render()
                )
                self.assertIn("Health: 95 / 100", details)
                self.assertIn(
                    "Health changed from 86 to 95",
                    str(app.query_one("#result", Static).render()),
                )
                self.assertTrue(app.last_backup_path.is_file())

    async def test_chooser_excludes_unknown_duplicate_and_bad_maximum(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = create_game(
                root,
                ((SAVE_IDS[0], "First", 100),),
            )
            character = (
                game
                / editor.SAVE_DIR
                / "12345"
                / "NFS_Characters"
                / f"{SAVE_IDS[0]}_ls"
            )
            character.write_bytes(
                make_character(
                    100,
                    primary=(
                        (0, 50, 100),
                        (0, 60, 100),
                        (1, 70, -1),
                        (3, 80, 100),
                        (77, 1, 10),
                    ),
                    secondary=(),
                )
            )
            app = HoboSaveEditorApp(
                game_dir=game,
                backup_dir=root / "backups",
            )
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("e")
                await pilot.pause()

                value_list = app.screen.query_one(
                    "#edit-value-list",
                    ListView,
                )
                labels = [
                    item.target.label
                    for item in value_list.children
                    if isinstance(item, EditableValueListItem)
                ]
                self.assertEqual(labels, ["Cash", "Energy"])

    async def test_primary_write_failure_keeps_state_and_shows_error(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = create_game(
                root,
                ((SAVE_IDS[0], "First", 100),),
            )
            character = (
                game
                / editor.SAVE_DIR
                / "12345"
                / "NFS_Characters"
                / f"{SAVE_IDS[0]}_ls"
            )

            def fail_primary_write(*args, **kwargs):
                raise OSError("disk is read-only")

            app = HoboSaveEditorApp(
                game_dir=game,
                backup_dir=root / "backups",
                set_primary_parameter=fail_primary_write,
            )
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await pilot.press("e")
                await pilot.pause()
                await pilot.press("down", "enter")
                await pilot.pause()
                app.screen.query_one("#value-input", Input).value = "95"
                await pilot.press("enter")
                await pilot.pause()
                await pilot.click("#confirm-change")
                await pilot.pause()

                self.assertIsInstance(app.screen, MessageModal)
                self.assertIn(
                    "disk is read-only",
                    str(
                        app.screen.query_one(
                            "#message-body",
                            Static,
                        ).render()
                    ),
                )
                parameters = editor.read_character_parameters(character)
                self.assertEqual(parameters.primary[0].current_value, 86)
                self.assertIsNone(app.last_backup_path)

    async def test_npc_data_unavailable_does_not_block_other_editors(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = create_game(
                root,
                ((SAVE_IDS[0], "First", 100),),
            )
            app = HoboSaveEditorApp(
                game_dir=game,
                backup_dir=root / "backups",
            )
            async with app.run_test() as pilot:
                await pilot.pause()

                details = str(
                    app.query_one("#save-details", Static).render()
                )
                self.assertIn("NPC data unavailable:", details)
                self.assertTrue(
                    app.query_one("#npc-action", Button).disabled
                )
                self.assertFalse(
                    app.query_one("#edit-value", Button).disabled
                )
                self.assertFalse(
                    app.query_one("#inventory-action", Button).disabled
                )

    async def test_npc_screen_stages_reverts_and_applies_mixed_changes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = create_game(
                root,
                ((SAVE_IDS[0], "First", 100),),
            )
            character = (
                game
                / editor.SAVE_DIR
                / "12345"
                / "NFS_Characters"
                / f"{SAVE_IDS[0]}_ls"
            )
            character.write_bytes(
                with_npc_flags(
                    with_reputation_table(
                        make_character(100),
                        {28: 80},
                    ),
                    {"BrunoNasrani": 1},
                )
            )
            app = HoboSaveEditorApp(
                game_dir=game,
                backup_dir=root / "backups",
                npc_parser=parse_test_npc_data,
                npc_annotation_loader=load_test_npc_annotations,
                npc_writer=write_test_npc_plan,
            )

            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                details = str(
                    app.query_one("#save-details", Static).render()
                )
                self.assertIn("NPC reputation records: 113", details)
                self.assertIn("NPC flags: 788", details)
                self.assertFalse(
                    app.query_one("#npc-action", Button).disabled
                )

                await pilot.press("p")
                await pilot.pause()
                self.assertIsInstance(app.screen, NpcScreen)

                search = app.screen.query_one(
                    "#npc-reputation-search",
                    Input,
                )
                table = app.screen.query_one(
                    "#npc-reputation-table",
                    DataTable,
                )
                search.value = "bruno"
                await pilot.pause()
                self.assertEqual(
                    tuple(str(value) for value in table.get_row_at(0)),
                    ("Bruno", "80", "-", "28"),
                )
                search.value = "Specific_Bruno"
                await pilot.pause()
                self.assertEqual(
                    len(app.screen.visible_reputation_ids),
                    1,
                )
                search.value = "28"
                await pilot.pause()
                self.assertEqual(
                    app.screen.visible_reputation_ids,
                    [28],
                )

                await pilot.click("#npc-edit")
                await pilot.pause()
                value_input = app.screen.query_one("#value-input", Input)
                value_input.value = "101"
                await pilot.pause()
                self.assertTrue(
                    app.screen.query_one("#accept-value", Button).disabled
                )
                value_input.value = "81"
                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, NpcScreen)

                search = app.screen.query_one(
                    "#npc-reputation-search",
                    Input,
                )
                search.value = "Hobo_Crazy"
                await pilot.pause()
                await pilot.click("#npc-edit")
                await pilot.pause()
                app.screen.query_one("#value-input", Input).value = "75"
                await pilot.press("enter")
                await pilot.pause()

                search = app.screen.query_one(
                    "#npc-reputation-search",
                    Input,
                )
                search.value = "Specific_Dory"
                await pilot.pause()
                await pilot.click("#npc-edit")
                await pilot.pause()
                app.screen.query_one("#value-input", Input).value = "20"
                await pilot.press("enter")
                await pilot.pause()
                await pilot.click("#npc-edit")
                await pilot.pause()
                app.screen.query_one("#value-input", Input).value = "10"
                await pilot.press("enter")
                await pilot.pause()

                self.assertEqual(
                    app.screen.staged_reputation_values,
                    {28: 81, 160: 75},
                )

                tabs = app.screen.query_one("#npc-tabs", TabbedContent)
                tabs.active = "npc-flags-pane"
                await pilot.pause()
                flag_search = app.screen.query_one(
                    "#npc-flags-search",
                    Input,
                )
                flag_search.value = "decided to work"
                await pilot.pause()
                self.assertEqual(
                    app.screen.visible_flag_keys,
                    ["BrunoSpoluprace"],
                )
                flag_table = app.screen.query_one(
                    "#npc-flags-table",
                    DataTable,
                )
                columns = tuple(flag_table.columns.values())
                self.assertEqual(
                    tuple(str(column.label) for column in columns),
                    (
                        "Flag",
                        "Current",
                        "Staged",
                        "Associated with",
                        "English meaning",
                    ),
                )
                self.assertEqual(
                    tuple(column.width for column in columns),
                    (24, 7, 6, 16, 32),
                )
                self.assertTrue(
                    all(not column.auto_width for column in columns)
                )
                self.assertEqual(
                    tuple(str(value) for value in flag_table.get_row_at(0)),
                    (
                        "BrunoSpoluprace",
                        "0",
                        "-",
                        "Bruno, Quest: Who's Bruno?",
                        (
                            'Controls dialogue: "I decided to work for '
                            'Bruno after talking to Anton."'
                        ),
                    ),
                )
                detail = app.screen.query_one("#npc-flag-detail", Static)
                detail_text = str(detail.render())
                self.assertIn("Flag: BrunoSpoluprace", detail_text)
                self.assertIn(
                    "Bruno, Quest: Who's Bruno?",
                    detail_text,
                )
                self.assertIn(
                    (
                        'Controls dialogue: "I decided to work for '
                        'Bruno after talking to Anton."'
                    ),
                    detail_text,
                )

                flag_search.value = "bruno"
                await pilot.pause()
                flag_table.focus()
                await pilot.press("down")
                await pilot.pause()
                self.assertIn(
                    "Flag: BrunoNasrani",
                    str(detail.render()),
                )

                flag_search.value = "no matching flag annotation"
                await pilot.pause()
                self.assertEqual(
                    str(detail.render()),
                    "No flags match the current search.",
                )

                flag_search.value = "decided to work"
                await pilot.pause()
                flag_table.focus()
                await pilot.press("enter")
                await pilot.pause()
                self.assertEqual(
                    app.screen.staged_flag_values,
                    {"BrunoSpoluprace": 1},
                )
                await pilot.press("enter")
                await pilot.pause()
                self.assertEqual(app.screen.staged_flag_values, {})
                await pilot.press("enter")
                await pilot.pause()
                self.assertEqual(
                    app.screen.staged_flag_values,
                    {"BrunoSpoluprace": 1},
                )

                await pilot.click("#npc-apply")
                await pilot.pause()
                self.assertIsInstance(
                    app.screen,
                    InventoryApplyConfirmationModal,
                )
                summary = str(
                    app.screen.query_one(
                        "#inventory-apply-summary",
                        Static,
                    ).render()
                )
                self.assertIn("quest flags are experimental", summary)
                self.assertIn("Bruno: 80 -> 81", summary)
                self.assertIn("Crazy: 10 -> 75", summary)
                self.assertIn("Flag BrunoSpoluprace: 0 -> 1", summary)
                self.assertNotIn("Dory", summary)
                await pilot.click("#confirm-inventory-apply")
                await pilot.pause()

                result = str(app.query_one("#result", Static).render())
                self.assertIn("NPC data updated", result)
                self.assertIn(str(app.last_backup_path), result)

            updated = parse_test_npc_data(character.read_bytes())
            self.assertEqual(updated.reputation.get(28).value, 81)
            self.assertEqual(updated.reputation.get(160).value, 75)
            self.assertEqual(updated.reputation.get(3).value, 10)
            self.assertEqual(
                updated.flags.get("BrunoSpoluprace").value,
                1,
            )
            self.assertEqual(updated.flags.get("BrunoNasrani").value, 1)
            self.assertEqual(len(list((root / "backups").iterdir())), 1)

    async def test_quest_screen_filters_and_opens_related_flags(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = create_game(
                root,
                ((SAVE_IDS[0], "First", 100),),
            )
            character = (
                game
                / editor.SAVE_DIR
                / "12345"
                / "NFS_Characters"
                / f"{SAVE_IDS[0]}_ls"
            )
            character.write_bytes(
                with_npc_flags(
                    with_reputation_table(make_character(100)),
                    {"BrunoSpoluprace": 1},
                )
            )
            app = HoboSaveEditorApp(
                game_dir=game,
                backup_dir=root / "backups",
                npc_parser=parse_test_npc_data,
                npc_annotation_loader=load_test_npc_annotations,
                quest_catalog_loader=load_test_quest_catalog,
                npc_writer=write_test_npc_plan,
            )

            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                self.assertFalse(
                    app.query_one("#quest-action", Button).disabled
                )

                app.query_one("#quest-action").scroll_visible(animate=False)
                await pilot.pause()
                await pilot.click("#quest-action")
                await pilot.pause()
                self.assertIsInstance(app.screen, QuestScreen)

                table = app.screen.query_one("#quest-table", DataTable)
                self.assertEqual(
                    tuple(str(column.label) for column in table.columns.values()),
                    ("Quest", "Likely status", "Flags", "Type"),
                )
                search = app.screen.query_one("#quest-search", Input)
                search.value = "bruno"
                await pilot.pause()
                self.assertEqual(len(app.screen.visible_indices), 1)
                self.assertEqual(
                    tuple(str(value) for value in table.get_row_at(0)),
                    ("Who's Bruno?", "In progress", "1/2", "Quest"),
                )
                detail = app.screen.query_one("#quest-detail", Static)
                detail_text = str(detail.render())
                self.assertIn("Status: In progress", detail_text)
                self.assertIn("BrunoSpoluprace", detail_text)
                self.assertIn(
                    "Work with Bruno after talking to Anton.",
                    detail_text,
                )

                search.value = ""
                await pilot.pause()
                await pilot.click("#quest-filter-no-flags")
                await pilot.pause()
                self.assertEqual(
                    tuple(str(value) for value in table.get_row_at(0)),
                    ("Temp Work", "No save flags", "0/0", "Repeatable / temp"),
                )

                await pilot.click("#quest-filter-all")
                await pilot.pause()
                search.value = "bruno"
                await pilot.pause()
                await pilot.click("#quest-open-flags")
                await pilot.pause()
                self.assertIsInstance(app.screen, NpcScreen)
                flag_search = app.screen.query_one("#npc-flags-search", Input)
                self.assertEqual(flag_search.value, "Quest: Who's Bruno?")
                self.assertEqual(
                    app.screen.visible_flag_keys,
                    ["BrunoSpoluprace", "BrunoNasrani"],
                )

    async def test_npc_back_confirms_discard_and_writer_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = create_game(
                root,
                ((SAVE_IDS[0], "First", 100),),
            )
            character = (
                game
                / editor.SAVE_DIR
                / "12345"
                / "NFS_Characters"
                / f"{SAVE_IDS[0]}_ls"
            )
            original = with_npc_flags(
                with_reputation_table(
                    make_character(100),
                    {28: 80},
                ),
            )
            character.write_bytes(original)

            def fail_npc_write(*args, **kwargs):
                raise OSError("NPC disk failure")

            def fail_annotation_load(*args, **kwargs):
                raise npc.NpcAnnotationError("annotation bundle failure")

            app = HoboSaveEditorApp(
                game_dir=game,
                backup_dir=root / "backups",
                npc_parser=parse_test_npc_data,
                npc_annotation_loader=fail_annotation_load,
                npc_writer=fail_npc_write,
            )
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await pilot.press("p")
                await pilot.pause()
                status = str(
                    app.screen.query_one("#npc-status", Static).render()
                )
                self.assertIn("Annotation warning:", status)
                self.assertIn("annotation bundle failure", status)
                app.screen.query_one(
                    "#npc-reputation-search",
                    Input,
                ).value = "Bruno"
                await pilot.pause()
                await pilot.click("#npc-edit")
                await pilot.pause()
                app.screen.query_one("#value-input", Input).value = "81"
                await pilot.press("enter")
                await pilot.pause()

                await pilot.click("#npc-back")
                await pilot.pause()
                self.assertIsInstance(
                    app.screen,
                    InventoryApplyConfirmationModal,
                )
                await pilot.click("#cancel-inventory-apply")
                await pilot.pause()
                self.assertIsInstance(app.screen, NpcScreen)

                await pilot.click("#npc-apply")
                await pilot.pause()
                await pilot.click("#confirm-inventory-apply")
                await pilot.pause()
                self.assertIsInstance(app.screen, MessageModal)
                self.assertIn(
                    "NPC disk failure",
                    str(
                        app.screen.query_one(
                            "#message-body",
                            Static,
                        ).render()
                    ),
                )
                await pilot.click("#close-message")
                await pilot.pause()
                await pilot.click("#npc-back")
                await pilot.pause()
                await pilot.click("#confirm-inventory-apply")
                await pilot.pause()
                self.assertNotIsInstance(app.screen, NpcScreen)

            self.assertEqual(character.read_bytes(), original)
            self.assertIsNone(app.last_backup_path)

    async def test_inventory_screen_stages_and_applies_full_edit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = create_game(
                root,
                ((SAVE_IDS[0], "First", 100),),
            )
            character = (
                game
                / editor.SAVE_DIR
                / "12345"
                / "NFS_Characters"
                / f"{SAVE_IDS[0]}_ls"
            )
            character.write_bytes(make_bag_transfer_character(equipped=True))
            catalog = make_bag_transfer_catalog()
            app = HoboSaveEditorApp(
                game_dir=game,
                backup_dir=root / "backups",
                inventory_catalog_loader=lambda _: catalog,
            )

            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await pilot.press("i")
                await pilot.pause()
                self.assertIsInstance(app.screen, InventoryScreen)
                item_table = app.screen.query_one(
                    "#inventory-items",
                    DataTable,
                )
                slot_table = app.screen.query_one(
                    "#inventory-slots",
                    DataTable,
                )
                self.assertEqual(len(item_table.get_row_at(0)), 5)
                self.assertEqual(len(slot_table.get_row_at(0)), 4)
                self.assertNotIn("|", str(item_table.get_row_at(0)[0]))

                app.screen.query_one("#inventory-search", Input).value = "3"
                await pilot.pause()
                self.assertEqual(item_table.get_row_at(0)[3], "3")
                await pilot.click("#inventory-quantity")
                await pilot.pause()
                self.assertIsInstance(app.screen, InventoryQuantityModal)
                quantity_input = app.screen.query_one(
                    "#inventory-quantity-input",
                    Input,
                )
                quantity_input.value = "5"
                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, InventoryScreen)

                slot_table.move_cursor(row=0, column=0, animate=False)
                app.screen.selection_kind = "slot"
                app.screen._refresh_detail()
                app.screen._refresh_action_buttons()
                self.assertFalse(
                    app.screen.query_one(
                        "#inventory-unequip",
                        Button,
                    ).disabled
                )
                await pilot.click("#inventory-unequip")
                await pilot.pause()
                await pilot.click("#inventory-apply")
                await pilot.pause()
                self.assertIsInstance(
                    app.screen,
                    InventoryApplyConfirmationModal,
                )
                await pilot.click("#confirm-inventory-apply")
                await pilot.pause()
                result = str(app.query_one("#result", Static).render())
                self.assertIn("Inventory updated", result)
                self.assertIn(str(app.last_backup_path), result)

            snapshot = inventory.read_inventory(character, catalog)
            equipment = inventory.read_equipment(character, catalog)
            item_three = next(
                item for item in snapshot.items if item.definition.item_id == 3
            )
            self.assertEqual(item_three.quantity, 5)
            self.assertIsNone(equipment.get("hat").item)
            self.assertTrue(
                any(item.definition.item_id == 10 for item in snapshot.items)
            )
            self.assertIsNotNone(app.last_backup_path)
            self.assertTrue(app.last_backup_path.is_file())

    async def test_wide_and_narrow_terminal_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = create_game(root)
            app = HoboSaveEditorApp(
                game_dir=game,
                backup_dir=root / "backups",
            )
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                self.assertFalse(app.screen.has_class("narrow"))

                await pilot.resize_terminal(60, 30)
                await pilot.pause()
                self.assertTrue(app.screen.has_class("narrow"))

                await pilot.resize_terminal(100, 30)
                await pilot.pause()
                self.assertFalse(app.screen.has_class("narrow"))


if __name__ == "__main__":
    unittest.main()
