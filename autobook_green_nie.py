# autobook_green_nie.py
# Persistent Chrome profile; human-like interactions; screenshots; robust flow.
# Modes:
#   - NIE (green NIE, original path)
#   - TIE (new path with different office/section and identity fields)
# NEW:
#   - Close the whole window (persistent context) at the end of each cycle unless booked.
#   - Alarm triggers when page at the availability stage deviates from the default “no slots” page.
#   - TIE path has detailed screenshot breadcrumbs and robust PASAPORTE radio selection.

import asyncio, os, random, sys, time, re, shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError, Page, Locator, BrowserContext
from pathlib import Path

BUILD_TAG = "autobook vA19-nie-or-tie-debugged"

# ---------------- Config ----------------
PORTAL_URL = "https://sede.administracionespublicas.gob.es/pagina/index/directorio/icpplus"

# Load .env before reading anything from it
load_dotenv()

# Select flow: "NIE" (green NIE, current) or "TIE"
TRAMITE_MODE = os.getenv("TRAMITE_MODE", "NIE").strip().upper()  # values: "NIE" or "TIE"

PROVINCIA_KEY  = "illes balears"
TRAMITE_TOKENS = ["certificado de registro", "ciudadano", "u.e"]
AUTH_USE_CLAVE = False   # always SIN Cl@ve

# Debug screenshots
LOG_SHOTS       = os.getenv("LOG_SHOTS", "0") == "1"
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
BACKOFF_RANGE = (7*60, 10*60)

HEADLESS = False
PROFILE_DIR     = "./chrome-profile"
LOCALE          = "es-ES"
TIMEZONE_ID     = "Europe/Madrid"
ACCEPT_LANGUAGE = "es-ES,es;q=0.9,en;q=0.8"
TRY_STEALTH     = True   # optional (pip install playwright-stealth)

# Alarm
PLAY_ALARM_ON_CALENDAR = True

import base64, mimetypes, pathlib
def data_url_for_audio(path: str) -> str:
    p = pathlib.Path(path).resolve()
    mime = mimetypes.guess_type(p.name)[0] or "audio/mpeg"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"

ALARM_URL = data_url_for_audio("/Users/silveregangloff/Desktop/Cita Previa/cita_previa/Thunderstruck.mp3")

# -------------- Personal data --------------
def need(k: str) -> str:
    v = os.getenv(k, "").strip()
    if not v: raise ValueError(f"Missing {k} in .env")
    return v

# NIE (green) flow identity (the site may still ask these later in TIE)
NIE_DNI     = need("NIE_DNI")
FULL_NAME   = need("FULL_NAME")
NATIONALITY = need("NATIONALITY")
EMAIL       = need("EMAIL")
PHONE       = need("PHONE")

# TIE-specific identity (passport)
PASSPORT_NUMBER = os.getenv("PASSPORT_NUMBER", "").strip()  # required when TRAMITE_MODE="TIE"
BIRTH_YEAR      = os.getenv("BIRTH_YEAR", "").strip()       # 4-digit string, e.g., "1989"

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

def reset_shots_dir():
    if not LOG_SHOTS:
        return
    p = Path("debug_shots")
    try:
        shutil.rmtree(p, ignore_errors=True)
    finally:
        p.mkdir(exist_ok=True)
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

