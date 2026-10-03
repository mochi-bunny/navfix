// Live feed: one camera or robot stream on screen, quick switching across the fleet.
// The player lives in its own part so polling never restarts the stream.
import * as F from '../format.js';
import { icon } from '../icons.js';
import { store } from '../state.js';
import { currentFeed, visibleFeeds } from '../feeds.js';

const noSignal = "this.closest('.part-stage').classList.add('no-signal')";

function player(f) {
  const src = F.esc(f.src || '');
  let media;
  if (f.kind === 'synthetic') media = `<canvas class="feed-media" data-synthetic data-site="${F.esc(f.site || '')}" data-robot="${F.esc(f.robot_id || '')}"></canvas>`;
  else if (!f.src) media = '';
  else if (f.kind === 'video') media = `<video class="feed-media" src="${src}" autoplay muted loop playsinline onerror="${noSignal}"></video>`;
  else if (f.kind === 'iframe') media = `<iframe class="feed-media" src="${src}" allow="autoplay; fullscreen" title="${F.esc(f.label)}"></iframe>`;
  else if (f.kind === 'image') media = `<img class="feed-media" alt="" data-base="${src}" data-refresh="${Number(f.refresh_ms) || 1000}" src="${src}" onerror="${noSignal}">`;
  else media = `<img class="feed-media" alt="" src="${src}" onerror="${noSignal}">`; // mjpeg
  return `<div class="stage${media ? '' : ' no-signal'}" data-feed="${F.esc(f.id)}">${media}</div>`;
}

function overlay(s, f, ctx) {
  const r = f.robot;
  const com = s.commissioning;
  const trial = com && com.status === 'running' && com.site === f.site && (!r || r.zone === 'drawer')
    ? `<div class="hud-chip hud-accent">Commissioning · trial ${com.trials.length}${com.policies.length ? ` · best P ${F.conf(Math.max(...com.policies.map((p) => p.confidence ?? 0)))}` : ''}</div>` : '';
  const site = s.warehouse.find((w) => w.site === f.site);
  return `<div class="hud">
    <div class="hud-tl"><span class="live-dot"></span><strong>${F.esc(f.label)}</strong>${site ? `<span class="hud-dim">${F.esc(site.name)} · ${F.esc(site.area || '')}</span>` : ''}</div>
    <div class="hud-tr">${f.simulated ? '<span class="hud-chip hud-warn">SIMULATED</span>' : ''}<span class="hud-chip">${ctx.now ? F.clockText(ctx.now.h, ctx.now.m) : ''}</span></div>
    <div class="hud-bl">${r ? `<div class="hud-chip st-${F.esc(r.status)}">${F.esc(r.status)}</div><div class="hud-chip">${F.esc(r.task)}</div><div class="hud-chip">🔋 ${r.battery}%</div>` : site ? `${F.status(site.health)} <div class="hud-chip">${site.robots_active}/${site.robots_total} robots active</div>` : ''}</div>
    <div class="hud-br">${trial}</div>
  </div>`;
}

export default {
  title: 'Live feed',
  more: '/live',
  meta(s) {
    const f = currentFeed(s);
    return f ? `<span class="muted small">[ ] to switch · ${s.feeds.length} feeds</span>` : '';
  },
  render(s, ctx) {
    const f = currentFeed(s);
    if (!f) return [['stage', '<div class="stage no-signal"><div class="no-feeds">No feeds yet. Add them under <span class="mono">live.feeds</span> in config.json, or serve <span class="mono">GET /feeds</span> from the cell service.</div></div>']];
    const site = store.ui.feedSite || 'all';
    const sites = [...new Set(s.feeds.map((x) => x.site).filter(Boolean))];
    const list = visibleFeeds(s);
    const switcher = `<div class="switcher">
      <div class="seg seg-sm">${['all', ...sites].map((k) => `<button data-action="feed-site" data-site="${F.esc(k)}" class="${site === k ? 'is-on' : ''}">${k === 'all' ? 'All' : F.esc(s.warehouse.find((w) => w.site === k)?.name || k)}</button>`).join('')}</div>
      <button class="icon-btn sm" data-action="feed-step" data-dir="-1" title="Previous ( [ )">${icon('prev')}</button>
      <div class="feed-chips">${list.map((x) => `<button class="feed-chip st-${F.esc(x.robot?.status || 'cam')}${x.id === f.id ? ' is-on' : ''}" data-action="watch" data-feed="${F.esc(x.id)}" title="${F.esc(x.label)}">
        ${x.robot_id ? '<span class="dot-st"></span>' : icon('camera')}${F.esc(x.robot_id ? x.robot_id : 'Overview')}</button>`).join('')}</div>
      <button class="icon-btn sm" data-action="feed-step" data-dir="1" title="Next ( ] )">${icon('next')}</button>
    </div>`;
    return [['stage', player(f)], ['overlay', overlay(s, f, ctx)], ['switch', switcher]];
  },
};
