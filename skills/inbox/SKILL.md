---
name: inbox
description: Use when sending a cron job's result, a reminder or any follow-up to the user's Hermes Desktop Inbox.
---

# Send things to the Inbox

The Inbox is a Hermes Desktop pane (plugin `desktop-inbox`). Every item is its own Desktop
conversation that Hermes starts, with one clear ask for the user. The user works the pane like
an email inbox: open, answer, mark done, snooze. Use it for anything the user must read, answer,
decide or do later, so it does not get lost in a chat.

## File one item

Run this with the terminal tool:

```bash
hermes desktop-inbox new --type read --title "Morning brief" --source morning-brief \
  --body-file /path/to/message.md
```

- `--type`: `read`, `answer`, `decide`, `do`, or a type the user added in settings.
  Run `hermes desktop-inbox types` to see them. Pick by the ask: something to read, a question
  to answer, options to choose from, or a task.
- `--title`: short, 2 to 5 words. The type emoji and the date are added.
- `--source`: what filed it, usually the cron job name. The pane shows it.
- Body: `--body-file`, `--body "..."`, or stdin. Markdown. Put the content the user needs here;
  the item conversation shows it as Hermes's first message.
- `--reply-notes` / `--reply-notes-file`: rules for the Hermes that answers in the item later
  (for example "when the user says 'more like this', add the topic to ~/notes/taste.md").
- Filing takes one short Hermes turn (10 to 30 s) and prints JSON with the item id and title.

## Wire an existing cron job to the Inbox

When the user asks to send a cron job's result to the Inbox:

1. Read the job with `hermes cron list` (or the cronjob tool) and keep its schedule and prompt.
2. Set its delivery to `local`, so the run does not also post into a chat.
3. Add this block to the END of the job prompt (change the type, title and source):

```text
INBOX STEP (last step):
- If nothing in this run needs the user, file nothing and end with "Nothing for the inbox."
- Otherwise write the message for the user to /tmp/<job-name>.md and run:
  hermes desktop-inbox new --type read --title "<short title>" --source <job-name> --body-file /tmp/<job-name>.md
- If that command fails, end your answer with "INBOX FAILED: <the error>" and stop. Do not retry.
```

4. Run the job once (`hermes cron run <id>`) and confirm the item appears with
   `hermes desktop-inbox list`.

`INBOX FAILED:` is a contract: the pane reads the newest run output and shows the job under
"Needs you" when it finds that line, even if the run itself ended ok.

## Close, snooze, list

```bash
hermes desktop-inbox list [--all] [--json]
hermes desktop-inbox done ["<exact title>" | <session id>]   # no argument inside the item chat
hermes desktop-inbox snooze ["<title>"] tomorrow             # 30m, 2h, 1d, evening, next-week, fri, ISO
hermes desktop-inbox wake "<title>"
hermes desktop-inbox reopen "<title>"
```

Inside an item conversation, when the user says "done", "read" or "clear it", run
`hermes desktop-inbox done` and confirm in one short line.
