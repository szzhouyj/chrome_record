(() => {
  let session = null, collector, observer, timer, panel, saving = Promise.resolve();
  let dirty = false;
  const key = () => `transcript:${session.id}`;
  function status() {
    return { recording: !!session && !session.endedAt, count: collector?.entries.length || 0 };
  }
  function indicator(message) {
    panel.querySelector('span').textContent = message;
  }
  function save() {
    clearTimeout(timer);
    timer = null;
    if (!dirty) return saving;
    dirty = false;
    const snapshot = structuredClone({ ...session, entries: collector.entries });
    const storageKey = key();
    saving = saving.catch(() => {}).then(() => chrome.storage.local.set({ [storageKey]: snapshot }));
    return saving;
  }
  function capture() {
    let changed = false;
    for (const row of MeetCaptions.rows(document)) {
      changed = collector.ingest(row, MeetCaptions.read(row)) || changed;
    }
    if (changed) {
      dirty = true;
      indicator(`Transcribing · ${collector.entries.length} captions · saved on this device`);
      if (!timer) timer = setTimeout(async () => {
        timer = null;
        try { await save(); } catch { await stop('Storage full or unavailable. Export your saved transcript.').catch(() => {}); }
      }, 750);
    }
  }
  async function stop(message = 'Transcript saved. You can export it from the extension.') {
    if (!status().recording) return status();
    observer.disconnect();
    capture();
    session.endedAt = new Date().toISOString();
    dirty = true;
    panel.querySelector('button').disabled = true;
    try { await save(); indicator(message); }
    catch (error) { indicator('Could not save. Keep this tab open and export the live transcript.'); throw error; }
    return status();
  }
  async function start(consent) {
    if (status().recording) return status();
    if (!consent) throw new Error('Confirm participant consent before starting.');
    if (!/^\/[a-z]{3}-[a-z]{4}-[a-z]{3}\/?$/.test(location.pathname)) throw new Error('Open a Google Meet meeting first.');
    panel?.remove();
    collector = new MeetCaptions.Collector();
    session = { id: crypto.randomUUID(), title: document.title, url: location.href.split('?')[0], startedAt: new Date().toISOString(), endedAt: null };
    panel = document.createElement('div');
    panel.id = 'meet-transcript-indicator';
    Object.assign(panel.style, { position: 'fixed', top: '16px', right: '16px', zIndex: '2147483647', background: '#172c27', color: 'white', padding: '14px', border: '2px solid #73e5b5', borderRadius: '12px', font: '14px system-ui', maxWidth: '350px', boxShadow: '0 4px 24px #0006' });
    const label = document.createElement('span');
    label.setAttribute('role', 'status');
    label.textContent = 'Transcribing · turn on Meet captions (CC). Inform all participants.';
    const button = document.createElement('button');
    button.textContent = 'Stop';
    Object.assign(button.style, { marginLeft: '12px', padding: '6px 12px', cursor: 'pointer' });
    button.onclick = () => stop().catch(() => {});
    panel.append(label, button);
    document.documentElement.append(panel);
    dirty = true;
    try { await save(); } catch (error) { session.endedAt = new Date().toISOString(); indicator('Storage unavailable. Transcription did not start.'); throw error; }
    observer = new MutationObserver(records => {
      if (records.some(record => !panel.contains(record.target))) capture();
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    capture();
    return status();
  }
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (message.type === 'AUDIO_INDICATOR') {
      let audioPanel = document.getElementById('meet-audio-indicator');
      if (!audioPanel) {
        audioPanel = document.createElement('div'); audioPanel.id = 'meet-audio-indicator';
        Object.assign(audioPanel.style, { position: 'fixed', top: '16px', right: '16px', zIndex: '2147483647', background: '#172c27', color: 'white', padding: '14px', border: '2px solid #73e5b5', borderRadius: '12px', font: '14px system-ui', maxWidth: '350px' });
        const label = document.createElement('span'); label.setAttribute('role', 'status');
        const button = document.createElement('button'); button.textContent = 'Stop audio'; button.style.marginLeft = '12px';
        button.onclick = () => chrome.runtime.sendMessage({ target: 'audio-recorder', type: 'STOP', tabId: message.tabId }).catch(() => {});
        audioPanel.append(label, button); document.documentElement.append(audioPanel);
      }
      audioPanel.querySelector('span').textContent = String(message.text);
      audioPanel.querySelector('button').disabled = !message.recording;
      respond({ result: true }); return;
    }
    const actions = {
      STATUS: () => status(),
      START: () => start(message.consent === true),
      STOP: () => stop(),
      LIVE: () => session ? { ...session, entries: collector.entries } : null
    };
    if (!actions[message.type]) return;
    Promise.resolve().then(actions[message.type]).then(result => respond({ result }), error => respond({ error: error.message }));
    return true;
  });
  // A reload never silently resumes recording. Each saved snapshot remains recoverable.
  window.addEventListener('pagehide', () => {
    if (status().recording) {
      observer.disconnect();
      session.endedAt = new Date().toISOString(); dirty = true;
      void save().catch(() => {});
    }
  });
})();
