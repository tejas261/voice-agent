// Capture a bounded reference locally, using the same PCM worklet as the chat mic.
export const MIN_REFERENCE_SECONDS = 3;
export const MAX_REFERENCE_SECONDS = 20;

export function createReferenceRecorder({onStart, onProgress, onComplete, onError}) {
  let stream, context, source, worklet, timer;
  let stopped = false;
  let started = false;
  let count = 0;
  let lastUpdate = 0;
  const chunks = [];

  function release() {
    clearTimeout(timer);
    if (worklet) { worklet.port.onmessage = null; worklet.disconnect(); }
    source?.disconnect();
    stream?.getTracks().forEach(track => { track.removeEventListener('ended', disconnected); track.stop(); });
    if (context && context.state !== 'closed') void context.close().catch(() => {});
  }

  function fail(error) {
    if (stopped) return;
    stopped = true;
    release();
    onError(error);
  }

  function disconnected() {
    fail(new Error('The microphone disconnected. Connect it and record again.'));
  }

  function finish() {
    if (stopped || !started) return;
    const duration = count / context.sampleRate;
    if (duration < MIN_REFERENCE_SECONDS) {
      fail(new Error('Record at least 3 seconds of speech. Please try again.'));
      return;
    }
    stopped = true;
    release();
    onComplete({chunks, sampleRate: context.sampleRate, duration});
  }

  async function start() {
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}, video: false,
      });
      // Permission can arrive after Cancel or after the dialog has closed.
      if (stopped) { release(); return; }
      context = new AudioContext({sampleRate: 24000});
      await context.audioWorklet.addModule(new URL('./recorder-worklet.js', import.meta.url));
      if (stopped) { release(); return; }
      await context.resume();
      if (stopped) { release(); return; }
      source = context.createMediaStreamSource(stream);
      worklet = new AudioWorkletNode(context, 'pcm-recorder');
      worklet.port.onmessage = ({data}) => {
        if (stopped) return;
        const samples = data.slice(0, MAX_REFERENCE_SECONDS * context.sampleRate - count);
        chunks.push(samples);
        count += samples.length;
        const duration = count / context.sampleRate;
        if (duration - lastUpdate >= 0.1) {
          lastUpdate = duration;
          const rms = Math.sqrt(samples.reduce((sum, sample) => sum + sample * sample, 0) / samples.length);
          onProgress(duration, Math.min(1, rms * 8));
        }
        if (count >= MAX_REFERENCE_SECONDS * context.sampleRate) finish();
      };
      stream.getAudioTracks().forEach(track => track.addEventListener('ended', disconnected));
      source.connect(worklet);
      // The worklet emits silence; this connection keeps capture scheduled.
      worklet.connect(context.destination);
      started = true;
      onStart();
      timer = setTimeout(finish, MAX_REFERENCE_SECONDS * 1000);
    } catch (error) { fail(error); }
  }

  return {start, finish, cancel() { stopped = true; release(); }};
}
