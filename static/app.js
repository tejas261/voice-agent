import {VoiceControls} from './voices.js';
import {createSpeechPlayer, unlockSpeech} from './speech-player.js';

const $ = (selector) => document.querySelector(selector);
let voices;
const state = {history: [], turn: 0, controller: null, recorder: null, audio: null, audioUrl: null, voiceReady: false, voiceOnly: false, lastAnswer: null, maxSeconds: 20, phase: 'idle', token: ''};
const canvas = $('#waveform');
const brush = canvas.getContext('2d');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const emptyTemplate = $('#empty').cloneNode(true);
const microphoneSupported = Boolean(navigator.mediaDevices?.getUserMedia && window.AudioWorkletNode);

function drawWave(level = 0) {
  brush.clearRect(0, 0, canvas.width, canvas.height);
  const heights = [15, 28, 46, 75, 44, 110, 66, 93, 46, 73, 35, 52, 22];
  heights.forEach((height, i) => {
    const h = state.phase === 'recording' && !reducedMotion ? 12 + height * Math.min(1.8, level * 14) : height * 0.65;
    brush.fillStyle = i > 3 && i < 10 ? '#5b7b47' : '#a5b991';
    brush.beginPath();
    brush.roundRect(105 + i * 27, 90 - h / 2, 10, h, 5);
    brush.fill();
  });
}
function setPhase(phase, hint) {
  state.phase = phase;
  const titles = {idle: state.lastAnswer ? 'Anything else I can help with?' : 'Ready when you are.', permission: 'Let’s turn on your mic.', recording: 'I’m listening.', thinking: 'Finding your answer…', transcribing: 'Understanding your question…', preparing: 'Your answer is ready.', speaking: 'Here’s what I found.'};
  const hints = {idle: 'Tap the microphone to ask a question.', permission: 'Choose Allow in your browser’s microphone prompt.', recording: 'Speak naturally. Tap the square when you’re done.', transcribing: 'You can stop or ask a new question at any time.', thinking: 'Checking your policy document.', preparing: 'Getting your spoken answer ready…', speaking: 'Listen, or tap the microphone to ask something else.'};
  const statuses = {idle: state.lastAnswer ? 'Answered' : 'Ready', permission: 'Microphone access', recording: 'Listening · live', transcribing: 'Processing voice', thinking: 'Finding answer', preparing: 'Preparing audio', speaking: 'Speaking'};
  $('#voice-panel').dataset.phase = phase;
  $('#voice-title').textContent = titles[phase];
  $('#voice-hint').textContent = hint || hints[phase];
  $('#voice-status').textContent = statuses[phase];
  $('#mic-label').textContent = phase === 'recording' ? 'Finish & send' : ['thinking', 'transcribing', 'preparing', 'speaking'].includes(phase) ? 'Interrupt & speak' : 'Start speaking';
  $('#stop').hidden = phase === 'idle';
  $('#stop').textContent = phase === 'permission' ? 'Cancel microphone request' : phase === 'recording' ? 'Cancel recording' : 'Stop response';
  $('#recording-progress').hidden = phase !== 'recording';
  $('#replay').hidden = phase !== 'idle' || !state.lastAnswer || !state.voiceReady;
  $('#mic').disabled = !state.voiceReady || !microphoneSupported || phase === 'permission';
  drawWave();
}
function showNotice(message) {
  $('#notice-text').textContent = message;
  $('#notice').hidden = !message;
  $('#show-chat').hidden = !state.voiceOnly;
}
function setVoiceOnly(enabled, persist = true) {
  state.voiceOnly = enabled;
  $('#voice-only').checked = enabled;
  $('#workspace').classList.toggle('voice-only', enabled);
  $('#chat-panel').hidden = enabled;
  $('#autoplay-option').hidden = enabled;
  $('#voice-only-note').hidden = !enabled;
  $('#new-conversation').hidden = !enabled;
  $('#audio-privacy').hidden = enabled;
  $('#show-chat').hidden = !enabled;
  $('#mode-description').textContent = enabled ? 'Just you and your voice assistant. Chat is hidden.' : 'Speak or type. Your conversation stays here.';
  // Only the layout preference is persisted, never audio, text, or credentials.
  if (persist) {
    try { localStorage.setItem('listen.voiceOnly', String(enabled)); } catch { /* Storage may be disabled. */ }
  }
  if (!enabled) $('#conversation').scrollTop = $('#conversation').scrollHeight;
}
async function api(path, options = {}) {
  const headers = new Headers(options.headers);
  if (state.token) headers.set('Authorization', `Bearer ${state.token}`);
  const response = await fetch(path, {...options, headers});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error?.message || `Request failed (${response.status})`);
  }
  return response;
}
async function connect() {
  try {
    const status = await (await api('/api/status')).json();
    state.voiceReady = status.voice_ready;
    state.maxSeconds = status.max_audio_seconds;
    $('#knowledge-source').textContent = `${status.knowledge_base.name} · ${status.knowledge_base.entry_count} indexed entries`;
    for (const parent of [emptyTemplate, $('#empty')].filter(Boolean)) {
      const examples = parent.querySelector('.suggestions');
      examples.replaceChildren();
      for (const suggestion of status.knowledge_base.suggestions) {
        const button = document.createElement('button');
        button.dataset.question = suggestion.question;
        button.dataset.language = suggestion.language;
        button.textContent = suggestion.question;
        const arrow = document.createElement('span');
        arrow.textContent = '↗';
        button.append(arrow);
        examples.append(button);
      }
    }
    $('#connection').textContent = status.voice_ready ? 'Local voice ready' : 'Text mode ready';
    $('#connection').classList.add('ready');
    $('#provider-label').textContent = status.provider === 'groq' ? 'Groq · account quota applies' : 'No per-request API charges';
    $('#engine').textContent = status.voice_ready ? `Whisper + Kokoro${status.voice_cloning?.available ? ' + Qwen3-TTS' : ''}` : 'Speech not enabled';
    showNotice(!microphoneSupported ? 'This browser cannot record audio. Open this page on localhost in a browser with microphone support, or use chat.' : status.voice_ready ? '' : 'Voice is currently unavailable. You can use chat while the local voice service is checked.');
    await voices.refresh().catch(error => showNotice(error.message));
    setPhase(state.phase);
  } catch (error) {
    state.voiceReady = false;
    $('#connection').textContent = 'Connection needed';
    $('#connection').classList.remove('ready');
    setPhase(state.phase);
    showNotice(error.message);
  }
}
function stopAudio() {
  state.audio?.stop();
  state.audio = null;
}
function interrupt() {
  voices?.stopPreview();
  state.turn += 1;
  state.controller?.abort();
  state.controller = null;
  void cancelRecording();
  stopAudio();
  setPhase('idle');
}
function addMessage(role, text, result) {
  $('#empty')?.remove();
  const item = document.createElement('article');
  item.className = `message ${role}`;
  const label = document.createElement('span');
  label.className = 'speaker';
  label.textContent = role === 'user' ? 'You' : 'Listen';
  const content = document.createElement('p');
  content.textContent = text;
  item.append(label, content);
  if (result) {
    const meta = document.createElement('span');
    meta.className = 'meta';
    const names = {faq: 'Source answer', ollama: 'Local model', groq: 'Groq', fallback: 'Source fallback', no_match: 'No matching source'};
    meta.textContent = `${names[result.mode]} · ${Math.round(result.latency_ms)} ms${result.cached ? ' · cached' : ''}`;
    item.append(meta);
    if (result.sources.length) {
      const details = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = `View ${result.sources.length === 1 ? 'source' : 'sources'}`;
      details.append(summary);
      for (const source of result.sources) {
        const title = document.createElement('p');
        const strong = document.createElement('strong');
        strong.textContent = source.title;
        title.append(strong);
        const quote = document.createElement('p');
        quote.textContent = source.text;
        details.append(title, quote);
      }
      item.append(details);
    }
  }
  $('#conversation').append(item);
  $('#conversation').scrollTop = $('#conversation').scrollHeight;
}
async function ask(question) {
  if (!question.trim()) return;
  interrupt();
  if (state.voiceOnly || $('#autoplay').checked) void unlockSpeech().catch(() => {});
  state.lastAnswer = null;
  const turn = state.turn;
  const language = $('#language').value;
  const controller = new AbortController();
  state.controller = controller;
  showNotice('');
  addMessage('user', question);
  setPhase('thinking');
  try {
    const response = await api('/api/ask', {method: 'POST', headers: {'Content-Type': 'application/json'}, signal: controller.signal, body: JSON.stringify({question, language, history: state.history.slice(-6)})});
    const result = await response.json();
    if (turn !== state.turn) return;
    state.history.push({role: 'user', content: question}, {role: 'assistant', content: result.answer});
    state.history = state.history.slice(-6);
    state.lastAnswer = result;
    addMessage('assistant', result.answer, result);
    if ((state.voiceOnly || $('#autoplay').checked) && state.voiceReady) {
      await speakAnswer(result, turn, controller);
    } else setPhase('idle');
  } catch (error) {
    if (turn !== state.turn || error.name === 'AbortError') return;
    stopAudio();
    setPhase('idle');
    showNotice(error.message);
  }
}
async function speakAnswer(result, turn, controller) {
  const voiceId = voices.forLanguage(result.language);
  setPhase('preparing');
  if (voiceId !== 'default') $('#voice-hint').textContent = 'Preparing your reply in the selected voice…';
  const failed = (error) => {
    if (turn !== state.turn) return;
    stopAudio();
    setPhase('idle');
    showNotice(voiceId === 'default' ? 'Your answer is ready, but audio could not play. Press Listen again to retry, or open chat to read it.' : `${error?.message || 'The cloned voice could not play.'} You can select Standard voice and press Listen again.`);
  };
  try {
    const response = await api('/api/speak/stream', {method: 'POST', headers: {'Content-Type': 'application/json'}, signal: controller.signal, body: JSON.stringify({text: result.answer, language: result.language, voice_id: voiceId})});
    if (turn !== state.turn) return;
    const audio = createSpeechPlayer({
      onStarted: () => { if (turn === state.turn) setPhase('speaking'); },
      onEnded: () => { if (turn === state.turn) { stopAudio(); setPhase('idle'); } },
    });
    state.audio = audio;
    await audio.play(response, controller.signal);
  } catch (error) {
    if (error.name !== 'AbortError') failed(error);
  }
}
function updateRecordingTime(seconds) {
  const format = (value) => `0:${String(Math.floor(value)).padStart(2, '0')}`;
  $('#recording-time').textContent = `${format(seconds)} / ${format(state.maxSeconds)}`;
  $('#recording-meter').max = state.maxSeconds;
  $('#recording-meter').value = seconds;
}
function wavBlob(chunks, rate) {
  const length = chunks.reduce((n, c) => n + c.length, 0);
  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);
  const write = (offset, text) => [...text].forEach((char, i) => view.setUint8(offset + i, char.charCodeAt(0)));
  write(0, 'RIFF'); view.setUint32(4, 36 + length * 2, true); write(8, 'WAVE'); write(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  write(36, 'data'); view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const chunk of chunks) for (const value of chunk) {
    const sample = Math.max(-1, Math.min(1, value));
    view.setInt16(offset, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
    offset += 2;
  }
  return new Blob([buffer], {type: 'audio/wav'});
}
async function disposeRecorder(recorder) {
  clearTimeout(recorder.timer);
  recorder.worklet.port.onmessage = null;
  recorder.worklet.disconnect();
  recorder.source.disconnect();
  recorder.stream.getTracks().forEach(track => track.stop());
  if (recorder.context.state !== 'closed') await recorder.context.close();
}
async function cancelRecording() {
  const recorder = state.recorder;
  state.recorder = null;
  if (recorder) await disposeRecorder(recorder);
}
async function finishRecording() {
  const recorder = state.recorder;
  if (!recorder) return;
  state.recorder = null;
  const turn = state.turn;
  setPhase('transcribing');
  await disposeRecorder(recorder);
  if (turn !== state.turn) return;
  const controller = new AbortController();
  state.controller = controller;
  try {
    const response = await api(`/api/transcribe?language=${recorder.language}`, {method: 'POST', headers: {'Content-Type': 'audio/wav'}, body: wavBlob(recorder.chunks, recorder.context.sampleRate), signal: controller.signal});
    const result = await response.json();
    if (turn !== state.turn) return;
    if (!result.text) { setPhase('idle'); showNotice('No speech detected. Try again closer to the microphone.'); return; }
    $('#language').value = result.language;
    await ask(result.text);
  } catch (error) {
    if (turn !== state.turn || error.name === 'AbortError') return;
    setPhase('idle');
    showNotice(error.message);
  }
}
async function startRecording() {
  interrupt();
  showNotice('');
  const turn = state.turn;
  let stream, context;
  setPhase('permission');
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}, video: false});
    if (turn !== state.turn) { stream.getTracks().forEach(t => t.stop()); return; }
    context = new AudioContext({sampleRate: 16000});
    await context.audioWorklet.addModule(new URL('./recorder-worklet.js', import.meta.url));
    await context.resume();
    if (turn !== state.turn) { stream.getTracks().forEach(t => t.stop()); await context.close(); return; }
    const source = context.createMediaStreamSource(stream);
    const worklet = new AudioWorkletNode(context, 'pcm-recorder');
    // The processor emits silence; connecting it keeps capture scheduled.
    source.connect(worklet); worklet.connect(context.destination);
    const recorder = {stream, context, source, worklet, chunks: [], count: 0, language: $('#language').value, timer: null};
    state.recorder = recorder;
    worklet.port.onmessage = ({data}) => {
      if (state.recorder !== recorder) return;
      const remaining = state.maxSeconds * context.sampleRate - recorder.count;
      if (remaining <= 0) { void finishRecording(); return; }
      const samples = data.slice(0, remaining);
      recorder.chunks.push(samples);
      recorder.count += samples.length;
      updateRecordingTime(recorder.count / context.sampleRate);
      drawWave(Math.sqrt(samples.reduce((sum, sample) => sum + sample * sample, 0) / samples.length));
    };
    recorder.timer = setTimeout(finishRecording, state.maxSeconds * 1000);
    updateRecordingTime(0);
    setPhase('recording');
  } catch (error) {
    stream?.getTracks().forEach(t => t.stop());
    if (context && context.state !== 'closed') await context.close();
    if (turn === state.turn) {
      const messages = {NotAllowedError: 'Microphone access is blocked. Allow it in your browser’s site settings and try again, or use chat.', NotFoundError: 'No microphone was found. Connect a microphone and try again, or use chat.', NotReadableError: 'Your microphone could not start. Check whether another app is using it and try again.'};
      showNotice(messages[error.name] || error.message);
      setPhase('idle');
    }
  } finally { if (turn === state.turn) $('#mic').disabled = !state.voiceReady || !microphoneSupported; }
}
$('#mic').addEventListener('click', () => { void unlockSpeech().catch(() => {}); return state.recorder ? finishRecording() : startRecording(); });
$('#stop').addEventListener('click', interrupt);
$('#replay').addEventListener('click', () => {
  if (!state.lastAnswer || !state.voiceReady) return;
  interrupt();
  void unlockSpeech().catch(() => {});
  showNotice('');
  const controller = new AbortController();
  state.controller = controller;
  void speakAnswer(state.lastAnswer, state.turn, controller);
});
$('#voice-only').addEventListener('change', (event) => setVoiceOnly(event.target.checked));
$('#show-chat').addEventListener('click', () => { setVoiceOnly(false); $('#question').focus(); });
$('#question-form').addEventListener('submit', (event) => { event.preventDefault(); const text = $('#question').value.trim(); $('#question').value = ''; void ask(text); });
$('#conversation').addEventListener('click', (event) => { const button = event.target.closest('[data-question]'); if (button) { if (button.dataset.language) $('#language').value = button.dataset.language; void ask(button.dataset.question); } });
function clearConversation() {
  state.lastAnswer = null;
  interrupt();
  state.history = [];
  $('#conversation').replaceChildren(emptyTemplate.cloneNode(true));
  showNotice('');
}
$('#clear').addEventListener('click', clearConversation);
$('#new-conversation').addEventListener('click', clearConversation);
$('#language').addEventListener('change', () => { interrupt(); voices.languageChanged(); });
$('#connect').addEventListener('click', () => { state.token = $('#api-token').value.trim(); void connect(); });
window.addEventListener('pagehide', interrupt);
voices = new VoiceControls({api, onActivity: interrupt, onNotice: showNotice, getLanguage: () => $('#language').value, makeWav: wavBlob});
try { setVoiceOnly(localStorage.getItem('listen.voiceOnly') === 'true', false); } catch { setVoiceOnly(false, false); }
setPhase('idle');
void connect();