def attach_debug_listeners(page: Page) -> None:
    """Mirror browser console and page errors into Python logs."""
    try:
        page.on("console", lambda m: log(f"[BROWSER {m.type}] {m.text}"))
        page.on("pageerror", lambda e: log(f"[BROWSER ERROR] {e}"))
    except Exception:
        pass

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
async def js_fill_birth_year(page: Page, year: str) -> bool:
    """
    Find a visible, enabled 4-digit year input and set it by JS with proper events.
    """
    if not (year and len(year) == 4 and year.isdigit()):
        return False
    try:
        ok = await page.evaluate("""
            (val) => {
              const isVis = el => el && el.offsetParent !== null && !el.disabled;
              const inputs = Array.from(document.querySelectorAll('input'));
              // priority: explicit hints
              const good = (el) => {
                const id  = (el.id   || '').toLowerCase();
                const nm  = (el.name || '').toLowerCase();
                const ph  = (el.getAttribute('placeholder') || '').toLowerCase();
                const mxl = el.getAttribute('maxlength');
                const pat = el.getAttribute('pattern') || '';
                if (!isVis(el)) return false;
                if (id.includes('año') || id.includes('ano') || id.includes('anio')) return true;
                if (nm.includes('año') || nm.includes('ano') || nm.includes('anio')) return true;
                if (id.includes('nacim') || nm.includes('nacim')) return true;
                if (ph.includes('aaaa')) return true;
                if (mxl === '4') return true;
                if (/\\d{4}/.test(pat)) return true;
                return false;
              };
              const target = inputs.find(good);
              if (!target) return false;
              target.focus();
              target.value = val;
              target.dispatchEvent(new Event('input',  {bubbles:true}));
              target.dispatchEvent(new Event('change', {bubbles:true}));
              target.blur && target.blur();
              return true;
            }
        """, year)
        return bool(ok)
    except Exception:
        return False
    
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
        loc = scope.locator(selector).first
        # Fast path: if it doesn't exist, bail immediately.
        if await loc.count() == 0:
            return False

        # Try JS set + dispatch (most reliable on this site)
        el = await loc.element_handle(timeout=800)
        if not el:
            return False
        ok = await (scope.evaluate if hasattr(scope, "evaluate") else scope.page.evaluate)(
            """(el)=>{
                el.checked = true;
                el.dispatchEvent(new Event('input',{bubbles:true}));
                el.dispatchEvent(new Event('change',{bubbles:true}));
                return el.checked === true;
            }""",
            el,
        )
        if ok:
            return True

        # Fallback to .check(force=True)
        await loc.check(force=True, timeout=800)
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

async def check_and_fill_data_form(page, mode="NIE"):
    if mode.upper() == "TIE":
        await fill_personal_tie(page)
    else:
        await fill_personal(page)
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

async def play_rock_alarm(page, url: str | None = None):
    if not PLAY_ALARM_ON_CALENDAR:
        return {"ok": False, "why": "alarm disabled"}

    the_url = (url if url is not None else ALARM_URL) or ""
    log(f"🎸 Availability signal detected — playing alarm! src[:64]={the_url[:64]!r}")

    try:
        diag = await page.evaluate(
            """
            async (url) => {
              const result = {
                ok: false,
                created: false,
                hadElem: false,
                readyState: -1,
                paused: true,
                currentTime: 0,
                error: null,
                events: [],
                srcPrefix: (url||'').slice(0, 32),
              };

              try {
                let a = document.getElementById('__autobook_alarm__');
                result.hadElem = !!a;
                if (!a) {
                  a = document.createElement('audio');
                  a.id = '__autobook_alarm__';
                  a.autoplay = true;
                  a.loop = false;
                  a.preload = 'auto';
                  a.playsInline = true;
                  a.muted = false;
                  a.volume = 1.0;

                  const logEv = (ev) => result.events.push(ev.type);
                  ['loadstart','durationchange','loadedmetadata','loadeddata','canplay',
                   'canplaythrough','play','playing','pause','timeupdate','stalled','error','ended']
                   .forEach(ev => a.addEventListener(ev, logEv, {once:false}));

                  document.body.appendChild(a);
                }

                if (url) a.src = url;

                await new Promise(r => setTimeout(r, 50));

                const waitReady = () => new Promise((resolve) => {
                  let done = false;
                  const timer = setTimeout(() => { if (!done) { done = true; resolve(); } }, 2000);
                  const onMeta = () => { if (!done) { done = true; clearTimeout(timer); resolve(); } };
                  a.addEventListener('loadedmetadata', onMeta, {once:true});
                  a.addEventListener('canplay', onMeta, {once:true});
                });
                await waitReady();

                try {
                  await a.play();
                } catch (e) {
                  result.error = String(e && e.message || e);
                }

                await new Promise(r => setTimeout(r, 500));

                result.readyState = a.readyState;
                result.paused     = a.paused;
                result.currentTime= a.currentTime || 0;
                // ---- FIXED: use JS logical OR (||), not Python 'or'
                result.ok = (result.currentTime > 0) || (result.paused === false) || result.events.includes('playing');
              } catch (e) {
                result.error = 'outer:' + (e && e.message || String(e));
              }
              return result;
            }
            """,
            the_url,
        )
        log(f"[alarm diag] ok={diag.get('ok')} paused={diag.get('paused')} "
            f"ready={diag.get('readyState')} t={diag.get('currentTime'):.3f} "
            f"events={diag.get('events')[:6]} srcPrefix={diag.get('srcPrefix')!r} "
            f"err={diag.get('error')!r}")
        return diag
    except Exception as e:
        log(f"Alarm error (evaluate): {e}")
        return {"ok": False, "why": f"evaluate exception: {e}"}

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

