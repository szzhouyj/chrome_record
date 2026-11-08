(() => {
  function wav(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const text = (at, value) => [...value].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)));
    text(0, 'RIFF'); view.setUint32(4, buffer.byteLength - 8, true);
    text(8, 'WAVE'); text(12, 'fmt '); view.setUint32(16, 16, true);
    view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    text(36, 'data'); view.setUint32(40, samples.length * 2, true);
    samples.forEach((sample, i) => {
      const clipped = Math.max(-1, Math.min(1, sample));
      view.setInt16(44 + i * 2, clipped * (clipped < 0 ? 32768 : 32767), true);
    });
    return new Blob([buffer], { type: 'audio/wav' });
  }
  function download(session, format = 'txt') {
    const text = format === 'json' ? JSON.stringify(session, null, 2) : [
      session.title, session.url, `Started: ${session.startedAt}`, `Source: ${session.source || 'captions'}`,
      ...(session.error ? [`Interrupted: ${session.error}`] : []), '',
      ...session.entries.map(entry => `[${entry.at}] ${entry.speaker}: ${entry.text}`)
    ].join('\n');
    const url = URL.createObjectURL(new Blob([text], { type: format === 'json' ? 'application/json' : 'text/plain;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url; link.download = `meet-${session.startedAt.replace(/[:.]/g, '-')}.${format}`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 15000);
  }
  globalThis.MeetAudio = { wav, download };
})();
