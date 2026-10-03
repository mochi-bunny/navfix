import * as F from '../format.js';

const up = (ok) => `<span class="dot ${ok ? 'dot-up' : 'dot-down'}"></span>${ok ? 'up' : 'down'}`;

export default {
  title: 'Local proof',
  meta: (s) => (s.any_mock ? '<span class="badge badge-mock">some sources are MOCK</span>' : 'all sources live'),
  render(s) {
    const sys = s.system || {};
    const g = sys.gpu || {};
    const used = g.used_gb ?? null;
    const calls = sys.cloud_model_calls;
    const tiles = `<div class="tiles">
      <div class="tile"><div class="tile-label">vLLM</div><div class="tile-value">${up(sys.vllm?.up)}</div><div class="tile-sub mono">${F.esc(sys.vllm?.model || '—')}</div></div>
      <div class="tile"><div class="tile-label">GPU memory</div><div class="tile-value num">${used ?? '—'} <span class="tile-unit">/ ${g.total_gb ?? '—'} GB</span></div>
        <div class="meter"><div class="meter-fill" style="width:${used != null && g.total_gb ? ((used / g.total_gb) * 100).toFixed(1) : 0}%"></div></div>
        <div class="tile-sub muted">${F.esc(g.source || '')}</div></div>
      <div class="tile tile-calls"><div class="tile-label">Cloud model calls</div><div class="tile-value hero num">${calls == null ? '—' : calls}</div>
        <div class="tile-sub muted">${calls == null ? 'egress counter not wired' : F.esc(sys.egress_source || '')}</div></div>
      <div class="tile"><div class="tile-label">Dashboard LLM calls</div><div class="tile-value num">${sys.dashboard_llm_calls ?? 0}</div><div class="tile-sub muted">the dashboard only reads data</div></div>
    </div>`;
    const services = `<h3 class="sub">Local services</h3><table class="table"><thead><tr><th>Service</th><th>Port</th><th>Status</th></tr></thead><tbody>
      ${(sys.services || []).map((x) => `<tr data-key="svc-${F.esc(x.name)}-${x.up}"><td>${F.esc(x.name)}</td><td class="num">${x.port ?? ''}</td><td>${up(x.up)}</td></tr>`).join('')}
    </tbody></table>`;
    const sources = `<h3 class="sub">Dashboard data sources</h3><table class="table"><thead><tr><th>Source</th><th>Mode</th><th>Status</th><th>Detail</th></tr></thead><tbody>
      ${Object.entries(sys.sources || {}).map(([k, v]) => `<tr><td>${F.esc(k)}</td><td>${v.mode === 'mock' ? '<span class="badge badge-mock">mock</span>' : 'live'}</td>
        <td>${up(v.ok)}</td><td class="muted small">${F.esc(v.error || (v.age_s != null ? `read ${v.age_s}s ago` : ''))}</td></tr>`).join('')}
    </tbody></table>`;
    return [['tiles', tiles], ['services', services], ['sources', sources]];
  },
};
