# Changelog

All notable changes to GuildForge are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-20

### Added

- `guildforge clone` — copy a server structure directly into another server.
- `guildforge export` — write a server structure to a versioned JSON backup (`schema_version: 1`).
- `guildforge import` — apply a JSON backup to a target server.
- `guildforge list` — list servers the bot is a member of.
- `guildforge --version` and full command help.
- Interactive arrow-key menu when started with no arguments (double-click friendly).
- Source modes: server ID (bot member), Discord template link/code (no bot needed in the source),
  and JSON backup file.
- Copies server settings, roles, categories, text/voice/announcement/stage/forum/media channels,
  role permission overwrites, and optionally emojis and stickers (`--emojis`).
- `--dry-run`, `--clear-target` (with typed-name confirmation), `--emojis`, `--yes`, `--verbose`.
- Resume support through `.guildforge-state.json`, written after every item.
- Discord hard-limit checks: 250 roles, 500 channels, 50 channels per category, emoji/sticker slots.
- Retry with backoff on HTTP 429/5xx and transient network errors.
- Graceful skips with warnings for missing boost level, missing Community features,
  managed roles, Lottie stickers, and member-specific overwrites.
- Rich tables, progress bars, and friendly error messages with actionable hints.
- Pytest suite (30 tests) covering role mapping, overwrite mapping, position ordering,
  JSON validation, limit checks, template parsing, state files, and retries.
- GitHub Actions CI (ruff + pytest on Python 3.10, 3.11, 3.12) and a release workflow that
  builds a Windows `.exe` with PyInstaller and attaches it plus the source zip to the release.

### Known limitations

- Messages, members, webhooks, integrations, AutoMod, events, and member-specific permission
  overwrites are not copied.
- Managed roles are skipped, and a bot cannot manage roles above its own highest role.
- Community-only channels fall back on targets without Community; banners need boost level 2
  and invite splashes need boost level 1.
- Lottie stickers cannot be created by bots; template sources contain no settings, emojis, or stickers.
- On a non-empty target, copied positions can shift existing items; use `--clear-target`
  for an exact layout.
