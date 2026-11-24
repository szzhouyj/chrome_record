(() => {
  const site = location.hostname === 'meet.google.com' ? 'meet' : 'teams';
  const platformName = site === 'teams' ? 'Microsoft Teams' : 'Google Meet';
  let inCall = false, prompt;
  let session = null, collector, observer, timer, panel, saving = Promise.resolve();
  let dirty = false;
  const key = () => `transcript:${session.id}`;
  function status() {
    return { recording: !!session && !session.endedAt, count: collector?.entries.length || 0, joined: inCall };
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
    for (const row of MeetCaptions.rows(document, site)) {
      changed = collector.ingest(row, MeetCaptions.read(row, site)) || changed;
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
    observer?.disconnect();
    capture();
    session.endedAt = new Date().toISOString();
    dirty = true;
    panel.querySelector('button').disabled = true;
    try { await save(); indicator(message); }
    catch (error) { indicator('Could not save. Keep this tab open and export the live transcript.'); throw error; }
    return status();
  }
  async function start() {
    if (status().recording) return status();
    if (!MeetCaptions.platform(location.href) || (site === 'teams' && !MeetCaptions.joined(document, site))) {
      throw new Error('Join a meeting in your browser first.');
    }
    if (site === 'meet' && document.getElementById('meet-audio-indicator')?.dataset.active === 'true') {
      throw new Error('Stop audio transcription before starting captions.');
    }
    panel?.remove();
    collector = new MeetCaptions.Collector();
    const current = session = { platform: site, id: crypto.randomUUID(), title: document.title, url: location.origin + location.pathname, startedAt: new Date().toISOString(), endedAt: null };
    panel = document.createElement('div');
    panel.id = 'meet-transcript-indicator';
    Object.assign(panel.style, { position: 'fixed', top: '16px', right: '16px', zIndex: '2147483647', background: '#172c27', color: 'white', padding: '14px', border: '2px solid #73e5b5', borderRadius: '12px', font: '14px system-ui', maxWidth: '350px', boxShadow: '0 4px 24px #0006' });
    const label = document.createElement('span');
    label.setAttribute('role', 'status');
    label.textContent = 'Checking live captions…';
    const button = document.createElement('button');
    button.textContent = 'Stop';
    Object.assign(button.style, { marginLeft: '12px', padding: '6px 12px', cursor: 'pointer' });
    button.onclick = () => stop().catch(() => {});
    panel.append(label, button);
    document.documentElement.append(panel);
    dirty = true;
    try { await save(); } catch (error) {
      current.endedAt = new Date().toISOString();
      if (session === current) {
        panel.querySelector('button').disabled = true;
        indicator('Storage unavailable. Transcription did not start.');
      }
      throw error;
    }
    if (session !== current || current.endedAt) return status();
    prompt?.remove();
    observer = new MutationObserver(records => {
      if (records.some(record => !panel.contains(record.target))) capture();
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    capture();
    if (!collector.entries.length) {
      const active = () => session === current && !current.endedAt && !collector.entries.length &&
        MeetCaptions.joined(document, site);
      if (active()) {
        void MeetCaptions.enable(document, site, active).then(enabled => {
          if (active()) indicator(enabled
            ? 'Saving live captions. Waiting for speech…'
            : `Turn on live captions in ${platformName} to start receiving text.`);
        });
      } else {
        indicator(`Turn on live captions in ${platformName} to start receiving text.`);
      }
    }
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
      audioPanel.dataset.active = String(message.recording === true);
      audioPanel.querySelector('span').textContent = String(message.text);
      audioPanel.querySelector('button').disabled = !message.recording;
      respond({ result: true }); return;
    }
    const actions = {
      STATUS: () => status(),
      START: () => start(),
      STOP: () => stop(),
      LIVE: () => session ? { ...session, entries: collector.entries } : null
    };
    if (!actions[message.type]) return;
    Promise.resolve().then(actions[message.type]).then(result => respond({ result }), error => respond({ error: error.message }));
    return true;
  });
  function showPrompt() {
    prompt = document.createElement('aside');
    prompt.id = 'meet-transcript-prompt';
    prompt.setAttribute('aria-label', 'Save meeting captions');
    Object.assign(prompt.style, {
      position: 'fixed', bottom: '88px', right: '16px', zIndex: '2147483647',
      boxSizing: 'border-box', width: 'min(320px, calc(100% - 32px))', padding: '16px',
      background: '#ffffff', color: '#202b37', border: '1px solid #d9dfe7',
      borderRadius: '12px', boxShadow: '0 8px 28px #17283d26', font: '14px/1.5 system-ui, sans-serif'
    });
    const question = document.createElement('p');
    question.textContent = 'Save live captions?';
    Object.assign(question.style, { margin: '0', fontWeight: '650', fontSize: '16px' });
    const hint = document.createElement('p');
    hint.textContent = 'Please let everyone know before you start transcribing.';
    Object.assign(hint.style, { margin: '5px 0 14px', color: '#48566a' });
    const accept = document.createElement('button');
    accept.textContent = 'Start saving captions';
    accept.onclick = () => {
      accept.disabled = true;
      void start().catch(error => {
        if (prompt?.isConnected) {
          hint.textContent = error.message;
          accept.disabled = false;
        }
      });
    };
    const dismiss = document.createElement('button');
    dismiss.textContent = 'Not now';
    dismiss.onclick = () => prompt.remove();
    const actions = document.createElement('div');
    Object.assign(actions.style, { display: 'flex', flexWrap: 'wrap', gap: '8px' });
    for (const button of [accept, dismiss]) {
      button.type = 'button';
      Object.assign(button.style, {
        padding: '7px 11px', border: '1px solid #ccd6e3', borderRadius: '7px',
        background: '#ffffff', color: '#23374d', font: '600 13px/1.5 system-ui, sans-serif',
        cursor: 'pointer'
      });
    }
    Object.assign(accept.style, { background: '#204a78', borderColor: '#204a78', color: '#ffffff' });
    actions.append(accept, dismiss);
    prompt.append(question, hint, actions);
    document.documentElement.append(prompt);
  }
  function checkMeeting() {
    const joined = !!MeetCaptions.platform(location.href) && MeetCaptions.joined(document, site);
    if (joined === inCall) return;
    inCall = joined;
    if (joined) {
      if (!status().recording && document.getElementById('meet-audio-indicator')?.dataset.active !== 'true') showPrompt();
    } else {
      prompt?.remove();
      if (status().recording) void stop().catch(() => {});
    }
  }
  checkMeeting();
  const meetingCheck = setInterval(checkMeeting, 500);
  // A reload never silently resumes recording. Each saved snapshot remains recoverable.
  window.addEventListener('pagehide', () => {
    clearInterval(meetingCheck);
    if (status().recording) {
      observer?.disconnect();
      session.endedAt = new Date().toISOString(); dirty = true;
      void save().catch(() => {});
    }
  });
})();
