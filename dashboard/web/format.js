// Formatting only. Every number shown comes from /api/state; nothing is computed here
// beyond turning values into text.

export const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ISO strings from the server are already wall-clock America/New_York.
export const minOfDay = (iso) => (iso ? +iso.slice(11, 13) * 60 + +iso.slice(14, 16) : null);
export const dateOf = (iso) => (iso ? iso.slice(0, 10) : null);

// Display preferences (set from Settings).
export const fmt = { clock24: false };

export function clockText(h, m) {
  if (fmt.clock24) return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
  const hh = h % 12 || 12;
  return `${hh}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
}

const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

// "1:25 PM", prefixed with the weekday when not on the sim clock's date.
export function time(iso, today) {
  if (!iso) return '—';
  const t = clockText(+iso.slice(11, 13), +iso.slice(14, 16));
  if (today && dateOf(iso) !== today) {
    const d = new Date(dateOf(iso) + 'T12:00:00Z');
    return `${DOW[d.getUTCDay()]} ${t}`;
  }
  return t;
}

export const range = (a, b, today) => (a ? `${time(a, today)}–${time(b, today)}` : '—');

// Compact "9:00–9:45" for tight spaces like calendar blocks.
export const short = (iso) => (iso ? `${fmt.clock24 ? iso.slice(11, 13) : +iso.slice(11, 13) % 12 || 12}:${iso.slice(14, 16)}` : '—');
export const shortRange = (a, b) => `${short(a)}–${short(b)}`;

export const dur = (min) => (min == null ? '—' : min >= 60 ? `${Math.floor(min / 60)} h ${min % 60 ? (min % 60) + ' min' : ''}`.trim() : `${min} min`);

export function relMin(mins) {
  if (mins == null) return '';
  if (Math.abs(mins) < 1) return 'now';
  const a = Math.abs(mins);
  const s = a >= 120 ? `${Math.round(a / 60)} h` : `${a} min`;
  return mins > 0 ? `in ${s}` : `${s} ago`;
}

// Confidence from the cell service, shown as-is.
export const conf = (p) => (p == null ? '—' : p < 0.001 ? '<0.001' : p.toFixed(3));
export const pct = (p) => (p == null ? '—' : `${Math.round(p * 100)}%`);

export const needLink = (id) => (id ? `<a class="need-link" href="/needs/${esc(id)}" data-nav>${esc(id)}</a>` : '');

export const linkifyNeeds = (text) => esc(text).replace(/\bT-\d+\b/g, (id) => needLink(id));

export const sev = (s) => (s ? `<span class="sev sev-${esc(s.toLowerCase())}">${esc(s)}</span>` : '');

export const status = (s) => (s ? `<span class="status status-${esc(String(s).replace(/[^a-z0-9_-]/gi, ''))}">${esc(String(s).replace(/_/g, ' '))}</span>` : '');

export const modeIcon = (m) => ({ transit: '🚇', walk: '🚶', car: '🚕', drive: '🚕', taxi: '🚕', bike: '🚲' }[m] || '→');
