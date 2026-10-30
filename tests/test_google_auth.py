import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from google.auth import crypt, jwt
from google.auth.exceptions import GoogleAuthError
from pydantic import SecretStr

from src.app import create_app
from src.google_auth import verify_identity

PASSWORD = 'a-long-and-secure-password'


@pytest.fixture
def google_settings(settings):
    return settings.model_copy(update={
        'google_client_id': 'example.apps.googleusercontent.com',
        'google_client_secret': SecretStr('google-secret'),
        'google_redirect_uri': 'http://127.0.0.1:8000/api/auth/google/callback',
    })


def begin(client):
    response = client.get('/api/auth/google/start', follow_redirects=False)
    assert response.status_code == 303
    params = parse_qs(urlsplit(response.headers['location']).query)
    assert params['code_challenge_method'] == ['S256']
    assert params['scope'] == ['openid email']
    assert 'HttpOnly' in response.headers['set-cookie']
    return params['state'][0], params


def callback(client, state, **params):
    return client.get('/api/auth/google/callback', params={'state': state, **params}, follow_redirects=False)


def local_signup(client, email):
    response = client.post('/api/auth/signup', json={'email': email, 'password': PASSWORD})
    assert response.status_code == 201
    return response.json()


def create_business(client, session):
    response = client.post('/api/onboarding/business', headers={'X-CSRF-Token': session['csrf']}, json={
        'name': 'My restaurant', 'kind': 'restaurant', 'greeting': 'Hello', 'phone': None,
    })
    assert response.status_code == 201
    return response.json()['business']['id']


def test_google_signup_signin_preserves_workspace_and_checks_state(google_settings, monkeypatch):
    identity = {'subject': 'google-sub-1', 'email': 'owner@gmail.com', 'authoritative': True}
    exchanges = []
    async def exchange(config, code, verifier, nonce):
        exchanges.append((code, verifier, nonce))
        return identity
    monkeypatch.setattr('src.app.exchange_code', exchange)
    app = create_app(google_settings)
    with TestClient(app, base_url='http://127.0.0.1:8000') as client, TestClient(app, base_url='http://127.0.0.1:8000') as other:
        assert client.get('/api/auth/capabilities').json()['google'] is True
        state, params = begin(client)
        assert callback(other, state, code='code').headers['location'] == '/login?google=failed'
        assert not exchanges
        response = callback(client, state, code='code')
        assert response.headers['location'] == '/app'
        assert response.headers['referrer-policy'] == 'no-referrer'
        assert exchanges[0][2] == params['nonce'][0]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(exchanges[0][1].encode()).digest()).rstrip(b'=').decode()
        assert challenge == params['code_challenge'][0]
        session = client.get('/api/auth/me').json()
        assert session['email'] == 'owner@gmail.com' and session['google_linked']
        business_id = create_business(client, session)
        assert callback(client, state, code='code').headers['location'] == '/login?google=failed'
        assert client.post('/api/auth/logout', headers={'X-CSRF-Token': session['csrf']}).status_code == 204
        identity['email'] = 'renamed@gmail.com'
        state, _ = begin(client)
        assert callback(client, state, code='another-code').headers['location'] == '/app'
        assert client.get('/api/auth/me').json()['business_id'] == business_id
        assert client.get(f'/api/businesses/{business_id}/knowledge').status_code == 200
        assert other.get(f'/api/businesses/{business_id}/knowledge').status_code == 401


def test_existing_account_auto_link_and_non_authoritative_link(google_settings, monkeypatch):
    identity = {'subject': 'subject-a', 'email': 'owner@gmail.com', 'authoritative': True}
    async def exchange(*args):
        return identity
    monkeypatch.setattr('src.app.exchange_code', exchange)
    with TestClient(create_app(google_settings), base_url='http://127.0.0.1:8000') as client:
        session = local_signup(client, 'owner@gmail.com')
        business_id = create_business(client, session)
        client.post('/api/auth/logout', headers={'X-CSRF-Token': session['csrf']})
        state, _ = begin(client)
        assert callback(client, state, code='code').headers['location'] == '/app'
        assert client.get('/api/auth/me').json()['business_id'] == business_id
        session = client.get('/api/auth/me').json()
        client.post('/api/auth/logout', headers={'X-CSRF-Token': session['csrf']})
        identity.update(subject='subject-b', email='someone@external.example', authoritative=False)
        session = local_signup(client, 'someone@external.example')
        business_id = create_business(client, session)
        client.post('/api/auth/logout', headers={'X-CSRF-Token': session['csrf']})
        state, _ = begin(client)
        assert callback(client, state, code='code').headers['location'] == '/login?google=email_not_authoritative'
        assert client.post('/api/auth/login', json={'email':'someone@external.example', 'password':PASSWORD}).status_code == 200
        session = client.get('/api/auth/me').json()
        assert client.post('/api/auth/google/link').status_code == 403
        response = client.post('/api/auth/google/link', headers={'X-CSRF-Token': session['csrf']})
        assert response.status_code == 200
        state = parse_qs(urlsplit(response.json()['url']).query)['state'][0]
        assert callback(client, state, code='code').headers['location'] == '/app?google=linked'
        assert client.get('/api/auth/me').json()['google_linked']
        client.post('/api/auth/logout', headers={'X-CSRF-Token': session['csrf']})
        state, _ = begin(client)
        assert callback(client, state, code='code').headers['location'] == '/app'
        assert client.get('/api/auth/me').json()['business_id'] == business_id


