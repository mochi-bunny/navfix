// Scheduler on the side of the command center: the viewer's day in compact form,
// with one click to the scheduler on its own.
import * as F from '../format.js';
import { icon } from '../icons.js';
import { avatar, unseenChanges } from '../state.js';

export default {
  title: 'Scheduler',
  more: '/plan',
  meta(s, ctx) {
    const n = ctx.user ? unseenChanges(s, ctx.user).length : 0;
    return n ? `<button class="btn btn-sm" data-action="open-updates">${icon('bell')} ${n} updates</button>` : '';
  },
  render(s, ctx) {
    const user = ctx.user;
    if (!user) return '<p class="empty">No staff data.</p>';
    const today = s.clock.date;
    const nowIso = ctx.now ? `${ctx.now.date}T${String(ctx.now.h).padStart(2, '0')}:${String(ctx.now.m).padStart(2, '0')}` : '';
    const events = s.events.filter((e) => e.staff_id === user.id);
    const leg = s.current_legs?.[user.id];
    const legLine = leg && leg.phase !== 'done'
      ? `<div class="ss-leg${leg.late_by ? ' is-late' : ''}">${icon('route')} <span>${leg.phase === 'in_transit' ? 'On the way to' : 'Next: leave'} ${leg.phase === 'in_transit' ? F.esc(leg.destination || '') : `<strong>${F.time(leg.leave_by, today)}</strong> for ${F.esc(leg.event_title || '')}`}
         ${leg.delay_min ? `<span class="delay">+${leg.delay_min} min</span>` : ''}${leg.phase === 'in_transit' ? ` · ETA ${F.time(leg.eta, today)}` : ''}</span></div>` : '';
    // Collapse everything finished except the latest, so the rest of the day fits.
    const pastCount = events.filter((e) => e.end.slice(0, 16) <= nowIso).length;
    const hidden = Math.max(0, pastCount - 1);
    const earlier = hidden ? `<div class="ss-earlier">${hidden} earlier event${hidden === 1 ? '' : 's'} done</div>` : '';
    const rows = earlier + events.slice(hidden).map((e) => {
      const past = e.end.slice(0, 16) <= nowIso;
      const live = e.start.slice(0, 16) <= nowIso && !past;
      const l = e.leg;
      return `<button class="ss-row cat-${F.esc(e.type)}${past ? ' is-past' : ''}${live ? ' is-live' : ''}" data-action="open-event" data-id="${F.esc(e.id)}" data-key="ss-${F.esc(e.id)}-${F.esc(e.start)}-${l?.delay_min || 0}">
        <span class="ss-time">${F.time(e.start, today)}</span>
        <span class="ss-bar"></span>
        <span class="ss-main"><span class="ss-title">${F.esc(e.title)}${e.need_id ? ` <span class="pl-need">${F.esc(e.need_id)}</span>` : ''}</span>
          <span class="ss-sub">${F.esc(e.place || '')}${l ? ` · leave ${F.time(l.leave_by, today)}` : ''}${l?.delay_min ? ` <span class="delay">+${l.delay_min}</span>` : ''}</span></span>
      </button>`;
    }).join('');
    return [['who', `<div class="ss-who">${avatar(user)}<div><strong>${F.esc(user.name)}</strong><div class="muted small">${F.esc(user.role || '')}</div></div></div>${legLine}`],
      ['list', `<div class="ss-list">${rows || '<p class="empty">Nothing planned.</p>'}</div>`],
      ['actions', `<div class="btn-row ss-actions"><a class="btn btn-primary" href="/plan" data-nav>${icon('expand')} Scheduler only</a>
        <button class="btn" data-action="add-change">${icon('edit')} Add change</button>
        <a class="btn" href="/day" data-nav>${icon('user')} My day</a></div>`]];
  },
};
