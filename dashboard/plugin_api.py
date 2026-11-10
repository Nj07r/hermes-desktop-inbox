"""Inbox pane backend: /api/plugins/desktop-inbox/*."""
import importlib
import sys
from pathlib import Path

from fastapi import APIRouter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from desktop_inbox import core, settings, webhook  # noqa: E402

router = APIRouter()

try:  # start the webhook listener once when it is turned on in settings
    webhook.status()
except Exception:
    pass


def _ui() -> dict:
    """What the pane needs from settings: the item types and the empty-pane text."""
    return {
        "types": {k: {"label": v.get("label", k), "icon": v.get("icon", "pin")} for k, v in settings.types().items()},
        "empty_title": settings.get("empty_title", "Inbox zero"),
        "empty_message": settings.get("empty_message", "Nothing needs you."),
        "done_days": int(settings.get("done_days", 7)),
    }


@router.get("/items")
def items(all: int = 0):
    try:
        importlib.reload(settings)
        importlib.reload(core)  # pick up edits without a restart (webhook keeps its thread)
        settled = core.settle_done_cards()
        return {"ok": True, "items": core.list_items(include_done=bool(all)), "failed_jobs": core.failed_jobs(),
                "cards": core.attention_cards(), "webhook": webhook.status(), "settled": settled, "ui": _ui()}
    except Exception as exc:  # never raise out to the client
        return {"ok": False, "error": str(exc), "items": [], "failed_jobs": [], "cards": [],
                "webhook": {"ok": False, "running": False}, "ui": {}}


def _guard(fn):
    try:
        return {"ok": True, **fn()}
    except SystemExit as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _id(body: dict) -> str:
    return str((body or {}).get("id") or "").strip()


@router.post("/done")
def done(body: dict):
    sid = _id(body)
    return _guard(lambda: core.mark_done(sid)) if sid else {"ok": False, "error": "missing id"}


@router.post("/reopen")
def reopen(body: dict):
    sid = _id(body)
    return _guard(lambda: core.reopen(sid)) if sid else {"ok": False, "error": "missing id"}


@router.post("/wake")
def wake(body: dict):
    sid = _id(body)
    return _guard(lambda: core.wake(sid)) if sid else {"ok": False, "error": "missing id"}


@router.post("/snooze")
def snooze(body: dict):
    """Park an item until a time: {"id": ..., "when": "2h" | "tomorrow" | epoch seconds | ISO}."""
    sid = _id(body)
    if not sid:
        return {"ok": False, "error": "missing id"}
    when = str((body or {}).get("when") or "tomorrow")

    def run():
        wake_at = float(when) if when.replace(".", "", 1).isdigit() else core.parse_when(when)
        return core.snooze(sid, wake_at, str((body or {}).get("note") or ""))
    return _guard(run)


@router.post("/archive-card")
def archive_card(body: dict):
    """Archive a kanban card from the pane (moves it out of blocked/triage)."""
    tid = _id(body)
    board = str((body or {}).get("board") or "default").strip()
    if not tid:
        return {"ok": False, "error": "missing id"}
    try:
        return core.archive_card(board, tid)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/file-failure")
def file_failure(body: dict):
    """Turn a failed cron job into a Do item (or reuse the open one)."""
    name = str((body or {}).get("name") or "").strip()
    try:
        return core.file_failure_item(name)
    except SystemExit as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
