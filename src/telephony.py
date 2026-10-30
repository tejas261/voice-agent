"""Exotel Voicebot WebSocket: bounded, per-call audio and conversation state."""

import asyncio
import base64
import binascii
import json
import logging
import re
import secrets
import struct
import time
from contextlib import suppress

from fastapi import WebSocket, WebSocketDisconnect

from src.audio_stream import SpeechStream, pcm_wav
from src.models import AskRequest, Message

log = logging.getLogger(__name__)
RATE = 8000
MAX_FRAME = 100_000
MAX_TURN = RATE * 2 * 15
SILENCE_BYTES = RATE * 2 * 8 // 10
MIN_SPEECH_BYTES = RATE * 2 // 4


def speech_level(pcm: bytes) -> float:
    # Sample every eighth PCM16 sample; avoid allocating an array per packet.
    values = [sample[0] for sample in struct.iter_unpack('<h', pcm)]
    sampled = values[::8]
    return (sum(value * value for value in sampled) / max(1, len(sampled))) ** 0.5


def pcm_for_phone(pcm: bytes, rate: int) -> bytes:
    from math import gcd

    import numpy as np
    from scipy.signal import resample_poly

    if not pcm or len(pcm) % 2 or not 8000 <= rate <= 48000:
        raise ValueError('Expected PCM16 speech')
    data = np.frombuffer(pcm, dtype='<i2').astype(np.float32)
    if rate != RATE:
        factor = gcd(rate, RATE)
        data = resample_poly(data, RATE // factor, rate // factor)
    return np.clip(data, -32768, 32767).astype('<i2').tobytes()


def wants_human(text: str) -> bool:
    return bool(re.search(r'\b(?:human|real person|representative|agent|speak to (?:someone|staff))\b|इंसान|कर्मचारी|व्यक्ति से बात', text, re.I))


async def exotel_session(ws: WebSocket, business: dict, store, settings, speech, voice, slots):
    if not speech.ready:
        await ws.close(code=1013)
        return
    try:
        await asyncio.wait_for(slots.acquire(), timeout=1)
    except TimeoutError:
        await ws.close(code=1013)
        return
    agent = None
    task = None
    history = []
    stream_sid = None
    send_lock = asyncio.Lock()
    mark_events = {}
    active = False
    buffer = bytearray()
    voiced = 0
    silence = 0
    began = time.monotonic()
    sequence = 0
    output_chunk = 0

    async def send(message):
        nonlocal sequence
        async with send_lock:
            sequence += 1
            message['sequence_number'] = sequence
            await ws.send_json(message)

    async def speak(text, end_after=False):
        stream = SpeechStream(
            lambda cancel: voice.stream(text, business['language'], business['voice_id'], cancel),
            lambda: None,
        )
        pending = bytearray()
        async def send_audio(chunk):
            nonlocal output_chunk
            output_chunk += 1
            await send({'event': 'media', 'stream_sid': stream_sid,
                        'media': {'chunk': output_chunk, 'timestamp': str(int((time.monotonic() - began) * 1000)),
                                  'payload': base64.b64encode(chunk).decode()}})
            await asyncio.sleep(0.16)

        body = stream.body()
        try:
            async for line in body:
                packet = json.loads(line)
                if packet['type'] == 'error':
                    raise RuntimeError('Custom voice stream failed')
                if packet['type'] != 'audio':
                    continue
                pcm = base64.b64decode(packet['pcm'], validate=True)
                pending.extend(await asyncio.to_thread(pcm_for_phone, pcm, packet['sample_rate']))
                while len(pending) >= 3200:
                    chunk = bytes(pending[:3200])
                    del pending[:3200]
                    await send_audio(chunk)
            if pending:
                await send_audio(bytes(pending) + b'\0' * (3200 - len(pending)))
        finally:
            await body.aclose()
        if end_after:
            marker = secrets.token_hex(8)
            event = asyncio.Event()
            mark_events[marker] = event
            await send({'event': 'mark', 'stream_sid': stream_sid, 'mark': {'name': marker}})
            try:
                with suppress(TimeoutError):
                    await asyncio.wait_for(event.wait(), timeout=5)
            finally:
                mark_events.pop(marker, None)
            await ws.close(code=1000)

    async def respond(pcm):
        nonlocal history
        result = await asyncio.to_thread(speech.transcribe, pcm_wav(pcm, RATE), business['language'])
        question = result.get('text', '').strip()
        if not question:
            return
        if wants_human(question):
            await speak('I will connect you to the team.' if business['language'] == 'en' else 'मैं आपको टीम से जोड़ता हूँ।', end_after=True)
            return
        reply = await agent.answer(AskRequest(question=question, language=business['language'], history=history[-6:]))
        await speak(reply.answer, end_after=reply.needs_human)
        history.extend([Message(role='user', content=question), Message(role='assistant', content=reply.answer)])
        history = history[-6:]

    async def guarded(coro):
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning('telephony_turn_failed', extra={'reason': type(exc).__name__})
            with suppress(Exception):
                await ws.close(code=1011)

    try:
        await ws.accept()
        agent = await asyncio.to_thread(store.assistant, business, settings)
        while time.monotonic() - began < 600:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=30)
            if len(raw) > 150_000:
                raise ValueError('Frame too large')
            event = json.loads(raw)
            kind = event.get('event')
            if kind == 'connected':
                continue
            if kind == 'start':
                start = event.get('start', {})
                sid = event.get('stream_sid') or start.get('stream_sid')
                destination = str(start.get('to', '')).lstrip('+')
                fmt = start.get('media_format') or {}
                if stream_sid or not sid or destination != business['phone'] or int(fmt.get('sample_rate', RATE)) != RATE:
                    raise ValueError('Invalid stream destination or format')
                stream_sid = str(sid)
                task = asyncio.create_task(guarded(speak(business['greeting'])))
                continue
            if not stream_sid or event.get('stream_sid') != stream_sid:
                raise ValueError('Invalid stream session')
            if kind == 'stop':
                break
            if kind == 'mark':
                mark = event.get('mark', {}).get('name')
                if mark in mark_events:
                    mark_events[mark].set()
                continue
            if kind == 'clear':
                if task and not task.done():
                    task.cancel()
                history.clear()
                buffer.clear()
                active, silence, voiced = False, 0, 0
                continue
            if kind == 'dtmf' and str(event.get('dtmf', {}).get('digit')) == '0':
                if task and not task.done():
                    task.cancel()
                    await send({'event': 'clear', 'stream_sid': stream_sid})
                task = asyncio.create_task(guarded(speak('Connecting you to the team.' if business['language'] == 'en' else 'मैं आपको टीम से जोड़ता हूँ।', end_after=True)))
                continue
            if kind != 'media':
                continue
            payload = event.get('media', {}).get('payload', '')
            try:
                pcm = base64.b64decode(payload, validate=True)
            except (binascii.Error, ValueError, TypeError) as exc:
                raise ValueError('Invalid audio') from exc
            if not pcm or len(pcm) > MAX_FRAME or len(pcm) % 2:
                raise ValueError('Invalid audio frame')
            talking = speech_level(pcm) > 350
            if talking and not active:
                active = True
                if task and not task.done():
                    task.cancel()
                    await send({'event': 'clear', 'stream_sid': stream_sid})
            if active:
                buffer.extend(pcm)
                voiced += len(pcm) if talking else 0
                silence = 0 if talking else silence + len(pcm)
                if silence >= SILENCE_BYTES or len(buffer) >= MAX_TURN:
                    turn = bytes(buffer)
                    buffer.clear()
                    active, silence = False, 0
                    if voiced >= MIN_SPEECH_BYTES:
                        task = asyncio.create_task(guarded(respond(turn)))
                    voiced = 0
    except (WebSocketDisconnect, TimeoutError):
        pass
    except (ValueError, KeyError, TypeError) as exc:
        log.warning('telephony_invalid_stream', extra={'reason': type(exc).__name__})
        with suppress(Exception):
            await ws.close(code=1008)
    except Exception as exc:
        log.warning('telephony_failed', extra={'reason': type(exc).__name__})
        with suppress(Exception):
            await ws.close(code=1011)
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        if agent:
            await agent.close()
        with suppress(Exception):
            await ws.close(code=1000)
        slots.release()
