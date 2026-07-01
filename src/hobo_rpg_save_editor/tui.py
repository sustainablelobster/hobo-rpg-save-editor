"""Textual user interface for the Hobo: Tough Life save editor."""

from __future__ import annotations

import hashlib
import inspect
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import (
    Center,
    Horizontal,
    Vertical,
    VerticalScroll,
)
from textual.screen import ModalScreen, Screen
from textual.validation import Integer
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    TabbedContent,
    TabPane,
)

from . import editor
from . import inventory as inventory_editor
from . import npc as npc_editor
from . import quests as quest_editor
from . import reputation as reputation_editor


DiscoverInstalls = Callable[[], list[Path]]
ScanSaves = Callable[[Path], tuple[list[editor.SaveRecord], list[str]]]
SetCash = Callable[..., tuple[int, Path]]
SetPrimaryParameter = Callable[..., tuple[int, Path]]
SetWorldTimeValue = Callable[..., tuple[int, Path]]
LoadItemCatalog = Callable[[Path], inventory_editor.ItemCatalog]
WriteBagTransfer = Callable[..., Path]
WriteFullInventoryEdit = Callable[..., Path]
ParseNpcData = Callable[[bytes], npc_editor.NpcData]
LoadNpcAnnotations = Callable[
    [Path, Sequence[str]],
    npc_editor.NpcFlagAnnotationCatalog,
]
LoadQuestCatalog = Callable[[Path], quest_editor.QuestCatalog]
WriteNpcEditPlan = Callable[..., Path]

_NPC_FLAG_TABLE_COLUMNS = (
    ("Flag", "flag", 24),
    ("Current", "current", 7),
    ("Staged", "staged", 6),
    ("Associated with", "associations", 16),
    ("English meaning", "meaning", 32),
)
_QUEST_TABLE_COLUMNS = (
    ("Quest", "quest", 36),
    ("Likely status", "status", 20),
    ("Flags", "flags", 10),
    ("Type", "type", 16),
)
_QUEST_FILTERS = {
    "quest-filter-all": ("all", "All"),
    "quest-filter-undiscovered": ("undiscovered", "Likely undiscovered"),
    "quest-filter-progress": ("in-progress", "In progress"),
    "quest-filter-completed": ("completed", "Completed?"),
    "quest-filter-no-flags": ("no-flags", "No save flags"),
    "quest-filter-repeatable": ("repeatable", "Repeatable/temp"),
}


@dataclass(frozen=True)
class EditableValue:
    label: str
    current_value: int
    maximum_value: int
    minimum_value: int = 0
    kind: str = "cash"
    parameter_type: Optional[int] = None
    world_time_field: Optional[str] = None

    @property
    def is_cash(self) -> bool:
        return self.kind == "cash"

    @property
    def is_primary_parameter(self) -> bool:
        return self.kind == "primary"

    @property
    def is_world_time(self) -> bool:
        return self.kind == "world_time"


@dataclass(frozen=True)
class InventoryActionChoice:
    label: str
    description: str
    change: inventory_editor.SavedBagTransfer
    source_sha256: str


def _format_character_parameters(
    parameters: editor.CharacterParameters,
) -> list[str]:
    lines = ["", "Primary parameters:"]
    lines.extend(
        (
            f"{parameter.display_name}: "
            f"{parameter.current_value} / {parameter.maximum_value}"
        )
        for parameter in parameters.primary
    )
    if not parameters.primary:
        lines.append("(none)")

    lines.extend(["", "Secondary parameters (read-only, unverified):"])
    lines.extend(
        f"{parameter.display_name}: {parameter.current_value:g}"
        for parameter in parameters.secondary
    )
    if not parameters.secondary:
        lines.append("(none)")
    return lines


def _editable_values(record: editor.SaveRecord) -> list[EditableValue]:
    targets: list[EditableValue] = []
    try:
        targets.append(
            EditableValue(
                label="Cash",
                current_value=record.current_cash(),
                maximum_value=editor.MAX_CASH,
            )
        )
    except (OSError, editor.SaveFormatError):
        pass

    try:
        parameters = editor.read_character_parameters(record.character_path)
    except (OSError, editor.SaveFormatError):
        pass
    else:
        type_counts = Counter(
            parameter.parameter_type for parameter in parameters.primary
        )
        targets.extend(
            EditableValue(
                label=parameter.display_name,
                current_value=parameter.current_value,
                maximum_value=parameter.maximum_value,
                kind="primary",
                parameter_type=parameter.parameter_type,
            )
            for parameter in parameters.primary
            if parameter.parameter_type in editor.PRIMARY_PARAMETER_NAMES
            and type_counts[parameter.parameter_type] == 1
            and parameter.maximum_value >= 0
        )

    try:
        world_time = record.current_world_time()
    except (OSError, editor.SaveFormatError):
        pass
    else:
        for field in editor.WORLD_TIME_FIELDS.values():
            targets.append(
                EditableValue(
                    label=field.display_name,
                    current_value=getattr(world_time, field.name),
                    maximum_value=field.maximum_value,
                    minimum_value=field.minimum_value,
                    kind="world_time",
                    world_time_field=field.name,
                )
            )
    return targets


class SaveListItem(ListItem):
    """A save list entry which retains its domain record."""

    def __init__(self, record: editor.SaveRecord, label: str) -> None:
        super().__init__(Label(label, markup=False))
        self.record = record


class InstallationListItem(ListItem):
    """An installation list entry which retains its path."""

    def __init__(self, path: Path) -> None:
        super().__init__(Label(str(path), markup=False))
        self.path = path


class EditableValueListItem(ListItem):
    """An editable value list entry which retains its target metadata."""

    def __init__(self, target: EditableValue) -> None:
        super().__init__(
            Label(
                f"{target.label}: {target.current_value} "
                f"(range {target.minimum_value}-{target.maximum_value})",
                markup=False,
            )
        )
        self.target = target


class InventoryActionListItem(ListItem):
    """An inventory action list entry which retains its staged change."""

    def __init__(self, choice: InventoryActionChoice) -> None:
        super().__init__(
            Label(
                f"{choice.label}\n{choice.description}",
                markup=False,
            )
        )
        self.choice = choice


class InventoryRecordListItem(ListItem):
    """A staged carried-inventory list entry."""

    def __init__(self, position: int, label: str) -> None:
        super().__init__(Label(label, markup=False))
        self.position = position


class InventorySlotListItem(ListItem):
    """A staged equipment or saved-bag slot entry."""

    def __init__(
        self,
        kind: str,
        key: str,
        label: str,
    ) -> None:
        super().__init__(Label(label, markup=False))
        self.kind = kind
        self.key = key


class CatalogListItem(ListItem):
    """A catalog list entry which retains its item definition."""

    def __init__(self, definition: inventory_editor.ItemDefinition) -> None:
        super().__init__(
            Label(
                f"{definition.item_id} | {definition.name} | "
                f"{definition.category}",
                markup=False,
            )
        )
        self.definition = definition


