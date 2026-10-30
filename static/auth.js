const login = location.pathname === '/login';
const $ = selector => document.querySelector(selector);
if (login) {
  document.title = 'Sign in · Listen';
  $('#auth-kicker').textContent = 'WELCOME BACK';
  $('#auth-title').textContent = 'Good to see you.';
  $('#auth-subtitle').textContent = 'Sign in to continue to your workspace.';
  $('#password').autocomplete = 'current-password'; $('#password').minLength = 1;
  $('#password-hint').hidden = true; $('#auth-submit').firstChild.textContent = 'Sign in ';
  $('#switch-copy').textContent = 'New to Listen?'; $('#switch-link').textContent = 'Create an account'; $('#switch-link').href = '/signup';
}
fetch('/api/auth/capabilities').then(response => response.json()).then(result => {
  $('#google-option').hidden = !result.google || $('#auth-form').hidden;
  if (login) { $('#forgot-link').hidden = !result.email_recovery; $('#verify-link').hidden = !result.email_recovery; }
}).catch(() => {});
const googleError = new URLSearchParams(location.search).get('google');
if (googleError) {
  const messages = {
    email_not_authoritative: 'Create an account or sign in with your email and password first, then connect Google from your workspace.',
    email_unverified: 'Verify your email before connecting this Google account.',
    already_linked: 'This Google account or workspace is already connected to another account.',
    cancelled: 'Google sign-in was cancelled.',
    link_session_expired: 'Your session expired. Sign in again to connect Google.',
  };
  $('#auth-error').textContent = messages[googleError] || 'Google sign-in could not be completed. Please try again.';
  $('#auth-error').hidden = false;
  history.replaceState(null, '', location.pathname);
}
$('#show-password').addEventListener('click', () => { const input = $('#password'); const show = input.type === 'password'; input.type = show ? 'text' : 'password'; $('#show-password').textContent = show ? 'Hide' : 'Show'; });
$('#auth-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = $('#auth-submit'); button.disabled = true; $('#auth-error').hidden = true;
  try {
    const response = await fetch(login ? '/api/auth/login' : '/api/auth/signup', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:$('#email').value, password:$('#password').value})});
    const result = await response.json(); if (!response.ok) throw new Error(result.error?.message || 'Please try again.');
    if (result.verification_required) { $('#auth-form').hidden = true; $('#google-option').hidden = true; $('#check-email').hidden = false; $('#auth-kicker').textContent = 'ALMOST THERE'; $('#auth-title').textContent = 'Check your inbox.'; $('#auth-subtitle').hidden = true; }
    else location.assign('/app');
  } catch (error) { $('#auth-error').textContent = error.message; $('#auth-error').hidden = false; }
  finally { button.disabled = false; }
});
