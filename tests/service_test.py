"""Real HTTP/PCM protocol tests; the speech model is a deterministic fake."""
import importlib.util
import io
import json
import threading
import unittest
import urllib.error
import urllib.request
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

spec = importlib.util.spec_from_file_location('server', Path(__file__).resolve().parents[1] / 'service/server.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakeModel:
    def transcribe(self, audio):
        with wave.open(io.BytesIO(audio)) as wav:
            assert wav.getframerate() == 16000
        return [{'start': 0.2, 'end': 0.8, 'text': ' Hello from audio.'}]


class ServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), module.make_handler(FakeModel(), 'test-model', 'test-token'))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def request(self, path='/health', body=None, headers=None, method=None):
        defaults = {'Authorization': 'Bearer test-token', 'Origin': 'chrome-extension://' + 'a' * 32}
        defaults.update(headers or {})
        req = urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}{path}', data=body, headers=defaults, method=method)
        try:
            response = urllib.request.urlopen(req)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def test_auth_health_and_cors(self):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'ready': True, 'model': 'test-model', 'backend': 'cpu', 'device': 'CPU · int8'})
        self.assertEqual(headers['Access-Control-Allow-Origin'], 'chrome-extension://' + 'a' * 32)
        self.assertEqual(self.request(headers={'Authorization': 'Bearer wrong'})[0], 401)
        self.assertEqual(self.request(headers={'Origin': 'https://unrelated.example'})[0], 403)
        self.assertEqual(self.request(headers={'Host': 'unrelated.example'})[0], 403)
        self.assertEqual(self.request(method='OPTIONS')[0], 204)

    def test_audio_and_invalid_inputs(self):
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000); wav.writeframes(b'\0\0' * 16000)
        status, _, body = self.request('/transcribe', output.getvalue(), {'Content-Type': 'audio/wav'})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['segments'][0]['text'], ' Hello from audio.')
        self.assertEqual(self.request('/transcribe', b'x' * 100, {'Content-Type': 'audio/wav'})[0], 400)
        self.assertEqual(self.request('/transcribe', output.getvalue()[:-2], {'Content-Type': 'audio/wav'})[0], 400)
        self.assertEqual(self.request('/transcribe', output.getvalue(), {'Content-Type': 'audio/webm'})[0], 415)
        self.assertEqual(self.request('/transcribe', b'x' * 45, {'Content-Type': 'audio/wav', 'Content-Length': '2000001'})[0], 413)
        self.assertEqual(self.request('/missing')[0], 404)


if __name__ == '__main__':
    unittest.main()
