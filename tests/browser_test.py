"""Integration checks in Chromium using deterministic Meet DOM + Chrome API fixtures."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
MOCK = """
window.data = {}; window.failWrites = false; window.listeners = [];
window.chrome = {
 storage: {local: {
   async set(values) { if (window.failWrites) throw Error('Storage quota exceeded'); Object.assign(window.data, structuredClone(values)); },
   async get() { return structuredClone(window.data); },
   async remove(key) { delete window.data[key]; }
 }, onChanged: {addListener(fn) {}}},
 runtime: {onMessage: {addListener(fn) { window.listeners.push(fn); }}},
 tabs: {async query() { return [{id: 1, url: 'https://meet.google.com/abc-defg-hij'}]; }, async sendMessage(id, msg) {return {result: {recording: false, count: 0}};}}
};
window.send = message => new Promise(resolve => window.listeners[0](message, {}, resolve));
void 0;
"""

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
    page = browser.new_page()
    page.route('https://meet.google.com/**', lambda route: route.fulfill(body='<html><head><title>Test meeting</title></head><body><div id="captions"></div></body></html>', content_type='text/html'))
    page.goto('https://meet.google.com/abc-defg-hij')
    page.evaluate(MOCK)
    for script in ['captions.js', 'content.js']:
        page.add_script_tag(path=str(ROOT / script))
    page.evaluate("""() => {
        document.body.insertAdjacentHTML('beforeend', '<button aria-label="Leave call">Leave</button><button id="cc" aria-label="Turn on captions">CC</button>');
        document.querySelector('#cc').onclick = event => {
            event.currentTarget.setAttribute('aria-label', 'Turn off captions');
            window.captionClicks = (window.captionClicks || 0) + 1;
        };
    }""")
    assert 'consent' in page.evaluate("send({type:'START'})")['error']
    assert page.evaluate("send({type:'START',consent:true})")['result']['recording']
    page.wait_for_function("window.captionClicks === 1")
    page.evaluate("document.querySelector('#captions').innerHTML = '<div class=\"nMcdL\"><div class=\"zs7s8d\">Alex</div><div class=\"ygicle\">Hello</div></div>'")
    page.wait_for_function("document.querySelector('#meet-transcript-indicator').textContent.includes('1 captions')")
    page.evaluate("document.querySelector('.ygicle').textContent = 'Hello everyone'")
    page.wait_for_timeout(850)
    saved = page.evaluate('Object.values(window.data)[0]')
    assert len(saved['entries']) == 1 and saved['entries'][0]['text'] == 'Hello everyone'
    page.evaluate("document.querySelector('.ygicle').textContent = 'Next sentence'")
    page.wait_for_function("document.querySelector('#meet-transcript-indicator').textContent.includes('2 captions')")
    page.evaluate("document.querySelector('.zs7s8d').textContent = 'Sam'; document.querySelector('.ygicle').textContent = '<script>bad()</script>'")
    page.wait_for_function("document.querySelector('#meet-transcript-indicator').textContent.includes('3 captions')")
    assert page.evaluate("send({type:'STOP'})")['result']['recording'] is False
    saved = page.evaluate('Object.values(window.data)[0]')
    assert saved['endedAt'] and len(saved['entries']) == 3 and saved['entries'][2]['speaker'] == 'Sam'
    page.evaluate("document.querySelector('.ygicle').textContent = 'After stop'")
    assert len(page.evaluate("send({type:'LIVE'})")['result']['entries']) == 3
    assert page.evaluate("send({type:'START',consent:true})")['result']['recording']
    page.wait_for_timeout(850)
    assert len(page.evaluate('Object.keys(window.data)')) == 2
    assert page.evaluate('window.captionClicks') == 1
    page.evaluate('window.failWrites = true')
    page.evaluate("document.querySelector('.ygicle').textContent = 'Storage failure'")
    page.wait_for_function("document.querySelector('#meet-transcript-indicator').textContent.includes('Could not save')")
    assert not page.evaluate("send({type:'STATUS'})")['result']['recording']
    assert page.evaluate("send({type:'LIVE'})")['result']['entries'][-1]['text'] == 'Storage failure'
    # A new content script instance never resumes saved sessions.
    page.reload(); page.evaluate(MOCK)
    page.evaluate('(saved) => window.data = saved', {'transcript:recovered': saved})
    for script in ['captions.js', 'content.js']:
        page.add_script_tag(path=str(ROOT / script))
    assert not page.evaluate("send({type:'STATUS'})")['result']['recording']
    assert page.locator('#meet-transcript-indicator').count() == 0
    # A Teams lobby does not trigger the prompt or allow capture.
    teams = browser.new_page()
    teams.route('https://teams.cloud.microsoft/**', lambda route: route.fulfill(
        body='<html><body><div id="meeting"></div></body></html>', content_type='text/html'))
    teams.goto('https://teams.cloud.microsoft/v2/')
    teams.evaluate(MOCK)
    for script in ['captions.js', 'content.js']:
        teams.add_script_tag(path=str(ROOT / script))
    assert 'Join a meeting' in teams.evaluate("send({type:'START', consent:true})")['error']
    assert teams.locator('#meet-transcript-prompt').count() == 0

    teams.evaluate("""document.querySelector("#meeting").innerHTML = '<button data-tid="hangup-button">Leave</button>'""")
    teams.get_by_role('button', name='Not now').click()
    teams.wait_for_timeout(600)
    assert teams.locator('#meet-transcript-prompt').count() == 0
    assert teams.evaluate('Object.keys(window.data).length') == 0

    teams.locator('[data-tid="hangup-button"]').evaluate('button => button.remove()')
    teams.wait_for_function("""() => {
        void send({type:'STATUS'}).then(reply => { window.leftTeams = !reply.result.joined; });
        return window.leftTeams === true;
    }""")
    teams.evaluate("""document.querySelector("#meeting").innerHTML = '<button data-tid="hangup-button">Leave</button>'""")
    teams.evaluate("""() => {
        const meeting = document.querySelector('#meeting');
        meeting.insertAdjacentHTML('beforeend', '<button id="more-actions-button">More actions</button>');
        meeting.querySelector('#more-actions-button').onclick = () => {
            meeting.insertAdjacentHTML('beforeend', '<button id="language-menu">Language and speech</button>');
            meeting.querySelector('#language-menu').onclick = () => {
                meeting.insertAdjacentHTML('beforeend', '<button id="teams-cc">Show live captions</button>');
                meeting.querySelector('#teams-cc').onclick = event => {
                    event.currentTarget.textContent = 'Hide live captions';
                };
            };
        };
    }""")
    teams.get_by_role('button', name='Start saving captions').click()
    teams.get_by_role('button', name='Hide live captions').wait_for()
    teams.wait_for_function('Object.keys(window.data).length === 1')
    teams.locator('#meeting').evaluate("""meeting => meeting.insertAdjacentHTML("beforeend", '<div data-tid="closed-caption-chat-message"><span data-tid="author">Morgan</span><span data-tid="closed-caption-text">Teams caption</span></div>')""")
    teams.wait_for_function('Object.values(window.data)[0].entries.length === 1')
    teams.locator('[data-tid="hangup-button"]').evaluate('button => button.remove()')
    teams.wait_for_function('Object.values(window.data)[0].endedAt')
    saved_teams = teams.evaluate('Object.values(window.data)[0]')
    assert saved_teams['platform'] == 'teams'
    assert saved_teams['entries'][0]['speaker'] == 'Morgan'
    assert saved_teams['entries'][0]['text'] == 'Teams caption'

    # Popup renders stored content as text, and gates Start on the checkbox.
    popup = browser.new_page(viewport={'width': 420, 'height': 820})
    popup.route('http://extension.test/**', lambda route: route.fulfill(path=str(ROOT / (route.request.url.split('/')[-1] or 'popup.html'))))
    popup.add_init_script(MOCK)
    popup.goto('http://extension.test/popup.html')
    popup.wait_for_function("document.querySelector('#status').textContent === 'Ready when you are'")
    assert popup.locator('#start').text_content() == 'Open audio recorder'
    popup.locator('#mode').select_option('captions')
    assert popup.locator('#start').is_disabled()
    popup.locator('#consent').check()
    assert popup.locator('#start').is_enabled()
    popup.evaluate('(session) => window.data = {"transcript:test": session}', {**saved, 'title': '<img src=x onerror=bad()>'})
    popup.evaluate('render()')
    assert popup.locator('article img').count() == 0
    assert popup.locator('article strong').text_content() == '<img src=x onerror=bad()>'
    with popup.expect_download() as download:
        popup.get_by_text('Export TXT', exact=True).click()
    assert 'Hello everyone' in Path(download.value.path()).read_text()
    popup.evaluate('(session) => window.data = {"transcript:test": session}', saved)
    popup.evaluate('render()')
    popup.screenshot(path=str(ROOT / 'tests/popup-preview.png'))
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    assert manifest['manifest_version'] == 3
    assert set(manifest['permissions']) == {'storage', 'activeTab', 'tabCapture'}
    assert manifest['host_permissions'] == ['http://127.0.0.1:8765/*']
    browser.close()
    print('PASS: consent, caption updates, speaker changes, stop, persistence, restart, storage failure, recovery, popup, export, and manifest checks')
