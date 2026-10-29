import base64
import io
import json
import wave
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.app import create_app
from src.audio_stream import pcm_packet
from src.cloned_speech import ClonedSpeech
from src.voices import VoiceStore, normalize_reference

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")


def reference(seconds=4, frequency=180, rate=16000, silent=False):
    samples = np.sin(np.arange(int(seconds * rate)) * (2 * np.pi * frequency / rate))
    samples *= 0 if silent else 0.2
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes((samples * 32767).astype("<i2").tobytes())
    return output.getvalue()


class FakeWorker(ClonedSpeech):
    """Exercise real profile/cache routing without importing MLX or downloading a model."""

    def __init__(self, settings, store):
        super().__init__(settings, store)
        self.requests = []
        self.responses = []

    def status(self):
        return {"available": True, "loaded": False, "languages": ["en"], "reason": None}

    def _start(self, deadline):
        def write(data):
            request = json.loads(data)
            self.requests.append(request)
            if request.get("op") == "prepare":
                self.responses = [{"prepared": True}]
            elif request.get("op") == "cancel":
                self.responses = [{"id": request["id"], "done": True, "cancelled": True}]
            elif request.get("stream"):
                self.responses = [
                    {**pcm_packet(b"\0\0" * 800, 16000), "id": request["id"]},
                    {"id": request["id"], "done": True},
                ]

        self.process = SimpleNamespace(stdin=SimpleNamespace(write=write, flush=lambda: None))

    def _receive(self, deadline, cancel=None):
        if self.responses:
            return self.responses.pop(0)
        voice = self.requests[-1]["voice_id"]
        return {"audio": base64.b64encode(reference(frequency=150 + int(voice[:2], 16))).decode()}

    def _stop(self):
        self.process = None


class FakeStandard:
    ready = True

    def synthesize(self, text, language):
        return reference(frequency=100)


@pytest.fixture
def clone_settings(settings, tmp_path):
    return settings.model_copy(update={"voices_dir": tmp_path / "voices"})


@pytest.fixture
def cloned_client(clone_settings):
    store = VoiceStore(clone_settings.voices_dir)
    worker = FakeWorker(clone_settings, store)
    with TestClient(
        create_app(clone_settings, speech=FakeStandard(), cloned_speech=worker)
    ) as client:
        yield client, store, worker


def upload(client, name="Support voice", body=None, transcript="Hello, how can I help you today?"):
    return client.post(
        "/api/voices",
        data={"name": name, "transcript": transcript},
        files={"audio": ("../../ignored-filename.wav", body or reference(), "audio/wav")},
    )


def test_profile_creation_is_local_idempotent_and_persistent(cloned_client, clone_settings):
    client, store, worker = cloned_client
    first = upload(client)
    assert first.status_code == 201
    profile = first.json()
    assert profile["languages"] == ["en"]
    assert "transcript" not in profile
    assert upload(client).json()["id"] == profile["id"]
    assert len(client.get("/api/voices").json()["voices"]) == 1
    reloaded = VoiceStore(clone_settings.voices_dir).get(profile["id"])
    assert reloaded.transcript == "Hello, how can I help you today?"
    assert store.reference_path(profile["id"]).is_file()
    assert store.reference_path(profile["id"]).stat().st_mode & 0o777 == 0o600
    assert worker.requests == []  # saving a profile does not synthesize or load the GPU model


@pytest.mark.parametrize(
    "body",
    [
        b"invalid",
        reference(seconds=2),
        reference(seconds=21),
        reference(silent=True),
        reference()[:-5],
    ],
)
def test_invalid_reference_is_rejected(cloned_client, body):
    client, store, _ = cloned_client
    response = upload(client, body=body)
    assert response.status_code == 422
    assert response.json()["error"]["message"]
    assert store.list() == []


def test_blank_fields_and_body_limit(cloned_client):
    client, _, _ = cloned_client
    assert upload(client, name="  ").status_code == 422
    assert upload(client, transcript="  ").status_code == 422
    assert client.post("/api/voices", content=b"a" * (3 * 1024 * 1024 + 1)).status_code == 413


def test_voice_selection_cache_isolation_and_deletion(cloned_client):
    client, store, worker = cloned_client
    first = upload(client, "Voice A").json()["id"]
    second = upload(client, "Voice B", reference(frequency=280)).json()["id"]
    payload = {"text": "Here is your answer.", "language": "en", "voice_id": first}
    one = client.post("/api/speak", json=payload)
    assert one.status_code == 200
    assert one.headers["X-Voice-ID"] == first
    assert client.post("/api/speak", json=payload).content == one.content
    assert len(worker.requests) == 1
    two = client.post("/api/speak", json={**payload, "voice_id": second})
    assert two.status_code == 200
    assert len(worker.requests) == 2
    assert worker.requests[-1]["ref_audio"] == str(store.reference_path(second))
    assert worker.requests[-1]["ref_text"] == "Hello, how can I help you today?"
    assert client.delete(f"/api/voices/{first}").status_code == 204
    assert not (store.directory / first).exists()
    assert client.post("/api/speak", json=payload).status_code == 404
    assert client.delete(f"/api/voices/{first}").status_code == 404
    assert all(key[0] != first for key in worker.cache)
    assert store.get(second)


def test_hindi_is_never_silently_spoken_in_an_unsupported_clone(cloned_client):
    client, _, worker = cloned_client
    profile = upload(client).json()
    response = client.post(
        "/api/speak", json={"text": "नमस्ते", "language": "hi", "voice_id": profile["id"]}
    )
    assert response.status_code == 422
    assert "Hindi" in response.json()["error"]["message"]
    assert worker.requests == []
    standard = client.post("/api/speak", json={"text": "नमस्ते", "language": "hi"})
    assert standard.status_code == 200
    assert standard.headers["X-Voice-ID"] == "default"


