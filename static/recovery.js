const $ = selector => document.querySelector(selector);
const path = location.pathname;
const token = location.hash.slice(1);
if (token) history.replaceState(null, '', path);
function error(message) { $('#recovery-error').textContent = message; $('#recovery-error').hidden = false; }
async function post(url, body) {
  const response = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const result = await response.json().catch(() => ({})); if (!response.ok) throw new Error(result.error?.message || 'Please try again.'); return result;
}
if (path === '/forgot') {
  $('#recovery-title').textContent = 'Reset your password.'; $('#recovery-copy').textContent = 'Enter your email and we’ll send a reset link if an account exists.'; $('#email-form').hidden = false;
} else if (path === '/reset') {
  $('#recovery-title').textContent = 'Choose a new password.'; $('#recovery-copy').textContent = token ? 'Choose a secure password for your account.' : 'This link is missing or has expired.'; $('#reset-form').hidden = !token;
} else if (token) {
  try { await post('/api/auth/verify-email', {token}); location.assign('/app'); }
  catch (exception) { $('#recovery-title').textContent = 'Check your email.'; $('#recovery-copy').textContent = exception.message; $('#email-form').hidden = false; }
} else {
  $('#recovery-title').textContent = 'Verify your email.'; $('#recovery-copy').textContent = 'Enter your email and we’ll send a verification link if one is needed.'; $('#email-form').hidden = false;
}
$('#email-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.currentTarget.querySelector('button'); button.disabled = true; $('#recovery-error').hidden = true;
  try { await post(`/api/auth/email-link?purpose=${path === '/forgot' ? 'reset' : 'verify'}`, {email:$('#email').value}); $('#email-form').hidden = true; $('#recovery-title').textContent = 'Check your inbox.'; $('#recovery-copy').textContent = 'If an eligible account exists, an email has been sent.'; }
  catch (exception) { error(exception.message); }
  finally { button.disabled = false; }
});
$('#reset-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.currentTarget.querySelector('button'); button.disabled = true; $('#recovery-error').hidden = true;
  try { if ($('#new-password').value !== $('#confirm-password').value) throw new Error('Passwords do not match.'); await post('/api/auth/reset-password', {token, password:$('#new-password').value}); $('#reset-form').hidden = true; $('#recovery-title').textContent = 'Password updated.'; $('#recovery-copy').textContent = 'You can now sign in with your new password.'; }
  catch (exception) { error(exception.message); }
  finally { button.disabled = false; }
});
