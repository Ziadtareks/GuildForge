"""GuildForge command-line interface."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback
from collections.abc import Awaitable
from pathlib import Path

import questionary
import typer
from rich.console import Console
from rich.panel import Panel

from guildforge import __version__
from guildforge.config import (
    DEFAULT_BACKUP_NAME,
    PROJECT_URL,
    normalize_guild_id,
    read_token_from_env,
    resolve_token,
    state_path,
)
from guildforge.discord_io.client import DiscordSession, TargetContext
from guildforge.discord_io.reader import (
    ExportResult,
    classify_source,
    load_source,
)
from guildforge.discord_io.writer import (
    ApplyPlan,
    ApplyState,
    GuildWriter,
    build_plan,
    state_summary,
)
from guildforge.errors import (
    GuildForgeError,
    LimitError,
)
from guildforge.models.limits import (
    LimitIssue,
    TargetSnapshot,
    check_limits,
    hard_errors,
)
from guildforge.models.schema import BackupDocument
from guildforge.models.validation import load_backup
from guildforge.utils.logger import GuildForgeLogger
from guildforge.utils.prompts import confirm, prompt_server_name

app = typer.Typer(
    name="guildforge",
    help=(
        "Copy a Discord server's structure to your own server in one command.\n\n"
        "Only an official Discord BOT token is used. Messages, members, webhooks, and "
        "member-specific permission overwrites are never copied."
    ),
    add_completion=False,
    no_args_is_help=False,
    invoke_without_command=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"], "max_content_width": 100},
)
console = Console(highlight=False)


# --------------------------------------------------------------------------- #
# Shared runtime helpers
# --------------------------------------------------------------------------- #
def _make_logger(verbose: bool) -> GuildForgeLogger:
    return GuildForgeLogger(console=console, verbose=verbose)


def _run(operation: Awaitable[None], *, verbose: bool) -> None:
    """Execute an async operation with friendly error handling."""

    try:
        asyncio.run(operation)
    except GuildForgeError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[dim]{exc.hint}[/dim]")
        raise typer.Exit(exc.exit_code) from exc
    except KeyboardInterrupt as exc:
        console.print(
            "\n[yellow]Interrupted.[/yellow] Progress is saved. Rerun the same command "
            "to continue; GuildForge will offer to resume."
        )
        raise typer.Exit(130) from exc
    except Exception as exc:
        console.print(f"[bold red]Unexpected error:[/bold red] {exc}")
        if verbose:
            traceback.print_exc()
        else:
            console.print(
                "[dim]Rerun with --verbose to see the full traceback. If this is a bug, "
                f"please report it at {PROJECT_URL}/issues.[/dim]"
            )
        raise typer.Exit(1) from exc


def _write_json_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as temp_file:
            temp_file.write(text)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _print_validation_warnings(result: ExportResult, logger: GuildForgeLogger) -> None:
    for warning in result.warnings:
        logger.warn(warning)


def _source_label(document: BackupDocument) -> str:
    source_name = document.source.name or document.guild.name
    if document.source.type == "template" and document.source.template_code:
        return f"{source_name} (template {document.source.template_code})"
    if document.source.type == "guild" and document.source.guild_id:
        return f"{source_name} ({document.source.guild_id})"
    return f"{source_name} (backup file)"


def _print_plan(
    document: BackupDocument,
    plan: ApplyPlan,
    *,
    target_name: str,
    offline: bool,
    clear_target: bool,
    logger: GuildForgeLogger,
) -> None:
    rows = [
        ["Source", _source_label(document)],
        ["Target", target_name + (" [offline preview]" if offline else "")],
        ["Roles to create", str(plan.roles_create)],
        ["Categories to create", str(plan.categories_create)],
        ["Channels to create", str(plan.channels_create)],
        ["Emojis to copy", str(plan.emojis_create)],
        ["Stickers to copy", str(plan.stickers_create)],
    ]
    if clear_target:
        rows.append(["Roles to delete", str(plan.roles_delete)])
        rows.append(["Channels to delete", str(plan.channels_delete)])
    logger.table("Copy plan", ["Item", "Value"], rows)
    for note in plan.notes:
        logger.warn(note)


def _print_issues(issues: list[LimitIssue], logger: GuildForgeLogger) -> None:
    for issue in issues:
        logger.warn(issue.message)


def _offline_snapshot(target_id: int) -> TargetSnapshot:
    return TargetSnapshot(
        guild_id=target_id,
        name="(not inspected, offline preview)",
        roles=0,
        clearable_roles=0,
        channels=0,
        emojis=0,
        stickers=0,
        boost_level=0,
        features=set(),
    )


def _require_hard_limits(issues: list[LimitIssue]) -> None:
    errors = hard_errors(issues)
    if errors:
        raise LimitError(
            "This copy would exceed Discord's hard limits:\n- " + "\n- ".join(errors),
            hint=(
                "Use --clear-target to remove existing roles/channels first, or remove items "
                "from the source/backup. Nothing was changed."
            ),
        )


async def _load_state_for_target(
    document: BackupDocument,
    target_id: int,
    *,
    clear_target: bool,
    assume_yes: bool,
    logger: GuildForgeLogger,
) -> tuple[ApplyState, Path]:
    path = state_path()
    if clear_target:
        if path.exists():
            logger.warn("Existing resume state ignored because --clear-target was used.")
        return ApplyState.new(target_id, document.digest()), path

    state = ApplyState.load(path, target_guild_id=target_id, digest=document.digest())
    if state is not None:
        logger.info(f"Saved progress found: {state_summary(state)}.")
        if assume_yes or confirm("Resume the previous run?", default=True):
            return state, path
        logger.info("Starting a fresh run and overwriting the saved progress.")
        return ApplyState.new(target_id, document.digest()), path

    if path.exists():
        logger.warn(
            "A state file exists but belongs to a different target server or backup; "
            "starting fresh."
        )
    return ApplyState.new(target_id, document.digest()), path


async def _apply_document(
    session: DiscordSession,
    document: BackupDocument,
    target: TargetContext,
    *,
    dry_run: bool,
    clear_target: bool,
    include_emojis: bool,
    assume_yes: bool,
    logger: GuildForgeLogger,
) -> None:
    plan = build_plan(
        document,
        target.snapshot,
        clear_target=clear_target,
        include_emojis=include_emojis,
    )
    issues = check_limits(
        document,
        target.snapshot,
        include_emojis=include_emojis,
        clearing=clear_target,
    )
    _print_plan(
        document,
        plan,
        target_name=f"{target.guild.name} ({target.guild.id})",
        offline=False,
        clear_target=clear_target,
        logger=logger,
    )
    _print_issues(issues, logger)

    hierarchy = session.role_hierarchy_warning(target)
    if hierarchy:
        logger.warn(hierarchy)

    _require_hard_limits(issues)

    if dry_run:
        logger.success("Dry run complete. No changes were written to Discord.")
        return

    if not confirm(
        f"Copy the structure into '{target.guild.name}'?",
        default=False,
        assume_yes=assume_yes,
    ):
        logger.info("Cancelled. Nothing was changed.")
        return

    if clear_target:
        if assume_yes:
            logger.warn(
                "--clear-target with --yes: deleting target channels and non-managed roles "
                "without the typed-name confirmation."
            )
        elif not prompt_server_name(target.guild.name):
            logger.error("The server name did not match. Cancelled. Nothing was deleted.")
            raise typer.Exit(1)

    state, path = await _load_state_for_target(
        document,
        target.guild.id,
        clear_target=clear_target,
        assume_yes=assume_yes,
        logger=logger,
    )
    logger.info(f"Resume state: {path}")

    writer = GuildWriter(
        target=target.guild,
        bot_member=target.member,
        document=document,
        logger=logger,
        options_clear_target=clear_target,
        options_emojis=include_emojis,
        state=state,
        state_path=path,
    )
    report = await writer.run()

    if report.failed:
        logger.warn(
            f"{len(report.failed)} step(s) failed. Progress is saved at {path}. "
            "Rerun the same command to resume."
        )
        raise typer.Exit(2)

    try:
        if path.exists():
            path.unlink()
    except OSError as exc:
        logger.warn(f"Copy succeeded, but the state file could not be removed: {exc}")
    logger.success(f"Done. GuildForge {__version__} copied {len(report.created)} item(s).")


async def _run_offline_preview(
    document: BackupDocument,
    target_id: int,
    *,
    clear_target: bool,
    include_emojis: bool,
    logger: GuildForgeLogger,
) -> None:
    snapshot = _offline_snapshot(target_id)
    plan = build_plan(
        document,
        snapshot,
        clear_target=clear_target,
        include_emojis=include_emojis,
    )
    issues = check_limits(
        document,
        snapshot,
        include_emojis=include_emojis,
        clearing=clear_target,
    )
    logger.warn(
        "Offline preview: no DISCORD_TOKEN was found, so the target server was not "
        "inspected. Target-specific warnings below assume an empty boost-level-0 server."
    )
    _print_plan(
        document,
        plan,
        target_name=f"server {target_id}",
        offline=True,
        clear_target=clear_target,
        logger=logger,
    )
    _print_issues(issues, logger)
    _require_hard_limits(issues)
    logger.success("Dry run complete. Nothing was sent to Discord.")


# --------------------------------------------------------------------------- #
# Operations (also used by the interactive menu)
# --------------------------------------------------------------------------- #
async def perform_export(
    source: str,
    out: Path,
    *,
    include_emojis: bool,
    assume_yes: bool,
    logger: GuildForgeLogger,
) -> None:
    source_input = classify_source(source)
    if source_input.kind == "file":
        async with _session_or_none(None) as session:
            result = await load_source(
                session, source, include_emojis=include_emojis, logger=logger
            )
    else:
        token = resolve_token()
        async with DiscordSession(token, logger) as session:
            result = await load_source(
                session, source, include_emojis=include_emojis, logger=logger
            )

    _print_validation_warnings(result, logger)
    destination = out.expanduser()
    if (
        destination.exists()
        and not assume_yes
        and not confirm(f"{destination} already exists. Overwrite it?", default=False)
    ):
        logger.info("Cancelled. The existing file was not changed.")
        return

    document = result.document
    _write_json_atomic(destination, document.to_json())
    summary = document.roster_summary()
    logger.success(f"Backup written to {destination.resolve()}")
    logger.table(
        "Backup contents",
        ["Item", "Count"],
        [
            ["Roles", str(summary["roles"])],
            ["Categories", str(summary["categories"])],
            ["Channels", str(summary["channels"])],
            ["Emojis", str(summary["emojis"])],
            ["Stickers", str(summary["stickers"])],
            ["Schema version", str(document.schema_version)],
        ],
    )


class _session_or_none:
    """Tiny async context manager so file-only flows share one code path."""

    def __init__(self, session: DiscordSession | None) -> None:
        self.session = session

    async def __aenter__(self) -> DiscordSession | None:
        return self.session

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


async def perform_import(
    file: Path,
    target: str,
    *,
    dry_run: bool,
    clear_target: bool,
    include_emojis: bool,
    assume_yes: bool,
    logger: GuildForgeLogger,
) -> None:
    target_id = normalize_guild_id(target)
    validation = load_backup(file)
    document = validation.document
    _print_validation_warnings(ExportResult(document, validation.warnings), logger)

    token = read_token_from_env()
    if dry_run and not token:
        await _run_offline_preview(
            document,
            target_id,
            clear_target=clear_target,
            include_emojis=include_emojis,
            logger=logger,
        )
        return

    token = resolve_token(prompt_if_missing=not dry_run)
    async with DiscordSession(token, logger) as session:
        target_ctx = await session.fetch_target(target_id)
        await _apply_document(
            session,
            document,
            target_ctx,
            dry_run=dry_run,
            clear_target=clear_target,
            include_emojis=include_emojis,
            assume_yes=assume_yes,
            logger=logger,
        )


async def perform_clone(
    source: str,
    target: str,
    *,
    dry_run: bool,
    clear_target: bool,
    include_emojis: bool,
    assume_yes: bool,
    logger: GuildForgeLogger,
) -> None:
    target_id = normalize_guild_id(target)
    source_input = classify_source(source)
    token = read_token_from_env()

    if source_input.kind == "file" and dry_run and not token:
        validation = load_backup(source_input.path)  # type: ignore[arg-type]
        _print_validation_warnings(ExportResult(validation.document, validation.warnings), logger)
        await _run_offline_preview(
            validation.document,
            target_id,
            clear_target=clear_target,
            include_emojis=include_emojis,
            logger=logger,
        )
        return

    token = resolve_token(prompt_if_missing=not dry_run)
    async with DiscordSession(token, logger) as session:
        result = await load_source(session, source, include_emojis=include_emojis, logger=logger)
        _print_validation_warnings(result, logger)
        target_ctx = await session.fetch_target(target_id)
        await _apply_document(
            session,
            result.document,
            target_ctx,
            dry_run=dry_run,
            clear_target=clear_target,
            include_emojis=include_emojis,
            assume_yes=assume_yes,
            logger=logger,
        )


async def perform_list(logger: GuildForgeLogger) -> None:
    token = resolve_token()
    async with DiscordSession(token, logger) as session:
        guilds = await session.list_guilds()
    if not guilds:
        logger.info("The bot is not a member of any servers.")
        return
    rows = []
    for guild in guilds:
        owner = f"<@{guild.owner_id}>" if getattr(guild, "owner_id", None) else "?"
        rows.append(
            [
                guild.name,
                str(guild.id),
                str(getattr(guild, "member_count", None) or "?"),
                owner,
            ]
        )
    logger.table("Servers the bot is in", ["Name", "ID", "Members", "Owner"], rows)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
@app.callback()
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the GuildForge version and exit.",
        is_eager=True,
    ),
) -> None:
    """GuildForge: copy a Discord server's structure to your own server."""

    if version:
        console.print(f"GuildForge {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        if not sys.stdin or not sys.stdin.isatty():
            console.print(ctx.get_help())
            raise typer.Exit()
        interactive_menu()


@app.command("export")
def export_command(
    source: str = typer.Option(
        ...,
        "--source",
        "-s",
        help="Source server ID, template link/code, or a GuildForge JSON backup.",
    ),
    out: Path = typer.Option(
        Path(DEFAULT_BACKUP_NAME),
        "--out",
        "-o",
        help="Where to write the backup JSON file.",
        dir_okay=False,
        writable=True,
    ),
    emojis: bool = typer.Option(
        False, "--emojis", help="Include emojis and stickers in the backup (large files)."
    ),
    yes: bool = typer.Option(False, "--yes", help="Overwrite the output file without asking."),
    verbose: bool = typer.Option(False, "--verbose", help="Show detailed progress and tracebacks."),
) -> None:
    """Export a server's structure to a JSON backup file."""

    logger = _make_logger(verbose)
    _run(
        perform_export(
            source,
            out,
            include_emojis=emojis,
            assume_yes=yes,
            logger=logger,
        ),
        verbose=verbose,
    )


@app.command("import")
def import_command(
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="GuildForge backup JSON file to import.",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    target: str = typer.Option(
        ..., "--target", "-t", help="Destination server ID (the bot must be an Administrator)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Validate and show the plan without writing to Discord."
    ),
    clear_target: bool = typer.Option(
        False,
        "--clear-target",
        help="DELETE existing channels and non-managed roles on the target before copying.",
    ),
    emojis: bool = typer.Option(
        False, "--emojis", help="Also copy emojis and stickers from the backup."
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip the plan confirmation prompt."),
    verbose: bool = typer.Option(False, "--verbose", help="Show detailed progress and tracebacks."),
) -> None:
    """Import a JSON backup into a server."""

    logger = _make_logger(verbose)
    _run(
        perform_import(
            file,
            target,
            dry_run=dry_run,
            clear_target=clear_target,
            include_emojis=emojis,
            assume_yes=yes,
            logger=logger,
        ),
        verbose=verbose,
    )


@app.command("clone")
def clone_command(
    source: str = typer.Option(
        ...,
        "--source",
        "-s",
        help="Source server ID, template link/code, or a GuildForge JSON backup.",
    ),
    target: str = typer.Option(
        ..., "--target", "-t", help="Destination server ID (the bot must be an Administrator)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Validate and show the plan without writing to Discord."
    ),
    clear_target: bool = typer.Option(
        False,
        "--clear-target",
        help="DELETE existing channels and non-managed roles on the target before copying.",
    ),
    emojis: bool = typer.Option(
        False, "--emojis", help="Also copy emojis and stickers (when the source supports it)."
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip the plan confirmation prompt."),
    verbose: bool = typer.Option(False, "--verbose", help="Show detailed progress and tracebacks."),
) -> None:
    """Copy one server's structure directly into another server."""

    logger = _make_logger(verbose)
    _run(
        perform_clone(
            source,
            target,
            dry_run=dry_run,
            clear_target=clear_target,
            include_emojis=emojis,
            assume_yes=yes,
            logger=logger,
        ),
        verbose=verbose,
    )


@app.command("list")
def list_command(
    verbose: bool = typer.Option(False, "--verbose", help="Show detailed tracebacks."),
) -> None:
    """List the servers the bot is a member of."""

    logger = _make_logger(verbose)
    _run(perform_list(logger), verbose=verbose)


# --------------------------------------------------------------------------- #
# Interactive menu (double-click friendly)
# --------------------------------------------------------------------------- #
def interactive_menu() -> None:
    console.print(
        Panel.fit(
            f"[bold cyan]GuildForge {__version__}[/bold cyan]\n"
            "Copy a Discord server's structure to your own server.\n"
            "[dim]Bot token only. Messages, members, webhooks, and integrations are never copied.[/dim]",
            border_style="cyan",
        )
    )
    while True:
        try:
            choice = questionary.select(
                "What do you want to do?",
                choices=[
                    questionary.Choice("Clone a server into another server", value="clone"),
                    questionary.Choice("Export a server to a JSON backup", value="export"),
                    questionary.Choice("Import a JSON backup into a server", value="import"),
                    questionary.Choice("List servers the bot is in", value="list"),
                    questionary.Choice("Exit", value="exit"),
                ],
            ).ask()
            if choice is None or choice == "exit":
                console.print("Bye.")
                return

            logger = _make_logger(verbose=False)
            if choice == "clone":
                source = questionary.text(
                    "Source server ID, template link, or backup .json path:"
                ).ask()
                if not source:
                    continue
                target = questionary.text("Target server ID:").ask()
                if not target:
                    continue
                emojis = bool(
                    questionary.confirm("Copy emojis and stickers too?", default=False).ask()
                )
                dry_run = bool(questionary.confirm("Dry run (preview only)?", default=False).ask())
                clear_target = bool(
                    questionary.confirm(
                        "Clear the target server first (deletes channels and roles)?",
                        default=False,
                    ).ask()
                )
                _run(
                    perform_clone(
                        source,
                        target,
                        dry_run=dry_run,
                        clear_target=clear_target,
                        include_emojis=emojis,
                        assume_yes=False,
                        logger=logger,
                    ),
                    verbose=False,
                )
            elif choice == "export":
                source = questionary.text("Source server ID or template link/code:").ask()
                if not source:
                    continue
                out_text = questionary.text("Output file:", default=DEFAULT_BACKUP_NAME).ask()
                if not out_text:
                    continue
                emojis = bool(
                    questionary.confirm(
                        "Include emojis and stickers in the backup?", default=False
                    ).ask()
                )
                _run(
                    perform_export(
                        source,
                        Path(out_text),
                        include_emojis=emojis,
                        assume_yes=False,
                        logger=logger,
                    ),
                    verbose=False,
                )
            elif choice == "import":
                file_text = questionary.text("Backup .json path:").ask()
                if not file_text:
                    continue
                target = questionary.text("Target server ID:").ask()
                if not target:
                    continue
                emojis = bool(
                    questionary.confirm(
                        "Copy emojis and stickers from the backup?", default=False
                    ).ask()
                )
                dry_run = bool(questionary.confirm("Dry run (preview only)?", default=False).ask())
                clear_target = bool(
                    questionary.confirm(
                        "Clear the target server first (deletes channels and roles)?",
                        default=False,
                    ).ask()
                )
                _run(
                    perform_import(
                        Path(file_text),
                        target,
                        dry_run=dry_run,
                        clear_target=clear_target,
                        include_emojis=emojis,
                        assume_yes=False,
                        logger=logger,
                    ),
                    verbose=False,
                )
            elif choice == "list":
                _run(perform_list(logger), verbose=False)
        except KeyboardInterrupt:
            console.print("\nBye.")
            return
        except typer.Exit:
            pass
        except GuildForgeError as exc:
            console.print(f"[bold red]Error:[/bold red] {exc.message}")
            if exc.hint:
                console.print(f"[dim]{exc.hint}[/dim]")
        except Exception as exc:
            console.print(f"[bold red]Unexpected error:[/bold red] {exc}")

        console.print("")
        questionary.press_any_key_to_continue("Press any key to return to the menu").ask()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
