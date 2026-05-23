"""
Landline Validity Checker
- Full stealth mode to bypass bot detection
- Dual strategy: API interception + DOM fallback
- Auto-retry on page load failure
- Works on Python 3.14 Windows + Streamlit Cloud (Linux)
"""
import sys
import asyncio

if sys.platform == "win32":
    _loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(_loop)

import subprocess
import streamlit as st
import pandas as pd
from io import BytesIO
import json

@st.cache_resource(show_spinner="Installing browser (first run only)…")
def install_playwright_browsers():
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                   capture_output=True)
    subprocess.run([sys.executable, "-m", "playwright", "install-deps", "chromium"],
                   capture_output=True)
    return True

install_playwright_browsers()

# ── Full stealth init script ──────────────────────────────────────────────────
STEALTH_SCRIPT = """
// Hide webdriver
Object.defineProperty(navigator, 'webdriver', {get: () => false});

// Mock plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5],
});

// Mock languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en'],
});

// Mock permissions
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
);

// Hide automation chrome
window.chrome = { runtime: {} };

// Mock screen dimensions
Object.defineProperty(screen, 'width',  {get: () => 1920});
Object.defineProperty(screen, 'height', {get: () => 1080});
"""

async def check_numbers_async(numbers, delay_ms, progress_cb, status_cb, log_cb):
    from playwright.async_api import async_playwright, expect as aexpect

    TARGET  = "https://eand.ae/ecare/c/quick-pay"
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--window-size=1920,1080",
                "--start-maximized",
                "--disable-infobars",
                "--disable-extensions",
                "--ignore-certificate-errors",
                "--allow-running-insecure-content",
            ]
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="Asia/Dubai",
            # Pretend to be a real desktop browser
            java_script_enabled=True,
            accept_downloads=False,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            }
        )

        await context.add_init_script(STEALTH_SCRIPT)
        page = await context.new_page()

        # ── API interception state ─────────────────────────────────────────────
        api_result = {"data": None, "received": False}

        def handle_response(response):
            url = response.url.lower()
            if any(k in url for k in ["account", "bill", "quick", "pay",
                                       "balance", "inquiry", "ecare", "api"]):
                async def read_body():
                    try:
                        body = await response.json()
                        api_result["data"]     = body
                        api_result["received"] = True
                    except Exception:
                        try:
                            text = await response.text()
                            if any(k in text.lower() for k in
                                   ["amount", "bill", "valid", "invalid", "account"]):
                                api_result["data"]     = text
                                api_result["received"] = True
                        except Exception:
                            pass
                asyncio.ensure_future(read_body())

        page.on("response", handle_response)

        # ── Helpers ───────────────────────────────────────────────────────────
        async def load_page(retries=3):
            """Load TARGET with retries and stealth delays."""
            for attempt in range(1, retries + 1):
                try:
                    api_result["data"]     = None
                    api_result["received"] = False
                    # Random-ish delay to look human
                    await asyncio.sleep(0.5 + attempt * 0.3)
                    await page.goto(TARGET, timeout=60000,
                                    wait_until="domcontentloaded")
                    await page.wait_for_load_state("networkidle", timeout=30000)
                    # Small human-like pause
                    await asyncio.sleep(0.8)
                    return True
                except Exception as e:
                    log_cb(f"  ⚠️ Load attempt {attempt}/3 failed: {str(e)[:60]}")
                    if attempt == retries:
                        return False
                    await asyncio.sleep(2 * attempt)
            return False

        async def find_input():
            for sel in [
                'input[type="tel"]',
                'input[placeholder*="number" i]',
                'input[placeholder*="account" i]',
                'input[placeholder*="phone" i]',
                'input[placeholder*="landline" i]',
                'input[type="text"]',
            ]:
                try:
                    await page.wait_for_selector(sel, timeout=6000)
                    if await page.locator(sel).count() > 0:
                        return sel
                except Exception:
                    continue
            return None

        async def find_next_btn():
            for label in ["Next", "Submit", "Check", "Go", "Search", "Proceed"]:
                btn = page.locator(f'button:has-text("{label}")')
                if await btn.count() > 0 and await btn.first.is_visible():
                    return btn.first
            btn = page.locator('button[type="submit"]')
            if await btn.count() > 0:
                return btn.first
            return None

        def parse_api_result(data):
            if data is None:
                return None, None
            text = json.dumps(data).lower() if isinstance(data, dict) else str(data).lower()
            if any(k in text for k in ["invalid", "not found", "no account",
                                        "notfound", "does not exist"]):
                return "Invalid", ""
            if isinstance(data, dict):
                for key in ["amountdue", "amount_due", "amount", "bill",
                             "balance", "outstandingamount", "totalamount",
                             "dueamount", "outstanding", "total"]:
                    val = find_key(data, key)
                    if val is not None:
                        return "Valid", str(val)
                return "Valid", ""
            if any(k in text for k in ["valid", "amount", "bill", "balance"]):
                return "Valid", ""
            return None, None

        def find_key(d, key):
            if isinstance(d, dict):
                for k, v in d.items():
                    if k.lower() == key:
                        return v
                    r = find_key(v, key)
                    if r is not None:
                        return r
            elif isinstance(d, list):
                for item in d:
                    r = find_key(item, key)
                    if r is not None:
                        return r
            return None

        # ── Initial load ──────────────────────────────────────────────────────
        log_cb(f"🌐 Loading {TARGET} …")
        ok = await load_page(retries=3)
        if not ok:
            log_cb("❌ Could not load website after 3 attempts!")
            await browser.close()
            return [{"number": n, "status": "Error",
                     "bill": "Website unreachable"} for n in numbers]
        log_cb("✅ Site loaded.")

        active_sel = await find_input()
        if not active_sel:
            # Take a screenshot to help debug (saved to /tmp)
            await page.screenshot(path="/tmp/debug_noInput.png")
            log_cb(f"❌ No input found. Page title: {await page.title()}")
            await browser.close()
            return [{"number": n, "status": "Error",
                     "bill": "Input field not found"} for n in numbers]

        log_cb(f"🔍 Input detected: {active_sel}")
        total = len(numbers)

        for idx, number in enumerate(numbers):
            status_cb(f"⏳ ({idx+1}/{total}) Checking: **{number}**")
            log_cb(f"({idx+1}/{total}) → {number}")
            status, bill = "Error", ""

            api_result["data"]     = None
            api_result["received"] = False

            try:
                # Ensure input exists
                if await page.locator(active_sel).count() == 0:
                    log_cb("  ⚠️ Input gone, reloading…")
                    ok = await load_page(retries=2)
                    if not ok:
                        raise Exception("Page reload failed")
                    active_sel = await find_input() or active_sel

                # Human-like: small pause before typing
                await asyncio.sleep(0.3)
                await page.fill(active_sel, "")
                await asyncio.sleep(0.2)
                # Type like a human (char by char) instead of instant fill
                await page.type(active_sel, number, delay=50)

                # Human-like: pause before clicking
                await asyncio.sleep(0.4)
                btn = await find_next_btn()
                if btn:
                    await btn.click()
                else:
                    await page.keyboard.press("Enter")

                # ── Strategy 1: API interception ──────────────────────────────
                api_status, api_bill = None, None
                for _ in range(80):          # wait up to 8 seconds
                    await asyncio.sleep(0.1)
                    if api_result["received"]:
                        api_status, api_bill = parse_api_result(api_result["data"])
                        if api_status is not None:
                            break

                if api_status is not None:
                    status = api_status
                    bill   = api_bill or ""
                    icon   = "✅" if status == "Valid" else "❌"
                    log_cb(f"  {icon} {status}" + (f" — Bill: {bill}" if bill else ""))

                else:
                    # ── Strategy 2: DOM fallback ──────────────────────────────
                    log_cb("  ℹ️ Falling back to DOM…")
                    valid_loc   = page.locator("#amountPaid")
                    invalid_loc = page.locator("text=Invalid account number")
                    try:
                        await aexpect(
                            valid_loc.or_(invalid_loc)
                        ).to_be_visible(timeout=15000)

                        if await valid_loc.is_visible():
                            bill   = await valid_loc.input_value()
                            status = "Valid"
                            log_cb(f"  ✅ Valid (DOM) — Bill: {bill}")
                        elif await invalid_loc.is_visible():
                            status = "Invalid"
                            log_cb(f"  ❌ Invalid (DOM)")
                        else:
                            status = "Unknown"
                    except Exception as dom_err:
                        status = "Error"
                        bill   = f"Timeout: {str(dom_err)[:60]}"
                        log_cb(f"  ⚠️ DOM timeout: {str(dom_err)[:50]}")

                # ── Go back to form ───────────────────────────────────────────
                back = page.locator('button:has-text("Back")')
                if await back.is_visible():
                    await back.click()
                    try:
                        await page.wait_for_selector(active_sel, timeout=6000)
                    except Exception:
                        await load_page(retries=2)
                        active_sel = await find_input() or active_sel
                else:
                    await load_page(retries=2)
                    active_sel = await find_input() or active_sel

            except Exception as e:
                status = "Error"
                bill   = str(e)[:120]
                log_cb(f"  ⚠️ Error: {str(e)[:80]}")
                try:
                    await load_page(retries=2)
                    active_sel = await find_input() or active_sel
                except Exception:
                    pass

            results.append({"number": number, "status": status, "bill": bill})
            progress_cb((idx + 1) / total)
            await asyncio.sleep(max(delay_ms / 1000 - 0.5, 0.5))

        await browser.close()
    return results


