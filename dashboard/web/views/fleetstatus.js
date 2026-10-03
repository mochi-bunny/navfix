// Fleet status at a glance: alerts first, then one row per site.
import * as F from '../format.js';
import { icon } from '../icons.js';
import { spark } from './warehouse.js';

export default {
  title: 'Fleet status',
  more: '/fleet',
  meta(s) {
    const robots = s.warehouse.flatMap((w) => w.robots || []);
    const active = s.warehouse.reduce((a, w) => a + (w.robots_active || 0), 0);
    const total = s.warehouse.reduce((a, w) => a + (w.robots_total || 0), 0);
    return `${active}/${total || robots.length} robots active`;
  },
  render(s) {
    const today = s.clock.date;
    const open = s.needs.filter((n) => n.status !== 'closed');
    const blocked = s.warehouse.flatMap((w) => (w.robots || []).filter((r) => r.status === 'blocked').map((r) => ({ ...r, site: w.site, siteName: w.name })));
    const alerts = [
      ...open.map((n) => `<a class="alert sev-row-${F.esc((n.severity || '').toLowerCase())}" href="/needs/${F.esc(n.need_id)}" data-nav data-key="al-${F.esc(n.need_id)}-${F.esc(n.status)}">
        ${F.sev(n.severity)}<span class="alert-text"><strong>${F.esc(n.need_id)}</strong> ${F.esc(n.title || n.kind)}</span>${F.status(n.status)}</a>`),
      ...blocked.map((r) => `<button class="alert" data-action="watch" data-robot="${F.esc(r.id)}" data-key="ab-${F.esc(r.id)}">
        <span class="sev sev-p2">ROBOT</span><span class="alert-text"><strong>${F.esc(r.id)}</strong> blocked on ${F.esc(r.task)} · ${F.esc(r.siteName)}</span><span class="watch">${icon('camera')} watch</span></button>`),
    ];
    const sites = s.warehouse.map((w) => `<div class="fs-site health-${F.esc(w.health)}" data-key="fs-${F.esc(w.site)}-${F.esc(w.health)}">
        <div class="fs-top"><strong>${F.esc(w.name)}</strong><span class="muted small">${F.esc(w.area || '')}</span>${F.status(w.health)}
          <button class="btn btn-sm fs-watch" data-action="watch" data-site="${F.esc(w.site)}">${icon('camera')} Watch</button></div>
        <div class="fs-stats">
          <div><span class="fs-v">${w.robots_active}/${w.robots_total}</span><span class="fs-l">robots</span></div>
          <div><span class="fs-v">${w.throughput?.current ?? '—'}</span><span class="fs-l">${F.esc(w.throughput?.unit || '')}</span></div>
          <div><span class="fs-v">${w.backlog?.count ?? '—'}</span><span class="fs-l">backlog ${F.needLink(w.backlog?.need_id)}</span></div>
          <div class="fs-spark">${spark(w.throughput?.series)}</div>
        </div>
        ${w.health_note ? `<div class="fs-note">${F.linkifyNeeds(w.health_note)}</div>` : ''}
        ${(w.consumables || []).filter((c) => c.need_id).map((c) => `<div class="fs-note">${icon('box')} ${F.esc(c.item)} run out ${F.time(c.runout, today)} ${F.needLink(c.need_id)}</div>`).join('')}
      </div>`).join('');
    return [['alerts', `<h3 class="sub first">Alerts</h3>${alerts.length ? `<div class="alerts">${alerts.join('')}</div>` : '<p class="empty">All clear.</p>'}`],
      ['sites', `<h3 class="sub">Sites</h3><div class="fs-sites">${sites}</div>`]];
  },
};
