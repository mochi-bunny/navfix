import { ROUTES, ALWAYS } from './layout.js';
import { VIEWS } from './views/index.js';
import * as F from './format.js';
import { icon, logo } from './icons.js';
import {
  store, actions, applySettings, settings, currentUser, setUser, avatar, unseenChanges, unseenFleet, closeDrawer,
} from './state.js';
import { openEvent } from './drawers.js';

const $ = (sel, el = document) => el.querySelector(sel);
let receivedAt = 0;
let pollMs = 1000;
let current = null; // { route, params, panels: Map(viewId -> {el, seen}) }
let offline = false;

// ---- sim clock (interpolated between polls) ---------------------------------
export function simNow() {
  const snap = store.snap;
  if (!snap) return null;
  const c = snap.clock;
  const ms = c.epoch_ms + (Date.now() - receivedAt) * c.speed;
  const wall = new Date(ms + c.tz_offset_min * 60000); // read with UTC getters
  const h = wall.getUTCHours(), m = wall.getUTCMinutes(), s = wall.getUTCSeconds();
  return { ms, h, m, s, minOfDay: h * 60 + m + s / 60, date: wall.toISOString().slice(0, 10) };
}

function tickClock() {
  const n = simNow();
  if (!n) return;
  const t = F.clockText(n.h, n.m);
  $('#clock-time').textContent = F.fmt.clock24 ? `${t}:${String(n.s).padStart(2, '0')}` : t.replace(' ', `:${String(n.s).padStart(2, '0')} `);
  const d = new Date(n.date + 'T12:00:00Z');
  $('#clock-date').textContent = d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' });
  $('#clock-speed').textContent = store.snap.clock.speed !== 1 ? `×${store.snap.clock.speed}` : '';
}

// ---- routing ---------------------------------------------------------------
const compiled = ROUTES.map((r) => ({ ...r, re: new RegExp('^' + r.path.replace(/:(\w+)/g, '(?<$1>[^/]+)') + '/?$') }));

function match(path) {
  if (path === '/' && settings().home === 'full') path = '/day';
  for (const r of compiled) {
    const m = path.match(r.re);
    if (m) return { route: r, params: Object.fromEntries(Object.entries(m.groups || {}).map(([k, v]) => [k, decodeURIComponent(v)])) };
  }
  return { route: compiled[0], params: {} };
}

export function navigate(path) {
  if (path !== location.pathname) history.pushState({}, '', path);
  store.ui.drawer = null;
  mount();
}
store.navigate = navigate;

function mount() {
  const { route, params } = match(location.pathname);
  document.title = `${route.title} · Fleet & Field`;
  const main = $('#main');
  main.className = `layout layout-${route.layout}`;
  $('.app').classList.toggle('is-simple', route.chrome === false);
  store.ui.menuOpen = false;
  main.innerHTML = '';
  const panels = new Map();
  for (const id of route.views) {
    const v = VIEWS[id];
    const sec = document.createElement('section');
    sec.className = `panel panel-${id}${route.bare ? ' is-bare' : ''}`;
    sec.dataset.view = id;
    sec.innerHTML = route.bare ? '<div class="panel-body"></div>'
      : `<header class="panel-head"><h2 class="panel-title">${F.esc(v.title)}</h2><div class="panel-meta"></div></header><div class="panel-body"></div>`;
    main.appendChild(sec);
    panels.set(id, { el: sec, seen: null });
    v.init?.(sec);
  }
  current = { route, params, panels };
  render();
}

// ---- rendering ---------------------------------------------------------------
// A view returns an HTML string or a list of [partKey, html]. Each part is only
// replaced when its HTML changed, so video players and form inputs survive polls.
function patch(body, out) {
  const parts = typeof out === 'string' ? [['main', out]] : out;
  const keep = new Set();
  let changed = false;
  for (const [key, html] of parts) {
    keep.add(key);
    let el = body.querySelector(`:scope > [data-part="${key}"]`);
    if (!el) {
      el = document.createElement('div');
      el.dataset.part = key;
      el.className = `part part-${key}`;
      body.appendChild(el);
    }
    if (el._html !== html) {
      const scrollers = [...el.querySelectorAll('[data-autoscroll]')].map((s) => s.scrollHeight - s.scrollTop - s.clientHeight < 40);
      el.innerHTML = html;
      el._html = html;
      el.querySelectorAll('[data-autoscroll]').forEach((s, i) => { if (scrollers[i] ?? true) s.scrollTop = s.scrollHeight; });
      changed = true;
    }
  }
  for (const el of [...body.children]) if (el.dataset.part && !keep.has(el.dataset.part)) el.remove();
  return changed;
}

const setHTML = (el, html) => { if (el._html !== html) { el.innerHTML = html; el._html = html; } };

// Anything carrying data-key that wasn't on screen last render gets .is-new.
function flashNew(panel) {
  const els = [...panel.el.querySelectorAll('[data-key]')];
  if (panel.seen) {
    const ms = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--flash-ms')) || 4000;
    for (const e of els) {
      if (!panel.seen.has(e.dataset.key)) {
        e.classList.add('is-new');
        setTimeout(() => e.classList.remove('is-new'), ms);
      }
    }
  }
  panel.seen = new Set(els.map((e) => e.dataset.key));
}