def run_check(numbers, delay_ms, progress_cb, status_cb, log_cb):
    if sys.platform == "win32":
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.ProactorEventLoop()
            asyncio.set_event_loop(loop)
    else:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            check_numbers_async(numbers, delay_ms, progress_cb, status_cb, log_cb)
        )
    finally:
        if sys.platform != "win32":
            loop.close()


# ── UI ────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Landline Validity Checker", page_icon="📋", layout="wide")
st.markdown("<style>.stProgress>div>div{background:#00b4d8}</style>", unsafe_allow_html=True)
st.title("📋 Landline Validity Checker")
st.markdown("Upload an Excel file with landline numbers to check validity on the E&D portal.")

uploaded_file = st.file_uploader("Upload Excel file (.xlsx / .xls)", type=["xlsx", "xls"])

if uploaded_file:
    try:
        df = pd.read_excel(uploaded_file, dtype={"Landline": str})

        for col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                df[col] = df[col].astype(str)
            elif df[col].dtype == "object":
                df[col] = df[col].astype(str)

        if "Landline" not in df.columns:
            st.error("❌ File must have a column named **'Landline'**.")
            st.stop()

        st.subheader("📊 Preview")
        st.dataframe(df.head(10), width="stretch")
        st.caption(f"Total rows: {len(df)}")

        with st.expander("⚙️ Settings"):
            delay_ms = st.slider(
                "Delay between requests (ms)", 500, 4000, 1500, step=250,
                help="Lower = faster. Increase if website blocks requests."
            )

        if st.button("🚀 Start Validity Check", type="primary"):

            progress_bar       = st.progress(0)
            status_placeholder = st.empty()
            log_placeholder    = st.empty()
            log_lines          = []

            def log(msg):
                log_lines.append(msg)
                log_placeholder.code("\n".join(log_lines[-10:]))

            numbers = []
            for n in df["Landline"].astype(str).str.strip():
                numbers.append(n if n.startswith("0") else "0" + n)

            results = run_check(
                numbers     = numbers,
                delay_ms    = delay_ms,
                progress_cb = lambda v: progress_bar.progress(v),
                status_cb   = lambda m: status_placeholder.info(m),
                log_cb      = log,
            )

            df["Status"] = [r["status"] for r in results]
            df["Bill"]   = [r["bill"]   for r in results]

            status_placeholder.success("✅ Done!")
            log_placeholder.empty()

            st.subheader("📋 Results")
            st.dataframe(df, width="stretch")

            out = BytesIO()
            with pd.ExcelWriter(out, engine="openpyxl") as w:
                df.to_excel(w, index=False)
            out.seek(0)

            st.download_button(
                "📥 Download Results",
                data=out.getvalue(),
                file_name="validity_results.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

            st.subheader("📊 Summary")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total",      len(df))
            c2.metric("✅ Valid",   int((df["Status"] == "Valid").sum()))
            c3.metric("❌ Invalid", int((df["Status"] == "Invalid").sum()))
            c4.metric("⚠️ Errors",  int((df["Status"] == "Error").sum()))

    except Exception as e:
        st.error(f"❌ Error: {e}")
        st.info("Make sure your file is a valid .xlsx with a 'Landline' column.")
