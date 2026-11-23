from __future__ import annotations

import sqlite3

import pytest
from app.backup import create_backup, restore_backup
from app.fixtures import demo_cases
from app.store import LocalStore


def test_backup_and_restore_keep_a_consistent_state_snapshot(tmp_path) -> None:
    source = tmp_path / "state.sqlite3"
    backup = tmp_path / "backups" / "daily.sqlite3"
    backup.parent.mkdir()
    store = LocalStore(source)
    case = demo_cases()[0]
    store.save_case(case)

    assert create_backup(source, backup) == backup
    restored_path, safety_copy = restore_backup(backup, tmp_path / "restored.sqlite3")

    assert safety_copy is None
    restored = LocalStore(restored_path)
    assert restored.load_cases()[case.id] == case
    with sqlite3.connect(restored_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_backup_refuses_overwrite_and_same_file(tmp_path) -> None:
    source = tmp_path / "state.sqlite3"
    LocalStore(source)

    with pytest.raises(ValueError, match="must differ"):
        create_backup(source, source)
    backup = create_backup(source, tmp_path / "backup.sqlite3")
    with pytest.raises(FileExistsError, match="already exists"):
        create_backup(source, backup)


def test_restore_requires_explicit_replace_and_preserves_a_safety_copy(tmp_path) -> None:
    source = tmp_path / "backup.sqlite3"
    target = tmp_path / "state.sqlite3"
    source_store = LocalStore(source)
    case = demo_cases()[0]
    source_store.save_case(case)
    target_store = LocalStore(target)
    other_case = demo_cases()[1]
    target_store.save_case(other_case)

    with pytest.raises(FileExistsError, match="--replace"):
        restore_backup(source, target)

    restored_path, safety_copy = restore_backup(source, target, replace=True)

    assert restored_path == target
    assert safety_copy is not None and safety_copy.is_file()
    assert LocalStore(restored_path).load_cases() == {case.id: case}
    assert LocalStore(safety_copy).load_cases() == {other_case.id: other_case}


def test_corrupt_backup_does_not_replace_an_existing_target(tmp_path) -> None:
    source = tmp_path / "corrupt.sqlite3"
    target = tmp_path / "state.sqlite3"
    source.write_bytes(b"not a sqlite database")
    target_store = LocalStore(target)
    case = demo_cases()[0]
    target_store.save_case(case)

    with pytest.raises(sqlite3.DatabaseError):
        restore_backup(source, target, replace=True)

    assert LocalStore(target).load_cases() == {case.id: case}

