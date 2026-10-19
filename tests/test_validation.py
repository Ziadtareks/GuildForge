"""JSON loading, schema versioning, and cross-reference validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from guildforge.errors import BackupError
from guildforge.models.schema import SCHEMA_VERSION
from guildforge.models.validation import load_backup, parse_backup


def test_sample_backup_loads(sample_backup_path: Path) -> None:
    result = load_backup(sample_backup_path)
    document = result.document
    assert document.schema_version == SCHEMA_VERSION
    assert document.guild.name == "GuildForge Demo"
    assert document.everyone_role() is not None
    assert document.everyone_role().id == document.guild.id
    assert len(document.categories()) == 2
    assert len(document.children_of(100000000000000014)) == 5
    assert document.roster_summary()["roles"] == 3


def test_missing_file_is_friendly(tmp_path: Path) -> None:
    with pytest.raises(BackupError) as excinfo:
        load_backup(tmp_path / "nope.json")
    assert "not found" in excinfo.value.message.lower()
    assert excinfo.value.hint


def test_bad_json_is_friendly(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text('{"schema_version": 1, ', encoding="utf-8")
    with pytest.raises(BackupError) as excinfo:
        load_backup(broken)
    assert "valid JSON" in excinfo.value.message


def test_future_schema_version_is_rejected(sample_backup_path: Path) -> None:
    data = json.loads(sample_backup_path.read_text(encoding="utf-8"))
    data["schema_version"] = 99
    with pytest.raises(BackupError) as excinfo:
        parse_backup(data)
    assert "schema_version" in excinfo.value.message


def test_duplicate_role_ids_are_rejected(sample_backup_path: Path) -> None:
    data = json.loads(sample_backup_path.read_text(encoding="utf-8"))
    data["roles"].append(dict(data["roles"][1]))
    with pytest.raises(BackupError) as excinfo:
        parse_backup(data)
    assert "duplicate role IDs" in excinfo.value.message


def test_missing_parent_category_is_rejected(sample_backup_path: Path) -> None:
    data = json.loads(sample_backup_path.read_text(encoding="utf-8"))
    for channel in data["channels"]:
        if channel["name"] == "general":
            channel["parent_id"] = 999999999999999999
    with pytest.raises(BackupError) as excinfo:
        parse_backup(data)
    assert "missing parent category" in excinfo.value.message


def test_unknown_overwrite_role_warns(sample_backup_path: Path) -> None:
    data = json.loads(sample_backup_path.read_text(encoding="utf-8"))
    for channel in data["channels"]:
        if channel["name"] == "general":
            channel["permission_overwrites"].append(
                {"target_id": 777777777777777777, "target_type": "role", "allow": 0, "deny": 8}
            )
    result = parse_backup(data)
    assert any("unknown role" in warning for warning in result.warnings)


def test_extra_fields_are_rejected(sample_backup_path: Path) -> None:
    data = json.loads(sample_backup_path.read_text(encoding="utf-8"))
    data["surprise"] = True
    with pytest.raises(BackupError) as excinfo:
        parse_backup(data)
    assert "validation failed" in excinfo.value.message.lower()
