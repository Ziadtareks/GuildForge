# GuildForge manual test checklist

This is a ~20 minute checklist that proves GuildForge works end to end with a real bot.
It uses two **test** servers you own. No real token is stored in this repository, and
GuildForge never asks you to share yours.

## 0. What you need

- Two Discord servers you own, or one server you own plus a private test server:
  - `SOURCE` — the server whose structure you want to copy. Put a few roles, categories,
    channels, topics, and permission overwrites in it.
  - `TARGET` — an empty server you do not mind clearing.
- Python 3.10+ (for the source install) **or** the `GuildForge.exe` release asset.
- 10 minutes.

Record your values and keep them private:

| Value | Yours |
| --- | --- |
| SOURCE server ID | |
| TARGET server ID | |
| Bot application ID | |

## 1. Create the bot

1. Open <https://discord.com/developers/applications> > **New Application**, name it `GuildForge Test`.
2. **Bot** tab > **Reset Token** > **Copy**.
3. Paste the token into a local `.env` file next to the executable or project root:

   ```dotenv
   DISCORD_TOKEN=paste-your-token-here
   ```

   Keep this file out of git. It is already in `.gitignore`.
4. Confirm no privileged intents are required. GuildForge only uses the standard `GUILDS` intent.

## 2. Invite the bot with Administrator

1. **General Information** tab > copy the **Application ID**.
2. Open this URL, replacing `<APPLICATION_ID>`:

   ```text
   https://discord.com/oauth2/authorize?client_id=<APPLICATION_ID>&permissions=8&scope=bot%20applications.commands
   ```

3. Invite the bot to **both** `SOURCE` and `TARGET`.
4. In `TARGET`: **Server Settings > Roles** > drag the `GuildForge Test` role to the **top**.
5. Optional: enable **Community** on `TARGET` so announcement/forum/stage/media channels copy
   as their real types instead of text/voice fallbacks.

## 3. Find the server IDs

Enable **User Settings > Advanced > Developer Mode**, right-click each server icon, and choose
**Copy Server ID**.

## 4. Verify the install

```console
$ guildforge --version
GuildForge 1.0.0

$ guildforge --help
```

- [ ] `--version` prints `GuildForge 1.0.0`.
- [ ] `--help` lists `export`, `import`, `clone`, and `list`.

## 5. List servers

```console
$ guildforge list
```

- [ ] Both `SOURCE` and `TARGET` appear with the correct IDs and member counts.
- [ ] If you used a wrong token, the error is friendly and mentions the token, not a traceback.

## 6. Export the source

```console
$ guildforge export --source <SOURCE_ID> --out source_backup.json
```

- [ ] A `source_backup.json` file is created.
- [ ] The summary table shows role/category/channel counts that match `SOURCE`.
- [ ] Open the JSON in a text editor: it contains a `schema_version`, roles, and channels.
- [ ] On a private source with emojis, repeat with `--emojis` and confirm `emojis` is not empty.

Optional template test (works even without the bot in the source):

```console
$ guildforge export --source https://discord.new/<TEMPLATE_CODE> --out template_backup.json
```

- [ ] A warning explains that templates do not contain server settings, emojis, or stickers.
- [ ] Roles and channels still appear in the backup.

## 7. Dry run the clone (no writes)

```console
$ guildforge clone --source <SOURCE_ID> --target <TARGET_ID> --dry-run
```

- [ ] The plan shows counts to create and `TARGET`'s real name.
- [ ] Any Community/boost warnings are specific (for example "banner requires level 2").
- [ ] `TARGET` is completely unchanged in Discord.
- [ ] Repeat with `--clear-target`; the plan now shows deletions, and `TARGET` is still unchanged.

## 8. Clone into the target

```console
$ guildforge clone --source <SOURCE_ID> --target <TARGET_ID> --clear-target
```

Confirm with `y`, then type the exact `TARGET` server name when asked.

- [ ] The bot prints a progress section per step: settings, roles, categories, channels, overwrites.
- [ ] Final report shows `Failed 0`.
- [ ] `.guildforge-state.json` is deleted after a fully successful run.

Check in Discord:

- [ ] Server name matches the source.
- [ ] Server icon matches (if the source had one).
- [ ] Roles exist with the same names, colors, hoist/mention settings, and permissions.
- [ ] `@everyone` permissions match the source.
- [ ] Role order is the same relative order (bot role aside).
- [ ] Categories and channels match, including topics, NSFW, slowmode, bitrate, and user limits.
- [ ] Category parenting is correct.
- [ ] Channel permission overwrites match the source roles.
- [ ] No members, messages, webhooks, integrations, or bots were copied.
- [ ] The bot did not create a second `@everyone`.

## 9. Import the exported backup

1. Edit a copy of `source_backup.json`: change one channel topic or slowmode value.
2. Run:

```console
$ guildforge import --file source_backup.json --target <TARGET_ID> --yes
```

- [ ] The import creates the structure (or resumes existing mappings) without crashing.
- [ ] New items appear; existing mapped items are skipped, not duplicated.
- [ ] Your JSON edit is visible in the created channel.

## 10. Emojis and stickers (optional)

```console
$ guildforge clone --source <SOURCE_ID> --target <TARGET_ID> --emojis --yes
```

- [ ] Emojis are copied up to the target slot limit.
- [ ] If slots are full, GuildForge warns and skips the rest instead of failing.
- [ ] Lottie stickers are skipped with an explicit warning.

## 11. Community fallback test

On a `TARGET` without Community enabled:

```console
$ guildforge clone --source <SOURCE_ID> --target <TARGET_ID> --dry-run
```

- [ ] GuildForge warns that announcement/stage/forum/media channels will fall back.
- [ ] A real run creates them as text/voice channels and does not crash.

## 12. Resume test

1. Start a clone into a large-ish target.
2. Press `Ctrl+C` in the middle of role or channel creation.
3. Rerun the exact same command.

- [ ] GuildForge prints `Saved progress found: ...` and asks to resume.
- [ ] Answer `y`: it continues instead of duplicating already created items.
- [ ] The final report shows `Failed 0` once the cause of any interruption is gone.

## 13. Error handling spot checks

- [ ] `guildforge clone --source 1 --target <TARGET_ID>` → friendly "not a valid server ID" error.
- [ ] `guildforge import --file missing.json --target <TARGET_ID>` → Typer reports the missing file.
- [ ] Remove `DISCORD_TOKEN` from `.env` and run `guildforge list` in a terminal → hidden token prompt.
- [ ] Put a wrong token in `.env` → "Discord rejected the bot token" with a hint, no traceback.
- [ ] Revoke the bot's Administrator permission in `TARGET` and run a clone → clear permission error.
- [ ] Corrupt a JSON backup (delete a closing brace) → "not valid JSON" with line/column.

## 14. Cleanup

- Delete `source_backup.json`, `template_backup.json`, and `.guildforge-state.json` if present.
- Reset the bot token in the Developer Portal after testing.
- Delete the test bot application if you no longer need it.

## Reporting results

If any checkbox fails, open a bug report with:

- The command (token and private IDs removed).
- The expected result.
- The actual output with `--verbose`.
- Your OS, GuildForge version, and whether you used the `.exe` or source.
