# Hobo RPG Save Editor (Experimental)

> [!CAUTION]
> **NOTICE: This project is AI-generated and should be considered experimental slop.**

A terminal UI for inspecting and performing targeted edits on *Hobo: Tough Life* saves. This tool is the result of AI-assisted reverse engineering and prioritizes basic save integrity through backups, though many features remain experimental or inferred from game data.

## Features

- **Terminal Interface**: A Textual-based UI for save file navigation.
- **Basic Attribute Editing**: Modify cash and primary character values (Health, Food, Morale, etc.).
- **Inventory Management (Experimental)**: Tools for managing carried items and stacks based on validated record patterns.
- **NPC Reputation**: Edit reputation values for known archetypes.
- **Progression Flags (Expert/Experimental)**: Access to boolean game state flags. Note that changing these may cause inconsistent quest progression.
- **Quest Inference**: Read-only display of quest status inferred from installed game data. **Not a canonical game state.**
- **Automatic Backups**: Creates a backup of the save file before attempting any write operation.

## Requirements

- **Python**: 3.9 or newer.
- **Game Status**: *Hobo: Tough Life* must be closed before reading or modifying a save.

## Installation

Choose the method that best fits your platform and technical comfort level.

### Option 1: Standalone Windows Executable (Recommended)
The easiest way to use the editor on Windows. No Python installation required.
1. Go to the **[Releases](https://github.com/sustainablelobster/hobo-rpg-save-editor/releases)** page on GitHub.
2. Download the latest `hobo-rpg-save-editor.exe`.
3. Run the `.exe` directly.

### Option 2: Python Package (Wheel)
Recommended for Linux and macOS users, or Windows users who prefer a Python-managed environment.
1. Download the `.whl` file from the latest release.
2. Install it via pip:
   ```sh
   pip install hobo_rpg_save_editor-0.1.0-py3-none-any.whl
   ```

### Option 3: Development Install (From Source)
For users who want to run the latest code or contribute to development.
```sh
# Clone the repository and enter the directory
python -m venv .venv
source .venv/bin/activate  # Or .venv\Scripts\Activate.ps1 on Windows
python -m pip install -e .
```

## Usage

### Running the Editor
- **Windows EXE**: Double-click `hobo-rpg-save-editor.exe`.
- **Pip/Source Install**: Run `hobo-rpg-save-editor` in your terminal.

The editor automatically scans standard Steam installation paths on Windows, macOS, and Linux.

### Configuration
If the editor cannot find your game or you want to use a specific backup directory, you can use the following command-line arguments (or run the EXE from a terminal):

- `--game-dir "/path/to/game"`: Manually specify the game installation path.
- `--backup-dir "/path/to/backups"`: Specify a custom backup directory.
- **Environment Variables**: You can also set `HOBOTOUGHLIFE_DIR`, `STEAM_DIR`, or `HOBOTOUGHLIFE_BACKUP_DIR`.

## Controls

- **Arrow Keys / Mouse**: Navigate saves.
- **Enter / e**: Edit primary values and cash.
- **i**: Inventory editor (Experimental).
- **p**: NPC & Reputation editor.
- **Quests**: View inferred quest discovery.
- **r**: Rescan saves.
- **q**: Quit or cancel.

## Safety & Validation

Writes are performed using a staged approach:
1. Create and verify an external backup.
2. Validate the source file hash and structure.
3. Perform an atomic replacement of the target file.
4. Verify the file was written as expected.

**Default Backup Paths:**
- **Windows**: `%LOCALAPPDATA%\Hobo Save Editor\Backups`
- **macOS**: `~/Library/Application Support/Hobo Save Editor/Backups`
- **Linux**: `~/.local/share/hobo-save-editor/backups`

## Development

Source code is in `src/`. Reverse-engineering notes and research tools are in `research/`.

```sh
python -m unittest discover -s tests -v
python -m build
```

### Building for Windows (EXE)

To generate a standalone Windows executable that minimizes antivirus false positives:

1. Install the build dependencies: `pip install -e ".[build]"`
2. Run the build script: `powershell ./scripts/build_windows.ps1`

The resulting `hobo-rpg-save-editor.exe` will be located in the `dist/` directory. This build uses **Nuitka** to compile the Python source into a C++ binary.

### Continuous Integration (GitHub Actions)

The repository includes workflows for:
- **Automated Testing**: Runs on every push/PR for Ubuntu and Windows.
- **Automated Releases**: Builds the Windows EXE plus Python Wheel/Sdist and publishes them to a GitHub Release when a new version tag (`v*`) is pushed.

## Disclaimer

This is an unofficial, AI-generated research project. It is not affiliated with the game developers. Modifying save files can lead to data loss or broken game progression. **Use at your own risk and always keep independent backups.**
