/* Meet's caption DOM is undocumented. Keep selectors isolated for maintenance. */
(() => {
  const normalize = text => text.replace(/\s+/g, ' ').trim();
  const meetPath = /^\/[a-z]{3}-[a-z]{4}-[a-z]{3}\/?$/;
  const teamsHosts = new Set(['teams.microsoft.com', 'teams.cloud.microsoft', 'teams.live.com']);
  function platform(url) {
    if (!url) return null;
    const { protocol, hostname, pathname } = new URL(url);
    if (protocol !== 'https:') return null;
    if (hostname === 'meet.google.com' && meetPath.test(pathname)) return 'meet';
    return teamsHosts.has(hostname) ? 'teams' : null;
  }
  function joined(root, site) {
    const selector = site === 'teams'
      ? 'button[data-tid="hangup-button"], button#hangup-button, button[aria-label="Leave"]'
      : 'button[aria-label="Leave call"], button[data-tooltip="Leave call"], button[jsname="CQylAd"]';
    return [...root.querySelectorAll(selector)].some(button => button.getClientRects().length && !button.disabled);
  }
  const controls = 'button, [role="button"], [role="menuitem"], [role="menuitemcheckbox"], [role="switch"]';

  function findControl(root, matches) {
    return [...root.querySelectorAll(controls)].find(control => {
      if (!control.getClientRects().length || control.disabled || control.getAttribute('aria-disabled') === 'true') return false;
      const labels = [control.getAttribute('aria-label'), control.getAttribute('title'), control.textContent];
      return matches(control, labels);
    });
  }

  async function enable(root, site, active) {
    const teams = site === 'teams';
    const on = teams ? /^(show|turn on) live captions$/i : /^(turn on|show) (live )?captions(?: \(c\))?$/i;
    const off = teams ? /^(hide|turn off) live captions$/i : /^(turn off|hide) (live )?captions(?: \(c\))?$/i;
    const captionToggle = /^(live )?captions$/i;
    const checked = control => control.getAttribute('aria-checked') ?? control.getAttribute('aria-pressed');
    const matches = (pattern, labels) => labels.some(label => label && pattern.test(normalize(label)));
    const enabled = () => !!findControl(root, (control, labels) =>
      matches(off, labels) || (matches(captionToggle, labels) && checked(control) === 'true')) ||
      (teams && [...root.querySelectorAll('[data-tid="closed-caption-renderer-wrapper"], [data-tid="closed-caption-v2-virtual-list-content"]')]
        .some(element => element.getClientRects().length));
    const start = () => findControl(root, (control, labels) =>
      matches(on, labels) || (matches(captionToggle, labels) && checked(control) === 'false'));

    let openedActions = false;
    let openedSubmenu = false;
    let clicked = false;
    for (let attempt = 0; attempt < 12 && active(); attempt++) {
      if (enabled()) return true;
      if (!clicked) {
        const button = start();
        if (button) {
          button.click();
          clicked = true;
        } else if (teams) {
          const submenu = findControl(root, (_control, labels) => matches(/^language and speech$/i, labels));
          if (submenu && !openedSubmenu) {
            if (submenu.getAttribute('aria-expanded') !== 'true') submenu.click();
            openedSubmenu = true;
          } else if (!openedActions && !openedSubmenu) {
            const more = findControl(root, (control, labels) =>
              control.id === 'more-actions-button' || matches(/^more actions$/i, labels));
            if (more) {
              if (more.getAttribute('aria-expanded') !== 'true') more.click();
              openedActions = true;
            }
          }
        }
      }
      await new Promise(resolve => setTimeout(resolve, 150));
    }
    return false;
  }

  const teamsRows = '[data-tid="closed-caption-chat-message"], .fui-ChatMessageCompact';
  const teamsText = '[data-tid="closed-caption-text"]';
  function rows(root, site) {
    if (site === 'teams') {
      return [...root.querySelectorAll(teamsText)].map(text => text.closest(teamsRows) || text);
    }
    return [...root.querySelectorAll('.nMcdL, [data-meet-transcript-caption]')];
  }
  const read = (row, site) => {
    if (site === 'teams') {
      const speaker = row.querySelector('[data-tid="author"], .ui-chat__message__author');
      const text = row.matches(teamsText) ? row : row.querySelector(teamsText);
      return { speaker: normalize(speaker?.textContent || 'Unknown speaker'), text: normalize(text?.textContent || '') };
    }
    const speaker = row.querySelector('.KcIKyf, .zs7s8d, [data-speaker]');
    const text = row.querySelector('.ygicle, .VbkSUe, [data-caption-text]');
    return { speaker: normalize(speaker?.textContent || 'Unknown speaker'), text: normalize(text?.textContent || '') };
  };
  class Collector {
    constructor() { this.entries = []; this.seen = new WeakMap(); }
    ingest(row, caption, at = new Date().toISOString()) {
      if (!caption.text) return;
      const prior = this.seen.get(row);
      if (prior && prior.speaker === caption.speaker && prior.text === caption.text) return;
      // A growing caption updates its existing entry; a replaced row starts a new one.
      if (prior && prior.speaker === caption.speaker && caption.text.startsWith(prior.text)) {
        prior.text = caption.text;
      } else {
        const entry = { ...caption, at };
        this.entries.push(entry);
        this.seen.set(row, entry);
      }
      return true;
    }
  }
  globalThis.MeetCaptions = { platform, joined, enable, rows, read, Collector };
})();
