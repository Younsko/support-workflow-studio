"""Offline, atomic backup and restore helpers for the local SQLite runner."""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .store import configured_store_path


def _resolved_source(path: Path) -> Path:
    source = path.expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("The SQLite source must be a regular file.")
    return source


def _resolved_destination(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ValueError("Refusing to write through a destination symlink.")
    destination = expanded.resolve(strict=False)
    if not destination.parent.is_dir():
        raise ValueError("The destination directory must already exist.")
    return destination


def _copy_and_verify(source: Path, destination: Path) -> None:
    source_uri = source.as_uri() + "?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True, timeout=30)) as source_db, closing(
        sqlite3.connect(destination, timeout=30)
    ) as destination_db:
        source_db.backup(destination_db, pages=256, sleep=0.05)
        destination_db.commit()
        result = destination_db.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise sqlite3.DatabaseError("The SQLite copy failed its integrity check.")
        destination_db.execute("PRAGMA journal_mode=DELETE")
        destination_db.commit()


def _stage_verified_copy(source: Path, destination: Path) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".staging", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        _copy_and_verify(source, temporary)
        if os.name != "nt":
            temporary.chmod(0o600)
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _publish_without_replacement(temporary: Path, destination: Path) -> None:
    if os.name == "nt":
        # Windows rename is atomic on one volume and refuses an existing target.
        os.rename(temporary, destination)
    else:
        # A same-directory hard-link publishes atomically and never clobbers.
        os.link(temporary, destination)


def create_backup(source: Path, destination: Path) -> Path:
    """Create a consistent, verified backup without overwriting an existing file."""
    resolved_source = _resolved_source(source)
    resolved_destination = _resolved_destination(destination)
    if resolved_source == resolved_destination:
        raise ValueError("The backup destination must differ from the source database.")
    if resolved_destination.exists():
        raise FileExistsError("Backup already exists; choose a new destination path.")

    temporary = _stage_verified_copy(resolved_source, resolved_destination)
    try:
        _publish_without_replacement(temporary, resolved_destination)
        return resolved_destination
    finally:
        temporary.unlink(missing_ok=True)


def restore_backup(
    source: Path, destination: Path, *, replace: bool = False
) -> tuple[Path, Path | None]:
    """Restore a verified snapshot; replacement keeps a safety copy first.

    Stop the application before restoring its live database. ``replace=False``
    only restores into a path that does not exist. Replacing an existing target
    creates a timestamped safety backup before the atomic file swap.
    """
    resolved_source = _resolved_source(source)
    resolved_destination = _resolved_destination(destination)
    if resolved_source == resolved_destination:
        raise ValueError("The restore source and destination must differ.")
    target_exists = resolved_destination.exists()
    if target_exists and not replace:
        raise FileExistsError("Restore target exists; pass --replace to replace it safely.")

    temporary = _stage_verified_copy(resolved_source, resolved_destination)
    safety_backup: Path | None = None
    try:
        if target_exists:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            safety_backup = resolved_destination.with_name(
                f"{resolved_destination.name}.pre-restore-{stamp}-{uuid4().hex[:8]}"
            )
            create_backup(resolved_destination, safety_backup)
            os.replace(temporary, resolved_destination)
        else:
            _publish_without_replacement(temporary, resolved_destination)
        return resolved_destination, safety_backup
    finally:
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.backup",
        description="Create or restore a verified local SQLite state snapshot.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Create a new backup without overwriting files.")
    create.add_argument("destination", type=Path)
    create.add_argument("--source", type=Path, default=configured_store_path())
    restore = commands.add_parser("restore", help="Restore a backup while the app is stopped.")
    restore.add_argument("source", type=Path)
    restore.add_argument("--destination", type=Path, default=configured_store_path())
    restore.add_argument("--replace", action="store_true", help="Replace an existing database after creating a safety backup.")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "create":
            result = create_backup(args.source, args.destination)
            print(f"Verified backup created: {result}")
        else:
            result, safety_backup = restore_backup(
                args.source, args.destination, replace=args.replace
            )
            print(f"Verified backup restored: {result}")
            if safety_backup is not None:
                print(f"Pre-restore database saved: {safety_backup}")
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise SystemExit(f"Backup operation failed: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
