"""Inbox for Hermes Desktop.

The agent half registers the `hermes desktop-inbox` CLI (file, list, close and snooze items)
and a bundled skill that shows Hermes how to send a cron job's result to the Inbox. The pane
lives in desktop/plugin.js and its backend in dashboard/plugin_api.py.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent

USAGE = """examples:
  hermes desktop-inbox new --type read --title "Morning brief" --source morning-brief --body-file brief.md
  echo "Renew the passport" | hermes desktop-inbox new --type do --title "Passport"
  hermes desktop-inbox list --all
  hermes desktop-inbox snooze "<title or id>" tomorrow
  hermes desktop-inbox done          # inside an item's conversation
"""


def _core():
    from .desktop_inbox import core
    return core


def _setup(parser) -> None:
    parser.epilog = USAGE
    import argparse
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    sub = parser.add_subparsers(dest="inbox_cmd")

    n = sub.add_parser("new", help="file a new inbox item")
    n.add_argument("--type", required=True, help="read | answer | decide | do, or a type from settings")
    n.add_argument("--title", required=True, help="short name; the type emoji and the date are added")
    n.add_argument("--source", default="manual", help="what filed it, e.g. the cron job name")
    n.add_argument("--reply-notes", default="", help="how Hermes should handle replies in the item")
    n.add_argument("--reply-notes-file", help="file with reply notes (added to --reply-notes)")
    g = n.add_mutually_exclusive_group()
    g.add_argument("--body-file")
    g.add_argument("--body")

    for name, help_text in (("done", "mark an item done (archives its conversation)"),
                            ("reopen", "put a done item back in the inbox"),
                            ("wake", "bring a snoozed item back now")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("ref", nargs="?", help="session id or exact title (default: $HERMES_SESSION_ID)")

    s = sub.add_parser("snooze", help="park an item until a time")
    s.add_argument("ref", nargs="?")
    s.add_argument("when", nargs="?", default="tomorrow",
                   help="30m | 2h | 1d | tomorrow | evening | next-week | a weekday | ISO")
    s.add_argument("--note", default="")

    ls = sub.add_parser("list", help="list open items and failing jobs")
    ls.add_argument("--all", action="store_true", help="also items done in the last days")
    ls.add_argument("--json", action="store_true")

    sd = sub.add_parser("settle-done", help="close items whose kanban card finished")
    sd.add_argument("--json", action="store_true")

    sub.add_parser("types", help="show the item types (built-in plus settings)")
    w = sub.add_parser("webhook", help="run the webhook listener in the foreground")
    w.add_argument("--test", action="store_true", help="post a sample item to a running listener")
    parser.set_defaults(func=_run)


def _run(args) -> int:
    core = _core()
    cmd = getattr(args, "inbox_cmd", None)
    try:
        if cmd == "new":
            body = args.body if args.body is not None else (
                Path(args.body_file).read_text() if args.body_file else sys.stdin.read())
            notes = args.reply_notes
            if args.reply_notes_file:
                notes = (notes + "\n" + Path(args.reply_notes_file).expanduser().read_text()).strip()
            print(json.dumps(core.create_item(args.type, args.title, body, args.source, notes), ensure_ascii=False))
        elif cmd == "done":
            print(f"Done: {core.mark_done(args.ref)['title']}")
        elif cmd == "reopen":
            print(f"Reopened: {core.reopen(args.ref)['title']}")
        elif cmd == "wake":
            print(f"Woke: {core.wake(args.ref)['title']}")
        elif cmd == "snooze":
            res = core.snooze(args.ref, core.parse_when(args.when), args.note)
            print(f"Snoozed until {time.strftime('%a %H:%M', time.localtime(res['wake_at']))}: {res['title']}")
        elif cmd == "settle-done":
            res = core.settle_done_cards()
            if args.json:
                print(json.dumps(res, ensure_ascii=False, indent=2))
            else:
                print("\n".join(f"Settled ({s['board']}): {s['settled']}" for s in res) or "Nothing to settle.")
        elif cmd == "list":
            items = core.list_items(include_done=args.all)
            fails = core.failed_jobs()
            if args.json:
                print(json.dumps({"items": items, "failed_jobs": fails}, ensure_ascii=False, indent=2))
                return 0
            for f in fails:
                print(f"!  job {f['name']} {f['status']}: {f['error'][:120]}")
            if not items:
                print("Inbox zero.")
            for it in items:
                flag = "✓" if it["done"] else ("z" if it["wake_at"] else ("•" if it["unread"] else " "))
                print(f"{flag} {it['title']}   [{it['id']}]")
        elif cmd == "types":
            from .desktop_inbox import settings
            for name, spec in settings.types().items():
                print(f"{spec.get('emoji', '')} {name:<10} {spec.get('label', '')}: {spec.get('how', '')}")
        elif cmd == "webhook":
            from .desktop_inbox import webhook
            if args.test:
                import urllib.request
                req = urllib.request.Request(
                    f"http://127.0.0.1:{webhook.port()}/item?type=read&title=Webhook+test&source=webhook+test",
                    data=f"Test item filed at {time.strftime('%H:%M:%S')}.".encode(), method="POST")
                from .desktop_inbox import settings
                if token := settings.secret("webhook_token"):
                    req.add_header("Authorization", f"Bearer {token}")
                with urllib.request.urlopen(req, timeout=600) as res:
                    print(res.read().decode())
            else:
                webhook.serve_forever()
        else:
            print("usage: hermes desktop-inbox {new,list,done,reopen,snooze,wake,settle-done,types,webhook}")
            return 1
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return 1
        raise
    return 0


def register(ctx):
    ctx.register_cli_command(
        name="desktop-inbox",
        help="File, list and close Inbox items (Hermes-started Desktop conversations)",
        setup_fn=_setup,
        handler_fn=_run,
    )
    skills_dir = _HERE / "skills"
    for child in sorted(skills_dir.iterdir()) if skills_dir.is_dir() else []:
        if (child / "SKILL.md").exists():
            ctx.register_skill(child.name, child / "SKILL.md")
