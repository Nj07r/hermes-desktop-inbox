# API reference

The plugin has three interfaces: the `hermes desktop-inbox` CLI, the pane backend under `/api/plugins/desktop-inbox`, and the optional webhook listener on `127.0.0.1`. All three call [`desktop_inbox.core`](modules/core.md).

## CLI

Source: `_setup` and `_run` in [`__init__.py`](../../__init__.py). On a user error, the command prints the message to stderr and exits with code `1`.

| Command | Arguments | Output |
|---|---|---|
| `new` | `--type T` (required), `--title NAME` (required), `--source S` (default `manual`), `--reply-notes TEXT`, `--reply-notes-file F`, one of `--body TEXT` or `--body-file F`. Stdin is the body when no body flag is given. | JSON `{"id", "title", "type", "seed"}` |
| `list` | `--all` (also items done in the last `done_days` days), `--json` | Text lines, or JSON `{"items", "failed_jobs"}` |
| `done` | `[REF]` | `Done: <title>` |
| `reopen` | `[REF]` | `Reopened: <title>` |
| `wake` | `[REF]` | `Woke: <title>` |
| `snooze` | `[REF] [WHEN]` (default `tomorrow`), `--note TEXT` | `Snoozed until <Day HH:MM>: <title>` |
| `settle-done` | `--json` | `Settled (<board>): <title>` lines, or `Nothing to settle.` |
| `types` | none | One line per type: emoji, name, label, `how` rule |
| `webhook` | `--test` | Runs the listener in the foreground, or posts a test item and prints the response |

`REF` is a session id or the exact item title. When `REF` is empty, the command uses `$HERMES_SESSION_ID`, so it works inside an item conversation without an argument.

`WHEN` accepts `30m`, `2h`, `1d`, `1w` (also `min` and `hr`), `tomorrow`, `evening`, `next-week`, a weekday prefix of 3 or more letters, or an ISO date-time. `tomorrow`, `next-week` and weekdays use `morning_hour`. `evening` uses `evening_hour`.

## Pane backend

Source: [`dashboard/plugin_api.py`](../../dashboard/plugin_api.py). Mount point: `/api/plugins/desktop-inbox`. Every response has `ok`. On a failure, `ok` is `false` and `error` holds the message. No route raises an HTTP error.

| Method and path | Request | Response on success |
|---|---|---|
| `GET /items` | Query `all=0\|1` | `items`, `failed_jobs`, `cards`, `webhook`, `settled`, `ui` |
| `POST /done` | `{"id"}` | `{"id", "title", "done": true}` |
| `POST /reopen` | `{"id"}` | `{"id", "title", "done": false}` |
| `POST /wake` | `{"id"}` | `{"id", "title", "woke": true}` |
| `POST /snooze` | `{"id", "when", "note"}`. `when` is epoch seconds or a `WHEN` string. Default `tomorrow`. | `{"id", "title", "wake_at"}` |
| `POST /archive-card` | `{"id", "board"}`. Default board `default`. | `{"ok": true}` |
| `POST /file-failure` | `{"name"}` (the cron job name) | `{"id", "title", "type", "seed"}`, or `{"already_open": true, "id", "title"}` |

A request without `id` returns `{"ok": false, "error": "missing id"}`.

`GET /items` fields:

| Field | Type | Content |
|---|---|---|
| `items` | list of Item | From `core.list_items` |
| `failed_jobs` | list of FailedJob | From `core.failed_jobs` |
| `cards` | list of Card | From `core.attention_cards` |
| `webhook` | object | From `webhook.status`: `enabled`, `running`, `port`, and `error` or `external` when they apply |
| `settled` | list | From `core.settle_done_cards`: `{"task", "board", "settled"}` |
| `ui` | object | `types` (`label`, `icon`), `empty_title`, `empty_message`, `done_days` |

The Item, FailedJob and Card fields are in [diagrams/class-diagram.md](diagrams/class-diagram.md).

## Webhook listener

Source: [`desktop_inbox/webhook.py`](../../desktop_inbox/webhook.py). Address: `127.0.0.1:<webhook_port>`, default `8788`. Every response is JSON.

| Method and path | Credential | Result |
|---|---|---|
| `POST /item?type=T&title=NAME&source=S` | `Authorization: Bearer <webhook_token>` | Files the UTF-8 body as an item. Default `type` is `do`, default `source` is `webhook`. |
| `POST /github` or `POST /` | `X-Hub-Signature-256` HMAC with `github_webhook_secret` | Files a `do` item for `pull_request` `review_requested` aimed at the configured GitHub login. Ignores other events. |
| `GET /health` | none | `{"ok": true, "service": "desktop-inbox", "port": <port>}` |

Status codes:

| Code | Cause |
|---|---|
| `200` | Item filed, duplicate dropped (`duplicate: true`), PR item already open (`already_open: true`), or event ignored (`ignored`) |
| `400` | Missing title, unknown type, empty body, body not UTF-8, payload not a JSON object, or a title that `check_name` refuses |
| `403` | Request has an `Origin` header, credential not configured, bad token, or bad signature |
| `404` | Unknown path |
| `413` | `Content-Length` over 5 MiB |
| `500` | Exception during processing. GitHub retries on this code. |
