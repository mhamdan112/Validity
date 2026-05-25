"""
Landline Validity Checker
Uses Browserless.io cloud browser (free tier) which routes through global IPs
OR falls back to local Playwright if BROWSERLESS_TOKEN not set.
"""
import sys
import asyncio

if sys.platform == "win32":
    _loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(_loop)

import subprocess
import os
import streamlit as st
import pandas as pd
from io import BytesIO

@st.cache_resource(show_spinner="Installing browser (first run only)…")
def install_playwright_browsers():
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                   capture_output=True)
    subprocess.run([sys.executable, "-m", "playwright", "install-deps", "chromium"],
                   capture_output=True)
    return True

install_playwright_browsers()


async def check_numbers_async(numbers, delay_ms, progress_cb, status_cb, log_cb):
    from playwright.async_api import async_playwright, expect as aexpect

    TARGET = "https://eand.ae/ecare/c/quick-pay"
    results = []
    total   = len(numbers)

    # ── Check if Browserless token is configured ──────────────────────────────
    browserless_token = os.environ.get("BROWSERLESS_TOKEN", "")
    using_browserless = bool(browserless_token)

    async with async_playwright() as p:

        if using_browserless:
            # Connect to Browserless cloud browser (has real global IPs)
            log_cb("☁️ Connecting to cloud browser (Browserless)…")
            ws_url = f"wss://chrome.browserless.io?token={browserless_token}"
            try:
                browser = await p.chromium.connect_over_cdp(ws_url)
                log_cb("✅ Cloud browser connected.")
            except Exception as e:
                log_cb(f"❌ Browserless connection failed: {e}")
                log_cb("⚠️ Falling back to local browser…")
                using_browserless = False
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage",
                          "--disable-gpu", "--disable-setuid-sandbox"]
                )
        else:
            log_cb("🖥️ Using local browser…")
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox", "--disable-dev-shm-usage",
                    "--disable-gpu", "--disable-setuid-sandbox",
                ]
            )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="Asia/Dubai",
        )
        page = await context.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>false});"
        )

        # Load the site
        log_cb(f"🌐 Loading {TARGET}…")
        try:
            await page.goto(TARGET, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle", timeout=30000)
        except Exception as e:
            log_cb(f"❌ Page load failed: {e}")
            await browser.close()
            return [{"number": n, "status": "Error", "bill": "Page load failed"} for n in numbers]

        log_cb("✅ Site loaded. Starting checks…")

        for idx, number in enumerate(numbers):
            status_cb(f"⏳ ({idx+1}/{total}) Checking: **{number}**")
            log_cb(f"({idx+1}/{total}) → {number}")
            status, bill = "Error", ""

            try:
                await page.wait_for_selector('input[type="tel"]', timeout=15000)
                await page.fill('input[type="tel"]', "")
                await page.fill('input[type="tel"]', number)
                await asyncio.sleep(0.3)
                await page.click('button:has-text("Next")')

                valid_loc   = page.locator("#amountPaid")
                invalid_loc = page.locator("text=Invalid account number")

                await aexpect(valid_loc.or_(invalid_loc)).to_be_visible(timeout=30000)

                if await valid_loc.is_visible():
                    bill   = await valid_loc.input_value()
                    status = "Valid"
                    log_cb(f"  ✅ Valid — Bill: {bill}")
                elif await invalid_loc.is_visible():
                    status = "Invalid"
                    log_cb(f"  ❌ Invalid")
                else:
                    status = "Unknown"
                    log_cb(f"  ❓ Unknown")

                back = page.locator('button:has-text("Back")')
                if await back.is_visible():
                    await back.click()
                else:
                    await page.goto(TARGET, timeout=60000, wait_until="domcontentloaded")
                    await page.wait_for_load_state("networkidle", timeout=30000)

            except Exception as e:
                status = "Error"
                bill   = str(e)[:100]
                log_cb(f"  ⚠️ Error: {str(e)[:60]}")
                try:
                    await page.goto(TARGET, timeout=60000, wait_until="domcontentloaded")
                    await page.wait_for_load_state("networkidle", timeout=30000)
                except Exception:
                    pass

            results.append({"number": number, "status": status, "bill": bill})
            progress_cb((idx + 1) / total)
            await asyncio.sleep(delay_ms / 1000)

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

# Warn if no Browserless token
if not os.environ.get("BROWSERLESS_TOKEN"):
    st.warning(
        "⚠️ **Cloud browser not configured.** "
        "Add your `BROWSERLESS_TOKEN` in Streamlit Cloud secrets for this to work when hosted. "
        "Running locally will work fine without it."
    )

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
            delay_ms = st.slider("Delay between numbers (ms)", 1000, 5000, 2000, step=500)

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
