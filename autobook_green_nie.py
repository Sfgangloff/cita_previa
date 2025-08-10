# autobook_green_nie.py
# Persistent Chrome profile; human-like interactions; screenshots; robust “Solicitar Cita”.
# NEW: close the whole window (persistent context) at the end of each cycle.
#      On success, keep the window open to let you see/print the confirmation.
#      Still plays the hard-rock alarm when a real calendar appears.

import asyncio, os, random, sys, time, re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError, Page, Locator, BrowserContext

BUILD_TAG = "autobook vA17-close-window-per-cycle"

# ---------------- Config ----------------
PORTAL_URL = "https://sede.administracionespublicas.gob.es/pagina/index/directorio/icpplus"

PROVINCIA_KEY  = "illes balears"
TRAMITE_TOKENS = ["certificado de registro", "ciudadano", "u.e"]
AUTH_USE_CLAVE = False   # always SIN Cl@ve
CLEAR_SHOTS_EACH_CYCLE = True

FILTER_OFFICES_TO_MALLORCA = True
MALLORCA_INCLUDE_TOKENS = [
    "mallorca", "palma", "palma de mallorca", "inca", "manacor",
    "calvià", "calvia", "marratxí", "marratxi", "llucmajor", "felanitx",
    "alcúdia", "alcudia"
]
MALLORCA_EXCLUDE_TOKENS = ["menorca", "maó", "mao", "ciutadella", "ibiza", "eivissa", "formentera", "sant antoni"]

# Human-like pacing (tunable)
MICRO_PAUSE = (0.30, 0.80)
STEP_PAUSE  = (0.6,  1.4)
READ_PAUSE  = (1.2,  2.6)

# Retry cadence (seconds)
RETRY_RANGE   = (2*60, 5*60)
BACKOFF_RANGE = (20*60, 30*60)

HEADLESS = False
PROFILE_DIR     = "./chrome-profile"
LOCALE          = "es-ES"
TIMEZONE_ID     = "Europe/Madrid"
ACCEPT_LANGUAGE = "es-ES,es;q=0.9,en;q=0.8"
TRY_STEALTH     = True   # optional (pip install playwright-stealth)
LOG_SHOTS       = os.getenv("LOG_SHOTS", "0") == "1"

# Alarm
PLAY_ALARM_ON_CALENDAR = True
ALARM_URL = os.getenv("ALARM_URL", "").strip()  # direct MP3/OGG URL (optional)

# -------------- Personal data --------------
load_dotenv()
def need(k: str) -> str:
    v = os.getenv(k, "").strip()
    if not v: raise ValueError(f"Missing {k} in .env")
    return v

NIE_DNI     = need("NIE_DNI")
FULL_NAME   = need("FULL_NAME")
NATIONALITY = need("NATIONALITY")
EMAIL       = need("EMAIL")
PHONE       = need("PHONE")

# -------------- Utilities --------------
def log(msg: str) -> None:
    tz = timezone(timedelta(hours=2))
    print(f"[{datetime.now(tz=tz):%Y-%m-%d %H:%M:%S %Z}] {msg}", flush=True)

def r(a, b): return random.uniform(a, b)
async def pause_micro(): await asyncio.sleep(r(*MICRO_PAUSE))
async def pause_step():  await asyncio.sleep(r(*STEP_PAUSE))
async def pause_read():  await asyncio.sleep(r(*READ_PAUSE))

def _all(text: str, toks: list[str]) -> bool: return all(t.lower() in text.lower() for t in toks)
def _any(text: str, toks: list[str]) -> bool: return any(t.lower() in text.lower() for t in toks)
def _none(text: str, toks: list[str]) -> bool: return all(t.lower() not in text.lower() for t in toks)

# --- helpers (put near snap()) ---
import shutil
def reset_shots_dir():
    if not LOG_SHOTS:
        return
    p = Path("debug_shots")
    try:
        shutil.rmtree(p, ignore_errors=True)  # delete the whole folder
    finally:
        p.mkdir(exist_ok=True)               # recreate it
    log("[SHOTS] Cleared debug_shots/")


