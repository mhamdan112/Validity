"""
Landline Validity Checker - Fresh Page Per Number (Stable & Reliable)
Best approach: Reload page for each check to avoid state degradation
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

        total = len(numbers)

        for idx, number in enumerate(numbers):
            status_cb(f"⏳ ({idx+1}/{total}) Checking: **{number}**")
            log_cb(f"({idx+1}/{total}) → {number}")
            status, bill = "Error", ""

            page = None
            try:
                # Fresh page for each number - guarantees clean state
                page = await context.new_page()
                await page.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>false});"
                )

                # Load page
                await page.goto(TARGET, timeout=60000, wait_until="domcontentloaded")
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except:
                    pass
                await asyncio.sleep(0.2)

                # Find input field
                input_sel = None
                for sel in [
                    'input[type="tel"]',
                    'input[placeholder*="number" i]',
                    'input[placeholder*="account" i]',
                    'input[placeholder*="phone" i]',
                    'input[type="text"]',
                ]:
                    try:
                        if await page.locator(sel).count() > 0:
                            input_sel = sel
                            break
                    except:
                        continue

                if not input_sel:
                    status = "Error"
                    bill = "Input field not found"
                    log_cb(f"  ❌ {bill}")
                    results.append({"number": number, "status": status, "bill": bill})
                    progress_cb((idx + 1) / total)
                    await asyncio.sleep(delay_ms / 1000)
                    if page:
                        await page.close()
                    continue

                # Fill number
                inp_locator = page.locator(input_sel).first
                await inp_locator.click(timeout=3000)
                await inp_locator.fill(number, timeout=3000)
                await asyncio.sleep(0.2)

                # Find and click submit button
                submit_btn = None
                for label in ["Next", "Submit", "Check", "Go", "Search", "Proceed"]:
                    btn = page.locator(f'button:has-text("{label}")').first
                    try:
                        if await btn.count() > 0 and await btn.is_visible(timeout=1000):
                            submit_btn = btn
                            break
                    except:
                        pass

                # If no button found, try generic submit button
                if not submit_btn:
                    btn = page.locator('button[type="submit"]').first
                    try:
                        if await btn.count() > 0:
                            submit_btn = btn
                    except:
                        pass

                # Submit form
                submit_ok = False
                if submit_btn:
                    try:
                        await submit_btn.click(timeout=4000)
                        submit_ok = True
                        await asyncio.sleep(0.5)
                    except:
                        pass

                if not submit_ok:
                    try:
                        await page.keyboard.press("Enter", delay=100)
                        submit_ok = True
                        await asyncio.sleep(0.5)
                    except:
                        pass

                if not submit_ok:
                    status = "Error"
                    bill = "Could not submit form"
                    log_cb(f"  ❌ {bill}")
                    results.append({"number": number, "status": status, "bill": bill})
                    progress_cb((idx + 1) / total)
                    await asyncio.sleep(delay_ms / 1000)
                    if page:
                        await page.close()
                    continue

                # Wait for result
                valid_loc = page.locator("#amountPaid")
                invalid_loc = page.locator("text=Invalid account number")

                try:
                    await asyncio.wait_for(
                        aexpect(valid_loc.or_(invalid_loc)).to_be_visible(),
                        timeout=18
                    )
                except asyncio.TimeoutError:
                    status = "Error"
                    bill = "Response timeout"
                    log_cb(f"  ⚠️ {bill}")
                    results.append({"number": number, "status": status, "bill": bill})
                    progress_cb((idx + 1) / total)
                    await asyncio.sleep(delay_ms / 1000)
                    if page:
                        await page.close()
                    continue
                except Exception as e:
                    status = "Error"
                    bill = str(e)[:80]
                    log_cb(f"  ⚠️ Error: {bill[:50]}")
                    results.append({"number": number, "status": status, "bill": bill})
                    progress_cb((idx + 1) / total)
                    await asyncio.sleep(delay_ms / 1000)
                    if page:
                        await page.close()
                    continue

                # Check which result is visible
                try:
                    if await valid_loc.is_visible(timeout=1000):
                        try:
                            bill = await valid_loc.input_value(timeout=1000)
                        except:
                            bill = "N/A"
                        status = "Valid"
                        log_cb(f"  ✅ Valid — Bill: {bill}")
                except:
                    pass

                if status == "Error":
                    try:
                        if await invalid_loc.is_visible(timeout=1000):
                            status = "Invalid"
                            log_cb(f"  ❌ Invalid")
                    except:
                        pass

                if status == "Error":
                    status = "Unknown"
                    log_cb(f"  ❓ Unknown response")

            except Exception as e:
                status = "Error"
                bill = str(e)[:100]
                log_cb(f"  ⚠️ Unexpected error: {str(e)[:60]}")

            results.append({"number": number, "status": status, "bill": bill})
            progress_cb((idx + 1) / total)
            await asyncio.sleep(delay_ms / 1000)

            # Close page after each check
            if page:
                try:
                    await page.close()
                except:
                    pass

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
                "Delay between requests (ms)", 500, 2000, 1000, step=100,
                help="Lower = Faster. Increase if you get rate-limit errors from the website."
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
