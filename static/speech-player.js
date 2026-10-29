// Incremental PCM playback. Never wait for a complete answer-sized audio blob.
let context;

export async function unlockSpeech() {
  if (!context || context.state === 'closed') context = new AudioContext();
  if (context.state === 'suspended') await context.resume();
  return context;
}

export function createSpeechPlayer({onStarted, onEnded}) {
  const sources = new Set();
  let stopped = false, started = false, finished = false, nextTime = 0, resolveDrain;
  const drained = new Promise(resolve => { resolveDrain = resolve; });
  const settle = () => {
    if (finished && !sources.size && !stopped) {
      stopped = true;
      resolveDrain();
      onEnded();
    }
  };
  const stop = () => {
    stopped = true;
    for (const source of sources) {
      source.onended = null;
      source.stop();
      source.disconnect();
    }
    sources.clear();
    resolveDrain();
  };
  async function play(response, signal) {
    const audio = await unlockSpeech();
    if (stopped || signal.aborted) return;
    if (audio.state !== 'running') throw new Error('Press Listen again to enable audio.');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = '', receivedDone = false, totalSeconds = 0;
    const abort = () => { stop(); void reader.cancel().catch(() => {}); };
    signal.addEventListener('abort', abort, {once: true});
    try {
      while (!stopped) {
        const {value, done} = await reader.read();
        if (done) break;
        pending += decoder.decode(value, {stream: true});
        if (pending.length > 2_000_000) throw new Error('Invalid audio stream.');
        let boundary;
        while ((boundary = pending.indexOf('\n')) >= 0) {
          const line = pending.slice(0, boundary);
          pending = pending.slice(boundary + 1);
          if (!line.trim()) continue;
          const packet = JSON.parse(line);
          if (packet.type === 'error') throw new Error(packet.error?.message || 'Speech could not finish.');
          if (packet.type === 'done') { receivedDone = true; continue; }
          if (packet.type === 'start') continue;
          if (packet.type !== 'audio' || receivedDone) throw new Error('Invalid audio stream.');
          const rate = packet.sample_rate;
          const bytes = Uint8Array.from(atob(packet.pcm), char => char.charCodeAt(0));
          if (!bytes.length || bytes.length % 2 || !Number.isInteger(rate) || rate < 8000 || rate > 48000) throw new Error('Invalid audio samples.');
          totalSeconds += bytes.length / 2 / rate;
          if (totalSeconds > 120) throw new Error('Audio exceeds the playback limit.');
          // Bound scheduled audio and apply network backpressure on cache hits.
          while (!stopped && nextTime - audio.currentTime > 3) await new Promise(resolve => setTimeout(resolve, 40));
          if (stopped) return;
          const buffer = audio.createBuffer(1, bytes.length / 2, rate);
          const samples = buffer.getChannelData(0);
          const view = new DataView(bytes.buffer);
          for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
          const source = audio.createBufferSource();
          source.buffer = buffer;
          source.connect(audio.destination);
          sources.add(source);
          source.onended = () => { source.disconnect(); sources.delete(source); settle(); };
          nextTime = Math.max(nextTime, audio.currentTime + 0.12);
          source.start(nextTime);
          nextTime += buffer.duration;
          if (!started) { started = true; onStarted(); }
        }
      }
      if (stopped || signal.aborted) return;
      if (!receivedDone || pending.trim() || !started) throw new Error('The audio stream ended before the reply was complete.');
      finished = true;
      settle();
      await drained;
    } catch (error) {
      stop();
      await reader.cancel().catch(() => {});
      throw error;
    } finally {
      signal.removeEventListener('abort', abort);
      reader.releaseLock();
    }
  }
  return {play, stop};
}
