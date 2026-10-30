import sqlite3

from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.app import create_app
from src.businesses import BusinessStore

PASSWORD = 'a-sufficiently-long-password'


def signup(client, email):
    response = client.post('/api/auth/signup', json={'email': email, 'password': PASSWORD})
    assert response.status_code == 201, response.text
    return response.json()


def csrf(session):
    return {'X-CSRF-Token': session['csrf']}


def workspace(client, session, name, kind='restaurant'):
    response = client.post('/api/onboarding/business', headers=csrf(session), json={
        'name': name, 'kind': kind, 'phone': None, 'greeting': f'Welcome to {name}.', 'language': 'en',
    })
    assert response.status_code == 201, response.text
    return response.json()['business']


def test_public_pages_and_operator_gate(settings):
    config = settings.model_copy(update={'api_token': SecretStr('operator-secret')})
    with TestClient(create_app(config)) as client:
        assert client.get('/').status_code == 200
        assert 'Be there for' in client.get('/').text
        assert client.get('/signup').status_code == 200
        assert client.get('/login').status_code == 200
        assert client.get('/app', follow_redirects=False).headers['location'] == '/login'
        assert client.get('/testing', follow_redirects=False).headers['location'] == '/internal'
        assert client.get('/business', follow_redirects=False).headers['location'] == '/internal'
        assert client.get('/static/index.html').status_code == 404
        assert client.get('/static/business.html').status_code == 404
        assert client.get('/api/status').status_code == 401
        assert client.post('/api/operator/login', json={'token': 'wrong'}).status_code == 401
        assert client.post('/api/operator/login', json={'token': 'operator-secret'}).status_code == 200
        assert client.get('/testing').status_code == 200
        assert 'voice-panel' in client.get('/testing').text
        assert client.get('/api/status').status_code == 200
        assert client.post('/api/operator/logout').status_code == 204
        assert client.get('/testing', follow_redirects=False).headers['location'] == '/internal'


def test_signup_sessions_and_csrf(settings):
    with TestClient(create_app(settings)) as client:
        assert client.post('/api/auth/signup', json={'email': 'bad', 'password': PASSWORD}).status_code == 422
        assert client.post('/api/auth/signup', json={'email': 'hello@example.com', 'password': 'short'}).status_code == 422
        session = signup(client, 'Hello@Example.com')
        assert session['email'] == 'hello@example.com' and session['business_id'] is None
        cookie = client.cookies.get('listen_session')
        assert cookie and 'HttpOnly' in client.post('/api/auth/login', json={'email': 'hello@example.com', 'password': PASSWORD}).headers['set-cookie']
        session = client.get('/api/auth/me').json()
        assert client.post('/api/onboarding/business', json={'name': 'Oak', 'kind': 'restaurant', 'greeting': 'Hello'}).status_code == 403
        assert client.post('/api/auth/logout').status_code == 403
        assert client.post('/api/auth/logout', headers={**csrf(session), 'Origin': 'https://evil.example'}).status_code == 403
        assert client.post('/api/auth/logout', headers=csrf(session)).status_code == 204
        assert client.get('/api/auth/me').status_code == 401
        assert client.post('/api/auth/login', json={'email': 'hello@example.com', 'password': 'wrong'}).status_code == 401
        assert client.post('/api/auth/login', json={'email': 'hello@example.com', 'password': PASSWORD}).status_code == 200
        assert client.post('/api/auth/signup', json={'email': 'HELLO@example.com', 'password': PASSWORD}).status_code == 409
        with sqlite3.connect(settings.businesses_dir / 'businesses.sqlite3') as db:
            stored = db.execute('SELECT password_hash FROM users').fetchone()[0]
            assert stored.startswith('scrypt$') and PASSWORD not in stored


def test_self_service_tenants_are_isolated_and_onboarding_resumes(settings):
    config = settings.model_copy(update={'api_token': SecretStr('operator-secret')})
    app = create_app(config)
    with TestClient(app) as first, TestClient(app) as second:
        a = signup(first, 'owner-a@example.com')
        b = signup(second, 'owner-b@example.com')
        business_a = workspace(first, a, 'Oak Cafe')
        business_b = workspace(second, b, 'Mango Clinic', 'clinic')
        assert business_a['phone'] is None and business_b['phone'] is None
        aid, bid = business_a['id'], business_b['id']
        assert first.get(f'/api/businesses/{bid}/knowledge').status_code == 404
        assert second.get(f'/api/businesses/{aid}/knowledge').status_code == 404
        assert first.get('/api/businesses').status_code == 401
        entries = {'entries': [{'id':'hours','title':'Hours','questions':['When are you open?'],'answer_en':'We open at 9.'}]}
        assert first.put(f'/api/businesses/{aid}/knowledge', json=entries).status_code == 403
        assert first.put(f'/api/businesses/{aid}/knowledge', headers=csrf(a), json=entries).status_code == 200
        assert second.put(f'/api/businesses/{aid}/knowledge', headers=csrf(b), json=entries).status_code == 404
        assert first.post('/api/onboarding/progress', headers=csrf(a), json={'step':3}).status_code == 200
        assert first.get('/api/auth/me').json()['onboarding_step'] == 3
        assert first.get('/app').status_code == 200
        assert first.post(f'/api/businesses/{aid}/stream-key', headers=csrf(a)).status_code == 409
        body = {'name':'Oak Cafe','kind':'restaurant','phone':'+918000000001','greeting':'Welcome to Oak Cafe.','language':'en'}
        assert first.put(f'/api/businesses/{aid}', headers=csrf(a), json=body).status_code == 200
        assert first.post(f'/api/businesses/{aid}/stream-key', headers=csrf(a)).json()['stream_key']
        assert second.post(f'/api/businesses/{aid}/stream-key', headers=csrf(b)).status_code == 404
        assert first.post('/api/onboarding/business', headers=csrf(a), json=body).status_code == 409