class PathEntryModal(ModalScreen[Optional[Path]]):
    """Collect and validate a game installation path."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("q", "cancel", "Cancel", show=False, priority=True),
    ]

    CSS = """
    PathEntryModal {
        align: center middle;
    }

    PathEntryModal > Vertical {
        width: 76;
        max-width: 92%;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    PathEntryModal Input {
        margin: 1 0;
    }

    PathEntryModal .error {
        color: $error;
        min-height: 1;
    }

    PathEntryModal .buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    PathEntryModal Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        initial_path: Optional[Path] = None,
        message: str = "",
        allow_cancel: bool = True,
    ) -> None:
        super().__init__()
        self.initial_path = initial_path
        self.message = message
        self.allow_cancel = allow_cancel

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Locate Hobo: Tough Life", classes="modal-title")
            yield Static(
                self.message
                or "Enter the directory containing HoboRPG_Data.",
                markup=False,
            )
            yield Input(
                value=str(self.initial_path) if self.initial_path else "",
                placeholder="/path/to/Hobo Tough Life",
                id="game-path",
            )
            yield Static("", id="path-error", classes="error", markup=False)
            with Horizontal(classes="buttons"):
                if self.allow_cancel:
                    yield Button("Cancel", id="cancel-path")
                yield Button("Use path", id="accept-path", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#game-path", Input).focus()

    def _submit(self) -> None:
        raw_path = self.query_one("#game-path", Input).value.strip()
        error = self.query_one("#path-error", Static)
        if not raw_path:
            error.update("Enter the game installation directory.")
            return
        candidate = Path(raw_path).expanduser().absolute()
        if not (candidate / "HoboRPG_Data").is_dir():
            error.update(
                "This directory does not contain HoboRPG_Data: "
                f"{candidate}"
            )
            return
        self.dismiss(candidate)

    def on_input_submitted(self, _: Input.Submitted) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "accept-path":
            self._submit()
        elif event.button.id == "cancel-path":
            self.action_cancel()

    def action_cancel(self) -> None:
        if self.allow_cancel:
            self.dismiss(None)


class InstallationScreen(Screen[Optional[Path]]):
    """Choose from discovered game installations."""

    BINDINGS = [
        Binding("q", "cancel", "Quit", priority=True),
        Binding("escape", "cancel", "Quit", show=False, priority=True),
    ]

    CSS = """
    InstallationScreen {
        align: center middle;
    }

    #installation-dialog {
        width: 90;
        max-width: 94%;
        height: 28;
        max-height: 90%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #installation-list {
        height: 1fr;
        margin: 1 0;
        border: solid $panel;
    }

    InstallationScreen .buttons {
        height: auto;
        align-horizontal: right;
    }

    InstallationScreen Button {
        margin-left: 1;
    }
    """

    def __init__(self, installs: Sequence[Path]) -> None:
        super().__init__()
        self.installs = list(installs)

    def compose(self) -> ComposeResult:
        with Vertical(id="installation-dialog"):
            yield Label("Select a Hobo: Tough Life installation")
            yield Static(
                "Multiple installations were found. Choose one to scan.",
                markup=False,
            )
            yield ListView(
                *[InstallationListItem(path) for path in self.installs],
                id="installation-list",
            )
            with Horizontal(classes="buttons"):
                yield Button("Other path", id="other-path")
                yield Button(
                    "Use selected",
                    id="select-installation",
                    variant="primary",
                )

    def on_mount(self) -> None:
        self.query_one("#installation-list", ListView).focus()

    def _select_current(self) -> None:
        install_list = self.query_one("#installation-list", ListView)
        if install_list.index is None:
            return
        item = install_list.children[install_list.index]
        if isinstance(item, InstallationListItem):
            self.dismiss(item.path)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "installation-list":
            self._select_current()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "select-installation":
            self._select_current()
        elif event.button.id == "other-path":
            self.app.push_screen(
                PathEntryModal(),
                self._manual_path_selected,
            )

    def _manual_path_selected(self, path: Optional[Path]) -> None:
        if path is not None:
            self.dismiss(path)

    def action_cancel(self) -> None:
        self.dismiss(None)


class EditValueModal(ModalScreen[Optional[EditableValue]]):
    """Choose one cash or primary value to edit."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("q", "cancel", "Cancel", show=False, priority=True),
    ]

    CSS = """
    EditValueModal {
        align: center middle;
    }

    EditValueModal > Vertical {
        width: 70;
        max-width: 92%;
        height: 28;
        max-height: 90%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    EditValueModal ListView {
        height: 1fr;
        margin: 1 0;
        border: solid $panel;
    }

    EditValueModal .buttons {
        height: auto;
        align-horizontal: right;
    }

    EditValueModal Button {
        margin-left: 1;
    }
    """

    def __init__(self, targets: Sequence[EditableValue]) -> None:
        super().__init__()
        self.targets = list(targets)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Choose a value to edit")
            yield Static(
                "Primary maximum values remain read-only.",
                markup=False,
            )
            yield ListView(
                *[EditableValueListItem(target) for target in self.targets],
                id="edit-value-list",
            )
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel-edit-choice")
                yield Button(
                    "Edit selected",
                    id="accept-edit-choice",
                    variant="primary",
                )

    def on_mount(self) -> None:
        self.query_one("#edit-value-list", ListView).focus()

    def _select_current(self) -> None:
        value_list = self.query_one("#edit-value-list", ListView)
        if value_list.index is None:
            return
        item = value_list.children[value_list.index]
        if isinstance(item, EditableValueListItem):
            self.dismiss(item.target)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "edit-value-list":
            self._select_current()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "accept-edit-choice":
            self._select_current()
        elif event.button.id == "cancel-edit-choice":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)


class IntegerEditModal(ModalScreen[Optional[int]]):
    """Collect one integer value with live range validation."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("q", "cancel", "Cancel", show=False, priority=True),
    ]

    CSS = """
    IntegerEditModal {
        align: center middle;
    }

    IntegerEditModal > Vertical {
        width: 58;
        max-width: 92%;
        height: auto;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    IntegerEditModal Input {
        margin: 1 0;
    }

    IntegerEditModal .error {
        min-height: 1;
        color: $error;
    }

    IntegerEditModal .experimental-warning {
        height: auto;
        margin: 1 0 0 0;
        padding: 1;
        border: heavy $warning;
        color: $warning;
        text-style: bold;
    }

    IntegerEditModal .buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    IntegerEditModal Button {
        margin-left: 1;
    }
    """

    def __init__(self, target: EditableValue) -> None:
        super().__init__()
        self.target = target

    def compose(self) -> ComposeResult:
        validator = Integer(
            minimum=self.target.minimum_value,
            maximum=self.target.maximum_value,
            failure_description=(
                "Enter a whole number between "
                f"{self.target.minimum_value} and "
                f"{self.target.maximum_value}."
            ),
        )
        with Vertical():
            yield Label(f"Edit {self.target.label}", markup=False)
            yield Static(
                f"Current value: {self.target.current_value}",
                markup=False,
            )
            if self.target.world_time_field == "season":
                yield Static(
                    "WARNING: Season editing is highly experimental. "
                    "It often breaks the running game, and you may need "
                    "to force-quit and restart before things work again.",
                    id="value-warning",
                    classes="experimental-warning",
                    markup=False,
                )
            yield Input(
                value=str(self.target.current_value),
                type="integer",
                validators=validator,
                validate_on=("changed", "submitted"),
                id="value-input",
            )
            yield Static("", id="value-error", classes="error", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel-value")
                yield Button(
                    "Continue",
                    id="accept-value",
                    variant="primary",
                )

    def on_mount(self) -> None:
        value_input = self.query_one("#value-input", Input)
        value_input.focus()
        self._update_validation(value_input.value)

    def _update_validation(self, value: str) -> bool:
        result = self.query_one("#value-input", Input).validate(value)
        is_valid = bool(value) and result is not None and result.is_valid
        self.query_one("#accept-value", Button).disabled = not is_valid
        self.query_one("#value-error", Static).update(
            "" if is_valid else (
                "Enter a whole number between "
                f"{self.target.minimum_value} and "
                f"{self.target.maximum_value}."
            )
        )
        return is_valid

    def _submit(self) -> None:
        value = self.query_one("#value-input", Input).value
        if self._update_validation(value):
            self.dismiss(int(value, 10))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "value-input":
            self._update_validation(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "value-input":
            self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "accept-value":
            self._submit()
        elif event.button.id == "cancel-value":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmationModal(ModalScreen[bool]):
    """Confirm one character value mutation as a separate explicit step."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("q", "cancel", "Cancel", show=False, priority=True),
    ]

    CSS = """
    ConfirmationModal {
        align: center middle;
    }

    ConfirmationModal > Vertical {
        width: 62;
        max-width: 92%;
        height: auto;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }

    ConfirmationModal .buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    ConfirmationModal Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        label: str,
        old_value: int,
        new_value: int,
    ) -> None:
        super().__init__()
        self.label = label
        self.old_value = old_value
        self.new_value = new_value

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"Confirm {self.label} change", markup=False)
            yield Static(
                f"Change {self.label} from {self.old_value} "
                f"to {self.new_value}?",
                id="confirmation-message",
                markup=False,
            )
            yield Static(
                "The game must be closed. A backup will be created before "
                "the save is modified.",
                markup=False,
            )
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel-confirm")
                yield Button(
                    "Change value",
                    id="confirm-change",
                    variant="warning",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-change":
            self.dismiss(True)
        elif event.button.id == "cancel-confirm":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(False)


class InventoryActionModal(ModalScreen[Optional[InventoryActionChoice]]):
    """Choose one validated inventory mutation."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("q", "cancel", "Cancel", show=False, priority=True),
    ]

    CSS = """
    InventoryActionModal {
        align: center middle;
    }

    InventoryActionModal > Vertical {
        width: 76;
        max-width: 92%;
        height: 28;
        max-height: 90%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    InventoryActionModal ListView {
        height: 1fr;
        margin: 1 0;
        border: solid $panel;
    }

    InventoryActionModal .buttons {
        height: auto;
        align-horizontal: right;
    }

    InventoryActionModal Button {
        margin-left: 1;
    }
    """

    def __init__(self, choices: Sequence[InventoryActionChoice]) -> None:
        super().__init__()
        self.choices = list(choices)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Validated inventory actions")
            yield Static(
                "Only game-verified inventory operations are listed.",
                markup=False,
            )
            yield ListView(
                *[InventoryActionListItem(choice) for choice in self.choices],
                id="inventory-action-list",
            )
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel-inventory-action")
                yield Button(
                    "Apply selected",
                    id="apply-inventory-action",
                    variant="primary",
                )

    def on_mount(self) -> None:
        action_list = self.query_one("#inventory-action-list", ListView)
        if self.choices:
            action_list.index = 0
        action_list.focus()

    def _select_current(self) -> None:
        action_list = self.query_one("#inventory-action-list", ListView)
        if action_list.index is None:
            return
        item = action_list.children[action_list.index]
        if isinstance(item, InventoryActionListItem):
            self.dismiss(item.choice)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "inventory-action-list":
            self._select_current()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply-inventory-action":
            self._select_current()
        elif event.button.id == "cancel-inventory-action":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)


class InventoryConfirmationModal(ModalScreen[bool]):
    """Confirm one validated inventory mutation."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("q", "cancel", "Cancel", show=False, priority=True),
    ]

    CSS = """
    InventoryConfirmationModal {
        align: center middle;
    }

    InventoryConfirmationModal > Vertical {
        width: 68;
        max-width: 92%;
        height: auto;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }

    InventoryConfirmationModal .buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    InventoryConfirmationModal Button {
        margin-left: 1;
    }
    """

    def __init__(self, choice: InventoryActionChoice) -> None:
        super().__init__()
        self.choice = choice

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Confirm inventory change", markup=False)
            yield Static(self.choice.label, markup=False)
            yield Static(self.choice.description, markup=False)
            yield Static(
                "The game must be closed. A backup will be created before "
                "the save is modified.",
                markup=False,
            )
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel-inventory-confirm")
                yield Button(
                    "Apply change",
                    id="confirm-inventory",
                    variant="warning",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-inventory":
            self.dismiss(True)
        elif event.button.id == "cancel-inventory-confirm":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(False)


class InventoryQuantityModal(ModalScreen[Optional[int]]):
    """Collect one positive inventory quantity."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("q", "cancel", "Cancel", show=False, priority=True),
    ]

    CSS = """
    InventoryQuantityModal {
        align: center middle;
    }

    InventoryQuantityModal > Vertical {
        width: 58;
        max-width: 92%;
        height: auto;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    InventoryQuantityModal Input {
        margin: 1 0;
    }

    InventoryQuantityModal .error {
        min-height: 1;
        color: $error;
    }

    InventoryQuantityModal .buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    InventoryQuantityModal Button {
        margin-left: 1;
    }
    """

    def __init__(self, label: str, current_value: int = 1) -> None:
        super().__init__()
        self.label = label
        self.current_value = current_value

    def compose(self) -> ComposeResult:
        validator = Integer(
            minimum=1,
            maximum=inventory_editor.MAX_SERIALIZED_QUANTITY,
            failure_description=(
                "Enter a whole number between 1 and "
                f"{inventory_editor.MAX_SERIALIZED_QUANTITY}."
            ),
        )
        with Vertical():
            yield Label(self.label, markup=False)
            yield Input(
                value=str(self.current_value),
                type="integer",
                validators=validator,
                validate_on=("changed", "submitted"),
                id="inventory-quantity-input",
            )
            yield Static(
                "",
                id="inventory-quantity-error",
                classes="error",
                markup=False,
            )
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel-inventory-quantity")
                yield Button(
                    "Continue",
                    id="accept-inventory-quantity",
                    variant="primary",
                )

    def on_mount(self) -> None:
        quantity_input = self.query_one("#inventory-quantity-input", Input)
        quantity_input.focus()
        self._update_validation(quantity_input.value)

    def _update_validation(self, value: str) -> bool:
        result = self.query_one("#inventory-quantity-input", Input).validate(
            value
        )
        is_valid = bool(value) and result is not None and result.is_valid
        self.query_one("#accept-inventory-quantity", Button).disabled = (
            not is_valid
        )
        self.query_one("#inventory-quantity-error", Static).update(
            "" if is_valid else (
                "Enter a whole number between 1 and "
                f"{inventory_editor.MAX_SERIALIZED_QUANTITY}."
            )
        )
        return is_valid

    def _submit(self) -> None:
        value = self.query_one("#inventory-quantity-input", Input).value
        if self._update_validation(value):
            self.dismiss(int(value, 10))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "inventory-quantity-input":
            self._update_validation(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "inventory-quantity-input":
            self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "accept-inventory-quantity":
            self._submit()
        elif event.button.id == "cancel-inventory-quantity":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)


class CatalogSearchModal(
    ModalScreen[Optional[inventory_editor.ItemDefinition]]
):
    """Search the item catalog and choose one item."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("q", "cancel", "Cancel", show=False, priority=True),
    ]

    CSS = """
    CatalogSearchModal {
        align: center middle;
    }

    CatalogSearchModal > Vertical {
        width: 84;
        max-width: 94%;
        height: 32;
        max-height: 92%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    CatalogSearchModal Input {
        margin: 1 0;
    }

    CatalogSearchModal DataTable {
        height: 1fr;
        border: solid $panel;
    }

    CatalogSearchModal .buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    CatalogSearchModal Button {
        margin-left: 1;
    }
    """

    def __init__(self, catalog: inventory_editor.ItemCatalog) -> None:
        super().__init__()
        self.catalog = catalog
        self.matches: list[inventory_editor.ItemDefinition] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Add item from catalog")
            yield Input(
                placeholder="Search by name, ID, or category",
                id="catalog-search",
            )
            catalog_table = DataTable(id="catalog-table")
            catalog_table.cursor_type = "row"
            catalog_table.zebra_stripes = True
            catalog_table.add_columns("Name", "Category", "ID")
            yield catalog_table
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel-catalog")
                yield Button(
                    "Add selected",
                    id="accept-catalog",
                    variant="primary",
                )

    async def on_mount(self) -> None:
        self.query_one("#catalog-search", Input).focus()
        await self._refresh_catalog()

    def _matches(self, definition: inventory_editor.ItemDefinition) -> bool:
        query = self.query_one("#catalog-search", Input).value.strip()
        if not query:
            return True
        haystack = (
            f"{definition.item_id} {definition.name} "
            f"{definition.category}"
        ).casefold()
        return all(part in haystack for part in query.casefold().split())

    async def _refresh_catalog(self) -> None:
        self.matches = [
            definition
            for definition in self.catalog.items
            if self._matches(definition)
        ][:200]
        catalog_table = self.query_one("#catalog-table", DataTable)
        catalog_table.clear()
        for definition in self.matches:
            catalog_table.add_row(
                definition.name,
                definition.category,
                str(definition.item_id),
                key=f"item:{definition.item_id}",
            )
        if self.matches:
            catalog_table.move_cursor(row=0, column=0, animate=False)

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "catalog-search":
            await self._refresh_catalog()

    def _select_current(self) -> None:
        catalog_table = self.query_one("#catalog-table", DataTable)
        if not self.matches:
            return
        row = min(catalog_table.cursor_row, len(self.matches) - 1)
        self.dismiss(self.matches[row])

    def on_data_table_row_selected(
        self,
        event: DataTable.RowSelected,
    ) -> None:
        if event.data_table.id == "catalog-table":
            self._select_current()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "accept-catalog":
            self._select_current()
        elif event.button.id == "cancel-catalog":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)


class SlotSelectModal(ModalScreen[Optional[tuple[str, str]]]):
    """Choose a compatible equipment or saved-bag slot."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("q", "cancel", "Cancel", show=False, priority=True),
    ]

    CSS = """
    SlotSelectModal {
        align: center middle;
    }

    SlotSelectModal > Vertical {
        width: 70;
        max-width: 92%;
        height: 24;
        max-height: 90%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    SlotSelectModal DataTable {
        height: 1fr;
        margin: 1 0;
        border: solid $panel;
    }

    SlotSelectModal .buttons {
        height: auto;
        align-horizontal: right;
    }

    SlotSelectModal Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        label: str,
        choices: Sequence[tuple[str, str, str]],
    ) -> None:
        super().__init__()
        self.label = label
        self.choices = list(choices)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.label, markup=False)
            slot_table = DataTable(id="slot-choice-table")
            slot_table.cursor_type = "row"
            slot_table.zebra_stripes = True
            slot_table.add_columns("Slot", "Current item")
            for position, (_, _, label) in enumerate(self.choices):
                slot_name, _, current = label.partition(": ")
                slot_table.add_row(slot_name, current, key=f"slot:{position}")
            yield slot_table
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel-slot-choice")
                yield Button(
                    "Use slot",
                    id="accept-slot-choice",
                    variant="primary",
                )

    def on_mount(self) -> None:
        slot_table = self.query_one("#slot-choice-table", DataTable)
        if self.choices:
            slot_table.move_cursor(row=0, column=0, animate=False)
        slot_table.focus()

    def _select_current(self) -> None:
        slot_table = self.query_one("#slot-choice-table", DataTable)
        if not self.choices:
            return
        row = min(slot_table.cursor_row, len(self.choices) - 1)
        kind, key, _ = self.choices[row]
        self.dismiss((kind, key))

    def on_data_table_row_selected(
        self,
        event: DataTable.RowSelected,
    ) -> None:
        if event.data_table.id == "slot-choice-table":
            self._select_current()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "accept-slot-choice":
            self._select_current()
        elif event.button.id == "cancel-slot-choice":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)


