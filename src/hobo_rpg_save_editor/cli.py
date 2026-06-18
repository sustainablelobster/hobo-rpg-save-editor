"""Command-line entry point for the Textual save editor."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Sequence

from . import editor


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Edit Hobo: Tough Life save data in a terminal UI."
    )
    parser.add_argument(
        "--game-dir",
        type=Path,
        help="Use a specific Hobo Tough Life installation directory.",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help=(
            "Store backups in this directory instead of the platform default "
            f"or ${editor.BACKUP_DIR_ENV}."
        ),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_argument_parser().parse_args(argv)
    return editor.run_tui(
        game_dir=args.game_dir,
        backup_dir=editor.resolve_backup_dir(args.backup_dir),
    )


if __name__ == "__main__":
    raise SystemExit(main())
