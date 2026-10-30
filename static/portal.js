import {createReferenceRecorder} from './reference-recorder.js';

const $ = selector => document.querySelector(selector);
const state = {session: null, business: null, entries: [], version: 0, voicePrepared: false, dirty: false, view: '', recorder: null, recording: null, preview: null};

function notice(message, error = false) {
  const node = $('#portal-notice'); node.hidden = !message; node.textContent = message; node.dataset.error = String(error);
}
async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.method && !['GET', 'HEAD'].includes(options.method)) headers.set('X-CSRF-Token', state.session?.csrf || '');
  const response = await fetch(path, {...options, headers});
  if (response.status === 401) { location.assign('/login'); throw new Error('Please sign in again.'); }
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.error?.message || `Request failed (${response.status}).`); }
  return response.status === 204 ? null : response.json();
}
function action(fn) {
  return async event => {
    event?.preventDefault(); const target = event?.currentTarget;
    const button = target?.tagName === 'FORM' ? target.querySelector('button[type="submit"]') : target;
    if (button?.tagName === 'BUTTON') button.disabled = true;
    notice('');
    try { await fn(event); } catch (error) { notice(error.message, true); }
    finally { if (button?.tagName === 'BUTTON') button.disabled = false; }
  };
}
function el(tag, value, cls) { const node = document.createElement(tag); node.textContent = value; if (cls) node.className = cls; return node; }
function showView(view) {
  if (state.dirty && state.view === 'knowledge' && view !== 'knowledge') throw new Error('Save your knowledge changes before leaving this page.');
  state.view = view;
  for (const section of document.querySelectorAll('#workspace .view')) section.hidden = section.id !== `view-${view}`;
  for (const button of document.querySelectorAll('.portal-nav button')) button.setAttribute('aria-current', String(button.dataset.view === view));
  $('#top-label').textContent = view === 'overview' ? 'YOUR WORKSPACE' : view.toUpperCase();
  if (view !== 'phone') { $('#stream-details').hidden = true; $('#stream-url').textContent = ''; }
  window.scrollTo({top:0, behavior:'auto'});
}
function refreshStatus() {
  const business = state.business;
  $('#side-business').textContent = business.name; $('#overview-name').textContent = business.name;
  const label = {restaurant:'Restaurant', clinic:'Clinic', support:'Customer support'}[business.kind];
  $('#overview-kind').textContent = label;
  $('#business-name').value = business.name; $('#business-kind').value = business.kind; $('#business-greeting').value = business.greeting;
  $('#phone-number').value = business.phone || '';
  $('#status-knowledge').textContent = state.entries.length > 1 ? `${state.entries.length} answers` : 'To do';
  $('#status-voice').textContent = state.voicePrepared ? 'Prepared' : business.voice_id ? 'Needs preparation' : 'To do';
  $('#status-phone').textContent = business.phone ? 'Number added' : 'To do';
  $('#voice-status').textContent = state.voicePrepared ? 'Custom voice ready' : business.voice_id ? 'Voice saved · prepare for calls' : 'No voice added yet';
  $('#voice-dot').classList.toggle('ready', state.voicePrepared);
  $('#phone-status').textContent = business.phone ? `Number added · ${business.phone}` : 'No number added';
}
async function loadBusiness() {
  const data = await api(`/api/businesses/${encodeURIComponent(state.session.business_id)}/knowledge`);
  state.business = data.business; state.entries = data.entries; state.version = data.business.version; state.voicePrepared = data.voice_prepared; state.dirty = false;
  $('#side-business').textContent = state.business.name;
  document.querySelectorAll('.portal-nav button').forEach(button => { button.disabled = false; });
  $('#welcome').hidden = true; $('#workspace').hidden = false; refreshStatus(); renderEntries();
}
function renderEntries() {
  const list = $('#knowledge-list'); list.replaceChildren();
  for (const [index, entry] of state.entries.entries()) {
    const box = document.createElement('div'); box.className = 'knowledge-item';
    const top = document.createElement('div'); top.append(el('strong', entry.document_name || `Answer ${index + 1}`));
    const remove = el('button', 'Remove', 'link-button danger'); remove.type = 'button';
    remove.addEventListener('click', () => { state.entries.splice(index, 1); state.dirty = true; renderEntries(); }); top.append(remove); box.append(top);
    const questionLabel = el('label', 'Example question'); const question = document.createElement('input'); question.required = true; question.maxLength = 300; question.value = entry.questions[0];
    question.addEventListener('input', () => { entry.questions[0] = question.value; entry.title = question.value.slice(0, 100); state.dirty = true; }); questionLabel.append(question); box.append(questionLabel);
    const answerLabel = el('label', 'Answer'); const answer = document.createElement('textarea'); answer.required = true; answer.maxLength = 1200; answer.rows = 3; answer.value = entry.answer_en;
    answer.addEventListener('input', () => { entry.answer_en = answer.value; state.dirty = true; }); answerLabel.append(answer); box.append(answerLabel); list.append(box);
  }
}
async function saveKnowledge() {
  if (!state.entries.length) throw new Error('Add at least one answer.');
  if (!$('#knowledge-form').reportValidity()) throw new Error('Complete the question and answer fields.');
  const result = await api(`/api/businesses/${state.business.id}/knowledge`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({entries:state.entries, expected_version:state.version})});
  state.version = result.business.version; state.business = result.business; state.dirty = false; refreshStatus();
}
async function progress(step) {
  await api('/api/onboarding/progress', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({step})});
  state.session.onboarding_step = step; showView({3:'voice', 4:'phone', 5:'overview'}[step]);
}
function wavBlob(chunks, rate) {
  const count = chunks.reduce((n, chunk) => n + chunk.length, 0); const bytes = new ArrayBuffer(44 + count * 2); const view = new DataView(bytes);
  const write = (at, value) => [...value].forEach((char, i) => view.setUint8(at + i, char.charCodeAt(0)));
  write(0, 'RIFF'); view.setUint32(4, 36 + count * 2, true); write(8, 'WAVE'); write(12, 'fmt '); view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); write(36, 'data'); view.setUint32(40, count * 2, true);
  let offset = 44; for (const chunk of chunks) for (const value of chunk) { const sample = Math.max(-1, Math.min(1, value)); view.setInt16(offset, Math.round(sample * (sample < 0 ? 32768 : 32767)), true); offset += 2; }
  return new Blob([bytes], {type:'audio/wav'});
}
function showAudio(audio) { if (state.preview) URL.revokeObjectURL(state.preview); state.preview = URL.createObjectURL(audio); $('#voice-preview').src = state.preview; $('#voice-preview').hidden = false; }

