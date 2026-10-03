import * as F from '../format.js';

export default {
  title: 'Needs',
  meta(s) {
    const open = s.needs.filter((n) => n.status !== 'closed').length;
    return `${open} open · ${s.needs.length} total`;
  },
  render(s) {
    const today = s.clock.date;
    if (!s.needs.length) return '<p class="empty">No Needs yet.</p>';
    const rows = s.needs.map((n) => `
      <tr class="need-row sev-row-${F.esc((n.severity || '').toLowerCase())}${n.status === 'closed' ? ' is-closed' : ''}" data-key="need-${F.esc(n.need_id)}-${F.esc(n.status)}">
        <td>${F.needLink(n.need_id)}</td>
        <td>${F.sev(n.severity)}</td>
        <td class="need-title">${F.esc(n.title || n.kind || '')}</td>
        <td class="mono">${F.esc(n.source || '')}</td>
        <td>${F.esc(n.site || '')}</td>
        <td class="mono">${F.esc(n.skill_req || '—')}</td>
        <td>${F.esc(n.assigned_name || '—')}</td>
        <td class="num">${n.booked_slot ? F.range(n.booked_slot.start, n.booked_slot.end, today) : '—'}</td>
        <td class="num">${F.time(n.deadline, today)}</td>
        <td>${F.status(n.status)}</td>
      </tr>`).join('');
    return `<table class="table needs-table"><thead><tr>
      <th>ID</th><th>Sev</th><th>Need</th><th>Source</th><th>Site</th><th>Skill</th><th>Assigned</th><th>Booked slot</th><th>Deadline</th><th>Status</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
  },
};
