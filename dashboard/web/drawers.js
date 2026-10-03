// Slide-over panels: event details/edit and the per-user updates list.
import * as F from './format.js';
import { icon } from './icons.js';
import {
  store, on, post, openDrawer, closeDrawer, toast, currentUser, avatar,
  userChanges, unseenChanges, markSeen, describeChange, kindChip,
} from './state.js';

const hhmm = (iso) => (iso ? iso.slice(11, 16) : '');

function changeItem(c, today, unseenIds) {
  return `<li class="chg${unseenIds?.has(c.id) ? ' is-unseen' : ''}" data-key="dc-${F.esc(c.id)}">
    <div class="chg-top">${kindChip(c.kind)}<span class="chg-time">${F.time(c.at, today)}</span><span class="chg-by">${F.esc(c.by)}</span></div>
    <div class="chg-title">${F.esc(c.event_title || '')} ${F.needLink(c.need_id)}</div>
    <div class="chg-diff">${F.esc(describeChange(c, today))}</div>
    ${c.reason ? `<div class="chg-reason">${F.esc(c.reason)}</div>` : ''}
  </li>`;
}

// ---- event drawer -----------------------------------------------------------------
export function openEvent(eventId) {
  const ev0 = store.snap.events.find((e) => e.id === eventId);
  if (!ev0) return;
  // The form is built once from the event as it was when opened, so polling never wipes typing.
  const form = `<form class="edit-form" data-form="save-event" data-id="${F.esc(eventId)}">
      <h3 class="drawer-sub">${icon('edit')} Edit</h3>
      <label>Title <input name="title" value="${F.esc(ev0.title)}"></label>
      <div class="row2"><label>Start <input name="start" type="time" value="${hhmm(ev0.start)}"></label>
        <label>End <input name="end" type="time" value="${hhmm(ev0.end)}"></label></div>
      <label>Place <input name="place" value="${F.esc(ev0.place || '')}"></label>
      <label>Reason <input name="reason" placeholder="optional, shown in the change log"></label>
      <p class="muted small">Field re-checks travel for the neighbouring legs and tells everyone affected.</p>
      <div class="confirm-row" hidden><span>Send this change to Field?</span></div>
      <div class="btn-row">
        <button type="submit" class="btn btn-primary" name="step" value="review">Save changes</button>
        <button type="submit" class="btn btn-primary" name="step" value="confirm" hidden>Yes, update the plan</button>
        <button type="button" class="btn" data-action="drawer-close">Cancel</button>
      </div>
    </form>`;

  openDrawer(() => {
    const s = store.snap;
    const today = s.clock.date;
    const ev = s.events.find((e) => e.id === eventId);
    if (!ev) return [['info', '<p class="empty">This event is no longer in the plan.</p>']];
    const st = s.staff.find((x) => x.id === ev.staff_id);
    const leg = s.legs.find((l) => l.event_id === ev.id);
    const hist = userChanges(s, null, true).filter((c) => c.event_id === ev.id).reverse();
    const info = `<header class="drawer-head cat-${F.esc(ev.type)}">
        <div class="drawer-kicker">${F.esc(ev.type)} · ${avatar(st, 'sm')} ${F.esc(st?.name || '')}</div>
        <h2>${F.esc(ev.title)} ${F.needLink(ev.need_id)}</h2>
        <div class="drawer-when">${icon('clock')} ${F.range(ev.start, ev.end, today)}</div>
        <div class="drawer-where">${icon('pin')} ${F.esc(ev.place || '')}</div>
      </header>
      ${leg ? `<div class="drawer-leg${leg.late_by ? ' is-late' : ''}">${icon('route')}
        <div><strong>${F.modeIcon(leg.mode)} ${F.esc(leg.route || leg.mode)} · ${leg.minutes} min</strong>
        <div>Leave by ${F.time(leg.leave_by, today)} · arrive ${F.time(leg.eta, today)}${leg.delay_min ? ` · <span class="delay">+${leg.delay_min} min delay</span>` : ''}${leg.late_by ? ` · <span class="delay">${leg.late_by} min late</span>` : ''}</div>
        ${leg.note ? `<div class="muted">${F.esc(leg.note)}</div>` : ''}</div></div>` : ''}
      <h3 class="drawer-sub">${icon('history')} Changes to this event</h3>
      ${hist.length ? `<ul class="chg-list">${hist.map((c) => changeItem(c, today)).join('')}</ul>` : '<p class="empty">No changes yet.</p>'}`;
    return [['info', info], ['form', form]];
  });
}

