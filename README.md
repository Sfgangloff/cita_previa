# Auto-Booking Green NIE (Mallorca-only)

This script uses [Playwright](https://playwright.dev/python/) to automatically check and book appointments (*citas previas*) for the **Certificado de Registro de Ciudadano de la UE** (Green NIE) in the **Mallorca** offices only.

It navigates the official Spanish government "Cita Previa" portal, selects the correct province, trámite, and office, picks the earliest available date and time slot, fills in your details, and confirms the appointment.

---

## ⚠️ Disclaimer

- Use at your own risk and **respect the official portal’s terms of service**.
- This script interacts with the site exactly like a human would (no CAPTCHAs are bypassed, no hidden APIs are exploited).
- The code is tailored for the *Green NIE* appointment flow in Illes Balears and **filters to Mallorca offices only**.
- Always double-check the appointment details before relying on them.

---

## Features

- Runs locally on your computer — no server required.
- Checks every 5 minutes (configurable) until an appointment is available.
- Filters **province** to *Illes Balears* and **trámite** to *Certificado de Registro de Ciudadano de la UE*.
- Filters **offices** to Mallorca only (excludes Menorca, Ibiza, Formentera).
- Picks the **earliest available date and time**.
- Fills your personal details from a `.env` file.
- Confirms the appointment and logs the result.

---

## Requirements

- Python **3.8+** (tested on 3.10+)
- [Playwright for Python](https://playwright.dev/python/)
- [python-dotenv](https://pypi.org/project/python-dotenv/)

---

## Installation

1. **Clone or download** this project to your local machine.

2. **Create a virtual environment** and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate   # macOS/Linux
   # .\venv\Scripts\activate  # Windows PowerShell

   pip install playwright python-dotenv
   python -m playwright install
   ```

3. **Create a `.env` file** in the same directory as the script with your details:
   ```
   NIE_DNI=Y1234567X
   FULL_NAME=Jane Doe
   NATIONALITY=FRANCESA
   EMAIL=jane@example.com
   PHONE=612345678
   ```

   - `NATIONALITY` should match the nationality name shown in the dropdown on the portal (case-insensitive).

4. **Adjust configuration** in the script if needed:
   - `CHECK_PERIOD_SEC` — how often to retry if no slots (default: 5 minutes).
   - `MALLORCA_INCLUDE_TOKENS` — words that must appear in office names.
   - `MALLORCA_EXCLUDE_TOKENS` — words that must *not* appear in office names.

---

## Usage

Activate your virtual environment and run:

```bash
python autobook_green_nie_mallorca.py
```

The script will:

1. Launch Chromium in a visible window (`HEADLESS=False` by default).
2. Navigate to the official Cita Previa portal.
3. Select:
   - **Province:** Illes Balears
   - **Trámite:** Certificado de Registro de Ciudadano de la UE
   - **Office:** First one matching Mallorca include/exclude filters
4. Look for available appointments.
5. If a slot is available:
   - Select earliest date/time
   - Fill your details from `.env`
   - Submit and confirm
6. If no slot:
   - Close the browser
   - Wait 5 minutes (+ jitter) and try again

---

## Customisation

- **Target a different province or trámite:**
  - Change `PROVINCIA_KEY` and/or `TRAMITE_TOKENS` in the script.
- **Target specific Mallorca offices:**
  - Narrow `MALLORCA_INCLUDE_TOKENS` to just the desired office name(s).
- **Run headless:**
  - Set `HEADLESS = True` in the script.

---

## Stopping the Script

Press `Ctrl+C` in the terminal to stop it.

---

## Troubleshooting

- **Wrong trámite selected:**  
  Run once, watch the browser, and note the exact trámite text. Adjust `TRAMITE_TOKENS` in the script so all the important words are included.

- **Office filter excludes everything:**  
  The script will log all available offices if no match is found — adjust `MALLORCA_INCLUDE_TOKENS` or `MALLORCA_EXCLUDE_TOKENS` accordingly.

- **Playwright not installed properly:**  
  Run:
  ```bash
  pip install playwright
  python -m playwright install
  ```

---

## License

MIT License — free to use, modify, and distribute.
