const $ = id => document.getElementById(id);
const targetTabId = Number(new URLSearchParams(location.search).get('tab'));
const endpoint = 'http://127.0.0.1:8765';
let meeting, recorderTab, session, state = 'idle', starting = false;
let audioContext, worklet, tabStream, micStream, queue = [], processing = null, stopping = null;
let offsetSeconds = 0, flushResolve, lastSave = Promise.resolve();
function controls() {
  const busy = starting || !!processing || ['starting', 'recording', 'stopping'].includes(state);
  $('start').disabled = !meeting || busy || !$('consent').checked || !$('token').value.trim();
  $('stop').disabled = state !== 'recording';
  for (const id of ['token', 'microphone', 'consent']) $(id).disabled = busy;
  $('txt').disabled = $('json').disabled = !session;
}
async function indicator(text) {
  await chrome.tabs.sendMessage(targetTabId, { type: 'AUDIO_INDICATOR', text, recording: ['starting', 'recording'].includes(state), tabId: targetTabId }).catch(() => {});
}
function show(text) { $('status').textContent = text; controls(); void indicator(text); }
function save() {
  const snapshot = structuredClone(session);
  const storageKey = `transcript:${session.id}`;
  lastSave = lastSave.catch(() => {}).then(() => chrome.storage.local.set({ [storageKey]: snapshot }));
  return lastSave;
}
function release() {
  for (const stream of [tabStream, micStream]) stream?.getTracks().forEach(track => track.stop());
  tabStream = micStream = null;
  worklet?.disconnect(); worklet = null;
  void audioContext?.close().catch(() => {}); audioContext = null;
}
async function fail(error) {
  if (state === 'error') { release(); return; }
  state = 'error'; release(); queue = []; flushResolve?.(); flushResolve = null;
  const message = `${error.message} Unprocessed audio was discarded. Export the available transcript.`;
  $('error').textContent = message;
  if (session) { session.endedAt = new Date().toISOString(); session.error = message; await save().catch(() => {}); }
  show('Audio capture stopped after an error.');
}
async function request(path, options = {}, timeout = 120000) {
  const response = await fetch(`${endpoint}${path}`, {
    ...options, headers: { ...options.headers, Authorization: `Bearer ${$('token').value.trim()}` },
    signal: AbortSignal.timeout(timeout)
  });
  if (!response.ok) throw new Error(`Local Whisper service returned ${response.status}. Check its terminal and access token.`);
  return response.json();
}
function enqueue(samples) {
  if (!['recording', 'stopping'].includes(state)) return;
  if (queue.length >= 3) { void fail(new Error('Whisper could not keep up. Try a smaller model or reduce other workloads.')); return; }
  const sampleRate = audioContext.sampleRate;
  const offset = offsetSeconds; offsetSeconds += samples.length / sampleRate;
  queue.push({ samples, sampleRate, offset });
  if (!processing) processing = drain().catch(fail).finally(() => { processing = null; controls(); });
}
async function drain() {
  while (queue.length && state !== 'error') {
    const item = queue.shift();
    const result = await request('/transcribe', { method: 'POST', headers: { 'Content-Type': 'audio/wav' }, body: MeetAudio.wav(item.samples, item.sampleRate) });
    if (state === 'error') return;
    if (!Array.isArray(result.segments)) throw new Error('Invalid transcription response.');
    for (const segment of result.segments) {
      if (typeof segment.text !== 'string' || !Number.isFinite(segment.start) || segment.start < 0) throw new Error('Invalid transcription segment.');
      if (!segment.text.trim()) continue;
      const at = new Date(Date.parse(session.startedAt) + (item.offset + segment.start) * 1000).toISOString();
      session.entries.push({ at, speaker: 'Speaker unknown', text: segment.text.trim() });
      const paragraph = document.createElement('p');
      paragraph.textContent = `[${new Date(at).toLocaleTimeString()}] ${segment.text.trim()}`;
      $('transcript').append(paragraph);
    }
    await save();
    show(`${state === 'stopping' ? 'Finishing' : 'Recording tab audio'} · ${session.entries.length} segments saved · ${queue.length} windows waiting`);
  }
}
async function start() {
  if (starting || processing || ['recording', 'stopping'].includes(state)) return;
  if (!$('consent').checked) throw new Error('Confirm participant consent before recording.');
  if (!$('token').value.trim()) throw new Error('Paste the local service access token.');
  starting = true; state = 'starting'; session = null; controls(); $('error').textContent = ''; $('transcript').replaceChildren();
  show('Starting audio capture · connecting to local Whisper…');
  try {
    const current = await chrome.tabs.get(targetTabId);
    if (!/^https:\/\/meet\.google\.com\/[a-z]{3}-[a-z]{4}-[a-z]{3}(?:[/?#]|$)/.test(current.url || '')) throw new Error('The original Meet tab is no longer available. Reopen the recorder from Meet.');
    const caption = await chrome.tabs.sendMessage(targetTabId, { type: 'STATUS' }).catch(() => null);
    if (caption?.result?.recording) throw new Error('Stop the caption transcript before starting audio.');
    // Consume this ID immediately, before waiting for health checks or mic prompts.
    const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId });
    tabStream = await navigator.mediaDevices.getUserMedia({ audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } }, video: false });
    audioContext = new AudioContext({ sampleRate: 16000 });
    const tabSource = audioContext.createMediaStreamSource(tabStream);
    // Restore playback before waiting for the service or mic prompt.
    tabSource.connect(audioContext.destination);
    await audioContext.resume();
    const health = await request('/health', {}, 5000);
    if (!health.ready) throw new Error('Local Whisper service is not ready.');
    if ($('microphone').checked) micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
    if (state !== 'starting') { release(); return; }
    const mix = audioContext.createGain(); mix.channelCount = 1; mix.channelCountMode = 'explicit';
    tabSource.connect(mix);
    if (micStream) audioContext.createMediaStreamSource(micStream).connect(mix);
    await audioContext.audioWorklet.addModule('pcm-worklet.js');
    worklet = new AudioWorkletNode(audioContext, 'pcm-windows', { channelCount: 1, channelCountMode: 'explicit' });
    mix.connect(worklet);
    const silent = audioContext.createGain(); silent.gain.value = 0;
    worklet.connect(silent).connect(audioContext.destination);
    offsetSeconds = 0; queue = []; stopping = null;
    session = { id: crypto.randomUUID(), title: meeting.title, url: meeting.url.split('?')[0], source: 'tab-audio', model: health.model, backend: health.backend || 'unknown', device: health.device || 'unknown', microphone: !!micStream, tabId: targetTabId, recorderTabId: recorderTab.id, startedAt: new Date().toISOString(), endedAt: null, entries: [] };
    await save();
    worklet.port.onmessage = event => {
      if (event.data.type === 'pcm') enqueue(event.data.samples);
      if (event.data.type === 'flushed') { flushResolve?.(); flushResolve = null; }
    };
    for (const stream of [tabStream, micStream]) stream?.getTracks().forEach(track => track.addEventListener('ended', () => { void stop().catch(fail); }));
    state = 'recording'; show(`Recording tab audio · ${health.backend === 'mlx' ? 'MLX · ' : ''}Whisper ${health.model}${health.device ? ` · ${health.device}` : ''} · captions can stay off`);
  } catch (error) { await fail(error); }
  finally { starting = false; controls(); }
}
async function finish() {
  state = 'stopping'; show('Capture stopping · finishing the remaining audio…');
  if (worklet) {
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('Final audio flush timed out.')), 2000);
      flushResolve = () => { clearTimeout(timeout); resolve(); };
      worklet.port.postMessage('flush');
    });
  }
  release();
  if (processing) await processing;
  if (state === 'error') return;
  session.endedAt = new Date().toISOString(); await save();
  state = 'stopped'; show(`Transcript saved · ${session.entries.length} segments`);
}
function stop() {
  if (stopping) return stopping;
  if (state === 'starting') { state = 'error'; release(); show('Capture cancelled.'); return Promise.resolve(); }
  if (state !== 'recording') return Promise.resolve();
  stopping = finish().catch(fail); return stopping;
}
$('start').onclick = () => { void start().catch(fail); };
$('stop').onclick = () => { void stop().catch(fail); };
for (const id of ['consent', 'token']) $(id).oninput = controls;
for (const format of ['txt', 'json']) $(format).onclick = () => MeetAudio.download(session, format);
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message.target !== 'audio-recorder' || message.tabId !== targetTabId) return;
  const actions = {
    STATUS: () => ({ recording: ['starting', 'recording', 'stopping'].includes(state), count: session?.entries.length || 0, recorderTabId: recorderTab?.id, sessionId: session?.id }),
    STOP: () => stop(), LIVE: () => session
  };
  if (!actions[message.type]) return;
  Promise.resolve().then(actions[message.type]).then(result => respond({ result }), error => respond({ error: error.message }));
  return true;
});
chrome.tabs.onRemoved.addListener(id => { if (id === targetTabId) void stop().catch(fail); });
chrome.tabs.onUpdated.addListener((id, change) => { if (id === targetTabId && change.status === 'loading') void stop().catch(fail); });
window.addEventListener('beforeunload', event => {
  if (['recording', 'stopping'].includes(state)) { event.preventDefault(); event.returnValue = ''; }
});
window.addEventListener('pagehide', () => { release(); });
(async () => {
  if (!Number.isInteger(targetTabId) || targetTabId <= 0) throw new Error('Open the audio recorder from the Meet Transcript popup.');
  meeting = await chrome.tabs.get(targetTabId); recorderTab = await chrome.tabs.getCurrent();
  $('meeting').textContent = meeting.title || 'Google Meet'; controls();
})().catch(error => { $('error').textContent = error.message; });