on('open-event', (el) => openEvent(el.dataset.id));
on('drawer-close', () => closeDrawer());

on('save-event', async (form, submitter) => {
  const confirmBtn = form.querySelector('button[value="confirm"]');
  if (submitter?.value === 'review') {
    form.querySelector('.confirm-row').hidden = false;
    confirmBtn.hidden = false;
    submitter.hidden = true;
    return;
  }
  const ev = store.snap.events.find((e) => e.id === form.dataset.id);
  const fd = Object.fromEntries(new FormData(form));
  const set = {};
  if (fd.title && fd.title !== ev.title) set.title = fd.title;
  if (fd.place && fd.place !== ev.place) set.place = fd.place;
  if (fd.start && fd.start !== hhmm(ev.start)) set.start = fd.start;
  if (fd.end && fd.end !== hhmm(ev.end)) set.end = fd.end;
  if (!Object.keys(set).length) { toast('Nothing changed', false); return; }
  try {
    await post('/api/edit/event', { event_id: ev.id, set, reason: fd.reason || null });
    toast('Sent to Field: plan updated');
    closeDrawer();
  } catch (e) {
    toast(`Update failed: ${e.message}`, false);
  }
});

// ---- add change (new event, or change an existing one) --------------------------------
const TYPES = ['meeting', 'pitch', 'visit', 'lunch', 'dinner', 'standup', 'maintenance', 'office'];

export function openAddChange() {
  const s = store.snap;
  const user = currentUser();
  if (!user) return;
  const today = s.clock.date;
  const mine = s.events.filter((e) => e.staff_id === user.id);
  // Default slot for a new event: the next half hour, 30 minutes long.
  const now = F.minOfDay(s.clock.sim_time);
  const startMin = Math.min(23 * 60, Math.ceil((now + 1) / 30) * 30);
  const hm = (m) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;

  const form = `<form class="edit-form add-form" data-form="add-change" data-staff="${F.esc(user.id)}">
      <label>What
        <select name="target" data-change="add-pick">
          <option value="">＋ New event</option>
          ${mine.map((e) => `<option value="${F.esc(e.id)}">Change: ${F.time(e.start, today)} · ${F.esc(e.title)}</option>`).join('')}
        </select></label>
      <label>Title <input name="title" placeholder="e.g. Call with Globex ops"></label>
      <label class="only-new">Type <select name="type">${TYPES.map((t) => `<option>${t}</option>`).join('')}</select></label>
      <div class="row2"><label>Start <input name="start" type="time" value="${hm(startMin)}"></label>
        <label>End <input name="end" type="time" value="${hm(Math.min(23 * 60 + 59, startMin + 30))}"></label></div>
      <label>Place <input name="place" placeholder="Where"></label>
      <label>Reason <input name="reason" placeholder="optional, shown in the change log"></label>
      <p class="muted small">Field checks for conflicts and travel, then updates the plan and tells anyone affected.</p>
      <div class="confirm-row" hidden><span>Send this to Field?</span></div>
      <div class="btn-row">
        <button type="submit" class="btn btn-primary" name="step" value="review">Review</button>
        <button type="submit" class="btn btn-primary" name="step" value="confirm" hidden>Yes, update the plan</button>
        <button type="button" class="btn" data-action="drawer-close">Cancel</button>
      </div>
    </form>`;
  const head = `<header class="drawer-head plain"><div class="drawer-kicker">${avatar(user, 'sm')} ${F.esc(user.name)}</div>
      <h2>Add a change</h2><p class="muted">Add a new event, or move or rename one already in your plan.</p></header>`;
  openDrawer(() => [['head', head], ['form', form]]);
}

