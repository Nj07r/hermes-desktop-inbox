# Module: `desktop_inbox.core`

The item model and every read and write that the plugin does. The CLI, the pane backend and the webhook all call this module.

## Responsibilities

- File an item: write the seed, run one `hermes chat` turn, move the session to the inbox folder, set its title and rewrite its first reply.
- List items and read each item's source and ask line from its first assistant message.
- Close, reopen, snooze and wake items through `SessionDB`.
- Read failed cron jobs from `cron/jobs.json` and the newest file in `cron/output/<job_id>/`.
- Read kanban cards in `blocked` or `triage` from every board, and archive a card through `hermes kanban`.
- Close items whose subscribed kanban card is done (auto-settle).
- Turn a failed cron job into a Do item.

## Key files

| File | Content |
|---|---|
| [`desktop_inbox/core.py`](../../../desktop_inbox/core.py) | The whole module, 598 lines |
| [`tests/test_core.py`](../../../tests/test_core.py) | Unit tests for settings, `parse_when`, `parse_brief`, `seed_text`, `failed_jobs`, the webhook and `_clean_env` |

## Public API

### Paths and helpers

| Name | Signature | Description |
|---|---|---|
| `inbox_dir` | `inbox_dir() -> Path` | The inbox folder from [`settings.inbox_dir()`](settings.md). |
| `hermes_bin` | `hermes_bin() -> str` | The first `hermes` found on `PATH`, in `~/.local/bin`, or in `HERMES_HOME/hermes-agent/.hermes/bin`. Falls back to `"hermes"`. |

### Types and titles

| Name | Signature | Description |
|---|---|---|
| `parse_type` | `parse_type(title, types=None) -> str` | The type whose emoji starts the title. Default `"read"`. |
| `check_name` | `check_name(name) -> str` | Strips the name and checks it against `_TITLE_OK`. Raises `SystemExit` on an empty or refused name. |
| `make_title` | `make_title(kind, name, when=None) -> str` | `"<emoji> <name> · <Mon> <day>"`. Adds ` #2`, ` #3` and so on when the title exists in `sessions`. |

### Listing

| Name | Signature | Description |
|---|---|---|
| `list_items` | `list_items(include_done=False, done_days=None) -> list[dict]` | Calls `wake_due()`, then returns open items, snoozed items, and with `include_done` the items done in the last `done_days` days. Shape: [Item](../diagrams/class-diagram.md). |
| `parse_brief` | `parse_brief(text) -> dict` | `{"source", "ask"}` from a first reply. Removes a closing `Say "read"` or `Tell me "done"` sentence when 12 or more characters stay. Cuts the ask at 280 characters. |
| `failed_jobs` | `failed_jobs() -> list[dict]` | Watched, enabled jobs whose `last_status` is not `ok`. `delivery_queued` counts as `ok`. An `ok` job whose last output contains `INBOX FAILED:` gets status `inbox_failed`. |
| `board_dbs` | `board_dbs() -> list[tuple[str, Path]]` | `("default", HERMES_HOME/kanban.db)` plus every `kanban/boards/<slug>/kanban.db` that exists. |
| `attention_cards` | `attention_cards() -> list[dict]` | Cards with status in `ATTENTION_STATUSES` (`blocked`, `triage`) on every board, newest first. Each card has the `chat_id` of the newest `local` subscription, if any. |
| `card_status` | `card_status(board, task_id) -> str \| None` | The card's `status`, or `None`. |

### Mutations

| Name | Signature | Description |
|---|---|---|
| `resolve` | `resolve(ref) -> str` | A session id from an id, an exact inbox title, or `$HERMES_SESSION_ID`. Raises `SystemExit` unless exactly one inbox item matches a title. |
| `mark_done` | `mark_done(ref) -> dict` | Archives the session. |
| `reopen` | `reopen(ref) -> dict` | Un-archives the session. |
| `snooze` | `snooze(ref, wake_at, note="") -> dict` | Archives the session and adds `{title, wake_at, note}` to `snooze.json`. |
| `wake` | `wake(ref) -> dict` | Removes the item from `snooze.json` and un-archives it. Raises `SystemExit` if the item is not snoozed. |
| `wake_due` | `wake_due() -> list[dict]` | Wakes every snooze whose `wake_at` is now or earlier. |
| `parse_when` | `parse_when(s, now=None) -> float` | Epoch seconds for `30m`, `2h`, `1d`, `1w`, `tomorrow`, `evening`, `next-week`, a weekday prefix of 3 or more letters, or an ISO date-time. |
| `settle_done_cards` | `settle_done_cards(min_age=None) -> list[dict]` | Archives inbox items whose subscribed card has status `done` and a `completed` event older than `settle_after_minutes`. Records each card in `.settled.json` for 30 days. |
| `archive_card` | `archive_card(board, task_id) -> dict` | Runs `hermes kanban --board <board> archive <id>` and checks that the status is `archived`. |
| `seed_text` | `seed_text(kind, title, source, body, reply_notes) -> str` | The seed markdown: header, body, rules for Hermes, reply notes, style line and the request for one short line. |
| `present` | `present(sid, kind, source, body, db=None) -> bool` | Hides the seed, appends the readable first reply, deactivates the original reply. Returns `False` on any failure. |
| `create_item` | `create_item(kind, name, body, source="manual", reply_notes="", timeout=600) -> dict` | Files one item. Returns `{"id", "title", "type", "seed"}`. |
| `file_failure_item` | `file_failure_item(name) -> dict` | Files a `do` item titled `Fix <job>` for a failing job, or returns the open one with `already_open: true`. |

## Internal structure

The file has seven sections, each marked with a `# ──` comment: paths and helpers, types and titles, listing, cron jobs that failed, kanban cards, snooze, auto-settle, and mutations.

Files that the module writes in the inbox folder:

| File | Writer | Content |
|---|---|---|
| `.seeds/<YYYYmmdd-HHMMSS>-<slug>.md` | `create_item` | The seed passed to `hermes chat --query-file` |
| `snooze.json` | `snooze`, `_wake_sid` | `{session_id: {title, wake_at, note}}` |
| `.settled.json` | `settle_done_cards` | `{task_id: {board, chat_id, at}}`, entries kept 30 days |

`_write_json` writes to a `.tmp` file and renames it, so a reader never sees a half-written file.

## Dependencies

- **Used by:** [plugin entry](plugin-entry.md), [dashboard](dashboard.md), [webhook](webhook.md).
- **Uses:** [settings](settings.md), `hermes_state.SessionDB`, `sqlite3`, `subprocess` (`hermes chat`, `hermes kanban`).

## Notable patterns

- Every SQL connection uses `file:<path>?mode=ro`. All writes to sessions go through `SessionDB`.
- `list_items` skips sessions with `end_reason = 'compression'`. These are the old halves of a compressed conversation.
- An item is unread while the session has no active user message other than the hidden seed.
- `create_item` reads the session id from the `session_id:` line in the `hermes chat -Q` output. It raises `SystemExit` when the line is missing, or when the stored `cwd` or title does not match afterwards.
- `_clean_env` sets `HERMES_INBOX_ITEM=1` in the child environment.
