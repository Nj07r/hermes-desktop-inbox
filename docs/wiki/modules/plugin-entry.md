# Module: plugin entry (`__init__.py`)

The package root. Hermes imports it, calls `register(ctx)`, and gets the `hermes desktop-inbox` CLI and the bundled skill.

## Responsibilities

- Register the `desktop-inbox` CLI command with Hermes.
- Parse the subcommands and call the matching function in [`core`](core.md), [`settings`](settings.md) or [`webhook`](webhook.md).
- Register every `skills/*/SKILL.md` as a plugin skill.
- Turn a `SystemExit` with a string message into a stderr line and exit code `1`.

## Key files

| File | Content |
|---|---|
| [`__init__.py`](../../../__init__.py) | `register`, `_setup`, `_run`, `USAGE` |
| [`plugin.yaml`](../../../plugin.yaml) | Manifest: name, version `1.0.0`, `requires_hermes: ">=0.23.1"`, and `config_schema` for every setting |
| [`bin/inbox-item`](../../../bin/inbox-item) | `exec hermes desktop-inbox "$@"` |

## Public API

| Name | Signature | Description |
|---|---|---|
| `register` | `register(ctx) -> None` | Calls `ctx.register_cli_command(name="desktop-inbox", setup_fn=_setup, handler_fn=_run)` and `ctx.register_skill(name, path)` for each skill folder. |
| `_setup` | `_setup(parser) -> None` | Adds the subparsers `new`, `done`, `reopen`, `wake`, `snooze`, `list`, `settle-done`, `types` and `webhook`. |
| `_run` | `_run(args) -> int` | Runs one subcommand. Returns `0` on success and `1` on a usage error or a `SystemExit` message. |

The full flag list for each subcommand is in [api.md](../api.md#cli).

## Internal structure

- `_core()` imports `desktop_inbox.core` lazily, so `register()` stays cheap at Hermes startup.
- `new` reads the body from `--body`, then `--body-file`, then stdin. `--reply-notes-file` text is appended to `--reply-notes`.
- `list` prints failed jobs first (`!  job <name> <status>: <error>`), then one line per item. The flag is `✓` for done, `z` for snoozed, `•` for unread and a space for the rest. `--json` prints `{"items": [...], "failed_jobs": [...]}`.
- `webhook --test` posts a sample `read` item to `http://127.0.0.1:<port>/item` and adds the bearer token when one is set.

## Dependencies

- **Used by:** Hermes plugin loader.
- **Uses:** `desktop_inbox.core`, `desktop_inbox.settings`, `desktop_inbox.webhook`, Python standard library (`argparse`, `json`, `urllib.request`).

## Notable patterns

- The core reports user errors with `raise SystemExit("inbox: ...")`. `_run` catches only string codes, so other exits pass through.
- In the plugin host, Hermes skips `register_cli_command()`. The CLI works because Hermes wires plugin subcommands into the local CLI parser. `hermes plugins validate` prints this note.
