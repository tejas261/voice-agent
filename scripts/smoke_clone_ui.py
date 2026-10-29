"""Voice manager browser regression with stub inference; run against ./run.sh."""

import asyncio
import io
import json
import math
import struct
import wave
from email import policy
from email.parser import BytesParser

from playwright.async_api import async_playwright, expect
from smoke_ui import MICROPHONE, stream_wav

VOICE_ID = "a" * 24


def wav(seconds=4):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(
            b"".join(
                struct.pack("<h", int(5000 * math.sin(i * 0.07)))
                for i in range(int(16000 * seconds))
            )
        )
    return output.getvalue()


async def main():
    profiles, speech, errors, transcriptions, uploads = [], [], [], [], []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(viewport={"width": 1440, "height": 1000})
        await context.add_init_script(MICROPHONE)
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def voices(route):
            if route.request.method == "GET":
                await route.fulfill(
                    json={
                        "cloning": {"available": True, "loaded": True, "languages": ["en"]},
                        "voices": profiles,
                    }
                )
            elif route.request.method == "POST":
                message = BytesParser(policy=policy.default).parsebytes(
                    b"Content-Type: "
                    + route.request.headers["content-type"].encode()
                    + b"\r\n\r\n"
                    + route.request.post_data_buffer
                )
                audio = next(
                    part.get_payload(decode=True)
                    for part in message.iter_parts()
                    if part.get_param("name", header="content-disposition") == "audio"
                )
                with wave.open(io.BytesIO(audio)) as recording:
                    assert recording.getnchannels() == 1 and recording.getsampwidth() == 2
                    assert 3 <= recording.getnframes() / recording.getframerate() <= 20
                uploads.append(audio)
                profile = {
                    "id": VOICE_ID,
                    "name": "My support voice",
                    "duration_seconds": 4,
                    "created_at": "2026-10-23",
                    "languages": ["en"],
                }
                profiles[:] = [profile]
                await route.fulfill(status=201, json=profile)

        async def speak(route):
            speech.append(json.loads(route.request.post_data))
            if route.request.url.endswith("/stream"):
                await route.fulfill(content_type="application/x-ndjson", body=stream_wav(wav(0.25)))
            else:
                await route.fulfill(content_type="audio/wav", body=wav(0.25))

        async def delete(route):
            profiles.clear()
            await route.fulfill(status=204)

        async def transcribe(route):
            audio = route.request.post_data_buffer
            with wave.open(io.BytesIO(audio)) as recording:
                assert recording.getframerate() == 24000
                assert recording.getnchannels() == 1 and recording.getsampwidth() == 2
                duration = recording.getnframes() / recording.getframerate()
                assert 3 <= duration <= 20, duration
            transcriptions.append(audio)
            await route.fulfill(json={"text": "Hello, how can I help you today?", "language": "en"})

        await page.route("**/api/voices", voices)
        await page.route("**/api/voices/*", delete)
        await page.route("**/api/speak", speak)
        await page.route("**/api/speak/stream", speak)
        await page.route(
            "**/api/voices/*/prepare", lambda route: route.fulfill(json={"ready": True})
        )
        await page.route("**/api/transcribe?*", transcribe)
        await page.goto("http://127.0.0.1:8000")
        await expect(page.locator("#connection")).to_have_text("Local voice ready")
        await page.get_by_role("button", name="Clone a voice", exact=True).click()
        await expect(page.get_by_role("dialog")).to_be_visible()
        await page.locator("#clone-name").fill("My support voice")
        await page.locator("#clone-file").set_input_files(
            {"name": "sample.wav", "mimeType": "audio/wav", "buffer": wav()}
        )
        await expect(page.locator("#save-voice")).to_be_enabled()
        await expect(page.locator("#reference-player")).to_be_visible()
        await page.get_by_role("button", name="Fill from audio").click()
        await expect(page.locator("#clone-transcript")).to_have_value(
            "Hello, how can I help you today?"
        )
        await page.get_by_role("button", name="Save & preview").click()
        await expect(page.locator("#clone-player")).to_be_visible()
        await expect(page.locator("#reply-voice")).to_have_value(VOICE_ID)
        assert speech[-1]["voice_id"] == VOICE_ID
        await page.screenshot(path="runtime/voice-studio-desktop.png", full_page=True)
        await page.get_by_role("button", name="Close voice settings").click()
        await page.locator("#question").fill("What is this policy about?")
        await page.locator("#question").press("Enter")
        await expect(page.locator(".message.assistant")).to_have_count(1)
        await expect(page.locator("#voice-panel")).to_have_attribute(
            "data-phase", "idle", timeout=10000
        )
        assert speech[-1]["voice_id"] == VOICE_ID
        assert "Click 2 Protect Elite Pro" in speech[-1]["text"]
        await page.reload()
        await expect(page.locator("#reply-voice")).to_have_value(VOICE_ID)
        await page.locator("#language").select_option("hi")
        await expect(page.locator("#reply-voice")).to_have_value("default")
        await expect(page.locator("#notice")).to_contain_text("does not support Hindi")
        await page.locator("#question").fill("यह पॉलिसी किस बारे में है?")
        await page.locator("#question").press("Enter")
        await expect(page.locator(".message.assistant")).to_have_count(1)
        await expect(page.locator("#voice-panel")).to_have_attribute(
            "data-phase", "idle", timeout=10000
        )
        assert speech[-1]["voice_id"] == "default" and speech[-1]["language"] == "hi"
        await page.locator("#language").select_option("en")
        await page.get_by_role("switch", name="Voice only").check()
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.get_by_role("button", name="Clone a voice", exact=True).click()
        await page.screenshot(path="runtime/voice-studio-mobile.png", full_page=True)
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await page.locator("#clone-file").set_input_files(
            {"name": "too-short.wav", "mimeType": "audio/wav", "buffer": wav(1)}
        )
        await expect(page.locator("#studio-status")).to_contain_text("between 3 and 20")
        await expect(page.locator("#save-voice")).to_be_disabled()
        await page.get_by_role("button", name="Delete My support voice", exact=True).click()
        await expect(page.locator("#no-saved-voices")).to_be_visible()
        await expect(page.locator("#reply-voice")).to_have_value("default")

        # Permission failures keep upload usable; cancelling a late permission
        # response must release the microphone without starting capture.
        capture = page.locator("#reference-capture")
        record = page.locator("#record-reference")
        await page.evaluate("window.denyMicrophone = true")
        await record.click()
        await expect(page.locator("#capture-hint")).to_contain_text("Microphone access is blocked")
        await expect(page.locator("#clone-file")).to_be_enabled()
        await page.evaluate("window.denyMicrophone = false; window.delayPermission = true")
        await record.click()
        await expect(capture).to_have_attribute("data-phase", "permission")
        await page.wait_for_function("() => typeof window.allowMicrophone === 'function'")
        await page.locator("#cancel-reference").click()
        await page.evaluate("window.allowMicrophone(); window.delayPermission = false")
        await page.wait_for_function(
            "() => captureStreams.every(s => s.getTracks().every(t => t.readyState === 'ended'))"
        )
        await expect(capture).to_have_attribute("data-phase", "idle")

        # Record actual browser PCM through the worklet. Saving this sample must
        # work with an empty file input, and transcription starts automatically.
        await page.set_viewport_size({"width": 1440, "height": 1000})
        await page.locator("#clone-name").fill("My support voice")
        await record.click()
        await expect(capture).to_have_attribute("data-phase", "recording")
        await expect(record).to_be_disabled()  # three-second minimum
        await expect(page.locator("#clone-file")).to_be_disabled()
        await page.wait_for_function("() => document.querySelector('#reference-level').value > 0")
        await page.screenshot(path="runtime/voice-capture-desktop.png", full_page=True)
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.screenshot(path="runtime/voice-capture-mobile.png", full_page=True)
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await expect(record).to_be_enabled(timeout=7000)
        await record.click()
        await expect(page.locator("#clone-transcript")).to_have_value(
            "Hello, how can I help you today?"
        )
        await expect(page.locator("#reference-player")).to_be_visible()
        await expect(page.locator("#clone-file")).to_have_value("")
        assert len(transcriptions) == 2
        assert await page.evaluate(
            "captureStreams.every(s => s.getTracks().every(t => t.readyState === 'ended'))"
        )
        await page.get_by_role("button", name="Save & preview").click()
        await expect(page.locator("#clone-player")).to_be_visible()
        assert len(uploads) == 2 and uploads[-1] == transcriptions[-1]

        # A cancelled retake preserves the prior recording and its transcript.
        await expect(record).to_be_enabled()
        reference_url = await page.locator("#reference-player").get_attribute("src")
        await record.click()
        await expect(capture).to_have_attribute("data-phase", "recording")
        await page.locator("#cancel-reference").click()
        await expect(page.locator("#reference-player")).to_have_attribute("src", reference_url)
        await expect(page.locator("#clone-transcript")).to_have_value(
            "Hello, how can I help you today?"
        )
        assert len(transcriptions) == 2

        # The duration cap stops recording and transcribes without a second click.
        await record.click()
        await expect(capture).to_have_attribute("data-phase", "recording")
        await expect(capture).to_have_attribute("data-phase", "idle", timeout=25000)
        await expect(page.locator("#clone-transcript")).to_have_value(
            "Hello, how can I help you today?"
        )
        assert len(transcriptions) == 3
        with wave.open(io.BytesIO(transcriptions[-1])) as audio:
            assert 18 <= audio.getnframes() / audio.getframerate() <= 20

        # Closing while live must stop tracks without transcribing discarded audio.
        await expect(record).to_be_enabled()
        await record.click()
        await expect(capture).to_have_attribute("data-phase", "recording")
        await page.keyboard.press("Escape")
        await expect(page.get_by_role("dialog")).to_be_hidden()
        await page.wait_for_function(
            "() => captureStreams.every(s => s.getTracks().every(t => t.readyState === 'ended'))"
        )
        assert len(transcriptions) == 3
        # A late permission result after closing cannot revive the recorder.
        await page.get_by_role("button", name="Clone a voice", exact=True).click()
        await page.evaluate("window.delayPermission = true; window.allowMicrophone = null")
        await record.click()
        await page.wait_for_function("() => typeof window.allowMicrophone === 'function'")
        await page.get_by_role("button", name="Close voice settings").click()
        await page.evaluate("window.allowMicrophone(); window.delayPermission = false")
        await page.wait_for_function(
            "() => captureStreams.every(s => s.getTracks().every(t => t.readyState === 'ended'))"
        )
        assert len(transcriptions) == 3
        await expect(page.locator("#chat-panel")).to_be_hidden()
        assert not errors, errors
        await browser.close()
    print(
        "PASS: upload, microphone recording, timer/level, automatic transcript, duration cap, permission/cancel/close cleanup, saved clone, selected voice on RAG answers, persistence, Hindi fallback, deletion, desktop/mobile and voice-only layouts."
    )


if __name__ == "__main__":
    asyncio.run(main())
