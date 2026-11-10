/**
 * Inbox for Hermes Desktop.
 * Items are Hermes-started Desktop conversations in the inbox folder (a Desktop project), filed by
 * `hermes desktop-inbox new` from cron jobs, scripts or the webhook. Data and actions come from this
 * package's backend (/api/plugins/desktop-inbox, dashboard/plugin_api.py). Done = archived by the user.
 *
 * The pane is a triage queue: every row says what it needs (the item's ask line), actions are
 * optimistic with an Undo toast, and the keyboard works it top to bottom
 * (j/k move, Enter open, e done, s snooze, z undo).
 */
import {
  cn,
  Codicon,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  haptic,
  host,
  queryClient,
  ScrollArea,
  Tip,
  useQuery
} from '@hermes/plugin-sdk'
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'desktop-inbox'
const PANE_ID = `${ID}:pane`
const POLL_MS = 60_000
const KEY = [ID, 'items']
const UNDO_MS = 5_000
const LEAVE_MS = 200

// Built-in types; the backend sends the merged list (built-in plus the `types` setting) as ui.types.
const BUILTIN_TYPES = {
  read: { label: 'Read', icon: 'book' },
  answer: { label: 'Answer', icon: 'comment' },
  decide: { label: 'Decide', icon: 'law' },
  do: { label: 'Do', icon: 'checklist' }
}
let TYPES = BUILTIN_TYPES

let pluginCtx = null

const rest = (path, opts) => pluginCtx.rest(path, opts)
const fetchItems = all => rest(`/items?all=${all ? 1 : 0}`)
const refresh = () => queryClient.invalidateQueries({ queryKey: [ID] })
const openChat = id => id && host.openSession(id, { intent: 'tab' })
const tap = () => haptic('tap')

function useItems(all = false) {
  return useQuery({ queryKey: [...KEY, all], queryFn: () => fetchItems(all), refetchInterval: POLL_MS, staleTime: 10_000 })
}

// Live chats parked on a clarify / approval (the amber sidebar dot). Answering happens in THAT chat:
// a pending prompt can only be resolved there, so the pane links to it instead of copying it.
function useWaiting() {
  return useQuery({
    queryKey: [ID, 'waiting'],
    queryFn: async () => {
      const res = await host.request('session.active_list', {})
      return (res?.sessions || []).filter(s => s.status === 'waiting')
    },
    refetchInterval: 15_000,
    staleTime: 5_000
  })
}

// ── formatting ───────────────────────────────────────────────────────────

function ago(ts) {
  if (!ts) return ''
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 60) return 'now'
  if (s < 3600) return `${Math.round(s / 60)}m`
  if (s < 86400) return `${Math.round(s / 3600)}h`
  if (s < 6 * 86400) return `${Math.round(s / 86400)}d`
  return new Date(ts * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' })
}

const hhmm = d => d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })

