import * as F from '../format.js';

const EVENT_LABEL = {
  'need.opened': 'Need opened', 'visit.proposed': 'Slot proposed', 'visit.booked': 'Visit booked',
  'eta.updated': 'ETA updated', 'visit.arrived': 'Arrived', 'commissioning.started': 'Commissioning started',
  'visit.completed': 'Visit completed', 'order.shipped': 'Order shipped',
};

function pickNeed(s, id) {
  if (id) return s.needs.find((n) => n.need_id === id);
  // Default focus: newest P1 that's still open, else the newest Need.
  const byNewest = [...s.needs].sort((a, b) => String(b.opened_at).localeCompare(String(a.opened_at)));
  return byNewest.find((n) => n.status !== 'closed' && n.severity === 'P1') || byNewest[0];
}

function dataSummary(d) {
  if (!d || typeof d !== 'object') return '';
  return Object.entries(d).filter(([, v]) => v != null && typeof v !== 'object')
    .map(([k, v]) => `<span class="kv"><span class="k">${F.esc(k.replace(/_/g, ' '))}</span> ${F.esc(v)}</span>`).join(' ');
}

export default {
  title: 'Need detail',
  meta(s, ctx) {
    const n = pickNeed(s, ctx.params.id);
    return n ? `${F.esc(n.need_id)} · ${F.esc(n.site || '')}` : '';
  },
  render(s, ctx) {
    const today = s.clock.date;
    const n = pickNeed(s, ctx.params.id);
    const tabs = `<nav class="tabs">${s.needs.map((x) => `<a href="/needs/${F.esc(x.need_id)}" data-nav class="tab${x === n ? ' is-active' : ''}">${F.esc(x.need_id)} ${F.sev(x.severity)}</a>`).join('')}</nav>`;
    if (!n) return [['tabs', tabs], ['head', `<p class="empty">${ctx.params.id ? `Need ${F.esc(ctx.params.id)} not found (yet).` : 'No Needs yet.'}</p>`]];

    const head = `<div class="need-head" data-key="nh-${F.esc(n.need_id)}-${F.esc(n.status)}">
      <div class="need-head-title"><span class="need-id">${F.esc(n.need_id)}</span> ${F.sev(n.severity)} ${F.status(n.status)}<h3>${F.esc(n.title || n.kind || '')}</h3></div>
      <dl class="facts">
        <div><dt>Source</dt><dd class="mono">${F.esc(n.source || '—')}</dd></div>
        <div><dt>Kind</dt><dd class="mono">${F.esc(n.kind || '—')}</dd></div>
        <div><dt>Site</dt><dd>${F.esc(n.site || '—')}</dd></div>
        <div><dt>Skill</dt><dd class="mono">${F.esc(n.skill_req || '—')}</dd></div>
        <div><dt>Deadline</dt><dd>${F.time(n.deadline, today)}</dd></div>
        <div><dt>Duration</dt><dd>${F.dur(n.duration_min)}</dd></div>
        <div><dt>Assigned</dt><dd>${F.esc(n.assigned_name || '—')}</dd></div>
        <div><dt>Booked slot</dt><dd>${n.booked_slot ? F.range(n.booked_slot.start, n.booked_slot.end, today) : '—'}</dd></div>
        <div class="wide"><dt>Bring</dt><dd>${(n.bring || []).map((b) => `<span class="chip">${F.esc(b)}</span>`).join(' ') || '—'}</dd></div>
      </dl>
    </div>`;

    // Clip players live in their own part so polling never restarts playback.
    const clips = n.evidence?.clip_urls || [];
    const clipHtml = `<h3 class="sub">Failure clips</h3>` + (clips.length
      ? `<div class="clips">${clips.map((c) => `<figure class="clip">
          <video src="${F.esc(c.url)}" controls muted loop playsinline preload="metadata"
            onerror="this.closest('.clip').classList.add('clip-missing')"></video>
          <figcaption class="mono">${F.esc(c.name)}</figcaption>
          <div class="clip-missing-note">Clip not found on the box: ${F.esc(c.name)}</div>
        </figure>`).join('')}</div>`
      : '<p class="empty">No clips attached.</p>');

    const ev = n.evidence || {};
    const rel = s.reliability.find((r) => r.site === n.site && r.skill === n.skill_req);
    const stats = (ev.trials_run != null || rel) ? `<h3 class="sub">Trial stats</h3><dl class="facts">
        ${ev.trials_run != null ? `<div><dt>At ticket time</dt><dd class="num">${ev.successes} / ${ev.trials_run} trials${ev.policy ? ` · <span class="mono">${F.esc(ev.policy)}</span>` : ''}</dd></div>` : ''}
        ${ev.orders_blocked != null ? `<div><dt>Orders blocked</dt><dd class="num">${ev.orders_blocked}</dd></div>` : ''}
        ${rel ? `<div><dt>Now (cell service)</dt><dd class="num">${rel.successes} / ${rel.trials} · <span class="mono">${F.esc(rel.policy || '')}</span></dd></div>
        <div><dt>P(rate ≥ ${F.pct(rel.target_rate ?? 0.8)})</dt><dd class="num">${F.conf(rel.confidence)}</dd></div>
        <div><dt>Gate</dt><dd>${F.status('gate_' + rel.gate)}</dd></div>` : ''}
      </dl>` : (ev.consumable ? `<h3 class="sub">Evidence</h3><dl class="facts"><div><dt>Consumable</dt><dd>${F.esc(ev.consumable)}</dd></div><div><dt>Run-out</dt><dd>${F.time(ev.runout, today)}</dd></div></dl>` : '');

    const linked = s.events.filter((e) => e.need_id === n.need_id);
    const cal = `<h3 class="sub">Calendar block</h3>` + (linked.length ? linked.map((e) => {
      const st = s.staff.find((x) => x.id === e.staff_id);
      const l = e.leg;
      return `<div class="linked-event" data-key="le-${F.esc(e.id)}">
        <div class="linked-time">${F.range(e.start, e.end, today)}</div>
        <div><strong>${F.esc(e.title)}</strong> · ${F.esc(st?.name || '')}<br><span class="muted">${F.esc(e.place || '')}</span></div>
        ${l ? `<div class="muted">${F.modeIcon(l.mode)} ${l.minutes} min · leave ${F.time(l.leave_by, today)}</div>` : ''}
      </div>`;
    }).join('') : '<p class="empty">Not booked yet.</p>')
      + (n.alternatives?.length ? `<h4 class="sub2">Alternatives Field considered</h4><ul class="alts">${n.alternatives.map((a) =>
        `<li><span class="num">${F.esc(a.start)}–${F.esc(a.end)}</span> · +${a.extra_travel_min} travel min · ${F.esc(a.note || '')}</li>`).join('')}</ul>` : '');

    const tl = s.visit_events.filter((v) => v.need_id === n.need_id);
    const timeline = `<h3 class="sub">Timeline</h3>` + (tl.length ? `<ol class="timeline">${tl.map((v) => `
      <li class="tl-item tl-${F.esc(v.type.replace(/\./g, '-'))}" data-key="tl-${F.esc(v.id)}">
        <span class="tl-time num">${F.time(v.at, today)}</span>
        <span class="tl-type">${F.esc(EVENT_LABEL[v.type] || v.type)}</span>
        <span class="tl-by muted">${F.esc(v.by || '')}</span>
        <span class="tl-data">${dataSummary(v.data)}</span>
      </li>`).join('')}</ol>` : '<p class="empty">No events yet.</p>');

    return [['tabs', tabs], ['head', head], ['clips', clipHtml], ['stats', stats], ['calendar', cal], ['timeline', timeline]];
  },
};
