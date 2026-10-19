"""Discord hard-limit checks."""

from __future__ import annotations

import json
from pathlib import Path

from guildforge.models.limits import (
    TargetSnapshot,
    check_limits,
    count_channels_to_create,
    count_roles_to_create,
    hard_errors,
)
from guildforge.models.schema import EmojiModel
from guildforge.models.validation import load_backup, parse_backup


def _document(sample_backup_path: Path):
    return load_backup(sample_backup_path).document


def test_planned_counts_match_sample(sample_backup_path: Path) -> None:
    document = _document(sample_backup_path)
    assert count_roles_to_create(document) == 3
    assert count_channels_to_create(document) == 10


def test_role_limit_is_a_hard_error(sample_backup_path: Path) -> None:
    document = _document(sample_backup_path)
    target = TargetSnapshot(roles=250, channels=0)
    issues = check_limits(document, target)
    errors = hard_errors(issues)
    assert errors
    assert "Role limit exceeded" in errors[0]


def test_channel_limit_is_a_hard_error(sample_backup_path: Path) -> None:
    document = _document(sample_backup_path)
    target = TargetSnapshot(roles=0, channels=495)
    errors = hard_errors(check_limits(document, target))
    assert any("Channel limit exceeded" in message for message in errors)


def test_per_category_limit_is_a_hard_error(sample_backup_path: Path) -> None:
    data = json.loads(sample_backup_path.read_text(encoding="utf-8"))
    template = next(channel for channel in data["channels"] if channel["name"] == "general")
    for index in range(60):
        clone = json.loads(json.dumps(template))
        clone["id"] = 200000000000000000 + index
        clone["name"] = f"general-{index}"
        data["channels"].append(clone)
    document = parse_backup(data).document
    errors = hard_errors(check_limits(document, TargetSnapshot()))
    assert any("maximum is 50" in message for message in errors)


def test_clearing_target_removes_count_pressure(sample_backup_path: Path) -> None:
    document = _document(sample_backup_path)
    target = TargetSnapshot(roles=250, clearable_roles=249, channels=500)
    errors = hard_errors(check_limits(document, target, clearing=True))
    assert errors == []


def test_emoji_slot_warning_and_community_fallback(sample_backup_path: Path) -> None:
    document = _document(sample_backup_path)
    document.emojis.append(EmojiModel(id=1, name="fake", image="aGk="))
    target = TargetSnapshot(
        roles=0,
        channels=0,
        emojis=50,
        boost_level=0,
        features=set(),
    )
    issues = check_limits(document, target, include_emojis=True)
    messages = [issue.message for issue in issues]
    assert any("Emoji slots" in message for message in messages)
    assert any("Community is not enabled" in message for message in messages)


def test_banner_warning_when_boost_too_low(sample_backup_path: Path) -> None:
    document = _document(sample_backup_path)
    document.guild.banner = "aGk="
    result = check_limits(document, TargetSnapshot(features=set(), boost_level=0))
    assert any("banner" in issue.message.lower() for issue in result)
