// View registry. Each view: { title, render(snapshot, ctx) -> html | [[part, html]], meta?(), init?() }
import myday from './myday.js';
import calendar from './calendar.js';
import needs from './needs.js';
import need from './need.js';
import reliability from './reliability.js';
import commissioning from './commissioning.js';
import warehouse from './warehouse.js';
import leg from './leg.js';
import opslog from './opslog.js';
import discord from './discord.js';
import proof from './proof.js';
import control from './control.js';
import changes from './changes.js';
import fleetupdates from './fleetupdates.js';
import incorporated from './incorporated.js';
import settings from './settings.js';
import simple from './simple.js';

export const VIEWS = {
  simple, myday, calendar, needs, need, reliability, commissioning, warehouse, leg, opslog, discord, proof, control,
  changes, fleetupdates, incorporated, settings,
};