async def snap(page: Page, tag: str):
    if not LOG_SHOTS: return
    try:
        Path("debug_shots").mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        fname = f"debug_shots/{ts}-{tag}.png"
        await page.screenshot(path=fname, full_page=True)
        log(f"[SHOT] {fname}")
    except Exception as e:
        log(f"[SHOT ERR] {e}")

async def human_mouse_move(page: Page, x: float, y: float):
    cx, cy = x + random.uniform(-3, 3), y + random.uniform(-3, 3)
    await page.mouse.move(cx, cy, steps=random.randint(8, 16))
    await pause_micro()

async def human_click_locator(page: Page, loc: Locator) -> bool:
    try:
        await loc.scroll_into_view_if_needed(timeout=8000)
        box = await loc.bounding_box()
        if not box: return False
        tx = box["x"] + box["width"] * random.uniform(0.35, 0.65)
        ty = box["y"] + box["height"] * random.uniform(0.35, 0.65)
        await human_mouse_move(page, tx, ty)
        await page.mouse.down(); await pause_micro(); await page.mouse.up()
        await pause_step()
        return True
    except Exception:
        return False

async def click_text_human(page: Page, labels_regex: str) -> bool:
    rx = re.compile(labels_regex, re.I)
    for role in ("button", "link"):
        try:
            loc = page.get_by_role(role, name=rx).first
            if await loc.count(): return await human_click_locator(page, loc)
        except Exception: pass
    try:
        loc = page.locator(f"text=/{labels_regex}/i").first
        if await loc.count(): return await human_click_locator(page, loc)
    except Exception: pass
    return False

