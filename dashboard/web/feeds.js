// Live feeds: which feed is on screen, switching between them, and the
// synthetic camera used in mock mode (a top-down render of the cell driven by
// Fleet's robot telemetry) until the Isaac Sim / robot streams are wired in.
import { store, on, LS } from './state.js';

// ---- selection --------------------------------------------------------------
export function visibleFeeds(snap) {
  const site = store.ui.feedSite || 'all';
  return site === 'all' ? snap.feeds : snap.feeds.filter((f) => f.site === site);
}

export function currentFeed(snap) {
  const id = store.ui.feedId ?? LS.get('ff.feed', null);
  // Nothing picked yet: open on the site that needs attention.
  const trouble = snap.warehouse.find((w) => w.health && w.health !== 'ok')?.site;
  return snap.feeds.find((f) => f.id === id)
    || (trouble && snap.feeds.find((f) => f.site === trouble && !f.robot_id))
    || snap.feeds[0] || null;
}

export function selectFeed(id) {
  store.ui.feedId = id;
  LS.set('ff.feed', id);
  store.rerender();
}

export function stepFeed(dir) {
  const snap = store.snap;
  if (!snap?.feeds.length) return;
  const list = visibleFeeds(snap).length ? visibleFeeds(snap) : snap.feeds;
  const cur = currentFeed(snap);
  const i = Math.max(0, list.findIndex((f) => f.id === cur?.id));
  selectFeed(list[(i + dir + list.length) % list.length].id);
}

// data-feed="<id>", data-robot="<robot id>" or data-site="<site>" (that site's overview camera)
on('watch', (el) => {
  const feeds = store.snap.feeds;
  const f = el.dataset.feed ? feeds.find((x) => x.id === el.dataset.feed)
    : el.dataset.robot ? feeds.find((x) => x.robot_id === el.dataset.robot)
      : feeds.find((x) => x.site === el.dataset.site && !x.robot_id) || feeds.find((x) => x.site === el.dataset.site);
  if (!f) return;
  selectFeed(f.id);
  if (!document.querySelector('.panel-feed')) store.navigate('/live');
});
on('feed-step', (el) => stepFeed(Number(el.dataset.dir)));
on('feed-site', (el) => { store.ui.feedSite = el.dataset.site; store.rerender(); });