def test_v2_business_database_migrates_without_losing_existing_data(tmp_path):
    root = tmp_path / 'businesses'
    root.mkdir()
    with sqlite3.connect(root / 'businesses.sqlite3') as db:
        db.execute('''CREATE TABLE businesses (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
            phone TEXT NOT NULL UNIQUE, language TEXT NOT NULL, greeting TEXT NOT NULL, access_hash TEXT NOT NULL,
            stream_hash TEXT NOT NULL, knowledge TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, voice_id TEXT)''')
        db.execute("INSERT INTO businesses VALUES ('id1','Old Cafe','restaurant','918000000001','en','Hello','a','b','[]',7,'voice')")
        db.execute('PRAGMA user_version = 2')
    store = BusinessStore(root)
    existing = store.get('id1')
    assert (existing['name'], existing['phone'], existing['version'], existing['voice_id']) == ('Old Cafe', '918000000001', 7, 'voice')
    with store.connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 5
        assert db.execute("SELECT name FROM sqlite_master WHERE name = 'sessions'").fetchone()


def test_email_verification_and_one_time_password_reset(settings, monkeypatch):
    sent = []
    monkeypatch.setattr('src.app.send_account_email', lambda config, email, purpose, token: sent.append((email, purpose, token)))
    config = settings.model_copy(update={'api_token': SecretStr('operator-secret'), 'public_origin': 'https://listen.example',
                                         'smtp_host': 'smtp.example', 'smtp_from': 'hello@example.com'})
    with TestClient(create_app(config), base_url='https://listen.example') as client:
        response = client.post('/api/auth/signup', json={'email': 'owner@example.com', 'password': PASSWORD})
        assert response.status_code == 201 and response.json()['verification_required']
        assert client.get('/api/auth/me').status_code == 401
        assert client.post('/api/auth/login', json={'email': 'owner@example.com', 'password': PASSWORD}).status_code == 403
        assert sent[-1][:2] == ('owner@example.com', 'verify')
        verify_token = sent[-1][2]
        assert client.post('/api/auth/verify-email', json={'token': verify_token}).status_code == 200
        assert client.get('/api/auth/me').status_code == 200
        assert client.post('/api/auth/verify-email', json={'token': verify_token}).status_code == 400
        assert client.post('/api/auth/email-link?purpose=reset', json={'email': 'owner@example.com'}).status_code == 200
        reset_token = sent[-1][2]
        assert sent[-1][1] == 'reset'
        new_password = 'another-long-secure-password'
        assert client.post('/api/auth/reset-password', json={'token': reset_token, 'password': new_password}).status_code == 200
        assert client.get('/api/auth/me').status_code == 401
        assert client.post('/api/auth/reset-password', json={'token': reset_token, 'password': new_password}).status_code == 400
        assert client.post('/api/auth/login', json={'email': 'owner@example.com', 'password': PASSWORD}).status_code == 401
        assert client.post('/api/auth/login', json={'email': 'owner@example.com', 'password': new_password}).status_code == 200


def test_public_signup_requires_email_delivery_and_retries_after_failure(settings, monkeypatch):
    config = settings.model_copy(update={'api_token': SecretStr('operator-secret'), 'public_origin': 'https://listen.example'})
    with TestClient(create_app(config), base_url='https://listen.example') as client:
        assert client.post('/api/auth/signup', json={'email': 'owner@example.com', 'password': PASSWORD}).status_code == 503
    config = config.model_copy(update={'smtp_host':'smtp.example', 'smtp_from':'hello@example.com'})
    def fail(*args):
        raise OSError('mail offline')
    monkeypatch.setattr('src.app.send_account_email', fail)
    with TestClient(create_app(config), base_url='https://listen.example') as client:
        assert client.post('/api/auth/signup', json={'email': 'owner@example.com', 'password': PASSWORD}).status_code == 503
        monkeypatch.setattr('src.app.send_account_email', lambda *args: None)
        assert client.post('/api/auth/signup', json={'email': 'owner@example.com', 'password': PASSWORD}).status_code == 201