class InventoryApplyConfirmationModal(ModalScreen[bool]):
    """Confirm all staged inventory changes."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("q", "cancel", "Cancel", show=False, priority=True),
    ]

    CSS = """
    InventoryApplyConfirmationModal {
        align: center middle;
    }

    InventoryApplyConfirmationModal > Vertical {
        width: 76;
        max-width: 92%;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }

    #inventory-apply-summary {
        max-height: 14;
        overflow-y: auto;
    }

    InventoryApplyConfirmationModal .buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    InventoryApplyConfirmationModal Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        summary: str,
        title: str = "Apply inventory changes",
        confirm_label: str = "Apply changes",
    ) -> None:
        super().__init__()
        self.summary = summary
        self.modal_title = title
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.modal_title, markup=False)
            yield Static(
                self.summary,
                id="inventory-apply-summary",
                markup=False,
            )
            yield Static(
                "The game must be closed. A backup will be created before "
                "the save is modified.",
                markup=False,
            )
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel-inventory-apply")
                yield Button(
                    self.confirm_label,
                    id="confirm-inventory-apply",
                    variant="warning",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-inventory-apply":
            self.dismiss(True)
        elif event.button.id == "cancel-inventory-apply":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(False)


class MessageModal(ModalScreen[None]):
    """Display an error or other result that requires acknowledgement."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=False, priority=True),
        Binding("enter", "close", "Close", show=False, priority=True),
    ]

    CSS = """
    MessageModal {
        align: center middle;
    }

    MessageModal > Vertical {
        width: 70;
        max-width: 92%;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: round $error;
        background: $surface;
    }

    MessageModal Button {
        margin-top: 1;
        align-horizontal: right;
    }
    """

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self.modal_title = title
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.modal_title, markup=False)
            yield Static(self.message, id="message-body", markup=False)
            with Center():
                yield Button("Close", id="close-message", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-message":
            self.action_close()

    def action_close(self) -> None:
        self.dismiss(None)


class NpcScreen(Screen[None]):
    """Stage and apply NPC reputation and flag changes for one save."""

    BINDINGS = [
        Binding("escape", "back", "Back", show=False, priority=True),
        Binding("e", "edit_selected", "Edit / Toggle"),
        Binding("ctrl+s", "apply_changes", "Apply"),
    ]

    CSS = """
    NpcScreen {
        background: $background;
    }

    #npc-root {
        height: 1fr;
    }

    #npc-title,
    #npc-status,
    #npc-staged {
        height: auto;
        padding: 0 1;
    }

    #npc-title {
        text-style: bold;
        background: $panel;
    }

    #npc-tabs {
        height: 1fr;
        margin: 0 1;
    }

    #npc-reputation-search,
    #npc-flags-search {
        margin: 1;
    }

    #npc-reputation-table,
    #npc-flags-table {
        height: 1fr;
        border: round $panel;
    }

    #npc-flag-detail {
        height: auto;
        min-height: 3;
        max-height: 7;
        padding: 0 1;
        border: round $panel;
        color: $text-muted;
        overflow-y: auto;
    }

    #npc-staged {
        max-height: 7;
        color: $text-muted;
        overflow-y: auto;
    }

    .npc-toolbar {
        height: auto;
        padding: 0 1 1 1;
    }

    .npc-toolbar Button {
        width: auto;
        margin-right: 1;
    }
    """

    def __init__(
        self,
        record: editor.SaveRecord,
        data: npc_editor.NpcData,
        annotations: npc_editor.NpcFlagAnnotationCatalog,
        source_sha256: str,
        *,
        backup_dir: Path,
        writer: WriteNpcEditPlan,
        on_success: Callable[[Path, str], object],
        initial_flag_keys: Sequence[str] = (),
        initial_flag_filter_label: str = "",
    ) -> None:
        super().__init__()
        self.record = record
        self.data = data
        self.annotations = annotations
        self.source_sha256 = source_sha256
        self.backup_dir = backup_dir
        self.writer = writer
        self.on_success = on_success
        self.original_reputation_values = {
            item.archetype_id: item.value
            for item in data.reputation.records
        }
        self.original_flag_values = {
            item.key: item.value for item in data.flags.records
        }
        self.staged_reputation_values: dict[int, int] = {}
        self.staged_flag_values: dict[str, int] = {}
        self.visible_reputation_ids: list[int] = []
        self.visible_flag_keys: list[str] = []
        self.flags_loaded = False
        self.initial_flag_keys = frozenset(initial_flag_keys)
        self.flag_key_filter_active = bool(self.initial_flag_keys)
        self.initial_flag_filter_label = (
            initial_flag_filter_label
            if initial_flag_filter_label
            else "Related quest flags"
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="npc-root"):
            yield Static(
                f"NPCs: {self.record.display_name} / "
                f"{self.record.slot_name}",
                id="npc-title",
                markup=False,
            )
            yield Static(
                f"{len(self.data.reputation.records)} reputation records | "
                f"{len(self.data.flags.records)} boolean flags"
                + (
                    "\nAnnotation warning: "
                    + " ".join(self.annotations.warnings)
                    if self.annotations.warnings
                    else ""
                ),
                id="npc-status",
                markup=False,
            )
            with TabbedContent(
                initial=(
                    "npc-flags-pane"
                    if self.initial_flag_keys
                    else "npc-reputation-pane"
                ),
                id="npc-tabs",
            ):
                with TabPane("Reputation", id="npc-reputation-pane"):
                    yield Input(
                        placeholder=(
                            "Search name, enum name, or archetype ID"
                        ),
                        id="npc-reputation-search",
                    )
                    reputation_table = DataTable(
                        id="npc-reputation-table"
                    )
                    reputation_table.cursor_type = "row"
                    reputation_table.zebra_stripes = True
                    reputation_table.add_columns(
                        "NPC",
                        "Current",
                        "Staged",
                        "ID",
                    )
                    yield reputation_table
                with TabPane("Flags", id="npc-flags-pane"):
                    yield Input(
                        self.initial_flag_filter_label
                        if self.initial_flag_keys
                        else None,
                        placeholder="Search all 788 NPC and quest flags",
                        id="npc-flags-search",
                    )
                    flags_table = DataTable(id="npc-flags-table")
                    flags_table.cursor_type = "row"
                    flags_table.zebra_stripes = True
                    for label, key, width in _NPC_FLAG_TABLE_COLUMNS:
                        flags_table.add_column(
                            label,
                            key=key,
                            width=width,
                        )
                    yield flags_table
                    yield Static(
                        "Select a flag to view its full annotation.",
                        id="npc-flag-detail",
                        markup=False,
                    )
            yield Static("", id="npc-staged", markup=False)
            with Horizontal(classes="npc-toolbar"):
                yield Button(
                    "Edit / Toggle",
                    id="npc-edit",
                    variant="primary",
                )
                yield Button("Apply", id="npc-apply")
                yield Button("Discard", id="npc-discard")
                yield Button("Back", id="npc-back")

    async def on_mount(self) -> None:
        self._refresh_reputation_table()
        if self.initial_flag_keys:
            self._refresh_flag_table()
            self.query_one("#npc-flags-search", Input).focus()
        else:
            self.query_one("#npc-reputation-search", Input).focus()
        self._refresh_staged_summary()
        self._refresh_buttons()

    def _dirty(self) -> bool:
        return bool(
            self.staged_reputation_values or self.staged_flag_values
        )

    def _reputation_matches(self, archetype_id: int, query: str) -> bool:
        if not query:
            return True
        display_name = reputation_editor.reputation_display_name(
            archetype_id
        )
        raw_name = reputation_editor.reputation_raw_name(archetype_id)
        searchable = f"{display_name} {raw_name} {archetype_id}".casefold()
        return query in searchable

    def _refresh_reputation_table(self) -> None:
        reputation_query = self.query_one(
            "#npc-reputation-search",
            Input,
        ).value.strip().casefold()
        visible_reputation_ids = [
            item.archetype_id
            for item in self.data.reputation.records
            if self._reputation_matches(
                item.archetype_id,
                reputation_query,
            )
        ]
        self.visible_reputation_ids = visible_reputation_ids
        reputation_table = self.query_one(
            "#npc-reputation-table",
            DataTable,
        )
        reputation_table.clear(columns=False)
        for archetype_id in visible_reputation_ids:
            staged = self.staged_reputation_values.get(archetype_id)
            reputation_table.add_row(
                reputation_editor.reputation_display_name(archetype_id),
                str(self.original_reputation_values[archetype_id]),
                "-" if staged is None else str(staged),
                str(archetype_id),
                key=str(archetype_id),
            )
        if visible_reputation_ids:
            reputation_table.move_cursor(row=0, column=0, animate=False)

    def _refresh_flag_table(self) -> None:
        self.flags_loaded = True
        flag_query = self.query_one(
            "#npc-flags-search",
            Input,
        ).value.strip().casefold()
        visible_flag_keys = [
            item.key
            for item in self.data.flags.records
            if (
                item.key in self.initial_flag_keys
                if self.flag_key_filter_active
                else (
                    not flag_query
                    or flag_query
                    in (
                        f"{item.key} "
                        f"{' '.join(self.annotations.get(item.key).associations)} "
                        f"{self.annotations.get(item.key).meaning}"
                    ).casefold()
                )
            )
        ]
        self.visible_flag_keys = visible_flag_keys
        flags_table = self.query_one("#npc-flags-table", DataTable)
        flags_table.clear(columns=False)
        for key in visible_flag_keys:
            staged = self.staged_flag_values.get(key)
            annotation = self.annotations.get(key)
            flags_table.add_row(
                Text(key, overflow="ellipsis", no_wrap=True),
                str(self.original_flag_values[key]),
                "-" if staged is None else str(staged),
                Text(
                    ", ".join(annotation.associations),
                    overflow="ellipsis",
                    no_wrap=True,
                ),
                Text(
                    annotation.meaning,
                    overflow="ellipsis",
                    no_wrap=True,
                ),
                key=key,
            )
        if visible_flag_keys:
            flags_table.move_cursor(row=0, column=0, animate=False)
        self._refresh_flag_detail()

    def _refresh_flag_detail(self) -> None:
        detail = self.query_one("#npc-flag-detail", Static)
        key = self._selected_flag_key()
        if key is None:
            detail.update(
                "No related flags are present in this save."
                if self.flag_key_filter_active
                else "No flags match the current search."
            )
            return
        annotation = self.annotations.get(key)
        detail.update(
            f"Flag: {key}\n"
            "Associated with: "
            f"{', '.join(annotation.associations)}\n"
            f"English meaning: {annotation.meaning}"
        )

    def _refresh_reputation_view(self) -> None:
        self._refresh_reputation_table()
        self._refresh_staged_summary()
        self._refresh_buttons()

    def _refresh_flag_view(self) -> None:
        if self.flags_loaded:
            self._refresh_flag_table()
        self._refresh_staged_summary()
        self._refresh_buttons()

    def _refresh_staged_summary(self) -> None:
        staged = self.query_one("#npc-staged", Static)
        if not self._dirty():
            staged.update("No NPC changes staged.")
            return
        lines = ["Staged changes:"]
        lines.extend(
            "- Reputation "
            f"{reputation_editor.reputation_display_name(archetype_id)}: "
            f"{self.original_reputation_values[archetype_id]} -> {new_value}"
            for archetype_id, new_value
            in self._ordered_staged_reputation_values()
        )
        lines.extend(
            f"- Flag {key}: {self.original_flag_values[key]} -> {new_value}"
            for key, new_value in self._ordered_staged_flag_values()
        )
        staged.update("\n".join(lines))

    def _refresh_buttons(self) -> None:
        has_selection = (
            self._selected_archetype_id() is not None
            if self._active_kind() == "reputation"
            else self._selected_flag_key() is not None
        )
        self.query_one("#npc-edit", Button).disabled = not has_selection
        self.query_one("#npc-apply", Button).disabled = not self._dirty()
        self.query_one("#npc-discard", Button).disabled = not self._dirty()

    def _active_kind(self) -> str:
        tabs = self.query_one("#npc-tabs", TabbedContent)
        return "flags" if tabs.active == "npc-flags-pane" else "reputation"

    def _selected_archetype_id(self) -> Optional[int]:
        table = self.query_one("#npc-reputation-table", DataTable)
        row = table.cursor_row
        if row < 0 or row >= len(self.visible_reputation_ids):
            return None
        return self.visible_reputation_ids[row]

    def _selected_flag_key(self) -> Optional[str]:
        table = self.query_one("#npc-flags-table", DataTable)
        row = table.cursor_row
        if row < 0 or row >= len(self.visible_flag_keys):
            return None
        return self.visible_flag_keys[row]

    def _ordered_staged_reputation_values(self) -> list[tuple[int, int]]:
        return [
            (
                item.archetype_id,
                self.staged_reputation_values[item.archetype_id],
            )
            for item in self.data.reputation.records
            if item.archetype_id in self.staged_reputation_values
        ]

    def _ordered_staged_flag_values(self) -> list[tuple[str, int]]:
        return [
            (item.key, self.staged_flag_values[item.key])
            for item in self.data.flags.records
            if item.key in self.staged_flag_values
        ]

    def _change_summary(self) -> str:
        lines = [
            "- Reputation "
            f"{reputation_editor.reputation_display_name(archetype_id)}: "
            f"{self.original_reputation_values[archetype_id]} -> {new_value}"
            for archetype_id, new_value
            in self._ordered_staged_reputation_values()
        ]
        lines.extend(
            f"- Flag {key}: {self.original_flag_values[key]} -> {new_value}"
            for key, new_value in self._ordered_staged_flag_values()
        )
        return "\n".join(lines)

    def _confirmation_summary(self) -> str:
        return (
            "Warning: quest flags are experimental. Arbitrary combinations "
            "may create inconsistent progression.\n\n"
            f"{self._change_summary()}"
        )

    def _plan(self) -> npc_editor.NpcEditPlan:
        return npc_editor.NpcEditPlan(
            source_sha256=self.source_sha256,
            reputation_changes=tuple(
                reputation_editor.ReputationChange(
                    archetype_id=archetype_id,
                    expected_value=(
                        self.original_reputation_values[archetype_id]
                    ),
                    new_value=new_value,
                )
                for archetype_id, new_value
                in self._ordered_staged_reputation_values()
            ),
            flag_changes=tuple(
                npc_editor.NpcFlagChange(
                    key=key,
                    expected_value=self.original_flag_values[key],
                    new_value=new_value,
                )
                for key, new_value in self._ordered_staged_flag_values()
            ),
        )

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "npc-reputation-search":
            self._refresh_reputation_view()
        elif event.input.id == "npc-flags-search":
            if (
                self.flag_key_filter_active
                and event.input.value != self.initial_flag_filter_label
            ):
                self.flag_key_filter_active = False
            self._refresh_flag_view()

    def on_tabbed_content_tab_activated(
        self,
        event: TabbedContent.TabActivated,
    ) -> None:
        if event.tabbed_content.id != "npc-tabs":
            return
        if self._active_kind() == "flags":
            if not self.flags_loaded:
                self._refresh_flag_table()
            self.query_one("#npc-flags-search", Input).focus()
        else:
            self.query_one("#npc-reputation-search", Input).focus()
        self._refresh_buttons()

    def on_data_table_row_selected(
        self,
        event: DataTable.RowSelected,
    ) -> None:
        if event.data_table.id in (
            "npc-reputation-table",
            "npc-flags-table",
        ):
            self.action_edit_selected()

    def on_data_table_row_highlighted(
        self,
        event: DataTable.RowHighlighted,
    ) -> None:
        if event.data_table.id in (
            "npc-reputation-table",
            "npc-flags-table",
        ):
            if event.data_table.id == "npc-flags-table":
                self._refresh_flag_detail()
            self._refresh_buttons()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "npc-edit": self.action_edit_selected,
            "npc-apply": self.action_apply_changes,
            "npc-discard": self.action_discard_changes,
            "npc-back": self.action_back,
        }
        action = actions.get(event.button.id or "")
        if action is not None:
            action()

    def action_edit_selected(self) -> None:
        if self._active_kind() == "flags":
            self._toggle_selected_flag()
            return
        archetype_id = self._selected_archetype_id()
        if archetype_id is None:
            return
        current_value = self.staged_reputation_values.get(
            archetype_id,
            self.original_reputation_values[archetype_id],
        )
        target = EditableValue(
            label=reputation_editor.reputation_display_name(archetype_id),
            current_value=current_value,
            maximum_value=reputation_editor.MAX_WRITABLE_REPUTATION,
        )
        self.app.push_screen(
            IntegerEditModal(target),
            lambda value: self._value_entered(archetype_id, value),
        )

    def _value_entered(
        self,
        archetype_id: int,
        new_value: Optional[int],
    ) -> None:
        if new_value is None:
            return
        if new_value == self.original_reputation_values[archetype_id]:
            self.staged_reputation_values.pop(archetype_id, None)
        else:
            self.staged_reputation_values[archetype_id] = new_value
        self.call_later(self._refresh_reputation_view)

    def _toggle_selected_flag(self) -> None:
        key = self._selected_flag_key()
        if key is None:
            return
        original = self.original_flag_values[key]
        current = self.staged_flag_values.get(key, original)
        new_value = 0 if current else 1
        if new_value == original:
            self.staged_flag_values.pop(key, None)
        else:
            self.staged_flag_values[key] = new_value
        self.call_later(self._refresh_flag_view)

    def action_discard_changes(self) -> None:
        self.staged_reputation_values.clear()
        self.staged_flag_values.clear()
        self.call_later(self._refresh_reputation_view)
        self.call_later(self._refresh_flag_view)

    def action_apply_changes(self) -> None:
        if not self._dirty():
            return
        self.app.push_screen(
            InventoryApplyConfirmationModal(
                self._confirmation_summary(),
                title="Apply NPC changes",
                confirm_label="Apply NPC changes",
            ),
            self._apply_confirmed,
        )

    async def _apply_confirmed(self, confirmed: bool) -> None:
        if not confirmed:
            return
        try:
            backup_path = self.writer(
                self.record.character_path,
                self._plan(),
                backup_dir=self.backup_dir,
            )
        except (
            OSError,
            editor.SaveFormatError,
            npc_editor.NpcFormatError,
            reputation_editor.ReputationResearchError,
            ValueError,
        ) as exc:
            self.app.push_screen(
                MessageModal("NPC edit failed", str(exc))
            )
            return

        summary = self._change_summary()
        result = self.on_success(backup_path, summary)
        if inspect.isawaitable(result):
            await result
        self.app.pop_screen()

    def action_back(self) -> None:
        if not self._dirty():
            self.app.pop_screen()
            return
        self.app.push_screen(
            InventoryApplyConfirmationModal(
                "Discard staged NPC changes?",
                title="Discard NPC changes",
                confirm_label="Discard changes",
            ),
            self._discard_and_back,
        )

    def _discard_and_back(self, confirmed: bool) -> None:
        if confirmed:
            self.app.pop_screen()


ReputationScreen = NpcScreen


class QuestScreen(Screen[None]):
    """Inspect installed quests against one save's NPC flag table."""

    BINDINGS = [
        Binding("escape", "back", "Back", show=False, priority=True),
        Binding("f", "open_related_flags", "Open related flags"),
    ]

    CSS = """
    QuestScreen {
        background: $background;
    }

    #quest-root {
        height: 1fr;
    }

    #quest-title,
    #quest-status {
        height: auto;
        padding: 0 1;
    }

    #quest-title {
        text-style: bold;
        background: $panel;
    }

    #quest-search {
        margin: 1;
    }

    .quest-filters {
        height: auto;
        padding: 0 1 1 1;
    }

    .quest-filters Button {
        width: auto;
        margin-right: 1;
    }

    #quest-table {
        height: 1fr;
        margin: 0 1;
        border: round $panel;
    }

    #quest-detail {
        height: auto;
        min-height: 8;
        max-height: 12;
        margin: 0 1;
        padding: 0 1;
        border: round $panel;
        color: $text-muted;
        overflow-y: auto;
    }

    .quest-toolbar {
        height: auto;
        padding: 1;
    }

    .quest-toolbar Button {
        width: auto;
        margin-right: 1;
    }
    """

    def __init__(
        self,
        record: editor.SaveRecord,
        npc_data: npc_editor.NpcData,
        catalog: quest_editor.QuestCatalog,
        annotations: npc_editor.NpcFlagAnnotationCatalog,
        source_sha256: str,
        *,
        backup_dir: Path,
        writer: WriteNpcEditPlan,
        on_success: Callable[[Path, str], object],
    ) -> None:
        super().__init__()
        self.record = record
        self.npc_data = npc_data
        self.catalog = catalog
        self.annotations = annotations
        self.source_sha256 = source_sha256
        self.backup_dir = backup_dir
        self.writer = writer
        self.on_success = on_success
        self.states = quest_editor.correlate_quests(
            catalog,
            npc_data.flags,
        )
        self.status_filter = "all"
        self.visible_indices: list[int] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="quest-root"):
            yield Static(
                f"Quests: {self.record.display_name} / "
                f"{self.record.slot_name}",
                id="quest-title",
                markup=False,
            )
            yield Static(
                self._status_text(),
                id="quest-status",
                markup=False,
            )
            yield Input(
                placeholder=(
                    "Search quest title, NPC, flag, asset, or evidence"
                ),
                id="quest-search",
            )
            with Horizontal(classes="quest-filters"):
                yield Button("All", id="quest-filter-all")
                yield Button(
                    "Likely undiscovered",
                    id="quest-filter-undiscovered",
                )
                yield Button("In progress", id="quest-filter-progress")
                yield Button("Completed?", id="quest-filter-completed")
                yield Button("No save flags", id="quest-filter-no-flags")
                yield Button(
                    "Repeatable/temp",
                    id="quest-filter-repeatable",
                )
            table = DataTable(id="quest-table")
            table.cursor_type = "row"
            table.zebra_stripes = True
            for label, key, width in _QUEST_TABLE_COLUMNS:
                table.add_column(label, key=key, width=width)
            yield table
            yield Static(
                "Select a quest to inspect its save-flag evidence.",
                id="quest-detail",
                markup=False,
            )
            with Horizontal(classes="quest-toolbar"):
                yield Button(
                    "Open related flags",
                    id="quest-open-flags",
                    variant="primary",
                )
                yield Button("Back", id="quest-back")

    def on_mount(self) -> None:
        self.query_one("#quest-search", Input).focus()
        self._refresh_quest_table()
        self._refresh_buttons()

    def _status_text(self) -> str:
        warning = (
            "\nQuest warning: " + " ".join(self.catalog.warnings)
            if self.catalog.warnings
            else ""
        )
        return (
            f"{len(self.catalog.quests)} installed quest definitions | "
            "status is inferred from referenced save flags"
            f"{warning}"
        )

    def _filter_matches(self, state: quest_editor.QuestState) -> bool:
        if self.status_filter == "all":
            return True
        if self.status_filter == "undiscovered":
            return (
                state.progress.status
                == quest_editor.QUEST_STATUS_NOT_STARTED
            )
        if self.status_filter == "in-progress":
            return (
                state.progress.status
                == quest_editor.QUEST_STATUS_IN_PROGRESS
            )
        if self.status_filter == "completed":
            return state.progress.status == quest_editor.QUEST_STATUS_COMPLETED
        if self.status_filter == "no-flags":
            return state.progress.status == quest_editor.QUEST_STATUS_NO_FLAGS
        if self.status_filter == "repeatable":
            return state.definition.is_repeatable
        return True

    def _state_matches(self, state: quest_editor.QuestState, query: str) -> bool:
        return (
            self._filter_matches(state)
            and (
                not query
                or query in quest_editor.quest_search_text(state)
            )
        )

    def _refresh_quest_table(self) -> None:
        query = self.query_one("#quest-search", Input).value.strip().casefold()
        self.visible_indices = [
            index
            for index, state in enumerate(self.states)
            if self._state_matches(state, query)
        ]
        table = self.query_one("#quest-table", DataTable)
        table.clear(columns=False)
        for index in self.visible_indices:
            state = self.states[index]
            definition = state.definition
            progress = state.progress
            table.add_row(
                Text(definition.title, overflow="ellipsis", no_wrap=True),
                Text(progress.status, overflow="ellipsis", no_wrap=True),
                progress.flag_summary,
                Text(definition.type_label, overflow="ellipsis", no_wrap=True),
                key=f"quest:{index}",
            )
        if self.visible_indices:
            table.move_cursor(row=0, column=0, animate=False)
        self._refresh_detail()

    def _selected_state(self) -> Optional[quest_editor.QuestState]:
        table = self.query_one("#quest-table", DataTable)
        row = table.cursor_row
        if row < 0 or row >= len(self.visible_indices):
            return None
        return self.states[self.visible_indices[row]]

    def _format_list(self, values: Sequence[str]) -> str:
        return ", ".join(values) if values else "-"

    def _refresh_detail(self) -> None:
        detail = self.query_one("#quest-detail", Static)
        state = self._selected_state()
        if state is None:
            detail.update("No quests match the current search and filter.")
            return
        definition = state.definition
        progress = state.progress
        lines = [
            f"Quest: {definition.title}",
            f"Status: {progress.status} - {progress.reason}",
            f"Type: {definition.type_label}",
            f"Asset: {definition.asset_path}",
            f"NPCs: {self._format_list(definition.associations)}",
            f"Flags: {progress.flag_summary}",
            f"Enabled: {self._format_list(progress.enabled_flags)}",
            f"Disabled: {self._format_list(progress.disabled_flags)}",
        ]
        if progress.missing_flags:
            lines.append(f"Missing: {self._format_list(progress.missing_flags)}")
        if definition.evidence:
            lines.append("Evidence:")
            lines.extend(f"- {text}" for text in definition.evidence)
        detail.update("\n".join(lines))

    def _refresh_buttons(self) -> None:
        state = self._selected_state()
        self.query_one("#quest-open-flags", Button).disabled = (
            state is None or not state.progress.known_flags
        )

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "quest-search":
            self._refresh_quest_table()
            self._refresh_buttons()

    def on_data_table_row_highlighted(
        self,
        event: DataTable.RowHighlighted,
    ) -> None:
        if event.data_table.id == "quest-table":
            self._refresh_detail()
            self._refresh_buttons()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id in _QUEST_FILTERS:
            self.status_filter = _QUEST_FILTERS[button_id][0]
            self.query_one("#quest-status", Static).update(
                f"{self._status_text()}\nFilter: "
                f"{_QUEST_FILTERS[button_id][1]}"
            )
            self._refresh_quest_table()
            self._refresh_buttons()
        elif button_id == "quest-open-flags":
            self.action_open_related_flags()
        elif button_id == "quest-back":
            self.action_back()

    def action_open_related_flags(self) -> None:
        state = self._selected_state()
        if state is None or not state.progress.known_flags:
            self.app.push_screen(
                MessageModal(
                    "No related flags",
                    "The selected quest has no known save flags.",
                )
            )
            return
        self.app.push_screen(
            NpcScreen(
                self.record,
                self.npc_data,
                self.annotations,
                self.source_sha256,
                backup_dir=self.backup_dir,
                writer=self.writer,
                on_success=self.on_success,
                initial_flag_keys=state.progress.known_flags,
                initial_flag_filter_label=(
                    f"Quest: {state.definition.title}"
                ),
            )
        )

    def action_back(self) -> None:
        self.app.pop_screen()


