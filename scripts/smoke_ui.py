"""Browser regression against a running local server.

Requires Playwright + Chromium. Uses the real /api/ask endpoint, simulated
microphone PCM, and stub transcription/TTS to exercise UI state deterministically.
Run the separate smoke_voice.py for actual speech model integration.
"""

import argparse
import asyncio
import base64
import io
import json
import wave
from pathlib import Path

from playwright.async_api import async_playwright, expect

QUESTION = "Will I get a refund of my premium if I don't claim it during the insurance period?"
MICROPHONE = """
window.captureStreams = [];
navigator.mediaDevices.getUserMedia = async () => {
  if (window.denyMicrophone) throw new DOMException('Denied', 'NotAllowedError');
  const context = new AudioContext();
  await context.resume();
  const source = context.createOscillator();
  const destination = context.createMediaStreamDestination();
  source.connect(destination);
  source.start();
  const stream = destination.stream;
  window.captureStreams.push(stream);
  // Clean up the synthetic source when the app releases its microphone track.
  const track = stream.getAudioTracks()[0];
  const stop = track.stop.bind(track);
  track.stop = () => { stop(); source.stop(); void context.close(); };
  if (window.delayPermission) {
    await new Promise(resolve => { window.allowMicrophone = resolve; });
  }
  return stream;
};
"""


def silent_wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 24000)
    return buffer.getvalue()


def stream_wav(body):
    with wave.open(io.BytesIO(body)) as audio:
        event = {
            "type": "audio",
            "sample_rate": audio.getframerate(),
            "pcm": base64.b64encode(audio.readframes(audio.getnframes())).decode(),
        }
    return json.dumps(event) + "\n" + json.dumps({"type": "done"}) + "\n"


