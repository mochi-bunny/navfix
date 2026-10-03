import * as F from '../format.js';

export function spark(series) {
  if (!series?.length) return '';
  const W = 320, H = 64, P = 4;
  const vs = series.map((p) => p.v);
  const lo = Math.min(...vs) * 0.9, hi = Math.max(...vs) * 1.05;
  const x = (i) => P + (i / Math.max(1, series.length - 1)) * (W - 2 * P);
  const y = (v) => H - P - ((v - lo) / (hi - lo || 1)) * (H - 2 * P);
  const d = series.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ');
  const last = series[series.length - 1];
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Throughput trend">
    <path class="spark-line" d="${d}"/><circle class="spark-dot" cx="${x(series.length - 1)}" cy="${y(last.v)}" r="3"><title>${last.v}</title></circle></svg>`;
}

export default {
  title: 'Warehouse status',
  render(s, ctx) {
    const today = s.clock.date;
    if (!s.warehouse.length) return '<p class="empty">No warehouse telemetry.</p>';
    return `<div class="sites">${s.warehouse.map((w) => `
      <article class="site health-${F.esc(w.health)}" data-key="site-${F.esc(w.site)}-${F.esc(w.health)}">
        <header class="site-head">
          <h3>${F.esc(w.name)} <span class="muted">${F.esc(w.area || '')}</span></h3>
          ${F.status(w.health)}
        </header>
        ${w.health_note ? `<p class="site-note">${F.linkifyNeeds(w.health_note)} ${w.need_id && !String(w.health_note).includes(w.need_id) ? F.needLink(w.need_id) : ''}</p>` : ''}
        <dl class="facts">
          <div><dt>Robots active</dt><dd class="num">${w.robots_active} / ${w.robots_total}</dd></div>
          <div><dt>Throughput</dt><dd class="num">${w.throughput?.current ?? '—'} ${F.esc(w.throughput?.unit || '')}</dd></div>
          <div data-key="bl-${F.esc(w.site)}-${F.esc(w.backlog?.need_id || '')}"><dt>Order backlog</dt><dd class="num">${w.backlog?.count ?? '—'} ${w.backlog?.note ? `<span class="muted">${F.esc(w.backlog.note)}</span>` : ''} ${F.needLink(w.backlog?.need_id)}</dd></div>
        </dl>
        <div class="spark-wrap">${spark(w.throughput?.series)}</div>
        ${w.robots?.length ? `<h4 class="sub2">Robots</h4><div class="robots">${w.robots.map((r) => `
          <button class="robot r-${F.esc(r.status)}" data-action="watch" data-robot="${F.esc(r.id)}" data-key="rb-${F.esc(r.id)}-${F.esc(r.status)}" title="${F.esc(`Watch ${r.id} · ${r.zone} · ${r.task} · battery ${r.battery}% · ${r.status}`)}">
            <span class="robot-id">${F.esc(r.id)}</span><span class="robot-batt"><span style="width:${r.battery}%"></span></span><span class="robot-st">${F.esc(r.status)}</span>
          </button>`).join('')}</div>` : ''}
        <h4 class="sub2">Consumables run-out</h4>
        <ul class="consumables">${(w.consumables || []).map((c) => `
          <li data-key="cons-${F.esc(w.site)}-${F.esc(c.item)}-${F.esc(c.runout)}"><span>${F.esc(c.item)}</span>
          <span class="num">${F.time(c.runout, today)}</span> ${F.needLink(c.need_id)}</li>`).join('')}</ul>
      </article>`).join('')}</div>`;
  },
};