async def human_scroll(page: Page):
    try:
        h = await page.evaluate("() => document.body.scrollHeight")
        for _ in range(random.randint(2, 4)):
            await page.mouse.wheel(0, random.randint(250, max(550, h//3)))
            await pause_micro()
    except Exception:
        pass

def is_waf_text(text: str) -> bool:
    t = text.lower()
    return ("requested url was rejected" in t) or ("support id is" in t)

async def waf_rejected(page: Page) -> bool:
    try:
        html = (await page.content()).lower()
        return is_waf_text(html)
    except Exception:
        return False

async def select_option_by_contains(select: Locator, needle: str) -> bool:
    if await select.count():
        await human_click_locator(select.page, select.first)
        for opt in await select.locator("option").all():
            txt = (await opt.text_content() or "").strip()
            val = await opt.get_attribute("value")
            if val and needle.lower() in txt.lower():
                await select.select_option(value=val)
                await pause_step()
                return True
    return False

# ---------------- Robust field helpers ----------------
async def accept_cookies_if_present(page: Page):
    try:
        bar = page.locator("#cookie-law-info-bar")
        if await bar.is_visible(timeout=1000):
            btn = page.locator("#cookie_action_close_header")
            if await btn.count():
                await human_click_locator(page, btn.first)
                await pause_step()
                log("Cookie bar accepted.")
    except Exception:
        pass

async def focus_click(locator: Locator, page: Page) -> bool:
    try:
        await locator.scroll_into_view_if_needed()
        return await human_click_locator(page, locator)
    except Exception:
        return False

async def select_all_and_clear(page: Page):
    for combo in ("Control+A", "Meta+A"):
        try:
            await page.keyboard.press(combo); await asyncio.sleep(0.12)
        except Exception:
            pass
    try:
        await page.keyboard.press("Delete"); await asyncio.sleep(0.12)
    except Exception:
        pass

async def type_like_user(page: Page, locator: Locator, text: str, label: str) -> bool:
    if not await focus_click(locator, page):
        log(f"⚠️ Could not focus {label}")
        return False

    await select_all_and_clear(page)

    # Strategy 1: normal typing
    try:
        for ch in text:
            await page.keyboard.type(ch, delay=random.randint(110, 190))
            await asyncio.sleep(0.04)
        got = await locator.input_value()
        if got == text:
            log(f"✓ {label}: typed normally")
            return True
    except Exception:
        pass

    # Strategy 2: slower typing
    try:
        await select_all_and_clear(page)
        for ch in text:
            await page.keyboard.type(ch, delay=random.randint(200, 320))
            await asyncio.sleep(0.08)
        got = await locator.input_value()
        if got == text:
            log(f"✓ {label}: typed slowly")
            return True
    except Exception:
        pass

    # Strategy 3: insert_text
    try:
        await select_all_and_clear(page)
        await page.keyboard.insert_text(text)
        got = await locator.input_value()
        if got == text:
            log(f"✓ {label}: insert_text")
            return True
    except Exception:
        pass

    got = ""
    try: got = await locator.input_value()
    except Exception: pass
    log(f"✗ {label}: could not set (now='{got}')")
    return False

async def check_radio_robust(scope, selector: str) -> bool:
    try:
        el = await scope.locator(selector).first.element_handle()
        if not el: return False
        ok = await scope.evaluate("""(el)=>{
            el.checked=true;
            el.dispatchEvent(new Event('input',{bubbles:true}));
            el.dispatchEvent(new Event('change',{bubbles:true}));
            return el.checked===true;
        }""", el)
        return bool(ok)
    except Exception:
        try:
            await scope.locator(selector).first.check(force=True, timeout=1500)
            return True
        except Exception:
            return False

# --------- Form detection & filling ---------
async def data_form_present(page: Page) -> bool:
    return (await page.locator("#txtIdCitado").count()) > 0 or (await page.locator("#txtDesCitado").count()) > 0

async def prepare_inputs(page: Page):
    try:
        await page.evaluate("""
            (() => {
              const $ = window.jQuery;
              const name = document.querySelector('#txtDesCitado');
              if (name) name.removeAttribute('pattern'); // allow spaces
              if ($) {
                $('#txtIdCitado,#txtDesCitado').off('keyup'); // detach uppercasing handler
              }
            })();
        """)
    except Exception:
        pass
    await pause_micro()

async def fill_personal(page: Page):
    log("🧾 Data form detected — filling NIE/Name…")
    await accept_cookies_if_present(page)
    await snap(page, "data-form-start")

    nie_input  = page.locator("#txtIdCitado").first
    name_input = page.locator("#txtDesCitado").first
    await nie_input.wait_for(state="visible", timeout=15000)
    await name_input.wait_for(state="visible", timeout=15000)

    if await check_radio_robust(page, "#rdbTipoDocNie"):
        log("✓ Radio N.I.E. checked")
        await pause_micro()

    await prepare_inputs(page)

    nie_text  = NIE_DNI.upper()
    name_text = FULL_NAME.upper()

    await type_like_user(page, nie_input,  nie_text,  "NIE")
    await type_like_user(page, name_input, name_text, "Nombre y apellidos")

    try:
        await page.evaluate("document.activeElement && document.activeElement.blur && document.activeElement.blur()")
    except Exception:
        pass

    v_nie  = await nie_input.input_value()
    v_name = await name_input.input_value()
    log(f"→ Final NIE='{v_nie}', NAME='{v_name}'")
    await snap(page, "data-form-filled")

async def check_and_fill_data_form(page: Page) -> bool:
    if await data_form_present(page):
        log("🧾 Data form detected (early) — filling NIE/Name…")
        await fill_personal(page)
        if not await click_text_human(page, r"(aceptar|continuar|siguiente)"):
            await click_text_human(page, r"(confirmar|enviar|guardar)")
        await pause_read()
        await snap(page, "after-data-form-early")
        return True
    return False

# -------------- Date/time & confirmation --------------
CAL_SEL = ", ".join([
    "table.ui-datepicker-calendar",
    "div.ui-datepicker",
    "div.datepicker",
    "div#calendar",
    "div[class*=calendar]"
])

async def has_calendar(page: Page) -> bool:
    return (await page.locator(CAL_SEL).count()) > 0

async def play_rock_alarm(page: Page):
    if not PLAY_ALARM_ON_CALENDAR: return
    log("🎸 Calendar found — playing alarm!")
    try:
        if ALARM_URL:
            await page.evaluate("""
                (url) => {
                  let a = document.getElementById('__autobook_alarm__');
                  if (!a) {
                    a = document.createElement('audio');
                    a.id='__autobook_alarm__'; a.autoplay=true; a.loop=false;
                    a.src=url; a.volume=1.0;
                    document.body.appendChild(a);
                  }
                  a.currentTime = 0;
                  a.play().catch(()=>{});
                }
            """, ALARM_URL)
        else:
            await page.evaluate("""
                () => {
                  try{
                    const ac = new (window.AudioContext||window.webkitAudioContext)();
                    const riff = (t0) => {
                      const osc = ac.createOscillator();
                      const gain = ac.createGain();
                      const dist = ac.createWaveShaper();
                      const curve = new Float32Array(44100);
                      for (let i=0;i<curve.length;i++){
                        const x = i*2/curve.length-1;
                        curve[i] = (1.5*x)/(1+Math.abs(x));
                      }
                      dist.curve = curve; dist.oversample='4x';
                      osc.type='square';
                      gain.gain.setValueAtTime(0.0001, t0);
                      gain.gain.exponentialRampToValueAtTime(0.9, t0+0.05);
                      gain.gain.exponentialRampToValueAtTime(0.0001, t0+2.2);
                      osc.frequency.setValueAtTime(196, t0);
                      osc.frequency.linearRampToValueAtTime(233, t0+0.5);
                      osc.frequency.linearRampToValueAtTime(262, t0+1.0);
                      osc.connect(dist); dist.connect(gain); gain.connect(ac.destination);
                      osc.start(t0); osc.stop(t0+2.2);
                    };
                    for(let k=0;k<4;k++) riff(ac.currentTime + k*2.4);
                  }catch(e){}
                }
            """)
    except Exception as e:
        log(f"Alarm error: {e}")

async def pick_first_enabled_day(page: Page) -> bool:
    loc = page.locator(", ".join([
        "table.ui-datepicker-calendar td:not(.ui-datepicker-unselectable) a",
        "div.ui-datepicker td:not(.ui-datepicker-unselectable) a",
        "div.datepicker td:not(.disabled) a",
        "div#calendar td a",
        "div[class*=calendar] td a"
    ]))
    if await loc.count():
        await human_click_locator(page, loc.first)
        return True
    return False

async def pick_first_time(page: Page) -> bool:
    radios = page.locator("input[type=radio][name*=hora], input[type=radio][id*=hora], div.hora input[type=radio]")
    if await radios.count():
        await human_click_locator(page, radios.first)
        return True
    btns = page.locator("button:has-text('Seleccionar'), a:has-text('Seleccionar')")
    if await btns.count():
        await human_click_locator(page, btns.first)
        return True
    any_btn = page.locator("button, a").filter(has_text=re.compile("seleccionar|cita", re.I))
    if await any_btn.count():
        await human_click_locator(page, any_btn.first)
        return True
    return False

async def confirmation_detected(page: Page) -> bool:
    try:
        await page.wait_for_function("""() => {
            const t = document.body.innerText.toLowerCase();
            return t.includes('cita confirmada') || t.includes('localizador') || t.includes('resguardo');
        }""", timeout=15000)
        return True
    except PWTimeoutError:
        return False

# -------------- Portal-specific helpers --------------

async def pick_tramite_anywhere(page: Page, tokens: list[str]) -> bool:
    log("Step: scanning trámites…")
    combined = page.locator(
        "select[id^=tramiteGrupo], select[name^=tramiteGrupo], "
        "select#subtramite, select[name=subtramite], "
        "select[id*=tramite], select[name*=tramite]"
    )
    await combined.first.wait_for(timeout=15000)

    for sel in await combined.all():
        for opt in await sel.locator("option").all():
            txt = (await opt.text_content() or "").strip()
            val = await opt.get_attribute("value")
            if val and _all(txt, tokens):
                await human_click_locator(page, sel)
                await sel.select_option(value=val)
                await pause_step()
                log(f"Selected trámite: {txt}")
                return True

    related = ["policía", "policia", "certificado", "registro", "u.e", "ue"]
    for sel in await combined.all():
        for opt in await sel.locator("option").all():
            label = (await opt.text_content() or "").strip().lower()
            val = await opt.get_attribute("value")
            if val and _any(label, related):
                await human_click_locator(page, sel)
                await sel.select_option(value=val)
                await pause_step()
                for sub in await combined.all():
                    for op2 in await sub.locator("option").all():
                        t2 = (await op2.text_content() or "").strip()
                        v2 = await op2.get_attribute("value")
                        if v2 and _all(t2, tokens):
                            await human_click_locator(page, sub)
                            await sub.select_option(value=v2)
                            await pause_step()
                            log(f"Selected sub-trámite: {t2}")
                            return True

    all_opts = []
    for sel in await combined.all():
        texts = [ (await o.text_content() or "").strip() for o in await sel.locator("option").all() ]
        all_opts.extend([t for t in texts if t])
    log("⚠️ No trámite matched tokens. Seen options:")
    for t in all_opts[:50]:
        log(f" - {t}")
    return False

async def click_auth_mode(page: Page, use_clave: bool) -> bool:
    try:
        await page.wait_for_function("""() => {
            const t = document.body.innerText.toLowerCase();
            return t.includes('presentación con cl@ve') || t.includes('presentacion con cl@ve') ||
                   t.includes('presentación sin cl@ve') || t.includes('presentacion sin cl@ve');
        }""", timeout=12000)
    except PWTimeoutError:
        return True

    target = r"presentaci[óo]n con cl@ve" if use_clave else r"presentaci[óo]n sin cl@ve"
    ok = await click_text_human(page, target)
    if not ok:
        try:
            target_word = "con" if use_clave else "sin"
            loc = page.locator(
                f"xpath=//*[contains(translate(., 'Ó@VE', 'ó@ve'), 'presentación {target_word} cl@ve') or "
                f"contains(translate(., 'Ó@VE', 'ó@ve'), 'presentacion {target_word} cl@ve')]"
            ).first
            if await loc.count():
                ok = await human_click_locator(page, loc)
        except Exception:
            ok = False

    if ok:
        log("Auth mode selected.")
        await page.wait_for_load_state("domcontentloaded", timeout=20000)
        await pause_read()
        return True
    return False

async def choose_mallorca_office_if_needed(page: Page) -> bool:
    office = page.locator("select#sede, select[name*=oficina], select[id*=oficina], select[name*=sede]")
    if not await office.count():
        log("No office selector on this step.")
        return True

    await human_click_locator(page, office.first)
    options = await office.locator("option").all()
    cand = []
    for opt in options:
        v = (await opt.get_attribute("value")) or ""
        t = ((await opt.text_content()) or "").strip()
        if v and t: cand.append((v, t))
    if not cand:
        log("Office select present but empty.")
        return False

    if FILTER_OFFICES_TO_MALLORCA:
        mallorca = [(v, t) for v, t in cand if _any(t, MALLORCA_INCLUDE_TOKENS) and _none(t, MALLORCA_EXCLUDE_TOKENS)]
        if not mallorca:
            log("⚠️ No Mallorca office found. Offices were:")
            for _, t in cand: log(f"   - {t}")
            return False
        v, t = mallorca[0]
        log(f"Seleccionando oficina Mallorca: {t}")
        await office.select_option(value=v)
    else:
        v, t = cand[0]
        log(f"Seleccionando oficina: {t}")
        await office.select_option(value=v)

    await pause_step()
    await click_text_human(page, r"(aceptar|continuar|siguiente)")
    await pause_read()
    return True

async def no_slots(page: Page) -> bool:
    txt = (await page.content()).lower()
    return any(p in txt for p in [
        "no hay citas disponibles", "no existen citas disponibles", "en este momento no hay citas disponibles"
    ])

async def attempt_click_solicitar_cita(page: Page, wait_ms: int = 12000) -> bool:
    log("Step: try 'Solicitar Cita' (robust)…")
    deadline = time.time() + (wait_ms/1000)
    clicked = False

    while time.time() < deadline and not clicked:
        try:
            loc = page.get_by_role("button", name=re.compile(r"solicitar\s*cita", re.I)).first
            if await loc.count():
                clicked = await human_click_locator(page, loc)
                if clicked: break
        except Exception: pass
        try:
            loc = page.locator("input[type=button][value*=Solicitar i]").first
            if await loc.count():
                clicked = await human_click_locator(page, loc)
                if clicked: break
        except Exception: pass
        try:
            loc = page.locator("a, button").filter(has_text=re.compile(r"solicitar\s*cita", re.I)).first
            if await loc.count():
                clicked = await human_click_locator(page, loc)
                if clicked: break
        except Exception: pass
        if await click_text_human(page, r"solicitar\s*cita"):
            clicked = True; break
        try:
            ok = await page.evaluate("""
                () => {
                  const nodes = Array.from(document.querySelectorAll('input[type=button],button,a'));
                  const node = nodes.find(n => /solicitar\s*cita/i.test((n.value||'') + ' ' + (n.innerText||'')));
                  if (node) { node.click(); return true; }
                  return false;
                }
            """)
            if ok: clicked = True; break
        except Exception: pass
        await asyncio.sleep(0.25)

    if clicked:
        log("Clicked 'Solicitar Cita'.")
        await pause_read()
        await snap(page, "after-solicitar-cita")
        return True
    else:
        log("Did not find/click 'Solicitar Cita' within wait window.")
        await snap(page, "no-solicitar-cita")
        return False

# -------------- Launch helpers --------------
async def make_context(pw) -> BrowserContext:
    Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)
    stealth_fn = None
    if TRY_STEALTH:
        try:
            from playwright_stealth import stealth_async
            stealth_fn = stealth_async
        except Exception:
            stealth_fn = None

    ctx = await pw.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        channel="chrome",               # comment if Chrome isn't installed
        headless=HEADLESS,
        viewport={"width": 1280, "height": 900},
        locale=LOCALE,
        timezone_id=TIMEZONE_ID,
        user_agent=None,
        extra_http_headers={"Accept-Language": ACCEPT_LANGUAGE},
        args=[
            "--disable-blink-features=AutomationControlled",
            "--autoplay-policy=no-user-gesture-required",   # allow alarm to play
        ],
    )

    if stealth_fn:
        try:
            p0 = await ctx.new_page()
            await stealth_fn(p0)
            await p0.close()
        except Exception:
            pass

    return ctx

# -------------- One cycle --------------
async def run_cycle(page: Page) -> tuple[bool, bool]:
    log("Navigate to portal…")
    try:
        await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
        await pause_read()
        await snap(page, "landed")
        await human_scroll(page)
    except Exception as e:
        log(f"Nav error: {e}")
        await snap(page, "nav-error")
        return False, False

    if await waf_rejected(page):
        log("WAF rejection page detected right after landing.")
        await snap(page, "waf-landing")
        return False, True

    log("Try initial Enter/Acceder/Iniciar…")
    await click_text_human(page, r"(entrar|acceder|iniciar)")
    await human_scroll(page)

    log("Step: select province…")
    provincia = page.locator("select#form, select[name*=provincia], select[id*=provincia]")
    try:
        await provincia.first.wait_for(timeout=20000)
    except PWTimeoutError:
        log("Province select not found.")
        await snap(page, "no-province")
        return False, False
    if not await select_option_by_contains(provincia, PROVINCIA_KEY):
        log("Could not select province.")
        await snap(page, "province-fail")
        return False, False
    await click_text_human(page, r"(aceptar|continuar|siguiente)")
    await pause_read(); await snap(page, "after-province"); await human_scroll(page)

    log("Step: pick trámite…")
    if not await pick_tramite_anywhere(page, TRAMITE_TOKENS):
        await snap(page, "tramite-fail")
        return False, False
    await click_text_human(page, r"(aceptar|continuar|solicitar cita|entrar|acceder)")
    await pause_read(); await snap(page, "after-tramite"); await human_scroll(page)

    await check_and_fill_data_form(page)

    if await waf_rejected(page):
        log("WAF rejection detected after trámite.")
        await snap(page, "waf-after-tramite")
        return False, True

    log("Step: auth mode (SIN CL@VE)…")
    await click_auth_mode(page, AUTH_USE_CLAVE)
    await human_scroll(page)

    await check_and_fill_data_form(page)
    await attempt_click_solicitar_cita(page, wait_ms=15000)

    log("Step: choose office (Mallorca)…")
    if not await choose_mallorca_office_if_needed(page):
        await snap(page, "office-fail")
        return False, False

    await check_and_fill_data_form(page)
    await attempt_click_solicitar_cita(page, wait_ms=6000)

    log("Step: wait for calendar or 'no citas'…")
    await check_and_fill_data_form(page)

    try:
        await page.wait_for_function(f"""() => {{
            const t = document.body.innerText.toLowerCase();
            return t.includes('no hay citas disponibles') || t.includes('no existen citas disponibles') ||
                   document.querySelector({CAL_SEL!r});
        }}""", timeout=25000)
    except PWTimeoutError:
        log("Calendar/no-citas did not appear in time.")
        await snap(page, "no-calendar")

    if await waf_rejected(page):
        log("WAF rejection detected at calendar step.")
        await snap(page, "waf-calendar")
        return False, True

    if await no_slots(page):
        log("No slots — will retry later.")
        await snap(page, "no-slots")
        return False, False

    if await has_calendar(page):
        await play_rock_alarm(page)

    log("Step: pick earliest date…")
    if not await pick_first_enabled_day(page):
        log("Could not click a calendar day.")
        await snap(page, "no-day")
        return False, False
    await pause_step()

    log("Step: pick earliest time…")
    if not await pick_first_time(page):
        log("Could not pick a time.")
        await snap(page, "no-time")
        return False, False
    await pause_step()

    log("Step: continue to data form…")
    await click_text_human(page, r"(continuar|siguiente|aceptar|confirmar)")
    await pause_read(); await snap(page, "before-data-form")

    if await data_form_present(page):
        await fill_personal(page)
        log("Step: submit personal data…")
        if not await click_text_human(page, r"(confirmar|reservar|finalizar|aceptar)"):
            await click_text_human(page, r"(enviar|guardar)")
        await pause_read(); await snap(page, "after-data-form")

    log("Step: check confirmation…")
    booked = await confirmation_detected(page)
    if booked:
        log("✅ Cita confirmada. Leaving this window open for the resguardo.")
        await snap(page, "confirmed")
    else:
        log("No confirmation text detected (may still be booked — check tab).")
        await snap(page, "no-confirm-text")
    return booked, False

# -------------- Runner --------------
async def main():
    log("Auto-booking green NIE (Mallorca-only, closes window per iteration) — starting.")
    log(f"Running build: {BUILD_TAG}")
    async with async_playwright() as pw:
        while True:
            if CLEAR_SHOTS_EACH_CYCLE:
                reset_shots_dir() 
            # NEW: create a fresh *window* (persistent context) each cycle
            ctx = await make_context(pw)
            page = await ctx.new_page()
            t0 = time.time()

            try:
                booked, blocked = await run_cycle(page)
            finally:
                if booked:
                    # Do NOT close the window on success so you can see/print the resguardo
                    log("Process finished. Window left open.")
                    break
                else:
                    # Close the whole window at the end of the cycle
                    try:
                        await ctx.close()
                    except Exception:
                        pass

            if blocked:
                sleep_for = random.randint(*BACKOFF_RANGE)
                log(f"⚠️ WAF rejection detected. Backing off for ~{sleep_for//60} min.")
            else:
                sleep_for = random.randint(*RETRY_RANGE)
                log(f"No cita this round. Retrying in ~{sleep_for//60} min.")

            await asyncio.sleep(max(5, sleep_for - (time.time() - t0)))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Stopped by user.")
        sys.exit(0)
