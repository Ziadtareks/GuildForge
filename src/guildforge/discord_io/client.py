"""Discord REST session management and pre-flight checks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import aiohttp
import discord

from guildforge.config import PROJECT_URL
from guildforge.errors import (
    ConfigError,
    DiscordAPIError,
    PermissionCheckError,
    SourceError,
    TargetError,
    TemplateError,
)
from guildforge.models.limits import TargetSnapshot
from guildforge.utils.logger import GuildForgeLogger
from guildforge.utils.retry import with_retry


@dataclass
class TargetContext:
    guild: discord.Guild
    member: discord.Member
    snapshot: TargetSnapshot

    @property
    def boost_level(self) -> int:
        return self.snapshot.boost_level


def invite_url(bot_id: int) -> str:
    return (
        f"https://discord.com/oauth2/authorize?client_id={bot_id}"
        "&permissions=8&scope=bot%20applications.commands"
    )


class DiscordSession:
    """A minimal REST-only discord.py client with friendly error translation."""

    def __init__(self, token: str, logger: GuildForgeLogger) -> None:
        self._token = token
        self.logger = logger
        intents = discord.Intents.none()
        intents.guilds = True
        self.client = discord.Client(intents=intents)
        self._logged_in = False

    async def __aenter__(self) -> DiscordSession:
        self.logger.add_secret(self._token)
        try:
            await self.client.login(self._token)
        except discord.LoginFailure as exc:
            raise ConfigError(
                "Discord rejected the bot token.",
                hint=(
                    "Copy the token again from the Bot tab of the Discord Developer Portal. "
                    "Do not use a user token or a client secret. If you reset the token, "
                    "update .env."
                ),
            ) from exc
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise DiscordAPIError(
                f"Could not reach Discord: {exc}",
                hint="Check your internet connection, VPN, or firewall and try again.",
            ) from exc
        self._logged_in = True
        self.logger.add_secret(self._token)
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._logged_in:
            await self.client.close()
            self._logged_in = False

    @property
    def bot_id(self) -> int:
        if self.client.user is None:
            raise ConfigError("The Discord client is not logged in.")
        return self.client.user.id

    async def fetch_guild(self, guild_id: int, *, role: str) -> discord.Guild:
        try:
            return await with_retry(
                lambda: self.client.fetch_guild(guild_id, with_counts=True),
                description=f"Fetching {role} server {guild_id}",
                logger=self.logger,
            )
        except discord.Forbidden as exc:
            error = SourceError if role == "source" else TargetError
            raise error(
                f"The bot cannot access the {role} server {guild_id}.",
                hint=(
                    "Invite the bot to that server first, and make sure the server ID is "
                    "correct. To copy a server you cannot add a bot to, use template mode: "
                    "--source https://discord.com/template/CODE"
                ),
            ) from exc
        except discord.NotFound as exc:
            raise TargetError(
                f"The {role} server {guild_id} was not found.",
                hint="Check the server ID. The bot must be a member of the server.",
            ) from exc
        except discord.HTTPException as exc:
            raise DiscordAPIError(
                f"Discord returned HTTP {exc.status} while fetching the {role} server."
            ) from exc

    async def fetch_template(self, code: str, raw_source: str) -> discord.Template:
        try:
            return await with_retry(
                lambda: self.client.fetch_template(code),
                description=f"Fetching template {code}",
                logger=self.logger,
            )
        except discord.NotFound as exc:
            raise TemplateError(
                f"Template '{code}' was not found or has expired.",
                hint=(
                    "Open the template link in a browser to confirm it still works. "
                    "Templates expire if they are not used for a long time."
                ),
            ) from exc
        except discord.HTTPException as exc:
            raise TemplateError(
                f"Discord rejected template '{raw_source}' (HTTP {exc.status}).",
                hint="Double-check the link. It should look like https://discord.new/abc123.",
            ) from exc

    async def fetch_target(self, guild_id: int) -> TargetContext:
        guild = await self.fetch_guild(guild_id, role="target")
        try:
            member = await with_retry(
                lambda: guild.fetch_member(self.bot_id),
                description="Checking the bot member in the target server",
                logger=self.logger,
            )
        except discord.NotFound as exc:
            raise TargetError(
                "The bot is not a member of the target server.",
                hint=f"Invite it with Administrator permissions: {invite_url(self.bot_id)}",
            ) from exc
        except discord.Forbidden as exc:
            raise TargetError(
                "Discord denied access to the bot's member record in the target server.",
                hint=f"Re-invite the bot with Administrator permissions: {invite_url(self.bot_id)}",
            ) from exc

        if not member.guild_permissions.administrator:
            raise PermissionCheckError(
                "The bot does not have Administrator permission on the target server.",
                hint=(
                    "Server Settings > Roles > (bot role) > enable Administrator, then rerun. "
                    f"Invite link with Administrator: {invite_url(self.bot_id)}"
                ),
            )

        clearable_roles = 0
        for role in guild.roles:
            if role.is_default() or role.managed:
                continue
            if role.position < member.top_role.position:
                clearable_roles += 1

        snapshot = TargetSnapshot(
            guild_id=guild.id,
            name=guild.name,
            roles=len(guild.roles),
            clearable_roles=clearable_roles,
            channels=len(guild.channels),
            emojis=len(guild.emojis),
            stickers=len(guild.stickers),
            boost_level=guild.premium_tier,
            features=set(guild.features),
        )
        return TargetContext(guild=guild, member=member, snapshot=snapshot)

    async def list_guilds(self) -> list[discord.Guild]:
        guilds: list[discord.Guild] = []
        try:
            async for guild in self.client.fetch_guilds(with_counts=True, limit=None):
                guilds.append(guild)
        except discord.HTTPException as exc:
            raise DiscordAPIError(
                f"Discord returned HTTP {exc.status} while listing servers."
            ) from exc
        return sorted(guilds, key=lambda item: item.name.casefold())

    async def validate_token(self) -> discord.User:
        if self.client.user is None:
            raise ConfigError("The Discord client is not logged in.")
        return self.client.user

    def role_hierarchy_warning(self, target: TargetContext) -> str | None:
        highest = max((role.position for role in target.guild.roles), default=0)
        if target.member.top_role.position < highest:
            return (
                f"The bot's top role '{target.member.top_role.name}' is not the highest role "
                "in the target server. Move the bot role to the top in Server Settings > Roles "
                "so it can create and position every role."
            )
        return None

    def docs_url(self) -> str:
        return PROJECT_URL