# --- New helpers for TIE flow and non-default detection ---

async def force_fill_birth_year(page: Page, year: str) -> bool:
    """
    Fill the 4-digit birth year reliably, without clicking labels/radios.
    Strategy:
      - Find the element whose text contains 'Año de nacimiento' (or 'Ano de nacimiento'),
        then pick the closest input within that block or immediately following it.
      - Fallbacks: any visible enabled input with placeholder ~ 'aaaa', maxlength=4,
        or id/name including anio/año/nacim.
      - Set value via JS and dispatch input/change.
    """
    if not (year and len(year) == 4 and year.isdigit()):
        return False

    try:
        ok = await page.evaluate(r"""
            (val) => {
              const norm = s => (s||'').toLowerCase()
                .normalize('NFD').replace(/[\u0300-\u036f]/g,''); // strip accents
              const isVis = el => !!(el && el.offsetParent !== null && !el.disabled);

              // 1) Find the label/block that mentions the field
              const blocks = Array.from(document.querySelectorAll('label,div,span,strong,p'));
              let anchor = null;
              for (const b of blocks) {
                const t = norm(b.innerText || b.textContent);
                if (!t) continue;
                if (t.includes('ano de nacimiento') || t.includes('año de nacimiento')) {
                  anchor = b;
                  break;
                }
              }

              const pickInputNear = (root) => {
                if (!root) return null;
                // Prefer an input inside the same container
                let inp = root.querySelector('input');
                if (isVis(inp)) return inp;
                // else try immediate following input in DOM order
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT, null);
                walker.currentNode = root;
                for (let i=0;i<40 && walker.nextNode();i++){
                  const el = walker.currentNode;
                  if (el.tagName && el.tagName.toLowerCase()==='input' && isVis(el)) return el;
                }
                return null;
              };

              let target = pickInputNear(anchor);

              // 2) Fallback heuristics
              if (!target) {
                const inputs = Array.from(document.querySelectorAll('input')).filter(isVis);
                const score = (el) => {
                  const id  = norm(el.id);
                  const nm  = norm(el.name);
                  const ph  = norm(el.getAttribute('placeholder'));
                  const mxl = el.getAttribute('maxlength');
                  let s = 0;
                  if (id.includes('anio') || id.includes('ano') || id.includes('año')) s += 3;
                  if (nm.includes('anio') || nm.includes('ano') || nm.includes('año')) s += 3;
                  if (id.includes('nacim') || nm.includes('nacim')) s += 2;
                  if (ph && ph.includes('aaaa')) s += 2;
                  if (mxl === '4') s += 1;
                  return s;
                };
                inputs.sort((a,b)=>score(b)-score(a));
                if (inputs.length && score(inputs[0])>0) target = inputs[0];
              }

              if (!target) return false;

              // 3) Set value and fire events
              target.focus();
              target.value = val;
              target.dispatchEvent(new Event('input',  {bubbles:true}));
              target.dispatchEvent(new Event('change', {bubbles:true}));
              target.blur && target.blur();
              return true;
            }
        """, year)
        return bool(ok)
    except Exception:
        return False

