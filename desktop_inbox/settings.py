"""Settings for the Inbox plugin.

Values live in config.yaml under ``plugins.entries.desktop-inbox.settings`` (the Desktop
Settings -> Plugins form writes there too). Defaults come from ``config_schema`` in plugin.yaml,
so the manifest is the one place that lists every setting. Secrets come from Hermes's secret
scope, never from a file.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

PLUGIN_ID = "desktop-inbox"
PLUGIN_DIR = Path(__file__).resolve().parent.parent

# Built-in item types. A type sets the title emoji, the pane icon (a codicon name) and the
# rule Hermes follows in the item's conversation. "{user}" becomes the user_name setting.
BUILTIN_TYPES: dict[str, dict] = {
    "read": {"emoji": "📖", "label": "Read", "icon": "book",
             "how": "{user} reads it, then says 'read' (or 'done') to clear it."},
    "answer": {"emoji": "💬", "label": "Answer", "icon": "comment",
               "how": "{user} answers the question(s) in this conversation."},
    "decide": {"emoji": "⚖️", "label": "Decide", "icon": "law",
               "how": "{user} picks between the options in this conversation."},
    "do": {"emoji": "✅", "label": "Do", "icon": "checklist",
           "how": "{user} does the task (outside or with Hermes), then says 'done'."},
}

SECRET_ENV = {
    "webhook_token": "DESKTOP_INBOX_WEBHOOK_TOKEN",
    "github_webhook_secret": "DESKTOP_INBOX_GITHUB_SECRET",
}


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


@lru_cache(maxsize=1)
def schema_defaults() -> dict:
    try:
        import yaml
        manifest = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text()) or {}
    except Exception:
        return {}
    return {k: (v or {}).get("default") for k, v in (manifest.get("config_schema") or {}).items()
            if (v or {}).get("type") != "secret"}


def _user_settings() -> dict:
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly() or {}
    except Exception:
        return {}
    entry = ((cfg.get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID) or {}
    settings = entry.get("settings") if isinstance(entry, dict) else None
    return settings if isinstance(settings, dict) else {}


def load() -> dict:
    """All settings, user values over schema defaults. Cheap enough to call per request."""
    out = dict(schema_defaults())
    out.update({k: v for k, v in _user_settings().items() if v is not None})
    return out


def get(key: str, default=None):
    value = load().get(key)
    return default if value in (None, "") else value


def secret(key: str) -> str:
    name = SECRET_ENV[key]
    try:
        from agent.secret_scope import get_secret_str
        return get_secret_str(name, "") or ""
    except Exception:
        return ""


def user_name() -> str:
    return str(get("user_name", "the user")).strip() or "the user"


def inbox_dir() -> Path:
    raw = str(get("inbox_dir", "") or "").strip()
    return Path(raw).expanduser() if raw else hermes_home() / "inbox"


def types() -> dict[str, dict]:
    """Built-in types merged with the ``types`` setting (add new keys or override fields)."""
    merged = {k: dict(v) for k, v in BUILTIN_TYPES.items()}
    extra = get("types", {}) or {}
    if isinstance(extra, dict):
        for name, spec in extra.items():
            key = str(name).strip().lower()
            if not key or not isinstance(spec, dict):
                continue
            base = merged.get(key, {"emoji": "📌", "label": key.title(), "icon": "pin",
                                    "how": "{user} handles it, then says 'done'."})
            merged[key] = {**base, **{k: str(v) for k, v in spec.items() if v not in (None, "")}}
    return merged


def watched_jobs() -> list[str] | None:
    """Cron job names whose failures show in the pane. ``None`` = every enabled job."""
    jobs = get("watched_jobs", []) or []
    names = [str(j).strip() for j in jobs if str(j).strip()] if isinstance(jobs, list) else []
    return names or None
