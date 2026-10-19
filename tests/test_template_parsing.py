"""Template link detection and template-to-backup conversion."""

from __future__ import annotations

import discord
import pytest

from guildforge.config import parse_template_code
from guildforge.discord_io.reader import classify_source, read_template_source
from guildforge.models.schema import ChannelKind


class FakeRole:
    def __init__(self, role_id: int, name: str, position: int, *, default: bool = False) -> None:
        self.id = role_id
        self.name = name
        self.position = position
        self.color = 0
        self.hoist = False
        self.mentionable = False
        self.permissions = 1024
        self.managed = False
        self.unicode_emoji = None
        self.icon = None
        self._default = default

    def is_default(self) -> bool:
        return self._default


class FakeChannel:
    def __init__(
        self,
        channel_id: int,
        name: str,
        kind: discord.ChannelType,
        position: int,
        parent_id: int | None = None,
    ) -> None:
        self.id = channel_id
        self.name = name
        self.type = kind
        self.position = position
        self.parent_id = parent_id
        self.overwrites: dict = {}
        self.nsfw = False
        self.slowmode_delay = 0


class FakeGuild:
    def __init__(self) -> None:
        self.id = 111111111111111111
        self.name = "Template Source"
        self.roles = [
            FakeRole(self.id, "@everyone", 0, default=True),
            FakeRole(222222222222222222, "Members", 1),
        ]
        self.channels = [
            FakeChannel(333333333333333333, "General", discord.ChannelType.category, 0),
            FakeChannel(
                444444444444444444,
                "chat",
                discord.ChannelType.text,
                1,
                parent_id=333333333333333333,
            ),
        ]
        self.emojis: list = []
        self.stickers: list = []
        self.icon = None
        self.banner = None
        self.splash = None
        self.description = None
        self.premium_tier = 0
        self.features: list[str] = []


class FakeTemplate:
    def __init__(self, source_guild: FakeGuild) -> None:
        self.source_guild = source_guild
        self.code = "abc123XYZ"


class FakeSession:
    def __init__(self, template: FakeTemplate) -> None:
        self.template = template

    async def fetch_template(self, code: str, raw_source: str) -> FakeTemplate:
        assert code == "abc123XYZ"
        return self.template


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://discord.new/abc123XYZ", "abc123XYZ"),
        ("discord.gg/template/abc123XYZ", "abc123XYZ"),
        ("https://discord.com/template/abc-123_XYZ", "abc-123_XYZ"),
        ("abc123XYZ", "abc123XYZ"),
        ("123456789012345678", None),
    ],
)
def test_parse_template_code(value: str, expected: str | None) -> None:
    assert parse_template_code(value) == expected


def test_classify_source_variants(tmp_path) -> None:
    assert classify_source("123456789012345678").kind == "guild"
    assert classify_source("https://discord.new/abc123XYZ").kind == "template"
    assert classify_source("abc123XYZ").kind == "template"

    backup = tmp_path / "backup.json"
    backup.write_text("{}", encoding="utf-8")
    classified = classify_source(str(backup))
    assert classified.kind == "file"
    assert classified.path == backup


def test_classify_source_rejects_nonsense() -> None:
    from guildforge.errors import SourceError

    with pytest.raises(SourceError):
        classify_source("!!!")


async def test_read_template_source_builds_document(logger) -> None:
    session = FakeSession(FakeTemplate(FakeGuild()))
    result = await read_template_source(
        session, "https://discord.new/abc123XYZ", include_emojis=False, logger=logger
    )
    document = result.document
    assert document.source.type == "template"
    assert document.source.template_code == "abc123XYZ"
    assert document.guild.name == "Template Source"
    assert document.everyone_role() is not None
    assert document.channel_by_id(444444444444444444).kind is ChannelKind.text
    assert document.channel_by_id(444444444444444444).parent_id == 333333333333333333
    assert any("Template mode" in warning for warning in result.warnings)
