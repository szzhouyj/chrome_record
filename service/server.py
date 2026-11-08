"""Loopback-only Whisper service. Audio is decoded in memory; never written to disk."""
import argparse
import hmac
import io
import json
import re
import secrets
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BYTES = 2_000_000
EXTENSION_ORIGIN = re.compile(r'chrome-extension://[a-p]{32}')


def make_handler(model, model_name, token, backend='cpu', device='CPU · int8'):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Never log request payloads or access tokens.

        def cors_origin(self):
            origin = self.headers.get('Origin', '')
            return origin if EXTENSION_ORIGIN.fullmatch(origin) else None

        def reply(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            origin = self.cors_origin()
            if origin:
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def authorized(self):
            if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                self.reply(403, {'error': 'Invalid host'})
                return False
            if self.headers.get('Origin') and not self.cors_origin():
                self.reply(403, {'error': 'Only Chrome extension origins are accepted'})
                return False
            expected = f'Bearer {token}'.encode()
            if not hmac.compare_digest(self.headers.get('Authorization', '').encode(), expected):
                self.reply(401, {'error': 'Invalid access token'})
                return False
            return True

        def do_OPTIONS(self):
            if not self.cors_origin():
                self.reply(403, {'error': 'Only Chrome extension origins are accepted'})
                return
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', self.cors_origin())
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
            self.end_headers()

        def do_GET(self):
            if not self.authorized():
                return
            if self.path != '/health':
                self.reply(404, {'error': 'Not found'})
                return
            self.reply(200, {'ready': True, 'model': model_name, 'backend': backend, 'device': device})

        def do_POST(self):
            if not self.authorized():
                return
            if self.path != '/transcribe':
                self.reply(404, {'error': 'Not found'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = 0
            if length <= 44 or length > MAX_BYTES:
                self.reply(413, {'error': 'Audio window is empty or too large'})
                return
            if self.headers.get('Content-Type') != 'audio/wav':
                self.reply(415, {'error': 'Expected audio/wav'})
                return
            if not lock.acquire(blocking=False):
                self.reply(429, {'error': 'Whisper is busy. Use one recorder at a time.'})
                return
            try:
                self.connection.settimeout(15)
                audio = self.rfile.read(length)
                try:
                    with wave.open(io.BytesIO(audio)) as wav:
                        if (len(audio) != length or wav.getnchannels() != 1 or wav.getsampwidth() != 2
                                or not 8000 <= wav.getframerate() <= 96000
                                or not 0 < wav.getnframes() / wav.getframerate() <= 11):
                            raise ValueError('Invalid audio window')
                        if len(wav.readframes(wav.getnframes())) != wav.getnframes() * 2:
                            raise ValueError('Truncated WAV')
                except (wave.Error, EOFError, ValueError):
                    self.reply(400, {'error': 'Expected complete mono 16-bit WAV, at most 11 seconds'})
                    return
                self.reply(200, {'segments': model.transcribe(audio)})
            except Exception as error:
                print(f'Transcription request failed ({type(error).__name__}).', flush=True)
                self.reply(500, {'error': 'Whisper failed to process this audio window'})
            finally:
                lock.release()

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['auto', 'mlx', 'cpu'], default='auto',
                        help='auto: MLX on Apple Silicon, CPU elsewhere')
    parser.add_argument('--model', help='Default: large-v3-turbo on MLX; small on CPU. MLX also accepts an MLX repository / directory.')
    args = parser.parse_args()
    from backends import LocalTranscriber, select_backend
    try:
        selected = select_backend(args.backend)
    except ValueError as error:
        parser.error(str(error))
    print(f'Loading local Whisper ({selected}). The first launch downloads model files; MLX also warms up before Ready.', flush=True)
    try:
        model = LocalTranscriber(selected, args.model)
    except Exception as error:
        parser.exit(1, f'Could not load the local model: {error}\nCheck the backend, uv dependencies, and model-download access.\n')
    token = secrets.token_urlsafe(32)
    server = None
    try:
        server = ThreadingHTTPServer(('127.0.0.1', 8765), make_handler(model, model.model_name, token, model.name, model.device))
        server.daemon_threads = True
        print(f'Ready: {model.model_name} · {model.device} at http://127.0.0.1:8765 (this computer only).', flush=True)
        print(f'Paste this access token into the recorder tab: {token}', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.server_close()
        model.close()


if __name__ == '__main__':
    main()
