"""Read a guild or template into GuildForge's backup model."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path

import discord

from guildforge.config import GUILD_ID_RE, parse_template_code
from guildforge.discord_io.client import DiscordSession
from guildforge.errors import SourceError
from guildforge.models.schema import (
    BackupDocument,
    BackupSource,
    ChannelKind,
    ChannelModel,
    EmojiModel,
    ForumTagModel,
    GuildModel,
    GuildSettings,
    PermissionOverwriteModel,
    RoleModel,
    StickerModel,
)
from guildforge.models.validation import load_backup
from guildforge.utils.logger import GuildForgeLogger

SUPPORTED_TYPES: dict[discord.ChannelType, ChannelKind] = {
    discord.ChannelType.text: ChannelKind.text,
    discord.ChannelType.voice: ChannelKind.voice,
    discord.ChannelType.category: ChannelKind.category,
    discord.ChannelType.news: ChannelKind.news,
    discord.ChannelType.stage_voice: ChannelKind.stage,
    discord.ChannelType.forum: ChannelKind.forum,
    discord.ChannelType.media: ChannelKind.media,
}


@dataclass
class ExportResult:
    document: BackupDocument
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SourceInput:
    kind: str
    value: str
    path: Path | None = None


def classify_source(value: str) -> SourceInput:
    """Decide whether --source is a guild ID, template link/code, or JSON file."""

    text = value.strip()
    if not text:
        raise SourceError("--source cannot be empty.")

    path = Path(text).expanduser()
    if path.exists():
        if path.is_dir():
            raise SourceError(f"Source path is a folder, not a backup file: {path}")
        return SourceInput(kind="file", value=text, path=path)

    if path.suffix.lower() == ".json":
        raise SourceError(
            f"Backup file not found: {path}",
            hint="Check the path or export the server first with: guildforge export",
        )

    if GUILD_ID_RE.match(text):
        return SourceInput(kind="guild", value=text)

    code = parse_template_code(text)
    if code:
        return SourceInput(kind="template", value=text)

    raise SourceError(
        f"'{value}' is not a server ID, template link/code, or backup file.",
        hint=(
            "Pass a 17-20 digit server ID, a https://discord.new/CODE link, a template "
            "code, or a path to a .json backup."
        ),
    )


def _enum_value(value: object, default: int = 0) -> int:
    raw = getattr(value, "value", value)
    return int(raw) if isinstance(raw, (int, float, str)) else default


def _flag_value(value: object, default: int = 0) -> int:
    raw = getattr(value, "value", value)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _locale_value(guild: discord.Guild) -> str:
    value = getattr(guild, "preferred_locale", None)
    if value is None:
        return "en-US"
    value = getattr(value, "value", value)
    return str(value)


def _is_default_role(role: discord.Role, guild: discord.Guild) -> bool:
    checker = getattr(role, "is_default", None)
    if callable(checker):
        return bool(checker())
    return role.id == guild.id


async def _asset_base64(asset: object | None, label: str, warnings: list[str]) -> str | None:
    if asset is None:
        return None
    read = getattr(asset, "read", None)
    if not callable(read):
        return None
    try:
        data = await read()
    except Exception as exc:
        warnings.append(f"Could not download {label}: {exc}")
        return None
    return base64.b64encode(data).decode("ascii")


def _reaction_to_text(reaction: object | None) -> str | None:
    if reaction is None:
        return None
    emoji_id = getattr(reaction, "id", None)
    name = getattr(reaction, "name", None)
    if emoji_id:
        return f"{name or 'emoji'}:{emoji_id}"
    if name:
        return str(name)
    text = str(reaction)
    return text or None


def _forum_tags(channel: discord.abc.GuildChannel) -> list[ForumTagModel]:
    tags: list[ForumTagModel] = []
    for tag in getattr(channel, "available_tags", []) or []:
        emoji = getattr(tag, "emoji", None)
        tags.append(
            ForumTagModel(
                name=str(getattr(tag, "name", "tag"))[:20],
                emoji_name=getattr(emoji, "name", None) if emoji else None,
                emoji_id=getattr(emoji, "id", None) if emoji else None,
                moderated=bool(getattr(tag, "moderated", False)),
            )
        )
    return tags


def _permission_overwrites(
    channel: discord.abc.GuildChannel, warnings: list[str]
) -> list[PermissionOverwriteModel]:
    result: list[PermissionOverwriteModel] = []
    for target, overwrite in getattr(channel, "overwrites", {}).items():
        if isinstance(target, discord.Member):
            continue
        role_id = getattr(target, "id", None)
        if not role_id:
            continue
        result.append(
            PermissionOverwriteModel(
                target_id=int(role_id),
                allow=int(getattr(overwrite.allow, "value", overwrite.allow)),
                deny=int(getattr(overwrite.deny, "value", overwrite.deny)),
            )
        )
    return result


def _channel_payload(channel: discord.abc.GuildChannel, kind: ChannelKind) -> ChannelModel:
    parent_id = getattr(channel, "parent_id", None)
    if parent_id is None:
        category = getattr(channel, "category", None)
        parent_id = getattr(category, "id", None)
    return ChannelModel(
        id=int(channel.id),
        name=str(channel.name),
        kind=kind,
        position=int(getattr(channel, "position", 0)),
        parent_id=int(parent_id) if parent_id else None,
        topic=getattr(channel, "topic", None),
        nsfw=bool(getattr(channel, "nsfw", False)),
        slowmode=int(getattr(channel, "slowmode_delay", 0) or 0),
        bitrate=getattr(channel, "bitrate", None),
        user_limit=getattr(channel, "user_limit", None),
        rtc_region=getattr(channel, "rtc_region", None),
        video_quality_mode=(
            _enum_value(getattr(channel, "video_quality_mode", None), 1)
            if getattr(channel, "video_quality_mode", None) is not None
            else None
        ),
        default_auto_archive_duration=getattr(channel, "default_auto_archive_duration", None),
        default_thread_slowmode=getattr(channel, "default_thread_slowmode_delay", None),
        default_sort_order=(
            _enum_value(getattr(channel, "default_sort_order", None))
            if getattr(channel, "default_sort_order", None) is not None
            else None
        ),
        default_forum_layout=(
            _enum_value(getattr(channel, "default_layout", None))
            if getattr(channel, "default_layout", None) is not None
            else None
        ),
        default_reaction_emoji=_reaction_to_text(getattr(channel, "default_reaction_emoji", None)),
        available_tags=_forum_tags(channel),
        permission_overwrites=_permission_overwrites(channel, []),
    )


async def guild_to_backup(
    guild: discord.Guild,
    *,
    source: BackupSource,
    include_emojis: bool,
    logger: GuildForgeLogger,
) -> ExportResult:
    """Convert a discord.py Guild (real or template-provided) into a backup."""

    warnings: list[str] = []
    roles: list[RoleModel] = []

    for role in sorted(getattr(guild, "roles", []) or [], key=lambda item: item.position):
        icon = await _asset_base64(
            getattr(role, "icon", None), f"the icon of role '{role.name}'", warnings
        )
        roles.append(
            RoleModel(
                id=int(role.id),
                name=str(role.name)[:100],
                color=_flag_value(getattr(role, "color", 0)),
                hoist=bool(getattr(role, "hoist", False)),
                mentionable=bool(getattr(role, "mentionable", False)),
                permissions=_flag_value(getattr(role, "permissions", 0)),
                position=int(getattr(role, "position", 0)),
                managed=bool(getattr(role, "managed", False)),
                is_everyone=_is_default_role(role, guild),
                unicode_emoji=getattr(role, "unicode_emoji", None),
                icon=icon,
            )
        )

    channels: list[ChannelModel] = []
    for channel in getattr(guild, "channels", []) or []:
        kind = SUPPORTED_TYPES.get(getattr(channel, "type", None))
        if kind is None:
            warnings.append(f"Skipped unsupported channel '{channel.name}' ({channel.type}).")
            continue
        channels.append(_channel_payload(channel, kind))

    channels.sort(key=lambda item: item.position)

    emojis: list[EmojiModel] = []
    stickers: list[StickerModel] = []
    if include_emojis:
        for emoji in getattr(guild, "emojis", []) or []:
            try:
                data = await emoji.read()
            except Exception as exc:
                warnings.append(f"Could not download emoji '{emoji.name}': {exc}")
                continue
            emojis.append(
                EmojiModel(
                    id=int(emoji.id),
                    name=str(emoji.name),
                    image=base64.b64encode(data).decode("ascii"),
                    animated=bool(getattr(emoji, "animated", False)),
                    role_ids=[int(role.id) for role in getattr(emoji, "roles", [])],
                )
            )
        for sticker in getattr(guild, "stickers", []) or []:
            try:
                data = await sticker.read()
            except Exception as exc:
                warnings.append(f"Could not download sticker '{sticker.name}': {exc}")
                continue
            stickers.append(
                StickerModel(
                    id=int(sticker.id),
                    name=str(sticker.name),
                    description=str(getattr(sticker, "description", "") or ""),
                    tags=str(getattr(sticker, "tags", "") or ""),
                    format_type=_enum_value(getattr(sticker, "format", 1), 1),
                    image=base64.b64encode(data).decode("ascii"),
                )
            )
        if emojis or stickers:
            logger.debug(f"Exported {len(emojis)} emoji(s) and {len(stickers)} sticker(s).")

    settings = GuildSettings(
        description=getattr(guild, "description", None),
        verification_level=_enum_value(getattr(guild, "verification_level", 0)),
        default_notifications=_enum_value(getattr(guild, "default_notifications", 0)),
        explicit_content_filter=_enum_value(getattr(guild, "explicit_content_filter", 0), 0),
        afk_channel_id=getattr(getattr(guild, "afk_channel", None), "id", None),
        afk_timeout=int(getattr(guild, "afk_timeout", 300) or 300),
        system_channel_id=getattr(getattr(guild, "system_channel", None), "id", None),
        system_channel_flags=_enum_value(getattr(guild, "system_channel_flags", 0), 0),
        rules_channel_id=getattr(getattr(guild, "rules_channel", None), "id", None),
        public_updates_channel_id=getattr(
            getattr(guild, "public_updates_channel", None), "id", None
        ),
        preferred_locale=_locale_value(guild),
    )

    guild_id = int(getattr(guild, "id", 0) or 0)
    if guild_id <= 0:
        guild_id = 1
        warnings.append(
            "Discord did not expose the source server ID for this template; "
            "using a placeholder in the backup."
        )

    document = BackupDocument(
        source=source,
        guild=GuildModel(
            id=guild_id,
            name=str(guild.name)[:100],
            icon=await _asset_base64(getattr(guild, "icon", None), "the server icon", warnings),
            banner=await _asset_base64(
                getattr(guild, "banner", None), "the server banner", warnings
            ),
            splash=await _asset_base64(
                getattr(guild, "splash", None), "the invite splash", warnings
            ),
            premium_tier=int(getattr(guild, "premium_tier", 0) or 0),
            features=list(getattr(guild, "features", []) or []),
            settings=settings,
        ),
        roles=roles,
        channels=channels,
        emojis=emojis,
        stickers=stickers,
    )
    logger.debug(
        f"Read {len(roles)} role(s), {len(channels)} channel(s), "
        f"{len(emojis)} emoji(s), {len(stickers)} sticker(s) from '{guild.name}'."
    )
    return ExportResult(document=document, warnings=warnings)


async def read_guild_source(
    session: DiscordSession, guild_id: int, *, include_emojis: bool, logger: GuildForgeLogger
) -> ExportResult:
    guild = await session.fetch_guild(guild_id, role="source")
    source = BackupSource(type="guild", guild_id=guild.id, name=guild.name)
    return await guild_to_backup(guild, source=source, include_emojis=include_emojis, logger=logger)


async def read_template_source(
    session: DiscordSession,
    raw_source: str,
    *,
    include_emojis: bool,
    logger: GuildForgeLogger,
) -> ExportResult:
    code = parse_template_code(raw_source)
    if code is None:
        raise SourceError(f"Could not read a template code from '{raw_source}'.")
    template = await session.fetch_template(code, raw_source)
    source_guild = template.source_guild
    if source_guild is None:
        raise SourceError(
            f"Template '{code}' does not contain a server structure.",
            hint="The template may be corrupted. Open it in a browser to check it.",
        )
    source = BackupSource(
        type="template",
        guild_id=int(getattr(source_guild, "id", 0) or 0) or None,
        template_code=code,
        name=getattr(source_guild, "name", None),
    )
    result = await guild_to_backup(
        source_guild,
        source=source,
        include_emojis=include_emojis,
        logger=logger,
    )
    result.warnings.append(
        "Template mode copies the structure Discord stores in the template. Server "
        "settings, icon, banner, splash, emojis, and stickers are not available in templates."
    )
    return result


async def load_source(
    session: DiscordSession | None,
    raw_source: str,
    *,
    include_emojis: bool,
    logger: GuildForgeLogger,
) -> ExportResult:
    source = classify_source(raw_source)
    if source.kind == "file":
        if source.path is None:
            raise SourceError(f"Could not resolve backup path '{raw_source}'.")
        validation = load_backup(source.path)
        for warning in validation.warnings:
            logger.warn(warning)
        logger.debug(f"Loaded backup file {source.path}.")
        return ExportResult(document=validation.document, warnings=list(validation.warnings))

    if session is None:
        raise SourceError(
            "Reading a server or template requires a Discord bot token.",
            hint="Add DISCORD_TOKEN to .env or export the server to a JSON file first.",
        )

    if source.kind == "guild":
        from guildforge.config import normalize_guild_id

        result = await read_guild_source(
            session,
            normalize_guild_id(source.value),
            include_emojis=include_emojis,
            logger=logger,
        )
    else:
        result = await read_template_source(
            session, source.value, include_emojis=include_emojis, logger=logger
        )
    return result
