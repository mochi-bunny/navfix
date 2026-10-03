// Change log: every change to the plan (Fleet-driven bookings, MBTA delays,
// reschedules, manual edits), with edit and revert. Writes go to Field.
import * as F from '../format.js';
import { icon } from '../icons.js';
import {
  store, on, post, toast, currentUser, userChanges, unseenChanges, seen, markSeen, describeChange, kindChip, KIND_LABEL, avatar,
} from '../state.js';
import { openEvent } from '../drawers.js';

on('chg-scope', (el) => { store.ui.chgScope = el.dataset.scope; store.rerender(); });
on('chg-kind', (el) => { store.ui.chgKind = el.dataset.kind; store.rerender(); });
on('chg-edit', (el) => openEvent(el.dataset.id));
on('chg-revert-ask', (el) => { store.ui.revertAsk = el.dataset.id; store.rerender(); });
on('chg-revert-cancel', () => { store.ui.revertAsk = null; store.rerender(); });
on('chg-revert', async (el) => {
  store.ui.revertAsk = null;
  try {
    await post('/api/edit/revert', { change_id: el.dataset.id });
    toast('Reverted: Field is updating the plan');
  } catch (e) {
    toast(`Revert failed: ${e.message}`, false);
  }
});
on('chg-seen', () => markSeen('changes', unseenChanges(store.snap, currentUser()).map((c) => c.id)));

const BY_ICON = { Field: 'route', Fleet: 'robot', You: 'user' };

export default {
  title: 'Changes',
  meta(s, ctx) {
    const n = unseenChanges(s, ctx.user).length;
    return n ? `<button class="btn btn-sm" data-action="chg-seen">${icon('check')} Mark ${n} new as seen</button>` : 'all seen';
  },
  render(s, ctx) {
    const today = s.clock.date;
    const user = ctx.user;
    const scope = store.ui.chgScope || 'mine';
    const kind = store.ui.chgKind || 'all';
    const seenIds = seen('changes');
    let list = userChanges(s, user, scope === 'all');
    const kinds = [...new Set(list.map((c) => c.kind))];
    if (kind !== 'all') list = list.filter((c) => c.kind === kind);
    list = [...list].reverse();

    const filters = `<div class="filters">
      <div class="seg"><button data-action="chg-scope" data-scope="mine" class="${scope === 'mine' ? 'is-on' : ''}">${avatar(user, 'sm')} ${F.esc(user?.name.split(' ')[0] || 'Mine')}</button>
        <button data-action="chg-scope" data-scope="all" class="${scope === 'all' ? 'is-on' : ''}">Everyone</button></div>
      <div class="chips"><button data-action="chg-kind" data-kind="all" class="chip-btn${kind === 'all' ? ' is-on' : ''}">All</button>
        ${kinds.map((k) => `<button data-action="chg-kind" data-kind="${F.esc(k)}" class="chip-btn${kind === k ? ' is-on' : ''}">${F.esc(KIND_LABEL[k] || k)}</button>`).join('')}</div>
    </div>`;

    const rows = list.map((c) => {
      const st = s.staff.find((x) => x.id === c.staff_id);
      const asking = store.ui.revertAsk === c.id;
      const actions = [
        c.event_exists ? `<button class="btn btn-sm" data-action="chg-edit" data-id="${F.esc(c.event_id)}">${icon('edit')} Edit</button>` : '',
        c.revertable && !asking ? `<button class="btn btn-sm" data-action="chg-revert-ask" data-id="${F.esc(c.id)}">${icon('undo')} Revert</button>` : '',
        asking ? `<span class="confirm-inline">Undo this change?</span><button class="btn btn-sm btn-danger" data-action="chg-revert" data-id="${F.esc(c.id)}">Yes, revert</button><button class="btn btn-sm" data-action="chg-revert-cancel">Keep</button>` : '',
        c.reverted_by ? '<span class="muted small">reverted</span>' : '',
      ].join('');
      return `<li class="log-row${!seenIds.has(c.id) && c.by !== 'You' ? ' is-unseen' : ''}${c.reverted_by ? ' is-reverted' : ''}" data-key="log-${F.esc(c.id)}-${c.reverted_by ? 'r' : ''}">
        <div class="log-time">${F.time(c.at, today)}</div>
        <div class="log-by" title="${F.esc(c.by)}">${icon(BY_ICON[c.by] || 'history')}<span>${F.esc(c.by)}</span></div>
        <div class="log-main">
          <div class="log-title">${kindChip(c.kind)} <strong>${F.esc(c.event_title || '')}</strong> ${F.needLink(c.need_id)} ${scope === 'all' && st ? `<span class="muted small">${F.esc(st.name)}</span>` : ''}</div>
          <div class="log-diff">${F.esc(describeChange(c, today))}</div>
          ${c.reason ? `<div class="log-reason">${F.esc(c.reason)}</div>` : ''}
        </div>
        <div class="log-actions">${actions}</div>
      </li>`;
    }).join('');
    return [['filters', filters], ['list', rows ? `<ol class="log">${rows}</ol>` : '<p class="empty">No changes yet.</p>']];
  },
};
