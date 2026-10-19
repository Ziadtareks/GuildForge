"""Pydantic models that form GuildForge's portable backup format."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1


class ChannelKind(str, Enum):
    """Channel kinds GuildForge knows how to copy."""

    text = "text"
    voice = "voice"
    category = "category"
    news = "news"
    stage = "stage"
    forum = "forum"
    media = "media"

    @property
    def requires_community(self) -> bool:
        return self in {
            ChannelKind.news,
            ChannelKind.stage,
            ChannelKind.forum,
            ChannelKind.media,
        }


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PermissionOverwriteModel(StrictModel):
    """A role-targeted permission overwrite. Member overwrites are never exported."""

    target_id: int = Field(gt=0)
    target_type: Literal["role"] = "role"
    allow: int = Field(default=0, ge=0)
    deny: int = Field(default=0, ge=0)


class ForumTagModel(StrictModel):
    name: str = Field(min_length=1, max_length=20)
    emoji_name: str | None = None
    emoji_id: int | None = Field(default=None, gt=0)
    moderated: bool = False


class RoleModel(StrictModel):
    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=100)
    color: int = Field(default=0, ge=0, le=0xFFFFFF)
    hoist: bool = False
    mentionable: bool = False
    permissions: int = Field(default=0, ge=0)
    position: int = Field(default=0, ge=0)
    managed: bool = False
    is_everyone: bool = False
    unicode_emoji: str | None = None
    icon: str | None = Field(default=None, description="Base64 encoded role icon PNG.")

    @field_validator("unicode_emoji")
    @classmethod
    def _strip_emoji(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ChannelModel(StrictModel):
    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=100)
    kind: ChannelKind = ChannelKind.text
    position: int = Field(default=0, ge=0)
    parent_id: int | None = Field(default=None, gt=0)
    topic: str | None = None
    nsfw: bool = False
    slowmode: int = Field(default=0, ge=0, le=21600)
    bitrate: int | None = Field(default=None, ge=8000)
    user_limit: int | None = Field(default=None, ge=0, le=10000)
    rtc_region: str | None = None
    video_quality_mode: int | None = Field(default=None, ge=1, le=2)
    default_auto_archive_duration: int | None = None
    default_thread_slowmode: int | None = Field(default=None, ge=0, le=21600)
    default_sort_order: int | None = Field(default=None, ge=0, le=1)
    default_forum_layout: int | None = Field(default=None, ge=0, le=2)
    default_reaction_emoji: str | None = None
    available_tags: list[ForumTagModel] = Field(default_factory=list)
    permission_overwrites: list[PermissionOverwriteModel] = Field(default_factory=list)

    @property
    def is_category(self) -> bool:
        return self.kind is ChannelKind.category


class EmojiModel(StrictModel):
    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=100)
    image: str = Field(min_length=1, description="Base64 encoded emoji image.")
    animated: bool = False
    role_ids: list[int] = Field(default_factory=list)


class StickerModel(StrictModel):
    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    tags: str = ""
    format_type: int = 1
    image: str = Field(min_length=1, description="Base64 encoded sticker image.")


class GuildSettings(StrictModel):
    description: str | None = None
    verification_level: int = Field(default=0, ge=0, le=4)
    default_notifications: int = Field(default=0, ge=0, le=2)
    explicit_content_filter: int = Field(default=0, ge=0, le=2)
    afk_channel_id: int | None = Field(default=None, gt=0)
    afk_timeout: int = Field(default=300, ge=60, le=3600)
    system_channel_id: int | None = Field(default=None, gt=0)
    system_channel_flags: int = Field(default=0, ge=0)
    rules_channel_id: int | None = Field(default=None, gt=0)
    public_updates_channel_id: int | None = Field(default=None, gt=0)
    preferred_locale: str = "en-US"


class GuildModel(StrictModel):
    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=100)
    icon: str | None = Field(default=None, description="Base64 encoded server icon.")
    banner: str | None = Field(default=None, description="Base64 encoded server banner.")
    splash: str | None = Field(default=None, description="Base64 encoded invite splash.")
    premium_tier: int = Field(default=0, ge=0, le=3)
    features: list[str] = Field(default_factory=list)
    settings: GuildSettings = Field(default_factory=GuildSettings)


class BackupSource(StrictModel):
    type: Literal["guild", "template", "file"] = "file"
    guild_id: int | None = Field(default=None, gt=0)
    template_code: str | None = None
    name: str | None = None

    @field_validator("template_code")
    @classmethod
    def _strip_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class BackupDocument(StrictModel):
    """The complete, versioned structure of one Discord server."""

    schema_version: int = SCHEMA_VERSION
    tool_version: str = "1.0.0"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: BackupSource = Field(default_factory=BackupSource)
    guild: GuildModel
    roles: list[RoleModel] = Field(default_factory=list)
    channels: list[ChannelModel] = Field(default_factory=list)
    emojis: list[EmojiModel] = Field(default_factory=list)
    stickers: list[StickerModel] = Field(default_factory=list)

    # ----- convenience lookups -------------------------------------------------
    def role_by_id(self, role_id: int | None) -> RoleModel | None:
        if role_id is None:
            return None
        return next((role for role in self.roles if role.id == role_id), None)

    def channel_by_id(self, channel_id: int | None) -> ChannelModel | None:
        if channel_id is None:
            return None
        return next((channel for channel in self.channels if channel.id == channel_id), None)

    def everyone_role(self) -> RoleModel | None:
        return next((role for role in self.roles if role.is_everyone), None)

    def categories(self) -> list[ChannelModel]:
        return sorted(
            (channel for channel in self.channels if channel.is_category),
            key=lambda channel: channel.position,
        )

    def children_of(self, category_id: int) -> list[ChannelModel]:
        return sorted(
            (
                channel
                for channel in self.channels
                if not channel.is_category and channel.parent_id == category_id
            ),
            key=lambda channel: channel.position,
        )

    def top_level_channels(self) -> list[ChannelModel]:
        return sorted(
            (
                channel
                for channel in self.channels
                if not channel.is_category and channel.parent_id is None
            ),
            key=lambda channel: channel.position,
        )

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"

    def digest(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_json(cls, text: str) -> BackupDocument:
        return cls.model_validate(json.loads(text))

    def roster_summary(self) -> dict[str, int]:
        roles = sum(1 for role in self.roles if not role.is_everyone and not role.managed)
        categories = sum(1 for channel in self.channels if channel.is_category)
        channels = len(self.channels) - categories
        return {
            "roles": roles,
            "categories": categories,
            "channels": channels,
            "emojis": len(self.emojis),
            "stickers": len(self.stickers),
        }


def expected_everyone_id(guild_id: int) -> int:
    """Discord uses the guild ID as the @everyone role ID."""

    return guild_id


def dump_for_state(document: BackupDocument) -> dict[str, Any]:
    return document.model_dump(mode="json")
