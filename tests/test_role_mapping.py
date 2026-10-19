"""Role ordering and source-ID to target-ID mapping."""

from __future__ import annotations

from guildforge.discord_io.writer import (
    ApplyState,
    build_role_id_map,
    order_roles_for_creation,
)
from guildforge.models.schema import RoleModel


def _role(
    role_id: int, position: int, *, everyone: bool = False, managed: bool = False
) -> RoleModel:
    return RoleModel(
        id=role_id,
        name=f"role-{role_id}",
        position=position,
        is_everyone=everyone,
        managed=managed,
    )


def test_order_roles_lowest_first_and_skips_special_roles() -> None:
    roles = [
        _role(3, 3),
        _role(1, 0, everyone=True),
        _role(2, 2, managed=True),
        _role(4, 1),
    ]
    ordered = order_roles_for_creation(roles)
    assert [role.id for role in ordered] == [4, 3]


def test_build_role_id_map_includes_everyone_and_created_roles() -> None:
    roles = [_role(10, 0, everyone=True), _role(11, 1), _role(12, 2, managed=True)]
    mapping = build_role_id_map(
        roles,
        everyone_source_id=10,
        everyone_target_id=500,
        created={11: 501},
    )
    assert mapping[10] == 500
    assert mapping[11] == 501
    assert 12 not in mapping


def test_state_roundtrip_and_mismatch(tmp_path) -> None:
    path = tmp_path / ".guildforge-state.json"
    state = ApplyState.new(42, "digest-abc")
    state.role_map[1] = 2
    state.channel_map[3] = 4
    state.created.append("role @Admin")
    state.save(path)

    loaded = ApplyState.load(path, target_guild_id=42, digest="digest-abc")
    assert loaded is not None
    assert loaded.role_map == {1: 2}
    assert loaded.channel_map == {3: 4}
    assert loaded.created == ["role @Admin"]

    assert ApplyState.load(path, target_guild_id=99, digest="digest-abc") is None
    assert ApplyState.load(path, target_guild_id=42, digest="other") is None


def test_state_load_tolerates_corrupt_file(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    assert ApplyState.load(path, target_guild_id=1, digest="x") is None