function render() {
  const snap = store.snap;
  if (!snap || !current) return;
  const ctx = { now: simNow(), params: current.params, today: snap.clock.date, navigate, user: currentUser() };
  for (const [id, panel] of current.panels) {
    const v = VIEWS[id];
    try {
      if (patch($('.panel-body', panel.el), v.render(snap, ctx))) flashNew(panel);
      const m = $('.panel-meta', panel.el);
      if (m) setHTML(m, v.meta ? v.meta(snap, ctx) : '');
    } catch (e) {
      console.error(id, e);
      patch($('.panel-body', panel.el), `<div class="error">View error: ${F.esc(e.message)}</div>`);
    }
  }
  renderChrome(ctx);
}
store.rerender = render;

function railItem(r, badges) {
  const active = current && (current.route.path === r.path
    || (r.path === '/needs' && current.route.path.startsWith('/need')));
  const n = r.badge ? badges[r.badge] : 0;
  return `<a href="${r.path}" data-nav class="rail-item${active ? ' is-active' : ''}" title="${F.esc(r.title)}${r.key ? ` (${r.key})` : ''}">
    ${icon(r.icon)}<span class="rail-label">${F.esc(r.title)}</span>${n ? `<span class="count">${n}</span>` : ''}</a>`;
}

function renderChrome(ctx) {
  const snap = store.snap;
  const user = ctx.user;
  const badges = { changes: unseenChanges(snap, user).length, fleet: unseenFleet(snap).length };

  setHTML($('.rail-logo'), logo());
  setHTML($('#rail-nav'), compiled.filter((r) => r.rail).map((r) => railItem(r, badges)).join(''));
  setHTML($('#rail-bottom'), compiled.filter((r) => r.railBottom).map((r) => railItem(r, badges)).join(''));
  setHTML($('#rail-users'), snap.staff.map((s) => `<button class="rail-user${s.id === user?.id ? ' is-active' : ''}" data-action="set-user" data-id="${F.esc(s.id)}" title="View as ${F.esc(s.name)}">${avatar(s, 'lg')}</button>`).join(''));
  setHTML($('#search-icon'), icon('search'));

  setHTML($('#top-icons'), `
    <a class="icon-btn" href="/fleet-updates" data-nav title="Fleet updates">${icon('robot')}${badges.fleet ? `<span class="count">${badges.fleet}</span>` : ''}</a>
    <button class="icon-btn" data-action="open-updates" title="Plan updates">${icon('bell')}${badges.changes ? `<span class="count">${badges.changes}</span>` : ''}</button>
    <a class="icon-btn" href="/settings" data-nav title="Settings">${icon('gear')}</a>`);
  setHTML($('#hello'), user ? `<span>Hello, <strong>${F.esc(user.name.split(' ')[0])}</strong></span>${avatar(user, 'lg')}` : '');

  const bad = Object.entries(snap.system?.sources || {}).filter(([, s]) => !s.ok);
  const b = [];
  if (snap.any_mock) b.push(`<span class="badge badge-mock" title="${F.esc(Object.entries(snap.modes).map(([k, v]) => `${k}: ${v}`).join(', '))}">MOCK DATA</span>`);
  if (offline) b.push('<span class="badge badge-error">dashboard server unreachable</span>');
  for (const [name, s] of bad) b.push(`<span class="badge badge-error" title="${F.esc(s.error || '')}">${F.esc(name)} offline</span>`);
  setHTML($('#badges'), b.join(''));

  if (ALWAYS.localProofStrip) {
    const sys = snap.system || {};
    const calls = sys.cloud_model_calls;
    setHTML($('#proof-strip'), `<span class="dot ${sys.vllm?.up ? 'dot-up' : 'dot-down'}"></span>vLLM ${sys.vllm?.up ? 'up' : 'down'}`
      + ` · <span class="mono">${F.esc(sys.vllm?.model || '—')}</span>`
      + (sys.gpu?.used_gb != null ? ` · GPU mem ${sys.gpu.used_gb} / ${sys.gpu.total_gb} GB` : '')
      + ` · cloud model calls: <strong class="calls">${calls == null ? 'not wired' : calls}</strong>`);
  }

  // drawer
  const d = store.ui.drawer;
  const drawer = $('#drawer');
  drawer.hidden = !d;
  $('#drawer-backdrop').hidden = !d;
  if (d) {
    if (!drawer._open) {
      drawer.innerHTML = `<button class="drawer-close icon-btn" data-action="drawer-close" title="Close">${icon('close')}</button><div class="drawer-body"></div>`;
      drawer._open = true;
      drawer._seen = null;
    }
    const body = $('.drawer-body', drawer);
    if (patch(body, d.render())) { const p = { el: drawer, seen: drawer._seen }; flashNew(p); drawer._seen = p.seen; }
  } else if (drawer._open) {
    drawer._open = false;
    drawer.innerHTML = '';
  }

  // toast
  const t = store.ui.toast;
  const te = $('#toast');
  const show = t && t.until > Date.now();
  te.hidden = !show;
  if (show) { te.textContent = t.text; te.className = `toast ${t.ok ? 'ok' : 'fail'}`; }
}

