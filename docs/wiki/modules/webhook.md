# Module: `desktop_inbox.webhook`

A local HTTP listener that turns generic posts and GitHub review requests into items. It is off by default (`webhook_enabled: false`).

## Responsibilities

- Serve `POST /item`, `POST /github` (also `POST /`) and `GET /health` on `127.0.0.1:<webhook_port>`.
- Check the bearer token for `/item` and the `X-Hub-Signature-256` HMAC for `/github`.
- Refuse every request that has an `Origin` header, and bodies over 5 MiB.
- Keep raw payloads in `<inbox folder>/webhook-log/`.
- Drop identical `/item` posts within 24 hours, and fold a second review request for an open PR into the open item.
- Start once inside the pane backend process, or run in the foreground from the CLI.

## Key files

| File | Content |
|---|---|
| [`desktop_inbox/webhook.py`](../../../desktop_inbox/webhook.py) | The whole module |

## Public API

| Name | Signature | Description |
|---|---|---|
| `MAX_BODY` | `int` | `5 * 1024 * 1024` |
| `DEFAULT_REVIEW_NOTES` | `str` | Reply notes for review items when `github_review_notes` is empty |
| `port` | `port() -> int` | The `webhook_port` setting, default `8788` |
| `handle_github` | `handle_github(payload, signature, event_name="") -> tuple[int, dict]` | Checks the HMAC, ignores `ping`, files a `do` item named `Review <repo> #<n>` for `review_requested` aimed at the configured login |
| `handle_item` | `handle_item(query, payload, token) -> tuple[int, dict]` | Checks the token, `type`, `title` and body, then files the item |
| `Handler` | `BaseHTTPRequestHandler` | Routes requests to the two handlers. Returns `500` on an exception so that the sender retries. |
| `status` | `status() -> dict` | `{"enabled": False}` when off, else the result of `ensure_server()` |
| `ensure_server` | `ensure_server() -> dict` | Starts the listener on a daemon thread once. Safe to call on every poll. |
| `serve_forever` | `serve_forever() -> None` | Runs the listener in the foreground. Used by `hermes desktop-inbox webhook`. |

Status codes and response bodies are in [api.md](../api.md#webhook-listener).

## Internal structure

- `_github_login()` uses the `github_login` setting. When it is empty, the module runs `gh api user --jq .login` once and caches the result in `_login_cache`.
- `handle_item` stores dedup keys in `<inbox folder>/.webhook-dedup.json`. The key is a SHA-256 of `type:title:body`. A key is saved only after the item is filed, so a failed post can be retried.
- `ensure_server` holds `_lock`. If the port is taken, it calls `/health` on that port. When the answer has `service: "desktop-inbox"`, the result is `running: True, external: True`.

## Dependencies

- **Used by:** [dashboard](dashboard.md) (`webhook.status()` at import and on each `/items` poll), [plugin entry](plugin-entry.md).
- **Uses:** [core](core.md) (`create_item`, `list_items`, `inbox_dir`, `_read_json`, `_write_json`), [settings](settings.md), `http.server`, `hmac`.

## Notable patterns

- Each item starts a real `hermes chat` turn, so each route needs its own credential. `/item` returns `403` until `webhook_token` is set, and `/github` until `github_webhook_secret` is set.
- `ensure_server` and `serve_forever` do not start while both credentials are empty.
- The token and HMAC checks use `hmac.compare_digest`.
