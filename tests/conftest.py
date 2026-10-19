"""Shared pytest fixtures. No Discord token or network access is required."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from guildforge.utils.logger import GuildForgeLogger

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_BACKUP = ROOT / "examples" / "sample_backup.json"


@pytest.fixture
def sample_backup_path() -> Path:
    assert SAMPLE_BACKUP.is_file(), f"missing sample backup: {SAMPLE_BACKUP}"
    return SAMPLE_BACKUP


@pytest.fixture
def logger() -> GuildForgeLogger:
    return GuildForgeLogger(
        console=Console(file=io.StringIO(), width=120, no_color=True),
        quiet=True,
    )
