// Client-side state shared by the shell and the views: who is viewing, their
// display settings, what they've already seen, the drawer, and click actions.
// Per-viewer preferences live in localStorage; plan data always comes from the server.
import * as F from './format.js';

export const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private window etc. */ } },
};

export const store = {
  snap: null,
  ui: {},            // transient UI state (open tab, pending confirm, …)
  rerender: () => {},
  navigate: () => {},
};

// ---- settings ----------------------------------------------------------------
export const DEFAULT_SETTINGS = {
  theme: 'dark', accent: '#8b7cf6', density: 'comfortable', clock24: false, highlight: true, planMode: 'plan',
};
export const ACCENTS = ['#8b7cf6', '#3d9bf0', '#ec5f9c', '#2fbf8f', '#f5a524'];

export function settings() { return { ...DEFAULT_SETTINGS, ...LS.get('ff.settings', {}) }; }

export function saveSettings(patch) {
  LS.set('ff.settings', { ...settings(), ...patch });
  applySettings();
  store.rerender();
}

export function applySettings() {
  const s = settings();
  const root = document.documentElement;
  root.dataset.theme = s.theme;
  root.dataset.density = s.density;
  root.dataset.flash = s.highlight ? 'on' : 'off';
  root.style.setProperty('--accent', s.accent);
  F.fmt.clock24 = !!s.clock24;
}

// ---- who is viewing ------------------------------------------------------------
export function currentUser() {
  const staff = store.snap?.staff || [];
  const id = LS.get('ff.user', null);
  return staff.find((s) => s.id === id) || staff.find((s) => s.spotlight) || staff[0] || null;
}
export function setUser(id) { LS.set('ff.user', id); store.ui = {}; store.rerender(); }

export const initials = (name) => String(name || '?').split(/\s+/).map((p) => p[0]).slice(0, 2).join('').toUpperCase();
export function avatar(st, size = '') {
  const i = Math.max(0, (store.snap?.staff || []).findIndex((s) => s.id === st?.id));
  return `<span class="avatar ${size}" style="--av: var(--avatar-${(i % 5) + 1})" title="${F.esc(st?.name || '')}">${F.esc(initials(st?.name))}</span>`;
}

// ---- seen tracking (per user, per feed) ------------------------------------------
const seenKey = (kind) => `ff.seen.${currentUser()?.id || 'anon'}.${kind}`;
export function seen(kind) { return new Set(LS.get(seenKey(kind), [])); }
export function markSeen(kind, ids) {
  const s = seen(kind);
  ids.forEach((id) => s.add(id));
  LS.set(seenKey(kind), [...s].slice(-800));
  store.rerender();
}

// ---- derived lists -------------------------------------------------------------
export function userChanges(snap, user, all = false) {
  return (snap.changes || []).filter((c) => all || !user || c.staff_id === user.id);
}
export function unseenChanges(snap, user) {
  const s = seen('changes');
  return userChanges(snap, user).filter((c) => c.by !== 'You' && !s.has(c.id));
}

const VE_LABEL = {
  'need.opened': 'Need opened', 'visit.proposed': 'Slot proposed', 'visit.booked': 'Visit booked', 'eta.updated': 'ETA updated',
  'visit.arrived': 'Engineer arrived', 'commissioning.started': 'Commissioning started', 'visit.completed': 'Visit completed', 'order.shipped': 'Order shipped',
};
export const veLabel = (t) => VE_LABEL[t] || t;

// Everything Fleet said or did, newest first.
export function fleetFeed(snap) {
  const items = [];
  for (const [ch, msgs] of Object.entries(snap.messages || {})) {
    for (const m of msgs) {
      if (m.author !== 'Fleet') continue;
      items.push({ id: `m-${m.id}`, at: m.at, kind: ch === 'ops-log' ? 'handoff' : 'customer', channel: ch, text: m.text });
    }
  }
  for (const v of snap.visit_events || []) {
    if (v.by !== 'Fleet') continue;
    items.push({ id: `v-${v.id}`, at: v.at, kind: 'ticket', channel: 'tickets', text: `${veLabel(v.type)} · ${v.need_id}`, need_id: v.need_id, data: v.data });
  }
  return items.sort((a, b) => String(b.at).localeCompare(String(a.at)));
}
export function unseenFleet(snap) { const s = seen('fleet'); return fleetFeed(snap).filter((i) => !s.has(i.id)); }

// Human text for one change: "Start 7:00 PM → 7:30 PM · Leave by 6:34 PM → 7:04 PM".
const FIELD = { start: 'Start', end: 'End', leave_by: 'Leave by', eta: 'ETA', delay_min: 'Delay', need_id: 'Need', title: 'Title', place: 'Place' };
const val = (field, v, today) => {
  if (v == null || v === '') return '—';
  if (field === 'delay_min') return v ? `+${v} min` : 'none';
  if (typeof v === 'string' && /^\d{4}-\d\d-\d\dT/.test(v)) return F.time(v, today);
  return String(v);
};
export function describeChange(c, today) {
  if (c.added) {
    const a = Object.fromEntries(c.diff.map((d) => [d.field, d.after]));
    return `Added ${a.start ? F.range(a.start, a.end, today) : ''}`.trim();
  }
  if (c.removed) return 'Removed from the plan';
  const order = ['start', 'end', 'leave_by', 'eta', 'delay_min', 'title', 'place', 'need_id'];
  return [...c.diff].sort((a, b) => order.indexOf(a.field) - order.indexOf(b.field))
    .map((d) => `${FIELD[d.field] || d.field} ${val(d.field, d.before, today)} → ${val(d.field, d.after, today)}`).join(' · ');
}

export const KIND_LABEL = {
  booked: 'New visit', added: 'Added', attached: 'Need attached', rescheduled: 'Rescheduled', delayed: 'Delay', replanned: 'Re-planned',
  edited: 'Edited', reverted: 'Reverted', changed: 'Changed',
};
export const kindChip = (k) => `<span class="kind kind-${F.esc(k)}">${F.esc(KIND_LABEL[k] || k)}</span>`;

// ---- actions (click handlers registered by views; fired via data-action) ------------
export const actions = {};
export function on(name, fn) { actions[name] = fn; }

export async function post(url, body) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

// ---- drawer (slide-over panel) ------------------------------------------------------
// render() is called on every poll while open, so drawer content stays live.
export function openDrawer(render, mount) {
  store.ui.drawer = { render, mount };
  store.rerender();
}
export function closeDrawer() { store.ui.drawer = null; store.rerender(); }

export function toast(text, ok = true) {
  store.ui.toast = { text, ok, until: Date.now() + 3500 };
  store.rerender();
  setTimeout(() => store.rerender(), 3600);
}
