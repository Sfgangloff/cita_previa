import asyncio, os, random, sys, time, re
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError

# ==========================
# CONFIG
# ==========================
PORTAL_URL = "https://sede.administracionespublicas.gob.es/pagina/index/directorio/icpplus"

PROVINCIA_KEY = "illes balears"
TRAMITE_TOKENS = ["certificado de registro", "ciudadano", "u.e"]

# Only pick offices in Mallorca:
FILTER_OFFICES_TO_MALLORCA = True
MALLORCA_INCLUDE_TOKENS = [
    "mallorca", "palma", "palma de mallorca", "inca", "manacor", "calvià", "calvia", "marratxí", "marratxi"
]
MALLORCA_EXCLUDE_TOKENS = [
    "menorca", "maó", "mao", "ciutadella",
    "ibiza", "eivissa", "formentera", "sant antoni"
]

HEADLESS = False
CHECK_PERIOD_SEC = 5 * 60
JITTER_SEC = 20
HUMAN_PAUSE_SEC = 0.4
EXTRA_LOG = False

# ==========================
# Personal data
# ==========================
load_dotenv()
def need(k):
    v = os.getenv(k, "").strip()
    if not v: raise ValueError(f"Missing {k} in .env")
    return v

NIE_DNI     = need("NIE_DNI")
FULL_NAME   = need("FULL_NAME")
NATIONALITY = need("NATIONALITY")
EMAIL       = need("EMAIL")
PHONE       = need("PHONE")

# ==========================
# Helpers
# ==========================
def log(msg):
    tz_madrid = timezone(timedelta(hours=2))
    now = datetime.now(tz=tz_madrid).strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"[{now}] {msg}", flush=True)

async def short_pause(): await asyncio.sleep(HUMAN_PAUSE_SEC)

def _matches_all(text, tokens): 
    t = text.lower(); return all(tok.lower() in t for tok in tokens)

def _contains_any(text, tokens):
    t = text.lower(); return any(tok.lower() in t for tok in tokens)

def _contains_none(text, tokens):
    t = text.lower(); return all(tok.lower() not in t for tok in tokens)

async def select_option_by_contains(select, needle):
    for opt in await select.locator("option").all():
        txt = (await opt.text_content() or "").strip()
        if needle.lower() in txt.lower():
            val = await opt.get_attribute("value")
            if val: 
                await select.select_option(value=val)
                return True
    return False

async def select_option_by_tokens(select, tokens):
    for opt in await select.locator("option").all():
        txt = (await opt.text_content() or "").strip()
        if _matches_all(txt, tokens):
            val = await opt.get_attribute("value")
            if val:
                await select.select_option(value=val)
                return True
    return False

async def click_any(page, labels_regex):
    re_obj = re.compile(labels_regex, re.I)
    for role in ("button", "link"):
        try:
            await page.get_by_role(role, name=re_obj).first.click(timeout=2000)
            return True
        except Exception:
            pass
    try:
        await page.locator(f"text=/{labels_regex}/i").first.click(timeout=1500)
        return True
    except Exception:
        return False

async def expect_text(page, substrings, timeout=15000):
    pattern_js = " || ".join([f"t.includes({repr(s.lower())})" for s in substrings])
    await page.wait_for_function(f"""() => {{
        const t = document.body.innerText.toLowerCase();
        return {pattern_js};
    }}""", timeout=timeout)

async def fill_if_exists(page, selectors, value):
    for sel in selectors:
        loc = page.locator(sel)
        if await loc.count():
            await loc.fill(value); 
            return True
    return False

async def check_if_no_slots(page):
    txt = (await page.content()).lower()
    return any(p in txt for p in [
        "no hay citas disponibles", "no existen citas disponibles", 
        "en este momento no hay citas disponibles"
    ])

async def calendar_present(page):
    sels = [
        "table.ui-datepicker-calendar", "div.ui-datepicker", "div.datepicker",
        "div#calendar", "div[class*=calendar]"
    ]
    return any([await page.locator(sel).count() for sel in sels])