// "15:40" today, "Wed 09:00" this week, "Nov 18" later.
function at(tsOrDate) {
  const d = tsOrDate instanceof Date ? tsOrDate : new Date(tsOrDate * 1000)
  const now = new Date()
  if (d.toDateString() === now.toDateString()) return hhmm(d)
  if (d - now < 6 * 86400_000) return `${d.toLocaleDateString([], { weekday: 'short' })} ${hhmm(d)}`
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

// "📖 Morning brief · Nov 10 #2" -> "Morning brief". The type has its own icon and the row its own time.
function cleanTitle(title) {
  let t = String(title || '').trim()
  const sp = t.indexOf(' ')
  if (sp > 0 && !/[\p{L}\p{N}]/u.test(t.slice(0, sp))) t = t.slice(sp + 1)
  return t.replace(/ · [A-Z][a-z]{2} \d{1,2}( #\d+)?$/, '') || 'Untitled'
}

// Mirrors inbox_core.parse_when, but the pane sends the exact timestamp so the shown time is the stored one.
function snoozeOptions(now = new Date()) {
  const day = (plus, hour) => {
    const d = new Date(now)
    d.setDate(d.getDate() + plus)
    d.setHours(hour, 0, 0, 0)
    return d
  }
  const opts = [{ label: 'Later today', at: new Date(+now + 3 * 3600_000) }]
  if (now.getHours() < 18) opts.push({ label: 'This evening', at: day(0, 20) })
  opts.push({ label: 'Tomorrow morning', at: day(1, 9) })
  const monday = day(1, 9)
  monday.setDate(monday.getDate() + ((7 - ((monday.getDay() + 6) % 7)) % 7 || 7))
  opts.push({ label: 'Next week', at: monday })
  return opts
}

// ── server calls + optimistic cache ──────────────────────────────────────

async function post(path, body) {
  let res
  try {
    res = await rest(path, { method: 'POST', body })
  } catch (e) {
    res = { ok: false, error: e?.message }
  }
  if (!res?.ok) host.notify({ kind: 'error', message: `Inbox: ${res?.error || 'request failed'}` })
  refresh()
  return res
}

// Optimistic writes cancel any poll in flight first, or its older answer would undo them.
function patchItem(id, patch) {
  queryClient.cancelQueries({ queryKey: KEY })
  queryClient.setQueriesData({ queryKey: KEY }, d =>
    d?.items ? { ...d, items: d.items.map(i => (i.id === id ? { ...i, ...patch } : i)) } : d
  )
}

// Undo: a refetch may already have dropped the item from the cache, so put the snapshot back.
function restoreItem(item, patch) {
  queryClient.cancelQueries({ queryKey: KEY })
  queryClient.setQueriesData({ queryKey: KEY }, d => {
    if (!d?.items) return d
    const has = d.items.some(i => i.id === item.id)
    return { ...d, items: has ? d.items.map(i => (i.id === item.id ? { ...item, ...patch } : i)) : [...d.items, { ...item, ...patch }] }
  })
}

// Cards have no un-archive, so archiving waits out the undo window before it reaches the server.
// Module scope: a pending archive survives the pane re-rendering or closing.
const pendingCards = new Map()
const cardKey = c => `${c.board}:${c.id}`

// ── undo toast (one at a time) ───────────────────────────────────────────

let toast = null
const toastSubs = new Set()
const setToast = t => {
  toast = t
  toastSubs.forEach(f => f())
}
const useToast = () =>
  useSyncExternalStore(
    f => (toastSubs.add(f), () => toastSubs.delete(f)),
    () => toast
  )

function showUndo(message, undo, key) {
  const t = { id: Math.random(), message, undo, key }
  clearTimeout(showUndo.timer)
  showUndo.timer = setTimeout(() => toast?.id === t.id && setToast(null), UNDO_MS)
  setToast(t)
}

// Returns the restored row's key (or null) so the pane can select it again.
function runUndo() {
  const t = toast
  if (!t?.undo) return null
  setToast(null)
  tap()
  t.undo()
  return t.key ?? ''
}

// ── actions (shared by clicks and keys) ──────────────────────────────────

function makeActions(leave) {
  return {
    done(item) {
      tap()
      leave(item.id, () => patchItem(item.id, { done: true, unread: false }))
      post('/done', { id: item.id })
      showUndo(`Done · ${cleanTitle(item.title)}`, () => {
        restoreItem(item, { done: false })
        post('/reopen', { id: item.id })
      }, item.id)
    },
    snooze(item, opt) {
      tap()
      const wake = Math.round(+opt.at / 1000)
      leave(item.id, () => patchItem(item.id, { wake_at: wake }))
      post('/snooze', { id: item.id, when: String(wake) })
      showUndo(`Snoozed until ${at(opt.at)}`, () => {
        restoreItem(item, { wake_at: null })
        post('/wake', { id: item.id })
      }, item.id)
    },
    wake(item) {
      tap()
      const was = item.wake_at
      leave(item.id, () => patchItem(item.id, { wake_at: null }))
      post('/wake', { id: item.id })
      showUndo(`Back in the inbox · ${cleanTitle(item.title)}`, () => {
        restoreItem(item, { wake_at: was })
        post('/snooze', { id: item.id, when: String(was) })
      })
    },
    reopen(item) {
      tap()
      leave(item.id, () => patchItem(item.id, { done: false }))
      post('/reopen', { id: item.id })
      showUndo(`Back in the inbox · ${cleanTitle(item.title)}`, () => {
        restoreItem(item, { done: true })
        post('/done', { id: item.id })
      })
    },
    archiveCard(card) {
      tap()
      const key = cardKey(card)
      leave(key, () => {
        pendingCards.set(key, setTimeout(() => {
          pendingCards.delete(key)
          post('/archive-card', { id: card.id, board: card.board })
        }, UNDO_MS - LEAVE_MS))
        refresh()
      })
      showUndo(`Card archived · ${card.title}`, () => {
        clearTimeout(pendingCards.get(key))
        pendingCards.delete(key)
        refresh()
      }, key)
    }
  }
}

// ── pieces ───────────────────────────────────────────────────────────────

function IconButton({ label, icon, onClick, tone, ...rest }) {
  return jsx(Tip, {
    label,
    children: jsx('button', {
      type: 'button',
      'aria-label': label,
      className: 'ibx-btn',
      'data-tone': tone,
      tabIndex: -1,
      onClick: e => {
        e.stopPropagation()
        onClick?.(e)
      },
      ...rest,
      children: jsx(Codicon, { name: icon })
    })
  })
}

function SnoozeMenu({ item, open, onOpenChange, onPick, onClosed }) {
  const opts = open ? snoozeOptions() : []
  return jsxs(DropdownMenu, {
    open,
    onOpenChange,
    modal: false,
    children: [
      jsx(DropdownMenuTrigger, {
        asChild: true,
        children: jsx('button', {
          type: 'button',
          'aria-label': 'Snooze',
          title: 'Snooze (s)',
          className: 'ibx-btn',
          'data-open': open || undefined,
          tabIndex: -1,
          onClick: e => e.stopPropagation(),
          children: jsx(Codicon, { name: 'clock' })
        })
      }),
      jsx(DropdownMenuContent, {
        align: 'end',
        sideOffset: 4,
        className: 'ibx-menu',
        onClick: e => e.stopPropagation(),
        // Hand focus back to the row (not the trigger), so the keyboard flow continues.
        onCloseAutoFocus: e => {
          e.preventDefault()
          onClosed?.()
        },
        onKeyDown: e => {
          const o = opts[Number(e.key) - 1]
          if (o) {
            e.preventDefault()
            onOpenChange(false)
            onPick(item, o)
          }
        },
        children: [
          jsx('div', { className: 'ibx-menu-label', children: 'Snooze until' }, 'label'),
          ...opts.map((o, i) =>
            jsxs(
              DropdownMenuItem,
              {
                className: 'ibx-menu-item',
                onSelect: () => onPick(item, o),
                children: [
                  jsx('span', { className: 'ibx-menu-key', children: i + 1 }),
                  jsx('span', { className: 'flex-1', children: o.label }),
                  jsx('span', { className: 'ibx-mono ibx-dim', children: at(o.at) })
                ]
              },
              o.label
            )
          )
        ]
      })
    ]
  })
}

function Row({ rowKey, icon, iconTone, iconTip, title, sub, time, unread, muted, busy, actions, onOpen, nav, i }) {
  const selected = nav.sel === rowKey
  return jsx('div', {
    className: 'ibx-collapse',
    'data-leaving': nav.leaving.has(rowKey) || undefined,
    style: { '--i': Math.min(i ?? 0, 12) },
    children: jsx('div', {
      children: jsxs('div', {
        role: 'button',
        tabIndex: selected ? 0 : -1,
        'data-row': rowKey,
        'data-selected': selected || undefined,
        'data-unread': unread || undefined,
        'data-muted': muted || undefined,
        'aria-busy': busy || undefined,
        className: cn('ibx-row', nav.entering && 'ibx-enter'),
        onClick: () => {
          nav.select(rowKey)
          onOpen()
        },
        onFocus: () => nav.select(rowKey, false),
        children: [
          jsx(Tip, {
            label: iconTip,
            children: jsx('span', { className: 'ibx-icon', 'data-tone': iconTone, children: jsx(Codicon, { name: busy ? 'loading' : icon, spinning: busy }) })
          }),
          jsxs('div', {
            className: 'ibx-main',
            children: [
              jsxs('div', {
                className: 'ibx-line',
                children: [
                  jsx('span', { className: 'ibx-title', children: title }),
                  jsxs('span', {
                    className: 'ibx-meta',
                    children: [unread && jsx('span', { className: 'ibx-dot' }), jsx('span', { className: 'ibx-mono', children: time })]
                  }),
                  actions && jsx('span', { className: 'ibx-actions', children: actions })
                ]
              }),
              sub && jsx('div', { className: 'ibx-sub', children: sub })
            ]
          })
        ]
      })
    })
  })
}

function ItemRow({ item, nav, act, i }) {
  const t = TYPES[item.type] || TYPES.read
  const snoozed = !!item.wake_at && !item.done
  const actions = item.done
    ? jsx(IconButton, { label: 'Put back in the inbox', icon: 'discard', onClick: () => act.reopen(item) })
    : snoozed
      ? jsx(IconButton, { label: 'Wake now', icon: 'bell', onClick: () => act.wake(item) })
      : [
          jsx(SnoozeMenu, { item, open: nav.snoozeFor === item.id, onOpenChange: o => nav.setSnoozeFor(o ? item.id : null), onPick: act.snooze, onClosed: () => nav.select(item.id) }, 's'),
          jsx(IconButton, { label: item.type === 'read' ? 'Mark read (e)' : 'Mark done (e)', icon: 'check', tone: 'ok', onClick: () => act.done(item) }, 'd')
        ]
  return jsx(Row, {
    rowKey: item.id,
    i,
    icon: t.icon,
    iconTip: `${t.label}${item.source ? ` · from ${item.source}` : ''}`,
    title: cleanTitle(item.title),
    sub: item.ask || (item.unread ? `${t.label} item` : 'You replied'),
    time: snoozed ? `back ${at(item.wake_at)}` : ago(item.started_at),
    unread: item.unread && !snoozed,
    muted: item.done || snoozed,
    actions,
    onOpen: () => openChat(item.id),
    nav
  })
}

function WaitingRow({ s, nav, i }) {
  return jsx(Row, {
    rowKey: `w:${s.id}`,
    i,
    icon: 'comment-discussion',
    iconTone: 'warn',
    iconTip: 'A chat is waiting for your answer',
    title: s.title || 'Untitled chat',
    sub: s.preview || 'Waiting for your answer',
    time: ago(s.last_active),
    unread: true,
    onOpen: () => openChat(s.session_key || s.id),
    nav
  })
}

function CardRow({ card, nav, act, i }) {
  const kind = card.block_kind ? card.block_kind.replace('_', ' ') : 'blocked'
  const what = card.status === 'triage' ? 'Triage' : kind[0].toUpperCase() + kind.slice(1)
  return jsx(Row, {
    rowKey: cardKey(card),
    i,
    icon: 'project',
    iconTone: 'warn',
    iconTip: `Kanban card · ${card.board} · ${card.id}`,
    title: card.title,
    sub: `${what}${card.reason ? ` · ${card.reason}` : ''}`,
    time: ago(card.since),
    actions: jsx(IconButton, { label: 'Archive card (e)', icon: 'archive', onClick: () => act.archiveCard(card) }),
    onOpen: () => (card.chat_id ? openChat(card.chat_id) : host.navigate('/kanban')),
    nav
  })
}

// Click: file (or reuse) a "✅ Fix <job>" Do item and open it. Filing runs one short Hermes
// turn, so it takes a few seconds; the row shows that it is working.
function FailedRow({ job, nav, i }) {
  const [busy, setBusy] = useState(false)
  const open = async () => {
    if (busy) return
    tap()
    setBusy(true)
    const res = await post('/file-failure', { name: job.name }).finally(() => setBusy(false))
    if (res?.ok && res.id) openChat(res.id)
  }
  return jsx(Row, {
    rowKey: `f:${job.id}`,
    i,
    icon: 'warning',
    iconTone: 'err',
    iconTip: 'A cron job failed. Open to get it fixed',
    title: busy ? `Opening a fix for ${job.name}…` : `${job.name} failed`,
    sub: job.error || job.status,
    time: ago(job.at),
    busy,
    onOpen: open,
    nav
  })
}

function Section({ title, count, collapsible, open, onToggle, tools, children }) {
  return jsxs('section', {
    className: 'ibx-section',
    children: [
      jsxs('div', {
        className: 'ibx-section-bar',
        children: [
      collapsible
        ? jsxs('button', {
            type: 'button',
            className: 'ibx-section-head ibx-section-toggle',
            'aria-expanded': open,
            onClick: () => {
              tap()
              onToggle()
            },
            children: [
              jsx(Codicon, { name: 'chevron-right', className: 'ibx-caret' }),
              jsx('span', { children: title }),
              count != null && jsx('span', { className: 'ibx-mono ibx-count', children: count })
            ]
          })
        : jsxs('div', {
            className: 'ibx-section-head',
            children: [jsx('span', { children: title }), count != null && jsx('span', { className: 'ibx-mono ibx-count', children: count })]
          }),
          tools
        ]
      }),
      (!collapsible || open) && children
    ]
  })
}

function InboxZero({ celebrate, ui }) {
  return jsxs('div', {
    className: 'ibx-zero',
    'data-celebrate': celebrate || undefined,
    children: [
      jsxs('svg', {
        viewBox: '0 0 48 48',
        className: 'ibx-zero-mark',
        'aria-hidden': true,
        children: [
          jsx('circle', { cx: 24, cy: 24, r: 21, className: 'ibx-zero-ring' }),
          jsx('path', { d: 'M15 24.5l6.2 6.2L33.5 18', className: 'ibx-zero-check' })
        ]
      }),
      jsx('div', { className: 'ibx-zero-title', children: ui?.empty_title || 'Inbox zero' }),
      ui?.empty_message !== '' && jsx('div', { className: 'ibx-zero-line', children: ui?.empty_message || 'Nothing needs you.' })
    ]
  })
}

function UndoToast({ onUndo }) {
  const t = useToast()
  if (!t) return null
  return jsxs('div', {
    className: 'ibx-toast',
    role: 'status',
    children: [
      jsx('span', { className: 'ibx-toast-msg', children: t.message }),
      t.undo &&
        jsxs('button', {
          type: 'button',
          className: 'ibx-toast-undo',
          onClick: onUndo,
          children: ['Undo', jsx('span', { className: 'ibx-mono ibx-dim', children: 'z' })]
        }),
      jsx('span', { className: 'ibx-toast-bar', style: { animationDuration: `${UNDO_MS}ms` } }, t.id)
    ]
  })
}

// ── the pane ─────────────────────────────────────────────────────────────

function InboxPane() {
  const [showDone, setShowDone] = useState(false)
  const [showSnoozed, setShowSnoozed] = useState(false)
  const [sel, setSel] = useState(null)
  const [snoozeFor, setSnoozeFor] = useState(null)
  const [leaving, setLeaving] = useState(() => new Set())
  const [entering, setEntering] = useState(true)
  const [celebrate, setCelebrate] = useState(false)
  const rootRef = useRef(null)

  const q = useItems(showDone)
  if (q.data?.ui?.types) TYPES = { ...BUILTIN_TYPES, ...q.data.ui.types }
  const waitingQ = useWaiting()
  const all = q.data?.items || []
  const byNew = (a, b) => (b.unread - a.unread) || (b.started_at || 0) - (a.started_at || 0)
  const open = all.filter(i => !i.done && !i.wake_at).sort(byNew)
  const snoozed = all.filter(i => i.wake_at && !i.done).sort((a, b) => a.wake_at - b.wake_at)
  const done = all.filter(i => i.done).sort((a, b) => (b.started_at || 0) - (a.started_at || 0))
  const failed = q.data?.failed_jobs || []
  const cards = (q.data?.cards || []).filter(c => !pendingCards.has(cardKey(c)))
  const waiting = waitingQ.data || []
  const hook = q.data?.webhook
  const attention = waiting.length + cards.length + failed.length
  const zero = q.isSuccess && q.data?.ok !== false && !open.length && !attention

  // Stagger only the first paint; later rows still fade in on their own mount.
  useEffect(() => {
    if (!q.isSuccess) return
    const t = setTimeout(() => setEntering(false), 700)
    return () => clearTimeout(t)
  }, [q.isSuccess])

  // The reward plays only when the user empties the inbox, not when they open an empty one.
  const prevOpen = useRef(null)
  useEffect(() => {
    if (!q.isSuccess) return
    const n = open.length + attention
    if (prevOpen.current > 0 && n === 0) setCelebrate(true)
    if (n > 0) setCelebrate(false)
    prevOpen.current = n
  }, [q.isSuccess, open.length, attention])

  const actedAt = useRef(0)
  const leave = useCallback((key, apply) => {
    actedAt.current = Date.now()
    setLeaving(s => new Set(s).add(key))
    setTimeout(() => {
      apply()
      setLeaving(s => {
        const n = new Set(s)
        n.delete(key)
        return n
      })
    }, LEAVE_MS)
  }, [])
  const act = useRef(null)
  act.current ??= makeActions(leave)

  // Flat order for the keyboard: what blocks you, then the inbox, then folded sections if open.
  const order = [
    ...waiting.map(s => ({ key: `w:${s.id}`, open: () => openChat(s.session_key || s.id) })),
    ...cards.map(c => ({ key: cardKey(c), open: () => (c.chat_id ? openChat(c.chat_id) : host.navigate('/kanban')), done: () => act.current.archiveCard(c) })),
    ...failed.map(j => ({ key: `f:${j.id}`, click: true })),
    ...open.map(i => ({ key: i.id, open: () => openChat(i.id), done: () => act.current.done(i), snooze: () => setSnoozeFor(i.id) })),
    ...(showSnoozed ? snoozed.map(i => ({ key: i.id, open: () => openChat(i.id), done: () => act.current.wake(i) })) : []),
    ...(showDone ? done.map(i => ({ key: i.id, open: () => openChat(i.id), done: () => act.current.reopen(i) })) : [])
  ].filter(r => !leaving.has(r.key))

  const focusRow = key => {
    const el = rootRef.current?.querySelector(`[data-row="${CSS.escape(key)}"]`)
    el?.focus({ preventScroll: true })
    el?.scrollIntoView({ block: 'nearest' })
  }

  // Keep a valid selection when rows leave: move to the row that took its place.
  const selIndex = order.findIndex(r => r.key === sel)
  const lastIndex = useRef(0)
  if (selIndex >= 0) lastIndex.current = selIndex
  // A removed row takes focus with it (document.activeElement falls back to body). Follow with
  // the next row only then; if the user is typing elsewhere, never steal focus.
  const hadFocus = useRef(false)
  useEffect(() => {
    if (sel && selIndex < 0 && order.length && !leaving.has(sel)) {
      const next = order[Math.min(lastIndex.current, order.length - 1)].key
      setSel(next)
      const lost = !document.activeElement || document.activeElement === document.body
      // Only right after a pane action: a row vanishing on a background poll never grabs focus.
      if (hadFocus.current && lost && Date.now() - actedAt.current < 1500) requestAnimationFrame(() => focusRow(next))
    }
  })

  const nav = {
    sel,
    select: (key, focus = true) => {
      setSel(key)
      if (focus) requestAnimationFrame(() => focusRow(key))
    },
    leaving,
    entering,
    snoozeFor,
    setSnoozeFor
  }

  const undo = () => {
    const key = runUndo()
    if (key) requestAnimationFrame(() => nav.select(key))
    return key
  }

  const onKeyDown = e => {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'z' && undo() != null) e.preventDefault()
      return
    }
    if (e.target.closest?.('input, textarea, [contenteditable=true]')) return
    const i = order.findIndex(r => r.key === sel)
    const row = order[i]
    const move = d => {
      if (!order.length) return
      const n = order[i < 0 ? (d > 0 ? 0 : order.length - 1) : (i + d + order.length) % order.length]
      nav.select(n.key)
    }
    const k = e.key
    if (k === 'j' || k === 'ArrowDown') move(1)
    else if (k === 'k' || k === 'ArrowUp') move(-1)
    else if ((k === 'Enter' || k === 'o') && row) {
      if (row.click) rootRef.current?.querySelector(`[data-row="${CSS.escape(row.key)}"]`)?.click()
      else row.open?.()
    } else if (k === 'e' && row?.done) row.done()
    else if (k === 's' && row?.snooze) row.snooze()
    else if (k === 'z') undo()
    else if (k === 'Escape') rootRef.current?.focus()
    else return
    e.preventDefault()
  }

  // The tab already says "Inbox 8", so there is no header: refresh rides on the first section bar.
  const first = attention > 0 ? 'needs' : open.length > 0 ? 'inbox' : snoozed.length > 0 ? 'snoozed' : 'done'
  const tools = name =>
    name === first &&
    jsx('span', {
      className: 'ibx-tools',
      children: jsx(IconButton, {
        label: 'Refresh',
        icon: 'refresh',
        'data-spin': q.isFetching || undefined,
        onClick: () => {
          tap()
          refresh()
        }
      })
    })
  let n = 0

  return jsxs('div', {
    ref: rootRef,
    className: 'ibx-pane',
    tabIndex: -1,
    onKeyDown,
    onFocus: () => (hadFocus.current = true),
    onBlur: e => {
      if (e.relatedTarget && !rootRef.current?.contains(e.relatedTarget)) hadFocus.current = false
    },
    children: [
      jsx(ScrollArea, {
        className: 'min-h-0 flex-1',
        children: jsxs('div', {
          className: 'ibx-list',
          children: [
            q.isPending && jsx('div', { className: 'ibx-skeleton', children: [0, 1, 2].map(k => jsx('div', {}, k)) }),
            q.data?.ok === false && jsx('div', { className: 'ibx-note', children: `Backend error: ${q.data.error}` }),
            q.isError && jsx('div', { className: 'ibx-note', children: 'Inbox backend unreachable. Is desktop-inbox enabled (hermes plugins enable desktop-inbox)?' }),
            hook && hook.enabled && hook.ok === false &&
              jsx(Tip, { label: hook.error || 'The webhook listener could not start', children: jsx('div', { className: 'ibx-note', children: `Webhook listener down (port ${hook.port ?? '?'})` }) }),
            attention > 0 &&
              jsxs(Section, {
                title: 'Needs you',
                count: attention,
                tools: tools('needs'),
                children: [
                  ...waiting.map(s => jsx(WaitingRow, { s, nav, i: n++ }, `w:${s.id}`)),
                  ...cards.map(c => jsx(CardRow, { card: c, nav, act: act.current, i: n++ }, cardKey(c))),
                  ...failed.map(j => jsx(FailedRow, { job: j, nav, i: n++ }, `f:${j.id}`))
                ]
              }),
            open.length > 0 &&
              jsx(Section, { title: 'Inbox', count: open.length, tools: tools('inbox'), children: open.map(i => jsx(ItemRow, { item: i, nav, act: act.current, i: n++ }, i.id)) }),
            zero && jsx(InboxZero, { celebrate, ui: q.data?.ui }),
            snoozed.length > 0 &&
              jsx(Section, {
                title: 'Snoozed',
                count: snoozed.length,
                tools: tools('snoozed'),
                collapsible: true,
                open: showSnoozed,
                onToggle: () => setShowSnoozed(v => !v),
                children: snoozed.map(i => jsx(ItemRow, { item: i, nav, act: act.current }, i.id))
              }),
            q.isSuccess &&
              jsx(Section, {
                title: 'Done',
                count: showDone ? done.length : null,
                tools: tools('done'),
                collapsible: true,
                open: showDone,
                onToggle: () => setShowDone(v => !v),
                children: showDone && done.length === 0 && !q.isFetching
                  ? jsx('div', { className: 'ibx-note', children: `Nothing closed in the last ${q.data?.ui?.done_days ?? 7} days.` })
                  : done.map(i => jsx(ItemRow, { item: i, nav, act: act.current }, i.id))
              })
          ]
        })
      }),
      jsxs('footer', {
        className: 'ibx-keys ibx-mono',
        children: ['j k move', ' · ', '↵ open', ' · ', 'e done', ' · ', 's snooze', ' · ', 'z undo']
      }),
      jsx(UndoToast, { onUndo: undo })
    ]
  })
}

// ── status bar + tab badge + notifications ───────────────────────────────

// Everything that needs the user, one number (chip + pane tab badge share it).
function useAttention() {
  const q = useItems(false)
  const waiting = useWaiting().data || []
  const open = (q.data?.items || []).filter(i => !i.done && !i.wake_at)
  const cards = (q.data?.cards || []).filter(c => !pendingCards.has(cardKey(c)))
  return { open, waiting, cards, failed: q.data?.failed_jobs || [], q }
}

const total = a => a.open.length + a.failed.length + a.cards.length + a.waiting.length

// Pane tab label: "Inbox 3". The count is plain text in the tab's own font, tinted with the
// accent, so it reads like part of the label instead of a sticker (a filled pill looked off).
function InboxTabTitle() {
  const count = total(useAttention())
  return jsxs('span', {
    className: 'inline-flex items-baseline gap-1',
    children: ['Inbox', count > 0 && jsx('span', { className: 'tabular-nums text-(--ui-accent)', children: count > 99 ? '99+' : count })]
  })
}

// One announcement per batch: an in-app toast when Hermes has focus, an OS notification when not.
function announce(list, { title, one, many, open }) {
  if (!list.length) return
  const single = list.length === 1
  const body = single ? one(list[0]) : many(list.length)
  const go = () => (single ? open(list[0]) : host.revealPane(PANE_ID))
  if (typeof document !== 'undefined' && document.hasFocus()) {
    host.notify({ kind: 'info', icon: 'inbox', title, message: body, action: { label: single ? 'Open' : 'Show', onClick: go } })
  } else {
    pluginCtx.os.notify({ title, body, onActivate: go })
  }
}

// Remember what was already seen; announce only what is new since the last poll. The first run
// after install only primes the list, so a fresh install does not fire a burst.
function diffSeen(name, keys) {
  const primed = pluginCtx.storage.get(`${name}Primed`, false)
  const seen = new Set(pluginCtx.storage.get(name, []))
  pluginCtx.storage.set(name, [...new Set([...keys, ...seen])].slice(0, 300))
  pluginCtx.storage.set(`${name}Primed`, true)
  return primed ? keys.filter(k => !seen.has(k)) : []
}

// Statusbar chip: always mounted, so it also owns notifications.
function InboxChip() {
  const a = useAttention()
  const { q, open, cards, waiting, failed } = a
  const count = total(a)

  useEffect(() => {
    if (!q.data?.ok || !pluginCtx) return
    // Snoozed items count as seen too, so waking one is not announced as new.
    const fresh = new Set(diffSeen('seenItems', (q.data.items || []).map(i => i.id)))
    announce(open.filter(i => fresh.has(i.id)), {
      title: 'Hermes Inbox',
      one: i => cleanTitle(i.title) + (i.ask ? `: ${i.ask}` : ''),
      many: n => `${n} new items`,
      open: i => openChat(i.id)
    })

    const ck = c => `${cardKey(c)}:${c.status}:${c.since}`
    const freshCards = new Set(diffSeen('seenCardKeys', cards.map(ck)))
    announce(cards.filter(c => freshCards.has(ck(c))), {
      title: 'A card needs you',
      one: c => `${c.status === 'triage' ? 'Triage' : 'Blocked'}: ${c.title}`,
      many: n => `${n} cards need you`,
      open: c => (c.chat_id ? openChat(c.chat_id) : host.navigate('/kanban'))
    })

    // Snooze wake-up: an item that was snoozed and now is open again gets its own notice,
    // otherwise snoozing past a closed pane would swallow the return.
    // Only a snooze whose time came counts; "Wake now" or Undo in the pane stays quiet.
    const wasSnoozed = pluginCtx.storage.get('snoozedAt', {})
    const nowSnoozed = Object.fromEntries((q.data.items || []).filter(i => i.wake_at).map(i => [i.id, i.wake_at]))
    const woke = open.filter(i => wasSnoozed[i.id] && wasSnoozed[i.id] <= Date.now() / 1000 + 60)
    pluginCtx.storage.set('snoozedAt', nowSnoozed)
    announce(woke, { title: 'Snooze over', one: i => cleanTitle(i.title), many: n => `${n} snoozed items are back`, open: i => openChat(i.id) })

    // Auto-settled items: finished cards closed their threads.
    const freshSettled = diffSeen('settledTasks', (q.data.settled || []).map(s => s.task))
    if (freshSettled.length) host.notify({ kind: 'info', icon: 'inbox', message: `Inbox: ${freshSettled.length} item(s) closed because their cards finished` })
  }, [q.dataUpdatedAt])

  const parts = [
    waiting.length && `${waiting.length} chat(s) waiting on you`,
    cards.length && `${cards.length} card(s) need you`,
    failed.length && `${failed.length} failing job(s)`,
    open.length && `${open.length} open item(s)`
  ].filter(Boolean)

  return jsx(Tip, {
    label: parts.length ? parts.join(', ') : 'Inbox zero',
    children: jsxs('button', {
      type: 'button',
      className: 'ibx-chip',
      'data-active': count > 0 || undefined,
      onClick: () => {
        tap()
        host.revealPane(PANE_ID)
        requestAnimationFrame(() => document.querySelector('.ibx-pane')?.focus())
      },
      children: [jsx(Codicon, { name: 'inbox' }), count > 0 && jsx('span', { className: 'ibx-mono', children: count > 99 ? '99+' : count })]
    })
  })
}

// ── styles ───────────────────────────────────────────────────────────────

const EASE = 'cubic-bezier(.2,.8,.2,1)'
const STYLES = `
.ibx-pane{position:relative;display:flex;height:100%;flex-direction:column;outline:none;font-size:13px;color:var(--ui-text-primary)}
.ibx-mono{font-family:var(--font-mono,ui-monospace,monospace);font-variant-numeric:tabular-nums;font-size:11px}
.ibx-dim{color:var(--ui-text-quaternary)}
.ibx-list{display:flex;flex-direction:column;gap:2px;padding:2px 6px 56px}
.ibx-section-bar{display:flex;align-items:center;margin-top:6px}
.ibx-section-bar>.ibx-section-head{flex:1;margin-top:0}
.ibx-tools{display:flex;margin-right:2px}
.ibx-tools .ibx-btn{width:24px;height:24px;color:var(--ui-text-quaternary)}
.ibx-section{display:flex;flex-direction:column}
.ibx-section-head{display:flex;align-items:center;gap:6px;height:28px;padding:0 8px;margin-top:6px;font-size:11px;font-weight:500;color:var(--ui-text-tertiary);user-select:none}
.ibx-section-toggle{width:100%;border-radius:6px;text-align:left;cursor:pointer;transition:color 150ms ${EASE},background 150ms ${EASE}}
.ibx-section-toggle:hover{color:var(--ui-text-secondary);background:var(--chrome-action-hover)}
.ibx-section-toggle:focus-visible{outline:1px solid var(--ui-accent);outline-offset:-1px}
.ibx-caret{font-size:12px!important;margin-left:-3px;transition:transform 200ms ${EASE}}
.ibx-section-toggle[aria-expanded=true] .ibx-caret{transform:rotate(90deg)}
.ibx-count{color:var(--ui-text-quaternary)}
.ibx-collapse{display:grid;grid-template-rows:1fr;transition:grid-template-rows ${LEAVE_MS}ms ${EASE},opacity ${LEAVE_MS}ms ${EASE},transform ${LEAVE_MS}ms ${EASE}}
.ibx-collapse>div{min-height:0;overflow:hidden}
.ibx-collapse[data-leaving]{grid-template-rows:0fr;opacity:0;transform:translateX(12px);pointer-events:none}
.ibx-row{position:relative;display:flex;align-items:flex-start;gap:10px;padding:7px 8px 8px;border-radius:7px;cursor:pointer;outline:none;transition:background 150ms ${EASE}}
.ibx-row:hover,.ibx-row[data-selected]{background:var(--chrome-action-hover)}
.ibx-row:focus-visible{box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--ui-accent) 32%,transparent)}
.ibx-row[aria-busy]{cursor:progress}
.ibx-icon{display:flex;align-items:center;justify-content:center;width:16px;height:18px;flex-shrink:0;color:var(--ui-text-tertiary);font-size:14px}
.ibx-icon[data-tone=warn]{color:#D0A215}
.ibx-icon[data-tone=err]{color:var(--dt-destructive,#D14D41)}
.ibx-main{min-width:0;flex:1}
.ibx-line{display:flex;align-items:center;gap:8px;height:18px}
.ibx-title{min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ui-text-secondary)}
.ibx-row[data-unread] .ibx-title{color:var(--ui-text-primary);font-weight:600}
.ibx-row[data-muted] .ibx-title{color:var(--ui-text-tertiary)}
.ibx-meta{display:flex;align-items:center;gap:6px;flex-shrink:0;color:var(--ui-text-quaternary)}
.ibx-dot{width:6px;height:6px;border-radius:99px;background:var(--ui-accent)}
.ibx-sub{margin-top:2px;font-size:12px;line-height:17px;color:var(--ui-text-tertiary);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.ibx-row[data-muted] .ibx-sub{color:var(--ui-text-quaternary)}
.ibx-actions{display:none;align-items:center;gap:2px;margin:-4px -4px -4px 0}
.ibx-row:hover .ibx-actions,.ibx-row[data-selected] .ibx-actions,.ibx-row:has([data-open]) .ibx-actions{display:flex}
.ibx-row:hover .ibx-meta,.ibx-row[data-selected] .ibx-meta,.ibx-row:has([data-open]) .ibx-meta{display:none}
.ibx-btn{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:6px;color:var(--ui-text-tertiary);transition:background 150ms ${EASE},color 150ms ${EASE},transform 120ms ${EASE}}
.ibx-btn:hover,.ibx-btn[data-open]{background:var(--ui-control-hover-background,var(--chrome-action-hover));color:var(--ui-text-primary)}
.ibx-btn[data-tone=ok]:hover{color:var(--ui-accent)}
.ibx-btn:active{transform:scale(.94)}
.ibx-btn[data-spin] .codicon{animation:ibx-spin 800ms linear infinite}
.ibx-menu{min-width:220px;padding:4px}
.ibx-menu-label{padding:4px 8px 2px;font-size:11px;color:var(--ui-text-tertiary)}
.ibx-menu-item{display:flex;gap:8px;align-items:center;font-size:12px}
.ibx-menu-key{font-family:var(--font-mono,monospace);font-size:10px;width:10px;color:var(--ui-text-quaternary)}
.ibx-note{padding:10px 12px;font-size:12px;color:var(--ui-text-tertiary)}
.ibx-skeleton{display:flex;flex-direction:column;gap:6px;padding:8px}
.ibx-skeleton>div{height:42px;border-radius:7px;background:var(--chrome-action-hover);animation:ibx-pulse 1.4s ease-in-out infinite}
.ibx-enter{animation:ibx-in 360ms ${EASE} both;animation-delay:calc(var(--i,0) * 32ms)}
.ibx-zero{display:flex;flex-direction:column;align-items:center;text-align:center;gap:4px;padding:48px 14px 32px}
.ibx-zero-mark{width:40px;height:40px;margin-bottom:10px;overflow:visible}
.ibx-zero-ring{fill:none;stroke:var(--ui-stroke-secondary);stroke-width:1.5}
.ibx-zero-check{fill:none;stroke:var(--ui-accent);stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}
.ibx-zero-title{font-size:14px;font-weight:600}
.ibx-zero-line{font-family:var(--font-mono,monospace);font-style:italic;font-size:12px;color:var(--ui-text-tertiary)}
.ibx-zero[data-celebrate] .ibx-zero-ring{stroke-dasharray:132;--len:132;animation:ibx-draw 520ms ${EASE} both}
.ibx-zero[data-celebrate] .ibx-zero-check{stroke-dasharray:28;--len:28;animation:ibx-draw 380ms ${EASE} 380ms both}
.ibx-zero[data-celebrate] .ibx-zero-mark{animation:ibx-glow 900ms ${EASE} 650ms both}
.ibx-zero[data-celebrate] .ibx-zero-title,.ibx-zero[data-celebrate] .ibx-zero-line{animation:ibx-in 360ms ${EASE} 520ms both}
.ibx-keys{flex-shrink:0;height:24px;line-height:24px;padding:0 12px;color:var(--ui-text-quaternary);font-size:10.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;opacity:0;transition:opacity 200ms ${EASE};pointer-events:none}
.ibx-pane:focus-within .ibx-keys{opacity:1}
.ibx-pane:has(.ibx-toast) .ibx-keys{opacity:0}
.ibx-toast{position:absolute;left:8px;right:8px;bottom:6px;z-index:5;display:flex;align-items:center;gap:8px;padding:8px 8px 8px 12px;overflow:hidden;border-radius:8px;font-size:12px;background:var(--ui-bg-elevated);color:var(--ui-text-primary);border:1px solid var(--ui-stroke-secondary);box-shadow:0 8px 24px rgb(0 0 0 / .28);animation:ibx-up 260ms ${EASE} both}
.ibx-toast-msg{min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ibx-toast-undo{display:inline-flex;align-items:baseline;gap:6px;padding:3px 8px;border-radius:6px;font-weight:600;color:var(--ui-accent);transition:background 150ms ${EASE},transform 120ms ${EASE}}
.ibx-toast-undo:hover{background:var(--chrome-action-hover)}
.ibx-toast-undo:active{transform:scale(.94)}
.ibx-toast-bar{position:absolute;left:0;bottom:0;height:2px;width:100%;background:var(--ui-accent);opacity:.7;transform-origin:left;animation:ibx-bar linear both}
.ibx-chip{display:inline-flex;height:100%;align-items:center;gap:4px;padding:0 6px;font-size:11px;color:var(--ui-text-tertiary);transition:color 150ms ${EASE},background 150ms ${EASE}}
.ibx-chip:hover{background:var(--chrome-action-hover);color:var(--ui-text-primary)}
.ibx-chip[data-active]{color:var(--ui-text-secondary)}
.ibx-chip[data-active] .ibx-mono{color:var(--ui-accent)}
.ibx-chip .codicon{font-size:13px}
@keyframes ibx-in{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
@keyframes ibx-up{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes ibx-bar{from{transform:scaleX(1)}to{transform:scaleX(0)}}
@keyframes ibx-draw{from{stroke-dashoffset:var(--len)}to{stroke-dashoffset:0}}
@keyframes ibx-glow{0%{filter:none}40%{filter:drop-shadow(0 0 8px color-mix(in srgb,var(--ui-accent) 55%,transparent))}100%{filter:none}}
@keyframes ibx-spin{to{transform:rotate(360deg)}}
@keyframes ibx-pulse{50%{opacity:.45}}
@media (prefers-reduced-motion:reduce){.ibx-pane *,.ibx-chip{animation:none!important;transition:none!important}}
`

export default {
  id: ID,
  name: 'Inbox',
  register(ctx) {
    pluginCtx = ctx
    const style = document.createElement('style')
    style.dataset.plugin = ID
    style.textContent = STYLES
    document.head.append(style)
    ctx.onDispose?.(() => style.remove())
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'Inbox',
      data: { placement: 'right', width: '300px', hideOnly: true, tabTitle: () => jsx(InboxTabTitle, {}), tabTitleText: () => 'Inbox' },
      render: () => jsx(InboxPane, {})
    })
    ctx.register({ id: 'chip', area: 'statusBar.right', order: 120, render: () => jsx(InboxChip, {}) })
  }
}