async def choose_specific_office(page: Page, office_label_substring: str) -> bool:
    """
    Selects a specific office by (case-insensitive) substring match on the visible option text.
    """
    office = page.locator("select#sede, select[name*=oficina], select[id*=oficina], select[name*=sede]")
    if not await office.count():
        log("No office selector on this step.")
        return False
    await human_click_locator(page, office.first)
    chosen = False
    for opt in await office.locator("option").all():
        txt = (await opt.text_content() or "").strip()
        val = await opt.get_attribute("value")
        if val and (office_label_substring.lower() in txt.lower()):
            await office.select_option(value=val)
            log(f"Selected office: {txt}")
            chosen = True
            break
    if not chosen:
        log("⚠️ Specific office not found among options.")
        return False
    await pause_step()
    return True

async def select_tramite_in_section(page: Page, section_label_regex: str, option_contains: str) -> bool:
    """
    Finds a <select> that is likely associated with the section label (title text near it),
    then selects the option whose text contains `option_contains`.
    """
    try:
        # Find any element whose normalized text matches the regex (case/diacritics handled by JS)
        sec = page.locator(
            f"xpath=//*[matches(translate(normalize-space(.), 'ÁÉÍÓÚÜÑ', 'áéíóúüñ'), {section_label_regex!r})]"
        ).first
    except Exception:
        sec = page.locator("xpath=//*").first

    candidate_selects = []
    try:
        if await sec.count():
            near = sec.locator("xpath=following::select[1]")
            if await near.count():
                candidate_selects.append(near.first)
    except Exception:
        pass
    candidate_selects.extend(await page.locator("select").all())

    for sel in candidate_selects:
        try:
            for opt in await sel.locator("option").all():
                txt = (await opt.text_content() or "").strip()
                val = await opt.get_attribute("value")
                if val and option_contains.lower() in txt.lower():
                    await human_click_locator(page, sel)
                    await sel.select_option(value=val)
                    await pause_step()
                    log(f"Selected trámite in section: {txt}")
                    return True
        except Exception:
            continue
    log("⚠️ Could not find the target trámite option inside/near the section.")
    return False

async def is_default_no_slots_page(page: Page) -> bool:
    """
    Heuristic: the 'default' negative page contains a standard 'no citas' phrase,
    and also shows neither a calendar widget nor any time-selection controls.
    If any calendar/time selector is present, we *do not* consider it default.
    """
    try:
        html = (await page.content()).lower()
    except Exception:
        return False

    default_phrases = [
        "no hay citas",
        "no existen citas disponibles"
    ]
    contains_default_text = any(p in html for p in default_phrases)

    print("CONTAINS_DEFAULT_TEXT",contains_default_text)

    return contains_default_text

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

# -------------- NIE flow (original) --------------
async def run_cycle(page: Page) -> tuple[bool, bool]:
    """
    NIE (green) flow.
    """
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
    await click_text_human(page, r"(acceder al procedimiento|entrar|acceder|iniciar)")
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

    # New logic: alarm if page is NOT the default 'no slots' page
    if await is_default_no_slots_page(page):
        log("No slots (default page) — will retry later.")
        await snap(page, "no-slots-default")
        return False, False
    else:
        await play_rock_alarm(page)
        await page.wait_for_function(""" () => { const a = document.getElementById('__autobook_alarm__'); return a && a.ended; } """, timeout=0) # timeout=0 = no limit

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

# -------------- TIE flow --------------

