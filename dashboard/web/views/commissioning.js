import * as F from '../format.js';

// Chart geometry (SVG user units; the SVG scales to the panel width).
const W = 1100, H = 380, M = { l: 56, r: 170, t: 20, b: 44 };
const PW = W - M.l - M.r, PH = H - M.t - M.b;

function chart(c) {
  const maxN = Math.max(30, c.trials.length + 2);
  const x = (n) => M.l + (n / maxN) * PW;
  const y = (p) => M.t + (1 - p) * PH;
  const thr = c.gate_threshold ?? 0.95;
  const parts = [];
  // grid + axes
  for (const p of [0, 0.25, 0.5, 0.75, 1]) {
    parts.push(`<line class="grid" x1="${M.l}" x2="${M.l + PW}" y1="${y(p)}" y2="${y(p)}"/>`);
    parts.push(`<text class="tick" x="${M.l - 8}" y="${y(p) + 4}" text-anchor="end">${p.toFixed(2)}</text>`);
  }
  for (let n = 0; n <= maxN; n += 5) parts.push(`<text class="tick" x="${x(n)}" y="${M.t + PH + 18}" text-anchor="middle">${n}</text>`);
  parts.push(`<line class="axis" x1="${M.l}" x2="${M.l + PW}" y1="${y(0)}" y2="${y(0)}"/>`);
  parts.push(`<text class="axis-title" x="${M.l + PW / 2}" y="${H - 6}" text-anchor="middle">trial number</text>`);
  parts.push(`<text class="axis-title" transform="translate(14 ${M.t + PH / 2}) rotate(-90)" text-anchor="middle">P(rate ≥ ${F.pct(c.target_rate ?? 0.8)})</text>`);
  // threshold
  parts.push(`<line class="threshold" x1="${M.l}" x2="${M.l + PW}" y1="${y(thr)}" y2="${y(thr)}"/>`);
  parts.push(`<text class="threshold-label" x="${M.l + 6}" y="${y(thr) - 6}">${Math.round(thr * 100)}% gate</text>`);
  // gate marker (label low on the line, clear of the series labels at the top right)
  if (c.gate_trial) {
    parts.push(`<line class="gate-line" x1="${x(c.gate_trial)}" x2="${x(c.gate_trial)}" y1="${M.t}" y2="${M.t + PH}"/>`);
    parts.push(`<text class="gate-label" x="${x(c.gate_trial) - 8}" y="${y(0.18)}" text-anchor="end">gate open · trial ${c.gate_trial}</text>`);
  }
  // series (fixed order = policy order from the cell service)
  c.policies.forEach((p, i) => {
    const pts = c.trials.filter((t) => t.policy === p.id);
    if (!pts.length) return;
    const cls = `series series-${i + 1} policy-${F.esc(p.status)}`;
    const d = pts.map((t, k) => `${k ? 'L' : 'M'}${x(t.n).toFixed(1)},${y(t.confidence).toFixed(1)}`).join(' ');
    parts.push(`<g class="${cls}"><path class="series-line" d="${d}"/>`);
    for (const t of pts) {
      parts.push(`<circle class="pt ${t.success ? 'pt-ok' : 'pt-fail'}" cx="${x(t.n).toFixed(1)}" cy="${y(t.confidence).toFixed(1)}" r="5">
        <title>${F.esc(p.name)} · trial ${t.n} · ${t.success ? 'success' : 'failure'} · P = ${F.conf(t.confidence)}</title></circle>`);
    }
    const last = pts[pts.length - 1];
    parts.push(`<text class="series-label" x="${x(last.n) + 10}" y="${y(last.confidence) + 4}">${F.esc(p.name)}${p.status === 'dropped' ? ' · dropped' : p.status === 'passed' ? ' · passed' : ''}</text></g>`);
  });
  return `<svg class="chart com-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Confidence over trial number per policy">${parts.join('')}</svg>`;
}

export default {
  title: 'Commissioning',
  meta(s) {
    const c = s.commissioning;
    return c ? `${F.esc(c.site)} · <span class="mono">${F.esc(c.skill)}</span> · ${F.needLink(c.need_id)}` : '';
  },
  render(s) {
    const c = s.commissioning;
    const today = s.clock.date;
    if (!c) {
      const n = s.needs.find((x) => x.kind === 'skill_unproven' && x.status !== 'closed');
      return `<div class="gate-banner gate-idle">No commissioning run yet${n ? ` · ${F.needLink(n.need_id)} ${F.status(n.status)}` : ''}</div>`;
    }
    const best = [...c.policies].filter((p) => p.confidence != null).sort((a, b) => b.confidence - a.confidence)[0];
    const banner = c.status === 'gate_open'
      ? `<div class="gate-banner gate-open" data-key="gate-open">GATE OPEN <span class="gate-sub">${F.esc(c.winner || '')} · trial ${c.gate_trial} · ${F.time(c.gate_opened_at, today)}</span></div>`
      : c.status === 'running'
        ? `<div class="gate-banner gate-closed">Gate closed · running · trial ${c.trials.length}${best ? ` · best P = ${F.conf(best.confidence)}` : ''}</div>`
        : `<div class="gate-banner gate-idle">Scheduled · starts ${F.time(c.started_at, today)}</div>`;
    const maxTrials = Math.max(1, ...c.policies.map((p) => p.trials));
    const alloc = `<table class="table alloc"><thead><tr><th>Policy</th><th>Trials allocated</th><th>Successes</th><th>P(rate ≥ ${F.pct(c.target_rate ?? 0.8)})</th><th>Status</th></tr></thead><tbody>
      ${c.policies.map((p, i) => `<tr data-key="al-${F.esc(p.id)}-${F.esc(p.status)}">
        <td><span class="swatch series-${i + 1}"></span><span class="mono">${F.esc(p.name)}</span></td>
        <td><div class="bar"><div class="bar-fill series-${i + 1}" style="width:${((p.trials / maxTrials) * 100).toFixed(1)}%"></div><span class="num">${p.trials}</span></div></td>
        <td class="num">${p.successes} / ${p.trials}</td>
        <td class="num">${F.conf(p.confidence)}</td>
        <td>${F.status(p.status)}</td>
      </tr>`).join('')}</tbody></table>`;
    return [['banner', banner], ['chart', `<div class="chart-wrap">${chart(c)}</div>`], ['alloc', alloc]];
  },
};
