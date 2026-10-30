import base64
import io
import math
import struct
import wave

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.websockets import WebSocketDisconnect

from src.app import create_app
from src.audio_stream import wav_packets


class FakeSpeech:
    ready = True

    def transcribe(self, audio, language):
        with wave.open(io.BytesIO(audio)) as wav:
            assert wav.getframerate() == 8000
        return {"text": "When are you open?", "language": language}


class FakeVoice:
    def __init__(self, voice_id):
        self.prepared_voice = voice_id
        self.calls = []

    def synthesize(self, text, language, voice_id):
        assert voice_id == self.prepared_voice
        self.calls.append((text, voice_id))
        return sample_wav(duration=0.1)

    def stream(self, text, language, voice_id, cancel):
        yield from wav_packets(self.synthesize(text, language, voice_id))

    def close(self):
        pass


def sample_wav(duration=3.1):
    rate = 16000
    frames = b''.join(struct.pack('<h', int(5000 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(int(rate * duration)))
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(frames)
    return buffer.getvalue()


def create(client, admin, name, kind, phone):
    response = client.post('/api/businesses', headers=admin, json={
        'name': name, 'kind': kind, 'phone': phone, 'greeting': f'Hello from {name}.', 'language': 'en',
    })
    assert response.status_code == 201, response.text
    return response.json()


def credentials(business):
    return {'Authorization': 'Bearer ' + business['access_key']}


def basic(business):
    value = business['business']['id'] + ':' + business['stream_key']
    return {'Authorization': 'Basic ' + base64.b64encode(value.encode()).decode()}


def entry(answer):
    return {'entries': [{'id': 'hours', 'title': 'Opening hours', 'questions': ['When are you open?'], 'answer_en': answer}]}


@pytest.fixture
def platform(settings):
    config = settings.model_copy(update={'api_token': SecretStr('operator-secret')})
    app = create_app(config, speech=FakeSpeech())
    with TestClient(app) as client:
        yield client


def test_tenant_isolation_knowledge_and_validation(platform):
    admin = {'Authorization': 'Bearer operator-secret'}
    a = create(platform, admin, 'Oak Cafe', 'restaurant', '+918000000001')
    b = create(platform, admin, 'Mango Clinic', 'clinic', '+918000000002')
    aid, bid = a['business']['id'], b['business']['id']
    updated = platform.put(f'/api/businesses/{bid}', headers=credentials(b), json={
        'name': 'Mango Health', 'kind': 'clinic', 'phone': '+918000000002', 'greeting': 'Hello.',
    })
    assert updated.status_code == 200
    knowledge = platform.get(f'/api/businesses/{bid}/knowledge', headers=credentials(b)).json()
    assert knowledge['entries'][0]['answer_en'] == 'This is Mango Health.'
    assert platform.post('/api/businesses', json={}).status_code == 401
    assert platform.post('/api/businesses', headers=admin, json={'name': 'Duplicate', 'kind': 'support', 'phone': '+918000000001', 'greeting': 'Hello'}).status_code == 409
    assert platform.get('/api/businesses', headers=credentials(a)).status_code == 401
    assert platform.get(f'/api/businesses/{bid}/knowledge', headers=credentials(a)).status_code == 401
    assert platform.put(f'/api/businesses/{bid}/knowledge', headers=credentials(a), json=entry('leaked')).status_code == 401
    assert platform.put(f'/api/businesses/{aid}/knowledge', headers=credentials(a), json=entry('We open at 9.')).status_code == 200
    stale = {**entry('Overwritten hours'), 'expected_version': 1}
    assert platform.put(f'/api/businesses/{aid}/knowledge', headers=credentials(a), json=stale).status_code == 409
    assert platform.put(f'/api/businesses/{bid}/knowledge', headers=credentials(b), json=entry('We open at 10.')).status_code == 200
    for business, expected, forbidden in [(a, '9', '10'), (b, '10', '9')]:
        result = platform.post(f"/api/businesses/{business['business']['id']}/ask", headers=credentials(business), json={'question': 'When are you open?'}).json()
        assert expected in result['answer']
        assert forbidden not in result['answer']
        assert [source['id'] for source in result['sources']] == ['hours']
    assert platform.put(f'/api/businesses/{aid}/knowledge', headers=credentials(a), json={'entries': []}).status_code == 422
    clinical = platform.post(f'/api/businesses/{bid}/ask', headers=credentials(b), json={'question': 'What medicine should I take?'}).json()
    assert clinical['needs_human'] and 'medical advice' in clinical['answer']
    assert not clinical['sources']
    assert platform.get('/business').status_code == 200
    rotated = platform.post(f'/api/businesses/{aid}/rotate-keys', headers=admin).json()
    assert platform.get(f'/api/businesses/{aid}/knowledge', headers=credentials(a)).status_code == 401
    assert platform.get(f'/api/businesses/{aid}/knowledge', headers={'Authorization': 'Bearer ' + rotated['access_key']}).status_code == 200


def test_custom_voice_requires_consent_and_is_tenant_scoped(platform):
    admin = {'Authorization': 'Bearer operator-secret'}
    a = create(platform, admin, 'Oak Cafe', 'restaurant', '+918000000001')
    b = create(platform, admin, 'Mango Clinic', 'clinic', '+918000000002')
    aid, bid = a['business']['id'], b['business']['id']
    path = f'/api/businesses/{aid}/voice'
    form = {'name': 'Host', 'transcript': 'Hello welcome to the cafe', 'consent': 'false'}
    audio = {'audio': ('reference.wav', sample_wav(), 'audio/wav')}
    assert platform.post(path, headers=credentials(a), data=form, files=audio).status_code == 422
    form['consent'] = 'true'
    assert platform.post(path, headers=credentials(b), data=form, files=audio).status_code == 401
    response = platform.post(path, headers=credentials(a), data=form, files=audio)
    assert response.status_code == 201, response.text
    voice_id = response.json()['voice']['id']
    assert platform.get(f'/api/businesses/{aid}/knowledge', headers=credentials(a)).json()['business']['voice_id'] == voice_id
    assert platform.get(f'/api/businesses/{bid}/knowledge', headers=credentials(b)).json()['business']['voice_id'] is None
    assert platform.post(f'/api/businesses/{aid}/voice/preview', headers=credentials(a), json={'text': 'Hello'}).status_code == 409
    platform.app.state.business_voices[aid] = FakeVoice(voice_id)
    assert platform.post(f'/api/businesses/{aid}/voice/preview', headers=credentials(b), json={'text': 'Hello'}).status_code == 401
    preview = platform.post(f'/api/businesses/{aid}/voice/preview', headers=credentials(a), json={'text': 'Hello'})
    assert preview.status_code == 200 and preview.content.startswith(b'RIFF')
    # Remove the test worker before the real profile deletion.
    platform.app.state.business_voices.pop(aid)
    assert platform.delete(path, headers=credentials(b)).status_code == 401
    assert platform.delete(path, headers=credentials(a)).status_code == 204
    assert platform.get(f'/api/businesses/{aid}/knowledge', headers=credentials(a)).json()['business']['voice_id'] is None


def test_import_text_is_scoped_bounded_and_idempotent(platform):
    admin = {'Authorization': 'Bearer operator-secret'}
    a = create(platform, admin, 'Oak Cafe', 'restaurant', '+918000000001')
    b = create(platform, admin, 'Mango Clinic', 'clinic', '+918000000002')
    path = f"/api/businesses/{a['business']['id']}/knowledge/import"
    file = {'file': ('menu.txt', b'Vegetarian pizza is available.\n\nOur cafe opens at 9.', 'text/plain')}
    assert platform.post(path, headers=credentials(b), files=file).status_code == 401
    response = platform.post(path, headers=credentials(a), files=file)
    assert response.status_code == 200
    result = response.json()
    assert len(result['entries']) == 3
    answer = platform.post(f"/api/businesses/{a['business']['id']}/ask", headers=credentials(a), json={'question': 'Is vegetarian pizza available?'}).json()
    assert 'Vegetarian pizza is available' in answer['answer']
    again = platform.post(path, headers=credentials(a), files=file).json()
    assert again['business']['version'] == result['business']['version']
    assert len(again['entries']) == 3
    changed = platform.post(path, headers=credentials(a), files={'file': ('menu.txt', b'Vegan pasta is available.', 'text/plain')}).json()
    assert changed['business']['version'] == result['business']['version'] + 1
    assert len(changed['entries']) == 2
    assert all('pizza' not in item['answer_en'].lower() for item in changed['entries'])
    assert any('Vegan pasta' in item['answer_en'] for item in changed['entries'])
    assert platform.post(path, headers=credentials(a), files={'file': ('menu.pdf', b'pdf', 'application/pdf')}).status_code == 422
    assert platform.post(path, headers=credentials(a), files={'file': ('large.txt', b'x' * 1201, 'text/plain')}).status_code == 422
    other = platform.get(f"/api/businesses/{b['business']['id']}/knowledge", headers=credentials(b)).json()
    assert all('pizza' not in item['answer_en'] for item in other['entries'])


def test_exotel_checks_credentials_destination_and_uses_business_voice(platform):
    admin = {'Authorization': 'Bearer operator-secret'}
    a = create(platform, admin, 'Oak Cafe', 'restaurant', '+918000000001')
    b = create(platform, admin, 'Mango Cafe', 'restaurant', '+918000000002')
    aid = a['business']['id']
    assert platform.put(f'/api/businesses/{aid}/knowledge', headers=credentials(a), json=entry('We open at 9.')).status_code == 200
    voice_id = 'a' * 24
    platform.app.state.businesses.set_voice(aid, voice_id)
    voice = FakeVoice(voice_id)
    platform.app.state.business_voices[aid] = voice
    path = f'/telephony/exotel/{aid}'
    with pytest.raises(WebSocketDisconnect):
        with platform.websocket_connect(path, headers=basic(b)) as ws:
            ws.receive_json()
    with platform.websocket_connect(path, headers=basic(a)) as ws:
        ws.send_json({'event': 'connected'})
        ws.send_json({'event': 'start', 'stream_sid': 'stream1', 'start': {'to': '918000000002', 'media_format': {'sample_rate': 8000}}})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    with platform.websocket_connect(path, headers=basic(a)) as ws:
        ws.send_json({'event': 'connected'})
        ws.send_json({'event': 'start', 'stream_sid': 'stream2', 'start': {'to': '918000000001', 'media_format': {'sample_rate': 8000}}})
        greeting = ws.receive_json()
        assert greeting['event'] == 'media'
        assert voice.calls[0] == ('Hello from Oak Cafe.', voice_id)
        speech = base64.b64encode(struct.pack('<h', 7000) * 800).decode()
        silence = base64.b64encode(b'\0\0' * 800).decode()
        for payload in [speech] * 3 + [silence] * 8:
            ws.send_json({'event': 'media', 'stream_sid': 'stream2', 'media': {'payload': payload}})
        for _ in range(5):
            packet = ws.receive_json()
            if packet['event'] == 'media' and len(voice.calls) > 1:
                break
        assert any('9' in text and used == voice_id for text, used in voice.calls)
        ws.send_json({'event': 'stop', 'stream_sid': 'stream2'})


def test_exotel_simultaneous_businesses_keep_voice_and_knowledge_separate(platform):
    admin = {'Authorization': 'Bearer operator-secret'}
    a = create(platform, admin, 'Oak Cafe', 'restaurant', '+918000000001')
    b = create(platform, admin, 'Mango Cafe', 'restaurant', '+918000000002')
    voices = []
    for business, value in [(a, 'We open at 9.'), (b, 'We open at 10.')]:
        bid = business['business']['id']
        platform.put(f'/api/businesses/{bid}/knowledge', headers=credentials(business), json=entry(value))
        voice_id = ('a' if business is a else 'b') * 24
        platform.app.state.businesses.set_voice(bid, voice_id)
        voice = FakeVoice(voice_id)
        platform.app.state.business_voices[bid] = voice
        voices.append(voice)
    with platform.websocket_connect(f"/telephony/exotel/{a['business']['id']}", headers=basic(a)) as first:
        with platform.websocket_connect(f"/telephony/exotel/{b['business']['id']}", headers=basic(b)) as second:
            for ws, business, sid in [(first, a, 'a-stream'), (second, b, 'b-stream')]:
                ws.send_json({'event': 'start', 'stream_sid': sid, 'start': {'to': business['business']['phone']}})
                assert ws.receive_json()['event'] == 'media'
            speech = base64.b64encode(struct.pack('<h', 7000) * 800).decode()
            silence = base64.b64encode(b'\0\0' * 800).decode()
            for ws, sid in [(first, 'a-stream'), (second, 'b-stream')]:
                for payload in [speech] * 3 + [silence] * 8:
                    ws.send_json({'event': 'media', 'stream_sid': sid, 'media': {'payload': payload}})
            for ws in (first, second):
                for _ in range(5):
                    if ws.receive_json()['event'] == 'media':
                        break
            assert any('9' in text and used == 'a' * 24 for text, used in voices[0].calls)
            assert any('10' in text and used == 'b' * 24 for text, used in voices[1].calls)
            first.send_json({'event': 'stop', 'stream_sid': 'a-stream'})
            second.send_json({'event': 'stop', 'stream_sid': 'b-stream'})


def test_exotel_human_handoff_waits_for_custom_voice_playback(platform):
    admin = {'Authorization': 'Bearer operator-secret'}
    business = create(platform, admin, 'Oak Cafe', 'restaurant', '+918000000001')
    bid = business['business']['id']
    voice = FakeVoice('a' * 24)
    platform.app.state.businesses.set_voice(bid, voice.prepared_voice)
    platform.app.state.business_voices[bid] = voice
    with platform.websocket_connect(f'/telephony/exotel/{bid}', headers=basic(business)) as ws:
        ws.send_json({'event': 'start', 'stream_sid': 'stream1', 'start': {'to': '918000000001'}})
        assert ws.receive_json()['event'] == 'media'
        ws.send_json({'event': 'dtmf', 'stream_sid': 'stream1', 'dtmf': {'digit': '0'}})
        seen = []
        while True:
            packet = ws.receive_json()
            seen.append(packet['event'])
            if packet['event'] == 'mark':
                break
        assert 'clear' in seen and 'media' in seen
        assert any('Connecting you' in text and voice_id == 'a' * 24 for text, voice_id in voice.calls)
        ws.send_json({'event': 'mark', 'stream_sid': 'stream1', 'mark': packet['mark']})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
