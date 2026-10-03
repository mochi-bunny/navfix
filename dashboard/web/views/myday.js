// My day: one person's plan in the simplest possible form. Times, places and
// travel all come from the server; nothing is computed here beyond layout.
import * as F from '../format.js';
import { icon } from '../icons.js';
import { store, on, settings, saveSettings, unseenChanges, userChanges, avatar, kindChip } from '../state.js';
import '../drawers.js';

const DOW = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const MON = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

on('plan-mode', (el) => saveSettings({ planMode: el.dataset.mode }));

function miniCalendar(dateStr) {
  const d = new Date(dateStr + 'T12:00:00Z');
  const y = d.getUTCFullYear(), m = d.getUTCMonth(), today = d.getUTCDate();
  const first = (new Date(Date.UTC(y, m, 1)).getUTCDay() + 6) % 7; // Monday first
  const days = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  const cells = [];
  for (let i = 0; i < first; i++) cells.push('<span class="mc-day is-out"></span>');
  for (let i = 1; i <= days; i++) cells.push(`<span class="mc-day${i === today ? ' is-today' : ''}">${i}</span>`);
  return `<div class="card mini-cal"><div class="card-head"><strong>${MON[m]} ${y}</strong></div>
    <div class="mc-grid">${['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'].map((x) => `<span class="mc-dow">${x}</span>`).join('')}${cells.join('')}</div></div>`;
}

const changeChips = (changes, eventId, unseenIds) => changes
  .filter((c) => c.event_id === eventId && !c.reverted_by && c.kind !== 'reverted')
  .slice(-2)
  .map((c) => `<span class="chip-change${unseenIds.has(c.id) ? ' is-unseen' : ''}">${kindChip(c.kind)}</span>`).join('');

on('go-simple', () => store.navigate('/plan'));

export function planList(s, events, ctx, changes, unseenIds, { chips = true } = {}) {
  const today = s.clock.date;
  const nowIso = ctx.now ? `${ctx.now.date}T${String(ctx.now.h).padStart(2, '0')}:${String(ctx.now.m).padStart(2, '0')}` : null;
  const nowLine = `<div class="pl-now"><span>Now · ${ctx.now ? F.clockText(ctx.now.h, ctx.now.m) : ''}</span></div>`;
  let nowPlaced = !nowIso || ctx.now.date !== today;
  const rows = [];
  for (const e of events) {
    const leg = s.legs.find((l) => l.event_id === e.id);
    const past = nowIso && e.end.slice(0, 16) <= nowIso;
    const live = nowIso && e.start.slice(0, 16) <= nowIso && !past;
    if (!nowPlaced && leg && nowIso < leg.leave_by.slice(0, 16)) { rows.push(nowLine); nowPlaced = true; }
    if (leg) {
      const late = leg.late_by > 0;
      rows.push(`<div class="pl-leg${late ? ' is-late' : ''}${leg.delay_min ? ' has-delay' : ''}" data-key="pll-${F.esc(e.id)}-${F.esc(leg.leave_by)}-${leg.delay_min}">
        <span class="pl-leg-line"></span>
        <span class="pl-leg-text">${F.modeIcon(leg.mode)} Leave <strong>${F.time(leg.leave_by, today)}</strong> · ${F.esc(leg.route || leg.mode)} · ${leg.minutes} min
        ${leg.delay_min ? `<span class="delay">+${leg.delay_min} min delay</span>` : ''}
        · arrive ${F.time(leg.eta, today)}${late ? ` <span class="delay">${leg.late_by} min late</span>` : ''}
        ${leg.note ? `<span class="pl-leg-note">${F.esc(leg.note)}</span>` : ''}</span>
      </div>`);
    }
    if (!nowPlaced && nowIso < e.start.slice(0, 16)) { rows.push(nowLine); nowPlaced = true; }
    rows.push(`<div class="pl-row cat-${F.esc(e.type)}${past ? ' is-past' : ''}${live ? ' is-live' : ''}" data-key="plr-${F.esc(e.id)}-${F.esc(e.start)}-${F.esc(e.need_id || '')}">
      <div class="pl-time"><div class="pl-start">${F.time(e.start, today)}</div><div class="pl-end">${F.time(e.end, today)}</div></div>
      <button class="pl-card" data-action="open-event" data-id="${F.esc(e.id)}">
        <span class="pl-title">${F.esc(e.title)}</span>
        <span class="pl-place">${icon('pin')} ${F.esc(e.place || '')}</span>
        <span class="pl-tags">${e.need_id ? `<span class="pl-need">${F.esc(e.need_id)}</span>` : ''}${live ? '<span class="pl-live">now</span>' : ''}${chips ? changeChips(changes, e.id, unseenIds) : ''}</span>
      </button>
    </div>`);
    if (!nowPlaced && live) { nowPlaced = true; }
  }
  if (!nowPlaced) rows.push(nowLine);
  return `<div class="plan">${rows.join('') || '<p class="empty">Nothing planned today.</p>'}</div>`;
}