$('#welcome-form').addEventListener('submit', action(async () => {
  const name = $('#welcome-name').value.trim();
  const body = {name, kind:$('#welcome-kind').value, phone:null, language:'en', greeting:`Hello, thank you for calling ${name}. How can I help you?`};
  const result = await api('/api/onboarding/business', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  state.session.business_id = result.business.id; state.session.onboarding_step = 2; await loadBusiness(); showView('knowledge');
  notice('Your workspace is ready. Let’s add what your agent should know.');
}));
$('#business-form').addEventListener('submit', action(async () => {
  const body = {name:$('#business-name').value, kind:$('#business-kind').value, greeting:$('#business-greeting').value, phone:state.business.phone, language:'en'};
  state.business = await api(`/api/businesses/${state.business.id}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  state.version = state.business.version; refreshStatus(); notice('Business details saved.');
}));
$('#add-entry').addEventListener('click', () => { state.entries.push({id:`entry_${crypto.randomUUID().replaceAll('-', '')}`, title:'', questions:[''], answer_en:'', answer_hi:null, document_name:null}); state.dirty = true; renderEntries(); });
$('#knowledge-form').addEventListener('submit', action(async () => { await saveKnowledge(); notice('Knowledge saved for new calls.'); }));
$('#import-form').addEventListener('submit', action(async () => {
  if (state.dirty) throw new Error('Save your knowledge changes before importing.');
  const body = new FormData(); body.set('file', $('#knowledge-file').files[0]);
  const result = await api(`/api/businesses/${state.business.id}/knowledge/import`, {method:'POST', body});
  state.entries = result.entries; state.business = result.business; state.version = result.business.version; renderEntries(); refreshStatus(); $('#import-form').reset(); notice('Text imported. Review it and try a few questions.');
}));
$('#test-form').addEventListener('submit', action(async () => {
  const result = await api(`/api/businesses/${state.business.id}/ask`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question:$('#test-question').value, language:'en'})});
  const node = $('#test-answer'); node.hidden = false; node.textContent = `${result.answer}\n\n${result.sources.length ? 'Source: ' + result.sources.map(source => source.title).join(', ') : 'No source found.'}`;
}));
$('#voice-file').addEventListener('change', () => { state.recording = null; const file = $('#voice-file').files[0]; if (file) showAudio(file); });
$('#record-voice').addEventListener('click', action(async () => {
  if (state.recorder) { state.recorder.finish(); return; }
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('Microphone recording requires localhost or HTTPS.');
  state.recorder = createReferenceRecorder({
    onStart:() => { $('#record-voice').textContent = 'Finish recording'; $('#record-status').textContent = 'Recording…'; },
    onProgress:seconds => { $('#record-status').textContent = `Recording ${seconds.toFixed(1)} / 20 seconds`; },
    onComplete:({chunks,sampleRate}) => { state.recording = wavBlob(chunks,sampleRate); $('#voice-file').value = ''; showAudio(state.recording); state.recorder = null; $('#record-voice').textContent = 'Record again'; $('#record-status').textContent = 'Recording ready'; },
    onError:error => { state.recorder = null; $('#record-voice').textContent = 'Record voice'; $('#record-status').textContent = 'Try recording again'; notice(error.message, true); },
  });
  await state.recorder.start();
}));
$('#voice-form').addEventListener('submit', action(async () => {
  const audio = state.recording || $('#voice-file').files[0]; if (!audio) throw new Error('Record or upload a voice first.');
  const body = new FormData(); body.set('name', $('#voice-name').value); body.set('transcript', $('#voice-text').value); body.set('consent', String($('#voice-consent').checked)); body.set('audio', audio, 'reference.wav');
  const result = await api(`/api/businesses/${state.business.id}/voice`, {method:'POST', body}); state.business.voice_id = result.voice.id; state.voicePrepared = false; refreshStatus(); notice('Voice saved. Select Prepare for calls when you are ready.');
}));
$('#prepare-voice').addEventListener('click', action(async () => {
  notice('Preparing your voice. This can take a little while.');
  const result = await api(`/api/businesses/${state.business.id}/voice/prepare`, {method:'POST'}); state.voicePrepared = result.prepared; refreshStatus(); notice(result.prepared ? 'Your custom voice is ready.' : 'Voice is busy. Try again shortly.');
}));
$('#preview-clone').addEventListener('click', action(async () => {
  notice('Generating your voice preview…');
  const response = await fetch(`/api/businesses/${state.business.id}/voice/preview`, {method:'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token':state.session.csrf}, body:JSON.stringify({text:'Hello, thank you for calling. How can I help you today?', language:'en'})});
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.error?.message || 'Could not preview the voice.'); }
  const audio = $('#clone-preview'); if (audio.src.startsWith('blob:')) URL.revokeObjectURL(audio.src); audio.src = URL.createObjectURL(await response.blob()); audio.hidden = false; await audio.play(); notice('Playing your custom voice.');
}));
$('#delete-voice').addEventListener('click', action(async () => {
  if (!confirm('Delete your custom voice? Calls will need a new voice before they can be answered.')) return;
  await api(`/api/businesses/${state.business.id}/voice`, {method:'DELETE'}); state.business.voice_id = null; state.voicePrepared = false; refreshStatus(); notice('Your custom voice was deleted.');
}));
$('#phone-form').addEventListener('submit', action(async () => {
  const body = {name:state.business.name, kind:state.business.kind, greeting:state.business.greeting, phone:$('#phone-number').value.trim() || null, language:'en'};
  state.business = await api(`/api/businesses/${state.business.id}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}); state.version = state.business.version; refreshStatus(); notice('Number saved. Test the connection before taking calls.');
}));
$('#generate-stream').addEventListener('click', action(async () => {
  if (!state.business.phone) throw new Error('Save your Exotel destination number first.');
  if (!confirm('Generate a new connection key? Any previous Exotel connection key will stop working for new calls.')) return;
  const result = await api(`/api/businesses/${state.business.id}/stream-key`, {method:'POST'});
  const host = location.protocol === 'https:' ? location.host : 'YOUR_PUBLIC_HOST';
  $('#stream-url').textContent = `wss://${result.business_id}:${result.stream_key}@${host}/telephony/exotel/${result.business_id}`;
  $('#stream-details').hidden = false; notice('Connection key generated. Save the URL in Exotel now.');
}));
$('#copy-stream').addEventListener('click', action(async () => { await navigator.clipboard.writeText($('#stream-url').textContent); notice('Secure URL copied.'); }));
for (const button of document.querySelectorAll('[data-view], [data-go]')) button.addEventListener('click', action(async () => showView(button.dataset.view || button.dataset.go)));
for (const button of document.querySelectorAll('[data-next]')) button.addEventListener('click', action(async () => { const step = Number(button.dataset.next); if (step === 3 && state.dirty) await saveKnowledge(); await progress(step); }));
$('#logout').addEventListener('click', action(async () => { if (state.dirty) throw new Error('Save your knowledge changes before signing out.'); await api('/api/auth/logout', {method:'POST'}); location.assign('/'); }));
$('#link-google').addEventListener('click', action(async () => {
  if (state.dirty) throw new Error('Save your knowledge changes before connecting Google.');
  const result = await api('/api/auth/google/link', {method:'POST'}); location.assign(result.url);
}));
window.addEventListener('beforeunload', event => { if (state.dirty) event.preventDefault(); });

try {
  state.session = await api('/api/auth/me'); $('#side-email').textContent = state.session.email; $('#account-email').textContent = state.session.email;
  fetch('/api/auth/capabilities').then(response => response.json()).then(result => {
    $('#link-google').hidden = !result.google;
    if (state.session.google_linked) { $('#link-google').textContent = 'Google connected ✓'; $('#link-google').disabled = true; }
  }).catch(() => {});
  if (state.session.business_id) { await loadBusiness(); showView(({2:'knowledge', 3:'voice', 4:'phone'})[state.session.onboarding_step] || 'overview'); }
  else { $('#welcome').hidden = false; }
  const google = new URLSearchParams(location.search).get('google');
  if (google) {
    const messages = {linked:'Google is connected to this workspace.', cancelled:'Google connection was cancelled.', already_linked:'This Google account or workspace is already connected to another account.', link_session_expired:'Your session expired. Sign in again to connect Google.'};
    notice(messages[google] || 'Google connection could not be completed. Please try again.', google !== 'linked');
    history.replaceState(null, '', location.pathname);
  }
} catch (error) { notice(error.message, true); }