async def _select_pasaporte_radio_in_scope(scope) -> bool:
    patterns = [
        "input[type=radio][id*=pasap]",
        "input[type=radio][value*=pasap]",
        "input[type=radio][name*=tipo][value=P]",
        "#rdbTipoDocPas",
    ]
    for sel in patterns:
        # Skip quickly if nothing matches
        if await scope.locator(sel).first.count() == 0:
            continue
        if await check_radio_robust(scope, sel):
            log("✓ Radio PASAPORTE checked (pattern match).")
            await pause_micro()
            return True

    try:
        lab_radio = scope.get_by_label(re.compile(r"pasaport", re.I))
        if await lab_radio.count():
            await lab_radio.first.check(force=True, timeout=800)
            log("✓ Radio PASAPORTE checked (get_by_label).")
            await pause_micro()
            return True
    except Exception:
        pass

    try:
        lab = scope.locator("label").filter(has_text=re.compile(r"pasaport", re.I)).first
        if await lab.count():
            ok = await human_click_locator(scope.page if hasattr(scope, 'page') else scope, lab)
            if ok and await scope.locator("input[type=radio]:checked").locator("[id*=pasap], [value*=pasap]").count():
                log("✓ Radio PASAPORTE checked (clicked label).")
                await pause_micro()
                return True
    except Exception:
        pass

    try:
        eval_fn = scope.evaluate if hasattr(scope, "evaluate") else scope.page.evaluate
        ok = await eval_fn("""
            () => {
              const radios = Array.from(document.querySelectorAll('input[type=radio]'));
              for (const r of radios) {
                let txt = '';
                if (r.labels && r.labels.length) {
                  txt = Array.from(r.labels).map(L => (L.innerText||L.textContent||'')).join(' ').toLowerCase();
                } else {
                  const sib = r.nextSibling && r.nextSibling.textContent ? r.nextSibling.textContent : '';
                  txt = (sib||'').toLowerCase();
                }
                if (txt.includes('pasaport')) {
                  r.checked = true;
                  r.dispatchEvent(new Event('input', {bubbles:true}));
                  r.dispatchEvent(new Event('change', {bubbles:true}));
                  return true;
                }
              }
              return false;
            }
        """)
        if ok:
            log("✓ Radio PASAPORTE checked (JS sweep).")
            await pause_micro()
            return True
    except Exception:
        pass

    log("✗ Could not select PASAPORTE radio.")
    return False

async def find_birth_year_input(page: Page) -> Locator | None:
    """
    Return a locator to a visible+enabled 4-digit year input.
    Tries several robust heuristics; returns None if nothing suitable.
    """
    candidates = [
        # Common ids/names/placeholders (diacritics handled by multiple patterns)
        "input:visible:not([disabled])[id*='año']",
        "input:visible:not([disabled])[name*='año']",
        "input:visible:not([disabled])[id*='anio' i]",
        "input:visible:not([disabled])[name*='anio' i]",
        "input:visible:not([disabled])[placeholder*='aaaa' i]",
        # very common generic field on this portal:
        "input:visible:not([disabled])[id*=nacim i]",
        "input:visible:not([disabled])[name*=nacim i]",
        # Any visible text/number input with maxlength=4
        "input:visible:not([disabled])[maxlength='4']",
        # Any input with a 4-digit pattern
        "input:visible:not([disabled])[pattern*='\\d{4}']",
    ]
    for sel in candidates:
        loc = page.locator(sel).first
        try:
            if await loc.count():
                return loc
        except Exception:
            continue

    # Fallback: nearest input after a label containing "Año de nacimiento"/"Ano de nacimiento"
    try:
        xp = (
            "xpath=(//label|//span|//div|//strong)"
            "[contains(translate(normalize-space(.),'ÁÉÍÓÚÜÑ','áéíóúüñ'), 'año de nacimiento') or "
            " contains(translate(normalize-space(.),'ÁÉÍÓÚÜÑ','áéíóúüñ'), 'ano de nacimiento')]"
            "/following::input[1]"
        )
        loc = page.locator(xp).first
        if await loc.count():
            # must be visible and not disabled
            if await loc.is_visible() and not await loc.is_disabled():
                return loc
    except Exception:
        pass
    return None

