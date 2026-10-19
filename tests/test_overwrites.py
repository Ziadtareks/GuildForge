"""Permission overwrite translation between source and target IDs."""

from __future__ import annotations

from guildforge.discord_io.writer import map_overwrites
from guildforge.models.schema import PermissionOverwriteModel


def _overwrite(target_id: int, allow: int, deny: int) -> PermissionOverwriteModel:
    return PermissionOverwriteModel(target_id=target_id, allow=allow, deny=deny)


def test_overwrites_are_remapped_through_role_map() -> None:
    mapped, missing = map_overwrites(
        [_overwrite(10, 1024, 0), _overwrite(11, 0, 2048)],
        {10: 500, 11: 501},
    )
    assert missing == []
    assert [(item.target_id, item.allow, item.deny) for item in mapped] == [
        (500, 1024, 0),
        (501, 0, 2048),
    ]


def test_missing_roles_are_reported_not_crashed() -> None:
    mapped, missing = map_overwrites(
        [_overwrite(10, 1, 0), _overwrite(999, 0, 1)],
        {10: 500},
    )
    assert [item.target_id for item in mapped] == [500]
    assert missing == [999]


def test_empty_overwrites_are_allowed() -> None:
    mapped, missing = map_overwrites([], {1: 2})
    assert mapped == []
    assert missing == []