async def pick_first_enabled_day(page):
    loc = page.locator(", ".join([
        "table.ui-datepicker-calendar td:not(.ui-datepicker-unselectable) a",
        "div.ui-datepicker td:not(.ui-datepicker-unselectable) a",
        "div.datepicker td:not(.disabled) a",
        "div#calendar td a",
        "div[class*=calendar] td a"
    ]))
    if await loc.count():
        await loc.first.click()
        return True
    return False

async def pick_first_time_slot(page):
    radios = page.locator("input[type=radio][name*=hora], input[type=radio][id*=hora], div.hora input[type=radio]")
    if await radios.count():
        await radios.first.check(); 
        return True
    buttons = page.locator("button:has-text('Seleccionar'), a:has-text('Seleccionar')")
    if await buttons.count():
        await buttons.first.click(); 
        return True
    any_btn = page.locator("button, a").filter(has_text=re.compile("seleccionar|cita", re.I))
    if await any_btn.count():
        await any_btn.first.click(); 
        return True
    return False

async def data_form_present(page):
    candidates = ["#txtIdCitado", "[name*=documento]", "[id*=documento]", "#txtNombre",
                  "[name*=nombre]", "[id*=nombre]", "[name*=correo]", "[id*=correo]"]
    return any([await page.locator(sel).count() for sel in candidates])

async def fill_personal_data(page):
    await fill_if_exists(page, ["#txtIdCitado", "[name*=txtIdCitado]", "[name*=documento]", "[id*=documento]"], NIE_DNI); await short_pause()
    await fill_if_exists(page, ["#txtNombre", "[name*=nombre]", "[id*=nombre]"], FULL_NAME); await short_pause()
    # Nationality dropdown:
    for sel in ["select#txtPaisNac", "select#paisNac", "select[name*=nacionalidad]", "select[id*=nacionalidad]"]:
        if await page.locator(sel).count():
            await select_option_by_contains(page.locator(sel), NATIONALITY); 
            break
    await short_pause()
    await fill_if_exists(page, ["#txtCorreo", "[name*=correo]", "[id*=correo]", "[name*=email]", "[id*=email]"], EMAIL); await short_pause()
    await fill_if_exists(page, ["#txtTelefono", "[name*=telefono]", "[id*=telefono]"], PHONE)
    for sel in ["input[type=checkbox][name*=condiciones]", "input[type=checkbox][id*=condiciones]",
                "input[type=checkbox][name*=lopd]", "input[type=checkbox][id*=lopd]"]:
        if await page.locator(sel).count():
            await page.check(sel); 
            break

async def confirmation_detected(page):
    try:
        await expect_text(page, ["cita confirmada", "localizador", "resguardo"], timeout=8000)
        return True
    except PWTimeoutError:
        return False

# ---- Office filtering to Mallorca ----
async def choose_mallorca_office_if_needed(page) -> bool:
    """
    If an office <select> is present, choose the first option that:
    - contains ANY token from MALLORCA_INCLUDE_TOKENS, and
    - contains NONE of MALLORCA_EXCLUDE_TOKENS.
    Returns True if an office was selected or if no office selection is required.
    """
    office = page.locator("select#sede, select[name*=oficina], select[id*=oficina], select[name*=sede]")
    if not await office.count():
        return True  # no office selection required

    options = await office.locator("option").all()
    visible = []
    for opt in options:
        val = (await opt.get_attribute("value")) or ""
        txt = ((await opt.text_content()) or "").strip()
        if val and txt:
            visible.append((val, txt))

    if not visible:
        return False

    if FILTER_OFFICES_TO_MALLORCA:
        # keep only Mallorca, exclude Menorca/Ibiza/Formentera
        mallorca_opts = [
            (val, txt) for (val, txt) in visible
            if _contains_any(txt, MALLORCA_INCLUDE_TOKENS) and _contains_none(txt, MALLORCA_EXCLUDE_TOKENS)
        ]
        if not mallorca_opts:
            log("⚠️ No office matching Mallorca tokens was found. Available offices were:")
            for _, txt in visible: log(f"   - {txt}")
            return False
        val, txt = mallorca_opts[0]
        log(f"Seleccionando oficina Mallorca: {txt}")
        await office.select_option(value=val)
    else:
        # pick first non-empty
        val, txt = visible[0]
        log(f"Seleccionando oficina: {txt}")
        await office.select_option(value=val)

    await short_pause()
    await click_any(page, r"(aceptar|continuar|siguiente)")
    await short_pause()
    return True

