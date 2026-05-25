"""
Landline Validity Checker
Each number gets its own fresh Browserless session - no shared state.
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


async def check_one(number: str, token: str, log_cb) -> tuple[str, str]:
    """
    One number = one fresh browser session.
    Works with both Browserless (cloud) and local Playwright.
    """
    from playwright.async_api import async_playwright, expect as aexpect

    TARGET = "https://eand.ae/ecare/c/quick-pay"

    async with async_playwright() as p:
        # ── Connect to browser ────────────────────────────────────────────────
        if token:
            try:
                ws = f"wss://chrome.browserless.io?token={token}"
                browser = await p.chromium.connect_over_cdp(ws)
            except Exception as e:
                log_cb(f"  ⚠️ Browserless connect failed: {str(e)[:50]}")
                return "Error", "Browserless connection failed"
        else:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-gpu", "--disable-setuid-sandbox",
                      "--disable-blink-features=AutomationControlled"]
            )

        try:
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

            # ── Load page ─────────────────────────────────────────────────────
            try:
                await page.goto(TARGET, timeout=60000, wait_until="domcontentloaded")
                await page.wait_for_load_state("networkidle", timeout=20000)
            except Exception as e:
                return "Error", f"Load failed: {str(e)[:60]}"

            await asyncio.sleep(1)

            # ── Find input ────────────────────────────────────────────────────
            input_sel = None
            for sel in [
                'input[type="tel"]',
                'input[placeholder*="number" i]',
                'input[placeholder*="account" i]',
                'input[placeholder*="phone" i]',
                'input[type="text"]',
            ]:
                try:
                    await page.wait_for_selector(sel, timeout=5000)
                    if await page.locator(sel).count() > 0:
                        input_sel = sel
                        break
                except Exception:
                    continue

            if not input_sel:
                return "Error", "Input field not found"

            # ── Fill number ───────────────────────────────────────────────────
            await page.fill(input_sel, "")
            await asyncio.sleep(0.2)
            await page.fill(input_sel, number)
            await asyncio.sleep(0.3)

            # ── Click Next ────────────────────────────────────────────────────
            clicked = False
            for label in ["Next", "Submit", "Check", "Go"]:
                try:
                    btn = page.locator(f'button:has-text("{label}")')
                    if await btn.count() > 0 and await btn.first.is_visible():
                        await btn.first.click()
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                try:
                    btn = page.locator('button[type="submit"]')
                    if await btn.count() > 0:
                        await btn.first.click()
                        clicked = True
                except Exception:
                    pass

            if not clicked:
                await page.keyboard.press("Enter")

            # ── Wait for result ───────────────────────────────────────────────
            valid_loc   = page.locator("#amountPaid")
            invalid_loc = page.locator("text=Invalid account number")

            try:
                await aexpect(
                    valid_loc.or_(invalid_loc)
                ).to_be_visible(timeout=30000)
            except Exception as e:
                return "Error", f"Result timeout: {str(e)[:60]}"

            await asyncio.sleep(0.3)

            # ── Read result ───────────────────────────────────────────────────
            if await valid_loc.is_visible():
                bill = await valid_loc.input_value()
                return "Valid", bill

            if await invalid_loc.is_visible():
                return "Invalid", ""

            return "Unknown", ""

        finally:
            try:
                await browser.close()
            except Exception:
                pass


async def check_numbers_async(numbers, delay_ms, progress_cb, status_cb, log_cb):
    token = os.environ.get("BROWSERLESS_TOKEN", "")
    total = len(numbers)
    results = []

    if token:
        log_cb("☁️ Using Browserless cloud browser.")
    else:
        log_cb("🖥️ Using local browser.")

    for idx, number in enumerate(numbers):
        status_cb(f"⏳ ({idx+1}/{total}) Checking: **{number}**")
        log_cb(f"({idx+1}/{total}) → {number}")

        status, bill = await check_one(number, token, log_cb)

        if status == "Valid":
            log_cb(f"  ✅ Valid — Bill: {bill}")
        elif status == "Invalid":
            log_cb(f"  ❌ Invalid")
        elif status == "Error":
            log_cb(f"  ⚠️ {bill}")
        else:
            log_cb(f"  ❓ Unknown")

        results.append({"number": number, "status": status, "bill": bill})
        progress_cb((idx + 1) / total)
        await asyncio.sleep(delay_ms / 1000)

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

if not os.environ.get("BROWSERLESS_TOKEN"):
    st.warning("⚠️ **No BROWSERLESS_TOKEN set.** Add it in Streamlit Cloud → Settings → Secrets.")

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
                "Delay between numbers (ms)", 1000, 5000, 2000, step=500,
                help="Increase if you get errors."
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
