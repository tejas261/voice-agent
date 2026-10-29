import asyncio
import json
import threading

import pytest

from src.audio_stream import SpeechStream, pcm_packet


@pytest.mark.asyncio
async def test_first_audio_arrives_before_generation_finishes_and_cancel_releases_slot():
    complete = threading.Event()
    closed = threading.Event()
    released = asyncio.Event()

    def generate(cancel):
        try:
            yield pcm_packet(b"\0\0" * 400, 16000)
            while not cancel.wait(0.01):
                if complete.is_set():
                    return
        finally:
            closed.set()

    stream = SpeechStream(generate, released.set)
    body = stream.body()
    assert json.loads(await anext(body))["type"] == "start"
    assert json.loads(await asyncio.wait_for(anext(body), 1))["type"] == "audio"
    assert not complete.is_set()
    await body.aclose()
    await asyncio.wait_for(released.wait(), 1)
    assert closed.is_set()


@pytest.mark.asyncio
async def test_stream_failure_is_explicit_not_a_successful_truncated_reply():
    released = asyncio.Event()

    def generate(cancel):
        yield pcm_packet(b"\0\0" * 20, 16000)
        raise RuntimeError("inference failed")

    stream = SpeechStream(generate, released.set)
    messages = [json.loads(line) async for line in stream.body()]
    assert [m["type"] for m in messages] == ["start", "audio", "error"]
    assert messages[-1]["error"]["code"] == "speech_failed"
    await asyncio.wait_for(released.wait(), 1)


@pytest.mark.asyncio
async def test_slow_reader_has_bounded_buffer_and_can_cancel():
    produced = []
    released = asyncio.Event()

    def generate(cancel):
        for _ in range(1000):
            produced.append(1)
            yield pcm_packet(b"\0\0" * 20, 16000)

    stream = SpeechStream(generate, released.set)
    body = stream.body()
    await anext(body)
    await asyncio.sleep(0.1)
    assert len(produced) <= 5
    await body.aclose()
    await asyncio.wait_for(released.wait(), 1)
