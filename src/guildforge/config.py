"""Runtime configuration: token discovery, paths, and app constants."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from guildforge.errors import ConfigError

APP_NAME = "GuildForge"
ENV_VAR = "DISCORD_TOKEN"
DEFAULT_BACKUP_NAME = "backup.json"
DEFAULT_STATE_FILE = ".guildforge-state.json"
STATE_SCHEMA_VERSION = 1
PROJECT_URL = "https://github.com/Ziadtareks/GuildForge"

GUILD_ID_RE = re.compile(r"^\d{17,20}$")
TEMPLATE_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord\.com/template|discord\.gg/template|discord\.new)/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
TEMPLATE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")


def _candidate_env_files() -> list[Path]:
    candidates = [Path.cwd() / ".env"]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / ".env")
    candidates.append(Path.home() / ".guildforge" / ".env")
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def load_env_files() -> list[Path]:
    """Load dotenv files without overriding variables already in the environment."""

    loaded: list[Path] = []
    for path in _candidate_env_files():
        if path.is_file():
            load_dotenv(path, override=False)
            loaded.append(path)
    return loaded


def read_token_from_env() -> str | None:
    load_env_files()
    token = os.environ.get(ENV_VAR, "").strip()
    return token or None


def resolve_token(*, prompt_if_missing: bool = True) -> str:
    """Return the bot token from .env or a hidden prompt. Never persist it."""

    token = read_token_from_env()
    if token:
        return token

    if not prompt_if_missing or not sys.stdin or not sys.stdin.isatty():
        raise ConfigError(
            "No Discord bot token was found.",
            hint=(
                "Create a file named .env next to the executable (or in the current folder) "
                "with DISCORD_TOKEN=your-bot-token, or run the command in a terminal so "
                "GuildForge can ask for it."
            ),
        )

    from guildforge.utils.prompts import prompt_hidden

    token = prompt_hidden("Discord bot token (input is hidden)")
    if not token:
        raise ConfigError("A Discord bot token is required.")
    return token


def normalize_guild_id(value: str | int) -> int:
    text = str(value).strip()
    if not GUILD_ID_RE.match(text):
        raise ConfigError(
            f"'{value}' is not a valid Discord server ID.",
            hint=(
                "Enable Settings > Advanced > Developer Mode in Discord, right-click the "
                "server icon, and choose Copy Server ID (17-20 digits)."
            ),
        )
    return int(text)


def parse_template_code(value: str) -> str | None:
    """Return a template code from a URL or raw code, else None."""

    text = value.strip()
    match = TEMPLATE_URL_RE.search(text)
    if match:
        return match.group(1)
    if TEMPLATE_CODE_RE.match(text) and not GUILD_ID_RE.match(text):
        return text
    return None


def state_path(base_dir: Path | None = None) -> Path:
    return (base_dir or Path.cwd()) / DEFAULT_STATE_FILE


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))
