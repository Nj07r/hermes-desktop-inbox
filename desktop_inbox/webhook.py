"""Local webhook listener: GitHub review requests and generic posts become inbox items.

Off by default (setting ``webhook_enabled``). When on, it runs as a daemon thread inside the
process that serves the pane backend, or standalone with ``hermes desktop-inbox webhook``. It
binds 127.0.0.1 only; to let GitHub in, put it behind a tunnel (``tailscale funnel <port>``,
an smee.io forwarder) and set ``github_webhook_secret``.

Routes:
  POST /item?type=do&title=Name&source=webhook   body text becomes the item body
  POST /github                                   GitHub webhook (pull_request review_requested, ping)
  GET  /health

Durability: raw payloads land in <inbox>/webhook-log/ before processing; a processing failure
returns 500 so the sender retries. A second review request for the same open PR folds into the
open item, and identical generic posts within 24 h are dropped.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import core, settings

MAX_BODY = 5 * 1024 * 1024
DEFAULT_REVIEW_NOTES = (
    "{user} was asked to review this PR. One-line reply: what the PR is. When {user} says go "
    "(\"review it\"), fetch the diff with gh and review it here; findings first, and ask before "
    "anything that posts to GitHub. Mark done once {user} says the review is filed."
)
_login_cache: str | None = None


def port() -> int:
    return int(settings.get("webhook_port", 8788))


def _github_login() -> str:
    global _login_cache
    configured = str(settings.get("github_login", "") or "").strip().lstrip("@")
    if configured:
        return configured
    if _login_cache is None:
        try:
            out = subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True, timeout=15)
            _login_cache = out.stdout.strip().lstrip("@") if out.returncode == 0 else ""
        except Exception:
            _login_cache = ""
    return _login_cache


def _log(name: str, payload: bytes, note: str) -> None:
    try:
        log_dir = core.inbox_dir() / "webhook-log"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (log_dir / f"{stamp}-{name}.json").write_bytes(payload)
        if note:
            (log_dir / f"{stamp}-{name}.note").write_text(note)
    except Exception:
        pass  # logging must never break the response


def _already_open(title_prefix: str) -> dict | None:
    return next((it for it in core.list_items() if it["title"].startswith(title_prefix)), None)


def handle_github(payload: bytes, signature: str, event_name: str = "") -> tuple[int, dict]:
    secret = settings.secret("github_webhook_secret")
    if secret:
        expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature):
            _log("github-rejected", payload, "bad signature")
            return 403, {"ok": False, "error": "bad signature"}
    try:
        event = json.loads(payload or b"{}")
    except Exception:
        return 400, {"ok": False, "error": "payload is not JSON"}
    if not isinstance(event, dict):
        return 400, {"ok": False, "error": "payload is not a JSON object"}

    action = str(event.get("action") or "")
    if event_name == "ping" or event.get("zen") is not None:
        return 200, {"ok": True, "ignored": "ping"}
    pr = event.get("pull_request") or {}
    if not pr:
        _log(f"github-{event_name or action or 'unknown'}", payload, "unhandled event")
        return 200, {"ok": True, "ignored": f"event {event_name or action or '?'}"}

    repo = (event.get("repository") or {}).get("full_name") or "repo"
    number, title, url = pr.get("number"), pr.get("title") or "(untitled)", pr.get("html_url") or ""
    name = f"Review {repo} #{number}"

    if action == "review_requested":
        reviewer = (event.get("requested_reviewer") or {}).get("login") or ""
        me = _github_login()
        if reviewer and me and reviewer.lstrip("@").lower() != me.lower():
            return 200, {"ok": True, "ignored": f"review asked of {reviewer}, not {me}"}
        do = settings.types()["do"]
        if existing := _already_open(f"{do['emoji']} {name} ·"):
            return 200, {"ok": True, "already_open": True, "id": existing["id"], "title": existing["title"]}
        actor = (event.get("sender") or {}).get("login") or "someone"
        base = (pr.get("base") or {}).get("ref") or ""
        head = (pr.get("head") or {}).get("ref") or ""
        body = (f"**{repo} #{number}: {title}**\n{url}\n\n"
                f"Review requested by @{actor} · `{head}` → `{base}`\n\n"
                f"_Filed from the GitHub webhook._")
        notes = str(settings.get("github_review_notes", "") or DEFAULT_REVIEW_NOTES)
        _log(f"pr-review-{repo.replace('/', '-')}-{number}", payload, "")
        item = core.create_item("do", name, body, source="github webhook",
                                reply_notes=notes.replace("{user}", settings.user_name()))
        return 200, {"ok": True, "id": item["id"], "title": item["title"]}

    _log(f"github-{action or 'unknown'}", payload, "unhandled action")
    return 200, {"ok": True, "ignored": f"pull_request {action}"}


def handle_item(query: dict, payload: bytes, token: str) -> tuple[int, dict]:
    expected = settings.secret("webhook_token")
    if expected and not hmac.compare_digest(token or "", expected):
        return 403, {"ok": False, "error": "bad token"}
    kind = (query.get("type") or "do").lower()
    name = (query.get("title") or "").strip()
    source = (query.get("source") or "webhook").strip()
    if not name:
        return 400, {"ok": False, "error": "missing title"}
    if kind not in settings.types():
        return 400, {"ok": False, "error": f"unknown type {kind}"}
    try:
        body = payload.decode("utf-8").strip()
    except UnicodeDecodeError:
        return 400, {"ok": False, "error": "body is not utf-8"}
    if not body:
        return 400, {"ok": False, "error": "empty body"}
    dedup_path = core.inbox_dir() / ".webhook-dedup.json"
    key = "dedup:" + hashlib.sha256(f"{kind}:{name}:{body}".encode()).hexdigest()[:16]
    seen = core._read_json(dedup_path)
    now = time.time()
    if now - seen.get(key, 0) < 86400:
        return 200, {"ok": True, "duplicate": True}
    _log("item", payload, f"{kind}: {name}")
    try:
        item = core.create_item(kind, name, body, source=source)
    except SystemExit as exc:
        return 400, {"ok": False, "error": str(exc)}
    seen = {k: v for k, v in seen.items() if now - v < 86400}
    seen[key] = now  # only a filed item counts, so a failed post can be retried
    core._write_json(dedup_path, seen)
    return 200, {"ok": True, **item}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # no per-request stderr noise
        pass

    def _send(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._send(413, {"ok": False, "error": "body too large"})
            return
        payload = self.rfile.read(length)
        parsed = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        try:
            if parsed.path in ("/", "/github"):
                code, data = handle_github(payload, self.headers.get("X-Hub-Signature-256") or "",
                                           self.headers.get("X-GitHub-Event") or "")
            elif parsed.path == "/item":
                auth = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                code, data = handle_item(query, payload, auth)
            else:
                code, data = 404, {"ok": False, "error": f"no route {parsed.path}"}
        except Exception as exc:  # 500 so the sender (GitHub) retries
            code, data = 500, {"ok": False, "error": str(exc)[:300]}
        self._send(code, data)

    def do_GET(self):
        if urllib.parse.urlsplit(self.path).path == "/health":
            self._send(200, {"ok": True, "service": "desktop-inbox", "port": port()})
        else:
            self._send(404, {"ok": False, "error": "POST /item or /github"})


_server: ThreadingHTTPServer | None = None
_lock = threading.Lock()


def status() -> dict:
    if not settings.get("webhook_enabled", False):
        return {"ok": True, "enabled": False, "running": False}
    return ensure_server()


def ensure_server() -> dict:
    """Idempotent start; safe to call on every pane poll and after a restart."""
    global _server
    with _lock:
        if _server is not None:
            return {"ok": True, "enabled": True, "port": _server.server_address[1], "running": True}
        p = port()
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            srv.daemon_threads = True
        except OSError:
            # Port taken: fine if it is another copy of this listener (standalone vs backend).
            try:
                import urllib.request
                with urllib.request.urlopen(f"http://127.0.0.1:{p}/health", timeout=2) as res:
                    if json.loads(res.read().decode()).get("service") == "desktop-inbox":
                        return {"ok": True, "enabled": True, "port": p, "running": True, "external": True}
            except Exception:
                pass
            return {"ok": False, "enabled": True, "port": p, "running": False, "error": "port in use"}
        _server = srv
        threading.Thread(target=srv.serve_forever, daemon=True, name="desktop-inbox-webhook").start()
        return {"ok": True, "enabled": True, "port": p, "running": True}


def serve_forever() -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", port()), Handler)
    print(f"desktop-inbox webhook listening on 127.0.0.1:{port()}")
    srv.serve_forever()
