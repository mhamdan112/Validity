"""
Landline Validity Checker - Stable Working Version
Uses simple DOM checking - the version that worked correctly.
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
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="Asia/Dubai",
        )
        page = await context.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>false});"
        )

        async def reload():
            await page.goto(TARGET, timeout=60000, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)

        async def find_input():
            for sel in [
                'input[type="tel"]',
                'input[placeholder*="number" i]',
                'input[placeholder*="account" i]',
                'input[placeholder*="phone" i]',
                'input[type="text"]',
            ]:
                try:
                    if await page.locator(sel).count() > 0:
                        return sel
                except Exception:
                    continue
            return None

        async def find_next_btn():
            for label in ["Next", "Submit", "Check", "Go", "Search", "Proceed"]:
                btn = page.locator(f'button:has-text("{label}")')
                if await btn.count() > 0:
                    try:
                        if await btn.first.is_visible(timeout=2000):
                            return btn.first
                    except:
                        pass
            try:
                btn = page.locator('button[type="submit"]')
                if await btn.count() > 0 and await btn.first.is_visible(timeout=2000):
                    return btn.first
            except:
                pass
            return None
        
        async def safe_click_or_enter(btn):
            """Try to click button, fallback to Enter key - faster"""
            if not btn:
                try:
                    await page.keyboard.press("Enter", delay=100)
                    await asyncio.sleep(0.5)
                    return True
                except:
                    return False
            try:
                await asyncio.wait_for(btn.click(timeout=5000), timeout=6)
                await asyncio.sleep(0.5)
                return True
            except:
                try:
                    await page.keyboard.press("Enter", delay=100)
                    await asyncio.sleep(0.5)
                    return True
                except:
                    return False
        
        async def wait_for_response(timeout_ms=25000):
            """Wait for either valid or invalid result with timeout"""
            valid_loc   = page.locator("#amountPaid")
            invalid_loc = page.locator("text=Invalid account number")
            try:
                await aexpect(
                    valid_loc.or_(invalid_loc)
                ).to_be_visible(timeout=timeout_ms)
                return True
            except:
                return False

        # Initial load
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
        consecutive_errors = 0

        for idx, number in enumerate(numbers):
            status_cb(f"⏳ ({idx+1}/{total}) Checking: **{number}**")
            log_cb(f"({idx+1}/{total}) → {number}")
            status, bill = "Error", ""

            try:
                # Check if input still exists, if not try to find it without full reload
                if await page.locator(active_sel).count() == 0:
                    log_cb("  ⚠️ Input not found, recovering…")
                    active_sel = await find_input()
                    if not active_sel:
                        log_cb("  🔄 Full reload needed…")
                        await reload()
                        active_sel = await find_input() or active_sel

                # Fill number quickly
                inp = page.locator(active_sel).first
                await inp.click(timeout=3000)
                await inp.fill(number, timeout=3000)
                await asyncio.sleep(0.3)

                # Try to submit (Enter key is faster than clicking)
                btn = await find_next_btn()
                click_success = await safe_click_or_enter(btn)
                
                if not click_success:
                    log_cb(f"  ⚠️ Submit failed, retrying…")
                    await asyncio.sleep(0.5)
                    click_success = await safe_click_or_enter(btn)

                if not click_success:
                    raise Exception("Failed to submit form")

                # Wait for response with adaptive timeout
                timeout_val = 25000
                if consecutive_errors > 2:
                    timeout_val = 15000  # Faster timeout if having issues
                
                resp_ok = await wait_for_response(timeout_val)
                if not resp_ok:
                    raise Exception("No response from server")

                # Check result
                valid_loc   = page.locator("#amountPaid")
                invalid_loc = page.locator("text=Invalid account number")

                if await valid_loc.is_visible(timeout=1000):
                    try:
                        bill   = await valid_loc.input_value(timeout=1000)
                    except:
                        bill = "N/A"
                    status = "Valid"
                    log_cb(f"  ✅ Valid — Bill: {bill}")
                    consecutive_errors = 0
                elif await invalid_loc.is_visible(timeout=1000):
                    status = "Invalid"
                    log_cb(f"  ❌ Invalid")
                    consecutive_errors = 0
                else:
                    status = "Unknown"
                    log_cb(f"  ❓ Unknown response")
                    consecutive_errors += 1

                # Go back for next number - use Back button if available
                back_btn = page.locator('button:has-text("Back")')
                back_found = False
                try:
                    if await back_btn.count() > 0 and await back_btn.first.is_visible(timeout=2000):
                        await back_btn.first.click(timeout=5000)
                        await asyncio.sleep(0.5)
                        # Verify we're back at input form
                        if await page.locator(active_sel).count() > 0:
                            back_found = True
                except:
                    pass
                
                if not back_found:
                    # Reload only if Back didn't work
                    log_cb("  🔄 Reloading for next check…")
                    await reload()
                    active_sel = await find_input() or active_sel

            except Exception as e:
                status = "Error"
                bill   = str(e)[:100]
                log_cb(f"  ⚠️ Error: {str(e)[:70]}")
                consecutive_errors += 1
                
                # Smart recovery
                if consecutive_errors > 3:
                    log_cb("  🔄 Multiple errors, reloading…")
                    try:
                        await reload()
                        active_sel = await find_input() or active_sel
                        consecutive_errors = 0
                    except Exception as reload_err:
                        log_cb(f"  ❌ Reload failed: {str(reload_err)[:50]}")

            results.append({"number": number, "status": status, "bill": bill})
            progress_cb((idx + 1) / total)
            
            # Adaptive delay: shorter if successful, slightly longer if errors
            sleep_time = (delay_ms / 1000) * (1 + 0.3 * min(consecutive_errors, 2))
            await asyncio.sleep(sleep_time)

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
                "Delay between requests (ms)", 500, 3000, 800, step=100,
                help="Lower = Faster (but more errors on slow connections). Increase if site throttles."
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
