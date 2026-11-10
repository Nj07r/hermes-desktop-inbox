"""Inbox core: items are normal Desktop conversations filed under the inbox folder.

Shared by the `hermes desktop-inbox` CLI and the Desktop pane backend (dashboard/plugin_api.py).

State model (no extra database):
  open    = a session in the inbox folder that the user has not archived
            (rows hidden by the sessions.auto_archive idle sweep still count as open)
  snoozed = archived on purpose and listed in snooze.json until its wake time
  done    = archived on purpose (archived=1 AND auto_archived=0)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import settings

# ── paths and helpers ────────────────────────────────────────────────────


def inbox_dir() -> Path:
    return settings.inbox_dir()


def _state_db() -> Path:
    return settings.hermes_home() / "state.db"


def hermes_bin() -> str:
    for cand in (shutil.which("hermes"), Path.home() / ".local/bin/hermes",
                 settings.hermes_home() / "hermes-agent/.hermes/bin/hermes"):
        if cand and Path(cand).exists():
            return str(cand)
    return "hermes"


def _session_db():
    from hermes_state import SessionDB
    return SessionDB()


def _ro_conn(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path or _state_db()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))
    tmp.replace(path)


# ── types and titles ─────────────────────────────────────────────────────


def parse_type(title: str, types: dict | None = None) -> str:
    title = title or ""
    for kind, spec in (types or settings.types()).items():
        if spec.get("emoji") and title.startswith(spec["emoji"]):
            return kind
    return "read"


def make_title(kind: str, name: str, when: datetime | None = None) -> str:
    when = when or datetime.now()
    emoji = settings.types()[kind].get("emoji", "")
    base = f"{emoji} {name.strip()} · {when.strftime('%b')} {when.day}".strip()
    with _ro_conn() as conn:
        title, n = base, 2
        while conn.execute("SELECT 1 FROM sessions WHERE title = ? LIMIT 1", (title,)).fetchone():
            title, n = f"{base} #{n}", n + 1
    return title


# ── listing ──────────────────────────────────────────────────────────────


def list_items(include_done: bool = False, done_days: int | None = None) -> list[dict]:
    wake_due()  # opportunistic: the pane polls this every minute
    done_days = int(done_days or settings.get("done_days", 7))
    folder = str(inbox_dir())
    snoozes = load_snoozes()
    types = settings.types()
    cols = ("id, title, started_at, last_activity_at, archived, "
            "COALESCE(auto_archived, 0) AS auto_archived")
    base = "cwd = ? AND COALESCE(end_reason, '') <> 'compression'"
    queries = [(f"SELECT {cols} FROM sessions WHERE {base} "
                "AND (archived = 0 OR COALESCE(auto_archived, 0) = 1)", [folder])]
    if include_done:
        queries.append((f"SELECT {cols} FROM sessions WHERE {base} AND archived = 1 "
                        "AND COALESCE(auto_archived, 0) = 0 AND started_at > ?",
                        [folder, time.time() - done_days * 86400]))
    if snoozes:  # snoozed rows are archived on purpose, so they need their own query
        queries.append((f"SELECT {cols} FROM sessions WHERE id IN ({','.join('?' * len(snoozes))})",
                        list(snoozes)))
    rows, seen = [], set()
    with _ro_conn() as conn:
        for sql, params in queries:
            for r in conn.execute(sql, params):
                if r["id"] not in seen:
                    seen.add(r["id"])
                    rows.append(dict(r))
    briefs = _briefs([r["id"] for r in rows])
    out = []
    for r in rows:
        wake_at = (snoozes.get(r["id"]) or {}).get("wake_at")
        done = bool(r["archived"]) and not r["auto_archived"] and not wake_at
        brief = briefs.get(r["id"], {})
        out.append({
            "id": r["id"],
            "title": r["title"] or "(untitled)",
            "type": parse_type(r["title"] or "", types),
            "started_at": r["started_at"],
            "last_activity_at": r["last_activity_at"] or r["started_at"] or 0,
            # Unread = the user has not written in the conversation yet.
            "unread": not brief.get("replies") and not done,
            "done": done,
            "wake_at": wake_at,
            "source": brief.get("source", ""),
            "ask": brief.get("ask", ""),
        })
    return out


# The presented first reply reads: _📥 From `job` · 📖 Read_ ... --- **<ask>**
_SOURCE_RE = re.compile(r"From `([^`]+)`")
_ASK_RE = re.compile(r"\n---\s*\n\*\*(.+?)\*\*\s*$", re.S)
# Closing boilerplate the pane does not need ('Say "read" when you are done.'). Only a whole
# closing sentence that STARTS with Say / Tell me; an instruction that ends in 'and say "done"'
# is the real ask and stays.
_CLOSER_RE = re.compile(r"(?:^|(?<=[.?!])\s+)(?:Say|Tell me)\b[^.?!]*\"(?:read|done)\"[^.?!]*[.?!]?\s*$")


def parse_brief(text: str) -> dict:
    """The job that filed an item and its one-line ask, read from the first Hermes reply."""
    text = str(text or "")
    src = _SOURCE_RE.search(text[:300])
    m = _ASK_RE.search(text)
    ask = " ".join((m.group(1) if m else "").split())
    trimmed = _CLOSER_RE.sub("", ask).strip()
    return {"source": src.group(1) if src else "", "ask": (trimmed if len(trimmed) >= 12 else ask)[:280]}


def _briefs(ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    first_reply = (f"SELECT m.session_id, m.content FROM messages m WHERE m.id IN ("
                   f"SELECT MIN(id) FROM messages WHERE session_id IN ({marks}) "
                   "AND role = 'assistant' AND active = 1 GROUP BY session_id)")
    replies = (f"SELECT session_id, COUNT(*) AS n FROM messages WHERE session_id IN ({marks}) "
               "AND role = 'user' AND active = 1 AND COALESCE(display_kind, '') <> 'hidden' "
               "GROUP BY session_id")
    out: dict[str, dict] = {}
    try:
        with _ro_conn() as conn:
            for row in conn.execute(first_reply, ids):
                out[row["session_id"]] = parse_brief(row["content"])
            for row in conn.execute(replies, ids):
                out.setdefault(row["session_id"], {})["replies"] = row["n"]
    except Exception:
        return {}
    return out


# ── cron jobs that failed ────────────────────────────────────────────────


def failed_jobs() -> list[dict]:
    """Watched cron jobs whose last run did not end ok, so failures land in the pane too."""
    data = _read_json(settings.hermes_home() / "cron" / "jobs.json")
    jobs = data.get("jobs", []) if data else []
    watched = settings.watched_jobs()
    out = []
    for j in jobs if isinstance(jobs, list) else []:
        if j.get("enabled") is False or (watched is not None and j.get("name") not in watched):
            continue
        status = j.get("last_status")
        if status == "delivery_queued":  # the run succeeded; only a delivery receipt is pending
            status = "ok"
        if status and status != "ok":
            out.append({"id": j.get("id"), "name": j.get("name"), "status": status,
                        "error": (j.get("last_error") or j.get("last_delivery_error") or "")[:300],
                        "at": j.get("last_run_at")})
        elif status == "ok" and (err := _inbox_failure_in_last_output(j.get("id"))):
            # The run succeeded but filing its item did not: the job's final answer contains
            # 'INBOX FAILED: <error>' (see the bundled skill).
            out.append({"id": j.get("id"), "name": j.get("name"), "status": "inbox_failed",
                        "error": err[:300], "at": j.get("last_run_at")})
    return out


def _inbox_failure_in_last_output(job_id: str | None) -> str:
    if not job_id:
        return ""
    runs = sorted((settings.hermes_home() / "cron" / "output" / job_id).glob("*.md"))
    if not runs:
        return ""
    text = runs[-1].read_text(errors="replace")
    response = text.rsplit("## Response", 1)[-1] if "## Response" in text else text[-4000:]
    m = re.search(r"INBOX FAILED:?\s*(.*)", response)
    return (m.group(1).strip() or "filing the inbox item failed") if m else ""


# ── kanban cards that need the user ──────────────────────────────────────

ATTENTION_STATUSES = ("blocked", "triage")


def board_dbs() -> list[tuple[str, Path]]:
    home = settings.hermes_home()
    boards = [("default", home / "kanban.db")]
    boards += [(p.parent.name, p) for p in sorted((home / "kanban" / "boards").glob("*/kanban.db"))]
    return [(slug, p) for slug, p in boards if p.exists()]


def attention_cards() -> list[dict]:
    """Blocked / triage cards on every board, each linked to the Desktop chat that subscribed to
    it (the chat that created it), so the pane opens THAT chat."""
    out = []
    marks = ",".join("?" * len(ATTENTION_STATUSES))
    for board, path in board_dbs():
        try:
            with _ro_conn(path) as conn:
                rows = conn.execute(
                    f"SELECT id, title, status, assignee, block_kind, created_at FROM tasks WHERE status IN ({marks})",
                    ATTENTION_STATUSES).fetchall()
                for t in rows:
                    ev = conn.execute(
                        "SELECT kind, payload, created_at FROM task_events WHERE task_id = ? "
                        "AND kind IN ('blocked','block_loop_detected','specified','created') ORDER BY id DESC LIMIT 1",
                        (t["id"],)).fetchone()
                    sub = conn.execute(
                        "SELECT chat_id FROM kanban_notify_subs WHERE task_id = ? AND platform = 'local' "
                        "ORDER BY rowid DESC LIMIT 1", (t["id"],)).fetchone()
                    reason = ""
                    if ev and ev["payload"]:
                        try:
                            p = json.loads(ev["payload"])
                            reason = str(p.get("reason") or p.get("summary") or "") if isinstance(p, dict) else ""
                        except Exception:
                            reason = ""
                    out.append({
                        "id": t["id"], "board": board, "title": t["title"], "status": t["status"],
                        "block_kind": t["block_kind"], "assignee": t["assignee"],
                        "reason": " ".join(reason.split())[:300],
                        "since": (ev["created_at"] if ev else None) or t["created_at"],
                        "chat_id": sub["chat_id"] if sub else None,
                    })
        except Exception:
            continue
    return sorted(out, key=lambda c: c["since"] or 0, reverse=True)


def card_status(board: str, task_id: str) -> str | None:
    path = dict(board_dbs()).get(board)
    if not path:
        return None
    try:
        with _ro_conn(path) as conn:
            row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def archive_card(board: str, task_id: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", board or "") or not re.fullmatch(r"[A-Za-z0-9._-]+", task_id or ""):
        return {"ok": False, "error": "bad board or card id"}
    proc = subprocess.run([hermes_bin(), "kanban", "--board", board, "archive", task_id],
                          capture_output=True, text=True, timeout=60)
    status = card_status(board, task_id)
    if proc.returncode != 0 or status != "archived":
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return {"ok": False, "error": out[-300:] or f"status is {status}"}
    return {"ok": True}


# ── snooze ───────────────────────────────────────────────────────────────
# A snoozed item is archived on purpose and remembered here; wake_due() un-archives it when
# the time passes (called from list_items, so the pane poll or any CLI list wakes things).


def _snooze_path() -> Path:
    return inbox_dir() / "snooze.json"


def load_snoozes() -> dict[str, dict]:
    return _read_json(_snooze_path())


def snooze(ref: str | None, wake_at: float, note: str = "") -> dict:
    sid = resolve(ref)
    row = _assert_inbox_session(sid)
    _session_db().set_session_archived(sid, True)
    data = load_snoozes()
    data[sid] = {"title": row["title"], "wake_at": float(wake_at), "note": note.strip()}
    _write_json(_snooze_path(), data)
    return {"id": sid, "title": row["title"], "wake_at": float(wake_at)}


def _wake_sid(sid: str) -> dict:
    data = load_snoozes()
    entry = data.pop(sid, None)
    _write_json(_snooze_path(), data)
    with _ro_conn() as conn:
        row = conn.execute("SELECT title, archived FROM sessions WHERE id = ?", (sid,)).fetchone()
    if row and row["archived"]:
        _session_db().set_session_archived(sid, False)
    return {"id": sid, "title": row["title"] if row else (entry or {}).get("title", ""), "woke": True}


def wake(ref: str | None) -> dict:
    sid = resolve(ref)
    if sid not in load_snoozes():
        raise SystemExit("inbox: that item is not snoozed")
    return _wake_sid(sid)


def wake_due() -> list[dict]:
    now = time.time()
    return [_wake_sid(sid) for sid, v in load_snoozes().items() if (v.get("wake_at") or 0) <= now]


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def parse_when(s: str, now: datetime | None = None) -> float:
    """'30m' | '2h' | '1d' | '1w' | 'tomorrow' | 'evening' | 'next-week' | 'mon'.. | ISO datetime."""
    s = (s or "").strip().lower()
    now = now or datetime.now()
    morning = int(settings.get("morning_hour", 9))
    evening = int(settings.get("evening_hour", 20))
    tomorrow = now.replace(hour=morning, minute=0, second=0, microsecond=0) + timedelta(days=1)
    if s == "tomorrow":
        return tomorrow.timestamp()
    if s == "evening":
        eve = now.replace(hour=evening, minute=0, second=0, microsecond=0)
        return (eve if eve > now else eve + timedelta(days=1)).timestamp()
    if s in ("next-week", "nextweek"):
        return (tomorrow + timedelta(days=(7 - tomorrow.weekday()) % 7)).timestamp()
    if (m := re.fullmatch(r"(\d+)\s*(m|min|h|hr|d|w)", s)):
        n, unit = int(m.group(1)), m.group(2)
        return now.timestamp() + n * {"m": 60, "min": 60, "h": 3600, "hr": 3600, "d": 86400, "w": 604800}[unit]
    for i, name in enumerate(_WEEKDAYS):
        if len(s) >= 3 and name.startswith(s):
            days_ahead = (i - now.weekday()) % 7 or 7  # strictly the next one; today counts as past
            target = now + timedelta(days=days_ahead)
            return target.replace(hour=morning, minute=0, second=0, microsecond=0).timestamp()
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        raise SystemExit(f"inbox: cannot read the time '{s}' "
                         "(use 30m, 2h, 1d, tomorrow, evening, next-week, a weekday, or ISO)")


# ── auto-settle: a finished card settles the inbox item subscribed to it ──


def settle_done_cards(min_age: int | None = None) -> list[dict]:
    if min_age is None:
        min_age = int(settings.get("settle_after_minutes", 15)) * 60
    folder = str(inbox_dir())
    path = inbox_dir() / ".settled.json"
    state = _read_json(path)
    out = []
    for board, db_path in board_dbs():
        try:
            with _ro_conn(db_path) as conn:
                rows = conn.execute(
                    """SELECT t.id AS task_id, s.chat_id, MAX(e.created_at) AS done_at
                       FROM tasks t
                       JOIN task_events e ON e.task_id = t.id AND e.kind = 'completed'
                       JOIN kanban_notify_subs s ON s.task_id = t.id AND s.platform = 'local'
                       WHERE t.status = 'done' GROUP BY t.id, s.chat_id""").fetchall()
        except Exception:
            continue
        for r in rows:
            if r["task_id"] in state or (r["done_at"] and time.time() - r["done_at"] < min_age):
                continue
            state[r["task_id"]] = {"board": board, "chat_id": r["chat_id"], "at": time.time()}
            with _ro_conn() as conn:
                sess = conn.execute("SELECT title, cwd, archived FROM sessions WHERE id = ?",
                                    (r["chat_id"],)).fetchone()
            if sess is None or sess["cwd"] != folder or sess["archived"]:
                continue  # only inbox items settle; other subscribed chats keep their history
            _session_db().set_session_archived(r["chat_id"], True)
            out.append({"task": r["task_id"], "board": board, "settled": sess["title"]})
    if inbox_dir().exists() or state:
        _write_json(path, {k: v for k, v in state.items() if time.time() - v.get("at", 0) < 30 * 86400})
    return out


# ── mutations ────────────────────────────────────────────────────────────


def _assert_inbox_session(session_id: str) -> dict:
    with _ro_conn() as conn:
        row = conn.execute("SELECT id, title, cwd FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise SystemExit(f"inbox: no session {session_id}")
    if row["cwd"] != str(inbox_dir()):
        raise SystemExit(f"inbox: session {session_id} is not an inbox item (cwd={row['cwd']})")
    return dict(row)


def resolve(ref: str | None) -> str:
    """Session id from an id, an exact title, or $HERMES_SESSION_ID."""
    ref = (ref or "").strip() or os.environ.get("HERMES_SESSION_ID", "")
    if not ref:
        raise SystemExit("inbox: give a session id or exact title (or run inside the item's conversation)")
    with _ro_conn() as conn:
        if conn.execute("SELECT 1 FROM sessions WHERE id = ?", (ref,)).fetchone():
            return ref
        rows = conn.execute("SELECT id FROM sessions WHERE title = ? AND cwd = ?",
                            (ref, str(inbox_dir()))).fetchall()
    if len(rows) != 1:
        raise SystemExit(f"inbox: '{ref}' matched {len(rows)} inbox items")
    return rows[0]["id"]


def mark_done(ref: str | None) -> dict:
    sid = resolve(ref)
    row = _assert_inbox_session(sid)
    _session_db().set_session_archived(sid, True)
    return {"id": sid, "title": row["title"], "done": True}


def reopen(ref: str | None) -> dict:
    sid = resolve(ref)
    row = _assert_inbox_session(sid)
    _session_db().set_session_archived(sid, False)
    return {"id": sid, "title": row["title"], "done": False}


def seed_text(kind: str, title: str, source: str, body: str, reply_notes: str) -> str:
    spec = settings.types()[kind]
    user = settings.user_name()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    how = str(spec.get("how", "")).replace("{user}", user)
    notes = "".join(f"\n- {ln.strip().lstrip('-').strip()}" for ln in reply_notes.splitlines() if ln.strip())
    style = str(settings.get("reply_style", "") or "").strip()
    style_line = f"\n- Style: {style}" if style else ""
    return f"""📥 **Inbox item** · {spec['emoji']} {spec['label']} · from `{source}` · {stamp}

