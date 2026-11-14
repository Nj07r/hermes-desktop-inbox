# Module: `skills/inbox`

A Hermes skill that the plugin registers as `desktop-inbox:inbox`. It tells Hermes how to file items and how to send a cron job's result to the Inbox.

## Responsibilities

- Give the `hermes desktop-inbox new` flags and how to pick a type.
- Give the steps to wire a cron job: set delivery to `local`, add the `INBOX STEP` block to the end of the job prompt, run the job once and check the result with `list`.
- Define the `INBOX FAILED:` contract.
- List the close, snooze, wake, reopen and list commands.

## Key files

| File | Content |
|---|---|
| [`skills/inbox/SKILL.md`](../../../skills/inbox/SKILL.md) | The skill, with front matter `name: inbox` |

## Contract with the core

The cron prompt block ends with: `If that command fails, end your answer with "INBOX FAILED: <the error>" and stop.` [`core.failed_jobs()`](core.md) reads the newest `cron/output/<job_id>/*.md` file of each `ok` job. It searches the text after the last `## Response` heading, or the last 4000 characters. When it finds `INBOX FAILED:`, the job shows under Needs you with status `inbox_failed`.

## Dependencies

- **Registered by:** `register()` in [plugin entry](plugin-entry.md), which loads every `skills/*/SKILL.md`.
- **Read by:** Hermes, when a user asks to send something to the Inbox.
