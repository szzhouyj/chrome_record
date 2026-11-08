# Meet Transcript · v0.3

Chrome Manifest V3 extension with two modes:

| Mode | Captions required? | Model / service | Captures |
| --- | --- | --- | --- |
| Tab audio | No | Local MLX Whisper on Apple Silicon, faster-whisper on CPU | Sound played by Meet; optionally your microphone |
| Meet captions | Yes | Google's built-in captions | Caption text and speaker names when available |

No cloud transcription API or API key. Audio mode requires running the included Python service on the **same computer as Chrome**. On Apple Silicon, the service defaults to **MLX + large-v3-turbo on the Apple GPU**. Other computers default to Whisper **small** on CPU using int8. The first launch downloads model files; subsequent launches use the local cache. Meeting audio is sent only to `http://127.0.0.1:8765` for local processing. Raw audio is not saved by the extension or service.

## Install the extension

1. Clone this repository or download and unzip its source.
2. Open `chrome://extensions` in Chrome 116 or newer and enable **Developer mode**.
3. Click **Load unpacked** and select the project folder containing `manifest.json` (for a Git clone, this is `chrome_record`).
4. If updating an earlier version, replace the old folder and click **Reload** on its extension card. Refresh your Meet tab. Stop the old Python service with Ctrl+C before launching the new version.

The extension requests tab capture, active-tab access, local storage, and access to the fixed local service address. It never starts automatically. Chrome's capture and microphone indicators remain enabled.

## Set up local Whisper (audio mode)

**For your M4 Pro / 24 GB Mac: MLX + `large-v3-turbo`.** This uses Metal on the Apple GPU. CPU inference remains available with `--backend cpu`. All models run locally without an OpenAI account or API key.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), open a terminal in the project folder containing `pyproject.toml`, then run on your Apple Silicon Mac:

```sh
uv run --locked python service/server.py --backend mlx --model large-v3-turbo
```

`uv` manages Python 3.12, creates the project's `.venv`, and installs the dependency versions in `uv.lock` automatically. Native macOS ARM64 environments also install the pinned MLX Whisper package and its GPU runtime. No environment activation or separate model-install command is required. The first run needs internet access to install Python / dependencies and download the Whisper model. You can also install the locked service dependencies ahead of time with `uv sync --locked`.

