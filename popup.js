const $ = id => document.getElementById(id);
let tab, recording = false, activeMode = null, busy = false;
async function send(type, extra = {}, mode = $('mode').value) {
  if (!tab?.id) throw new Error('Open a Meet meeting, then reopen this popup.');
  const reply = mode === 'audio'
    ? await chrome.runtime.sendMessage({ target: 'audio-recorder', tabId: tab.id, type, ...extra })
    : await chrome.tabs.sendMessage(tab.id, { type, ...extra });
  if (!reply) throw new Error('Open the audio recorder first.');
  if (reply.error) throw new Error(reply.error);
  return reply.result;
}
function controls() {
  const audio = $('mode').value === 'audio';
  $('caption-consent').hidden = audio;
  $('mode').disabled = recording || busy;
  $('mode-hint').textContent = audio ? 'Uses local Whisper. Opens a recorder tab with setup and consent controls.' : 'Turn on Google Meet captions (CC). Only displayed captions are captured.';
  $('start').textContent = audio ? 'Open audio recorder' : 'Start transcript';
  $('start').disabled = !tab || recording || busy || (!audio && !$('consent').checked);
  $('stop').disabled = !recording || busy; $('live').disabled = !tab || busy;
}
async function render() {
  const all = await chrome.storage.local.get(null);
  const sessions = Object.entries(all).filter(([key]) => key.startsWith('transcript:')).sort((a, b) => b[1].startedAt.localeCompare(a[1].startedAt));
  $('sessions').replaceChildren();
  if (!sessions.length) $('sessions').textContent = 'Your first transcript will appear here.';
  for (const [key, session] of sessions) {
    const audioLive = session.source === 'tab-audio' && !session.endedAt
      ? await chrome.runtime.sendMessage({ target: 'audio-recorder', tabId: session.tabId, type: 'STATUS' }).catch(() => null) : null;
    const article = document.createElement('article');
    const title = document.createElement('strong'); title.textContent = session.title || 'Google Meet';
    const detail = document.createElement('small'); detail.textContent = `${new Date(session.startedAt).toLocaleString()} · ${session.entries.length} entries · ${session.source === 'tab-audio' ? 'Whisper' : 'captions'}`;
    const actions = document.createElement('div');
    for (const [label, action] of [
      ['Export TXT', () => MeetAudio.download(session, 'txt')],
      ['Export JSON', () => MeetAudio.download(session, 'json')],
      ['Delete', async () => { if (confirm('Permanently delete this saved transcript?')) { await chrome.storage.local.remove(key); await render(); } }]
    ]) {
      const button = document.createElement('button'); button.textContent = label;
      button.disabled = label === 'Delete' && !session.endedAt && (session.source === 'tab-audio'
        ? audioLive?.result?.recording && audioLive.result.sessionId === session.id : recording);
      button.onclick = () => Promise.resolve().then(action).catch(error => $('error').textContent = error.message);
      actions.append(button);
    }
    article.append(title, detail, actions); $('sessions').append(article);
  }
}
async function refresh() {
  const [caption, audio] = await Promise.all([send('STATUS', {}, 'captions').catch(() => null), send('STATUS', {}, 'audio').catch(() => null)]);
  activeMode = audio?.recording ? 'audio' : caption?.recording ? 'captions' : null;
  recording = !!activeMode;
  if (activeMode) $('mode').value = activeMode;
  $('status').textContent = recording ? `Transcribing ${activeMode === 'audio' ? 'audio' : 'captions'} · ${(activeMode === 'audio' ? audio : caption).count} entries` : !tab ? 'Open a Google Meet meeting' : 'Ready when you are';
  controls(); await render();
}
$('start').onclick = async () => {
  busy = true; controls(); $('error').textContent = '';
  try {
    if ($('mode').value === 'audio') {
      const existing = await send('STATUS', {}, 'audio').catch(() => null);
      if (existing?.recorderTabId) await chrome.tabs.update(existing.recorderTabId, { active: true });
      else await chrome.tabs.create({ url: chrome.runtime.getURL(`recorder.html?tab=${tab.id}`) });
    } else {
      const audio = await send('STATUS', {}, 'audio').catch(() => null);
      if (audio?.recording) throw new Error('Stop audio transcription before starting captions.');
      await send('START', { consent: $('consent').checked }, 'captions');
    }
    await refresh();
  } catch (error) { $('error').textContent = error.message; }
  finally { busy = false; controls(); }
};
$('stop').onclick = async () => {
  busy = true; controls(); $('error').textContent = '';
  try { await send('STOP', {}, activeMode); await refresh(); }
  catch (error) { $('error').textContent = error.message; }
  finally { busy = false; controls(); }
};
$('consent').onchange = controls;
$('mode').onchange = () => { controls(); };
$('live').onclick = async () => {
  try { const session = await send('LIVE'); if (session) MeetAudio.download(session); else $('error').textContent = 'No transcript in this tab yet.'; }
  catch (error) { $('error').textContent = error.message; }
};
chrome.storage.onChanged.addListener(() => { void refresh().catch(error => $('error').textContent = error.message); });
(async () => {
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!/^https:\/\/meet\.google\.com\/[a-z]{3}-[a-z]{4}-[a-z]{3}(?:[/?#]|$)/.test(tab?.url || '')) tab = null;
  await refresh();
})().catch(error => $('error').textContent = error.message);
