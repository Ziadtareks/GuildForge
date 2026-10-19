"""Backup loading and structural validation with friendly error messages."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from guildforge.errors import BackupError
from guildforge.models.schema import (
    SCHEMA_VERSION,
    BackupDocument,
    ChannelKind,
    ChannelModel,
)


@dataclass
class ValidationResult:
    document: BackupDocument
    warnings: list[str] = field(default_factory=list)


def _format_pydantic_error(exc: ValidationError) -> str:
    details: list[str] = []
    for error in exc.errors()[:8]:
        location = ".".join(str(part) for part in error.get("loc", ()))
        message = error.get("msg", "invalid value")
        details.append(f"  - {location or '<root>'}: {message}")
    if len(exc.errors()) > 8:
        details.append(f"  - ... and {len(exc.errors()) - 8} more errors")
    return "Backup schema validation failed:\n" + "\n".join(details)


def parse_backup(data: Any, *, origin: str = "backup") -> ValidationResult:
    """Validate an already-decoded JSON object and return the document."""

    if not isinstance(data, dict):
        raise BackupError(
            f"{origin} is not a GuildForge backup object.",
            hint="The top-level JSON value must be an object with a schema_version field.",
        )

    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise BackupError(
            f"Unsupported backup schema_version {version!r} in {origin}.",
            hint=(
                f"This build of GuildForge understands schema_version {SCHEMA_VERSION}. "
                "Re-export the server with the same version."
            ),
        )

    try:
        document = BackupDocument.model_validate(data)
    except ValidationError as exc:
        raise BackupError(_format_pydantic_error(exc)) from exc

    warnings = validate_document(document)
    return ValidationResult(document=document, warnings=warnings)


def load_backup(path: Path) -> ValidationResult:
    """Read and validate a backup JSON file."""

    path = Path(path)
    if not path.exists():
        raise BackupError(
            f"Backup file not found: {path}",
            hint="Check the path, or create a backup first with: guildforge export --source <id>",
        )
    if not path.is_file():
        raise BackupError(f"Backup path is not a file: {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BackupError(f"Could not read backup file {path}: {exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BackupError(
            f"Backup file is not valid JSON: {path} (line {exc.lineno}, column {exc.colno})",
            hint="Use a file exported by GuildForge and do not edit it by hand.",
        ) from exc

    return parse_backup(data, origin=str(path))


def validate_document(document: BackupDocument) -> list[str]:
    """Check cross-references and Discord constraints that Pydantic does not cover."""

    warnings: list[str] = []
    role_ids = [role.id for role in document.roles]
    channel_ids = [channel.id for channel in document.channels]

    if len(set(role_ids)) != len(role_ids):
        raise BackupError("Backup contains duplicate role IDs.")
    if len(set(channel_ids)) != len(channel_ids):
        raise BackupError("Backup contains duplicate channel IDs.")
    if len({emoji.id for emoji in document.emojis}) != len(document.emojis):
        raise BackupError("Backup contains duplicate emoji IDs.")

    everyone_roles = [role for role in document.roles if role.is_everyone]
    if not everyone_roles:
        warnings.append(
            "@everyone role is missing from the backup; target permissions for @everyone "
            "will be left unchanged."
        )
    elif len(everyone_roles) > 1:
        raise BackupError("Backup contains more than one @everyone role.")

    categories = {
        channel.id: channel for channel in document.channels if channel.kind is ChannelKind.category
    }
    role_id_set = set(role_ids)
    category_children: dict[int, int] = {}

    for channel in document.channels:
        if channel.kind is ChannelKind.category and channel.parent_id is not None:
            raise BackupError(f"Category '{channel.name}' cannot have a parent category.")
        if channel.parent_id is not None:
            parent = next(
                (item for item in document.channels if item.id == channel.parent_id),
                None,
            )
            if parent is None:
                raise BackupError(
                    f"Channel '{channel.name}' references missing parent category "
                    f"{channel.parent_id}."
                )
            if parent.kind is not ChannelKind.category:
                raise BackupError(
                    f"Channel '{channel.name}' is parented to '{parent.name}', "
                    "which is not a category."
                )
            category_children[parent.id] = category_children.get(parent.id, 0) + 1

        for overwrite in channel.permission_overwrites:
            if overwrite.target_id not in role_id_set:
                warnings.append(
                    f"Channel '{channel.name}' has an overwrite for unknown role "
                    f"{overwrite.target_id}; it will be skipped."
                )
        if channel.kind in {ChannelKind.forum, ChannelKind.media}:
            if len(channel.available_tags) > 20:
                warnings.append(
                    f"Forum '{channel.name}' has {len(channel.available_tags)} tags; "
                    "Discord allows 20, so extras are truncated."
                )
            seen_tags: set[str] = set()
            for tag in channel.available_tags:
                lowered = tag.name.casefold()
                if lowered in seen_tags:
                    warnings.append(
                        f"Forum '{channel.name}' has duplicate tag '{tag.name}'; "
                        "the duplicate is skipped."
                    )
                seen_tags.add(lowered)

    for category_id, child_count in category_children.items():
        if child_count > 50:
            warnings.append(
                f"Category '{categories[category_id].name}' has {child_count} channels; "
                "Discord's hard limit is 50."
            )

    if len([role for role in document.roles if not role.is_everyone]) > 250:
        warnings.append("Backup has more than 250 roles; Discord will reject extra roles.")
    if len(document.channels) > 500:
        warnings.append("Backup has more than 500 channels; Discord will reject extra channels.")

    return warnings


def validate_channel_parents(channels: list[ChannelModel]) -> list[str]:
    """Return human-readable parent problems (used by tests and the reader)."""

    by_id = {channel.id: channel for channel in channels}
    problems: list[str] = []
    for channel in channels:
        if channel.parent_id is None:
            continue
        parent = by_id.get(channel.parent_id)
        if parent is None:
            problems.append(f"{channel.name}: missing parent {channel.parent_id}")
        elif not parent.is_category:
            problems.append(f"{channel.name}: parent {parent.name} is not a category")
    return problems
