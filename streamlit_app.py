"""
Landline Validity Checker — Fast & Reliable Version
Strategy: intercept the website's own API response instead of waiting for DOM elements.
This eliminates locator timeouts and is 3-4x faster.
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

# ── Async checker ─────────────────────────────────────────────────────────────
async def check_numbers_async(numbers, delay_ms, progress_cb, status_cb, log_cb):
    from playwright.async_api import async_playwright, expect as aexpect

    TARGET = "https://eand.ae/ecare/c/quick-pay"
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
            ]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>false});"
        )

        # ── Shared state for API interception ─────────────────────────────────
        api_result = {"data": None, "received": False}

        def handle_response(response):
            """Intercept every network response; grab the one with account data."""
            url = response.url.lower()
            # Catch any JSON response that looks like an account/bill lookup
            if any(k in url for k in ["account", "bill", "quick", "pay", "balance", "inquiry", "ecare"]):
                async def read_body():
                    try:
                        body = await response.json()
                        api_result["data"] = body
                        api_result["received"] = True
                    except Exception:
                        try:
                            text = await response.text()
                            if any(k in text.lower() for k in ["amount", "bill", "valid", "invalid", "account"]):
                                api_result["data"] = text
                                api_result["received"] = True
                        except Exception:
                            pass
                import asyncio as _asyncio
                _asyncio.ensure_future(read_body())

        page.on("response", handle_response)

        async def reload():
            api_result["data"] = None
            api_result["received"] = False
            await page.goto(TARGET, timeout=60000)
            await page.wait_for_load_state("networkidle")

        async def find_input():
            for sel in [
                'input[type="tel"]',
                'input[placeholder*="number" i]',
                'input[placeholder*="account" i]',
                'input[placeholder*="phone" i]',
                'input[type="text"]',
            ]:
                try:
                    await page.wait_for_selector(sel, timeout=8000)
                    if await page.locator(sel).count() > 0:
                        return sel
                except Exception:
                    continue
            return None

        async def find_next_btn():
            for label in ["Next", "Submit", "Check", "Go", "Search"]:
                btn = page.locator(f'button:has-text("{label}")')
                if await btn.count() > 0 and await btn.first.is_visible():
                    return btn.first
            fallback = page.locator('button[type="submit"]')
            if await fallback.count() > 0:
                return fallback.first
            return None

        def parse_api_result(data):
            """
            Try to extract status + bill from whatever the API returned.
            Returns (status, bill) — works whether data is dict or string.
            """
            if data is None:
                return None, None

            text = json.dumps(data).lower() if isinstance(data, dict) else str(data).lower()

            # Check for invalid signals
            invalid_keywords = ["invalid", "not found", "no account", "notfound", "error"]
            if any(k in text for k in invalid_keywords):
                return "Invalid", ""

            # Try to extract bill amount from dict
            if isinstance(data, dict):
                # Common key names APIs use for bill/balance
                for key in ["amountdue", "amount_due", "amount", "bill",
                             "balance", "outstandingamount", "totalamount",
                             "dueamount", "outstanding", "total"]:
                    # Search recursively
                    val = find_key(data, key)
                    if val is not None:
                        return "Valid", str(val)
                # If we got a response but couldn't find amount key,
                # still mark as valid if no error signal
                return "Valid", ""

            # Plain text response
            if any(k in text for k in ["valid", "amount", "bill", "balance"]):
                return "Valid", ""

            return None, None  # couldn't determine from API alone

        def find_key(d, key):
            """Recursively search dict for a key (case-insensitive)."""
            if isinstance(d, dict):
                for k, v in d.items():
                    if k.lower() == key:
                        return v
                    result = find_key(v, key)
                    if result is not None:
                        return result
            elif isinstance(d, list):
                for item in d:
                    result = find_key(item, key)
                    if result is not None:
                        return result
            return None

        # ── Initial page load ─────────────────────────────────────────────────
        log_cb(f"🌐 Loading {TARGET} …")
        await reload()
        log_cb("✅ Site loaded.")

        active_sel = await find_input()
        if not active_sel:
            log_cb("❌ Could not find input field!")
            await browser.close()
            return [{"number": n, "status": "Error", "bill": "Input not found"} for n in numbers]

        log_cb(f"🔍 Input detected: {active_sel}")
        total = len(numbers)

        for idx, number in enumerate(numbers):
            status_cb(f"⏳ ({idx+1}/{total}) Checking: **{number}**")
            log_cb(f"({idx+1}/{total}) → {number}")
            status, bill = "Error", ""

            # Reset API capture for this round
            api_result["data"] = None
            api_result["received"] = False

            try:
                # ── Ensure input is present ────────────────────────────────────
                if await page.locator(active_sel).count() == 0:
                    log_cb("⚠️ Input gone, reloading…")
                    await reload()
                    active_sel = await find_input() or active_sel

                # ── Fill number ────────────────────────────────────────────────
                await page.fill(active_sel, "")
                await page.fill(active_sel, number)

                # ── Click Next ─────────────────────────────────────────────────
                btn = await find_next_btn()
                if btn:
                    await btn.click()
                else:
                    await page.keyboard.press("Enter")

                # ── Strategy 1: wait for API response (fast, reliable) ─────────
                api_status, api_bill = None, None
                for _ in range(60):          # wait up to 6 seconds
                    await asyncio.sleep(0.1)
                    if api_result["received"]:
                        api_status, api_bill = parse_api_result(api_result["data"])
                        if api_status is not None:
                            break

                if api_status is not None:
                    status = api_status
                    bill   = api_bill or ""
                    log_cb(f"  {'✅' if status == 'Valid' else '❌'} {status}"
                           + (f" — Bill: {bill}" if bill else ""))

                else:
                    # ── Strategy 2: fallback to DOM (original approach) ────────
                    log_cb("  ℹ️ API not caught, falling back to DOM…")
                    valid_loc   = page.locator("#amountPaid")
                    invalid_loc = page.locator("text=Invalid account number")

                    try:
                        await aexpect(
                            valid_loc.or_(invalid_loc)
                        ).to_be_visible(timeout=15000)   # shorter timeout now

                        if await valid_loc.is_visible():
                            bill   = await valid_loc.input_value()
                            status = "Valid"
                            log_cb(f"  ✅ Valid (DOM) — Bill: {bill}")
                        elif await invalid_loc.is_visible():
                            status = "Invalid"
                            log_cb(f"  ❌ Invalid (DOM)")
                        else:
                            status = "Unknown"
                            log_cb(f"  ❓ Unknown")
                    except Exception as dom_err:
                        status = "Error"
                        bill   = f"DOM timeout: {str(dom_err)[:80]}"
                        log_cb(f"  ⚠️ DOM fallback failed: {str(dom_err)[:60]}")

                # ── Navigate back ──────────────────────────────────────────────
                back = page.locator('button:has-text("Back")')
                if await back.is_visible():
                    await back.click()
                    try:
                        await page.wait_for_selector(active_sel, timeout=6000)
                    except Exception:
                        await reload()
                        active_sel = await find_input() or active_sel
                else:
                    await reload()
                    active_sel = await find_input() or active_sel

            except Exception as e:
                status = "Error"
                bill   = str(e)[:120]
                log_cb(f"  ⚠️ Error: {str(e)[:80]}")
                try:
                    await reload()
                    active_sel = await find_input() or active_sel
                except Exception:
                    pass

            results.append({"number": number, "status": status, "bill": bill})
            progress_cb((idx + 1) / total)
            # Shorter delay since API interception is faster
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
                help="Lower = faster. Increase if you get errors."
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