class InventoryScreen(Screen[None]):
    """Stage and apply full inventory changes for one save."""

    BINDINGS = [
        Binding("escape", "back", "Back", show=False, priority=True),
        Binding("a", "add_item", "Add"),
        Binding("d", "remove_item", "Remove"),
        Binding("q", "change_quantity", "Quantity"),
        Binding("e", "equip_item", "Equip"),
        Binding("u", "unequip_slot", "Unequip"),
        Binding("ctrl+s", "apply_changes", "Apply"),
    ]

    CSS = """
    InventoryScreen {
        background: $background;
    }

    #inventory-root {
        height: 1fr;
    }

    #inventory-title,
    #inventory-status,
    #inventory-staged {
        height: auto;
        padding: 0 1;
    }

    #inventory-title {
        text-style: bold;
        background: $panel;
    }

    #inventory-main {
        height: 1fr;
    }

    #inventory-carried-pane {
        width: 3fr;
        height: 1fr;
        border: round $panel;
    }

    #inventory-slot-pane {
        width: 2fr;
        height: 1fr;
        border: round $panel;
    }

    #inventory-search {
        margin: 0 1;
    }

    #inventory-items,
    #inventory-slots {
        height: 1fr;
    }

    #inventory-items,
    #inventory-slots {
        border-top: solid $panel;
    }

    #inventory-detail {
        height: auto;
        max-height: 8;
        padding: 0 1;
        color: $text-muted;
        border-top: solid $panel;
        overflow-y: auto;
    }

    .inventory-toolbar {
        height: auto;
        padding: 0 1;
    }

    .inventory-toolbar Button {
        margin-right: 1;
        width: auto;
    }

    #inventory-staged {
        max-height: 4;
        color: $text-muted;
        overflow-y: auto;
    }

    .narrow #inventory-main {
        layout: vertical;
    }

    .narrow #inventory-carried-pane,
    .narrow #inventory-slot-pane {
        width: 1fr;
    }
    """

    def __init__(
        self,
        record: editor.SaveRecord,
        catalog: inventory_editor.ItemCatalog,
        equipment: inventory_editor.EquipmentSnapshot,
        snapshot: inventory_editor.InventorySnapshot,
        *,
        backup_dir: Path,
        writer: WriteFullInventoryEdit,
        on_success: Callable[[Path, str], object],
    ) -> None:
        super().__init__()
        self.record = record
        self.catalog = catalog
        self.backup_dir = backup_dir
        self.writer = writer
        self.on_success = on_success
        plan = inventory_editor.inventory_edit_plan_from_snapshots(
            equipment,
            snapshot,
        )
        self.source_sha256 = plan.source_sha256
        self.original_equipment = list(plan.equipment_slot_raws)
        self.original_bags = list(plan.saved_bag_slot_raws)
        self.original_carried = list(plan.carried_item_raws)
        self.equipment_slot_raws = list(plan.equipment_slot_raws)
        self.saved_bag_slot_raws = list(plan.saved_bag_slot_raws)
        self.carried_item_raws = list(plan.carried_item_raws)
        self.descriptions: list[str] = []
        self.visible_carried_positions: list[int] = []
        self.visible_slots: list[tuple[str, str]] = []
        self.selection_kind = "carried"

    def compose(self) -> ComposeResult:
        with Vertical(id="inventory-root"):
            yield Static(
                f"Inventory: {self.record.display_name} / "
                f"{self.record.slot_name}",
                id="inventory-title",
                markup=False,
            )
            yield Static("", id="inventory-status", markup=False)
            with Horizontal(id="inventory-main"):
                with Vertical(id="inventory-carried-pane"):
                    yield Static("Carried items", classes="pane-title")
                    yield Input(
                        placeholder="Search name, ID, or category",
                        id="inventory-search",
                    )
                    item_table = DataTable(id="inventory-items")
                    item_table.cursor_type = "row"
                    item_table.zebra_stripes = True
                    item_table.add_columns(
                        "Name",
                        "Qty",
                        "Category",
                        "ID",
                        "Type",
                    )
                    yield item_table
                    with Horizontal(classes="inventory-toolbar"):
                        yield Button("Add", id="inventory-add")
                        yield Button("Remove", id="inventory-remove")
                        yield Button("Quantity", id="inventory-quantity")
                        yield Button("Equip", id="inventory-equip")
                with Vertical(id="inventory-slot-pane"):
                    yield Static("Equipment", classes="pane-title")
                    slot_table = DataTable(id="inventory-slots")
                    slot_table.cursor_type = "row"
                    slot_table.zebra_stripes = True
                    slot_table.add_columns("Slot", "Item", "Qty", "ID")
                    yield slot_table
                    yield Static("", id="inventory-detail", markup=False)
                    with Horizontal(classes="inventory-toolbar"):
                        yield Button("Unequip", id="inventory-unequip")
                        yield Button("Remove", id="inventory-remove-slot")
            yield Static("", id="inventory-staged", markup=False)
            with Horizontal(classes="inventory-toolbar"):
                yield Button("Apply", id="inventory-apply", variant="primary")
                yield Button("Discard", id="inventory-discard")
                yield Button("Back", id="inventory-back")

    async def on_mount(self) -> None:
        self._apply_responsive_layout(self.size.width)
        self.query_one("#inventory-search", Input).focus()
        await self._refresh_lists()
        self.selection_kind = "carried"
        self._refresh_detail()
        self._refresh_action_buttons()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_layout(event.size.width)

    def _apply_responsive_layout(self, width: int) -> None:
        self.set_class(width < 90, "narrow")

    def _dirty(self) -> bool:
        return (
            self.equipment_slot_raws != self.original_equipment
            or self.saved_bag_slot_raws != self.original_bags
            or self.carried_item_raws != self.original_carried
        )

    def _raw_item(self, raw: bytes) -> inventory_editor.InventoryItem:
        return inventory_editor.parse_item_record(raw, self.catalog)

    def _item_cells(self, raw: bytes) -> tuple[str, str, str, str, str]:
        item = self._raw_item(raw)
        return (
            item.definition.name,
            str(item.quantity),
            item.definition.category,
            str(item.definition.item_id),
            str(item.serialize_type),
        )

    def _slot_raw_label(self, raw: bytes) -> str:
        if raw == b"\0":
            return "Empty"
        item = self._raw_item(raw)
        return (
            f"{item.definition.name} | ID {item.definition.item_id} | "
            f"qty {item.quantity}"
        )

    def _slot_cells(self, label: str, raw: bytes) -> tuple[str, str, str, str]:
        if raw == b"\0":
            return (label, "Empty", "", "")
        item = self._raw_item(raw)
        return (
            label,
            item.definition.name,
            str(item.quantity),
            str(item.definition.item_id),
        )

    def _slot_rows(self) -> list[tuple[str, str, tuple[str, str, str, str]]]:
        rows: list[tuple[str, str, tuple[str, str, str, str]]] = []
        for index, (slot_name, _, _) in enumerate(
            inventory_editor.EQUIPMENT_SLOT_SPECS
        ):
            rows.append(
                (
                    "equipment",
                    slot_name,
                    self._slot_cells(
                        slot_name.title(),
                        self.equipment_slot_raws[index],
                    ),
                )
            )
        for index, raw in enumerate(self.saved_bag_slot_raws):
            rows.append(
                (
                    "bag",
                    str(index),
                    self._slot_cells(f"Bag slot {index + 1}", raw),
                )
            )
        return rows

    def _matches_search(self, raw: bytes) -> bool:
        query = self.query_one("#inventory-search", Input).value.strip()
        if not query:
            return True
        item = self._raw_item(raw)
        haystack = (
            f"{item.definition.item_id} {item.definition.name} "
            f"{item.definition.category} {item.serialize_type}"
        ).casefold()
        return all(part in haystack for part in query.casefold().split())

    async def _refresh_lists(self) -> None:
        selection_kind = self.selection_kind
        carried_table = self.query_one("#inventory-items", DataTable)
        selected_position = self._selected_carried_position()
        self.visible_carried_positions = [
            position
            for position, raw in enumerate(self.carried_item_raws)
            if self._matches_search(raw)
        ]
        carried_table.clear()
        for position in self.visible_carried_positions:
            carried_table.add_row(
                *self._item_cells(self.carried_item_raws[position]),
                key=f"carried:{position}",
            )
        if self.visible_carried_positions and selection_kind == "carried":
            selected_index = 0
            if selected_position is not None:
                selected_index = next(
                    (
                        index
                        for index, position in enumerate(
                            self.visible_carried_positions
                        )
                        if position == selected_position
                    ),
                    0,
                )
            carried_table.move_cursor(
                row=selected_index,
                column=0,
                animate=False,
            )

        slot_table = self.query_one("#inventory-slots", DataTable)
        selected_slot = self._selected_slot_key()
        slot_rows = self._slot_rows()
        self.visible_slots = [(kind, key) for kind, key, _ in slot_rows]
        slot_table.clear()
        for kind, key, cells in slot_rows:
            slot_table.add_row(*cells, key=f"{kind}:{key}")
        if slot_rows and selection_kind == "slot":
            selected_index = 0
            if selected_slot is not None:
                selected_index = next(
                    (
                        index
                        for index, key in enumerate(self.visible_slots)
                        if key == selected_slot
                    ),
                    0,
                )
            slot_table.move_cursor(
                row=selected_index,
                column=0,
                animate=False,
            )

        self.selection_kind = selection_kind
        self._refresh_status()

    def _refresh_status(self) -> None:
        status = self.query_one("#inventory-status", Static)
        status.update(
            f"{len(self.carried_item_raws)} carried records | "
            f"{len(self.saved_bag_slot_raws)} saved-bag slots | "
            f"{len(self.descriptions)} staged changes"
        )
        staged = self.query_one("#inventory-staged", Static)
        if not self.descriptions:
            staged.update("No staged inventory changes.")
        else:
            staged.update("\n".join(self.descriptions[-4:]))
        self._refresh_detail()
        self._refresh_action_buttons()

    def _refresh_detail(self) -> None:
        detail = self.query_one("#inventory-detail", Static)
        if self.selection_kind == "slot":
            selected = self._selected_slot_key()
            if selected is None:
                detail.update("Select a slot to inspect or modify it.")
                return
            kind, key = selected
            raw = self._slot_raw(kind, key)
            label = (
                key.title()
                if kind == "equipment"
                else f"Bag slot {int(key) + 1}"
            )
            if raw == b"\0":
                detail.update(f"{label}\nEmpty slot")
                return
            item = self._raw_item(raw)
            detail.update(
                "\n".join(
                    [
                        label,
                        item.definition.name,
                        f"ID {item.definition.item_id} | "
                        f"{item.definition.category}",
                        f"Quantity {item.quantity} | "
                        f"type {item.serialize_type}",
                    ]
                )
            )
            return

        position = self._selected_carried_position()
        if position is None:
            detail.update("Select a carried item to inspect or modify it.")
            return
        item = self._raw_item(self.carried_item_raws[position])
        detail.update(
            "\n".join(
                [
                    item.definition.name,
                    f"ID {item.definition.item_id} | "
                    f"{item.definition.category}",
                    f"Quantity {item.quantity} | type {item.serialize_type}",
                    "Actions: quantity, remove, equip when compatible.",
                ]
            )
        )

    def _refresh_action_buttons(self) -> None:
        carried_position = self._selected_carried_position()
        carried_raw = (
            self.carried_item_raws[carried_position]
            if carried_position is not None
            else None
        )
        slot_key = self._selected_slot_key()
        slot_raw = (
            self._slot_raw(*slot_key)
            if slot_key is not None
            else b"\0"
        )
        has_carried_selection = (
            self.selection_kind == "carried" and carried_raw is not None
        )
        has_slot_selection = self.selection_kind == "slot" and slot_key is not None
        can_equip = (
            has_carried_selection
            and carried_raw is not None
            and bool(self._compatible_slot_choices(carried_raw))
        )
        can_unequip_or_remove = has_slot_selection and slot_raw != b"\0"

        self.query_one("#inventory-remove", Button).disabled = (
            not has_carried_selection
        )
        self.query_one("#inventory-quantity", Button).disabled = (
            not has_carried_selection
        )
        self.query_one("#inventory-equip", Button).disabled = not can_equip
        self.query_one("#inventory-unequip", Button).disabled = (
            not can_unequip_or_remove
        )
        self.query_one("#inventory-remove-slot", Button).disabled = (
            not can_unequip_or_remove
        )
        self.query_one("#inventory-apply", Button).disabled = not self._dirty()
        self.query_one("#inventory-discard", Button).disabled = not self._dirty()

    def _selected_carried_position(self) -> Optional[int]:
        carried_table = self.query_one("#inventory-items", DataTable)
        if not self.visible_carried_positions:
            return None
        row = carried_table.cursor_row
        if row < 0 or row >= len(self.visible_carried_positions):
            return None
        return self.visible_carried_positions[row]

    def _selected_slot_key(self) -> Optional[tuple[str, str]]:
        slot_table = self.query_one("#inventory-slots", DataTable)
        if not self.visible_slots:
            return None
        row = slot_table.cursor_row
        if row < 0 or row >= len(self.visible_slots):
            return None
        return self.visible_slots[row]

    def _slot_raw(self, kind: str, key: str) -> bytes:
        if kind == "equipment":
            index = self._equipment_index(key)
            return self.equipment_slot_raws[index]
        return self.saved_bag_slot_raws[int(key)]

    def _set_slot_raw(self, kind: str, key: str, raw: bytes) -> None:
        if kind == "equipment":
            index = self._equipment_index(key)
            self.equipment_slot_raws[index] = raw
        else:
            self.saved_bag_slot_raws[int(key)] = raw

    def _equipment_index(self, slot_name: str) -> int:
        for index, (name, _, _) in enumerate(
            inventory_editor.EQUIPMENT_SLOT_SPECS
        ):
            if name == slot_name:
                return index
        raise ValueError(f"Unknown equipment slot {slot_name!r}")

    def _stage(self, description: str) -> None:
        self.descriptions.append(description)

    def _message(self, title: str, message: str) -> None:
        self.app.push_screen(MessageModal(title, message))

    def _compatible_slot_choices(
        self,
        raw: bytes,
    ) -> list[tuple[str, str, str]]:
        item = self._raw_item(raw)
        choices: list[tuple[str, str, str]] = []
        for slot_name, _, _ in inventory_editor.EQUIPMENT_SLOT_SPECS:
            if inventory_editor.equipment_slot_accepts(
                slot_name,
                item.definition,
            ):
                choices.append(
                    (
                        "equipment",
                        slot_name,
                        f"{slot_name.title()}: "
                        f"{self._slot_raw_label(self._slot_raw('equipment', slot_name))}",
                    )
                )
        if item.serialize_type == 2:
            for index, slot_raw in enumerate(self.saved_bag_slot_raws):
                choices.append(
                    (
                        "bag",
                        str(index),
                        f"Bag slot {index + 1}: "
                        f"{self._slot_raw_label(slot_raw)}",
                    )
                )
        return choices

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "inventory-search":
            self.selection_kind = "carried"
            await self._refresh_lists()

    def on_data_table_row_highlighted(
        self,
        event: DataTable.RowHighlighted,
    ) -> None:
        if not event.data_table.has_focus:
            return
        if event.data_table.id == "inventory-items":
            self.selection_kind = "carried"
            self._refresh_detail()
            self._refresh_action_buttons()
        elif event.data_table.id == "inventory-slots":
            self.selection_kind = "slot"
            self._refresh_detail()
            self._refresh_action_buttons()

    def on_data_table_row_selected(
        self,
        event: DataTable.RowSelected,
    ) -> None:
        if not event.data_table.has_focus:
            return
        if event.data_table.id == "inventory-items":
            self.selection_kind = "carried"
        elif event.data_table.id == "inventory-slots":
            self.selection_kind = "slot"
        self._refresh_detail()
        self._refresh_action_buttons()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "inventory-add": self.action_add_item,
            "inventory-remove": self.action_remove_item,
            "inventory-quantity": self.action_change_quantity,
            "inventory-equip": self.action_equip_item,
            "inventory-unequip": self.action_unequip_slot,
            "inventory-remove-slot": self.action_remove_slot,
            "inventory-apply": self.action_apply_changes,
            "inventory-discard": self.action_discard_changes,
            "inventory-back": self.action_back,
        }
        action = actions.get(event.button.id or "")
        if action is not None:
            action()

    def action_add_item(self) -> None:
        self.app.push_screen(
            CatalogSearchModal(self.catalog),
            self._catalog_item_selected,
        )

    def _catalog_item_selected(
        self,
        definition: Optional[inventory_editor.ItemDefinition],
    ) -> None:
        if definition is None:
            return
        self.app.push_screen(
            InventoryQuantityModal(f"Quantity for {definition.name}", 1),
            lambda quantity: self._catalog_quantity_entered(
                definition,
                quantity,
            ),
        )

    async def _catalog_quantity_entered(
        self,
        definition: inventory_editor.ItemDefinition,
        quantity: Optional[int],
    ) -> None:
        if quantity is None:
            return
        raw = inventory_editor.default_item_record(
            self.catalog,
            definition.item_id,
            quantity,
        )
        self.carried_item_raws = list(
            inventory_editor.merge_carried_item_raw(
                self.carried_item_raws,
                raw,
                self.catalog,
            )
        )
        self._stage(f"Add {quantity} x {definition.name}")
        await self._refresh_lists()

    def action_remove_item(self) -> None:
        position = self._selected_carried_position()
        if position is None:
            self._message("No item selected", "Select a carried item first.")
            return
        item = self._raw_item(self.carried_item_raws[position])
        del self.carried_item_raws[position]
        self._stage(f"Remove carried {item.definition.name}")
        self.call_later(self._refresh_lists)

    def action_change_quantity(self) -> None:
        position = self._selected_carried_position()
        if position is None:
            self._message("No item selected", "Select a carried item first.")
            return
        item = self._raw_item(self.carried_item_raws[position])
        self.app.push_screen(
            InventoryQuantityModal(
                f"Quantity for {item.definition.name}",
                item.quantity,
            ),
            lambda quantity: self._quantity_entered(position, quantity),
        )

    async def _quantity_entered(
        self,
        position: int,
        quantity: Optional[int],
    ) -> None:
        if quantity is None:
            return
        if position >= len(self.carried_item_raws):
            self._message("Selection changed", "Select the item again.")
            return
        item = self._raw_item(self.carried_item_raws[position])
        self.carried_item_raws[position] = inventory_editor.record_with_quantity(
            self.carried_item_raws[position],
            quantity,
        )
        self._stage(f"Set {item.definition.name} quantity to {quantity}")
        await self._refresh_lists()

    def action_equip_item(self) -> None:
        position = self._selected_carried_position()
        if position is None:
            self._message("No item selected", "Select a carried item first.")
            return
        raw = self.carried_item_raws[position]
        item = self._raw_item(raw)
        choices = self._compatible_slot_choices(raw)
        if not choices:
            self._message(
                "Cannot equip item",
                f"{item.definition.name} has no compatible slot.",
            )
            return
        self.app.push_screen(
            SlotSelectModal(f"Equip {item.definition.name}", choices),
            lambda target: self._equip_target_selected(position, target),
        )

    async def _equip_target_selected(
        self,
        position: int,
        target: Optional[tuple[str, str]],
    ) -> None:
        if target is None:
            return
        if position >= len(self.carried_item_raws):
            self._message("Selection changed", "Select the item again.")
            return
        kind, key = target
        item = self._raw_item(self.carried_item_raws[position])
        new_carried, equipped_raw = (
            inventory_editor.remove_one_from_carried_raw(
                self.carried_item_raws,
                position,
                self.catalog,
            )
        )
        self.carried_item_raws = list(new_carried)
        displaced = self._slot_raw(kind, key)
        self._set_slot_raw(kind, key, equipped_raw)
        if displaced != b"\0":
            self.carried_item_raws = list(
                inventory_editor.merge_carried_item_raw(
                    self.carried_item_raws,
                    displaced,
                    self.catalog,
                )
            )
        self._stage(f"Equip {item.definition.name}")
        await self._refresh_lists()

    def action_unequip_slot(self) -> None:
        selected = self._selected_slot_key()
        if selected is None:
            self._message("No slot selected", "Select a slot first.")
            return
        kind, key = selected
        raw = self._slot_raw(kind, key)
        if raw == b"\0":
            self._message("Empty slot", "The selected slot is already empty.")
            return
        item = self._raw_item(raw)
        self._set_slot_raw(kind, key, b"\0")
        self.carried_item_raws = list(
            inventory_editor.merge_carried_item_raw(
                self.carried_item_raws,
                raw,
                self.catalog,
            )
        )
        self._stage(f"Unequip {item.definition.name}")
        self.call_later(self._refresh_lists)

    def action_remove_slot(self) -> None:
        selected = self._selected_slot_key()
        if selected is None:
            self._message("No slot selected", "Select a slot first.")
            return
        kind, key = selected
        raw = self._slot_raw(kind, key)
        if raw == b"\0":
            self._message("Empty slot", "The selected slot is already empty.")
            return
        item = self._raw_item(raw)
        self._set_slot_raw(kind, key, b"\0")
        self._stage(f"Remove equipped {item.definition.name}")
        self.call_later(self._refresh_lists)

    def action_discard_changes(self) -> None:
        self.equipment_slot_raws = list(self.original_equipment)
        self.saved_bag_slot_raws = list(self.original_bags)
        self.carried_item_raws = list(self.original_carried)
        self.descriptions.clear()
        self.call_later(self._refresh_lists)

    def _apply_summary(self) -> str:
        if not self.descriptions:
            return "No staged inventory changes."
        return "\n".join(f"- {description}" for description in self.descriptions)

    def _plan(self) -> inventory_editor.FullInventoryEditPlan:
        return inventory_editor.FullInventoryEditPlan(
            source_sha256=self.source_sha256,
            equipment_slot_raws=tuple(self.equipment_slot_raws),
            saved_bag_slot_raws=tuple(self.saved_bag_slot_raws),
            carried_item_raws=tuple(self.carried_item_raws),
            descriptions=tuple(self.descriptions),
        )

    def action_apply_changes(self) -> None:
        if not self._dirty():
            self._message("Nothing to apply", "No inventory changes are staged.")
            return
        self.app.push_screen(
            InventoryApplyConfirmationModal(self._apply_summary()),
            self._apply_confirmed,
        )

    async def _apply_confirmed(self, confirmed: bool) -> None:
        if not confirmed:
            return
        try:
            backup_path = self.writer(
                self.record.character_path,
                self.catalog,
                self._plan(),
                backup_dir=self.backup_dir,
            )
        except (
            OSError,
            editor.SaveFormatError,
            inventory_editor.InventoryFormatError,
            inventory_editor.InventoryMutationError,
            ValueError,
        ) as exc:
            self._message("Inventory edit failed", str(exc))
            return

        summary = self._apply_summary()
        result = self.on_success(backup_path, summary)
        if inspect.isawaitable(result):
            await result
        self.app.pop_screen()

    def action_back(self) -> None:
        if not self._dirty():
            self.app.pop_screen()
            return
        self.app.push_screen(
            InventoryApplyConfirmationModal(
                "Discard staged inventory changes?",
                title="Discard inventory changes",
                confirm_label="Discard changes",
            ),
            self._discard_and_back,
        )

    def _discard_and_back(self, confirmed: bool) -> None:
        if confirmed:
            self.app.pop_screen()


