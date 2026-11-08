/* Meet's caption DOM is undocumented. Keep selectors isolated for maintenance. */
(() => {
  const normalize = text => text.replace(/\s+/g, ' ').trim();
  const rows = root => [...root.querySelectorAll('.nMcdL, [data-meet-transcript-caption]')];
  const read = row => {
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
  globalThis.MeetCaptions = { rows, read, Collector };
})();
