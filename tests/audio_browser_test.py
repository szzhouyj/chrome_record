"""Real Chromium Web Audio/worklet tests; Chrome capture APIs and Whisper are fixtures."""
import io
import json
import wave
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(ROOT)))
threading.Thread(target=server.serve_forever, daemon=True).start()
MOCK = """
window.data = {}; window.failWrites = false; window.streams = []; window.constraints = [];
window.messageListeners = []; window.updatedListeners = []; window.removedListeners = [];
window.chrome = {
 storage: {local: { async set(values) { if (window.failWrites) throw Error('Storage full'); Object.assign(window.data, structuredClone(values)); }}},
 runtime: {onMessage: {addListener(fn) { window.messageListeners.push(fn); }}},
 tabs: {
  async get(id) { return { id, url: 'https://meet.google.com/abc-defg-hij', title: 'Audio test meeting' }; },
  async getCurrent() { return { id: 99 }; },
  async sendMessage(id, msg) { return { result: msg.type === 'STATUS' ? {recording: false} : true }; },
  onUpdated: { addListener(fn) { window.updatedListeners.push(fn); }},
  onRemoved: { addListener(fn) { window.removedListeners.push(fn); }}
 }, tabCapture: { async getMediaStreamId() { return 'synthetic'; }}
};
Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {value: async constraints => {
 window.constraints.push(constraints);
 const context = new AudioContext(); const oscillator = context.createOscillator();
 oscillator.frequency.value = 440;
 const destination = context.createMediaStreamDestination();
 oscillator.connect(destination); oscillator.start(); await context.resume();
 window.streams.push(destination.stream); return destination.stream;
}});
void 0;
"""

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
                              args=['--no-sandbox', '--autoplay-policy=no-user-gesture-required'])
    calls = []

    def page_fixture(fail_health=False, fail_audio=False):
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def service(route):
            if route.request.method == 'OPTIONS':
                route.fulfill(status=204, headers={'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'Authorization, Content-Type', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS'})
                return
            assert route.request.headers['authorization'] == 'Bearer test-token'
            if route.request.url.endswith('/health'):
                route.fulfill(status=503 if fail_health else 200, json={'ready': True, 'model': 'large-v3-turbo', 'backend': 'mlx', 'device': 'Apple GPU · Metal'}, headers={'Access-Control-Allow-Origin': '*'})
            else:
                with wave.open(io.BytesIO(route.request.post_data_buffer)) as wav:
                    assert wav.getnchannels() == 1 and wav.getsampwidth() == 2
                    duration = wav.getnframes() / wav.getframerate()
                    assert 0 < duration <= 10.01
                    calls.append(duration)
                route.fulfill(status=503 if fail_audio else 200, json={'segments': [{'start': 0.1, 'end': 0.5, 'text': '<script>not code</script>'}]}, headers={'Access-Control-Allow-Origin': '*'})

        page.route('http://127.0.0.1:8765/**', service)
        page.add_init_script(MOCK)
        page.goto(f'http://127.0.0.1:{server.server_port}/recorder.html?tab=1')
        page.wait_for_function("document.querySelector('#meeting').textContent === 'Audio test meeting'")
        assert page.locator('#start').is_disabled()
        page.locator('#token').fill('test-token')
        assert page.locator('#start').is_disabled()
        page.locator('#consent').check()
        assert page.locator('#start').is_enabled()
        return page, errors

    page, errors = page_fixture()
    page.locator('#microphone').check()
    page.locator('#start').click()
    page.wait_for_function("document.querySelector('#status').textContent.includes('Recording tab audio') || document.querySelector('#error').textContent")
    assert page.locator('#error').text_content() == '', page.locator('#error').text_content()
    assert 'MLX · Whisper large-v3-turbo · Apple GPU' in page.locator('#status').text_content()
    assert page.evaluate('window.constraints.length') == 2
    page.wait_for_function('Object.values(window.data)[0]?.entries.length === 1', timeout=20000)
    page.wait_for_timeout(300)
    page.locator('#stop').click()
    page.wait_for_function("document.querySelector('#status').textContent.includes('Transcript saved')")
    saved = page.evaluate('Object.values(window.data)[0]')
    assert saved['endedAt'] and saved['source'] == 'tab-audio' and saved['microphone']
    assert saved['backend'] == 'mlx' and saved['model'] == 'large-v3-turbo'
    assert len(saved['entries']) == 2 and calls[0] == 10 and 0 < calls[1] < 10
    assert saved['entries'][1]['at'] > saved['entries'][0]['at']
    assert page.evaluate("window.streams.every(s => s.getTracks().every(t => t.readyState === 'ended'))")
    assert page.locator('#transcript script').count() == 0
    with page.expect_download() as download:
        page.locator('#txt').click()
    assert '<script>not code</script>' in Path(download.value.path()).read_text()
    assert errors == [], errors
    page.close()

    # An unavailable service releases the captured track immediately.
    page, errors = page_fixture(fail_health=True)
    page.locator('#start').click()
    page.wait_for_function("document.querySelector('#error').textContent.includes('503')")
    assert page.evaluate("window.streams.every(s => s.getTracks().every(t => t.readyState === 'ended'))")
    assert errors == [], errors
    page.close()

    # Stop flushes sub-window audio; failed transcription still ends capture.
    page, errors = page_fixture(fail_audio=True)
    page.locator('#start').click()
    page.wait_for_function("document.querySelector('#status').textContent.includes('Recording tab audio')")
    page.wait_for_timeout(250)
    page.locator('#stop').click()
    page.wait_for_function("document.querySelector('#error').textContent.includes('503')")
    assert page.evaluate('Object.values(window.data)[0].endedAt')
    assert page.evaluate("window.streams.every(s => s.getTracks().every(t => t.readyState === 'ended'))")
    assert errors == [], errors
    page.close()

    # Save failures retain the in-memory transcript for export.
    page, errors = page_fixture()
    page.locator('#start').click()
    page.wait_for_function("document.querySelector('#status').textContent.includes('Recording tab audio')")
    page.evaluate('window.failWrites = true')
    page.wait_for_timeout(250)
    page.locator('#stop').click()
    page.wait_for_function("document.querySelector('#error').textContent.includes('Storage full')")
    with page.expect_download() as download:
        page.locator('#json').click()
    assert len(json.loads(Path(download.value.path()).read_text())['entries']) == 1
    assert page.evaluate("window.streams.every(s => s.getTracks().every(t => t.readyState === 'ended'))")
    assert errors == [], errors
    page.close()

    # Navigating away from the target meeting stops capture.
    page, errors = page_fixture()
    page.locator('#start').click()
    page.wait_for_function("document.querySelector('#status').textContent.includes('Recording tab audio')")
    page.wait_for_timeout(250)
    page.evaluate("window.updatedListeners[0](1, {status: 'loading'})")
    page.wait_for_function("document.querySelector('#status').textContent.includes('Transcript saved')")
    assert page.evaluate("window.streams.every(s => s.getTracks().every(t => t.readyState === 'ended'))")
    assert errors == [], errors
    browser.close()
    server.shutdown(); server.server_close()
    print('PASS: consent/token gates, real PCM worklet, 10s WAV windows, mic mix, final flush, timestamps, export, service failure, storage recovery, and navigation stop')
