"""
Landline Validity Checker - Bulletproof Version
One fresh browser per number = zero session corruption = 100% reliable
Speed is NOT the goal. Accuracy is.
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


async def check_one_number(number: str, log_cb) -> tuple[str, str]:
    """
    Launches a completely fresh browser, checks ONE number, closes browser.
    No shared state. No session corruption. Ever.
    Retries up to 3 times before giving up.
    """
    from playwright.async_api import async_playwright, expect as aexpect

    TARGET = "https://eand.ae/ecare/c/quick-pay"

    for attempt in range(1, 4):  # 3 attempts max
        browser = None
        try:
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
                    viewport={"width": 1280, "height": 800},
                    locale="en-US",
                    timezone_id="Asia/Dubai",
                )
                page = await context.new_page()
                await page.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>false});"
                )

                # ── Step 1: Load page ─────────────────────────────────────────
                try:
                    await page.goto(TARGET, timeout=45000, wait_until="domcontentloaded")
                    await page.wait_for_load_state("networkidle", timeout=20000)
                except Exception as e:
                    log_cb(f"    Attempt {attempt}: page load failed — {str(e)[:50]}")
                    await browser.close()
                    await asyncio.sleep(3 * attempt)
                    continue

                await asyncio.sleep(1)  # let JS settle

                # ── Step 2: Find input ────────────────────────────────────────
                input_sel = None
                for sel in [
                    'input[type="tel"]',
                    'input[placeholder*="number" i]',
                    'input[placeholder*="account" i]',
                    'input[placeholder*="phone" i]',
                    'input[placeholder*="landline" i]',
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
                    log_cb(f"    Attempt {attempt}: input not found on page")
                    await browser.close()
                    await asyncio.sleep(3 * attempt)
                    continue

                # ── Step 3: Fill number ───────────────────────────────────────
                try:
                    await page.fill(input_sel, "")
                    await asyncio.sleep(0.3)
                    await page.fill(input_sel, number)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    log_cb(f"    Attempt {attempt}: fill failed — {str(e)[:50]}")
                    await browser.close()
                    await asyncio.sleep(3 * attempt)
                    continue

                # ── Step 4: Click Next ────────────────────────────────────────
                clicked = False
                for label in ["Next", "Submit", "Check", "Go", "Search"]:
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
                    try:
                        await page.keyboard.press("Enter")
                        clicked = True
                    except Exception:
                        pass

                if not clicked:
                    log_cb(f"    Attempt {attempt}: no button found")
                    await browser.close()
                    await asyncio.sleep(3 * attempt)
                    continue

                # ── Step 5: Wait for result ───────────────────────────────────
                valid_loc   = page.locator("#amountPaid")
                invalid_loc = page.locator("text=Invalid account number")

                try:
                    await aexpect(
                        valid_loc.or_(invalid_loc)
                    ).to_be_visible(timeout=30000)
                except Exception as e:
                    log_cb(f"    Attempt {attempt}: result timeout — {str(e)[:50]}")
                    await browser.close()
                    await asyncio.sleep(3 * attempt)
                    continue

                await asyncio.sleep(0.5)  # let DOM fully update

                # ── Step 6: Read result ───────────────────────────────────────
                try:
                    if await valid_loc.is_visible():
                        bill = await valid_loc.input_value()
                        await browser.close()
                        return "Valid", bill
                except Exception:
                    pass

                try:
                    if await invalid_loc.is_visible():
                        await browser.close()
                        return "Invalid", ""
                except Exception:
                    pass

                # Got neither — retry
                log_cb(f"    Attempt {attempt}: result unclear, retrying…")
                await browser.close()
                await asyncio.sleep(3 * attempt)

        except Exception as e:
            log_cb(f"    Attempt {attempt}: unexpected error — {str(e)[:60]}")
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            await asyncio.sleep(3 * attempt)

    # All 3 attempts failed
    return "Error", "Failed after 3 attempts"


async def check_numbers_async(numbers, delay_ms, progress_cb, status_cb, log_cb):
    total   = len(numbers)
    results = []

    for idx, number in enumerate(numbers):
        status_cb(f"⏳ ({idx+1}/{total}) Checking: **{number}**")
        log_cb(f"({idx+1}/{total}) → {number}")

        status, bill = await check_one_number(number, log_cb)

        if status == "Valid":
            log_cb(f"  ✅ Valid — Bill: {bill}")
        elif status == "Invalid":
            log_cb(f"  ❌ Invalid")
        elif status == "Error":
            log_cb(f"  ⚠️ Error: {bill}")
        else:
            log_cb(f"  ❓ Unknown")

        results.append({"number": number, "status": status, "bill": bill})
        progress_cb((idx + 1) / total)

        # Fixed delay between numbers — gives the server breathing room
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
                "Delay between numbers (ms)", 2000, 8000, 3000, step=500,
                help="Time to wait between each number. Higher = more reliable."
            )

        if st.button("🚀 Start Validity Check", type="primary"):

            progress_bar       = st.progress(0)
            status_placeholder = st.empty()
            log_placeholder    = st.empty()
            log_lines          = []

            def log(msg):
                log_lines.append(msg)
                log_placeholder.code("\n".join(log_lines[-12:]))

            numbers = []
            for n in df["Landline"].astype(str).str.strip():
                numbers.append(n if n.startswith("0") else "0" + n)

            # Estimated time warning
            est_seconds = len(numbers) * (delay_ms / 1000 + 15)
            est_minutes = round(est_seconds / 60, 1)
            st.info(f"⏱️ Estimated time: ~{est_minutes} minutes for {len(numbers)} numbers")

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