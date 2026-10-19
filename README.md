<div align="center">

# GuildForge

**Copy a Discord server's structure to your own server in one command.**

[![CI](https://github.com/Ziadtareks/GuildForge/actions/workflows/ci.yml/badge.svg)](https://github.com/Ziadtareks/GuildForge/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Ziadtareks/GuildForge?include_prereleases&sort=semver)](https://github.com/Ziadtareks/GuildForge/releases)
[![License: MIT](https://img.shields.io/github/license/Ziadtareks/GuildForge)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

</div>

---

## What is it

GuildForge is a command-line tool (Windows `.exe`, plus Linux and macOS from source)
that copies the **structure** of one Discord server into another: roles, categories,
channels, permission overwrites, server settings, and optionally emojis and stickers.

It works with three kinds of sources:

| Source | How it works |
| --- | --- |
| **Guild ID** | The bot is a member of the source server and reads it directly. |
| **Template link/code** | Uses Discord's public template API, so the bot does **not** need to be in the source server. |
| **JSON backup** | Reads a file previously created by `guildforge export`. |

> GuildForge uses an **official Discord bot token** only. It is not a self-bot and it never
> uses user tokens.

## Features

- **One command clone** — settings, roles, categories, channels, overwrites, and more.
- **Export/import** — save a server structure to a readable JSON backup (`schema_version` included).
- **Template mode** — clone the structure of servers you cannot add a bot to.
- **Dry run** — see exactly what will be created before anything is written.
- **Resume support** — progress is saved to a state file; after a crash or `Ctrl+C`, rerun and continue.
- **Rate-limit aware** — async everywhere, retries with backoff on HTTP 429/5xx.
- **Discord limit checks** — warns or stops on 250 roles, 500 channels, 50 channels per category, and emoji/sticker slots.
- **Graceful degradation** — missing boost level, Community features, permissions, or unsupported channels produce warnings, never crashes.
- **Friendly output** — Rich tables and progress bars, plus an arrow-key menu when you double-click the `.exe`.

### What it copies

- Server settings: name, icon, banner and splash (only when the target boost level allows),
  description, verification level, default notifications, explicit content filter, AFK channel
  and timeout, system channel and flags, rules channel, public updates channel, preferred locale.
- Roles: name, color, hoist, mentionable, permissions, order, unicode emoji or icon.
- Channels: category, text, voice, announcement, stage, forum, and media.
  Properties: name, topic, NSFW, slowmode, bitrate, user limit, RTC region, video quality,
  auto-archive duration, forum tags, default reaction, position, and parent category.
- Permission overwrites for **roles** (mapped from old role IDs to new ones).

### What it does NOT copy

- Messages, threads, or message history.
- Members, bans, or invites.
- Bot configurations, webhooks, integrations, or application commands.
- Member-specific permission overwrites (only role overwrites are copied).
- Managed roles (bot roles, booster roles, integration roles).
- Server boosts, stickers/emojis unless you pass `--emojis`.

## Quick start

1. **Create a bot** in the [Discord Developer Portal](https://discord.com/developers/applications)
   and copy its token (see [Bot setup](#bot-setup-step-by-step)).
2. **Install GuildForge** — download `GuildForge.exe` from the
   [latest release](https://github.com/Ziadtareks/GuildForge/releases) or run from source.
3. **Run it**:

```powershell
GuildForge.exe clone --source 111111111111111111 --target 222222222222222222
```

Prefer a menu? Double-click `GuildForge.exe` with no arguments.

## Installation

### Option A — Windows executable

1. Download `GuildForge.exe` from the [Releases](https://github.com/Ziadtareks/GuildForge/releases) page.
2. Put it in a folder you can write to (for example `C:\GuildForge`).
3. Create a file named `.env` next to the executable:

```dotenv
DISCORD_TOKEN=your-bot-token-here
```

4. Open a terminal in that folder and run `.\GuildForge.exe --version`.

> Some antivirus products flag PyInstaller executables as false positives. If that happens,
> either add an exception or use Option B — see the [FAQ](#faq).

### Option B — From source (recommended if you have Python)

```bash
git clone https://github.com/Ziadtareks/GuildForge.git
cd GuildForge
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS
pip install -e .
guildforge --version
```

Python **3.10 or newer** is required.

### Token configuration

GuildForge reads `DISCORD_TOKEN` from `.env` in this order:

1. The current working directory.
2. The folder that contains `GuildForge.exe` (when using the executable).
3. `~/.guildforge/.env`.

If no token is found, GuildForge asks for it with hidden input. The token is never printed,
logged, or saved by GuildForge. `.env` is in `.gitignore`; never commit it.

## Bot setup (step by step)

### 1. Create the application and bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**.
2. Give it a name (for example `GuildForge`) and create it.
3. Open the **Bot** tab, click **Reset Token**, and copy the token. Paste it into `.env` as `DISCORD_TOKEN`.
4. Stay on the **Bot** tab and enable these **Privileged Gateway Intents** if the panel asks:
   - Server Members Intent: **not required**
   - Message Content Intent: **not required**
   - Presence Intent: **not required**

   GuildForge only needs the standard `GUILDS` intent, which every bot has by default.

5. In **Installation** or **OAuth2 > URL Generator**, select the `bot` and `applications.commands`
   scopes and the permissions listed below, then open the generated URL to invite the bot.

### 2. Required permissions

| Permission | Used for |
| --- | --- |
| **Administrator** (simplest) | Everything below in one permission. |
| Manage Server | Server settings, name, icon, banner, splash. |
| Manage Roles | Creating and editing roles and role overwrites. |
| Manage Channels | Creating and editing categories and channels. |
| Manage Expressions | Copying emojis and stickers (`--emojis`). |

Minimal invite link (replace `<APPLICATION_ID>` with the Application ID from the **General Information** tab):

```text
https://discord.com/oauth2/authorize?client_id=<APPLICATION_ID>&permissions=8&scope=bot%20applications.commands
```

`permissions=8` is Administrator. For a custom set, use the URL Generator in the Developer Portal.

**Important:** after inviting the bot, open **Server Settings > Roles** and drag the bot's role
to the top of the list. Discord only lets a bot manage roles below its own highest role, and
GuildForge warns you when this is not the case.

### 3. Find a server ID

1. Discord **User Settings > Advanced > Developer Mode**: enable it.
2. Right-click the server icon and choose **Copy Server ID** (17–20 digits).

### 4. Community and boost features

Announcement, stage, forum, and media channels plus rules/updates channels require
**Community** on the target server. Banners require boost level 2 and invite splashes require
boost level 1. When the target does not support a feature, GuildForge warns and skips or
falls back — it never crashes.

## Usage

```text
guildforge export --source <id|template> --out backup.json
guildforge import --file backup.json --target <guildId>
guildforge clone  --source <id|template> --target <guildId>
guildforge list
guildforge --version
```

Flags for `clone` and `import`:

| Flag | Meaning |
| --- | --- |
| `--dry-run` | Validate everything and print the plan without writing to Discord. |
| `--clear-target` | Delete target channels and non-managed roles first (asks you to type the server name). |
| `--emojis` | Also copy emojis and stickers (off by default). |
| `--yes` | Skip the plan confirmation prompt (use with care). |
| `--verbose` | Show detailed progress and full tracebacks. |

`export` accepts `--emojis` and `--yes` (overwrite an existing output file without asking).

### List the servers the bot is in

```console
$ guildforge list
                       Servers the bot is in
+------------------+---------------------+---------+-------------+
| Name             | ID                  | Members | Owner       |
|------------------+---------------------+---------+-------------|
| GuildForge Demo  | 100000000000000001  | 42      | <@123456>   |
| My Test Server   | 222222222222222222  | 7       | <@654321>   |
+------------------+---------------------+---------+-------------+
```

### Export a server

```console
$ guildforge export --source 100000000000000001 --out backup.json
Backup written to C:\GuildForge\backup.json
     Backup contents
+----------------+-------+
| Item           | Count |
|----------------+-------|
| Roles          | 3     |
| Categories     | 2     |
| Channels       | 8     |
| Emojis         | 0     |
| Stickers       | 0     |
| Schema version | 1     |
+----------------+-------+
```

Template mode works the same way:

```console
$ guildforge export --source https://discord.new/abc123XYZ --out backup.json
```

Note: templates store channels, roles, and overwrites, but not server settings, icons,
banners, emojis, or stickers. GuildForge tells you this after reading a template.

### Preview an import

```console
$ guildforge import --file examples/sample_backup.json --target 222222222222222222 --dry-run
Warning: Offline preview: no DISCORD_TOKEN was found, so the target server was
not inspected. Target-specific warnings below assume an empty boost-level-0
server.
                       Copy plan
+------------------------------------------------------+
| Item                 | Value                         |
|----------------------+-------------------------------|
| Source               | GuildForge Demo (backup file) |
| Target               | server 222222222222222222     |
| Roles to create      | 3                             |
| Categories to create | 2                             |
| Channels to create   | 8                             |
| Emojis to copy       | 0                             |
| Stickers to copy     | 0                             |
+------------------------------------------------------+
Dry run complete. Nothing was sent to Discord.
```

When a token is available, the preview also checks the target server, permissions,
boost level, and Discord limits:

```console
$ guildforge clone --source 111111111111111111 --target 222222222222222222 --dry-run --clear-target
                       Copy plan
+------------------------------------------------------+
| Item                 | Value                         |
|----------------------+-------------------------------|
| Source               | My Source Server (111111111111111111) |
| Target               | My Test Server (222222222222222222)   |
| Roles to create      | 12                            |
| Categories to create | 4                             |
| Channels to create   | 21                            |
| Emojis to copy       | 0                             |
| Stickers to copy     | 0                             |
| Roles to delete      | 5                             |
| Channels to delete   | 18                            |
+------------------------------------------------------+
Dry run complete. No changes were written to Discord.
```

### Clone

```console
$ guildforge clone --source 111111111111111111 --target 222222222222222222
Copy the structure into 'My Test Server'? [y/N]: y
Resume state: C:\GuildForge\.guildforge-state.json
...
GuildForge report
+----------+-------+
| Result   | Count |
+----------+-------+
| Created  | 57    |
| Skipped  | 0     |
| Failed   | 0     |
| Warnings | 2     |
+----------+-------+
Copy complete.
Done. GuildForge 1.0.0 copied 57 item(s).
```

Useful variants:

```powershell
guildforge clone --source 111111111111111111 --target 222222222222222222 --dry-run
guildforge clone --source https://discord.new/abc123XYZ --target 222222222222222222
guildforge clone --source backup.json --target 222222222222222222 --clear-target --emojis
```

### Resume after a crash or `Ctrl+C`

Progress is written to `.guildforge-state.json` after every role, channel, and emoji.
If a run is interrupted, rerun the same command and GuildForge offers to continue:

```console
$ guildforge clone --source 111111111111111111 --target 222222222222222222
Saved progress found: 12 roles, 9 channels, 0 emojis mapped.
Resume the previous run? [Y/n]: y
```

Delete the state file if you want a clean start.

### Interactive menu

Run with no arguments (or double-click the `.exe`) to get an arrow-key menu:

```text
┌──────────────────────────────────────────────────────────┐
│ GuildForge 1.0.0                                         │
│ Copy a Discord server's structure to your own server.    │
│ Bot token only. Messages, members, webhooks, and         │
│ integrations are never copied.                           │
└──────────────────────────────────────────────────────────┘
? What do you want to do?  (Use arrow keys)
 » Clone a server into another server
   Export a server to a JSON backup
   Import a JSON backup into a server
   List servers the bot is in
   Exit
```

## Template mode

Templates are Discord's official way to share a server layout. GuildForge uses
`GET /guilds/templates/{code}` through `discord.py`, so the bot does **not** need to be
in the source server — only in the target.

```powershell
guildforge clone --source https://discord.new/abc123XYZ --target 222222222222222222
```

Limitations of templates:

- No server settings: name comes from the template, but icon, banner, splash, description,
  locale, verification level, and notifications are not available.
- No emojis or stickers.
- The template must still exist; old templates expire.

For a full copy, add the bot to the source server and use its server ID.

## Limitations

- Discord's hard limits apply: 250 roles, 500 channels, 50 channels per category, and
  emoji/sticker slots based on boost level. GuildForge checks these before writing.
- A bot can only manage roles below its own top role. Put the bot role at the top.
- Managed roles (bots, integrations, boosters) are never created or deleted.
- Member-specific channel overwrites are intentionally skipped.
- Message content, threads, webhooks, integrations, and events are not copied.
- Guild name changes are rate-limited by Discord; a few setting changes may take a moment.
- Position copying uses the source positions. On a non-empty target, existing items may
  shift; use `--clear-target` for an exact layout.
- Lottie stickers cannot be created by bots and are skipped with a warning.
- Custom emoji references in forum tags/default reactions fall back or are skipped because
  source emoji IDs do not exist on the target.

## Troubleshooting

### "Discord rejected the bot token"

Re-copy the token from **Developer Portal > Bot > Reset Token**. Make sure it is a bot token
(three dot-separated parts) and not a client secret or a user token. Update `.env`.

### "The bot cannot access the target server"

The bot is not in the server, the ID is wrong, or the invite was revoked. Re-invite it with
Administrator permissions.

### "The bot does not have Administrator permission"

Open **Server Settings > Roles > (bot role) > Administrator** and enable it, then rerun.

### Roles are created in the wrong order or creation fails

Discord only lets a bot manage roles below its own highest role. Drag the bot role to the
top of **Server Settings > Roles** and rerun. GuildForge resumes instead of duplicating.

### Announcement/forum/stage channels became text/voice channels

Enable **Community** on the target: **Server Settings > Enable Community**. Announcement,
forum, media, stage, and rules/updates channels need it.

### Banner or invite splash was skipped

Banners need boost level 2; invite splashes need level 1. GuildForge skips them with a warning.

### Emojis or stickers were not copied

They are opt-in. Pass `--emojis` during `export` and `clone`/`import`. Emoji slots depend on
the target boost level, and Lottie stickers cannot be created by bots.

### It says "Some steps failed"

The report lists every failure. Fix the cause (usually permissions or role position) and
rerun the same command; GuildForge resumes from the saved state.

## FAQ

**Is this against Discord's Terms of Service?**
GuildForge uses the official bot API. Copying a server *you own or have permission to copy*
is allowed. Do not use it to clone servers you do not own.

**Does it work without adding a bot to the source?**
Yes, with template mode. Templates only contain channels, roles, and overwrites.

**Can I undo a clone?**
Not automatically. Use `--dry-run` first, and keep a backup of the target (export it before
cloning). `--clear-target` is destructive and asks you to type the server name.

**Windows says the `.exe` is a virus.**
PyInstaller executables are often flagged as false positives. Download only from the
official Releases page, verify the file size, or run from source:

```bash
pip install guildforge
guildforge --version
```

**Where does the resume state file live?**
`.guildforge-state.json` in the current working directory. Delete it to start fresh.

**Can I copy only part of a server?**
Edit the exported JSON, remove the roles/channels you do not want, then import it. The file
is plain JSON with a documented `schema_version`.

**Do I need the Message Content intent?**
No. GuildForge never reads messages.

## Security and responsible use

- **Own or permission:** only copy servers you own or have explicit permission to copy.
- **Official tokens only:** GuildForge refuses to work with user tokens or self-bots.
- **Token safety:** the token is read from `.env` or hidden input, redacted from logs, and
  never written anywhere. Rotate the token immediately if you ever paste it into a chat,
  screenshot, or issue.
- **Backups are sensitive:** a backup file contains role and channel names and permission
  structures. Treat it like configuration data.
- **Dry run first:** `--dry-run` shows the full plan (including deletions) before writing.
- **Rate limits:** GuildForge retries 429/5xx responses with backoff and respects Discord's
  rate limits; avoid running multiple clones against the same server at once.

## Disclaimer

GuildForge is an unofficial tool and is not affiliated with, endorsed by, or sponsored by
Discord Inc. "Discord" is a trademark of Discord Inc. You are responsible for how you use
this tool and for complying with Discord's Terms of Service and Community Guidelines.
The authors are not liable for any data loss, server changes, or bans resulting from use of
this software. Always test with `--dry-run` and on a test server first.

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) first.

```bash
pip install -e ".[dev]"
ruff check .
ruff format --check .
pytest
```

## License

[MIT](LICENSE) © 2026 Ziad Tarek