function resetConfirm(form) {
  form.querySelector('.confirm-row').hidden = true;
  form.querySelector('button[value="confirm"]').hidden = true;
  form.querySelector('button[value="review"]').hidden = false;
}

on('add-pick', (sel) => {
  const form = sel.form;
  const f = form.elements;
  const ev = store.snap.events.find((e) => e.id === sel.value);
  form.querySelector('.only-new').hidden = !!ev;
  if (ev) {
    f.title.value = ev.title;
    f.start.value = hhmm(ev.start);
    f.end.value = hhmm(ev.end);
    f.place.value = ev.place || '';
  } else {
    f.title.value = '';
    f.place.value = '';
  }
  resetConfirm(form);
});

on('add-change', async (form, submitter) => {
  const fd = Object.fromEntries(new FormData(form));
  const ev = store.snap.events.find((e) => e.id === fd.target);
  if (submitter?.value === 'review') {
    if (!ev && !fd.title.trim()) { toast('Give the new event a title', false); return; }
    if (fd.end <= fd.start) { toast('End must be after start', false); return; }
    form.querySelector('.confirm-row').hidden = false;
    form.querySelector('.confirm-row span').textContent = ev
      ? `Change "${ev.title}" to ${fd.start}–${fd.end}? Field will re-check travel.`
      : `Add "${fd.title.trim()}" ${fd.start}–${fd.end}? Field will check for conflicts.`;
    form.querySelector('button[value="confirm"]').hidden = false;
    submitter.hidden = true;
    return;
  }
  try {
    if (ev) {
      const set = {};
      if (fd.title && fd.title !== ev.title) set.title = fd.title;
      if (fd.place && fd.place !== ev.place) set.place = fd.place;
      if (fd.start && fd.start !== hhmm(ev.start)) set.start = fd.start;
      if (fd.end && fd.end !== hhmm(ev.end)) set.end = fd.end;
      if (!Object.keys(set).length) { toast('Nothing changed', false); resetConfirm(form); return; }
      await post('/api/edit/event', { event_id: ev.id, set, reason: fd.reason || null });
    } else {
      await post('/api/edit/create', {
        staff_id: form.dataset.staff,
        set: { title: fd.title.trim(), type: fd.type, start: fd.start, end: fd.end, place: fd.place },
        reason: fd.reason || null,
      });
    }
    toast('Sent to Field: plan updated');
    closeDrawer();
  } catch (e) {
    toast(`Not added: ${e.message}`, false);
    resetConfirm(form);
  }
});

// ---- updates drawer (the corner button on My day) ----------------------------------
export function openUpdates() {
  openDrawer(() => {
    const s = store.snap;
    const user = currentUser();
    const today = s.clock.date;
    const unseen = unseenChanges(s, user);
    const unseenIds = new Set(unseen.map((c) => c.id));
    const recent = userChanges(s, user).slice(-12).reverse();
    return [['list', `<header class="drawer-head plain"><div class="drawer-kicker">${F.esc(user?.name || '')}</div>
        <h2>Plan updates</h2>
        <p class="muted">${unseen.length ? `${unseen.length} new since you last looked` : 'You are up to date'}</p>
        <div class="btn-row">
          <button class="btn btn-primary" data-action="mark-updates-seen"${unseen.length ? '' : ' disabled'}>${icon('check')} Mark all seen</button>
          <a class="btn" href="/changes" data-nav>${icon('history')} Change log</a>
        </div></header>
      ${recent.length ? `<ul class="chg-list">${recent.map((c) => changeItem(c, today, unseenIds)).join('')}</ul>` : '<p class="empty">No changes to your plan yet.</p>'}`]];
  });
}

on('open-updates', () => openUpdates());
on('mark-updates-seen', () => markSeen('changes', unseenChanges(store.snap, currentUser()).map((c) => c.id)));
