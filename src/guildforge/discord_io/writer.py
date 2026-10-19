"""Apply a backup to a Discord guild, with resume support and graceful skips."""

from __future__ import annotations

import base64
import binascii
import contextlib
import io
import json
import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import discord
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from guildforge.config import STATE_SCHEMA_VERSION
from guildforge.errors import TargetError
from guildforge.models.limits import TargetSnapshot, count_channels_to_create, count_roles_to_create
from guildforge.models.schema import (
    BackupDocument,
    ChannelKind,
    ChannelModel,
    EmojiModel,
    PermissionOverwriteModel,
    RoleModel,
    StickerModel,
)
from guildforge.utils.logger import GuildForgeLogger
from guildforge.utils.retry import with_retry

REASON = "GuildForge structure copy"
CUSTOM_EMOJI_RE = re.compile(r"^.*:\d+$")
VALID_ARCHIVE_DURATIONS = {60, 1440, 4320, 10080}


# --------------------------------------------------------------------------- #
# Pure helpers (unit tested without Discord)
# --------------------------------------------------------------------------- #
def order_roles_for_creation(roles: Iterable[RoleModel]) -> list[RoleModel]:
    """Lowest position first; skip @everyone and managed/integration roles."""

    return sorted(
        (role for role in roles if not role.is_everyone and not role.managed),
        key=lambda role: (role.position, role.name.casefold(), role.id),
    )


def build_role_id_map(
    roles: Iterable[RoleModel],
    *,
    everyone_source_id: int | None,
    everyone_target_id: int,
    created: dict[int, int],
) -> dict[int, int]:
    """Map source role IDs to target role IDs, including @everyone."""

    mapping = dict(created)
    if everyone_source_id is not None:
        mapping[everyone_source_id] = everyone_target_id
    for role in roles:
        if role.is_everyone:
            mapping[role.id] = everyone_target_id
    return mapping


@dataclass(frozen=True)
class MappedOverwrite:
    target_id: int
    allow: int
    deny: int


def map_overwrites(
    overwrites: Iterable[PermissionOverwriteModel],
    role_id_map: dict[int, int],
) -> tuple[list[MappedOverwrite], list[int]]:
    """Translate overwrites through the role map. Missing roles are returned separately."""

    mapped: list[MappedOverwrite] = []
    missing: list[int] = []
    for overwrite in overwrites:
        target_id = role_id_map.get(overwrite.target_id)
        if target_id is None:
            missing.append(overwrite.target_id)
            continue
        mapped.append(
            MappedOverwrite(
                target_id=target_id,
                allow=overwrite.allow,
                deny=overwrite.deny,
            )
        )
    return mapped, missing


@dataclass
class ApplyPlan:
    roles_create: int = 0
    categories_create: int = 0
    channels_create: int = 0
    emojis_create: int = 0
    stickers_create: int = 0
    roles_delete: int = 0
    channels_delete: int = 0
    notes: list[str] = field(default_factory=list)


def build_plan(
    document: BackupDocument,
    target: TargetSnapshot,
    *,
    clear_target: bool,
    include_emojis: bool,
) -> ApplyPlan:
    categories = sum(1 for channel in document.channels if channel.is_category)
    plan = ApplyPlan(
        roles_create=count_roles_to_create(document),
        categories_create=categories,
        channels_create=count_channels_to_create(document) - categories,
        emojis_create=len(document.emojis) if include_emojis else 0,
        stickers_create=len(document.stickers) if include_emojis else 0,
        roles_delete=target.clearable_roles if clear_target else 0,
        channels_delete=target.channels if clear_target else 0,
    )
    if not include_emojis and (document.emojis or document.stickers):
        plan.notes.append(
            "Emojis and stickers are present in the backup but will not be copied "
            "(pass --emojis to include them)."
        )
    return plan


@dataclass
class ApplyReport:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "created": len(self.created),
            "skipped": len(self.skipped),
            "failed": len(self.failed),
            "warnings": len(self.warnings),
        }


