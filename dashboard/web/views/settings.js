// Settings: appearance (per viewer, stored in this browser) and account info (from Field's staff table).
import * as F from '../format.js';
import { icon } from '../icons.js';
import { store, on, settings, saveSettings, DEFAULT_SETTINGS, ACCENTS, setUser, avatar } from '../state.js';

on('set-tab', (el) => { store.ui.setTab = el.dataset.tab; store.rerender(); });
on('set-opt', (el) => {
  const v = el.dataset.value;
  saveSettings({ [el.dataset.key]: v === 'true' ? true : v === 'false' ? false : v });
});
on('set-reset', () => saveSettings({ ...DEFAULT_SETTINGS }));

const opt = (key, value, label, cur) =>
  `<button class="chip-btn${String(cur) === String(value) ? ' is-on' : ''}" data-action="set-opt" data-key="${key}" data-value="${F.esc(value)}">${label}</button>`;

function appearance() {
  const s = settings();
  const row = (label, help, ctrl) => `<div class="set-row"><div><div class="set-label">${label}</div><div class="muted small">${help}</div></div><div class="chips">${ctrl}</div></div>`;
  return `<div class="set-card">
    ${row('Theme', 'Dark matches the recording style.', opt('theme', 'dark', 'Dark', s.theme) + opt('theme', 'light', 'Light', s.theme))}
    ${row('Accent', 'Buttons, highlights and the active nav item.', ACCENTS.map((a) => `<button class="swatch-btn${s.accent === a ? ' is-on' : ''}" style="--sw:${a}" data-action="set-opt" data-key="accent" data-value="${a}" title="${a}"></button>`).join(''))}
    ${row('Density', 'Compact fits more on one screen.', opt('density', 'comfortable', 'Comfortable', s.density) + opt('density', 'compact', 'Compact', s.density))}
    ${row('Clock', 'How times are written everywhere.', opt('clock24', false, '12-hour', s.clock24) + opt('clock24', true, '24-hour', s.clock24))}
    ${row('Highlight changes', 'Briefly highlight anything that just appeared or changed.', opt('highlight', true, 'On', s.highlight) + opt('highlight', false, 'Off', s.highlight))}
    ${row('My day opens as', 'The default layout of the day plan.', opt('planMode', 'plan', 'Plan', s.planMode) + opt('planMode', 'timeline', 'Timeline', s.planMode))}
    <div class="set-row"><div class="muted small">Saved in this browser only.</div><button class="btn btn-sm" data-action="set-reset">${icon('undo')} Reset to defaults</button></div>
  </div>`;
}

function account(s, user) {
  if (!user) return '<p class="empty">No staff data.</p>';
  const p = s.prefs || {};
  const pref = (k, label) => (p[k] != null ? `<div><dt>${label}</dt><dd>${F.esc(p[k])}</dd></div>` : '');
  return `<div class="set-grid">
    <div class="set-card profile">
      <div class="profile-head">${avatar(user, 'xl')}<div><h3>${F.esc(user.name)}</h3><div class="muted">${F.esc(user.role || '')}</div></div></div>
      <dl class="facts">
        <div><dt>Staff ID</dt><dd class="mono">${F.esc(user.id)}</dd></div>
        <div><dt>Home base</dt><dd>${F.esc(user.home_base || '—')}</dd></div>
        <div><dt>Current location</dt><dd>${F.esc(user.current_location || '—')}</dd></div>
        <div class="wide"><dt>Skills</dt><dd>${(user.skills || []).map((k) => `<span class="chip">${F.esc(k)}</span>`).join(' ') || '—'}</dd></div>
      </dl>
      <p class="muted small">Profile comes from Field's staff table. The dashboard has no sign-in; pick who you are below.</p>
    </div>
    <div class="set-card">
      <h3 class="set-h">Viewing as</h3>
      <div class="who-list">${s.staff.map((st) => `<button class="who${st.id === user.id ? ' is-on' : ''}" data-action="set-user" data-id="${F.esc(st.id)}">${avatar(st)}<span><strong>${F.esc(st.name)}</strong><br><span class="muted small">${F.esc(st.role || '')}</span></span></button>`).join('')}</div>
      <h3 class="set-h">Field preferences</h3>
      <dl class="facts">${pref('buffer_min', 'Buffer (min)')}${pref('buffer', 'Buffer (min)')}${pref('walk_limit_min', 'Walk limit (min)')}${pref('brief_time', 'Morning brief')}${pref('quiet_hours', 'Quiet hours')}${pref('tz', 'Time zone')}</dl>
      <p class="muted small">Set by Field; change them through Field.</p>
    </div>
  </div>`;
}

export default {
  title: 'Settings',
  render(s, ctx) {
    const tab = store.ui.setTab || 'appearance';
    const tabs = `<nav class="tabs big">${[['appearance', 'Appearance', 'grid'], ['account', 'Account', 'user']].map(([k, l, ic]) =>
      `<button class="tab${tab === k ? ' is-active' : ''}" data-action="set-tab" data-tab="${k}">${icon(ic)} ${l}</button>`).join('')}</nav>`;
    return [['tabs', tabs], ['body', tab === 'account' ? account(s, ctx.user) : appearance()]];
  },
};
