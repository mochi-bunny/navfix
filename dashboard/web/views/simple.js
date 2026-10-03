// Scheduler only: just the plan. One menu (back to the dashboard, the full day
// view, or another person) and one button to add a change to the schedule.
import * as F from '../format.js';
import { icon } from '../icons.js';
import { store, on, setUser, userChanges, unseenChanges, avatar } from '../state.js';
import { planList } from './myday.js';
import { openAddChange } from '../drawers.js';

const DOW = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const MON = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

on('menu-toggle', () => { store.ui.menuOpen = !store.ui.menuOpen; store.rerender(); });
on('go-to', (el) => { store.ui.menuOpen = false; store.navigate(el.dataset.path); });
on('menu-user', (el) => { store.ui.menuOpen = false; setUser(el.dataset.id); });
on('add-change', () => { store.ui.menuOpen = false; openAddChange(); });

export default {
  title: 'Plan',
  render(s, ctx) {
    const user = ctx.user;
    if (!user) return '<p class="empty">No staff data yet.</p>';
    const events = s.events.filter((e) => e.staff_id === user.id);
    const unseenIds = new Set(unseenChanges(s, user).map((c) => c.id));
    const d = new Date(s.clock.date + 'T12:00:00Z');

    const menu = `<div class="menu">
        <button class="icon-btn" data-action="menu-toggle" title="Menu" aria-expanded="${!!store.ui.menuOpen}">${icon('list')}</button>
        <div class="menu-pop"${store.ui.menuOpen ? '' : ' hidden'}>
          <button class="menu-item" data-action="go-to" data-path="/">${icon('grid')}<span><strong>Dashboard</strong><br><span class="muted small">Fleet, live feeds and everything else</span></span></button>
          <button class="menu-item" data-action="go-to" data-path="/day">${icon('calendar')}<span><strong>Full day view</strong><br><span class="muted small">Plan with timeline, Fleet signals, updates</span></span></button>
          <div class="menu-sep"></div>
          <div class="menu-label">Viewing as</div>
          ${s.staff.map((st) => `<button class="menu-item${st.id === user.id ? ' is-on' : ''}" data-action="menu-user" data-id="${F.esc(st.id)}">${avatar(st, 'sm')} ${F.esc(st.name)}${st.id === user.id ? ` <span class="menu-check">${icon('check')}</span>` : ''}</button>`).join('')}
        </div>
      </div>`;

    const head = `<header class="sp-head">
        <div>
          <div class="sp-date">${DOW[d.getUTCDay()]}, ${MON[d.getUTCMonth()]} ${d.getUTCDate()}</div>
          <div class="sp-sub">${F.esc(user.name)}${ctx.now ? ` · ${F.clockText(ctx.now.h, ctx.now.m)}` : ''}${s.any_mock ? ' · <span class="sp-mock">mock data</span>' : ''}</div>
        </div>
        <div class="sp-actions">
          <button class="btn btn-primary btn-lg" data-action="add-change">${icon('edit')} Add change</button>
          ${menu}
        </div>
      </header>`;

    return [['head', head], ['plan', `<div class="sp-plan hero">${planList(s, events, ctx, userChanges(s, user), unseenIds, { chips: false })}</div>`]];
  },
};