def test_paths_and_unknown_profiles_are_rejected(cloned_client):
    client, store, _ = cloned_client
    with pytest.raises(ValueError):
        store.get("../outside")
    assert client.delete("/api/voices/not-a-voice").status_code == 422
    assert (
        client.post("/api/speak", json={"text": "hello", "voice_id": "../outside"}).status_code
        == 422
    )
    assert (
        client.post("/api/speak", json={"text": "hello", "voice_id": "a" * 24}).status_code == 404
    )


def test_missing_clone_installation_keeps_standard_voice_working(clone_settings, tmp_path):
    config = clone_settings.model_copy(update={"qwen_python": tmp_path / "missing"})
    with TestClient(create_app(config, speech=FakeStandard())) as client:
        assert not client.get("/api/voices").json()["cloning"]["available"]
        assert upload(client).status_code == 503
        assert client.post("/api/speak", json={"text": "Hello"}).status_code == 200
        profile = VoiceStore(config.voices_dir).create("Test", "Test voice.", reference())
        assert client.post(f"/api/voices/{profile.id}/prepare").status_code == 503
        assert (
            client.post(
                "/api/speak/stream", json={"text": "Hello", "voice_id": profile.id}
            ).status_code
            == 503
        )


def test_profiles_and_uploads_require_auth(clone_settings):
    config = clone_settings.model_copy(update={"api_token": SecretStr("secret-test-token")})
    with TestClient(create_app(config, speech=FakeStandard())) as client:
        assert client.get("/api/voices").status_code == 401
        assert upload(client).status_code == 401
        assert client.delete("/api/voices/" + "a" * 24).status_code == 401
        assert (
            client.get(
                "/api/voices", headers={"Authorization": "Bearer secret-test-token"}
            ).status_code
            == 200
        )


def test_reference_is_resampled_to_model_rate():
    normalized, duration = normalize_reference(reference(rate=48000))
    with wave.open(io.BytesIO(normalized)) as audio:
        assert audio.getframerate() == 24000
        assert audio.getnchannels() == 1
        assert audio.getnframes() == 4 * 24000
    assert duration == 4


def test_clone_stream_has_pcm_completion_and_compatible_replay_cache(cloned_client):
    client, _, worker = cloned_client
    voice = upload(client).json()["id"]
    payload = {"text": "Here is the answer.", "voice_id": voice}
    response = client.post("/api/speak/stream", json=payload)
    assert response.status_code == 200
    packets = [json.loads(line) for line in response.text.splitlines()]
    assert [p["type"] for p in packets] == ["start", "audio", "done"]
    assert packets[1]["sample_rate"] == 16000
    assert len(base64.b64decode(packets[1]["pcm"])) == 1600
    assert len(worker.requests) == 1
    assert client.post("/api/speak", json=payload).content.startswith(b"RIFF")
    assert client.post("/api/speak/stream", json=payload).status_code == 200
    assert len(worker.requests) == 1


def test_clone_stream_validation_and_prepare(cloned_client):
    client, _, worker = cloned_client
    voice = upload(client).json()["id"]
    assert client.post(f"/api/voices/{voice}/prepare").json() == {"ready": True}
    assert client.post(f"/api/voices/{voice}/prepare").json() == {"ready": True}
    assert len(worker.requests) == 1
    assert client.post("/api/voices/not-valid/prepare").status_code == 422
    assert client.post(f"/api/voices/{'a' * 24}/prepare").status_code == 404
    assert (
        client.post("/api/speak/stream", json={"text": "Hi", "voice_id": "a" * 24}).status_code
        == 404
    )
    assert (
        client.post(
            "/api/speak/stream", json={"text": "नमस्ते", "language": "hi", "voice_id": voice}
        ).status_code
        == 422
    )
    assert client.post("/api/speak/stream", json={"text": ""}).status_code == 422


def test_shared_stream_routes_existing_and_new_voices_to_their_own_references(cloned_client):
    client, store, worker = cloned_client
    text = "You can ask about the options, and I can explain how they work."
    voices = [
        upload(client, "Voice A", reference(frequency=180)).json()["id"],
        upload(client, "Voice B", reference(frequency=280)).json()["id"],
    ]

    def speak(voice):
        response = client.post("/api/speak/stream", json={"text": text, "voice_id": voice})
        assert response.status_code == 200
        assert response.headers["X-Voice-ID"] == voice
        assert json.loads(response.text.splitlines()[-1])["type"] == "done"

    for voice in voices:
        speak(voice)
    # A voice added after the shared worker has already served other profiles
    # must use the same stream path, with its own reference and cache entry.
    voices.append(upload(client, "Added later", reference(frequency=380)).json()["id"])
    speak(voices[-1])
    assert [request["voice_id"] for request in worker.requests] == voices
    for request, voice in zip(worker.requests, voices):
        assert request["stream"] is True
        assert request["text"] == text
        assert request["ref_audio"] == str(store.reference_path(voice))
        assert (voice, "en", text) in worker.cache
    for voice in voices:
        speak(voice)
    assert len(worker.requests) == 3  # Each voice replays its own cached reply.


def test_abandoned_clone_stream_cancels_worker_without_caching_partial_audio(clone_settings):
    import threading

    store = VoiceStore(clone_settings.voices_dir)
    voice = store.create("Test", "This is a test voice.", reference()).id
    worker = FakeWorker(clone_settings, store)
    iterator = worker.stream("An interrupted response.", "en", voice, threading.Event())
    assert next(iterator)["type"] == "audio"
    iterator.close()
    assert worker.requests[-1]["op"] == "cancel"
    assert not worker.cache
    assert worker.process is not None
