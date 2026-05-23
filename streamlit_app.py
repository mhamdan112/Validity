"""
Landline Validity Checker
Python 3.14 Windows fix: explicitly create ProactorEventLoop instead of using
set_event_loop_policy (deprecated) or relying on the default SelectorEventLoop.
"""
import sys
import asyncio

# ── Python 3.14 Windows fix ───────────────────────────────────────────────────
# On Windows, asyncio defaults to SelectorEventLoop which cannot spawn subprocesses.
# Playwright needs subprocess support. We directly instantiate ProactorEventLoop.
# This avoids both the NotImplementedError AND the deprecation warnings.
if sys.platform == "win32":
    _loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(_loop)

import subprocess
import streamlit as st
import pandas as pd
from io import BytesIO

# ── Install Playwright browsers (Streamlit Cloud) ─────────────────────────────
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

        async def reload():
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

        log_cb(f"🌐 Loading {TARGET} …")
        await reload()
        log_cb("✅ Site loaded.")

        active_sel = await find_input()
        if not active_sel:
            log_cb("❌ Could not find phone input on page!")
            await browser.close()
            return [{"number": n, "status": "Error", "bill": "Input not found"} for n in numbers]

        log_cb(f"🔍 Input detected: {active_sel}")
        total = len(numbers)

        for idx, number in enumerate(numbers):
            status_cb(f"⏳ ({idx+1}/{total}) Checking: **{number}**")
            log_cb(f"({idx+1}/{total}) → {number}")
            status, bill = "Error", ""

            try:
                if await page.locator(active_sel).count() == 0:
                    log_cb("⚠️ Input gone, reloading…")
                    await reload()
                    active_sel = await find_input() or active_sel

                await page.fill(active_sel, "")
                await page.fill(active_sel, number)

                # Click Next button
                next_btn = None
                for label in ["Next", "Submit", "Check", "Go"]:
                    btn = page.locator(f'button:has-text("{label}")')
                    if await btn.count() > 0:
                        next_btn = btn.first
                        break
                if next_btn is None:
                    next_btn = page.locator('button[type="submit"]').first
                await next_btn.click()

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
                    try:
                        await page.wait_for_selector(active_sel, timeout=8000)
                    except Exception:
                        await reload()
                else:
                    await reload()

            except Exception as e:
                bill   = str(e)[:120]
                status = "Error"
                log_cb(f"  ⚠️ Error: {str(e)[:80]}")
                try:
                    await reload()
                    active_sel = await find_input() or active_sel
                except Exception:
                    pass

            results.append({"number": number, "status": status, "bill": bill})
            progress_cb((idx + 1) / total)
            await asyncio.sleep(delay_ms / 1000)

        await browser.close()
    return results


def run_check(numbers, delay_ms, progress_cb, status_cb, log_cb):
    """Run the async checker on the correct event loop."""
    if sys.platform == "win32":
        # Re-use the ProactorEventLoop we set at startup
        loop = asyncio.get_event_loop()
        # If it's closed (rerun), make a fresh one
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
            delay_ms = st.slider("Delay between requests (ms)", 1000, 5000, 2000, step=500)

        if st.button("🚀 Start Validity Check", type="primary"):

            progress_bar       = st.progress(0)
            status_placeholder = st.empty()
            log_placeholder    = st.empty()
            log_lines          = []

            def log(msg):
                log_lines.append(msg)
                log_placeholder.code("\n".join(log_lines[-8:]))

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