# ==========================
# Main flow
# ==========================
async def one_attempt(pw) -> bool:
    browser = await pw.chromium.launch(headless=HEADLESS, args=["--start-maximized"])
    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    page = await context.new_page()
    try:
        if EXTRA_LOG: log("Opening portal…")
        await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=45000); await short_pause()
        await click_any(page, r"(entrar|acceder|iniciar)"); await short_pause()

        provincia = page.locator("select#form, select[name*=provincia], select[id*=provincia]")
        await provincia.wait_for(timeout=15000)
        if not await select_option_by_contains(provincia, PROVINCIA_KEY):
            opts = await provincia.locator("option").all_inner_texts()
            raise RuntimeError(f"No province containing '{PROVINCIA_KEY}'. Options: {opts}")
        await short_pause()
        await click_any(page, r"(aceptar|continuar|siguiente)"); await short_pause()

        tramite = page.locator("select#tramiteGrupo, select[name*=tramite], select[id*=tramite]")
        await tramite.wait_for(timeout=15000)
        if not await select_option_by_tokens(tramite, TRAMITE_TOKENS):
            opts = [o.strip() for o in await tramite.locator("option").all_inner_texts() if o.strip()]
            raise RuntimeError("Trámite not found with tokens. Available:\n - " + "\n - ".join(opts))
        await short_pause()
        await click_any(page, r"(aceptar|continuar|solicitar cita|entrar|acceder)"); await short_pause()

        # ---- choose Mallorca office only ----
        if not await choose_mallorca_office_if_needed(page):
            await context.close(); await browser.close()
            return False

        try:
            await page.wait_for_function("""() => {
                const t = document.body.innerText.toLowerCase();
                return t.includes('no hay citas disponibles') || t.includes('no existen citas disponibles') ||
                       document.querySelector('table.ui-datepicker-calendar, div.ui-datepicker, div.datepicker, div#calendar, div[class*=calendar]');
            }""", timeout=15000)
        except PWTimeoutError:
            pass

        if await check_if_no_slots(page):
            await context.close(); await browser.close()
            return False

        if not await pick_first_enabled_day(page):
            await context.close(); await browser.close()
            return False
        await short_pause()

        if not await pick_first_time_slot(page):
            await context.close(); await browser.close()
            return False
        await short_pause()

        await click_any(page, r"(continuar|siguiente|aceptar|confirmar)"); await short_pause()

        if await data_form_present(page):
            await fill_personal_data(page); await short_pause()
            submitted = await click_any(page, r"(confirmar|reservar|finalizar|aceptar)")
            if not submitted:
                await click_any(page, r"(enviar|guardar)")
            await short_pause()

        ok = await confirmation_detected(page)
        if ok:
            log("✅ Cita confirmada (revisa el navegador para el localizador/resguardo).")
        else:
            log("⚠️ Enviado, pero no pude verificar el texto de confirmación. Revisa el navegador.")
        return ok

    except Exception as e:
        log(f"⚠️ Error during attempt: {e}")
        try: await context.close()
        except: pass
        try: await browser.close()
        except: pass
        return False

async def main():
    log("Auto-booking green NIE (Mallorca-only) — starting.")
    async with async_playwright() as pw:
        while True:
            t0 = time.time()
            booked = await one_attempt(pw)
            if booked:
                log("Process finished.")
                break
            sleep_for = CHECK_PERIOD_SEC + random.randint(0, JITTER_SEC)
            elapsed = time.time() - t0
            to_sleep = max(5, sleep_for - elapsed)
            log(f"No cita this round. Retrying in ~{int(to_sleep)}s…")
            await asyncio.sleep(to_sleep)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Stopped by user.")
        sys.exit(0)
