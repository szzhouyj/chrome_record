// Emit independent WAV-ready mono PCM windows, not incomplete WebM fragments.
class PcmWindows extends AudioWorkletProcessor {
  constructor() {
    super();
    this.window = new Float32Array(sampleRate * 10);
    this.used = 0; this.done = false;
    this.port.onmessage = event => {
      if (event.data === 'flush') {
        this.emit(); this.done = true;
        this.port.postMessage({ type: 'flushed' });
      }
    };
  }
  emit() {
    if (!this.used) return;
    const samples = this.window.slice(0, this.used);
    this.port.postMessage({ type: 'pcm', samples }, [samples.buffer]);
    this.used = 0;
  }
  process(inputs) {
    if (this.done) return false;
    const input = inputs[0]?.[0];
    if (input) {
      for (let offset = 0; offset < input.length;) {
        const length = Math.min(input.length - offset, this.window.length - this.used);
        this.window.set(input.subarray(offset, offset + length), this.used);
        this.used += length; offset += length;
        if (this.used === this.window.length) this.emit();
      }
    }
    return true;
  }
}
registerProcessor('pcm-windows', PcmWindows);
