# Module: `desktop_inbox.settings`

Reads the plugin settings and secrets, and merges the item types. Every other Python module gets configuration from here.

## Responsibilities

- Read defaults from `config_schema` in [`plugin.yaml`](../../../plugin.yaml).
- Read user values from `plugins.entries.desktop-inbox.settings` in `config.yaml`. The Desktop settings form writes the same key.
- Read the two secrets from the Hermes secret scope.
- Merge the built-in item types with the `types` setting.

## Key files

| File | Content |
|---|---|
| [`desktop_inbox/settings.py`](../../../desktop_inbox/settings.py) | The whole module |
| [`plugin.yaml`](../../../plugin.yaml) | `config_schema`: the single list of every setting and its default |

## Public API

| Name | Signature | Description |
|---|---|---|
| `PLUGIN_ID` | `str` | `"desktop-inbox"` |
| `PLUGIN_DIR` | `Path` | The repository root |
| `BUILTIN_TYPES` | `dict[str, dict]` | `read` (📖, `book`), `answer` (💬, `comment`), `decide` (⚖️, `law`), `do` (✅, `checklist`), each with a `how` rule |
| `SECRET_ENV` | `dict[str, str]` | `webhook_token` to `DESKTOP_INBOX_WEBHOOK_TOKEN`, `github_webhook_secret` to `DESKTOP_INBOX_GITHUB_SECRET` |
| `hermes_home` | `hermes_home() -> Path` | `hermes_constants.get_hermes_home()`, else `$HERMES_HOME`, else `~/.hermes` |
| `schema_defaults` | `schema_defaults() -> dict` | Defaults of all non-secret keys in `config_schema`. Cached with `lru_cache`. |
| `load` | `load() -> dict` | Defaults, then user values that are not `None` |
| `get` | `get(key, default=None)` | The value, or `default` when the value is `None` or `""` |
| `secret` | `secret(key) -> str` | `agent.secret_scope.get_secret_str(SECRET_ENV[key])`, or `""` |
| `user_name` | `user_name() -> str` | The `user_name` setting, or `"the user"` |
| `inbox_dir` | `inbox_dir() -> Path` | The `inbox_dir` setting with `~` expanded, or `HERMES_HOME/inbox` |
| `types` | `types() -> dict[str, dict]` | Built-in types merged with the `types` setting |
| `watched_jobs` | `watched_jobs() -> list[str] \| None` | Job names from `watched_jobs`, or `None` for every enabled job |

The setting keys, types and defaults are in the [project README](../../../README.md#settings).

## Internal structure

`types()` lowercases each key in the `types` setting. A new key starts from a default spec (📌, label from the key, icon `pin`, `how` "{user} handles it, then says 'done'."). An existing key keeps its built-in fields. Then the user fields replace those fields, converted to strings. Empty and `None` fields are ignored.

## Dependencies

- **Used by:** [core](core.md), [webhook](webhook.md), [dashboard](dashboard.md), [plugin entry](plugin-entry.md).
- **Uses:** `hermes_constants`, `hermes_cli.config.load_config_readonly`, `agent.secret_scope`, `yaml`. Each import is in a `try` block, so the module also loads outside Hermes.

## Notable patterns

- `load()` reads `config.yaml` on every call. The module has no cache for user values, so a settings change applies at the next call.
- The pane title emoji decides the type (`core.parse_type`), so two types with the same emoji cannot be told apart.
