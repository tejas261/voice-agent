import {createReferenceRecorder} from './reference-recorder.js';

const $ = (selector) => document.querySelector(selector);
const state = {id: '', key: '', entries: [], version: 0, dirty: false, recorder: null, recording: null, preview: null};

function notice(message, error = false) {
  const node = $('#notice'); node.hidden = !message; node.textContent = message;
  node.dataset.error = String(error);
}
async function api(path, options = {}, key = state.key) {
  const headers = new Headers(options.headers || {});
  if (key) headers.set('Authorization', `Bearer ${key}`);
  const response = await fetch(path, {...options, headers});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error?.message || `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}
function run(action) {
  return async (event) => { event?.preventDefault(); notice(''); try { await action(event); } catch (error) { notice(error.message, true); } };
}
function text(tag, value, cls) {
  const node = document.createElement(tag); node.textContent = value;
  if (cls) node.className = cls;
  return node;
}
function badge(prepared, hasVoice) {
  const node = $('#voice-badge'); node.classList.toggle('ready', prepared);
  node.textContent = prepared ? 'Voice ready' : hasVoice ? 'Voice needs preparation' : 'Voice not set';
}
function showCredentials(businessId, accessKey, streamKey) {
  $('#created-id').textContent = businessId; $('#created-access').textContent = accessKey; $('#created-stream').textContent = streamKey;
  $('#created-url').textContent = `wss://${businessId}:${streamKey}@YOUR_PUBLIC_HOST/telephony/exotel/${businessId}`;
  $('#credentials').hidden = false;
}
async function listBusinesses() {
  const result = await api('/api/businesses', {}, $('#operator-token').value.trim());
  const list = $('#business-list'); list.replaceChildren();
  if (!result.businesses.length) list.append(text('p', 'No businesses yet. Add one below.', 'hint'));
  for (const business of result.businesses) {
    const button = document.createElement('button'); button.type = 'button';
    button.textContent = business.name; button.append(text('span', `${business.kind} · ${business.phone}`));
    button.setAttribute('aria-current', String(state.id === business.id));
    button.addEventListener('click', run(async () => { await openBusiness(business.id, $('#operator-token').value.trim()); }));
    list.append(button);
  }
}
async function openBusiness(id, key = state.key) {
  if (state.dirty) throw new Error('Save your knowledge edits before switching workspaces.');
  const result = await api(`/api/businesses/${encodeURIComponent(id)}/knowledge`, {}, key);
  if (state.id !== result.business.id) {
    state.recorder?.cancel(); state.recorder = null; state.recording = null;
    if (state.preview) URL.revokeObjectURL(state.preview); state.preview = null;
    const clonePreview = $('#clone-preview'); if (clonePreview.src.startsWith('blob:')) URL.revokeObjectURL(clonePreview.src);
    clonePreview.hidden = true; clonePreview.removeAttribute('src'); $('#voice-preview').hidden = true;
    $('#voice-preview').removeAttribute('src'); $('#voice-form').reset();
    $('#record-voice').textContent = 'Record voice'; $('#record-status').textContent = '3–20 seconds';
  }
  state.key = key;
  state.id = result.business.id; state.entries = result.entries; state.version = result.business.version; state.dirty = false;
  $('#empty-workspace').hidden = true; $('#workspace').hidden = false;
  $('#business-name').textContent = result.business.name;
  $('#business-type').textContent = result.business.kind.toUpperCase();
  $('#business-phone').textContent = `Exotel number · ${result.business.phone}`;
  $('#edit-name').value = result.business.name; $('#edit-kind').value = result.business.kind;
  $('#edit-phone').value = result.business.phone; $('#edit-greeting').value = result.business.greeting;
  badge(result.voice_prepared, result.business.voice_id);
  $('#credentials').hidden = true; $('#test-answer').hidden = true;
  renderEntries();
}
function renderEntries() {
  const list = $('#knowledge-list'); list.replaceChildren();
  for (const [index, entry] of state.entries.entries()) {
    const box = document.createElement('div'); box.className = 'knowledge-item';
    const top = document.createElement('div'); top.append(text('strong', `Answer ${index + 1}`));
    const remove = text('button', 'Remove', 'text-button'); remove.type = 'button';
    remove.addEventListener('click', () => { state.entries.splice(index, 1); state.dirty = true; renderEntries(); }); top.append(remove); box.append(top);
    const questionLabel = text('label', 'Example question'); const question = document.createElement('input');
    question.required = true; question.maxLength = 300; question.value = entry.questions[0];
    question.addEventListener('input', () => { entry.questions[0] = question.value; entry.title = question.value.slice(0, 100); state.dirty = true; });
    questionLabel.append(question); box.append(questionLabel);
    const answerLabel = text('label', 'Answer'); const answer = document.createElement('textarea');
    answer.required = true; answer.maxLength = 1200; answer.rows = 3; answer.value = entry.answer_en;
    answer.addEventListener('input', () => { entry.answer_en = answer.value; state.dirty = true; });
    answerLabel.append(answer); box.append(answerLabel); list.append(box);
  }
}
function wavBlob(chunks, rate) {
  const count = chunks.reduce((n, chunk) => n + chunk.length, 0);
  const bytes = new ArrayBuffer(44 + count * 2); const view = new DataView(bytes);
  const write = (at, value) => [...value].forEach((char, i) => view.setUint8(at + i, char.charCodeAt(0)));
  write(0, 'RIFF'); view.setUint32(4, 36 + count * 2, true); write(8, 'WAVE'); write(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  write(36, 'data'); view.setUint32(40, count * 2, true); let offset = 44;
  for (const chunk of chunks) for (const value of chunk) { const sample = Math.max(-1, Math.min(1, value)); view.setInt16(offset, Math.round(sample * (sample < 0 ? 32768 : 32767)), true); offset += 2; }
  return new Blob([bytes], {type: 'audio/wav'});
}
function showAudio(audio) {
  if (state.preview) URL.revokeObjectURL(state.preview);
  state.preview = URL.createObjectURL(audio); $('#voice-preview').src = state.preview; $('#voice-preview').hidden = false;
}
$('#load-businesses').addEventListener('click', run(listBusinesses));
$('#open-workspace').addEventListener('click', run(async () => { await openBusiness($('#workspace-id').value.trim(), $('#workspace-key').value.trim()); }));
$('#create-form').addEventListener('submit', run(async () => {
  if (state.dirty) throw new Error('Save your knowledge edits before adding a business.');
  const operatorKey = $('#operator-token').value.trim();
  const body = {name: $('#new-name').value, kind: $('#new-kind').value, phone: $('#new-phone').value, greeting: $('#new-greeting').value, language: 'en'};
  const result = await api('/api/businesses', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}, operatorKey);
  await listBusinesses(); await openBusiness(result.business.id, operatorKey);
  showCredentials(result.business.id, result.access_key, result.stream_key);
  $('#new-business').open = false; $('#create-form').reset();
  notice('Workspace created. Save the keys shown below before leaving this page.');
}));
$('#settings-form').addEventListener('submit', run(async () => {
  if (state.dirty) throw new Error('Save your knowledge edits before changing settings.');
  const body = {name: $('#edit-name').value, kind: $('#edit-kind').value, phone: $('#edit-phone').value, greeting: $('#edit-greeting').value, language: 'en'};
  await api(`/api/businesses/${state.id}`, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  await openBusiness(state.id); notice('Business settings saved. New calls will use them.');
}));
$('#add-entry').addEventListener('click', () => { const id = `entry_${crypto.randomUUID().replaceAll('-', '')}`; state.entries.push({id, title: '', questions: [''], answer_en: '', answer_hi: null}); state.dirty = true; renderEntries(); });
$('#knowledge-form').addEventListener('submit', run(async () => {
  if (!state.entries.length) throw new Error('Add at least one answer.');
  const result = await api(`/api/businesses/${state.id}/knowledge`, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({entries: state.entries, expected_version: state.version})});
  state.version = result.business.version;
  state.dirty = false;
  notice('Knowledge saved. New calls will use the updated answers.');
}));
$('#import-form').addEventListener('submit', run(async () => {
  if (state.dirty) throw new Error('Save the current knowledge edits before importing.');
  const body = new FormData(); body.set('file', $('#knowledge-file').files[0]);
  const result = await api(`/api/businesses/${state.id}/knowledge/import`, {method: 'POST', body});
  state.entries = result.entries; state.version = result.business.version; renderEntries(); $('#import-form').reset();
  notice('Text imported. Review the sections and test some questions.');
}));
$('#test-form').addEventListener('submit', run(async () => {
  const result = await api(`/api/businesses/${state.id}/ask`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question: $('#test-question').value, language: 'en'})});
  const output = $('#test-answer'); output.hidden = false; output.textContent = `${result.answer}\n\n${result.sources.length ? 'Source: ' + result.sources.map(source => source.title).join(', ') : 'No source found.'}`;
}));
$('#voice-file').addEventListener('change', () => { state.recording = null; const file = $('#voice-file').files[0]; if (file) showAudio(file); });
$('#record-voice').addEventListener('click', run(async () => {
  if (state.recorder) { state.recorder.finish(); return; }
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('Microphone recording requires localhost or HTTPS.');
  state.recorder = createReferenceRecorder({
    onStart: () => { $('#record-voice').textContent = 'Finish recording'; $('#record-status').textContent = 'Recording…'; },
    onProgress: seconds => { $('#record-status').textContent = `Recording ${seconds.toFixed(1)} / 20 seconds`; },
    onComplete: ({chunks, sampleRate}) => { state.recording = wavBlob(chunks, sampleRate); $('#voice-file').value = ''; showAudio(state.recording); state.recorder = null; $('#record-voice').textContent = 'Record again'; $('#record-status').textContent = 'Recording ready'; },
    onError: error => { state.recorder = null; $('#record-voice').textContent = 'Record voice'; $('#record-status').textContent = 'Try recording again'; notice(error.message, true); },
  });
  await state.recorder.start();
}));
$('#voice-form').addEventListener('submit', run(async () => {
  const audio = state.recording || $('#voice-file').files[0];
  if (!audio) throw new Error('Record or upload a voice first.');
  const body = new FormData(); body.set('name', $('#voice-name').value); body.set('transcript', $('#voice-text').value); body.set('consent', String($('#voice-consent').checked)); body.set('audio', audio, 'reference.wav');
  await api(`/api/businesses/${state.id}/voice`, {method: 'POST', body}); badge(false, true);
  notice('Voice saved. Select Prepare for calls before connecting your number.');
}));
$('#prepare-voice').addEventListener('click', run(async () => {
  const button = $('#prepare-voice'); button.disabled = true; notice('Preparing the voice. This can take a little while.');
  try { const result = await api(`/api/businesses/${state.id}/voice/prepare`, {method: 'POST'}); badge(result.prepared, true); notice(result.prepared ? 'Custom voice ready for calls.' : 'Voice is busy. Try again shortly.'); }
  finally { button.disabled = false; }
}));
$('#preview-clone').addEventListener('click', run(async () => {
  const headers = {'Content-Type': 'application/json'};
  if (state.key) headers.Authorization = `Bearer ${state.key}`;
  notice('Generating the custom voice preview…');
  const response = await fetch(`/api/businesses/${state.id}/voice/preview`, {method: 'POST', headers, body: JSON.stringify({text: 'Hello, thank you for calling. How can I help you today?', language: 'en'})});
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.error?.message || 'Could not preview the voice.'); }
  const audio = $('#clone-preview'); if (audio.src.startsWith('blob:')) URL.revokeObjectURL(audio.src);
  audio.src = URL.createObjectURL(await response.blob()); audio.hidden = false; await audio.play(); notice('Playing your custom voice.');
}));
$('#rotate-keys').addEventListener('click', run(async () => {
  if (!confirm('Replace both keys? Existing Exotel and business connections will need the new keys.')) return;
  const result = await api(`/api/businesses/${state.id}/rotate-keys`, {method: 'POST'});
  showCredentials(state.id, result.access_key, result.stream_key);
  notice('Keys replaced. Save the new keys and update Exotel.');
}));
$('#delete-voice').addEventListener('click', run(async () => {
  if (!confirm('Delete this business’s custom voice?')) return;
  await api(`/api/businesses/${state.id}/voice`, {method: 'DELETE'}); badge(false, false); notice('Custom voice deleted. Calls will not use a replacement voice.');
}));
window.addEventListener('beforeunload', (event) => { if (state.dirty) event.preventDefault(); });
