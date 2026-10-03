// Fleet updates: everything the Fleet agent said or did, newest first.
import * as F from '../format.js';
import { icon } from '../icons.js';
import { store, on, fleetFeed, seen, markSeen, unseenFleet } from '../state.js';

on('fu-filter', (el) => { store.ui.fuFilter = el.dataset.f; store.rerender(); });
on('fu-seen', () => markSeen('fleet', unseenFleet(store.snap).map((i) => i.id)));

const KIND = { customer: ['chat', 'To customer'], handoff: ['route', 'Handoff'], ticket: ['ticket', 'Ticket'] };

export default {
  title: 'Fleet updates',
  meta(s) {
    const n = unseenFleet(s).length;
    return n ? `<button class="btn btn-sm" data-action="fu-seen">${icon('check')} Mark ${n} new as seen</button>` : 'all seen';
  },
  render(s) {
    const today = s.clock.date;
    const f = store.ui.fuFilter || 'all';
    const seenIds = seen('fleet');
    const all = fleetFeed(s);
    const items = f === 'all' ? all : all.filter((i) => i.kind === f);
    const filters = `<div class="filters"><div class="chips">
      ${[['all', 'All'], ['customer', 'To customers'], ['handoff', 'Handoffs'], ['ticket', 'Tickets']].map(([k, l]) =>
        `<button class="chip-btn${f === k ? ' is-on' : ''}" data-action="fu-filter" data-f="${k}">${l} <span class="muted">${k === 'all' ? all.length : all.filter((i) => i.kind === k).length}</span></button>`).join('')}
    </div></div>`;
    const list = items.map((i) => `<li class="fu-item fu-${i.kind}${seenIds.has(i.id) ? '' : ' is-unseen'}" data-key="fu-${F.esc(i.id)}">
        <span class="fu-icon">${icon(KIND[i.kind][0])}</span>
        <div class="fu-body"><div class="fu-meta"><span class="kind">${KIND[i.kind][1]}</span>${i.channel !== 'tickets' ? `<span class="muted">#${F.esc(i.channel)}</span>` : ''}<span class="muted">${F.time(i.at, today)}</span></div>
        <div class="fu-text">${F.linkifyNeeds(i.text)}</div></div>
      </li>`).join('');
    return [['filters', filters], ['list', list ? `<ol class="fu-list">${list}</ol>` : '<p class="empty">Fleet has not posted anything yet.</p>']];
  },
};
