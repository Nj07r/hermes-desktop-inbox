# Module: `desktop` (Inbox pane)

The Hermes Desktop half of the plugin: a React pane on the right, a status bar chip, the tab badge and notifications. It is one ES module that uses `@hermes/plugin-sdk` and React.

## Responsibilities

- Poll the [pane backend](dashboard.md) every 60 seconds and the Desktop session list every 15 seconds.
- Show four sections: Needs you, Inbox, Snoozed (folded) and Done (folded).
- Apply every action at once in the query cache, send it to the backend and show a 5-second Undo toast.
- Handle the keyboard: `j`/`k` or arrows move, `Enter` or `o` opens, `e` closes, `s` snoozes, `z` or Cmd/Ctrl+Z undoes, `Escape` returns focus to the pane.
- Show one attention count on the chip and the pane tab.
- Send an in-app toast when Desktop has focus, else an OS notification, for new items, new attention cards, ended snoozes and auto-settled items.

## Key files

| File | Content |
|---|---|
| [`desktop/plugin.js`](../../../desktop/plugin.js) | The whole UI, 991 lines, including the CSS in `STYLES` |

## Public API

The default export is `{ id: 'desktop-inbox', name: 'Inbox', register(ctx) }`. `register` does three things:

| Registration | Values |
|---|---|
| `<style data-plugin="desktop-inbox">` | `STYLES`, removed on `ctx.onDispose` |
| Pane | `id: 'pane'`, `area: 'panes'`, `placement: 'right'`, `width: '300px'`, `hideOnly: true`, tab title `InboxTabTitle` |
| Status bar chip | `id: 'chip'`, `area: 'statusBar.right'`, `order: 120`, renders `InboxChip` |

## Internal structure

### Data hooks

| Hook | Source | Interval |
|---|---|---|
| `useItems(all)` | `ctx.rest('/items?all=0\|1')` | 60 s (`POLL_MS`), stale after 10 s |
| `useWaiting()` | `host.request('session.active_list')`, filtered to `status === 'waiting'` | 15 s, stale after 5 s |
| `useAttention()` | Both hooks | Open items + waiting chats + cards + failed jobs |

### Components

| Component | Shows |
|---|---|
| `InboxPane` | The sections, keyboard order, selection and focus rules |
| `Row` | One row: icon, title, ask line, time, hover actions |
| `ItemRow` | An item. Actions: snooze menu and done, or wake, or reopen |
| `WaitingRow` | A chat with a pending clarify or approval. Opens that chat |
| `CardRow` | A blocked or triage card. Opens the subscribed chat or `/kanban`. Action: archive |
| `FailedRow` | A failed job. A click posts `/file-failure` and opens the returned item |
| `SnoozeMenu` | Later today (+3 h), This evening (20:00, before 18:00 only), Tomorrow morning (09:00), Next week (Monday 09:00). Keys `1` to `4` pick |
| `UndoToast` | The current undo message and a bar that runs for `UNDO_MS` |
| `InboxZero` | The empty state. The check mark animates only when the user empties the inbox |
| `InboxChip` | The status bar button. It owns the notification logic |
| `InboxTabTitle` | `Inbox <count>`, capped at `99+` |

### Actions

`makeActions(leave)` returns `done`, `snooze`, `wake`, `reopen` and `archiveCard`. Each action plays a 200 ms leave animation, patches the cache, posts to the backend and registers an undo that posts the opposite route. `archiveCard` is different: a card has no un-archive, so the post waits in `pendingCards` until the undo window ends.

## Dependencies

- **Used by:** Hermes Desktop.
- **Uses:** [dashboard](dashboard.md) routes, `@hermes/plugin-sdk` (`host`, `queryClient`, `useQuery`, `Codicon`, `DropdownMenu`, `ScrollArea`, `Tip`, `haptic`), `ctx.storage` and `ctx.os.notify`.

## Notable patterns

- The snooze menu computes the wake time in the browser and sends epoch seconds, so the time on screen is the stored time. The hours 9 and 20 are fixed in `snoozeOptions()`. They do not read the `morning_hour` and `evening_hour` settings, which only apply to `parse_when` on the CLI.
- `diffSeen()` stores seen keys in `ctx.storage`, up to 300 per list. The first run after install only records keys, so a new install does not send a burst of notifications.
- While the pane has focus, it sets `data-overlay-surface` on its root. Desktop then does not send typed keys to the composer.
- `cleanTitle()` removes the leading emoji and the ` · Mon D #n` suffix from a title.