// ---- search ------------------------------------------------------------------------
function runSearch(q) {
  const box = $('#search-results');
  q = q.trim().toLowerCase();
  if (!q || !store.snap) { box.hidden = true; return; }
  const s = store.snap;
  const today = s.clock.date;
  const hits = [];
  for (const st of s.staff) if (st.name.toLowerCase().includes(q)) hits.push(`<button data-action="search-user" data-id="${F.esc(st.id)}">${avatar(st, 'sm')} View as ${F.esc(st.name)}</button>`);
  for (const n of s.needs) if (`${n.need_id} ${n.title} ${n.site}`.toLowerCase().includes(q)) hits.push(`<a href="/needs/${F.esc(n.need_id)}" data-nav>${icon('ticket')} ${F.esc(n.need_id)} · ${F.esc(n.title || '')}</a>`);
  for (const e of s.events) if (`${e.title} ${e.place}`.toLowerCase().includes(q)) hits.push(`<button data-action="search-event" data-id="${F.esc(e.id)}">${icon('calendar')} ${F.esc(e.title)} · ${F.time(e.start, today)} · ${F.esc(s.staff.find((x) => x.id === e.staff_id)?.name || '')}</button>`);
  box.innerHTML = hits.slice(0, 10).join('') || '<div class="empty">No matches</div>';
  box.hidden = false;
}
const clearSearch = () => { $('#search').value = ''; $('#search-results').hidden = true; };

// ---- built-in actions ------------------------------------------------------------------
Object.assign(actions, {
  'set-user': (el) => setUser(el.dataset.id),
  'search-user': (el) => { clearSearch(); setUser(el.dataset.id); },
  'search-event': (el) => { clearSearch(); openEvent(el.dataset.id); },
});

// Drawers reachable by URL for recording: ?event=E4, ?updates=1, ?add=1, ?menu=1, ?user=s2
function openFromUrl() {
  const q = new URLSearchParams(location.search);
  if (q.get('user')) setUser(q.get('user'));
  if (q.get('event')) openEvent(q.get('event'));
  else if (q.get('updates')) actions['open-updates']?.();
  else if (q.get('add')) actions['add-change']?.();
  else if (q.get('menu')) { store.ui.menuOpen = true; render(); }
}

// ---- polling -------------------------------------------------------------------
async function poll() {
  try {
    const r = await fetch('/api/state', { cache: 'no-store' });
    if (!r.ok) throw new Error(r.status);
    const first = !store.snap;
    store.snap = await r.json();
    receivedAt = Date.now();
    offline = false;
    if (first) { mount(); openFromUrl(); } else render();
  } catch (e) {
    offline = true;
    if (store.snap) renderChrome({ user: currentUser() });
  }
  setTimeout(poll, pollMs);
}

// ---- wiring --------------------------------------------------------------------
document.addEventListener('click', (e) => {
  const a = e.target.closest('a[data-nav]');
  if (a && !(e.metaKey || e.ctrlKey || e.shiftKey || a.target)) {
    e.preventDefault();
    clearSearch();
    navigate(a.getAttribute('href'));
    return;
  }
  if (store.ui.menuOpen && !e.target.closest('.menu')) { store.ui.menuOpen = false; render(); }
  const act = e.target.closest('[data-action]');
  if (act && actions[act.dataset.action]) {
    e.preventDefault();
    actions[act.dataset.action](act, e);
    return;
  }
  if (!e.target.closest('.search')) $('#search-results').hidden = true;
});
document.addEventListener('submit', (e) => {
  const form = e.target.closest('form[data-form]');
  if (!form || !actions[form.dataset.form]) return;
  e.preventDefault();
  actions[form.dataset.form](form, e.submitter);
});
document.addEventListener('change', (e) => {
  const el = e.target.closest('[data-change]');
  if (el && actions[el.dataset.change]) actions[el.dataset.change](el, e);
});
$('#drawer-backdrop').addEventListener('click', () => closeDrawer());
$('#search').addEventListener('input', (e) => runSearch(e.target.value));
window.addEventListener('popstate', () => { store.ui.drawer = null; mount(); });
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && store.ui.drawer) { closeDrawer(); return; }
  if (e.key === 'Escape' && store.ui.menuOpen) { store.ui.menuOpen = false; render(); return; }
  if (e.metaKey || e.ctrlKey || e.altKey || /INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName)) return;
  const r = compiled.find((x) => x.key && x.key === e.key.toLowerCase());
  if (r) navigate(r.path);
});

(async function start() {
  applySettings();
  try {
    const c = await (await fetch('/api/config')).json();
    pollMs = c.poll_ms || 1000;
  } catch { /* defaults */ }
  if (!ALWAYS.clock) $('#clock').hidden = true;
  if (!ALWAYS.localProofStrip) $('#proof-strip').hidden = true;
  poll();
  setInterval(tickClock, 250);
  setInterval(() => store.snap && render(), 1000); // keeps "now" lines and countdowns moving between polls
})();
