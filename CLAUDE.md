# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

**F5 Господина** ("F5 of the Master") — a Telegram bot that polls a public Google
Sheet of "нормоконтроль" (thesis formal-compliance review) and reports changes to
Telegram as fast as possible. The critical signal in the sheet is **cell fill
color**: a cell turning **green** means a student passed. The bot also reports
text changes. Each report attaches a full xlsx dump and pings responsible people.

The original target: watch a specific group, report to a target group chat, ping
a responsible person by `@username`. But the design is generic —
arbitrary subscriptions (groups and/or individual students → any chat → any
pings), configurable at runtime via bot commands.

## Hard requirements (learned from the user — do not regress)

- **Color is the primary signal.** CSV/gviz exports do NOT carry formatting, so we
  fetch the **xlsx export** (`export?format=xlsx`, no API key needed for a
  link-shared sheet) and parse fill colors from `xl/styles.xml`. Never switch back
  to CSV for detection.
- **Track only the main table: first 9 columns (A…I, indices 0–8).** The right-side
  "Антиплагиат" block (column 10+) is outside the main table and must be ignored
  (`src/xlsx.py: MAX_TRACK_COL = 8`).
- **Ping by `@username`, not by display name.** A `tg://user?id=` text-mention with
  a name does not reliably notify. `render_mention` prioritizes `@username`.
- **Bot reacts only in whitelisted chats** (`config.listeners`). Chats outside the
  list are ignored entirely, including `/start`, unless `open_registration: true`.
- **Single instance only.** Two processes calling `getUpdates` cause 409 conflicts
  and ~minute-long latency. `run`/`bot` take a `flock` (`config/.f5gospodina.lock`).
- **No third-party dependencies.** Standard library only (urllib, zipfile,
  xml.etree, fcntl). Keep it deployable anywhere with plain `python3`.

## Architecture

Entry point: `main.py <command>` (adds `src/` to `sys.path`, calls `polling.main`).

```
main.py            entry point
src/
  polling.py       CLI dispatch + two threads in `run`: sheet_loop + bot_loop;
                   poll_once() does fetch → parse → diff → route to subscriptions;
                   single-instance lock lives here.
  config.py        load/save config (atomic), load/save state (atomic os.replace).
                   Paths default to ../config/, overridable via CONFIG_PATH/STATE_PATH.
  xlsx.py          fetch_xlsx() + parse_workbook(): unzip xlsx, parse sharedStrings
                   + styles (fills) + the right worksheet (chosen by marker match
                   on watched group codes), build snapshot. Cells = {"v": text,
                   "c": color_label}. Greens normalized to "🟢 зелёный".
  diff.py          diff_snapshots() compares text AND color per cell;
                   sub_matches() routes a change to a subscription (by group or
                   student substring, optional column filter).
  telegram.py      Telegram Bot API over urllib: send_message, send_document
                   (multipart), get_updates (long-poll).
  report.py        build_report() (grouped by student, color+text deltas),
                   render_mention(), send_report() (attaches xlsx dump).
  commands.py      CommandHandler: whitelist check, /start registration,
                   /watch /unwatch /ping /unping /list /status /dump /stop /help.
                   Mutating commands do read-modify-write on config.json.
config/
  config.example.json   template (committed)
  config.json           real config incl. bot_token (GITIGNORED)
  state.json            table snapshot (GITIGNORED, auto-created)
.github/workflows/
  deploy.yml            CI: push to main -> ssh -> git reset --hard -> restart
```

### Data model

- **Snapshot** (`state.json`): `{ "GROUP | ФИО": {"_group","_fio","cells": {col: {"v","c"}}} }`.
  Only main-table columns with non-empty text or non-white fill are stored.
- **Change**: `{group, fio, column, old_v, new_v, old_c, new_c}`.
- A subscription matches a change if the change's group is in `groups` OR the ФИО
  contains one of `students`; optional `columns` whitelist; empty groups+students
  means "watch everything".

### Sheet parsing notes (gotchas)

- The sheet has multiple tabs; the right worksheet is picked by counting
  occurrences of watched group codes (`parse_workbook(markers=...)`). The xlsx
  `sheetId` is NOT the Google `gid`, so do not map by number.
- Group code is a vertically-merged cell — only the first student row carries it;
  it is propagated down within a block (block = rows between header rows where
  col A == "№").
- Integers come back as `"1.0"` in xlsx; `_parse_worksheet` strips the trailing
  `.0` so row-number detection works.
- Green detection: explicit ARGB in `GREEN_RGBS` plus a `_greenish()` heuristic.
  Assumes **manual** fills (confirmed in this sheet). Conditional-formatting colors
  are NOT exported as cell fills and would be missed.

## Running

```bash
python3 main.py init   # seed state silently (first run)
python3 main.py run     # main mode: poll table + serve bot commands
python3 main.py once    # single poll (cron)
python3 main.py bot     # only command listener
python3 main.py test    # test message to all subscription chats
```

First run must `init` (or first `run`/`once` self-seeds) so existing marks are not
reported as new.

## Working with secrets

- `config/config.json` contains the bot token and is gitignored. Never commit it.
- For deployment prefer `TG_BOT_TOKEN` env (overrides config) and a persistent
  `STATE_PATH`.
- A personal MTProto client (e.g. the `tg` CLI skill) is handy for finding
  chat/user IDs and for end-to-end testing of the bot from a user account.
  Bot HTTP API is the production path.

## Reference values

Реальные значения (bot token, chat/user id, sheet id, имена ответственных)
держим только в `config/config.json` (gitignored) — в репозитории их нет.
Шаблон с плейсхолдерами: `config/config.example.json`.

## Deployment & CI

- **Prod runs on a VPS via systemd** (`main.py run`), NOT in a container. Code in
  `/opt/normocontrol-polling`, cloned from the public HTTPS remote.
- **Telegram must be reachable from the host.** Yandex Cloud was tried and
  rejected: `api.telegram.org` is unreachable there (`URLError: [Errno 101]`),
  though Google Sheets works. Pick a host from which Telegram is reachable.
- **Auto-deploy: `.github/workflows/deploy.yml`.** Push to `main` → SSH → `git
  reset --hard origin/main` → `systemctl restart f5-gospodina`. `reset --hard`
  (not `pull`) is force-push-safe and never touches untracked files.
- **State/config survive deploys precisely because they are gitignored** —
  `git reset --hard` leaves `config.json` and `state.json` alone. Never commit
  them; never have CI overwrite them (subscriptions are edited live via bot
  commands, so a CI overwrite would clobber them).
- **Deploy secrets live in GitHub Secrets**, not in the repo: `DEPLOY_HOST`,
  `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`. The CI SSH key is
  separate from any personal key so it can be revoked independently.
- Real host/IP/token/PII are NOT in the repo (it is public) — see "Reference
  values" above. Operational specifics live in Claude's project memory.

## Conventions

- Comments and user-facing strings are in Russian; code identifiers in English.
- Keep modules small and single-purpose; no external deps.
- State and config writes must stay atomic (tmp + `os.replace`).
