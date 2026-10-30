document.querySelector('#operator-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.currentTarget.querySelector('button'); button.disabled = true;
  const error = document.querySelector('#operator-error'); error.hidden = true;
  try {
    const response = await fetch('/api/operator/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({token:document.querySelector('#operator-token').value})});
    if (!response.ok) { const body = await response.json(); throw new Error(body.error?.message || 'Could not sign in.'); }
    location.assign('/testing');
  } catch (exception) { error.textContent = exception.message; error.hidden = false; }
  finally { button.disabled = false; }
});