class ApplyState(BaseModel):
    """Small JSON file that lets an interrupted copy resume."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = STATE_SCHEMA_VERSION
    target_guild_id: int
    backup_digest: str
    role_map: dict[int, int] = Field(default_factory=dict)
    channel_map: dict[int, int] = Field(default_factory=dict)
    emoji_map: dict[int, int] = Field(default_factory=dict)
    sticker_map: dict[int, int] = Field(default_factory=dict)
    basic_settings_done: bool = False
    final_settings_done: bool = False
    overwrites_done: list[int] = Field(default_factory=list)
    created: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def new(cls, target_guild_id: int, digest: str) -> ApplyState:
        return cls(target_guild_id=target_guild_id, backup_digest=digest)

    @classmethod
    def load(cls, path: Path, *, target_guild_id: int, digest: str) -> ApplyState | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            state = cls.model_validate(data)
        except (OSError, json.JSONDecodeError, ValidationError):
            return None
        if state.target_guild_id != target_guild_id or state.backup_digest != digest:
            return None
        if state.schema_version != STATE_SCHEMA_VERSION:
            return None
        return state

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.model_dump_json(indent=2)
        handle, temp_name = tempfile.mkstemp(
            prefix=".guildforge-state-", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as temp_file:
                temp_file.write(payload)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def report(self) -> ApplyReport:
        return ApplyReport(
            created=list(self.created),
            skipped=list(self.skipped),
            failed=list(self.failed),
            warnings=list(self.warnings),
        )


# --------------------------------------------------------------------------- #
# Writer
# --------------------------------------------------------------------------- #
class GuildWriter:
    def __init__(
        self,
        *,
        target: discord.Guild,
        bot_member: discord.Member,
        document: BackupDocument,
        logger: GuildForgeLogger,
        options_clear_target: bool = False,
        options_emojis: bool = False,
        state: ApplyState | None = None,
        state_path: Path | None = None,
    ) -> None:
        self.target = target
        self.bot_member = bot_member
        self.document = document
        self.logger = logger
        self.clear_target = options_clear_target
        self.include_emojis = options_emojis
        self.state_path = state_path
        self.state = state or ApplyState.new(target.id, document.digest())
        self.created_roles: dict[int, discord.Role] = {}
        self.created_channels: dict[int, discord.abc.GuildChannel] = {}
        self._missing_overwrite_roles: set[int] = set()
        self._emoji_count = len(target.emojis)
        self._sticker_count = len(target.stickers)
        self._state_save_failed = False
        self._prune_state()

    # ----- state helpers ---------------------------------------------------
    def _prune_state(self) -> None:
        valid_roles = {role.id for role in self.target.roles}
        valid_channels = {channel.id for channel in self.target.channels}
        self.state.role_map = {
            source: target_id
            for source, target_id in self.state.role_map.items()
            if target_id in valid_roles
        }
        self.state.channel_map = {
            source: target_id
            for source, target_id in self.state.channel_map.items()
            if target_id in valid_channels
        }
        self.state.overwrites_done = [
            source for source in self.state.overwrites_done if source in self.state.channel_map
        ]

    def _save(self) -> None:
        if self.state_path is None or self._state_save_failed:
            return
        try:
            self.state.save(self.state_path)
        except OSError as exc:
            self._state_save_failed = True
            self.logger.warn(f"Could not save resume state to {self.state_path}: {exc}")

    def _created(self, label: str) -> None:
        self.state.created.append(label)
        self.logger.debug(f"Created {label}")

    def _skipped(self, label: str) -> None:
        self.state.skipped.append(label)

    def _failed(self, label: str, exc: BaseException) -> None:
        detail = self._describe_exception(exc)
        self.state.failed.append(f"{label}: {detail}")
        self.logger.error(f"{label}: {detail}")

    def _warn(self, message: str) -> None:
        if message not in self.state.warnings:
            self.state.warnings.append(message)
        self.logger.warn(message)

    @staticmethod
    def _describe_exception(exc: BaseException) -> str:
        if isinstance(exc, discord.Forbidden):
            return "Discord denied the action (missing permission or role hierarchy)"
        if isinstance(exc, discord.HTTPException):
            text = (exc.text or "").strip().splitlines()[0] if exc.text else ""
            return f"HTTP {exc.status}" + (f": {text}" if text else "")
        if isinstance(exc, discord.NotFound):
            return "Not found (it may have been deleted)"
        return str(exc) or exc.__class__.__name__

    # ----- run -------------------------------------------------------------
    async def run(self) -> ApplyReport:
        hierarchy = self._hierarchy_warning()
        if hierarchy:
            self._warn(hierarchy)
        self._save()

        if self.clear_target:
            await self._clear_target()
            self.state = ApplyState.new(self.target.id, self.document.digest())
            self._save()

        if not self.state.basic_settings_done:
            await self._apply_basic_settings()
            self.state.basic_settings_done = True
            self._save()

        await self._apply_roles()
        await self._apply_categories()
        await self._apply_channels()
        await self._apply_overwrites()
        if self.include_emojis:
            await self._apply_emojis()
            await self._apply_stickers()
        else:
            if self.document.emojis or self.document.stickers:
                self._skipped("emojis and stickers (use --emojis to copy them)")

        if not self.state.final_settings_done:
            await self._apply_channel_settings()
            self.state.final_settings_done = True
            self._save()

        report = self.state.report()
        for warning in self.logger.warnings:
            if warning not in report.warnings:
                report.warnings.append(warning)
        self.logger.info("")
        self.logger.table(
            "GuildForge report",
            ["Result", "Count"],
            [
                ["Created", str(len(report.created))],
                ["Skipped", str(len(report.skipped))],
                ["Failed", str(len(report.failed))],
                ["Warnings", str(len(report.warnings))],
            ],
        )
        if report.failed:
            self.logger.warn("Some steps failed. Review the list above, then rerun to resume.")
        else:
            self.logger.success("Copy complete.")
        return report

    def _hierarchy_warning(self) -> str | None:
        highest = max((role.position for role in self.target.roles), default=0)
        if self.bot_member.top_role.position < highest:
            return (
                f"The bot's top role '{self.bot_member.top_role.name}' is not the highest role "
                "in the target server. Move the bot role to the top in Server Settings > Roles "
                "so every role can be created and positioned."
            )
        return None

    # ----- clear -----------------------------------------------------------
    async def _clear_target(self) -> None:
        self.logger.rule("Clearing target")
        channels = [
            channel
            for channel in self.target.channels
            if not isinstance(channel, discord.CategoryChannel)
        ]
        categories = [
            channel
            for channel in self.target.channels
            if isinstance(channel, discord.CategoryChannel)
        ]
        for channel in [*channels, *categories]:
            try:
                await with_retry(
                    lambda channel=channel: channel.delete(reason=REASON),
                    description=f"Deleting channel '{channel.name}'",
                    logger=self.logger,
                )
                self._created(f"deleted channel #{channel.name}")
            except (discord.HTTPException, discord.Forbidden) as exc:
                self._failed(f"delete channel #{channel.name}", exc)

        for role in sorted(self.target.roles, key=lambda item: item.position, reverse=True):
            if role.is_default() or role.managed:
                continue
            if role.position >= self.bot_member.top_role.position:
                self._warn(f"Role '{role.name}' is above the bot role and could not be deleted.")
                continue
            try:
                await with_retry(
                    lambda role=role: role.delete(reason=REASON),
                    description=f"Deleting role '{role.name}'",
                    logger=self.logger,
                )
                self._created(f"deleted role @{role.name}")
            except (discord.HTTPException, discord.Forbidden) as exc:
                self._failed(f"delete role @{role.name}", exc)
        self._save()

    # ----- settings --------------------------------------------------------
    async def _edit_guild_group(self, label: str, fields: dict[str, Any]) -> None:
        if not fields:
            return

        async def edit(**kwargs: Any) -> None:
            await self.target.edit(reason=REASON, **kwargs)

        try:
            await with_retry(
                lambda: edit(**fields),
                description=f"Applying {label}",
                logger=self.logger,
            )
        except discord.Forbidden as exc:
            self._warn(f"Could not apply {label}: {self._describe_exception(exc)}")
        except discord.HTTPException as exc:
            self._warn(
                f"Could not apply all {label} at once ({self._describe_exception(exc)}); "
                "retrying field by field."
            )
            for name, value in fields.items():
                try:
                    await with_retry(
                        lambda name=name, value=value: edit(**{name: value}),
                        description=f"Applying setting {name}",
                        logger=self.logger,
                    )
                except discord.HTTPException as field_exc:
                    self._warn(
                        f"Skipped server setting '{name}': {self._describe_exception(field_exc)}"
                    )

    @staticmethod
    def _decode_base64(value: str | None) -> bytes | None:
        if not value:
            return None
        try:
            return base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            return None

    def _enum_or_none(self, enum_type: type, value: int) -> Any:
        try:
            return enum_type(value)
        except ValueError:
            return None

    async def _apply_basic_settings(self) -> None:
        self.logger.rule("Applying server settings")
        settings = self.document.guild.settings
        identity: dict[str, Any] = {"name": self.document.guild.name[:100]}
        icon = self._decode_base64(self.document.guild.icon)
        if icon is not None:
            identity["icon"] = icon
        elif self.document.guild.icon:
            self._warn("Server icon in the backup is not valid base64; skipped.")

        if self.document.guild.banner:
            if "BANNER" in self.target.features:
                banner = self._decode_base64(self.document.guild.banner)
                if banner is not None:
                    identity["banner"] = banner
                else:
                    self._warn("Server banner in the backup is not valid base64; skipped.")
            else:
                self._warn(
                    f"Target is boost level {self.target.premium_tier}; a server banner "
                    "requires level 2. Banner skipped."
                )
        if self.document.guild.splash:
            if "INVITE_SPLASH" in self.target.features:
                splash = self._decode_base64(self.document.guild.splash)
                if splash is not None:
                    identity["splash"] = splash
                else:
                    self._warn("Invite splash in the backup is not valid base64; skipped.")
            else:
                self._warn(
                    f"Target is boost level {self.target.premium_tier}; an invite splash "
                    "requires level 1. Splash skipped."
                )
        if settings.description is not None:
            identity["description"] = settings.description[:300]

        moderation: dict[str, Any] = {}
        verification = self._enum_or_none(discord.VerificationLevel, settings.verification_level)
        if verification is not None:
            moderation["verification_level"] = verification
        notifications = self._enum_or_none(
            discord.NotificationLevel, settings.default_notifications
        )
        if notifications is not None:
            moderation["default_notifications"] = notifications
        content_filter = self._enum_or_none(discord.ContentFilter, settings.explicit_content_filter)
        if content_filter is not None:
            moderation["explicit_content_filter"] = content_filter
        moderation["afk_timeout"] = settings.afk_timeout
        moderation["system_channel_flags"] = discord.SystemChannelFlags._from_value(
            settings.system_channel_flags
        )
        try:
            moderation["preferred_locale"] = discord.Locale(settings.preferred_locale)
        except ValueError:
            self._warn(
                f"Preferred locale '{settings.preferred_locale}' is not supported by "
                "discord.py; skipped."
            )

        await self._edit_guild_group("server identity settings", identity)
        await self._edit_guild_group("server moderation settings", moderation)

    async def _apply_channel_settings(self) -> None:
        self.logger.rule("Applying channel-linked settings")
        settings = self.document.guild.settings
        fields: dict[str, Any] = {}

        def target_channel(source_id: int | None) -> discord.abc.GuildChannel | None:
            if source_id is None:
                return None
            target_id = self.state.channel_map.get(source_id)
            if target_id is None:
                return None
            return self.created_channels.get(target_id) or self.target.get_channel(target_id)

        afk = target_channel(settings.afk_channel_id)
        if isinstance(afk, (discord.VoiceChannel, discord.StageChannel)):
            fields["afk_channel"] = afk
        elif settings.afk_channel_id is not None:
            self._warn("AFK channel could not be mapped to a voice channel on the target.")

        system = target_channel(settings.system_channel_id)
        if isinstance(system, discord.TextChannel):
            fields["system_channel"] = system
        elif settings.system_channel_id is not None:
            self._warn("System channel could not be mapped to a text channel on the target.")

        if "COMMUNITY" in self.target.features:
            rules = target_channel(settings.rules_channel_id)
            if isinstance(rules, discord.TextChannel):
                fields["rules_channel"] = rules
            updates = target_channel(settings.public_updates_channel_id)
            if isinstance(updates, discord.TextChannel):
                fields["public_updates_channel"] = updates
        elif settings.rules_channel_id or settings.public_updates_channel_id:
            self._warn("Rules/updates channels require Community mode on the target; skipped.")

        await self._edit_guild_group("channel-linked server settings", fields)

    # ----- roles -----------------------------------------------------------
    async def _apply_roles(self) -> None:
        self.logger.rule("Creating roles")
        everyone = self.document.everyone_role()
        if everyone is not None:
            target_everyone = self.target.default_role
            try:
                await with_retry(
                    lambda: target_everyone.edit(
                        permissions=discord.Permissions(everyone.permissions),
                        reason=REASON,
                    ),
                    description="Updating @everyone permissions",
                    logger=self.logger,
                )
                self.state.role_map[everyone.id] = target_everyone.id
                self._created("role @everyone (permissions updated)")
            except (discord.HTTPException, discord.Forbidden) as exc:
                self._failed("update role @everyone", exc)

        roles = order_roles_for_creation(self.document.roles)
        with self.logger.progress("Creating roles", len(roles)) as progress:
            for role in roles:
                mapped = self.state.role_map.get(role.id)
                if mapped is not None:
                    existing = self.target.get_role(mapped)
                    if existing is not None:
                        self.created_roles[mapped] = existing
                        self._skipped(f"role @{role.name} (already mapped)")
                        progress.advance()
                        continue

                display_icon: bytes | str | None = None
                if role.unicode_emoji:
                    display_icon = role.unicode_emoji
                elif role.icon:
                    display_icon = self._decode_base64(role.icon)
                    if display_icon is None:
                        self._warn(f"Role @{role.name} has an invalid icon; skipped icon.")

                name = role.name[:100]
                if name != role.name:
                    self._warn(f"Role name '{role.name}' was truncated to 100 characters.")

                try:
                    try:
                        created = await with_retry(
                            lambda role=role, name=name, display_icon=display_icon: (
                                self._create_source_role(role, name, display_icon)
                            ),
                            description=f"Creating role @{name}",
                            logger=self.logger,
                        )
                    except discord.HTTPException as exc:
                        if display_icon is None or exc.status != 400:
                            raise
                        self._warn(
                            f"Role icon for @{name} was rejected by Discord; "
                            "creating the role without it."
                        )
                        created = await with_retry(
                            lambda role=role, name=name: self._create_source_role(role, name, None),
                            description=f"Creating role @{name} without icon",
                            logger=self.logger,
                        )
                    self.state.role_map[role.id] = created.id
                    self.created_roles[created.id] = created
                    self._created(f"role @{created.name}")
                except (discord.HTTPException, discord.Forbidden) as exc:
                    self._failed(f"create role @{name}", exc)
                progress.advance()
                self._save()

    async def _create_source_role(
        self, role: RoleModel, name: str, display_icon: bytes | str | None
    ) -> discord.Role:
        return await self.target.create_role(
            name=name,
            permissions=discord.Permissions(role.permissions),
            colour=discord.Colour(role.color),
            hoist=role.hoist,
            mentionable=role.mentionable,
            display_icon=display_icon,
            reason=REASON,
        )

    # ----- categories ------------------------------------------------------
    async def _create_category(self, category: ChannelModel) -> discord.CategoryChannel:
        name = category.name[:100]
        kwargs: dict[str, Any] = {"reason": REASON}
        try:
            return await with_retry(
                lambda: self.target.create_category(name, position=category.position, **kwargs),
                description=f"Creating category '{name}'",
                logger=self.logger,
            )
        except discord.HTTPException as exc:
            if exc.status != 400:
                raise
            return await with_retry(
                lambda: self.target.create_category(name, **kwargs),
                description=f"Creating category '{name}' without an explicit position",
                logger=self.logger,
            )

    async def _apply_categories(self) -> None:
        self.logger.rule("Creating categories")
        categories = self.document.categories()
        with self.logger.progress("Creating categories", len(categories)) as progress:
            for category in categories:
                mapped = self.state.channel_map.get(category.id)
                if mapped is not None:
                    existing = self.target.get_channel(mapped)
                    if existing is not None:
                        self.created_channels[mapped] = existing
                        self._skipped(f"category '{category.name}' (already mapped)")
                        progress.advance()
                        continue
                try:
                    created = await self._create_category(category)
                    self.state.channel_map[category.id] = created.id
                    self.created_channels[created.id] = created
                    self._created(f"category '{created.name}'")
                except (discord.HTTPException, discord.Forbidden) as exc:
                    self._failed(f"create category '{category.name}'", exc)
                progress.advance()
                self._save()

    # ----- channels --------------------------------------------------------
    def _target_category(self, channel: ChannelModel) -> discord.CategoryChannel | None:
        if channel.parent_id is None:
            return None
        target_id = self.state.channel_map.get(channel.parent_id)
        if target_id is None:
            self._warn(
                f"Parent category for '{channel.name}' was not created; "
                "the channel will be placed at the top level."
            )
            return None
        resolved = self.created_channels.get(target_id) or self.target.get_channel(target_id)
        if isinstance(resolved, discord.CategoryChannel):
            return resolved
        self._warn(
            f"Parent category for '{channel.name}' is missing on the target; "
            "the channel will be placed at the top level."
        )
        return None

    def _video_quality(self, channel: ChannelModel) -> discord.VideoQualityMode | None:
        if channel.video_quality_mode is None:
            return None
        try:
            return discord.VideoQualityMode(channel.video_quality_mode)
        except ValueError:
            return None

    def _forum_tags(self, channel: ChannelModel) -> list[discord.ForumTag]:
        tags: list[discord.ForumTag] = []
        seen: set[str] = set()
        for tag in channel.available_tags:
            if len(tags) >= 20:
                break
            lowered = tag.name.casefold()
            if lowered in seen:
                continue
            seen.add(lowered)
            emoji: str | None = None
            if tag.emoji_name and not tag.emoji_id:
                emoji = tag.emoji_name
            try:
                tags.append(discord.ForumTag(name=tag.name, emoji=emoji, moderated=tag.moderated))
            except (TypeError, ValueError):
                continue
        return tags

    def _default_reaction(self, channel: ChannelModel) -> str | None:
        value = channel.default_reaction_emoji
        if not value:
            return None
        if CUSTOM_EMOJI_RE.match(value):
            self._warn(
                f"Default reaction for '{channel.name}' uses a custom emoji; "
                "it was skipped because source emoji IDs differ on the target."
            )
            return None
        return value

    async def _create_channel(self, channel: ChannelModel) -> discord.abc.GuildChannel:
        name = channel.name[:100]
        if name != channel.name:
            self._warn(f"Channel name '{channel.name}' was truncated to 100 characters.")
        category = self._target_category(channel)
        common: dict[str, Any] = {
            "category": category,
            "position": channel.position,
            "reason": REASON,
        }
        topic = channel.topic[:1024] if channel.topic is not None else None

        community = "COMMUNITY" in self.target.features

        if channel.kind in {ChannelKind.text, ChannelKind.news}:
            if channel.kind is ChannelKind.news and not community:
                self._warn(
                    f"Announcement channel '{name}' created as a text channel because "
                    "Community is not enabled on the target."
                )
            news = channel.kind is ChannelKind.news and community
            kwargs = {
                **common,
                "news": news,
                "nsfw": channel.nsfw,
                "slowmode_delay": channel.slowmode,
            }
            if topic is not None:
                kwargs["topic"] = topic
            if (
                channel.default_auto_archive_duration in VALID_ARCHIVE_DURATIONS
                and channel.default_auto_archive_duration is not None
            ):
                kwargs["default_auto_archive_duration"] = channel.default_auto_archive_duration
            if channel.default_thread_slowmode is not None:
                kwargs["default_thread_slowmode_delay"] = channel.default_thread_slowmode
            return await with_retry(
                lambda: self.target.create_text_channel(name, **kwargs),
                description=f"Creating text channel #{name}",
                logger=self.logger,
            )

        if channel.kind is ChannelKind.voice:
            bitrate = channel.bitrate
            if bitrate is not None:
                maximum = int(self.target.bitrate_limit)
                if bitrate > maximum:
                    self._warn(
                        f"Voice channel '{name}' bitrate {bitrate} exceeds the target limit; "
                        f"using {maximum}."
                    )
                    bitrate = maximum
            kwargs = {**common, "nsfw": channel.nsfw}
            if bitrate is not None:
                kwargs["bitrate"] = bitrate
            if channel.user_limit is not None:
                user_limit = min(channel.user_limit, 99)
                if user_limit != channel.user_limit:
                    self._warn(
                        f"Voice channel '{name}' user limit was clamped to 99 "
                        "(Discord's maximum for voice channels)."
                    )
                kwargs["user_limit"] = user_limit
            if channel.rtc_region:
                kwargs["rtc_region"] = channel.rtc_region
            video_quality = self._video_quality(channel)
            if video_quality is not None:
                kwargs["video_quality_mode"] = video_quality
            return await with_retry(
                lambda: self.target.create_voice_channel(name, **kwargs),
                description=f"Creating voice channel {name}",
                logger=self.logger,
            )

        if channel.kind is ChannelKind.stage:
            if not community:
                self._warn(
                    f"Stage channel '{name}' created as a voice channel because "
                    "Community is not enabled on the target."
                )
                kwargs = {**common, "nsfw": channel.nsfw}
                if channel.bitrate is not None:
                    kwargs["bitrate"] = min(channel.bitrate, int(self.target.bitrate_limit))
                if channel.user_limit is not None:
                    kwargs["user_limit"] = channel.user_limit
                if channel.rtc_region:
                    kwargs["rtc_region"] = channel.rtc_region
                video_quality = self._video_quality(channel)
                if video_quality is not None:
                    kwargs["video_quality_mode"] = video_quality
                return await with_retry(
                    lambda: self.target.create_voice_channel(name, **kwargs),
                    description=f"Creating voice channel {name} (stage fallback)",
                    logger=self.logger,
                )
            kwargs = {**common, "nsfw": channel.nsfw}
            if channel.bitrate is not None:
                kwargs["bitrate"] = min(channel.bitrate, int(self.target.bitrate_limit))
            if channel.user_limit is not None:
                kwargs["user_limit"] = channel.user_limit
            if channel.rtc_region:
                kwargs["rtc_region"] = channel.rtc_region
            video_quality = self._video_quality(channel)
            if video_quality is not None:
                kwargs["video_quality_mode"] = video_quality
            return await with_retry(
                lambda: self.target.create_stage_channel(name, **kwargs),
                description=f"Creating stage channel {name}",
                logger=self.logger,
            )

        # forum / media
        if not community:
            self._warn(
                f"Forum/media channel '{name}' created as a text channel because "
                "Community is not enabled on the target."
            )
            return await with_retry(
                lambda: self.target.create_text_channel(
                    name,
                    **common,
                    nsfw=channel.nsfw,
                    slowmode_delay=channel.slowmode,
                ),
                description=f"Creating text channel #{name} (forum fallback)",
                logger=self.logger,
            )

        kwargs = {
            **common,
            "nsfw": channel.nsfw,
            "slowmode_delay": channel.slowmode,
            "media": channel.kind is ChannelKind.media,
        }
        if topic is not None:
            kwargs["topic"] = topic
        if channel.default_auto_archive_duration in VALID_ARCHIVE_DURATIONS:
            kwargs["default_auto_archive_duration"] = channel.default_auto_archive_duration
        if channel.default_thread_slowmode is not None:
            kwargs["default_thread_slowmode_delay"] = channel.default_thread_slowmode
        if channel.default_sort_order is not None:
            with contextlib.suppress(ValueError):
                kwargs["default_sort_order"] = discord.ForumOrderType(channel.default_sort_order)
        reaction = self._default_reaction(channel)
        if reaction:
            kwargs["default_reaction_emoji"] = reaction
        if channel.kind is ChannelKind.forum and channel.default_forum_layout is not None:
            with contextlib.suppress(ValueError):
                kwargs["default_layout"] = discord.ForumLayoutType(channel.default_forum_layout)
        tags = self._forum_tags(channel)
        if tags:
            kwargs["available_tags"] = tags
        return await with_retry(
            lambda: self.target.create_forum(name, **kwargs),
            description=f"Creating forum channel #{name}",
            logger=self.logger,
        )

    async def _apply_channels(self) -> None:
        self.logger.rule("Creating channels")
        channels = sorted(
            (channel for channel in self.document.channels if not channel.is_category),
            key=lambda channel: channel.position,
        )
        with self.logger.progress("Creating channels", len(channels)) as progress:
            for channel in channels:
                mapped = self.state.channel_map.get(channel.id)
                if mapped is not None:
                    existing = self.target.get_channel(mapped)
                    if existing is not None:
                        self.created_channels[mapped] = existing
                        self._skipped(f"channel '#{channel.name}' (already mapped)")
                        progress.advance()
                        continue
                try:
                    created = await self._create_channel(channel)
                    self.state.channel_map[channel.id] = created.id
                    self.created_channels[created.id] = created
                    if channel.id in self.state.overwrites_done:
                        self.state.overwrites_done.remove(channel.id)
                    self._created(f"channel #{created.name} ({created.type.name})")
                except (discord.HTTPException, discord.Forbidden) as exc:
                    self._failed(f"create channel #{channel.name}", exc)
                progress.advance()
                self._save()

    # ----- overwrites ------------------------------------------------------
    def _resolve_role(self, role_id: int) -> discord.Role | None:
        role = self.created_roles.get(role_id)
        if role is not None:
            return role
        return self.target.get_role(role_id)

    async def _apply_overwrites(self) -> None:
        self.logger.rule("Applying permission overwrites")
        role_map = build_role_id_map(
            self.document.roles,
            everyone_source_id=(
                self.document.everyone_role().id
                if self.document.everyone_role() is not None
                else None
            ),
            everyone_target_id=self.target.default_role.id,
            created=self.state.role_map,
        )
        pending = [
            channel
            for channel in self.document.channels
            if channel.id in self.state.channel_map and channel.id not in self.state.overwrites_done
        ]
        with self.logger.progress("Applying permission overwrites", len(pending)) as progress:
            for channel in pending:
                target_id = self.state.channel_map.get(channel.id)
                target_channel = self.created_channels.get(target_id or 0) or (
                    self.target.get_channel(target_id) if target_id else None
                )
                if target_channel is None:
                    self._warn(
                        f"Permissions for '{channel.name}' were skipped because the "
                        "target channel is missing."
                    )
                    progress.advance()
                    continue
                if not channel.permission_overwrites:
                    self.state.overwrites_done.append(channel.id)
                    self._save()
                    progress.advance()
                    continue

                mapped, missing = map_overwrites(channel.permission_overwrites, role_map)
                self._missing_overwrite_roles.update(missing)
                overwrites: dict[discord.Role, discord.PermissionOverwrite] = {}
                for item in mapped:
                    role = self._resolve_role(item.target_id)
                    if role is None:
                        continue
                    overwrites[role] = discord.PermissionOverwrite.from_pair(
                        discord.Permissions(item.allow),
                        discord.Permissions(item.deny),
                    )
                try:
                    await with_retry(
                        lambda target_channel=target_channel, overwrites=overwrites: (
                            target_channel.edit(overwrites=overwrites, reason=REASON)
                        ),
                        description=f"Applying permissions to #{channel.name}",
                        logger=self.logger,
                    )
                    self.state.overwrites_done.append(channel.id)
                    self._skipped(f"permissions for #{channel.name} (applied)")
                except (discord.HTTPException, discord.Forbidden) as exc:
                    self._failed(f"apply permissions to #{channel.name}", exc)
                progress.advance()
                self._save()

        if self._missing_overwrite_roles:
            self._warn(
                f"{len(self._missing_overwrite_roles)} role(s) referenced by overwrites "
                "were managed or could not be recreated; those overwrites were skipped."
            )

    # ----- emojis and stickers --------------------------------------------
    async def _create_emoji(self, emoji: EmojiModel, roles: list[discord.Role]) -> discord.Emoji:
        image = base64.b64decode(emoji.image)

        async def create(with_roles: bool) -> discord.Emoji:
            return await self.target.create_custom_emoji(
                name=emoji.name,
                image=image,
                roles=roles if with_roles and roles else [],
                reason=REASON,
            )

        try:
            return await with_retry(
                lambda: create(True),
                description=f"Creating emoji :{emoji.name}:",
                logger=self.logger,
            )
        except discord.HTTPException as exc:
            if exc.status != 400 or not roles:
                raise
            self._warn(
                f"Emoji :{emoji.name}: could not be restricted to its roles; "
                "creating it without role restrictions."
            )
            return await with_retry(
                lambda: create(False),
                description=f"Creating emoji :{emoji.name}: without roles",
                logger=self.logger,
            )

    async def _apply_emojis(self) -> None:
        if not self.document.emojis:
            return
        self.logger.rule("Copying emojis")
        limit = int(getattr(self.target, "emoji_limit", 50))
        with self.logger.progress("Copying emojis", len(self.document.emojis)) as progress:
            for emoji in self.document.emojis:
                existing = self.state.emoji_map.get(emoji.id)
                if existing is not None and self.target.get_emoji(existing) is not None:
                    self._skipped(f"emoji :{emoji.name}: (already copied)")
                    progress.advance()
                    continue
                if self._emoji_count >= limit:
                    self._warn(
                        f"Emoji slot limit reached ({limit}); remaining emojis were skipped."
                    )
                    break
                roles: list[discord.Role] = []
                for source_role_id in emoji.role_ids:
                    target_role_id = self.state.role_map.get(source_role_id)
                    if target_role_id is None:
                        continue
                    role = self._resolve_role(target_role_id)
                    if role is not None:
                        roles.append(role)
                try:
                    created = await self._create_emoji(emoji, roles)
                    self.state.emoji_map[emoji.id] = created.id
                    self._emoji_count += 1
                    self._created(f"emoji :{created.name}:")
                except (discord.HTTPException, discord.Forbidden, binascii.Error) as exc:
                    self._failed(f"create emoji :{emoji.name}:", exc)
                progress.advance()
                self._save()

    async def _apply_stickers(self) -> None:
        if not self.document.stickers:
            return
        self.logger.rule("Copying stickers")
        limit = int(getattr(self.target, "sticker_limit", 5))
        extensions = {1: "png", 2: "png", 3: "json", 4: "gif"}
        with self.logger.progress("Copying stickers", len(self.document.stickers)) as progress:
            for sticker in self.document.stickers:
                existing = self.state.sticker_map.get(sticker.id)
                if existing is not None:
                    self._skipped(f"sticker '{sticker.name}' (already copied)")
                    progress.advance()
                    continue
                if sticker.format_type == 3:
                    self._warn(
                        f"Sticker '{sticker.name}' is a Lottie sticker; Discord bots cannot "
                        "create those. Skipped."
                    )
                    progress.advance()
                    continue
                if self._sticker_count >= limit:
                    self._warn(
                        f"Sticker slot limit reached ({limit}); remaining stickers were skipped."
                    )
                    break
                try:
                    data = base64.b64decode(sticker.image, validate=True)
                    extension = extensions.get(sticker.format_type, "png")
                    tags = (sticker.tags.split(",")[0].strip() or "🙂")[:200]

                    async def create_sticker(
                        data: bytes = data,
                        sticker: StickerModel = sticker,
                        extension: str = extension,
                        tags: str = tags,
                    ) -> discord.GuildSticker:
                        file = discord.File(io.BytesIO(data), filename=f"{sticker.id}.{extension}")
                        return await self.target.create_sticker(
                            name=sticker.name[:30],
                            description=(sticker.description or "")[:100],
                            emoji=tags,
                            file=file,
                            reason=REASON,
                        )

                    created = await with_retry(
                        create_sticker,
                        description=f"Creating sticker '{sticker.name}'",
                        logger=self.logger,
                    )
                    self.state.sticker_map[sticker.id] = created.id
                    self._sticker_count += 1
                    self._created(f"sticker '{created.name}'")
                except (discord.HTTPException, discord.Forbidden, binascii.Error) as exc:
                    self._failed(f"create sticker '{sticker.name}'", exc)
                progress.advance()
                self._save()


async def validate_target_for_apply(
    guild: discord.Guild, bot_member: discord.Member
) -> tuple[TargetSnapshot, list[str]]:
    """Recompute a target snapshot plus non-fatal warnings before writing."""

    clearable = sum(
        1
        for role in guild.roles
        if not role.is_default()
        and not role.managed
        and role.position < bot_member.top_role.position
    )
    snapshot = TargetSnapshot(
        guild_id=guild.id,
        name=guild.name,
        roles=len(guild.roles),
        clearable_roles=clearable,
        channels=len(guild.channels),
        emojis=len(guild.emojis),
        stickers=len(guild.stickers),
        boost_level=guild.premium_tier,
        features=set(guild.features),
    )
    warnings: list[str] = []
    highest = max((role.position for role in guild.roles), default=0)
    if bot_member.top_role.position < highest:
        warnings.append(
            f"The bot's top role '{bot_member.top_role.name}' is not the highest role in "
            "the target server. Move the bot role to the top in Server Settings > Roles."
        )
    if not bot_member.guild_permissions.administrator:
        raise TargetError("The bot needs Administrator permission on the target server.")
    return snapshot, warnings


def _mapping_summary(state: ApplyState) -> str:
    return (
        f"{len(state.role_map)} roles, {len(state.channel_map)} channels, "
        f"{len(state.emoji_map)} emojis mapped"
    )


def state_summary(state: ApplyState) -> str:
    return _mapping_summary(state)
