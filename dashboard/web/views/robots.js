// Every robot Fleet reports, grouped by site. Click one to watch its camera.
import * as F from '../format.js';
import { store } from '../state.js';
import { currentFeed } from '../feeds.js';

export default {
  title: 'Robots',
  more: '/fleet',
  meta(s) {
    const all = s.warehouse.flatMap((w) => w.robots || []);
    const n = (st) => all.filter((r) => r.status === st).length;
    return `${n('ok')} ok · ${n('charging')} charging · ${n('blocked')} blocked${n('commissioning') ? ` · ${n('commissioning')} commissioning` : ''}`;
  },
  render(s) {
    const watching = currentFeed(s)?.robot_id;
    const sites = s.warehouse.filter((w) => w.robots?.length);
    if (!sites.length) return '<p class="empty">No robot telemetry yet.</p>';
    return `<div class="rb-sites">${sites.map((w) => `
      <div class="rb-site"><div class="rb-site-head"><strong>${F.esc(w.name)}</strong> ${F.status(w.health)}</div>
        <div class="rb-grid">${w.robots.map((r) => `
          <button class="rb st-${F.esc(r.status)}${r.id === watching ? ' is-watching' : ''}" data-action="watch" data-robot="${F.esc(r.id)}"
            data-key="rb2-${F.esc(r.id)}-${F.esc(r.status)}" title="${F.esc(`${r.id} · ${r.zone} · ${r.task} · ${r.battery}% · ${r.status}`)}">
            <span class="rb-id">${F.esc(r.id)}</span>
            <span class="rb-zone">${F.esc(r.zone)}</span>
            <span class="rb-batt"><span style="width:${r.battery}%"></span></span>
            <span class="rb-st">${F.esc(r.status)}</span>
          </button>`).join('')}</div></div>`).join('')}</div>`;
  },
};
