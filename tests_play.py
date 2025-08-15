# tests.py
import os
import asyncio
import autobook_green_nie as m
from playwright.async_api import async_playwright

SONG_URL = os.getenv("ALARM_URL", "").strip()  # e.g. http://localhost:8765/your.mp3

async def test_alarm_song_branch_actual():
    if not SONG_URL:
        print("SKIP: set TEST_SONG_URL to an actual MP3/OGG URL to run this test.")
        return

    m.ALARM_URL = SONG_URL  # force <audio> tag branch

    async with async_playwright() as pw:
        ctx = await m.make_context(pw)
        try:
            page = await ctx.new_page()

            # Wrap .play() to detect calls and log rejections
            await page.add_init_script("""
                const origPlay = HTMLMediaElement.prototype.play;
                HTMLMediaElement.prototype.play = function() {
                    window.__play_called = true;
                    const p = origPlay.apply(this, arguments);
                    return p.catch(e => {
                        window.__play_error = (e && e.message) || 'Unknown error';
                        return Promise.resolve();  // avoid unhandled rejection
                    });
                };
            """)

            # Track media events + attach function
            await page.add_init_script(r"""
                window.__alarm_events = [];
                window.__attachAlarmListeners = function(a){
                  if (!a || a.__alarm_listeners_attached__) return;
                  a.__alarm_listeners_attached__ = true;
                  ['loadstart','loadeddata','loadedmetadata','canplay','canplaythrough','play','playing','pause','ended','error','stalled','suspend','timeupdate']
                    .forEach(ev => a.addEventListener(ev, () => window.__alarm_events.push(ev)));
                };
            """)

            await page.goto("about:blank")

            # Trigger the alarm
            await m.play_rock_alarm(page)

            # Wait for element
            await page.wait_for_function("() => !!document.getElementById('__autobook_alarm__')", timeout=10000)

            # Attach event listeners
            await page.evaluate("() => window.__attachAlarmListeners(document.getElementById('__autobook_alarm__'))")

            # Wait until we get HAVE_CURRENT_DATA
            await page.wait_for_function(
                "(() => { const a = document.getElementById('__autobook_alarm__'); return a && a.readyState >= 2; })",
                timeout=15000
            )

            # Wait until not paused
            await page.wait_for_function(
                "(() => { const a = document.getElementById('__autobook_alarm__'); return a && a.paused === false; })",
                timeout=15000
            )

            # Wait a bit and check currentTime advance
            t0 = await page.evaluate("() => document.getElementById('__autobook_alarm__').currentTime || 0")
            await asyncio.sleep(1.0)
            t1 = await page.evaluate("() => document.getElementById('__autobook_alarm__').currentTime || 0")

            # Gather all diagnostics
            diagnostics = await page.evaluate("""
                () => {
                    const a = document.getElementById('__autobook_alarm__');
                    return {
                        exists: !!a,
                        paused: a?.paused,
                        readyState: a?.readyState,
                        currentTime: a?.currentTime,
                        error: a?.error?.message || a?.error?.code || null,
                        events: window.__alarm_events.slice(),
                        play_called: window.__play_called || false,
                        play_error: window.__play_error || null,
                    };
                }
            """)

            # Print diagnostics for manual review
            print("[🔍] ALARM DEBUG:", diagnostics)

            # Assertions
            assert diagnostics["exists"], "Alarm <audio> element was not injected"
            assert diagnostics["play_called"], "HTMLMediaElement.play() was not called"
            assert diagnostics["paused"] is False, "Audio element is paused (autoplay may have been blocked)"
            assert t1 > t0, f"Audio currentTime did not advance (t0={t0:.3f}, t1={t1:.3f})"
            assert any(ev in diagnostics["events"] for ev in (
                "play", "playing", "timeupdate", "canplay", "canplaythrough"
            )), f"No expected media events observed: {diagnostics['events']}"

        finally:
            try:
                await ctx.close()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(test_alarm_song_branch_actual())
    asyncio.run(test_alarm_webaudio_branch())