async def main(base_url):
    artifacts = Path("runtime")
    artifacts.mkdir(exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(viewport={"width": 1440, "height": 1000})
        await context.add_init_script(MICROPHONE)
        page = await context.new_page()
        errors, transcriptions, spoken, asked = [], [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "request",
            lambda request: (
                asked.append(json.loads(request.post_data))
                if request.url.endswith("/api/ask")
                else None
            ),
        )
        transcript = QUESTION
        fail_speech = False
        delay_transcription = False
        release_transcription = asyncio.Event()
        transcription_started = asyncio.Event()

        async def transcribe(route):
            body = route.request.post_data_buffer
            assert body[:4] == b"RIFF" and len(body) > 44
            transcriptions.append(body)
            if delay_transcription:
                transcription_started.set()
                await release_transcription.wait()
            await route.fulfill(json={"text": transcript, "language": "en"})

        async def speak(route):
            spoken.append(json.loads(route.request.post_data))
            if fail_speech:
                await route.fulfill(status=503, json={"error": {"message": "Test outage"}})
            else:
                await route.fulfill(
                    content_type="application/x-ndjson", body=stream_wav(silent_wav())
                )

        await page.route("**/api/transcribe?*", transcribe)
        await page.route("**/api/speak/stream", speak)
        await page.goto(base_url)
        await expect(page.locator("#connection")).to_have_text("Local voice ready")
        mode = page.get_by_role("switch", name="Voice only")
        mic = page.locator("#mic")
        panel = page.locator("#voice-panel")
        await expect(page.locator("#chat-panel")).to_be_visible()
        # Real reported question -> real policy API, with read-aloud off.
        await page.locator("#autoplay").uncheck()
        await page.locator("#question").fill(QUESTION)
        await page.locator("#question-form").evaluate("form => form.requestSubmit()")
        await expect(page.locator(".message.assistant")).to_contain_text("If ROP was not selected")
        await expect(page.locator(".message.assistant")).to_contain_text("100%")
        assert not spoken
        await page.screenshot(path=str(artifacts / "voice-controls-desktop.png"), full_page=True)
        # Keyboard-accessible switch hides the entire chat and preserves history.
        await mode.focus()
        await page.keyboard.press("Space")
        await expect(mode).to_be_checked()
        await expect(page.locator("#chat-panel")).to_be_hidden()
        await expect(page.locator("#autoplay-option")).to_be_hidden()
        await expect(page.locator("#replay")).to_be_visible()
        await page.screenshot(path=str(artifacts / "voice-only-desktop.png"), full_page=True)
        # Capture real browser PCM, observe timer, then cancel without sending.
        await mic.click()
        await expect(panel).to_have_attribute("data-phase", "recording")
        await expect(page.locator("#mic-label")).to_have_text("Finish & send")
        await page.wait_for_function("() => document.querySelector('#recording-meter').value > 0.1")
        await page.screenshot(path=str(artifacts / "voice-listening.png"), full_page=True)
        await page.get_by_role("button", name="Cancel recording", exact=True).click()
        await expect(panel).to_have_attribute("data-phase", "idle")
        assert not transcriptions
        assert await page.evaluate(
            "captureStreams.every(s => s.getTracks().every(t => t.readyState === 'ended'))"
        )
        # Voice-only forces spoken answers even if chat's autoplay is off.
        await mic.click()
        await page.wait_for_function("() => document.querySelector('#recording-meter').value > 0.1")
        await mic.click()
        await expect(panel).to_have_attribute("data-phase", "speaking")
        assert len(transcriptions) == 1
        assert "If ROP was not selected" in spoken[-1]["text"]
        assert asked[-1]["history"][-2]["content"] == QUESTION
        # Toggle during playback; no loss of conversation or interruption.
        await mode.uncheck()
        await expect(page.locator(".message.assistant")).to_have_count(2)
        await expect(page.locator("#autoplay")).not_to_be_checked()
        await expect(panel).to_have_attribute("data-phase", "idle")
        await mode.check()
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.screenshot(path=str(artifacts / "voice-only-mobile.png"), full_page=True)
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await page.reload()
        await expect(page.locator("#connection")).to_have_text("Local voice ready")
        await expect(mode).to_be_checked()
        await expect(page.locator("#chat-panel")).to_be_hidden()
        await expect(page.locator(".message")).to_have_count(0)  # no stored conversation
        # Denied microphone leaves a usable text recovery path.
        await page.evaluate("window.denyMicrophone = true")
        await mic.click()
        await expect(page.locator("#notice")).to_contain_text("Microphone access is blocked")
        await page.get_by_role("button", name="Show chat", exact=True).click()
        await expect(page.locator("#question")).to_be_focused()
        await expect(mode).not_to_be_checked()
        # Cancel a permission prompt; a late stream must never start recording.
        await mode.check()
        await page.evaluate("window.denyMicrophone = false; window.delayPermission = true")
        await mic.click()
        await expect(panel).to_have_attribute("data-phase", "permission")
        await page.wait_for_function("() => typeof window.allowMicrophone === 'function'")
        await page.get_by_role("button", name="Cancel microphone request").click()
        await page.evaluate("window.allowMicrophone(); window.delayPermission = false")
        await page.wait_for_function(
            "() => captureStreams.every(s => s.getTracks().every(t => t.readyState === 'ended'))"
        )
        await expect(panel).to_have_attribute("data-phase", "idle")
        # A transcription returning after cancellation cannot add a stale answer.
        delay_transcription = True
        await mic.click()
        await page.wait_for_function("() => document.querySelector('#recording-meter').value > 0.1")
        await mic.click()
        await expect(panel).to_have_attribute("data-phase", "transcribing")
        await asyncio.wait_for(transcription_started.wait(), timeout=5)
        await page.get_by_role("button", name="Stop response", exact=True).click()
        release_transcription.set()
        delay_transcription = False
        await expect(page.locator(".message")).to_have_count(0)
        # A failed audio request in voice-only offers retry and access to the text.
        fail_speech = True
        await mic.click()
        await page.wait_for_function("() => document.querySelector('#recording-meter').value > 0.1")
        await mic.click()
        await expect(page.locator("#notice")).to_contain_text("audio could not play")
        await expect(page.locator("#replay")).to_be_visible()
        await expect(page.locator("#show-chat")).to_be_visible()
        fail_speech = False
        await page.locator("#replay").click()
        await expect(panel).to_have_attribute("data-phase", "speaking")
        await page.locator("#stop").click()
        await expect(panel).to_have_attribute("data-phase", "idle")
        # Clear in voice-only clears both the transcript and follow-up context.
        await page.locator("#new-conversation").click()
        await expect(page.locator(".message")).to_have_count(0)
        await expect(page.locator("#replay")).to_be_hidden()
        await mic.click()
        await page.wait_for_function("() => document.querySelector('#recording-meter').value > 0.1")
        await mic.click()
        await expect(panel).to_have_attribute("data-phase", "speaking")
        assert asked[-1]["history"] == []
        await page.locator("#stop").click()
        # Mobile chat, reduced-motion layout, and keyboard focus still work.
        await mode.uncheck()
        await page.emulate_media(reduced_motion="reduce")
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await page.screenshot(path=str(artifacts / "voice-controls-mobile.png"), full_page=True)
        assert not errors, errors
        await browser.close()
        print("PASS: policy answer, mode persistence, microphone capture/cancel, timer, playback,")
        print("replay, permission errors, stale responses, history, and desktop/mobile layouts.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    asyncio.run(main(parser.parse_args().base_url))