async def select_pasaporte_radio(page: Page) -> bool:
    """
    Select PASAPORTE via a real user-like click so the portal's JS swaps the form.
    Prefer the radio by accessible name; fall back to label[for] -> input#id.
    """
    log("🔘 Selecting PASAPORTE via real click…")

    # 1) Try role=radio by accessible name
    try:
        radio_by_name = page.get_by_role("radio", name=re.compile(r"pasaport", re.I)).first
        if await radio_by_name.count():
            await radio_by_name.scroll_into_view_if_needed()
            await radio_by_name.click(force=True, delay=120)
            return True
    except Exception:
        pass

    # 2) Disambiguate labels: pick label[for] whose text says PASAPORTE, then click its input#for
    try:
        lab = page.locator("label[for]").filter(has_text=re.compile(r"pasaport", re.I)).first
        if await lab.count():
            for_attr = await lab.get_attribute("for")
            if for_attr:
                radio = page.locator(f"input[type=radio]#{for_attr}").first
                if await radio.count():
                    await radio.scroll_into_view_if_needed()
                    await radio.click(force=True, delay=120)
                    return True
            # Fallback: click the label itself (less ideal, but works if it’s bound correctly)
            await lab.scroll_into_view_if_needed()
            await lab.click(force=True, delay=120)
            return True
    except Exception:
        pass

    # 3) Last resort: any radio whose nearby label contains 'pasaport'
    try:
        ok = await page.evaluate("""
            () => {
              const isVis = el => !!(el && el.offsetParent !== null);
              const radios = Array.from(document.querySelectorAll('input[type=radio]')).filter(isVis);
              for (const r of radios) {
                let txt = '';
                if (r.labels && r.labels.length) {
                  txt = Array.from(r.labels).map(L => (L.innerText||L.textContent||'')).join(' ').toLowerCase();
                } else {
                  const sib = r.nextSibling && r.nextSibling.textContent ? r.nextSibling.textContent : '';
                  txt = (sib||'').toLowerCase();
                }
                if (txt.includes('pasaport')) { r.click(); return true; }
              }
              return false;
            }
        """)
        if ok:
            return True
    except Exception:
        pass

    log("⚠️ Could not find a clickable PASAPORTE radio.")
    return False