The `large-v3-turbo` alias downloads [mlx-community/whisper-large-v3-turbo](https://huggingface.co/mlx-community/whisper-large-v3-turbo) from Hugging Face into the local model cache (about 1.6 GB of model weights, plus package dependencies). It warms up the GPU model before accepting audio. Wait until the terminal prints **Ready: large-v3-turbo · Apple GPU · Metal** and an access token. Later launches reuse cached model files. Download access to Hugging Face and its file-hosting endpoints is needed on first use.

Verify that Python is running natively on your Mac and Metal is available:

```sh
uv run --locked python -c "import platform; import mlx.core as mx; print(platform.machine()); print('Metal:', mx.metal.is_available())"
```

Expected: `arm64` and `Metal: True`. If Python is running under Rosetta (`x86_64`), use a native Apple Silicon installation of uv / Python. Explicit `--backend mlx` fails with a clear error on unsupported systems; it never silently changes your request to CPU inference.

For automatic platform selection, omit the flags:

```sh
uv run --locked python service/server.py
```

This selects `mlx` / `large-v3-turbo` on native Apple Silicon macOS, and `cpu` / `small` elsewhere. To use a different MLX model, pass a full MLX-format Hugging Face repository ID or an existing local MLX model directory with `--model`. The aliases `large-v3-turbo` and `turbo` both select the repository linked above.

For CPU mode on any supported computer:

```sh
uv run --locked python service/server.py --backend cpu --model small
```

CPU model choices include `tiny`, `base`, `small`, `medium`, `large-v3`, and `large-v3-turbo`. Choose `tiny` if processing falls behind, or `small.en` for an English-only variant. Each new model choice downloads its own files once. MLX and CTranslate2 models have different formats and separate caches; original Whisper `.pt` files are not accepted as model directories.

Keep the terminal running. Paste its access token into the recorder tab; do not share it. A new token is generated on every service restart.

The service binds to loopback only, requires a bearer token, and rejects non-extension browser origins. Audio is decoded in memory. Do not expose the service through a public tunnel. CUDA and a system FFmpeg installation are not required: MLX receives decoded 16 kHz float32 PCM directly; CPU mode uses PyAV. Model loading, warmup, and transcription share a single worker thread. The recorder displays the active model and device, and saves backend metadata in JSON exports.

## Transcribe with captions off

1. Join Meet at a regular `abc-defg-hij` meeting URL and inform participants / obtain consent.
2. Open the extension, select **Tab audio · captions off**, and click **Open audio recorder**.
3. Paste the token from the local service. Optionally enable **Include my microphone** to capture your own voice. Grant microphone access when Chrome asks.
4. Check the consent box and click **Start audio transcription**. If Chrome requests local-network access, allow access to the local service.
5. Keep the recorder tab open; return to Meet while it works. First text appears after a 10-second window plus model processing time.
6. Click **Stop and finish** in the recorder (or Stop in Meet / the popup). Wait for processing to finish, then export TXT or JSON. Text is also saved in the extension's popup.

Do not mute the Meet tab: it must produce audio. The optional microphone input is independent of Meet's microphone toggle. **Muting yourself in Meet does not mute this extension's separate microphone capture.** Stop transcription if you need that input to stop. The microphone is never played back into your speakers; the tab's original playback is restored during capture. Headphones help reduce echo when including your microphone.

The recording indicator is local to your browser. The extension does **not** send a participant notification or chat message; you must inform participants yourself.

## Captions mode

Turn on Meet's **Captions (CC)**, choose **Meet captions · no model needed**, check the consent box and start. No Python service or microphone access is used in this mode. It captures only displayed captions, beginning when you start. Speaker names fall back to “Unknown speaker” if unavailable.

## Storage, failures, and recovery

- Text lives in `chrome.storage.local` in this Chrome profile, not cloud sync. It is not separately encrypted. People with access to the profile may read it. Uninstalling clears extension storage.
- Audio mode saves text after each processed window. Only a bounded amount of unprocessed PCM is kept in memory; it is discarded after processing or on error. If the model falls behind by three queued windows, capture stops with an error instead of silently skipping speech.
- Stop flushes the final short window, waits for queued transcription, and saves the result. Closing the recorder or crashing Chrome can lose unprocessed audio. A snapshot without an end time may be an interrupted session. It is never automatically resumed.
- If the service disconnects, returns an error, or storage fills up, capture stops. Keep the recorder open and export its in-memory text. Nothing retries or sends audio elsewhere.
- Navigation or closing the original Meet tab stops audio capture. Reloading the recorder closes its audio streams; start again explicitly from Meet.
- Caption updates normally save within 750 ms. If saving fails, keep Meet open and use **Export current tab transcript** with captions mode selected.
- Export, then delete transcripts you no longer need. An actively recording session cannot be deleted until it stops; interrupted saved snapshots can be deleted normally.

## Current limits and validation

- Audio mode does not identify individual speakers: all segments say “Speaker unknown.” Ten-second windows can split words or lose context at boundaries. Whisper may make mistakes, especially with overlapping speech or noise.
- The MLX path skips near-digital silence but does not use the CPU backend's full VAD filter. Noise and quiet windows can still produce incorrect text. Real-time speed on your Mac must be checked with actual meeting audio; no throughput guarantee is implied by hardware detection.
- Without the optional microphone, your own voice is usually absent because Meet doesn't play it back through the tab. Microphone mode needs user-granted permission in the recorder tab.
- One audio recorder at a time is supported. Tab capture is subject to Chrome permissions and organizational policies.
- Meet's caption markup is undocumented and may change. Selectors are isolated in `captions.js`; non-prefix caption corrections become new lines rather than deleting earlier text.
- Browser tests use representative caption fixtures, synthetic audio, real Web Audio/AudioWorklet processing, and mocked Chrome capture APIs / model responses, including MLX status and export metadata. HTTP service tests exercise authentication, origin checks, WAV validation, and response handling with a fake model. Backend tests cover platform selection, PCM normalization / resampling, GPU selection, warmup, and single-thread execution using fake inference runtimes.
- MLX GPU inference cannot run in this Linux cloud workspace. The implementation is checked against the pinned library API, but real Metal execution and speed still need validation on your Mac.
- Actual unpacked extension loading in this environment's Chromium was blocked with `ERR_BLOCKED_BY_CLIENT`. No real Meet call or Whisper accuracy/performance test has been completed; verify installation, permissions, and transcription in your Chrome before relying on it.

## Development

To collect CPU and RAM details on the computer running Chrome, run `uv run check_cpu.py`. This standalone checker installs only its own small dependency, not Whisper or model files. It prints the CPU name, core counts, total / available RAM, and a one-second CPU utilization sample. Share that output for model-selection advice. It does not benchmark sustained transcription speed; a real audio sample is needed to establish whether a model keeps up with meetings.

No extension build step or JavaScript dependencies. Python dependencies are declared in `pyproject.toml` and pinned in `uv.lock`; the Playwright development group is optional. Provide `/usr/bin/chromium` for browser tests, then run:

```sh
uv run --locked --group dev python tests/browser_test.py
uv run --locked --group dev python tests/audio_browser_test.py
uv run --locked --group dev python tests/backends_test.py
uv run --locked python tests/service_test.py
```

The HTTP tests use only the Python standard library. Inference libraries are imported lazily, so fake-model tests do not download model files. After intentionally changing dependencies, run `uv lock` and commit the updated `pyproject.toml` and `uv.lock` together.
