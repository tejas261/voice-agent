import threading
from types import SimpleNamespace

import pytest

from src.clone_audio import PhraseFailure, clone_phrase

np = pytest.importorskip("numpy")


def packet(audio=None, tokens=6):
    return SimpleNamespace(
        audio=np.full(100, 0.2, dtype=np.float32) if audio is None else audio,
        sample_rate=16000,
        token_count=tokens,
    )


def test_runaway_attempt_is_discarded_and_retry_uses_same_text():
    calls, closed, resets = [], [], []

    def generate(**kwargs):
        attempt = len(calls)
        calls.append(kwargs)
        try:
            if attempt == 0:
                yield packet(audio=np.full(100, -0.5), tokens=kwargs["max_tokens"])
            else:
                yield packet()
        finally:
            closed.append(attempt)

    pcm, rate, tokens, retries = clone_phrase(
        generate,
        lambda _: None,
        lambda: resets.append(True),
        "Keep all these words.",
        threading.Event(),
    )
    assert rate == 16000 and tokens == 6 and retries == 1
    assert np.frombuffer(pcm, dtype="<i2").min() > 0  # No failed attempt audio escaped.
    assert [c["text"] for c in calls] == ["Keep all these words."] * 2
    assert [c["temperature"] for c in calls] == [0.7, 0.0]
    assert closed == [0, 1] and len(resets) == 2


def test_two_failed_attempts_raise_without_unbounded_retry():
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        yield packet(audio=np.array([float("nan")]))

    with pytest.raises(PhraseFailure, match="nonfinite_audio"):
        clone_phrase(generate, lambda _: None, lambda: None, "Hello.", threading.Event())
    assert len(calls) == 2


def test_cancel_closes_generation_without_retry_or_partial_playback():
    cancel = threading.Event()
    closed = []

    def generate(**kwargs):
        try:
            yield packet()
            cancel.set()
            yield packet()
        finally:
            closed.append(True)

    with pytest.raises(InterruptedError):
        clone_phrase(generate, lambda _: None, lambda: None, "Hello.", cancel)
    assert closed == [True]


def test_quiet_margins_are_trimmed_but_internal_pause_is_kept():
    audio = np.concatenate(
        [np.zeros(8000), np.ones(1600) * 0.2, np.zeros(4000), np.ones(1600) * 0.2, np.zeros(8000)]
    )

    def generate(**kwargs):
        yield packet(audio=audio)

    pcm, rate, _, _ = clone_phrase(
        generate, lambda _: None, lambda: None, "Hello, welcome.", threading.Event()
    )
    samples = np.frombuffer(pcm, dtype="<i2")
    assert len(samples) == 1280 + 1600 + 4000 + 1600 + 3840
    assert not samples[1280 + 1600 : 1280 + 1600 + 4000].any()


def test_prepare_only_needs_one_chunk_and_always_closes_generator():
    closed = []

    def generate(**kwargs):
        try:
            yield packet()
            pytest.fail("Warm-up should not finish the entire phrase")
        finally:
            closed.append(True)

    pcm, _, _, _ = clone_phrase(
        generate, lambda _: None, lambda: None, "Hello.", threading.Event(), prepare=True
    )
    assert pcm == b"" and closed == [True]


def test_sentence_streams_before_eos_without_losing_or_repeating_samples():
    emitted = []
    audio = np.full(16000, 0.2, dtype=np.float32)

    def generate(**kwargs):
        yield packet(audio)
        yield packet(audio)
        assert emitted, "Playback must not wait for the entire sentence"
        yield packet(audio)

    pcm, _, _, _ = clone_phrase(
        generate,
        lambda _: None,
        lambda: None,
        "A complete sentence with its own rhythm.",
        threading.Event(),
        emit=lambda pcm, rate: emitted.append(pcm),
    )
    assert pcm == b""
    expected = (np.tile(audio, 3) * 32767).astype("<i2").tobytes()
    assert b"".join(emitted) == expected


def test_failure_after_publication_stops_instead_of_repeating_the_sentence():
    emitted, calls, closed = [], [], []

    def generate(**kwargs):
        calls.append(kwargs)
        try:
            yield packet(np.full(16000, 0.2))
            yield packet(np.full(16000, 0.2))
            yield packet(np.array([float("nan")]))
        finally:
            closed.append(True)

    with pytest.raises(PhraseFailure, match="nonfinite_audio"):
        clone_phrase(
            generate,
            lambda _: None,
            lambda: None,
            "Keep the complete sentence context.",
            threading.Event(),
            emit=lambda pcm, rate: emitted.append(pcm),
        )
    assert emitted and len(calls) == 1 and closed == [True]