async def fill_personal_tie(page: Page) -> bool:
    """
    TIE identity page:
      - Force-lock PASAPORTE
      - Fill passport number into the main ID field (#txtIdCitado or similar)
      - Fill Name
      - Fill Birth Year (JS)
      - Re-lock PASAPORTE again
      - Click Aceptar
    """
    log("🧾 TIE identity form — filling Passport/Name/Year…")
    await accept_cookies_if_present(page)
    await snap(page, "tie-form-start")

    # Soft presence check
    try:
        await page.wait_for_function("""() => {
            const t = document.body.innerText.toLowerCase();
            return t.includes('tipo de documento') || t.includes('año de nacimiento') || t.includes('ano de nacimiento');
        }""", timeout=15000)
    except PWTimeoutError:
        pass
    await snap(page, "tie-identity-visible")

    # 1) Click PASAPORTE like a human so the form really swaps
    ok_click = await select_pasaporte_radio(page)
    await page.wait_for_timeout(250)  # let their JS start

    # 2) Wait until the *checked* radio is the PASAPORTE one (diacritics-insensitive)
    try:
        await page.wait_for_function(r"""
            () => {
            const norm = s => (s||'').toLowerCase()
                .normalize('NFD').replace(/[\u0300-\u036f]/g,''); // strip accents
            const radios = Array.from(document.querySelectorAll('input[type=radio]'));
            for (const r of radios) {
                if (!r.checked) continue;
                let txt = '';
                if (r.labels && r.labels.length) {
                txt = Array.from(r.labels).map(L => (L.innerText||L.textContent||'')).join(' ');
                } else {
                const sib = r.nextSibling && r.nextSibling.textContent ? r.nextSibling.textContent : '';
                txt = sib || '';
                }
                if (norm(txt).includes('pasaport')) return true;
            }
            return false;
            }
        """, timeout=4000)
    except PWTimeoutError:
        log("⚠️ PASAPORTE does not appear as the checked radio — proceeding but form may still be NIE.")

    await snap(page, "tie-after-radio-human-click")

    # 2) Main ID field (same element across modes on this portal)
    id_field = page.locator(
        "#txtIdCitado, "
        "input:visible:not([disabled])[id*=pasap i], "
        "input:visible:not([disabled])[name*=pasap i], "
        "input:visible:not([disabled])[id*=documento i], "
        "input:visible:not([disabled])[name*=documento i]"
    ).first
    if await id_field.count() and PASSPORT_NUMBER:
        await type_like_user(page, id_field, PASSPORT_NUMBER.upper(), "Documento (passport)")
    else:
        log("⚠️ Could not find main ID field to type passport.")

    # 3) Name
    name_field = page.locator(
        "#txtDesCitado, "
        "input:visible:not([disabled])[id*=nombre i], "
        "input:visible:not([disabled])[name*=nombre i]"
    ).first
    if await name_field.count():
        await type_like_user(page, name_field, FULL_NAME.upper(), "Nombre y apellidos")
    else:
        log("⚠️ Name input not found.")

    # 4) Birth Year (robust, try label association)
    year_label = page.locator("label:has-text('AÑO')")
    if await year_label.count():
        await year_label.scroll_into_view_if_needed()
        # Try to find input *near* the label
        year_field = year_label.locator("xpath=following::input[1]").first
    else:
        # Fallback: search for any input with año/id pattern
        year_field = page.locator(
            "input[placeholder*='año' i], input[id*='ano' i], input[name*='ano' i]"
        ).first

    if await year_field.count() and BIRTH_YEAR:
        await year_field.scroll_into_view_if_needed()
        await year_field.click(force=True)
        await year_field.fill("")
        await year_field.type(str(BIRTH_YEAR), delay=50)
        log(f"✓ Year: {BIRTH_YEAR} typed normally")
    else:
        log("⚠️ Could not find year field")

    # 5) Re-lock PASAPORTE just before submitting (in case any focus flipped it)
    await select_pasaporte_radio(page)

    # 6) Submit
    if not await click_text_human(page, r"(aceptar|continuar|siguiente|confirmar)"):
        await click_text_human(page, r"(enviar|guardar)")
    await pause_read()
    await snap(page, "after-tie-form")
    return True

