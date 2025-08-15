# tests/test_alarm_injection.py
# tests.py
import asyncio
import autobook_green_nie as m            # import the module so we can set m.ALARM_URL
from playwright.async_api import async_playwright


async def test_alarm_tag_branch():
    """
    Verify the <audio> element branch:
      - Forces m.ALARM_URL to a non-empty URL.
      - Hooks HTMLMediaElement.play() to count invocations.
      - Asserts the <audio id="__autobook_alarm__"> is injected and play() is called.
    """
    m.ALARM_URL = "https://example.com/silence.mp3"  # force tag branch

    async with async_playwright() as pw:
        ctx = await m.make_context(pw)  # has --autoplay-policy=no-user-gesture-required
        try:
            page = await ctx.new_page()

            # Hook play() before navigation/alarm
            await page.add_init_script(r"""
                (function(){
                  const proto = window.HTMLMediaElement && window.HTMLMediaElement.prototype;
                  if (!proto || !proto.play) return;
                  const orig = proto.play;
                  proto.play = function(){
                    window.__play_called = (window.__play_called||0) + 1;
                    try { return orig.apply(this, arguments); } catch(e){ return Promise.resolve(); }
                  };
                })();
            """)

            await page.goto("about:blank")
            await m.play_rock_alarm(page)

            exists = await page.evaluate("() => !!document.getElementById('__autobook_alarm__')")
            plays  = await page.evaluate("() => window.__play_called || 0")

            assert exists, "Alarm <audio> element was not injected"
            assert plays >= 1, "HTMLMediaElement.play() was not called"
        finally:
            try:
                await ctx.close()
            except Exception:
                pass


async def test_alarm_webaudio_branch():
    """
    Verify the WebAudio fallback branch:
      - Forces m.ALARM_URL to empty.
      - Wraps AudioContext to count constructions.
      - Asserts at least one AudioContext was constructed.
    """
    m.ALARM_URL = ""  # force WebAudio branch

    async with async_playwright() as pw:
        ctx = await m.make_context(pw)
        try:
            page = await ctx.new_page()

            # Count AudioContext constructions
            await page.add_init_script(r"""
                (function(){
                  const OrigAC = window.AudioContext || window.webkitAudioContext;
                  if (!OrigAC) return;
                  function WrapperAC(){
                    window.__ac_constructed = (window.__ac_constructed||0) + 1;
                    return new OrigAC();
                  }
                  WrapperAC.prototype = OrigAC.prototype;
                  window.AudioContext = WrapperAC;
                  window.webkitAudioContext = WrapperAC;
                })();
            """)

            await page.goto("about:blank")
            await m.play_rock_alarm(page)

            # If your alarm schedules tones slightly in the future, a tiny wait is harmless
            # await asyncio.sleep(0.05)

            acs = await page.evaluate("() => window.__ac_constructed || 0")
            assert acs >= 1, "AudioContext was not constructed (WebAudio path not executed)"
        finally:
            try:
                await ctx.close()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(test_alarm_tag_branch())
    asyncio.run(test_alarm_webaudio_branch())