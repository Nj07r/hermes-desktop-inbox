# Sequence diagrams

Four workflows, each traced to the functions that run them.

## Workflow: file an item from the CLI

A cron job or a script runs `hermes desktop-inbox new`. This takes one model turn, so it runs for 10 to 30 seconds.

```mermaid
---
title: file an item
---
sequenceDiagram
    participant Job as Cron job or script
    participant CLI as __init__._run
    participant Core as core.create_item
    participant Chat as hermes chat
    participant DB as SessionDB
    Job->>CLI: hermes desktop-inbox new --type --title
    CLI->>Core: create_item(kind, name, body, source, notes)
    Core->>Core: check_name, make_title, seed_text
    Core->>Chat: chat -Q --source gui --query-file seed
    Chat-->>Core: session_id and one-line ask
    Core->>DB: update_session_cwd, set_session_title
    Core->>DB: present(): hide seed, append reply, deactivate ask
    Core-->>CLI: id, title, type, seed
    CLI-->>Job: JSON on stdout
```

### Walkthrough

1. **Parse**: `_run` in [`__init__.py`](../../../__init__.py) reads the body from `--body`, `--body-file` or stdin.
2. **Check**: `create_item` in [`core.py`](../../../desktop_inbox/core.py) checks the type and the name, and refuses an empty body.
3. **Seed**: `seed_text` writes `.seeds/<stamp>-<slug>.md` in the inbox folder.
4. **Turn**: `subprocess.run` starts `hermes chat` in the inbox folder with the environment from `_clean_env`.
5. **File**: `SessionDB` moves the session to the inbox folder and sets the title.
6. **Present**: `present` rewrites the first reply. A failure here leaves a plainer item.
7. **Verify**: `create_item` reads the row again and raises `SystemExit` when `cwd` or the title does not match.

## Workflow: pane poll

The pane asks for its data every 60 seconds. This poll also wakes snoozes and settles finished cards.

```mermaid
---
title: pane poll
---
sequenceDiagram
    participant Pane as plugin.js useItems
    participant API as plugin_api.items
    participant Core as core
    participant DB as state.db and kanban.db
    participant WH as webhook.status
    Pane->>API: GET /items?all=0
    API->>API: reload(settings), reload(core)
    API->>Core: settle_done_cards()
    Core->>DB: done cards with a local subscription
    API->>Core: list_items()
    Core->>Core: wake_due()
    Core->>DB: inbox sessions, first replies, reply counts
    API->>Core: failed_jobs(), attention_cards()
    API->>WH: status()
    API-->>Pane: items, failed_jobs, cards, webhook, settled, ui
    Pane->>Pane: InboxChip announces new keys
```

### Walkthrough

1. **Query**: `useItems` in [`plugin.js`](../../../desktop/plugin.js) calls `ctx.rest('/items?all=0')`.
2. **Reload**: `items` in [`plugin_api.py`](../../../dashboard/plugin_api.py) reloads `settings` and `core`.
3. **Settle**: `settle_done_cards` archives inbox items whose card is done.
4. **List**: `list_items` wakes due snoozes, then reads sessions and briefs.
5. **Attention**: `failed_jobs` reads `cron/jobs.json`. `attention_cards` reads every `kanban.db`.
6. **Notify**: `InboxChip` compares keys with `ctx.storage` through `diffSeen` and calls `announce`.

## Workflow: GitHub review request

GitHub posts a `pull_request` event with action `review_requested` to the listener, through a tunnel.

```mermaid
---
title: GitHub review request
---
sequenceDiagram
    participant GH as GitHub
    participant H as webhook.Handler
    participant HG as handle_github
    participant Core as core
    GH->>H: POST /github, X-Hub-Signature-256
    H->>H: refuse Origin header and body over 5 MiB
    H->>HG: payload, signature, event name
    HG->>HG: compare HMAC with github_webhook_secret
    HG->>HG: reviewer equals _github_login()
    HG->>Core: list_items(), is the review item open?
    HG->>Core: create_item("do", "Review repo #n")
    Core-->>HG: id, title
    HG-->>H: 200 and item
    H-->>GH: JSON response
```

### Walkthrough

1. **Guard**: `Handler.do_POST` in [`webhook.py`](../../../desktop_inbox/webhook.py) refuses browser requests and large bodies.
2. **Verify**: `handle_github` returns `403` when the secret is not set or the HMAC does not match.
3. **Filter**: the handler ignores `ping`, events without a `pull_request`, and reviews asked of another login.
4. **Fold**: `_already_open` returns the open item for the same PR, and no second item is filed.
5. **File**: `create_item` files a `do` item with `github_review_notes` or `DEFAULT_REVIEW_NOTES` as reply notes.

## Workflow: snooze and wake

The user presses `s` on a row and picks a time. Later, a poll wakes the item.

```mermaid
---
title: snooze and wake
---
sequenceDiagram
    participant User
    participant Pane as plugin.js
    participant API as plugin_api
    participant Core as core
    participant DB as SessionDB
    User->>Pane: s, then 1 to 4
    Pane->>Pane: patchItem wake_at, show Undo
    Pane->>API: POST /snooze id, when as epoch
    API->>Core: snooze(id, wake_at)
    Core->>DB: set_session_archived(true)
    Core->>Core: write snooze.json
    Note over Pane,Core: later, a GET /items poll
    API->>Core: list_items() calls wake_due()
    Core->>DB: set_session_archived(false)
    Core->>Core: remove entry from snooze.json
    Pane->>User: Snooze over notification
```

### Walkthrough

1. **Pick**: `SnoozeMenu` in [`plugin.js`](../../../desktop/plugin.js) gives four times from `snoozeOptions()`.
2. **Apply**: `makeActions().snooze` patches the cache, posts `/snooze` and registers an undo that posts `/wake`.
3. **Store**: `core.snooze` archives the session and adds the entry to `snooze.json`.
4. **Wake**: `wake_due` runs at the start of every `list_items` call and un-archives due items.
5. **Announce**: `InboxChip` compares the `snoozedAt` map in `ctx.storage` with the open items and announces items whose wake time has passed.
