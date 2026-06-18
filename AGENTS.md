# Repository Guidelines

## Project Structure & Module Organization

This is a Python package with a Textual full-screen UI:

- `src/hobo_rpg_save_editor/editor.py`: save discovery, slot parsing,
  character value editing, backups, and shared write safety helpers.
- `src/hobo_rpg_save_editor/inventory.py`: Unity item-catalog extraction,
  inventory framing, equipment, saved-bag slots, and inventory mutations.
- `src/hobo_rpg_save_editor/reputation.py`: reputation parsing and validated
  reputation writes.
- `src/hobo_rpg_save_editor/npc.py`: NPC reputation plus boolean
  NPC/quest-flag parsing, annotations, and mutation helpers.
- `src/hobo_rpg_save_editor/quests.py`: read-only quest catalog extraction
  and save-flag correlation.
- `src/hobo_rpg_save_editor/tui.py`: Textual screens, modals, responsive
  layout, and UI actions.
- `src/hobo_rpg_save_editor/cli.py`: packaged TUI command entry point.
- `tests/`: `unittest` coverage for the packaged application modules.
- `research/`: development-only reverse-engineering scripts and notes; these
  are not installed as release console commands.
- `pyproject.toml`: Hatchling package metadata and the
  `hobo-rpg-save-editor` console entry point.

Do not commit `__pycache__/`, `*.pyc`, `build/`, `dist/`, or `*.egg-info/`.

## Build, Test, and Development Commands

Python 3.9 or newer is required. The project uses Hatchling through
`pyproject.toml` and Textual as its runtime UI dependency.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
hobo-rpg-save-editor
python -m unittest discover -s tests -v
python -m py_compile src/hobo_rpg_save_editor/*.py tests/*.py
python -m build
```

The editable install exposes the `hobo-rpg-save-editor` console command.

## Coding Style & Naming Conventions

Follow PEP 8 with four-space indentation and keep compatibility with
Python 3.9. Use type hints on public and nontrivial internal functions. Name
functions and variables with `snake_case`, classes with `PascalCase`, and
constants with `UPPER_SNAKE_CASE`. Prefix module-private helpers with `_`.
Prefer package-relative imports inside `src/hobo_rpg_save_editor/`.

## Testing Guidelines

Tests use the standard-library `unittest` framework and temporary
directories; no real game installation should be required. Name test classes
by behavior, such as `CashEditingTests`, and methods
`test_<expected_behavior>`. For editing changes, assert that only intended
bytes change and the original is recoverable.

## Save-File Safety

Never weaken the requirements to close the game, default timestamped backups
outside the game directory, write atomically, and verify the result. Backup
failures must leave the save unchanged. Keep documented custom backup paths
outside game data, and avoid tests or examples that modify real saves.
