import {createReferenceRecorder, MIN_REFERENCE_SECONDS} from './reference-recorder.js';

const $ = (selector) => document.querySelector(selector);
const PREVIEW = 'Hi, what would you like to know about your policy? I can explain the options and help you understand how they work.';
const CAPTURE_HINT = 'Speak naturally for 3–20 seconds. Recording stops automatically.';

export class VoiceControls {
  constructor({api, onActivity, onNotice, getLanguage, makeWav}) {
    Object.assign(this, {api, onActivity, onNotice, getLanguage, makeWav});
    this.available = false;
    this.profiles = [];
    this.selected = 'default';
    this.sample = null;
    this.sampleUrl = null;
    this.previewUrl = null;
    this.controller = null;
    this.sequence = 0;
    this.busy = false;
    this.capture = null;
    this.capturePhase = 'idle';
    this.captureSeconds = 0;
    this.microphoneSupported = Boolean(navigator.mediaDevices?.getUserMedia && window.AudioWorkletNode && window.AudioContext);
    try { this.selected = localStorage.getItem('listen.replyVoice') || 'default'; } catch { /* Optional preference. */ }
    try { localStorage.removeItem('listen.accent'); } catch { /* Retired preset preference. */ }
    $('#manage-voices').addEventListener('click', () => this.open());
    $('#close-studio').addEventListener('click', () => $('#voice-studio').close());
    $('#voice-studio').addEventListener('close', () => this.reset());
    $('#reply-voice').addEventListener('change', () => { this.onActivity(); this.select($('#reply-voice').value); });
    $('#clone-file').addEventListener('change', () => { void this.readFile(); });
    $('#record-reference').addEventListener('click', () => this.capture ? this.capture.finish() : this.startCapture());
    $('#cancel-reference').addEventListener('click', () => this.cancelCapture());
    window.addEventListener('pagehide', () => this.reset());
    $('#transcribe-reference').addEventListener('click', () => { void this.transcribe(); });
    $('#clone-form').addEventListener('submit', (event) => { event.preventDefault(); void this.save(); });
    $('#voice-list').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-voice]');
      if (!button) return;
      if (button.dataset.action === 'delete') void this.remove(button.dataset.voice);
      if (button.dataset.action === 'preview') void this.preview(button.dataset.voice);
      if (button.dataset.action === 'use') { this.onActivity(); this.select(button.dataset.voice); $('#voice-studio').close(); }
    });
  }

  message(text, error = false) {
    $('#studio-status').textContent = text;
    $('#studio-status').classList.toggle('error', error);
  }

  controls() {
    const occupied = this.busy || Boolean(this.capture);
    $('#save-voice').disabled = occupied || !this.available || !this.sample;
    $('#transcribe-reference').disabled = occupied || !this.sample;
    for (const field of ['#clone-name', '#clone-file', '#clone-transcript']) $(field).disabled = occupied || !this.available;
    $('#record-reference').disabled = this.busy || !this.available || !this.microphoneSupported || this.capturePhase === 'permission' || (this.capturePhase === 'recording' && this.captureSeconds < MIN_REFERENCE_SECONDS);
    $('#record-reference').textContent = this.capturePhase === 'permission' ? 'Waiting for microphone…' : this.capturePhase === 'recording' ? 'Finish recording' : 'Record voice';
    $('#cancel-reference').hidden = !this.capture;
    $('#cancel-reference').textContent = this.capturePhase === 'permission' ? 'Cancel microphone request' : 'Cancel recording';
    $('#reference-recording').hidden = this.capturePhase !== 'recording';
    $('#reference-player').hidden = Boolean(this.capture) || !this.sample;
    $('#reference-capture').dataset.phase = this.capturePhase;
    if (!this.microphoneSupported) $('#capture-hint').textContent = 'Recording is unavailable in this browser. Open on localhost or HTTPS in a supported browser, or upload a file.';
    for (const button of $('#voice-list').querySelectorAll('button')) {
      const action = button.dataset.action;
      button.disabled = occupied || (action !== 'delete' && !this.available) || (action === 'use' && this.getLanguage() !== 'en');
      if (action === 'use') button.title = this.getLanguage() === 'en' ? 'Use for replies' : 'Switch the conversation to English to use this voice';
    }
  }

  select(id) {
    this.selected = this.getLanguage() === 'en' && this.available ? id : 'default';
    $('#reply-voice').value = this.selected;
    try { localStorage.setItem('listen.replyVoice', this.selected); } catch { /* Optional preference. */ }
    this.prepare();
  }

  prepare() {
    if (this.available && this.selected !== 'default' && this.getLanguage() === 'en') {
      // Load the selected voice while the customer reads, speaks or waits for RAG.
      // Actual playback still reports any failure and can retry preparation itself.
      void this.api(`/api/voices/${this.selected}/prepare`, {method: 'POST'}).catch(() => {});
    }
  }

  forLanguage(language) {
    if (language !== 'en' && this.selected !== 'default') {
      this.select('default');
      this.onNotice('Qwen voice cloning does not support Hindi. This reply will use the standard Hindi voice.');
    }
    return language === 'en' ? this.selected : 'default';
  }

  languageChanged() {
    this.forLanguage(this.getLanguage());
    this.render();
    this.controls();
    this.prepare();
  }

  async refresh() {
    const result = await (await this.api('/api/voices')).json();
    this.available = result.cloning.available;
    this.profiles = result.voices;
    if (!this.profiles.some(profile => profile.id === this.selected)) this.selected = 'default';
    if (!this.available) this.selected = 'default';
    this.render();
    $('#clone-availability').textContent = this.available ? 'Qwen3-TTS · runs locally · no API fees' : result.cloning.reason || 'Voice cloning is unavailable right now.';
    this.controls();
    this.prepare();
  }

  render() {
    const select = $('#reply-voice');
    select.replaceChildren(new Option('Standard voice', 'default'));
    const list = $('#voice-list');
    list.replaceChildren();
    for (const profile of this.profiles) {
      const option = new Option(profile.name, profile.id);
      option.disabled = this.getLanguage() !== 'en' || !this.available;
      select.append(option);
      const row = document.createElement('div');
      row.className = 'saved-voice';
      const label = document.createElement('div');
      const name = document.createElement('strong');
      name.textContent = profile.name;
      const detail = document.createElement('span');
      detail.textContent = `${profile.duration_seconds}s sample · English`;
      label.append(name, detail);
      const buttons = document.createElement('div');
      for (const [action, text] of [['preview', 'Preview'], ['use', 'Use'], ['delete', 'Delete']]) {
        const button = document.createElement('button');
        button.className = 'text-button';
        button.type = 'button';
        button.dataset.voice = profile.id;
        button.dataset.action = action;
        button.textContent = text;
        button.setAttribute('aria-label', `${text} ${profile.name}`);
        buttons.append(button);
      }
      row.append(label, buttons);
      list.append(row);
    }
    select.value = this.selected;
    $('#no-saved-voices').hidden = this.profiles.length > 0;
  }

  open() {
    this.onActivity();
    $('#voice-studio').showModal();
    this.message('');
    void this.refresh().catch(error => this.message(error.message, true));
  }

  stopPreview() {
    this.controller?.abort();
    this.controller = null;
    const player = $('#clone-player');
    player.pause();
    player.removeAttribute('src');
    player.hidden = true;
    if (this.previewUrl) URL.revokeObjectURL(this.previewUrl);
    this.previewUrl = null;
  }

  reset() {
    this.sequence += 1;
    this.capture?.cancel();
    this.capture = null;
    this.capturePhase = 'idle';
    $('#capture-hint').textContent = CAPTURE_HINT;
    this.stopPreview();
    $('#reference-player').pause();
    $('#reference-player').removeAttribute('src');
    $('#reference-player').hidden = true;
    if (this.sampleUrl) URL.revokeObjectURL(this.sampleUrl);
    this.sampleUrl = null;
    this.sample = null;
    $('#clone-form').reset();
    this.busy = false;
    this.controls();
  }

  acceptSample(sample, duration) {
    if (this.sampleUrl) URL.revokeObjectURL(this.sampleUrl);
    this.sample = sample;
    this.sampleUrl = URL.createObjectURL(sample);
    $('#reference-player').src = this.sampleUrl;
    $('#reference-player').hidden = false;
    $('#clone-transcript').value = '';
    this.message(`${duration.toFixed(1)} second sample ready. Listen back and check the spoken words before saving.`);
    this.controls();
  }

  startCapture() {
    if (this.busy || this.capture || !this.available || !this.microphoneSupported) return;
    this.onActivity();
    $('#reference-player').pause();
    const sequence = ++this.sequence;
    this.capturePhase = 'permission';
    this.captureSeconds = 0;
    $('#reference-time').textContent = '0.0 / 20s';
    $('#reference-level').value = 0;
    $('#capture-hint').textContent = 'Allow microphone access in your browser to start recording.';
    this.message('');
    this.capture = createReferenceRecorder({
      onStart: () => {
        if (sequence !== this.sequence) return;
        this.capturePhase = 'recording';
        $('#capture-hint').textContent = 'Read in your natural voice: “Hello, I’m here to help you understand your policy. Please tell me how I can help you today.”';
        this.controls();
      },
      onProgress: (seconds, level) => {
        if (sequence !== this.sequence) return;
        this.captureSeconds = seconds;
        $('#reference-time').textContent = `${seconds.toFixed(1)} / 20s`;
        $('#reference-level').value = level;
        this.controls();
      },
      onComplete: ({chunks, sampleRate, duration}) => {
        if (sequence !== this.sequence) return;
        this.capture = null;
        this.capturePhase = 'idle';
        $('#capture-hint').textContent = 'Recording ready. Listen below, or record again to replace it.';
        $('#clone-file').value = '';
        this.acceptSample(this.makeWav(chunks, sampleRate), duration);
        void this.transcribe();
      },
      onError: error => {
        if (sequence !== this.sequence) return;
        this.capture = null;
        this.capturePhase = 'error';
        const messages = {NotAllowedError: 'Microphone access is blocked. Allow it in your browser’s site settings and try again, or upload a file.', NotFoundError: 'No microphone was found. Connect one or upload an audio file.', NotReadableError: 'The microphone could not start. Check whether another app is using it and try again.'};
        const message = messages[error.name] || error.message || 'Recording failed. Try again or upload an audio file.';
        $('#capture-hint').textContent = message;
        this.message(message, true);
        this.controls();
      },
    });
    this.controls();
    void this.capture.start();
  }

  cancelCapture() {
    if (!this.capture) return;
    this.sequence += 1;
    this.capture.cancel();
    this.capture = null;
    this.capturePhase = 'idle';
    $('#capture-hint').textContent = CAPTURE_HINT;
    this.message(this.sample ? 'Recording cancelled. Your previous sample is still ready to use.' : 'Recording cancelled. Record again or upload an audio file.');
    this.controls();
  }

  async readFile() {
    if (this.capture || this.busy) return;
    this.stopPreview();
    const sequence = ++this.sequence;
    this.sample = null;
    $('#clone-transcript').value = '';
    if (this.sampleUrl) URL.revokeObjectURL(this.sampleUrl);
    this.sampleUrl = null;
    $('#reference-player').pause();
    $('#reference-player').removeAttribute('src');
    $('#reference-player').hidden = true;
    this.controls();
    const file = $('#clone-file').files[0];
    if (!file) return;
    this.busy = true;
    this.capturePhase = 'idle';
    $('#capture-hint').textContent = CAPTURE_HINT;
    this.controls();
    let context;
    try {
      if (file.size > 10 * 1024 * 1024) throw new Error('Choose an audio file smaller than 10 MB.');
      this.message('Reading your sample…');
      context = new AudioContext({sampleRate: 24000});
      const audio = await context.decodeAudioData(await file.arrayBuffer());
      if (sequence !== this.sequence) return;
      if (audio.duration < 3 || audio.duration > 20) throw new Error('Choose a clip between 3 and 20 seconds.');
      const mono = new Float32Array(audio.length);
      for (let channel = 0; channel < audio.numberOfChannels; channel++) {
        const samples = audio.getChannelData(channel);
        for (let i = 0; i < mono.length; i++) mono[i] += samples[i] / audio.numberOfChannels;
      }
      this.acceptSample(this.makeWav([mono], audio.sampleRate), audio.duration);
    } catch (error) {
      if (sequence === this.sequence) this.message(error.message || 'This audio format could not be read. Try WAV or MP3.', true);
    } finally {
      if (context && context.state !== 'closed') await context.close();
      if (sequence === this.sequence) { this.busy = false; this.controls(); }
    }
  }

  async transcribe() {
    if (!this.sample || this.busy || this.capture) return;
    this.onActivity();
    const sequence = this.sequence;
    this.controller = new AbortController();
    this.busy = true;
    this.controls();
    this.message('Transcribing your sample locally…');
    try {
      const result = await (await this.api('/api/transcribe?language=en', {method: 'POST', headers: {'Content-Type': 'audio/wav'}, body: this.sample, signal: this.controller.signal})).json();
      if (sequence !== this.sequence) return;
      $('#clone-transcript').value = result.text;
      this.message(result.text ? 'Check that these words match your recording before saving.' : 'No speech was detected. Try a clearer recording.', !result.text);
    } catch (error) {
      if (sequence === this.sequence && error.name !== 'AbortError') this.message(error.message, true);
    } finally {
      if (sequence === this.sequence) { this.busy = false; this.controls(); }
    }
  }

  async save() {
    if (!this.sample || this.busy || this.capture) return;
    this.onActivity();
    const sequence = this.sequence;
    this.controller = new AbortController();
    this.busy = true;
    this.controls();
    this.message('Saving your voice on this computer…');
    try {
      const form = new FormData();
      form.append('name', $('#clone-name').value.trim());
      form.append('transcript', $('#clone-transcript').value.trim());
      form.append('audio', this.sample, 'reference.wav');
      const profile = await (await this.api('/api/voices', {method: 'POST', body: form, signal: this.controller.signal})).json();
      if (sequence !== this.sequence) return;
      await this.refresh();
      if (sequence !== this.sequence) return;
      this.select(profile.id);
      this.busy = false;
      await this.preview(profile.id);
    } catch (error) {
      if (sequence === this.sequence && error.name !== 'AbortError') this.message(error.message, true);
    } finally {
      if (sequence === this.sequence) { this.busy = false; this.controls(); }
    }
  }

  async preview(id) {
    if (this.busy || this.capture) return;
    this.onActivity();
    const sequence = this.sequence;
    this.controller = new AbortController();
    this.busy = true;
    this.controls();
    this.message('Creating a voice preview… The first preview loads the local model and may take longer.');
    try {
      const response = await this.api('/api/speak', {method: 'POST', headers: {'Content-Type': 'application/json'}, signal: this.controller.signal, body: JSON.stringify({text: PREVIEW, language: 'en', voice_id: id})});
      const blob = await response.blob();
      if (sequence !== this.sequence) return;
      this.previewUrl = URL.createObjectURL(blob);
      const player = $('#clone-player');
      player.src = this.previewUrl;
      player.hidden = false;
      this.message('Preview ready. Choose Use to select this voice, or close to keep your current selection.');
      await player.play().catch(() => this.message('Preview ready. Press play to listen.'));
    } catch (error) {
      if (sequence === this.sequence && error.name !== 'AbortError') this.message(error.message, true);
    } finally {
      if (sequence === this.sequence) { this.busy = false; this.controls(); }
    }
  }

  async remove(id) {
    if (this.busy || this.capture) return;
    this.onActivity();
    const sequence = this.sequence;
    this.busy = true;
    this.controls();
    this.message('Deleting the saved sample and voice…');
    try {
      await this.api(`/api/voices/${id}`, {method: 'DELETE'});
      if (sequence !== this.sequence) return;
      if (this.selected === id) this.select('default');
      await this.refresh();
      if (sequence === this.sequence) this.message('Voice deleted. Its saved recording has been removed.');
    } catch (error) {
      if (sequence === this.sequence) this.message(error.message, true);
    } finally {
      if (sequence === this.sequence) { this.busy = false; this.controls(); }
    }
  }
}
