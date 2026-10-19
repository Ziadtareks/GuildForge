"""Rich-powered console output with secret redaction and warning collection."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table


class ProgressHandle:
    """Small wrapper so the writer does not depend on Rich internals."""

    def __init__(self, progress: Progress | None = None, task_id: int | None = None) -> None:
        self._progress = progress
        self._task_id = task_id

    def advance(self, amount: int = 1, description: str | None = None) -> None:
        if self._progress is None or self._task_id is None:
            return
        if description is not None:
            self._progress.update(self._task_id, description=description)
        self._progress.advance(self._task_id, amount)


@dataclass
class GuildForgeLogger:
    """All user-facing output goes through this class."""

    console: Console = field(default_factory=lambda: Console(highlight=False, soft_wrap=True))
    verbose: bool = False
    quiet: bool = False
    warnings: list[str] = field(default_factory=list)
    _secrets: set[str] = field(default_factory=set)

    def add_secret(self, value: str | None) -> None:
        if value:
            self._secrets.add(value)

    def redact(self, message: str) -> str:
        for secret in self._secrets:
            if secret:
                message = message.replace(secret, "<redacted>")
        return message

    def debug(self, message: str) -> None:
        if self.verbose and not self.quiet:
            self.console.print(f"[dim]{self.redact(message)}[/dim]")

    def info(self, message: str) -> None:
        if not self.quiet:
            self.console.print(self.redact(message))

    def success(self, message: str) -> None:
        if not self.quiet:
            self.console.print(f"[bold green]{self.redact(message)}[/bold green]")

    def warn(self, message: str) -> None:
        message = self.redact(message)
        self.warnings.append(message)
        if not self.quiet:
            self.console.print(f"[yellow]Warning:[/yellow] {message}")

    def error(self, message: str) -> None:
        if not self.quiet:
            self.console.print(f"[bold red]Error:[/bold red] {self.redact(message)}")

    def rule(self, title: str) -> None:
        if not self.quiet:
            self.console.rule(f"[bold]{self.redact(title)}")

    def table(self, title: str, columns: list[str], rows: list[list[str]]) -> None:
        if self.quiet:
            return
        table = Table(title=title, show_lines=False, header_style="bold cyan")
        for column in columns:
            table.add_column(column)
        for row in rows:
            table.add_row(*(self.redact(cell) for cell in row))
        self.console.print(table)

    @contextmanager
    def progress(self, description: str, total: int) -> Iterator[ProgressHandle]:
        if self.quiet or total <= 0:
            yield ProgressHandle()
            return
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=self.console,
            transient=False,
        ) as progress:
            task_id = progress.add_task(description, total=total)
            yield ProgressHandle(progress, task_id)
