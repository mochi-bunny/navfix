import * as F from '../format.js';

export default {
  title: 'Ops log',
  more: '/ops',
  meta: (s) => `${s.ops_log.length} handoffs`,
  render(s) {
    const today = s.clock.date;
    if (!s.ops_log.length) return '<p class="empty">No handoffs yet.</p>';
    return `<ol class="opslog" data-autoscroll>${s.ops_log.map((m) => `
      <li class="op op-${F.esc(String(m.author).toLowerCase())}" data-key="op-${F.esc(m.id)}">
        <span class="op-time num">${F.time(m.at, today)}</span>
        <span class="op-agent">${F.esc(m.author)}</span>
        <span class="op-text">${F.linkifyNeeds(m.text)}</span>
      </li>`).join('')}</ol>`;
  },
};
