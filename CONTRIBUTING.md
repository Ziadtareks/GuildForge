# Contributing to GuildForge

Thanks for helping improve GuildForge. This guide keeps contributions fast to review.

## Setup

```bash
git clone https://github.com/Ziadtareks/GuildForge.git
cd GuildForge
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate
pip install -e ".[dev]"
```

## Checks before opening a pull request

```bash
ruff check .
ruff format --check .
pytest
```

All three must pass. CI runs the same commands on Python 3.10, 3.11, and 3.12.

## Project layout

```text
src/guildforge/
    cli.py                # Typer commands, interactive menu, output
    config.py             # token discovery, paths, constants
    errors.py             # user-facing exception types
    discord_io/
        client.py         # REST session, pre-flight checks
        reader.py         # guild/template -> backup model
        writer.py         # backup -> guild, resume state, pure helpers
    models/
        schema.py         # Pydantic backup schema
        validation.py     # loading and structural validation
        limits.py         # Discord hard-limit checks
    utils/
        logger.py         # Rich output with secret redaction
        retry.py          # 429/5xx backoff helper
        prompts.py        # confirmation and hidden-input helpers
tests/                    # pytest suite, no token or network needed
examples/sample_backup.json
```

## Guidelines

- **No user tokens or self-bots.** Only official bot tokens are supported.
- **Async everywhere.** Use `await` for Discord calls; use `guildforge.utils.retry.with_retry`
  for operations that can hit rate limits.
- **Never log secrets.** Route output through `GuildForgeLogger` and call `add_secret`.
- **Fail soft.** A single unsupported channel, missing boost feature, or managed role must not
  abort the copy. Record a warning and continue.
- **Test without a token.** Mock or fake Discord objects; CI has no secrets.
- **Keep the backup schema versioned.** Breaking changes to the JSON format require bumping
  `SCHEMA_VERSION` and updating `examples/sample_backup.json` and tests.
- **Style:** type hints, `ruff format`, 100-column lines, no commented-out code.

## Commit messages

Use conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `ci:`, `refactor:`, `chore:`.

## Reporting bugs

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md) and include:

- GuildForge version (`guildforge --version`).
- Python version or `.exe` release tag.
- Command you ran (remove your token and server IDs if they are private).
- Full output with `--verbose`.

Never paste your bot token in an issue. If you did, reset it immediately.
