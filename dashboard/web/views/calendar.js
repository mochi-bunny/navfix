import * as F from '../format.js';

// Visible window of the day, in minutes after midnight.
const START = 8 * 60;
const END = 21.5 * 60;
const pct = (min) => Math.max(0, Math.min(100, ((min - START) / (END - START)) * 100));
const box = (a, b) => `left:${pct(a).toFixed(3)}%;width:${Math.max(0, pct(b) - pct(a)).toFixed(3)}%`;

function lane(s, st, today) {
  const events = s.events.filter((e) => e.staff_id === st.id);
  const legs = s.legs.filter((l) => l.staff_id === st.id);
  const ev = events.map((e) => {
    const need = e.need_id ? `<a class="need-badge" href="/needs/${F.esc(e.need_id)}" data-nav>${F.esc(e.need_id)}</a>` : '';
    return `<div class="cal-event type-${F.esc(e.type)}${e.need_id ? ' has-need' : ''}" data-key="ev-${F.esc(e.id)}-${F.esc(e.need_id || '')}"
      style="${box(F.minOfDay(e.start), F.minOfDay(e.end))}" title="${F.esc(`${e.title}\n${F.range(e.start, e.end, today)}\n${e.place || ''}`)}">
      <div class="cal-event-time">${F.shortRange(e.start, e.end)}${need}</div>
      <div class="cal-event-title">${F.esc(e.title)}</div>
      <div class="cal-event-place">${F.esc(e.place || '')}</div>
    </div>`;
  }).join('');
  const lg = legs.map((l) => {
    const a = F.minOfDay(l.leave_by), b = F.minOfDay(l.eta);
    const late = l.delay_min > 0 || l.late_by > 0;
    const tip = `${l.origin || '?'} → ${l.destination}\n${l.route || l.mode} · ${l.minutes} min${l.delay_min ? ` +${l.delay_min} delay` : ''}\nLeave ${F.time(l.leave_by, today)} · ETA ${F.time(l.eta, today)}`;
    return `<div class="cal-leg mode-${F.esc(l.mode)}${late ? ' is-late' : ''}" data-key="leg-${F.esc(l.event_id)}-${F.esc(l.leave_by)}-${l.delay_min}"
      style="${box(a, Math.max(b, a + 1))}" title="${F.esc(tip)}">
      <span class="cal-leg-label">${F.modeIcon(l.mode)} ${l.minutes}m${l.delay_min ? ` <b>+${l.delay_min}</b>` : ''}</span>
    </div>`;
  }).join('');
  return `<div class="cal-lane${st.spotlight ? ' is-spotlight' : ''}">
    <div class="cal-lane-head"><div class="cal-staff-name">${F.esc(st.name)}</div><div class="cal-staff-role">${F.esc(st.role || '')}</div></div>
    <div class="cal-track"><div class="cal-events">${ev}</div><div class="cal-legs">${lg}</div></div>
  </div>`;
}

function agenda(s, st, today) {
  const rows = s.events.filter((e) => e.staff_id === st.id).map((e) => {
    const l = e.leg;
    const travel = l ? `${F.modeIcon(l.mode)} ${l.minutes} min${l.delay_min ? ` <span class="delay">+${l.delay_min}</span>` : ''}` : '—';
    return `<tr class="${e.need_id ? 'has-need' : ''}" data-key="ag-${F.esc(e.id)}-${F.esc(e.need_id || '')}-${F.esc(l?.leave_by || '')}">
      <td class="num">${F.range(e.start, e.end, today)}</td>
      <td>${F.esc(e.title)} ${e.need_id ? F.needLink(e.need_id) : ''}</td>
      <td>${F.esc(e.place || '')}</td>
      <td>${travel}</td>
      <td class="num">${l ? F.time(l.leave_by, today) : '—'}</td>
      <td class="num">${l ? F.time(l.eta, today) : '—'}${l?.late_by ? ` <span class="delay">${l.late_by} min late</span>` : ''}</td>
    </tr>`;
  }).join('');
  return `<table class="table agenda"><thead><tr><th>Time</th><th>Event</th><th>Place</th><th>Travel</th><th>Leave by</th><th>ETA</th></tr></thead><tbody>${rows}</tbody></table>`;
}

export default {
  title: 'Day calendar',
  meta(s) {
    const st = s.staff.find((x) => x.spotlight);
    const mine = s.events.filter((e) => e.staff_id === st?.id);
    const counts = {};
    mine.forEach((e) => (counts[e.type] = (counts[e.type] || 0) + 1));
    return `${F.esc(st?.name || '')}: ${Object.entries(counts).map(([k, v]) => `${v} ${F.esc(k)}`).join(' · ')}`;
  },
  render(s, ctx) {
    const today = s.clock.date;
    const staff = [...s.staff].sort((a, b) => b.spotlight - a.spotlight);
    const hours = [];
    for (let h = Math.ceil(START / 60); h <= Math.floor(END / 60); h++) {
      hours.push(`<div class="cal-hour" style="left:${pct(h * 60).toFixed(3)}%"><span>${h % 12 || 12}${h < 12 ? 'a' : 'p'}</span></div>`);
    }
    const nowMin = ctx.now && ctx.now.date === today ? ctx.now.minOfDay : null;
    const nowLine = nowMin != null && nowMin >= START && nowMin <= END
      ? `<div class="cal-now" style="left:${pct(nowMin).toFixed(3)}%"></div>` : '';
    const grid = `<div class="cal">
      <div class="cal-axis"><div class="cal-lane-head"></div><div class="cal-track">${hours.join('')}</div></div>
      <div class="cal-body">${staff.map((st) => lane(s, st, today)).join('')}
        <div class="cal-overlay"><div class="cal-lane-head"></div><div class="cal-track">${hours.map((h) => h.replace('cal-hour', 'cal-gridline')).join('')}${nowLine}</div></div>
      </div>
    </div>`;
    const spot = staff.find((x) => x.spotlight);
    return [['grid', grid], ['agenda', spot ? `<h3 class="sub">${F.esc(spot.name)}: agenda</h3>${agenda(s, spot, today)}` : '']];
  },
};
