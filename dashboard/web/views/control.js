import * as F from '../format.js';

// Each control calls the owning service's endpoint via /api/control/*;
// the dashboard never edits data itself.
const FORMS = `
<div class="controls">
  <form class="control" data-control="clock">
    <h3>Sim clock</h3>
    <label>Time <input name="time" type="time" step="60" value="13:20"></label>
    <label>Speed <select name="speed">
      ${[1, 10, 30, 60, 120, 300].map((v) => `<option value="${v}"${v === 60 ? ' selected' : ''}>×${v}</option>`).join('')}
    </select></label>
    <div class="row"><button type="submit" name="which" value="both">Set time + speed</button>
      <button type="submit" name="which" value="time">Set time</button>
      <button type="submit" name="which" value="speed">Set speed</button></div>
  </form>
  <form class="control" data-control="delay">
    <h3>Inject MBTA delay</h3>
    <label>Line <select name="line">${['Red', 'Orange', 'Green', 'Blue', 'Silver'].map((l) => `<option>${l}</option>`).join('')}</select></label>
    <label>Minutes <input name="minutes" type="number" min="1" max="90" value="12"></label>
    <div class="row"><button type="submit">Inject delay</button></div>
  </form>
  <form class="control" data-control="fire">
    <h3>Fire mock Need</h3>
    <label>Need <input name="need_id" value="T-12"></label>
    <div class="row"><button type="submit">Fire</button></div>
  </form>
  <form class="control" data-control="reset">
    <h3>Reset demo day</h3>
    <p class="muted">Clock back to the start of the day at ×1; services reset.</p>
    <div class="row"><button type="submit" class="danger" name="which" value="ask">Reset…</button>
      <button type="submit" class="danger confirm" name="which" value="confirm" hidden>Yes, reset everything</button></div>
  </form>
</div>
<div class="control-result" aria-live="polite"></div>`;

async function send(action, body) {
  const r = await fetch(`/api/control/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.ok) throw new Error(j.error || `HTTP ${r.status}`);
}

export default {
  title: 'Demo control',
  init(sec) {
    sec.addEventListener('submit', async (e) => {
      e.preventDefault();
      const form = e.target;
      const which = e.submitter?.value;
      const out = sec.querySelector('.control-result');
      const fd = Object.fromEntries(new FormData(form));
      const action = form.dataset.control;
      let body = {};
      if (action === 'clock') {
        if (which !== 'speed') body.time = fd.time;
        if (which !== 'time') body.speed = Number(fd.speed);
      } else if (action === 'delay') body = { line: fd.line, minutes: Number(fd.minutes) };
      else if (action === 'fire') body = { need_id: fd.need_id.trim() };
      else if (action === 'reset') {
        const confirmBtn = form.querySelector('.confirm');
        if (which === 'ask') { confirmBtn.hidden = false; return; }
        confirmBtn.hidden = true;
      }
      out.textContent = 'Sending…';
      try {
        await send(action, body);
        out.textContent = `✓ ${action} sent at ${new Date().toLocaleTimeString()}`;
        out.className = 'control-result ok';
      } catch (err) {
        out.textContent = `✗ ${action} failed: ${err.message}`;
        out.className = 'control-result fail';
      }
    });
  },
  render(s) {
    const modes = Object.entries(s.modes).map(([k, v]) => `${F.esc(k)}=${F.esc(v)}`).join(' · ');
    const status = `<p class="muted">Sim time ${F.time(s.clock.sim_time)} · ×${s.clock.speed} · ${modes}</p>`;
    return [['status', status], ['forms', FORMS]];
  },
};
