"""Interactive prompt helpers used by the CLI and the double-click menu."""

from __future__ import annotations

import getpass
import sys

import typer

from guildforge.errors import ConfigError


def prompt_hidden(label: str) -> str:
    if not sys.stdin or not sys.stdin.isatty():
        raise ConfigError("A terminal is required to enter the bot token securely.")
    try:
        return getpass.getpass(f"{label}: ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise ConfigError("Token entry was cancelled.") from exc


def confirm(question: str, *, default: bool = False, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    return typer.confirm(question, default=default)


def prompt_text(label: str, *, default: str | None = None) -> str:
    return typer.prompt(label, default=default) if default is not None else typer.prompt(label)


def prompt_server_name(target_name: str) -> bool:
    """Second confirmation for --clear-target."""

    typer.echo(f"This will DELETE every channel and every non-managed role in '{target_name}'.")
    typed = typer.prompt(f'Type "{target_name}" to confirm')
    return typed.strip() == target_name