def test_link_is_bound_to_original_session_and_conflicting_subject_is_rejected(google_settings, monkeypatch):
    async def exchange(*args):
        return {'subject': 'claimed-subject', 'email': 'owner@gmail.com', 'authoritative': True}
    monkeypatch.setattr('src.app.exchange_code', exchange)
    with TestClient(create_app(google_settings), base_url='http://127.0.0.1:8000') as client:
        first = local_signup(client, 'first@example.com')
        response = client.post('/api/auth/google/link', headers={'X-CSRF-Token': first['csrf']})
        state = parse_qs(urlsplit(response.json()['url']).query)['state'][0]
        second = local_signup(client, 'second@example.com')
        assert callback(client, state, code='code').headers['location'] == '/app?google=link_session_expired'
        assert not client.get('/api/auth/me').json()['google_linked']
        response = client.post('/api/auth/google/link', headers={'X-CSRF-Token': second['csrf']})
        state = parse_qs(urlsplit(response.json()['url']).query)['state'][0]
        assert callback(client, state, code='code').headers['location'] == '/app?google=linked'
        assert client.post('/api/auth/login', json={'email':'first@example.com', 'password':PASSWORD}).status_code == 200
        first = client.get('/api/auth/me').json()
        response = client.post('/api/auth/google/link', headers={'X-CSRF-Token': first['csrf']})
        state = parse_qs(urlsplit(response.json()['url']).query)['state'][0]
        assert callback(client, state, code='code').headers['location'] == '/app?google=already_linked'
        assert not client.get('/api/auth/me').json()['google_linked']


def test_unverified_existing_email_and_cancelled_flow(google_settings, monkeypatch):
    async def exchange(*args):
        return {'subject':'subject', 'email':'unverified@gmail.com', 'authoritative':True}
    monkeypatch.setattr('src.app.exchange_code', exchange)
    app = create_app(google_settings)
    with TestClient(app, base_url='http://127.0.0.1:8000') as client:
        session = local_signup(client, 'unverified@gmail.com')
        client.post('/api/auth/logout', headers={'X-CSRF-Token': session['csrf']})
        with app.state.businesses.connect() as db:
            db.execute('UPDATE users SET email_verified = 0 WHERE email = ?', ('unverified@gmail.com',))
        state, _ = begin(client)
        assert callback(client, state, code='code').headers['location'] == '/login?google=email_unverified'
        state, _ = begin(client)
        assert callback(client, state, error='access_denied').headers['location'] == '/login?google=cancelled'
        state, _ = begin(client)
        with app.state.businesses.connect() as db:
            db.execute('UPDATE oauth_states SET expires_at = 0')
        assert callback(client, state, code='code').headers['location'] == '/login?google=failed'
        assert client.get('/api/auth/me').status_code == 401


def test_config_disabled_without_credentials_and_rejects_wrong_origin(settings, google_settings):
    with TestClient(create_app(settings)) as client:
        assert not client.get('/api/auth/capabilities').json()['google']
        assert client.get('/api/auth/google/start').status_code == 503
    with TestClient(create_app(google_settings)) as client:
        assert client.get('/api/auth/google/start').status_code == 400


def test_google_id_token_signature_and_claims_are_verified(google_settings, monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = private_key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    class CertResponse:
        status = 200
        data = ('{"key-1":' + json.dumps(public) + '}').encode()
    monkeypatch.setattr('src.google_auth.GoogleRequest', lambda: lambda *args, **kwargs: CertResponse())
    signer = crypt.RSASigner.from_string(pem)
    claims = {'iss':'https://accounts.google.com', 'aud':google_settings.google_client_id, 'sub':'stable-id',
              'email':'owner@gmail.com', 'email_verified':True, 'nonce':'nonce-1', 'iat':int(time.time()), 'exp':int(time.time()) + 300}
    def token(overrides):
        return jwt.encode(signer, {**claims, **overrides}, key_id='key-1').decode()
    assert verify_identity(token({}), google_settings, 'nonce-1') == {'subject':'stable-id','email':'owner@gmail.com','authoritative':True}
    with pytest.raises(ValueError):
        verify_identity(token({'nonce':'other'}), google_settings, 'nonce-1')
    with pytest.raises(ValueError):
        verify_identity(token({'aud':'another-client'}), google_settings, 'nonce-1')
    with pytest.raises(ValueError):
        verify_identity(token({'email_verified':False}), google_settings, 'nonce-1')
    with pytest.raises((ValueError, GoogleAuthError)):
        verify_identity(token({'iss':'https://evil.example'}), google_settings, 'nonce-1')
    with pytest.raises(ValueError):
        verify_identity(token({'exp':int(time.time()) - 300}), google_settings, 'nonce-1')
    assert not verify_identity(token({'email':'owner@external.example'}), google_settings, 'nonce-1')['authoritative']
    assert verify_identity(token({'email':'owner@company.example', 'hd':'company.example'}), google_settings, 'nonce-1')['authoritative']
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    forged = jwt.encode(crypt.RSASigner.from_string(other_key), claims, key_id='key-1').decode()
    with pytest.raises(ValueError):
        verify_identity(forged, google_settings, 'nonce-1')
