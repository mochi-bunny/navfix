// Skill gates: the live commissioning run (if any) and every site × skill gate.
import * as F from '../format.js';
import { chart } from './commissioning.js';

export default {
  title: 'Skill gates',
  more: '/commissioning',
  meta(s) {
    return `${s.reliability.filter((r) => r.gate === 'open').length}/${s.reliability.length} open`;
  },
  render(s) {
    const c = s.commissioning;
    const today = s.clock.date;
    let com = '';
    if (c) {
      const best = [...c.policies].filter((p) => p.confidence != null).sort((a, b) => b.confidence - a.confidence)[0];
      const banner = c.status === 'gate_open'
        ? `<div class="gate-mini gate-open" data-key="gm-open">✓ Gate open · ${F.esc(c.winner || '')} · trial ${c.gate_trial} · ${F.time(c.gate_opened_at, today)}</div>`
        : c.status === 'running'
          ? `<div class="gate-mini gate-closed">Commissioning ${F.esc(c.site)} · ${F.esc(c.skill)} · trial ${c.trials.length}${best ? ` · best P ${F.conf(best.confidence)}` : ''}</div>`
          : `<div class="gate-mini">Commissioning scheduled ${F.time(c.started_at, today)} · ${F.needLink(c.need_id)}</div>`;
      com = banner + (c.trials.length ? `<div class="gate-chart">${chart(c)}</div>` : '');
    }
    const rows = s.reliability.map((r) => `<div class="gate-row" data-key="gr-${F.esc(r.site)}-${F.esc(r.skill)}-${F.esc(r.gate)}">
        <span class="gate-dot gate-${F.esc(r.gate)}"></span>
        <span class="gate-name">${F.esc(r.site)} · <span class="mono">${F.esc(r.skill)}</span></span>
        <span class="num muted">${r.successes}/${r.trials}</span>
        <span class="num">P ${F.conf(r.confidence)}</span>
        ${r.need_id ? F.needLink(r.need_id) : '<span></span>'}
      </div>`).join('');
    return [['com', com], ['rows', `<div class="gate-rows">${rows}</div>`]];
  },
};