async def run_cycle_tie(page: Page) -> tuple[bool, bool]:
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

    # Page 1: "Acceder al procedimiento"
    log("Step: Acceder al procedimiento…")
    await click_text_human(page, r"(acceder al procedimiento|acceder|entrar|iniciar)")
    await pause_read(); await snap(page, "tie-after-acceder"); await human_scroll(page)

    # Page 2: select province Illes Balears, Acceptar
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
    await pause_read(); await snap(page, "tie-after-province"); await human_scroll(page)

    # Page 3: Office + section trámites
    log("Step: select 'Oficina de Extranjería de Palma'…")
    if not await choose_specific_office(page, "oficina de extranjería de palma"):
        await snap(page, "tie-office-fail")
        return False, False
    await snap(page, "tie-after-office")

    log("Step: under 'TRÁMITES OFICINAS DE EXTRANJERÍA' select 'SOLICITUD AUTORIZACIONES'…")
    ok_tram = await select_tramite_in_section(
        page,
        section_label_regex=r".*tr[aá]mites\s+oficinas\s+de\s+extranjer[ií]a.*",
        option_contains="solicitud autorizac"
    )
    if not ok_tram:
        await snap(page, "tie-tramite-fail")
        return False, False
    await click_text_human(page, r"(aceptar|continuar|siguiente)")
    await pause_read(); await snap(page, "tie-after-tramite"); await human_scroll(page)

    # Page 4: auth "sin clave"
    log("Step: auth mode (SIN CL@VE)…")
    await click_auth_mode(page, use_clave=False)
    await snap(page, "tie-after-auth"); await human_scroll(page)

    # Page 5: identity form (Passport/Name/Year)
    await fill_personal_tie(page)
    await snap(page, "tie-after-fill-personal")

    # From here: same availability logic
    log("Step: try 'Solicitar Cita' (robust)…")
    try:
        solicitar_btn = page.get_by_role("button", name=re.compile(r"solicitar cita", re.I))
        await solicitar_btn.click(timeout=7000)  # wait longer if needed
        log("✓ 'Solicitar Cita' clicked.")
        await snap(page, "tie-after-solicitar-cita")
    except PWTimeoutError:
        log("⚠️ 'Solicitar Cita' not found — verifying bounce-back form...")
        if await page.locator("text=Tipo de documento").count():
            log("↩️ Bounce-back form detected, refilling...")
            await check_and_fill_data_form(page, mode="TIE")
        else:
            log("❌ Neither button nor bounce-back form found")

    log("Step: wait for calendar or 'no citas'…")
    await check_and_fill_data_form(page, mode="TIE")  # in case portal bounces back an extra form
    try:
        await page.wait_for_function(f"""() => {{
            const t = document.body.innerText.toLowerCase();
            return t.includes('no hay citas disponibles') || t.includes('no existen citas disponibles') ||
                   document.querySelector({CAL_SEL!r});
        }}""", timeout=25000)
    except PWTimeoutError:
        log("Calendar/no-citas did not appear in time.")
        await snap(page, "tie-no-calendar-wait-timeout")

    if await waf_rejected(page):
        log("WAF rejection detected at calendar step.")
        await snap(page, "waf-calendar")
        return False, True

    if await is_default_no_slots_page(page):
        log("No slots (default page) — will retry later.")
        await snap(page, "tie-no-slots-default")
        return False, False
    else:
        await play_rock_alarm(page)
        await page.wait_for_function(""" () => { const a = document.getElementById('__autobook_alarm__'); return a && a.ended; } """, timeout=0) # timeout=0 = no limit

    # If there is a calendar, proceed; otherwise stop so you can inspect manually
    if not await has_calendar(page):
        log("No calendar visible after alarm — leaving window for manual check.")
        await snap(page, "tie-non-default-no-calendar")
        return False, False

    log("Step: pick earliest date…")
    if not await pick_first_enabled_day(page):
        log("Could not click a calendar day.")
        await snap(page, "tie-no-day")
        return False, False
    await pause_step()

    log("Step: pick earliest time…")
    if not await pick_first_time(page):
        log("Could not pick a time.")
        await snap(page, "tie-no-time")
        return False, False
    await pause_step()

    log("Step: continue to data form…")
    await click_text_human(page, r"(continuar|siguiente|aceptar|confirmar)")
    await pause_read(); await snap(page, "tie-before-data-form")

    if await data_form_present(page):
        # Reuse existing routine for any final NIE/Name page variants
        await fill_personal(page)
        log("Step: submit personal data…")
        if not await click_text_human(page, r"(confirmar|reservar|finalizar|aceptar)"):
            await click_text_human(page, r"(enviar|guardar)")
        await pause_read(); await snap(page, "tie-after-data-form")

    log("Step: check confirmation…")
    booked = await confirmation_detected(page)
    if booked:
        log("✅ Cita confirmada. Leaving this window open for the resguardo.")
        await snap(page, "tie-confirmed")
    else:
        log("No confirmation text detected (may still be booked — check tab).")
        await snap(page, "tie-no-confirm-text")
    return booked, False

# -------------- Runner --------------
async def main():
    log(f"Auto-booking ({TRAMITE_MODE}) — starting. Closes window per iteration unless booked.")
    log(f"Running build: {BUILD_TAG}")
    booked = False
    blocked = False
    async with async_playwright() as pw:
        while True:
            if CLEAR_SHOTS_EACH_CYCLE:
                reset_shots_dir()
            # Fresh *window* (persistent context) each cycle
            ctx = await make_context(pw)
            page = await ctx.new_page()
            attach_debug_listeners(page)
            t0 = time.time()
            
            try:
                if TRAMITE_MODE == "TIE":
                    booked, blocked = await run_cycle_tie(page)
                else:
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