const H0 = 8, H1 = 22, PX = 56; // timeline hours and pixels per hour
function timeline(s, events, ctx) {
  const today = s.clock.date;
  const y = (iso) => ((F.minOfDay(iso) - H0 * 60) / 60) * PX;
  const hours = [];
  for (let h = H0; h <= H1; h++) hours.push(`<div class="tl-hour" style="top:${(h - H0) * PX}px"><span>${F.clockText(h, 0).replace(':00', '')}</span></div>`);
  const legs = events.map((e) => s.legs.find((l) => l.event_id === e.id)).filter(Boolean).map((l) =>
    `<div class="tl-leg${l.late_by ? ' is-late' : ''}" style="top:${y(l.leave_by)}px;height:${Math.max(6, y(l.eta) - y(l.leave_by))}px" title="Leave ${F.time(l.leave_by, today)} · ${l.minutes} min"></div>`).join('');
  const cards = events.map((e) => `<button class="tl-card cat-${F.esc(e.type)}" data-action="open-event" data-id="${F.esc(e.id)}" data-key="tlc-${F.esc(e.id)}-${F.esc(e.start)}"
      style="top:${y(e.start)}px;height:${Math.max(28, y(e.end) - y(e.start) - 4)}px">
      <span class="tl-card-time">${F.shortRange(e.start, e.end)}</span><span class="tl-card-title">${F.esc(e.title)}</span>
      ${e.need_id ? `<span class="pl-need">${F.esc(e.need_id)}</span>` : ''}</button>`).join('');
  const nowY = ctx.now && ctx.now.date === today ? ((ctx.now.minOfDay - H0 * 60) / 60) * PX : null;
  return `<div class="tl" style="height:${(H1 - H0) * PX + 10}px">${hours.join('')}<div class="tl-lane">${legs}</div><div class="tl-events">${cards}</div>
    ${nowY != null && nowY >= 0 && nowY <= (H1 - H0) * PX ? `<div class="tl-now" style="top:${nowY}px"></div>` : ''}</div>`;
}

function nextUp(s, events, ctx) {
  const today = s.clock.date;
  const nowIso = ctx.now ? `${ctx.now.date}T${String(ctx.now.h).padStart(2, '0')}:${String(ctx.now.m).padStart(2, '0')}` : '';
  const e = events.find((x) => x.end.slice(0, 16) > nowIso);
  if (!e) return '<div class="card next-up"><div class="card-head"><strong>Next up</strong></div><p class="muted">Done for the day.</p></div>';
  const leg = s.legs.find((l) => l.event_id === e.id);
  const live = e.start.slice(0, 16) <= nowIso;
  const leaveIn = leg && !live ? Math.round(F.minOfDay(leg.leave_by) - ctx.now.minOfDay) : null;
  const pill = live ? 'happening now' : leaveIn != null ? (leaveIn > 0 ? `leave in ${leaveIn} min` : 'leave now') : `starts ${F.time(e.start, today)}`;
  return `<div class="card next-up cat-${F.esc(e.type)}" data-key="nu-${F.esc(e.id)}">
    <div class="nu-top"><span class="nu-time">${F.range(e.start, e.end, today)}</span><span class="nu-pill${leaveIn != null && leaveIn <= 10 ? ' is-urgent' : ''}">${icon('clock')} ${pill}</span></div>
    <div class="nu-title">${F.esc(e.title)}</div>
    <div class="nu-place">${icon('pin')} ${F.esc(e.place || '')}</div>
    ${leg && !live ? `<div class="nu-leg">${F.modeIcon(leg.mode)} ${F.esc(leg.route || leg.mode)} · ${leg.minutes} min${leg.delay_min ? ` <span class="delay">+${leg.delay_min}</span>` : ''}</div>` : ''}
    <div class="btn-row"><a class="btn" href="/leg" data-nav>${icon('route')} Route</a><button class="btn btn-primary" data-action="open-event" data-id="${F.esc(e.id)}">Details</button></div>
  </div>`;
}

