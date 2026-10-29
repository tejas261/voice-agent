import io
import wave
from types import SimpleNamespace

import pytest

from src.speech import SpeechService

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")


def wav_bytes(seconds=0.1, rate=16000, channels=1, width=2, value=0):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(
            int(value).to_bytes(width, "little", signed=True) * int(rate * seconds) * channels
        )
    return buffer.getvalue()


@pytest.mark.parametrize(
    "body",
    [
        b"not wav",
        wav_bytes(seconds=21),
        wav_bytes(channels=2),
        wav_bytes(width=1),
        wav_bytes(rate=96000),
        wav_bytes()[:-10],
    ],
)
def test_reject_invalid_or_unbounded_audio(settings, body):
    with pytest.raises(ValueError):
        SpeechService(settings).transcribe(body, "en")


def test_silence_does_not_hallucinate(settings):
    assert SpeechService(settings).transcribe(wav_bytes(), "hi")["text"] == ""


def test_resamples_to_whisper_rate_and_filters_silence(settings):
    class STT:
        def transcribe(self, samples, **kwargs):
            assert len(samples) == 1600
            assert samples.dtype == np.float32
            assert kwargs["language"] == "hi"
            return iter(
                [
                    SimpleNamespace(text="रिफंड कब मिलेगा?", no_speech_prob=0.1),
                    SimpleNamespace(text="noise hallucination", no_speech_prob=0.99),
                ]
            ), None

    speech = SpeechService(settings)
    speech.stt = STT()
    assert speech.transcribe(wav_bytes(rate=24000, value=1000), "hi")["text"] == "रिफंड कब मिलेगा?"


def test_speech_cache_separates_languages(settings):
    class TTS:
        def __init__(self):
            self.calls = 0

        def create(self, text, **kwargs):
            self.calls += 1
            return np.zeros(240, dtype=np.float32), 24000

    speech = SpeechService(settings)
    speech.tts = TTS()
    english = speech.synthesize("Hello", "en")
    assert speech.synthesize("Hello", "en") == english
    speech.synthesize("Hello", "hi")
    assert speech.tts.calls == 2
    assert english.startswith(b"RIFF")


def test_stream_passes_whole_sentence_and_preserves_model_timing(settings):
    import base64
    import threading

    speech = SpeechService(settings)
    calls = []
    audio = wav_bytes(rate=16000, value=1000)

    def synthesize(text, language):
        calls.append(text)
        return audio

    speech.synthesize = synthesize
    text = "If you chose this option, you're covered; otherwise the benefit isn't included."
    packets = list(speech.stream(text, "en", threading.Event()))
    assert calls == [text]
    with wave.open(io.BytesIO(audio)) as wav:
        assert b"".join(base64.b64decode(packet["pcm"]) for packet in packets) == wav.readframes(
            wav.getnframes()
        )
