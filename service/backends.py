"""Local transcription adapters; optional MLX imports stay off non-Apple machines."""
import io
import math
import platform
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TURBO_REPOSITORY = 'mlx-community/whisper-large-v3-turbo'


def apple_silicon():
    return platform.system() == 'Darwin' and platform.machine().lower() in {'arm64', 'aarch64'}


def select_backend(backend='auto'):
    if backend == 'auto':
        return 'mlx' if apple_silicon() else 'cpu'
    if backend not in {'mlx', 'cpu'}:
        raise ValueError(f'Unknown backend: {backend}')
    if backend == 'mlx' and not apple_silicon():
        raise ValueError('MLX requires macOS on Apple Silicon with native ARM64 Python. Use --backend cpu on other systems.')
    return backend


def mlx_model_path(model_name):
    if model_name in {'large-v3-turbo', 'turbo'}:
        return TURBO_REPOSITORY
    if Path(model_name).is_dir() or '/' in model_name:
        return model_name
    raise ValueError('For MLX, use --model large-v3-turbo, an MLX Hugging Face repository, or a local MLX model directory. For CPU model aliases, use --backend cpu.')


def pcm_waveform(audio):
    """Decode mono PCM WAV directly; MLX receives float32 samples at 16 kHz."""
    import numpy as np
    with wave.open(io.BytesIO(audio)) as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError('Expected mono 16-bit PCM WAV')
        sample_rate = wav.getframerate()
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2').astype(np.float32) / 32768.0
    if sample_rate != 16000:
        from scipy.signal import resample_poly
        divisor = math.gcd(sample_rate, 16000)
        samples = resample_poly(samples, 16000 // divisor, sample_rate // divisor).astype(np.float32)
    return np.ascontiguousarray(samples)


class CpuBackend:
    name = 'cpu'
    device = 'CPU · int8'

    def __init__(self, model_name):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(model_name, device='cpu', compute_type='int8')

    def transcribe(self, audio):
        segments, _info = self.model.transcribe(io.BytesIO(audio), beam_size=5, vad_filter=True,
                                                condition_on_previous_text=False)
        return [{'start': s.start, 'end': s.end, 'text': s.text} for s in segments]


class MlxBackend:
    name = 'mlx'
    device = 'Apple GPU · Metal'

    def __init__(self, model_name):
        import mlx.core as mx
        import mlx_whisper
        import numpy as np
        if not mx.metal.is_available():
            raise RuntimeError('MLX Metal GPU support is unavailable. Use native Apple Silicon Python or --backend cpu.')
        mx.set_default_device(mx.gpu)
        self.repository = mlx_model_path(model_name)
        self.infer = mlx_whisper.transcribe
        # The public API caches the model. Warm up here so /health is ready only
        # after the download, weight loading, and first inference have completed.
        self.infer(np.zeros(16000, dtype=np.float32), path_or_hf_repo=self.repository,
                   language='en', verbose=None, temperature=0.0,
                   condition_on_previous_text=False)

    def transcribe(self, audio):
        import numpy as np
        samples = pcm_waveform(audio)
        # Exact / near-digital silence needs no inference. This is not full VAD.
        if not samples.size or float(np.max(np.abs(samples))) < 1e-5:
            return []
        result = self.infer(samples, path_or_hf_repo=self.repository, verbose=None,
                            temperature=0.0, condition_on_previous_text=False)
        return [{'start': s['start'], 'end': s['end'], 'text': s['text']}
                for s in result['segments']]


class LocalTranscriber:
    """Load and use each model on the same persistent thread, including Metal calls."""
    def __init__(self, backend='auto', model_name=None):
        self.name = select_backend(backend)
        self.model_name = model_name or ('large-v3-turbo' if self.name == 'mlx' else 'small')
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='whisper')
        adapter = MlxBackend if self.name == 'mlx' else CpuBackend
        try:
            self.adapter = self.worker.submit(adapter, self.model_name).result()
        except BaseException:
            self.worker.shutdown(wait=True)
            raise
        self.device = self.adapter.device

    def transcribe(self, audio):
        return self.worker.submit(self.adapter.transcribe, audio).result()

    def close(self):
        self.worker.shutdown(wait=True)
