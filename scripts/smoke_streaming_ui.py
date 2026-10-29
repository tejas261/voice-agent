"""Browser regression for incremental playback, interruption and incomplete audio."""

import asyncio
import json

from playwright.async_api import async_playwright, expect

STREAM = """
if (!localStorage.getItem('listen.replyVoice')) localStorage.setItem('listen.replyVoice', 'cccccccccccccccccccccccc');
localStorage.setItem('listen.accent', 'british');
window.speechRequests = []; window.cancelledStreams = 0;
window.streamMode = 'normal'; window.completedStreams = 0;
const originalFetch = window.fetch.bind(window);
window.fetch = async (url, options) => {
  if (url !== '/api/speak/stream') return originalFetch(url, options);
  speechRequests.push(JSON.parse(options.body));
  const encoder = new TextEncoder();
  const packet = JSON.stringify({type: 'audio', sample_rate: 24000,
    pcm: btoa('\\0'.repeat(24000))}) + '\\n';
  let cancelled = false;
  const body = new ReadableStream({
    start(controller) {
      if (streamMode === 'error') {
        controller.enqueue(encoder.encode(JSON.stringify({type: 'error', error: {message: 'Test outage'}}) + '\\n'));
        controller.close(); return;
      }
      // Split a JSON packet across network reads.
      controller.enqueue(encoder.encode(packet.slice(0, 17)));
      controller.enqueue(encoder.encode(packet.slice(17)));
      if (streamMode === 'truncated') { controller.close(); return; }
      window.completeSpeech = () => {
        if (cancelled) return;
        controller.enqueue(encoder.encode(packet));
        controller.enqueue(encoder.encode('{"type":"done"}\\n'));
        controller.close(); completedStreams++;
      };
    },
    cancel() { cancelled = true; cancelledStreams++; }
  });
  return new Response(body, {headers: {'Content-Type': 'application/x-ndjson'}});
};
"""


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        await page.add_init_script(STREAM)
        errors, prepared = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.route(
            "**/api/voices",
            lambda route: route.fulfill(
                json={
                    "cloning": {"available": True},
                    "voices": [{"id": "c" * 24, "name": "Test voice", "duration_seconds": 4}],
                }
            ),
        )

        async def prepare(route):
            prepared.append(route.request.url)
            await route.fulfill(json={"ready": True})

        await page.route("**/api/voices/*/prepare", prepare)

        await page.route(
            "**/api/ask",
            lambda route: route.fulfill(
                json={
                    "answer": "Your policy includes a grace period. The length depends on how often you pay.",
                    "language": "en",
                    "mode": "groq",
                    "latency_ms": 10,
                    "sources": [],
                }
            ),
        )
        await page.goto("http://127.0.0.1:8000")
        await expect(page.locator("#connection")).to_have_text("Local voice ready")
        await page.wait_for_function(
            "document.querySelector('#reply-voice').value === 'cccccccccccccccccccccccc'"
        )
        assert prepared, "Restored voice should prepare before the first answer"
        await page.locator("#autoplay").check()
        panel = page.locator("#voice-panel")

        async def ask():
            await page.locator("#question").fill("How does my policy work?")
            await page.locator("#question").press("Enter")

        await ask()
        await expect(panel).to_have_attribute("data-phase", "speaking")
        assert await page.evaluate("completedStreams") == 0
        assert await page.evaluate("speechRequests[0].voice_id") == "c" * 24
        await page.evaluate("completeSpeech()")
        await expect(panel).to_have_attribute("data-phase", "idle")
        await page.locator("#replay").click()
        await expect(panel).to_have_attribute("data-phase", "speaking")
        await page.locator("#stop").click()
        await expect(panel).to_have_attribute("data-phase", "idle")
        assert await page.evaluate("cancelledStreams") == 1
        await page.evaluate("completeSpeech()")  # late completion cannot restart playback
        await expect(panel).to_have_attribute("data-phase", "idle")
        await page.evaluate("streamMode = 'truncated'")
        await ask()
        await expect(page.locator("#notice")).to_contain_text("before the reply was complete")
        await expect(panel).to_have_attribute("data-phase", "idle")
        await page.evaluate("streamMode = 'error'")
        await ask()
        await expect(page.locator("#notice")).to_contain_text("Test outage")
        await page.evaluate("streamMode = 'normal'")
        await page.locator("#voice-only").check()
        await page.locator("#replay").click()
        await expect(panel).to_have_attribute("data-phase", "speaking")
        await expect(page.locator("#chat-panel")).to_be_hidden()
        await page.evaluate("completeSpeech()")
        await expect(panel).to_have_attribute("data-phase", "idle")
        await expect(page.locator("#accent")).to_have_count(0)
        assert await page.evaluate("() => localStorage.getItem('listen.accent')") is None
        assert await page.evaluate("() => speechRequests.every(request => !('accent' in request))")
        await page.reload()
        await expect(page.locator("#connection")).to_have_text("Local voice ready")
        await expect(page.locator("#reply-voice")).to_have_value("c" * 24)
        await page.locator("#language").select_option("hi")
        await expect(page.locator("#reply-voice")).to_have_value("default")
        await page.set_viewport_size({"width": 390, "height": 844})
        await expect(page.locator("#reply-voice")).to_be_visible()
        assert await page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
        assert not errors, errors
        await browser.close()
        print(
            json.dumps(
                {
                    "passed": True,
                    "checks": [
                        "early playback",
                        "fragmented packets",
                        "warm-up",
                        "completion",
                        "cancel",
                        "stale completion",
                        "truncation",
                        "errors",
                        "voice-only",
                        "replay",
                        "retired accent preference cleared",
                        "voice selection preserved",
                        "mobile layout",
                    ],
                }
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