function categories(events) {
  const mins = {};
  for (const e of events) mins[e.type] = (mins[e.type] || 0) + (F.minOfDay(e.end) - F.minOfDay(e.start));
  const max = Math.max(1, ...Object.values(mins));
  const count = (t) => events.filter((e) => e.type === t).length;
  return `<div class="card cats"><div class="card-head"><strong>Today by type</strong></div>
    ${Object.entries(mins).map(([t, m]) => `<div class="cat-row cat-${F.esc(t)}"><span class="cat-dot"></span><span class="cat-name">${F.esc(t)} <span class="muted">×${count(t)}</span></span>
      <span class="cat-bar"><span style="width:${((m / max) * 100).toFixed(0)}%"></span></span></div>`).join('')}</div>`;
}

function fleetInDay(s, user) {
  const mine = s.needs.filter((n) => n.assigned_staff === user.id || s.events.some((e) => e.need_id === n.need_id && e.staff_id === user.id));
  if (!mine.length) return '';
  return `<div class="card fleet-mini"><div class="card-head"><strong>${icon('robot')} From Fleet</strong><a href="/fleet" data-nav class="muted small">Fleet watch</a></div>
    ${mine.map((n) => `<a class="fm-row" href="/needs/${F.esc(n.need_id)}" data-nav data-key="fm-${F.esc(n.need_id)}-${F.esc(n.status)}">${F.sev(n.severity)} <span class="fm-title">${F.esc(n.title || n.kind)}</span>${F.status(n.status)}</a>`).join('')}</div>`;
}

export default {
  title: 'My day',
  render(s, ctx) {
    const user = ctx.user;
    if (!user) return '<p class="empty">No staff data yet.</p>';
    const today = s.clock.date;
    const events = s.events.filter((e) => e.staff_id === user.id);
    const changes = userChanges(s, user);
    const unseen = unseenChanges(s, user);
    const unseenIds = new Set(unseen.map((c) => c.id));
    const mode = settings().planMode;
    const d = new Date(today + 'T12:00:00Z');
    const pitches = events.filter((e) => e.type === 'pitch').length;

    const side = `<div class="md-side">${miniCalendar(today)}${nextUp(s, events, ctx)}${categories(events)}${fleetInDay(s, user)}</div>`;
    const head = `<header class="md-head">
        <div class="md-who">${avatar(user, 'lg')}<div><div class="md-date">${DOW[d.getUTCDay()]}, ${MON[d.getUTCMonth()]} ${d.getUTCDate()} <span class="pill-today">Today</span></div>
        <div class="md-sub">${F.esc(user.name)} · ${events.length} events · ${pitches} pitch${pitches === 1 ? '' : 'es'}</div></div></div>
        <div class="md-tools">
          <button class="btn btn-glass" data-action="go-simple" title="Back to the simple plan">${icon('list')} Scheduler only</button>
          <div class="seg"><button data-action="plan-mode" data-mode="plan" class="${mode === 'plan' ? 'is-on' : ''}">${icon('list')} Plan</button>
            <button data-action="plan-mode" data-mode="timeline" class="${mode === 'timeline' ? 'is-on' : ''}">${icon('calendar')} Timeline</button></div>
          <button class="updates-btn${unseen.length ? ' has-new' : ''}" data-action="open-updates">${icon('refresh')} Updates${unseen.length ? `<span class="count">${unseen.length}</span>` : ''}</button>
        </div></header>`;
    const body = mode === 'timeline' ? timeline(s, events, ctx) : planList(s, events, ctx, changes, unseenIds);
    return [['side', side], ['main', `<div class="md-main hero">${head}<div class="md-body">${body}</div></div>`]];
  },
};
