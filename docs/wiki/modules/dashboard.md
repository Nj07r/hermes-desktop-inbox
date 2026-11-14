# Module: `dashboard` (pane backend)

A FastAPI router that the Hermes gateway mounts at `/api/plugins/desktop-inbox`. The Desktop pane calls it for data and for every action.

## Responsibilities

- Return everything the pane shows in one `GET /items` call.
- Run the pane actions: done, reopen, snooze, wake, archive a card, file a failure item.
- Start the webhook listener when the setting is on.
- Never raise to the client. Every route returns `{"ok": false, "error": ...}` on a failure.

## Key files

| File | Content |
|---|---|
| [`dashboard/plugin_api.py`](../../../dashboard/plugin_api.py) | The router |
| [`dashboard/manifest.json`](../../../dashboard/manifest.json) | Name `desktop-inbox`, label `Inbox`, `api: plugin_api.py` |

## Public API

| Route | Handler | Calls |
|---|---|---|
| `GET /items?all=0\|1` | `items` | `reload(settings)`, `reload(core)`, `core.settle_done_cards`, `core.list_items`, `core.failed_jobs`, `core.attention_cards`, `webhook.status`, `_ui` |
| `POST /done` | `done` | `core.mark_done` |
| `POST /reopen` | `reopen` | `core.reopen` |
| `POST /wake` | `wake` | `core.wake` |
| `POST /snooze` | `snooze` | `core.snooze` with epoch seconds or `core.parse_when` |
| `POST /archive-card` | `archive_card` | `core.archive_card` |
| `POST /file-failure` | `file_failure` | `core.file_failure_item` |

Request and response fields are in [api.md](../api.md#pane-backend).

## Internal structure

- At import, the module adds the repository root to `sys.path` and imports `desktop_inbox.core`, `settings` and `webhook`. Then it calls `webhook.status()` once.
- `_ui()` returns the pane settings: `types` (label and icon only), `empty_title`, `empty_message` and `done_days`.
- `_guard(fn)` wraps a core call. It returns `{"ok": True, **result}`, or `{"ok": False, "error": str(exc)}` for `SystemExit` and other exceptions.

## Dependencies

- **Used by:** [desktop](desktop.md) through `ctx.rest()`.
- **Uses:** [core](core.md), [settings](settings.md), [webhook](webhook.md), `fastapi.APIRouter`.

## Notable patterns

- `GET /items` reloads `settings` and `core`, so backend edits apply on the next poll. Edits to this file need a gateway restart.
- Auto-settle runs inside `GET /items`. Without a pane poll or a `settle-done` command, finished cards do not close their items.
