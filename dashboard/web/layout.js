// Routes, grouping and navigation: edit this file to regroup views.
//   views   panels from ./views/index.js, in order
//   layout  becomes .layout-<name> on the page grid (placement lives in base.css)
//   bare    no panel chrome (the view draws its own cards)
//   rail    show in the left icon rail; railBottom pins it to the bottom
//   badge   'changes' | 'fleet' → unseen count on the rail icon
//   key     keyboard shortcut while recording
//   chrome  false hides the rail, top bar and footer (the simple plan page)
//
// "/" shows the simple plan, or the full My day page if the viewer picked "Full view" from its menu.

export const ROUTES = [
  { path: '/',              title: 'Plan',          views: ['simple'],        layout: 'simple', bare: true, chrome: false, key: 'm' },
  { path: '/day',           title: 'My day',        icon: 'home',     views: ['myday'],         layout: 'myday', bare: true, rail: true, key: '1' },
  { path: '/calendar',      title: 'Team',          icon: 'calendar', views: ['calendar'],      layout: 'single', rail: true, key: '2' },
  { path: '/needs',         title: 'Needs',         icon: 'ticket',   views: ['needs'],         layout: 'single', rail: true, key: '3' },
  { path: '/fleet',         title: 'Fleet watch',   icon: 'radar',    views: ['incorporated', 'warehouse', 'commissioning', 'reliability'], layout: 'fleet', rail: true, key: 'f' },
  { path: '/fleet-updates', title: 'Fleet updates', icon: 'robot',    views: ['fleetupdates'],  layout: 'single', rail: true, badge: 'fleet', key: 'u' },
  { path: '/changes',       title: 'Changes',       icon: 'history',  views: ['changes'],       layout: 'single', rail: true, badge: 'changes', key: 'h' },
  { path: '/ops',           title: 'Ops',           icon: 'chat',     views: ['opslog', 'discord'], layout: 'ops', rail: true, key: '9' },
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