// ---- render loop: synthetic cameras + snapshot images --------------------------
let started = false;
export function startFeedLoop() {
  if (started) return;
  started = true;
  const tick = (t) => {
    document.querySelectorAll('canvas[data-synthetic]').forEach((c) => drawCell(c, t));
    document.querySelectorAll('img[data-refresh]').forEach((img) => {
      const every = Number(img.dataset.refresh) || 1000;
      if (!img._last || t - img._last >= every) {
        img._last = t;
        const base = img.dataset.base;
        img.src = `${base}${base.includes('?') ? '&' : '?'}t=${Date.now()}`;
      }
    });
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

const ZONES = { 'pack-1': [0, 0], 'pack-2': [1, 0], drawer: [0, 1], dock: [1, 1] };
const STATUS_COLOR = { ok: '#3d9bf0', blocked: '#ef4f4f', charging: '#f5a524', commissioning: '#8b7cf6' };
const hash = (s) => [...String(s)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) | 0, 7) >>> 0;

function roundRect(g, x, y, w, h, r) {
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}

function drawCell(c, t) {
  const w = c.clientWidth, h = c.clientHeight;
  if (!w || !h) return;
  const dpr = window.devicePixelRatio || 1;
  if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
    c.width = Math.round(w * dpr);
    c.height = Math.round(h * dpr);
  }
  const g = c.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const snap = store.snap;
  const wh = snap?.warehouse.find((x) => x.site === c.dataset.site);
  const robots = wh?.robots || [];
  const me = robots.find((r) => r.id === c.dataset.robot);
  const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#8b7cf6';

  g.fillStyle = '#0d0e15';
  g.fillRect(0, 0, w, h);

  const pad = 26, zw = (w - pad * 2) / 2, zh = (h - pad * 2) / 2;
  const zoneBox = (name) => { const [zx, zy] = ZONES[name] || [0, 0]; return { x: pad + zx * zw + 10, y: pad + zy * zh + 10, w: zw - 20, h: zh - 20 }; };

  g.save();
  let scale = 1;
  if (me) { // robot camera: zoom into its zone
    const z = zoneBox(me.zone);
    scale = Math.min(1.9, Math.min(w / (z.w + 40), h / (z.h + 40)));
    g.translate(w / 2, h / 2);
    g.scale(scale, scale);
    g.translate(-(z.x + z.w / 2), -(z.y + z.h / 2));
  }

  // floor grid
  g.strokeStyle = 'rgba(255,255,255,0.045)';
  g.lineWidth = 1 / scale;
  for (let x = 0; x < w; x += 28) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, h); g.stroke(); }
  for (let y = 0; y < h; y += 28) { g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke(); }

  // zones and stations
  for (const name of Object.keys(ZONES)) {
    const z = zoneBox(name);
    g.fillStyle = 'rgba(139,124,246,0.06)';
    g.strokeStyle = 'rgba(255,255,255,0.14)';
    g.lineWidth = 1.2 / scale;
    roundRect(g, z.x, z.y, z.w, z.h, 12);
    g.fill();
    g.stroke();
    g.fillStyle = 'rgba(255,255,255,0.45)';
    g.font = `${12}px system-ui, sans-serif`;
    g.fillText(name.toUpperCase(), z.x + 10, z.y + 18);
    g.fillStyle = 'rgba(255,255,255,0.10)';
    if (name.startsWith('pack')) { // conveyor with moving boxes
      const cy = z.y + z.h - 30;
      g.fillRect(z.x + 14, cy, z.w - 28, 14);
      g.fillStyle = 'rgba(245,165,36,0.75)';
      for (let k = 0; k < 4; k++) {
        const bx = z.x + 14 + (((t / 40) + k * (z.w / 4)) % (z.w - 46));
        g.fillRect(bx, cy + 2, 18, 10);
      }
    } else if (name === 'drawer') { // cabinet, middle drawer under test at Globex
      const cx = z.x + z.w - 70, cy = z.y + 26;
      for (let k = 0; k < 3; k++) {
        g.fillStyle = k === 1 && wh?.health !== 'ok' ? 'rgba(239,79,79,0.45)' : 'rgba(255,255,255,0.12)';
        g.fillRect(cx, cy + k * 22, 56, 18);
      }
    } else { // dock door
      g.fillRect(z.x + z.w - 26, z.y + 24, 12, z.h - 48);
    }
  }

  // robots
  const perZone = {};
  robots.forEach((r) => { (perZone[r.zone] = perZone[r.zone] || []).push(r); });
  for (const [zone, list] of Object.entries(perZone)) {
    const z = zoneBox(zone);
    list.forEach((r, k) => {
      const hsh = hash(r.id);
      const baseX = z.x + z.w * (0.3 + 0.35 * (k % 2)), baseY = z.y + z.h * (0.42 + 0.18 * Math.floor(k / 2));
      let x = baseX, y = baseY, heading = 0;
      if (r.status === 'ok') {
        const a = (t / 1000) * (0.5 + (hsh % 5) / 10) + hsh;
        x = baseX + Math.cos(a) * z.w * 0.12;
        y = baseY + Math.sin(a) * z.h * 0.1;
        heading = a + Math.PI / 2;
      } else if (r.status === 'commissioning') { // reach toward the cabinet and back: a trial
        const p = (Math.sin(t / 700) + 1) / 2;
        x = baseX + p * (z.w * 0.32);
        heading = 0;
      }
      const col = STATUS_COLOR[r.status] || '#9a9ab0';
      if (me && r.id === me.id) {
        g.strokeStyle = accent;
        g.lineWidth = 2.5 / scale;
        g.beginPath();
        g.arc(x, y, 20 + Math.sin(t / 300) * 2, 0, Math.PI * 2);
        g.stroke();
      }
      g.save();
      g.translate(x, y);
      g.rotate(heading);
      g.fillStyle = col;
      roundRect(g, -11, -11, 22, 22, 6);
      g.fill();
      g.fillStyle = 'rgba(255,255,255,0.9)';
      g.fillRect(4, -2, 8, 4); // heading marker
      g.restore();
      if (r.status === 'blocked' && Math.floor(t / 400) % 2) {
        g.strokeStyle = '#ef4f4f';
        g.lineWidth = 2 / scale;
        g.beginPath();
        g.arc(x, y, 16, 0, Math.PI * 2);
        g.stroke();
      }
      g.fillStyle = 'rgba(255,255,255,0.85)';
      g.font = `${11}px ui-monospace, monospace`;
      g.fillText(r.id, x - 14, y + 26);
    });
  }
  g.restore();

  // camera vignette + scanline
  const grd = g.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.35, w / 2, h / 2, Math.max(w, h) * 0.75);
  grd.addColorStop(0, 'rgba(0,0,0,0)');
  grd.addColorStop(1, 'rgba(0,0,0,0.55)');
  g.fillStyle = grd;
  g.fillRect(0, 0, w, h);
  const sy = (t / 12) % h;
  g.fillStyle = 'rgba(255,255,255,0.025)';
  g.fillRect(0, sy, w, 3);
}
