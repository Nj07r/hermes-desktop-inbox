# desktop-inbox code wiki

`desktop-inbox` is a Hermes plugin that adds an Inbox pane to Hermes Desktop. Cron jobs, scripts and a local webhook file items. Each item is a normal Hermes session that Hermes starts, with the content and one ask: Read, Answer, Decide or Do. The pane is a keyboard triage queue. It also lists chats that wait on the user, kanban cards in `blocked` or `triage`, and cron jobs that failed.

This wiki documents the code. For install steps and user settings, read the [project README](../../README.md).

## Key concepts

- **Item**: a Hermes session whose `cwd` is the inbox folder (default `HERMES_HOME/inbox`). The plugin keeps no item database of its own.
- **Item type**: a name with an emoji, a label, a codicon name and a `how` rule for Hermes. Four types are built in. The `types` setting adds more or changes them.
- **Seed**: the hidden first user message of an item. It holds the content and the rules that the item's Hermes obeys.
- **Ask line**: the one-line reply that Hermes writes when an item is filed. The pane shows it under the title.
- **Done, snoozed, open**: three states that come only from the session `archived` flag and `snooze.json`.
- **Needs you**: the top pane section. It shows waiting chats, attention cards and failed cron jobs.

## Entry points

| File | Runs when |
|---|---|
| [`__init__.py`](../../__init__.py) | Hermes loads the plugin. `register()` adds the `hermes desktop-inbox` CLI and the bundled skill. |
| [`dashboard/plugin_api.py`](../../dashboard/plugin_api.py) | The gateway loads the pane backend. It serves `/api/plugins/desktop-inbox/*`. |
| [`desktop/plugin.js`](../../desktop/plugin.js) | Hermes Desktop loads the window. It registers the pane and the status bar chip. |
| [`bin/inbox-item`](../../bin/inbox-item) | A user runs the optional shell alias for `hermes desktop-inbox`. |

## High-level architecture

Two front ends (the CLI and the Desktop pane) and one optional webhook listener call the same Python core. The core reads Hermes data stores as read-only SQL and writes session changes only through the public `SessionDB` API. [architecture.md](architecture.md) tells why the design has this shape.

## Module map

| Module | Purpose |
|---|---|
| [`plugin entry`](modules/plugin-entry.md) | `__init__.py`, the CLI parser and handler, and skill registration |
| [`desktop_inbox.core`](modules/core.md) | Item state, filing, listing, snooze, auto-settle, cards and failed jobs |
| [`desktop_inbox.settings`](modules/settings.md) | Settings merged from `plugin.yaml` defaults and `config.yaml`, item types and secrets |
| [`desktop_inbox.webhook`](modules/webhook.md) | The local HTTP listener for `POST /item` and GitHub review requests |
| [`dashboard`](modules/dashboard.md) | The FastAPI router that the pane calls |
| [`desktop`](modules/desktop.md) | The React pane, the status bar chip and notifications |
| [`skills/inbox`](modules/skill.md) | The bundled skill that tells Hermes how to file items and wire cron jobs |

## Other pages

- [api.md](api.md): every CLI command, pane backend route and webhook route.
- [diagrams/class-diagram.md](diagrams/class-diagram.md): the data shapes that pass between the modules.
- [diagrams/sequences.md](diagrams/sequences.md): four workflows, traced to the code.
- [getting-started.md](getting-started.md): a tutorial that installs a clone in a throwaway Hermes home and runs the test suite.

Rendered copies of every diagram are in [`img/`](img/). The Mermaid fences in the pages are the source.
