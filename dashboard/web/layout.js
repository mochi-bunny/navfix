// Routes, grouping and navigation: edit this file to regroup views.
//   views    panels from ./views/index.js, in order
//   layout   becomes .layout-<name> on the page grid (placement lives in base.css)
//   bare     no panel chrome (the view draws its own cards)
//   chrome   false hides the rail, top bar and footer (the scheduler-only page)
//   rail     show in the left icon rail; railSep draws a divider before it; railBottom pins it to the bottom
//   badge    'changes' | 'fleet' → unseen count on the rail icon
//   key      keyboard shortcut while recording
// Each view's `more` link (top right of its panel) is its deep dive.

export const ROUTES = [
  // Surveillance first
  { path: '/',              title: 'Command center', icon: 'grid',     views: ['feed', 'fleetstatus', 'schedside', 'robots', 'gates', 'opslog'], layout: 'command', rail: true, key: '1' },
  { path: '/live',          title: 'Live feeds',    icon: 'camera',   views: ['feed', 'robots'], layout: 'live', rail: true, key: 'l' },
  { path: '/fleet',         title: 'Fleet watch',   icon: 'radar',    views: ['incorporated', 'warehouse', 'commissioning', 'reliability'], layout: 'fleet', rail: true, key: 'f' },
  { path: '/fleet-updates', title: 'Fleet updates', icon: 'robot',    views: ['fleetupdates'],  layout: 'single', rail: true, badge: 'fleet', key: 'u' },
  { path: '/needs',         title: 'Needs',         icon: 'ticket',   views: ['needs'],         layout: 'single', rail: true, key: '3' },

  // Scheduler
  { path: '/plan',          title: 'Scheduler',     icon: 'list',     views: ['simple'],        layout: 'simple', bare: true, chrome: false, rail: true, railSep: true, key: 'm' },
  { path: '/day',           title: 'My day',        icon: 'user',     views: ['myday'],         layout: 'myday', bare: true, rail: true, key: 'd' },
  { path: '/calendar',      title: 'Team',          icon: 'calendar', views: ['calendar'],      layout: 'single', rail: true, key: '2' },
  { path: '/changes',       title: 'Changes',       icon: 'history',  views: ['changes'],       layout: 'single', rail: true, badge: 'changes', key: 'h' },

  // Ops & proof
  { path: '/ops',           title: 'Ops',           icon: 'chat',     views: ['opslog', 'discord'], layout: 'ops', rail: true, railSep: true, key: '9' },
  { path: '/proof',         title: 'Proof',         icon: 'shield',   views: ['proof'],         layout: 'single', rail: true, key: 'p' },
  { path: '/settings',      title: 'Settings',      icon: 'gear',     views: ['settings'],      layout: 'single', railBottom: true, key: 's' },

  // Direct URLs (not in the rail) so any single view can be shown full screen.
  { path: '/needs/:id',     title: 'Need',          views: ['need'],          layout: 'single' },
  { path: '/need',          title: 'Need detail',   views: ['need'],          layout: 'single', key: '4' },
  { path: '/reliability',   title: 'Reliability',   views: ['reliability'],   layout: 'single', key: '5' },
  { path: '/commissioning', title: 'Commissioning', views: ['commissioning'], layout: 'single', key: '6' },
  { path: '/warehouse',     title: 'Warehouse',     views: ['warehouse'],     layout: 'single', key: '7' },
  { path: '/leg',           title: 'Live leg',      views: ['leg'],           layout: 'single', key: '8' },
  { path: '/ops-log',       title: 'Ops log',       views: ['opslog'],        layout: 'single' },
  { path: '/discord',       title: 'Discord',       views: ['discord'],       layout: 'single', key: '0' },
  { path: '/overview',      title: 'Overview',      views: ['calendar', 'needs', 'leg', 'opslog'], layout: 'overview' },
  { path: '/control',       title: 'Demo control',  views: ['control'],       layout: 'single', key: 'c' },
];

// Shown on every page.
export const ALWAYS = { clock: true, localProofStrip: true };
