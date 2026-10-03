import * as F from '../format.js';

export default {
  title: 'Discord mirror',
  meta: () => 'read-only',
  render(s) {
    const today = s.clock.date;
    const chans = Object.keys(s.messages);
    return `<div class="discord">${chans.map((c) => `
      <div class="channel">
        <h3 class="channel-name">#${F.esc(c)}</h3>
        <ol class="messages" data-autoscroll>${s.messages[c].map((m) => `
          <li class="msg${m.bot ? ' is-bot' : ''}" data-key="msg-${F.esc(m.id)}">
            <div class="msg-head"><span class="msg-author">${F.esc(m.author)}</span>${m.bot ? '<span class="bot-tag">BOT</span>' : ''}<span class="msg-time">${F.time(m.at, today)}</span></div>
            <div class="msg-text">${F.linkifyNeeds(m.text)}</div>
          </li>`).join('') || '<li class="empty">No messages yet.</li>'}</ol>
      </div>`).join('')}</div>`;
  },
};
