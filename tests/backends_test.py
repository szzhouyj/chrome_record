"""Exercise real WAV conversion + adapter logic with fake inference runtimes.

No MLX hardware, model downloads, or speech-accuracy claims are involved.
"""
import importlib.util
import io
import sys
import threading
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import numpy as np

spec = importlib.util.spec_from_file_location('backends', Path(__file__).resolve().parents[1] / 'service/backends.py')
backends = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backends)


def wav_bytes(rate=16000, silent=False):
    samples = np.zeros(rate, dtype='<i2') if silent else (np.sin(np.arange(rate) * 2 * np.pi * 440 / rate) * 12000).astype('<i2')
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate); wav.writeframes(samples.tobytes())
    return output.getvalue()


class BackendTest(unittest.TestCase):
    def test_platform_selection(self):
        with patch.object(backends.platform, 'system', return_value='Darwin'), patch.object(backends.platform, 'machine', return_value='arm64'):
            self.assertEqual(backends.select_backend(), 'mlx')
            self.assertEqual(backends.select_backend('cpu'), 'cpu')
        for system, machine in [('Linux', 'aarch64'), ('Darwin', 'x86_64'), ('Windows', 'AMD64')]:
            with patch.object(backends.platform, 'system', return_value=system), patch.object(backends.platform, 'machine', return_value=machine):
                self.assertEqual(backends.select_backend(), 'cpu')
                with self.assertRaisesRegex(ValueError, 'native ARM64'):
                    backends.select_backend('mlx')

    def test_mlx_alias_and_pcm_resampling(self):
        self.assertEqual(backends.mlx_model_path('large-v3-turbo'), backends.TURBO_REPOSITORY)
        self.assertEqual(backends.mlx_model_path('turbo'), backends.TURBO_REPOSITORY)
        self.assertEqual(backends.mlx_model_path('owner/converted-model'), 'owner/converted-model')
        with self.assertRaisesRegex(ValueError, '--backend cpu'):
            backends.mlx_model_path('small')
        reference = backends.pcm_waveform(wav_bytes())
        self.assertEqual(reference.dtype, np.float32)
        self.assertEqual(reference.shape, (16000,))
        self.assertAlmostEqual(float(reference.max()), 12000 / 32768, places=4)
        for rate in [8000, 44100, 48000]:
            resampled = backends.pcm_waveform(wav_bytes(rate))
            self.assertEqual(resampled.shape, (16000,))
            # Confirm resampling keeps the tone and duration, not just array length.
            peak_frequency = np.argmax(np.abs(np.fft.rfft(resampled)))
            self.assertEqual(peak_frequency, 440)

    def test_mlx_warmup_gpu_and_single_worker(self):
        calls = []
        core = ModuleType('mlx.core')
        core.metal = SimpleNamespace(is_available=lambda: True)
        core.gpu = object()
        core.set_default_device = lambda device: calls.append(('device', threading.get_ident(), device))
        whisper = ModuleType('mlx_whisper')

        def infer(samples, **options):
            calls.append(('inference', threading.get_ident(), samples.copy(), options))
            return {'segments': [{'start': 0.2, 'end': 0.8, 'text': ' Local transcript.'}]}

        whisper.transcribe = infer
        mlx = ModuleType('mlx'); mlx.core = core
        with patch.dict(sys.modules, {'mlx': mlx, 'mlx.core': core, 'mlx_whisper': whisper}), patch.object(backends, 'apple_silicon', return_value=True):
            service = backends.LocalTranscriber()
            try:
                self.assertEqual(service.model_name, 'large-v3-turbo')
                self.assertEqual(service.device, 'Apple GPU · Metal')
                self.assertEqual(len(calls), 2)  # device selection, then warmup before readiness
                self.assertEqual(calls[0][2], core.gpu)
                self.assertEqual(calls[1][3]['language'], 'en')
                self.assertTrue(np.all(calls[1][2] == 0))
                with ThreadPoolExecutor(max_workers=2) as clients:
                    outputs = list(clients.map(service.transcribe, [wav_bytes(), wav_bytes()]))
                self.assertEqual(outputs[0][0]['text'], ' Local transcript.')
                self.assertEqual(len({call[1] for call in calls}), 1)
                self.assertNotEqual(calls[0][1], threading.get_ident())
                for call in calls[1:]:
                    self.assertEqual(call[3]['path_or_hf_repo'], backends.TURBO_REPOSITORY)
                    self.assertIsNone(call[3]['verbose'])
                    self.assertNotIn('beam_size', call[3])  # not supported by MLX Whisper
                    self.assertNotIn('vad_filter', call[3])
                count = len(calls)
                self.assertEqual(service.transcribe(wav_bytes(silent=True)), [])
                self.assertEqual(len(calls), count)
            finally:
                service.close()

    def test_mlx_rejects_missing_metal(self):
        core = ModuleType('mlx.core'); core.metal = SimpleNamespace(is_available=lambda: False)
        mlx = ModuleType('mlx'); mlx.core = core
        with patch.dict(sys.modules, {'mlx': mlx, 'mlx.core': core, 'mlx_whisper': ModuleType('mlx_whisper')}):
            with self.assertRaisesRegex(RuntimeError, 'Metal GPU support is unavailable'):
                backends.MlxBackend('large-v3-turbo')

    def test_cpu_backend_preserves_vad_and_output(self):
        calls = []

        class FakeCpuModel:
            def __init__(self, model, **options):
                calls.append((model, options))

            def transcribe(self, audio, **options):
                with wave.open(audio) as wav:
                    assert wav.getframerate() == 16000
                calls.append(options)
                return iter([SimpleNamespace(start=0.0, end=0.5, text='CPU text')]), None

        runtime = ModuleType('faster_whisper'); runtime.WhisperModel = FakeCpuModel
        with patch.dict(sys.modules, {'faster_whisper': runtime}), patch.object(backends, 'apple_silicon', return_value=True):
            service = backends.LocalTranscriber('cpu')
            try:
                self.assertEqual(service.model_name, 'small')
                self.assertEqual(service.transcribe(wav_bytes())[0]['text'], 'CPU text')
                self.assertEqual(calls[0], ('small', {'device': 'cpu', 'compute_type': 'int8'}))
                self.assertTrue(calls[1]['vad_filter'])
            finally:
                service.close()


if __name__ == '__main__':
    unittest.main()
