"""Discord hard-limit checks performed before any write happens."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from guildforge.models.schema import BackupDocument, ChannelKind

MAX_ROLES = 250
MAX_CHANNELS = 500
MAX_CHANNELS_PER_CATEGORY = 50
BASE_EMOJI_SLOTS = 50
BASE_STICKER_SLOTS = 5


@dataclass
class TargetSnapshot:
    """What GuildForge knows about the destination server."""

    guild_id: int = 0
    name: str = "unknown target"
    roles: int = 0
    clearable_roles: int = 0
    channels: int = 0
    emojis: int = 0
    stickers: int = 0
    boost_level: int = 0
    features: set[str] = field(default_factory=set)

    @property
    def emoji_slots(self) -> int:
        return BASE_EMOJI_SLOTS + BASE_EMOJI_SLOTS * self.boost_level

    @property
    def sticker_slots(self) -> int:
        return BASE_STICKER_SLOTS + BASE_STICKER_SLOTS * self.boost_level

    @property
    def has_community(self) -> bool:
        return "COMMUNITY" in self.features


@dataclass(frozen=True)
class LimitIssue:
    severity: Literal["warning", "error"]
    message: str


def count_roles_to_create(document: BackupDocument) -> int:
    return sum(1 for role in document.roles if not role.is_everyone and not role.managed)


def count_channels_to_create(document: BackupDocument) -> int:
    return len(document.channels)


def effective_role_count(target: TargetSnapshot, *, clearing: bool) -> int:
    if clearing:
        return max(0, target.roles - target.clearable_roles)
    return target.roles


def effective_channel_count(target: TargetSnapshot, *, clearing: bool) -> int:
    return 0 if clearing else target.channels


def check_limits(
    document: BackupDocument,
    target: TargetSnapshot,
    *,
    include_emojis: bool = False,
    clearing: bool = False,
) -> list[LimitIssue]:
    """Return warnings/errors for a planned copy. Errors are fatal before writes."""

    issues: list[LimitIssue] = []
    roles_to_create = count_roles_to_create(document)
    channels_to_create = count_channels_to_create(document)
    role_total = effective_role_count(target, clearing=clearing) + roles_to_create
    channel_total = effective_channel_count(target, clearing=clearing) + channels_to_create

    if role_total > MAX_ROLES:
        issues.append(
            LimitIssue(
                "error",
                f"Role limit exceeded: {role_total}/{MAX_ROLES} after the copy "
                f"(target has {effective_role_count(target, clearing=clearing)}, "
                f"backup adds {roles_to_create}).",
            )
        )

    if channel_total > MAX_CHANNELS:
        issues.append(
            LimitIssue(
                "error",
                f"Channel limit exceeded: {channel_total}/{MAX_CHANNELS} after the copy "
                f"(target has {effective_channel_count(target, clearing=clearing)}, "
                f"backup adds {channels_to_create}).",
            )
        )

    for category in document.categories():
        child_count = len(document.children_of(category.id))
        if child_count > MAX_CHANNELS_PER_CATEGORY:
            issues.append(
                LimitIssue(
                    "error",
                    f"Category '{category.name}' has {child_count} channels; the Discord "
                    f"maximum is {MAX_CHANNELS_PER_CATEGORY}.",
                )
            )

    if include_emojis:
        if not document.emojis:
            issues.append(LimitIssue("warning", "No emojis in the backup to copy."))
        elif target.emojis + len(document.emojis) > target.emoji_slots:
            issues.append(
                LimitIssue(
                    "warning",
                    f"Emoji slots: target has {target.emojis}/{target.emoji_slots} used. "
                    f"Only the first {max(0, target.emoji_slots - target.emojis)} of "
                    f"{len(document.emojis)} emojis will fit and the rest will be skipped.",
                )
            )
        if document.stickers and target.stickers + len(document.stickers) > target.sticker_slots:
            issues.append(
                LimitIssue(
                    "warning",
                    f"Sticker slots: target has {target.stickers}/{target.sticker_slots} used. "
                    "Stickers that do not fit will be skipped.",
                )
            )

    if document.guild.banner and "BANNER" not in target.features:
        issues.append(
            LimitIssue(
                "warning",
                f"Target server '{target.name}' cannot use a banner at boost level "
                f"{target.boost_level}. The banner will be skipped.",
            )
        )
    if document.guild.splash and "INVITE_SPLASH" not in target.features:
        issues.append(
            LimitIssue(
                "warning",
                f"Target server '{target.name}' cannot use an invite splash. It will be skipped.",
            )
        )

    if not target.has_community:
        special = [channel for channel in document.channels if channel.kind.requires_community]
        if special:
            names = ", ".join(channel.name for channel in special[:5])
            extra = "" if len(special) <= 5 else f" and {len(special) - 5} more"
            issues.append(
                LimitIssue(
                    "warning",
                    "Community is not enabled on the target. Announcement, stage, forum, and "
                    f"media channels will fall back to text/voice channels: {names}{extra}.",
                )
            )
        settings = document.guild.settings
        if settings.rules_channel_id or settings.public_updates_channel_id:
            issues.append(
                LimitIssue(
                    "warning",
                    "Rules/updates channels require Community on the target; those settings "
                    "will be skipped.",
                )
            )

    if clearing and target.roles - target.clearable_roles > 1:
        issues.append(
            LimitIssue(
                "warning",
                f"{target.roles - target.clearable_roles - 1} target role(s) cannot be deleted "
                "(managed or above the bot role). They are left in place.",
            )
        )

    return issues


def hard_errors(issues: list[LimitIssue]) -> list[str]:
    return [issue.message for issue in issues if issue.severity == "error"]


def channel_fallbacks(document: BackupDocument, target: TargetSnapshot) -> list[str]:
    """Names of channels that will use a fallback kind on the target."""

    if target.has_community:
        return []
    return [
        channel.name
        for channel in document.channels
        if channel.kind
        in {ChannelKind.news, ChannelKind.stage, ChannelKind.forum, ChannelKind.media}
    ]
