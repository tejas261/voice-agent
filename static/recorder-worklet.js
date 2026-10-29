// Capture PCM without browser/vendor speech-recognition services.
class PCMRecorder extends AudioWorkletProcessor {
  process(inputs) {
    const samples = inputs[0]?.[0];
    if (samples) this.port.postMessage(samples.slice());
    return true;
  }
}
registerProcessor('pcm-recorder', PCMRecorder);
