import * as F from '../format.js';

// Confidence values come from the cell service; this view only displays them.
export default {
  title: 'Reliability',
  meta(s) {
    const r = s.reliability[0];
    return r ? `Gate opens at P(rate ≥ ${F.pct(r.target_rate ?? 0.8)}) ≥ ${r.threshold ?? 0.95}` : '';
  },
  render(s) {
    const today = s.clock.date;
    if (!s.reliability.length) return '<p class="empty">No reliability data.</p>';
    const rows = s.reliability.map((r) => {
      const rate = r.trials ? r.successes / r.trials : null;
      const c = r.confidence ?? 0;
      return `<tr class="gate-row gate-${F.esc(r.gate)}" data-key="rel-${F.esc(r.site)}-${F.esc(r.skill)}-${F.esc(r.gate)}-${r.trials}">
        <td>${F.esc(r.site)}</td>
        <td class="mono">${F.esc(r.skill)}</td>
        <td class="mono">${F.esc(r.policy || '—')}</td>
        <td class="num">${r.trials}</td>
        <td class="num">${r.successes}</td>
        <td class="num">${F.pct(rate)}</td>
        <td class="num conf-cell">
          <div class="meter" title="P = ${F.conf(r.confidence)}"><div class="meter-fill" style="width:${(c * 100).toFixed(1)}%"></div><div class="meter-mark" style="left:${((r.threshold ?? 0.95) * 100).toFixed(1)}%"></div></div>
          <span>${F.conf(r.confidence)}</span>
        </td>
        <td>${F.status('gate_' + r.gate)}</td>
        <td class="num">${F.time(r.last_tested, today)}</td>
        <td>${r.need_id ? F.needLink(r.need_id) : ''} <span class="muted">${F.esc(r.note || '')}</span></td>
      </tr>`;
    }).join('');
    return `<table class="table rel-table"><thead><tr>
      <th>Site</th><th>Skill</th><th>Policy</th><th>Trials</th><th>Successes</th><th>Rate</th><th>P(rate ≥ ${F.pct(s.reliability[0].target_rate ?? 0.8)})</th><th>Gate</th><th>Last tested</th><th>Need</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
  },
};
