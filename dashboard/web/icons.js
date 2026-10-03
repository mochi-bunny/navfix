// Inline stroke icons (no icon fonts or CDNs).
const P = {
  home: 'M3 11l9-8 9 8 M5 9.5V20h5v-6h4v6h5V9.5',
  calendar: 'M4 6h16v14H4z M4 10h16 M8 3v4 M16 3v4',
  ticket: 'M3 7h18v3a2 2 0 0 0 0 4v3H3v-3a2 2 0 0 0 0-4z M14 7v10',
  radar: 'M12 21a9 9 0 1 1 9-9 M12 17a5 5 0 1 1 5-5 M12 12l7-7 M12 12h.01',
  robot: 'M5 9h14v10H5z M12 5v4 M12 4h.01 M9 13h.01 M15 13h.01 M9 16h6 M3 13v3 M21 13v3',
  history: 'M3 12a9 9 0 1 0 3-6.7 M3 4v5h5 M12 7v5l3 2',
  chat: 'M4 5h16v11H9l-5 4z M8 9h8 M8 12h5',
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z M9 12l2 2 4-4',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.4 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7 7 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.4-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.2-.8.2-1.2z',
  bell: 'M18 9a6 6 0 0 0-12 0c0 7-3 8-3 8h18s-3-1-3-8 M13.7 21a2 2 0 0 1-3.4 0',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z M21 21l-4.3-4.3',
  route: 'M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M18 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M8 17h7a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h7',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7 M20 4v7h-7',
  edit: 'M4 20h4L19 9l-4-4L4 16z M13 7l4 4',
  pin: 'M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z M12 12a2 2 0 1 0 0-4 2 2 0 0 0 0 4z',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 7v5l3 2',
  close: 'M6 6l12 12 M18 6L6 18',
  check: 'M5 12l5 5 9-10',
  undo: 'M9 14L4 9l5-5 M4 9h10a6 6 0 0 1 0 12h-3',
  list: 'M8 6h12 M8 12h12 M8 18h12 M4 6h.01 M4 12h.01 M4 18h.01',
  grid: 'M4 4h7v7H4z M13 4h7v7h-7z M4 13h7v7H4z M13 13h7v7h-7z',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M4 21a8 8 0 0 1 16 0',
  chevron: 'M9 6l6 6-6 6',
  camera: 'M3 8h4l2-3h6l2 3h4v11H3z M12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  prev: 'M15 6l-6 6 6 6',
  next: 'M9 6l6 6-6 6',
  expand: 'M4 9V4h5 M20 9V4h-5 M4 15v5h5 M20 15v5h-5',
  box:'M3 7l9-4 9 4v10l-9 4-9-4z M3 7l9 4 9-4 M12 11v10',
};

export const icon = (name, cls = '') =>
  `<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${P[name] || P.grid}"/></svg>`;

// The logo tile: four rounded petals, like the reference UI.
export const logo = () =>
  `<svg class="logo" viewBox="0 0 24 24" aria-hidden="true"><g fill="currentColor"><circle cx="8" cy="8" r="4"/><circle cx="16" cy="8" r="4"/><circle cx="8" cy="16" r="4"/><circle cx="16" cy="16" r="4"/></g></svg>`;