{body.strip()}

---
_For Hermes (how to handle this conversation):_
- This conversation is an item in {user}'s Hermes Inbox, titled "{title}". The content above was prepared by `{source}`; do not repeat it back.
- Ask type: {spec['label']}. {how}
- When {user} marks it done ("done", "read", "skip", "clear it"), run `hermes desktop-inbox done "{title}"` with the terminal tool, then confirm in one short line. If only part of it is handled, leave it open and say what is left.{notes}{style_line}

Reply now with ONE short line telling {user} what this item needs from them."""


def _clean_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        # A cron (or any) parent session's identity must not leak into the new conversation.
        if key.startswith(("HERMES_CRON", "HERMES_SESSION", "HERMES_KANBAN")):
            env.pop(key)
    env["HERMES_INBOX_ITEM"] = "1"
    return env


def present(sid: str, kind: str, source: str, body: str, db=None) -> bool:
    """Make the conversation read like Hermes started it.

    `hermes chat` stores the seed (instructions + content) as the first USER message, which
    Desktop paints as the user's own bubble with raw markdown. So: mark the seed row hidden (the
    model still sees it, the UI never paints it) and replace Hermes's one-line reply with the
    readable message: header, the content, then the ask. Public SessionDB calls only. Best
    effort: on any failure the item still works, it just looks plainer.
    """
    db = db or _session_db()
    try:
        msgs = db.get_messages(sid)
        user = next((m for m in msgs if m.get("role") == "user"), None)
        reply = next((m for m in msgs if m.get("role") == "assistant" and m.get("content")), None)
        if not user or not reply:
            return False
        db.set_latest_matching_message_display_kind(sid, role="user", content=user["content"],
                                                    display_kind="hidden")
        spec = settings.types()[kind]
        ask = " ".join(str(reply["content"] or "").split()) or f"{spec['label']} needed."
        text = f"_📥 From `{source}` · {spec['emoji']} {spec['label']}_\n\n{body.strip()}\n\n---\n**{ask}**"
        db.append_message(sid, "assistant", text)
        db.deactivate_message(sid, int(reply["id"]))
        return True
    except Exception:
        return False


def create_item(kind: str, name: str, body: str, source: str = "manual", reply_notes: str = "",
                timeout: int = 600) -> dict:
    kind = (kind or "").strip().lower()
    types = settings.types()
    if kind not in types:
        raise SystemExit(f"inbox: --type must be one of {', '.join(types)}")
    if not (name or "").strip():
        raise SystemExit("inbox: empty title")
    if not (body or "").strip():
        raise SystemExit("inbox: empty body, nothing to file")
    folder = inbox_dir()
    seeds = folder / ".seeds"
    seeds.mkdir(parents=True, exist_ok=True)
    title = make_title(kind, name)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "item"
    seed = seeds / f"{datetime.now():%Y%m%d-%H%M%S}-{slug}.md"
    seed.write_text(seed_text(kind, title, source, body, reply_notes))
    proc = subprocess.run(
        [hermes_bin(), "chat", "-Q", "--source", "gui", "--query-file", str(seed)],
        cwd=str(folder), env=_clean_env(), capture_output=True, text=True, timeout=timeout,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    m = re.search(r"session_id:\s*(\S+)", out)
    if not m:
        raise SystemExit(f"inbox: hermes chat did not return a session id (exit {proc.returncode}):\n{out[-1500:]}")
    sid = m.group(1)
    db = _session_db()
    db.update_session_cwd(sid, str(folder))  # files it under the Inbox project
    db.set_session_title(sid, title)
    present(sid, kind, source, body, db)
    with _ro_conn() as conn:
        row = conn.execute("SELECT title, cwd FROM sessions WHERE id = ?", (sid,)).fetchone()
    if row is None or row["cwd"] != str(folder) or row["title"] != title:
        raise SystemExit(f"inbox: created {sid} but could not file it (row={dict(row) if row else None})")
    return {"id": sid, "title": title, "type": kind, "seed": str(seed)}


def file_failure_item(name: str) -> dict:
    """Turn a failed cron job into a Do item, unless one is already open."""
    job = next((j for j in failed_jobs() if j["name"] == name), None)
    if job is None:
        return {"ok": False, "error": f"{name or 'job'} is not failing"}
    label = f"Fix {name}"
    prefix = f"{settings.types()['do']['emoji']} {label} ·"
    existing = next((it for it in list_items() if it["title"].startswith(prefix)), None)
    if existing:
        return {"ok": True, "already_open": True, "id": existing["id"], "title": existing["title"]}
    user = settings.user_name()
    text = (f"The cron job `{name}` (id {job['id']}) last ended with status `{job['status']}` "
            f"at {job['at']}:\n\n```\n{job['error']}\n```")
    notes = (f"{user} opened this to get the job fixed. Your one-line reply: what failed, and that you will "
             f"fix it when {user} says go. On the next message, diagnose with `hermes cron list` and "
             f"`hermes cron runs {job['id']}`, fix local config yourself, and ask before anything that needs "
             "a login or touches anything outside this machine. Mark done once a manual `hermes cron run` succeeds.")
    return {"ok": True, **create_item("do", label, text, source="inbox pane", reply_notes=notes)}
