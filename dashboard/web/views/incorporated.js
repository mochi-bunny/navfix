// Fleet watch header: what Fleet is surveying, and which of its signals are now in the plan.
import * as F from '../format.js';
import { icon } from '../icons.js';

export default {
  title: 'What Fleet is watching',
  render(s) {
    const today = s.clock.date;
    const robots = s.warehouse.flatMap((w) => w.robots || []);
    const gatesOpen = s.reliability.filter((r) => r.gate === 'open').length;
    const stats = [
      ['box', s.warehouse.length, 'sites'],
      ['robot', robots.length || s.warehouse.reduce((a, w) => a + (w.robots_total || 0), 0), 'robots'],
      ['shield', `${gatesOpen}/${s.reliability.length}`, 'skill gates open'],
      ['ticket', s.needs.filter((n) => n.status !== 'closed').length, 'open Needs'],
      ['radar', s.commissioning ? s.commissioning.status.replace('_', ' ') : 'idle', 'commissioning'],
    ];
    const tiles = `<div class="stat-row">${stats.map(([ic, v, l]) => `<div class="stat">${icon(ic)}<div><div class="stat-v">${F.esc(v)}</div><div class="stat-l">${l}</div></div></div>`).join('')}</div>`;
    const fleetNeeds = s.needs.filter((n) => String(n.source || '').startsWith('fleet'));
    const rows = fleetNeeds.map((n) => {
      const evs = s.events.filter((e) => e.need_id === n.need_id);
      const effect = evs.length ? evs.map((e) => {
        const st = s.staff.find((x) => x.id === e.staff_id);
        return `<span class="inc-effect">${icon('calendar')} ${F.esc(e.title)} · ${F.range(e.start, e.end, today)} · ${F.esc(st?.name || '')}</span>`;
      }).join('') : '<span class="muted">not in anyone\'s plan yet</span>';
      return `<li class="inc-row" data-key="inc-${F.esc(n.need_id)}-${F.esc(n.status)}-${evs.length}">
        <div class="inc-sig">${F.sev(n.severity)} ${F.needLink(n.need_id)} <span class="mono small">${F.esc(n.source)}</span><div>${F.esc(n.title || n.kind)}</div></div>
        <div class="inc-arrow">${icon('chevron')}</div>
        <div class="inc-plan">${effect}<div>${F.status(n.status)}</div></div>
      </li>`;
    }).join('');
    return [['stats', tiles], ['rows', `<h3 class="sub">Fleet signals incorporated into the plan</h3>${rows ? `<ul class="inc-list">${rows}</ul>` : '<p class="empty">No Fleet signals yet.</p>'}`]];
  },
};
