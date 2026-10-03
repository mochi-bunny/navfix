import * as F from '../format.js';

const PHASE = { in_transit: 'In transit', next: 'Next trip', done: 'No more trips today' };

function card(s, st, l, today, big) {
  if (!l || l.phase === 'done') {
    return `<div class="leg-card${big ? ' is-big' : ''}"><div class="leg-who">${F.esc(st.name)}</div><div class="leg-phase">${PHASE.done}</div></div>`;
  }
  const late = l.late_by > 0;
  return `<div class="leg-card${big ? ' is-big' : ''} leg-${F.esc(l.status)}" data-key="lc-${F.esc(st.id)}-${F.esc(l.event_id)}-${F.esc(l.status)}-${l.delay_min}">
    <div class="leg-who">${F.esc(st.name)} · <span class="leg-phase">${PHASE[l.phase]}</span></div>
    <div class="leg-route"><span class="leg-from">${F.esc(l.origin || '?')}</span> <span class="arrow">→</span> <span class="leg-to">${F.esc(l.destination || '')}</span></div>
    <div class="leg-for muted">for ${F.esc(l.event_title || '')} at ${F.time(l.event_start, today)}</div>
    <dl class="leg-facts">
      <div><dt>Mode</dt><dd>${F.modeIcon(l.mode)} ${F.esc(l.route || l.mode)} · ${l.minutes} min</dd></div>
      <div><dt>Leave by</dt><dd class="num">${F.time(l.leave_by, today)} <span class="muted">${l.phase === 'next' ? F.relMin(l.leave_in_min) : ''}</span></dd></div>
      <div class="${l.delay_min ? 'has-delay' : ''}"><dt>MBTA delay</dt><dd class="num">${l.delay_min ? `+${l.delay_min} min` : 'none'}${l.alert ? `<div class="leg-alert">${F.esc(l.alert)}</div>` : ''}</dd></div>
      <div class="${late ? 'is-late' : ''}"><dt>ETA</dt><dd class="num">${F.time(l.eta, today)} <span class="leg-ontime">${late ? `${l.late_by} min late` : 'on time'}</span></dd></div>
    </dl>
    ${l.note ? `<div class="leg-note">${F.esc(l.note)}</div>` : ''}
    ${l.last_checked ? `<div class="muted small">Last checked ${F.time(l.last_checked, today)}</div>` : ''}
  </div>`;
}

export default {
  title: 'Live leg',
  render(s) {
    const today = s.clock.date;
    const staff = [...s.staff].sort((a, b) => b.spotlight - a.spotlight);
    if (!staff.length) return '<p class="empty">No staff.</p>';
    const [spot, ...rest] = staff;
    return `<div class="legs">${card(s, spot, s.current_legs[spot.id], today, true)}
      <div class="legs-others">${rest.map((st) => card(s, st, s.current_legs[st.id], today, false)).join('')}</div></div>`;
  },
};
