"""Friendly, user-facing exceptions used across GuildForge."""

from __future__ import annotations


class GuildForgeError(Exception):
    """Base class for all expected GuildForge errors."""

    exit_code = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class ConfigError(GuildForgeError):
    """The local configuration (token, paths, options) is invalid."""


class SourceError(GuildForgeError):
    """The source server, template, or backup file could not be read."""


class BackupError(GuildForgeError):
    """A backup file is missing, unreadable, or does not match the schema."""


class TargetError(GuildForgeError):
    """The target server cannot be used for this operation."""


class PermissionCheckError(TargetError):
    """The bot is missing a permission required for the operation."""


class LimitError(GuildForgeError):
    """The plan exceeds a hard Discord limit."""


class TemplateError(SourceError):
    """A Discord template is invalid or could not be resolved."""


class DiscordAPIError(GuildForgeError):
    """A Discord API call failed in a way the user should know about."""