class HoboSaveEditorApp(App[int]):
    """Full-screen save editor application."""

    TITLE = "Hobo: Tough Life Save Editor"
    SUB_TITLE = "Save editor"

    BINDINGS = [
        Binding("q", "quit_app", "Quit"),
        Binding("r", "refresh_saves", "Refresh"),
        Binding("e", "edit_selected", "Character/Time"),
        Binding("i", "inventory", "Inventory"),
        Binding("p", "npcs", "NPCs"),
    ]
    ACTION_BUTTON_IDS = (
        "edit-value",
        "inventory-action",
        "npc-action",
        "quest-action",
    )

    CSS = """
    Screen {
        background: $background;
    }

    #safety-notice {
        height: auto;
        padding: 0 1;
        color: $warning;
        text-style: bold;
        background: $boost;
    }

    #location-bar {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }

    #warnings {
        display: none;
        height: auto;
        max-height: 5;
        padding: 0 1;
        color: $warning;
        border-bottom: solid $warning-muted;
        overflow-y: auto;
    }

    #warnings.visible {
        display: block;
    }

    #editor {
        height: 1fr;
    }

    #save-pane {
        width: 2fr;
        height: 1fr;
        border: round $panel;
    }

    #detail-pane {
        width: 3fr;
        height: 1fr;
        padding: 0 1;
        border: round $panel;
    }

    .pane-title {
        height: auto;
        padding: 0 1;
        text-style: bold;
        background: $panel;
    }

    #save-list {
        height: 1fr;
    }

    #save-list > ListItem {
        padding: 0 1;
    }

    #empty-state {
        display: none;
        padding: 1;
        color: $text-muted;
    }

    #empty-state.visible {
        display: block;
    }

    #save-details {
        height: auto;
        padding: 1 0;
    }

    #actions {
        height: auto;
        padding: 1 0;
        border-bottom: solid $panel;
    }

    #actions Button {
        display: none;
        width: auto;
        margin-right: 1;
    }

    #actions Button.visible {
        display: block;
    }

    #actions Button:focus {
        background: $accent;
        color: $text;
        text-style: bold;
    }

    #save-detail-scroll {
        height: 1fr;
    }

    #result {
        display: none;
        height: auto;
        max-height: 4;
        padding: 0 1;
        color: $success;
        border-top: solid $success-muted;
        overflow-y: auto;
    }

    #result.visible {
        display: block;
    }

    .narrow #editor {
        layout: vertical;
    }

    .narrow #save-pane {
        width: 1fr;
        height: 2fr;
    }

    .narrow #detail-pane {
        width: 1fr;
        height: 3fr;
    }
    """

    def __init__(
        self,
        game_dir: Optional[Path] = None,
        backup_dir: Optional[Path] = None,
        discover_installs: DiscoverInstalls = editor.discover_game_installs,
        scan_saves: ScanSaves = editor.scan_saves,
        set_cash: SetCash = editor.set_cash,
        set_primary_parameter: SetPrimaryParameter = (
            editor.set_primary_parameter
        ),
        set_world_time_value: SetWorldTimeValue = (
            editor.set_world_time_value
        ),
        inventory_catalog_loader: LoadItemCatalog = (
            inventory_editor.load_item_catalog
        ),
        bag_transfer_writer: WriteBagTransfer = (
            inventory_editor.write_bag_transfer
        ),
        full_inventory_writer: WriteFullInventoryEdit = (
            inventory_editor.write_full_inventory_edit
        ),
        npc_parser: ParseNpcData = npc_editor.parse_npc_data,
        npc_annotation_loader: LoadNpcAnnotations = (
            npc_editor.load_npc_flag_annotations
        ),
        quest_catalog_loader: LoadQuestCatalog = (
            quest_editor.load_quest_catalog
        ),
        npc_writer: WriteNpcEditPlan = (
            npc_editor.write_npc_edit_plan
        ),
    ) -> None:
        super().__init__()
        self.requested_game_dir = game_dir
        self.game_dir: Optional[Path] = None
        self.backup_dir = editor.resolve_backup_dir(backup_dir)
        self.discover_installs = discover_installs
        self.scan_saves = scan_saves
        self.cash_writer = set_cash
        self.primary_writer = set_primary_parameter
        self.world_time_writer = set_world_time_value
        self.inventory_catalog_loader = inventory_catalog_loader
        self.bag_transfer_writer = bag_transfer_writer
        self.full_inventory_writer = full_inventory_writer
        self.npc_parser = npc_parser
        self.npc_annotation_loader = npc_annotation_loader
        self.quest_catalog_loader = quest_catalog_loader
        self.npc_writer = npc_writer
        self.npc_annotations: Optional[
            npc_editor.NpcFlagAnnotationCatalog
        ] = None
        self.npc_annotation_keys: tuple[str, ...] = ()
        self.npc_annotation_game_dir: Optional[Path] = None
        self.quest_catalog: Optional[quest_editor.QuestCatalog] = None
        self.quest_catalog_game_dir: Optional[Path] = None
        self.records: list[editor.SaveRecord] = []
        self.selected_record: Optional[editor.SaveRecord] = None
        self.last_backup_path: Optional[Path] = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "SAFETY: Close the game before modifying a save.",
            id="safety-notice",
            markup=False,
        )
        yield Static(
            f"Installation: not selected\nBackups: {self.backup_dir}",
            id="location-bar",
            markup=False,
        )
        yield Static("", id="warnings", markup=False)
        with Horizontal(id="editor"):
            with Vertical(id="save-pane"):
                yield Static("Saves", classes="pane-title", markup=False)
                yield ListView(id="save-list")
                yield Static(
                    "Select an installation to scan for saves.",
                    id="empty-state",
                    classes="visible",
                    markup=False,
                )
            with Vertical(id="detail-pane"):
                yield Static(
                    "Selected save",
                    classes="pane-title",
                    markup=False,
                )
                with Horizontal(id="actions"):
                    yield Button(
                        "Character/Time",
                        id="edit-value",
                        disabled=True,
                    )
                    yield Button(
                        "Inventory",
                        id="inventory-action",
                        disabled=True,
                    )
                    yield Button(
                        "NPCs",
                        id="npc-action",
                        disabled=True,
                    )
                    yield Button(
                        "Quests",
                        id="quest-action",
                        disabled=True,
                    )
                with VerticalScroll(id="save-detail-scroll"):
                    yield Static(
                        "No save selected.",
                        id="save-details",
                        markup=False,
                    )
        yield Static("", id="result", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self._apply_responsive_layout(self.size.width)
        self.call_after_refresh(self._start)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_layout(event.size.width)

    def _apply_responsive_layout(self, width: int) -> None:
        self.screen.set_class(width < 80, "narrow")

    def _start(self) -> None:
        if self.requested_game_dir is not None:
            requested = self.requested_game_dir.expanduser().absolute()
            if (requested / "HoboRPG_Data").is_dir():
                self.call_later(self._load_installation, requested)
            else:
                self.push_screen(
                    PathEntryModal(
                        initial_path=requested,
                        message=(
                            "The startup path is not a valid installation. "
                            "Choose the directory containing HoboRPG_Data."
                        ),
                    ),
                    self._startup_path_selected,
                )
            return

        try:
            installs = self.discover_installs()
        except OSError as exc:
            self.push_screen(
                PathEntryModal(
                    message=(
                        f"Installation discovery failed: {exc}\n"
                        "Enter the game installation directory."
                    )
                ),
                self._startup_path_selected,
            )
            return

        if len(installs) == 1:
            self.call_later(self._load_installation, installs[0])
        elif installs:
            self.push_screen(
                InstallationScreen(installs),
                self._startup_path_selected,
            )
        else:
            self.push_screen(
                PathEntryModal(
                    message=(
                        "No installation was found in the standard Steam "
                        "locations. Enter the game installation directory."
                    )
                ),
                self._startup_path_selected,
            )

    async def _startup_path_selected(self, path: Optional[Path]) -> None:
        if path is None:
            self.exit(0)
            return
        await self._load_installation(path)

    async def _load_installation(self, path: Path) -> None:
        self.game_dir = path.expanduser().absolute()
        self.query_one("#location-bar", Static).update(
            f"Installation: {self.game_dir}\nBackups: {self.backup_dir}"
        )
        await self.refresh_saves(preserve_selection=False)
        self.query_one("#save-list", ListView).focus()

    def _save_label(self, record: editor.SaveRecord) -> str:
        details = (
            f"{record.slot_name} | {record.display_name} | "
            f"{record.saved_at}"
        )
        try:
            return f"{details}\nCash: {record.current_cash()}"
        except (OSError, editor.SaveFormatError) as exc:
            return f"{details}\nCash unavailable: {exc}"

    async def refresh_saves(self, preserve_selection: bool = True) -> None:
        if self.game_dir is None:
            return

        selected_id = (
            self.selected_record.save_id
            if preserve_selection and self.selected_record is not None
            else None
        )
        try:
            records, warnings = self.scan_saves(self.game_dir)
        except OSError as exc:
            records = []
            warnings = [f"Could not scan saves: {exc}"]

        self.records = records
        save_list = self.query_one("#save-list", ListView)
        await save_list.clear()
        await save_list.extend(
            SaveListItem(record, self._save_label(record))
            for record in records
        )

        warning_widget = self.query_one("#warnings", Static)
        warning_widget.update(
            "\n".join(f"Warning: {warning}" for warning in warnings)
        )
        warning_widget.set_class(bool(warnings), "visible")

        empty_state = self.query_one("#empty-state", Static)
        empty_state.set_class(not records, "visible")
        empty_state.update(
            "No slot files were found. Press r to scan again."
            if not records
            else ""
        )

        if not records:
            save_list.index = None
            self._show_record(None)
            return

        selected_index = 0
        if selected_id is not None:
            selected_index = next(
                (
                    index
                    for index, record in enumerate(records)
                    if record.save_id == selected_id
                ),
                0,
            )
        save_list.index = selected_index
        self._show_record(records[selected_index])

    def _show_record(self, record: Optional[editor.SaveRecord]) -> None:
        self.selected_record = record
        detail = self.query_one("#save-details", Static)
        edit_button = self.query_one("#edit-value", Button)
        inventory_button = self.query_one("#inventory-action", Button)
        npc_button = self.query_one("#npc-action", Button)
        quest_button = self.query_one("#quest-action", Button)
        if record is None:
            detail.update("No save selected.")
            edit_button.disabled = True
            edit_button.remove_class("visible")
            inventory_button.disabled = True
            inventory_button.remove_class("visible")
            npc_button.disabled = True
            npc_button.remove_class("visible")
            quest_button.disabled = True
            quest_button.remove_class("visible")
            return

        try:
            cash_text = str(record.current_cash())
        except (OSError, editor.SaveFormatError) as exc:
            cash_text = f"Unavailable: {exc}"

        try:
            world_time = record.current_world_time()
            world_time_lines = [
                f"Day: {world_time.day}",
                f"Season: {world_time.season}",
            ]
        except (OSError, editor.SaveFormatError) as exc:
            world_time_lines = [f"Day/season unavailable: {exc}"]

        try:
            parameters = editor.read_character_parameters(
                record.character_path
            )
            parameter_lines = _format_character_parameters(parameters)
        except (OSError, editor.SaveFormatError) as exc:
            parameter_lines = ["", f"Parameters unavailable: {exc}"]

        try:
            npc_data = self.npc_parser(record.character_path.read_bytes())
            npc_lines = [
                "",
                "NPC reputation records: "
                f"{len(npc_data.reputation.records)}",
                f"NPC flags: {len(npc_data.flags.records)}",
            ]
            npc_available = True
        except (
            OSError,
            npc_editor.NpcFormatError,
            reputation_editor.ReputationResearchError,
        ) as exc:
            npc_lines = ["", f"NPC data unavailable: {exc}"]
            npc_available = False

        detail.update(
            "\n".join(
                [
                    f"Name: {record.display_name}",
                    f"Slot: {record.slot_name}",
                    f"Saved: {record.saved_at}",
                    f"Account: {record.account_id}",
                    f"Cash: {cash_text}",
                    *world_time_lines,
                    f"Save UUID: {record.save_id}",
                    "",
                    f"Slot file: {record.slot_path}",
                    f"Character file: {record.character_path}",
                    f"World file: {record.world_path}",
                    *parameter_lines,
                    *npc_lines,
                ]
            )
        )
        edit_button.disabled = not _editable_values(record)
        edit_button.add_class("visible")
        inventory_button.disabled = False
        inventory_button.add_class("visible")
        npc_button.disabled = not npc_available
        npc_button.add_class("visible")
        quest_button.disabled = not npc_available
        quest_button.add_class("visible")

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id != "save-list":
            return
        item = event.item
        self._show_record(item.record if isinstance(item, SaveListItem) else None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "save-list":
            item = event.item
            self._show_record(
                item.record if isinstance(item, SaveListItem) else None
            )

    def _main_screen_active(self) -> bool:
        return self.screen.id == "_default"

    def _enabled_action_buttons(self) -> list[Button]:
        buttons: list[Button] = []
        for button_id in self.ACTION_BUTTON_IDS:
            button = self.query_one(f"#{button_id}", Button)
            if button.has_class("visible") and not button.disabled:
                buttons.append(button)
        return buttons

    def _focus_next_action_button(self) -> bool:
        if not self._main_screen_active():
            return False
        buttons = self._enabled_action_buttons()
        if not buttons:
            return False
        focused = self.screen.focused
        if focused not in buttons:
            buttons[0].focus()
            return True
        index = buttons.index(focused)
        if index < len(buttons) - 1:
            buttons[index + 1].focus()
        return True

    def _focus_previous_action_button(self) -> bool:
        if not self._main_screen_active():
            return False
        buttons = self._enabled_action_buttons()
        focused = self.screen.focused
        save_list = self.query_one("#save-list", ListView)
        if focused in buttons:
            index = buttons.index(focused)
            if index == 0:
                save_list.focus()
            else:
                buttons[index - 1].focus()
            return True
        if focused is not save_list:
            save_list.focus()
            return True
        return False

    def on_key(self, event: events.Key) -> None:
        if event.key == "right" and self._focus_next_action_button():
            event.stop()
        elif event.key == "left" and self._focus_previous_action_button():
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "edit-value":
            self.action_edit_selected()
        elif event.button.id == "inventory-action":
            self.action_inventory()
        elif event.button.id == "npc-action":
            self.action_npcs()
        elif event.button.id == "quest-action":
            self.action_quests()

    def _inventory_exception_types(self) -> tuple[type[Exception], ...]:
        return (
            OSError,
            editor.SaveFormatError,
            inventory_editor.CatalogError,
            inventory_editor.InventoryFormatError,
            inventory_editor.InventoryMutationError,
            ValueError,
        )

    def _inventory_action_choices(
        self,
        snapshot: inventory_editor.InventorySnapshot,
    ) -> list[InventoryActionChoice]:
        choices: list[InventoryActionChoice] = []
        for slot_index, slot in enumerate(snapshot.bag_slots):
            if slot.item is not None:
                try:
                    change = inventory_editor.stage_bag_transfer(
                        snapshot,
                        slot_index,
                    )
                except inventory_editor.InventoryMutationError:
                    continue
                choices.append(
                    InventoryActionChoice(
                        label=(
                            f"Unequip {slot.item.definition.name} from "
                            f"bag slot {slot_index + 1}"
                        ),
                        description=(
                            "Move the equipped bag into carried inventory."
                        ),
                        change=change,
                        source_sha256=snapshot.source_sha256,
                    )
                )
                continue

            for item in snapshot.items:
                if item.serialize_type != 2:
                    continue
                try:
                    change = inventory_editor.stage_bag_transfer(
                        snapshot,
                        slot_index,
                        item.offset,
                    )
                except inventory_editor.InventoryMutationError:
                    continue
                choices.append(
                    InventoryActionChoice(
                        label=(
                            f"Equip {item.definition.name} into "
                            f"bag slot {slot_index + 1}"
                        ),
                        description=(
                            "Move one carried bag into the empty saved-bag "
                            "slot."
                        ),
                        change=change,
                        source_sha256=snapshot.source_sha256,
                    )
                )
        return choices

    def action_quit_app(self) -> None:
        self.exit(0)

    async def action_refresh_saves(self) -> None:
        await self.refresh_saves(preserve_selection=True)

    def _npc_annotations_for(
        self,
        flag_keys: tuple[str, ...],
    ) -> npc_editor.NpcFlagAnnotationCatalog:
        if (
            self.npc_annotations is None
            or self.npc_annotation_keys != flag_keys
            or self.npc_annotation_game_dir != self.game_dir
        ):
            try:
                if self.game_dir is None:
                    raise npc_editor.NpcAnnotationError(
                        "game installation is not selected"
                    )
                self.npc_annotations = self.npc_annotation_loader(
                    self.game_dir,
                    flag_keys,
                )
            except (
                OSError,
                inventory_editor.CatalogError,
                npc_editor.NpcAnnotationError,
                ValueError,
            ) as exc:
                self.npc_annotations = (
                    npc_editor.fallback_npc_flag_annotations(
                        flag_keys,
                        warning=(
                            "Installed English context could not be loaded: "
                            f"{exc}"
                        ),
                    )
                )
            self.npc_annotation_keys = flag_keys
            self.npc_annotation_game_dir = self.game_dir
        return self.npc_annotations

    def action_npcs(self) -> None:
        record = self.selected_record
        if record is None:
            return
        try:
            data = record.character_path.read_bytes()
            npc_data = self.npc_parser(data)
        except (
            OSError,
            npc_editor.NpcFormatError,
            reputation_editor.ReputationResearchError,
        ) as exc:
            self.push_screen(
                MessageModal("NPC data unavailable", str(exc)),
            )
            return

        flag_keys = tuple(item.key for item in npc_data.flags.records)
        annotations = self._npc_annotations_for(flag_keys)

        self.push_screen(
            NpcScreen(
                record,
                npc_data,
                annotations,
                hashlib.sha256(data).hexdigest(),
                backup_dir=self.backup_dir,
                writer=self.npc_writer,
                on_success=self._npc_edit_succeeded,
            ),
        )

    def action_quests(self) -> None:
        record = self.selected_record
        if record is None:
            return
        try:
            data = record.character_path.read_bytes()
            npc_data = self.npc_parser(data)
        except (
            OSError,
            npc_editor.NpcFormatError,
            reputation_editor.ReputationResearchError,
        ) as exc:
            self.push_screen(
                MessageModal("NPC data unavailable", str(exc)),
            )
            return
        if self.game_dir is None:
            self.push_screen(
                MessageModal(
                    "Quest catalog unavailable",
                    "Game installation is not selected.",
                )
            )
            return

        if (
            self.quest_catalog is None
            or self.quest_catalog_game_dir != self.game_dir
        ):
            try:
                self.quest_catalog = self.quest_catalog_loader(self.game_dir)
            except (
                OSError,
                inventory_editor.CatalogError,
                quest_editor.QuestCatalogError,
                ValueError,
            ) as exc:
                self.push_screen(
                    MessageModal("Quest catalog unavailable", str(exc)),
                )
                return
            self.quest_catalog_game_dir = self.game_dir

        flag_keys = tuple(item.key for item in npc_data.flags.records)
        annotations = self._npc_annotations_for(flag_keys)
        self.push_screen(
            QuestScreen(
                record,
                npc_data,
                self.quest_catalog,
                annotations,
                hashlib.sha256(data).hexdigest(),
                backup_dir=self.backup_dir,
                writer=self.npc_writer,
                on_success=self._npc_edit_succeeded,
            )
        )

    async def _npc_edit_succeeded(
        self,
        backup_path: Path,
        summary: str,
    ) -> None:
        self.last_backup_path = backup_path
        result = self.query_one("#result", Static)
        result.update(f"NPC data updated. Backup: {backup_path}\n{summary}")
        result.add_class("visible")
        await self.refresh_saves(preserve_selection=True)

    def action_inventory(self) -> None:
        record = self.selected_record
        if record is None or self.game_dir is None:
            return
        try:
            catalog = self.inventory_catalog_loader(self.game_dir)
            data = record.character_path.read_bytes()
            snapshot = inventory_editor.parse_inventory(data, catalog)
            equipment = inventory_editor.parse_equipment(
                data,
                catalog,
                snapshot,
            )
        except self._inventory_exception_types() as exc:
            self.push_screen(
                MessageModal("Inventory unavailable", str(exc)),
            )
            return

        self.push_screen(
            InventoryScreen(
                record,
                catalog,
                equipment,
                snapshot,
                backup_dir=self.backup_dir,
                writer=self.full_inventory_writer,
                on_success=self._inventory_edit_succeeded,
            ),
        )

    async def _inventory_edit_succeeded(
        self,
        backup_path: Path,
        summary: str,
    ) -> None:
        self.last_backup_path = backup_path
        result = self.query_one("#result", Static)
        result.update(f"Inventory updated. Backup: {backup_path}\n{summary}")
        result.add_class("visible")
        await self.refresh_saves(preserve_selection=True)

    def _inventory_action_selected(
        self,
        record: editor.SaveRecord,
        catalog: inventory_editor.ItemCatalog,
        choice: Optional[InventoryActionChoice],
    ) -> None:
        if choice is None:
            return
        self.push_screen(
            InventoryConfirmationModal(choice),
            lambda confirmed: self._inventory_action_confirmed(
                record,
                catalog,
                choice,
                bool(confirmed),
            ),
        )

    async def _inventory_action_confirmed(
        self,
        record: editor.SaveRecord,
        catalog: inventory_editor.ItemCatalog,
        choice: InventoryActionChoice,
        confirmed: bool,
    ) -> None:
        if not confirmed:
            return
        try:
            backup_path = self.bag_transfer_writer(
                record.character_path,
                catalog,
                choice.change,
                expected_source_sha256=choice.source_sha256,
                backup_dir=self.backup_dir,
            )
        except self._inventory_exception_types() as exc:
            self.push_screen(
                MessageModal("Inventory edit failed", str(exc)),
            )
            return

        self.last_backup_path = backup_path
        result = self.query_one("#result", Static)
        result.update(f"{choice.label}. Backup: {backup_path}")
        result.add_class("visible")
        self.selected_record = record
        await self.refresh_saves(preserve_selection=True)

    def action_edit_selected(self) -> None:
        record = self.selected_record
        if record is None:
            return
        targets = _editable_values(record)
        if not targets:
            self.push_screen(
                MessageModal(
                    "Cannot edit save",
                    "No safely editable values were found.",
                ),
            )
            return
        self.push_screen(
            EditValueModal(targets),
            lambda target: self._edit_target_selected(
                record,
                target,
            ),
        )

    def _edit_target_selected(
        self,
        record: editor.SaveRecord,
        target: Optional[EditableValue],
    ) -> None:
        if target is None:
            return
        self.push_screen(
            IntegerEditModal(target),
            lambda new_value: self._edit_value_entered(
                record,
                target,
                new_value,
            ),
        )

    def _edit_value_entered(
        self,
        record: editor.SaveRecord,
        target: EditableValue,
        new_value: Optional[int],
    ) -> None:
        if new_value is None:
            return
        self.push_screen(
            ConfirmationModal(
                target.label,
                target.current_value,
                new_value,
            ),
            lambda confirmed: self._value_change_confirmed(
                record,
                target,
                new_value,
                bool(confirmed),
            ),
        )

    async def _value_change_confirmed(
        self,
        record: editor.SaveRecord,
        target: EditableValue,
        new_value: int,
        confirmed: bool,
    ) -> None:
        if not confirmed:
            return
        try:
            if target.is_cash:
                old_value, backup_path = self.cash_writer(
                    record.character_path,
                    new_value,
                    backup_dir=self.backup_dir,
                )
            elif target.is_primary_parameter:
                assert target.parameter_type is not None
                old_value, backup_path = self.primary_writer(
                    record.character_path,
                    target.parameter_type,
                    new_value,
                    backup_dir=self.backup_dir,
                )
            else:
                assert target.world_time_field is not None
                old_value, backup_path = self.world_time_writer(
                    record.world_path,
                    target.world_time_field,
                    new_value,
                    backup_dir=self.backup_dir,
                )
        except (OSError, editor.SaveFormatError, ValueError) as exc:
            self.push_screen(
                MessageModal("Edit failed", str(exc)),
            )
            return

        self.last_backup_path = backup_path
        result = self.query_one("#result", Static)
        result.update(
            f"{target.label} changed from {old_value} to {new_value}. "
            f"Backup: {backup_path}"
        )
        result.add_class("visible")
        self.selected_record = record
        await self.refresh_saves(preserve_selection=True)


def run_app(
    game_dir: Optional[Path] = None,
    backup_dir: Optional[Path] = None,
) -> int:
    """Run the full-screen editor and return its process exit code."""
    result = HoboSaveEditorApp(
        game_dir=game_dir,
        backup_dir=backup_dir,
    ).run()
    return 0 if result is None else result
