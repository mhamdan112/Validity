"""
Landline Validity Checker - Production Grade
Smart retry logic + Context recovery + Exponential backoff
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

async def check_single_number(number, browser, delay_base=1000):
    """Check a single number with automatic retry on failure"""
    from playwright.async_api import expect as aexpect
    
    TARGET = "https://eand.ae/ecare/c/quick-pay"
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        page = None
        try:
            # Fresh page for each attempt
            page = await browser.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>false});"
            )
            
            # Load page with timeout
            await asyncio.wait_for(
                page.goto(TARGET, wait_until="domcontentloaded"),
                timeout=30
            )
            
            # Wait for network (with graceful fallback)
            try:
                await asyncio.wait_for(
                    page.wait_for_load_state("networkidle"),
                    timeout=12
                )
            except asyncio.TimeoutError:
                pass  # Continue anyway
            
            await asyncio.sleep(0.3)
            
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
                    count = await page.locator(sel).count()
                    if count > 0:
                        input_sel = sel
                        break
                except:
                    pass
            
            if not input_sel:
                return "Error", "Input field not found", False
            
            # Click and fill input
            inp = page.locator(input_sel).first
            await inp.click(timeout=3)
            await inp.fill(number, timeout=3)
            await asyncio.sleep(0.15)
            
            # Find and click submit button
            submit_clicked = False
            for btn_text in ["Next", "Submit", "Check", "Go", "Search", "Proceed"]:
                try:
                    btns = page.locator(f'button:has-text("{btn_text}")')
                    count = await btns.count()
                    if count > 0:
                        btn = btns.first
                        try:
                            visible = await btn.is_visible(timeout=1)
                            if visible:
                                await btn.click(timeout=3)
                                submit_clicked = True
                                break
                        except:
                            pass
                except:
                    pass
            
            # Fallback: try keyboard Enter
            if not submit_clicked:
                try:
                    await page.keyboard.press("Enter", delay=50)
                    submit_clicked = True
                except:
                    pass
            
            if not submit_clicked:
                return "Error", "Could not submit", True  # Retry on submit failure
            
            await asyncio.sleep(0.4)
            
            # Wait for response with timeout
            valid_loc = page.locator("#amountPaid")
            invalid_loc = page.locator("text=Invalid account number")
            
            try:
                await asyncio.wait_for(
                    aexpect(valid_loc.or_(invalid_loc)).to_be_visible(),
                    timeout=16
                )
            except asyncio.TimeoutError:
                return "Error", "No response from server", True  # Retry
            except Exception as e:
                return "Error", f"Response error: {str(e)[:40]}", True
            
            # Check result
            bill = ""
            try:
                if await valid_loc.is_visible(timeout=1):
                    try:
                        bill = await valid_loc.input_value(timeout=1)
                    except:
                        bill = "N/A"
                    await page.close()
                    return "Valid", bill, False
            except:
                pass
            
            try:
                if await invalid_loc.is_visible(timeout=1):
                    await page.close()
                    return "Invalid", "", False
            except:
                pass
            
            await page.close()
            return "Unknown", "", False
            
        except asyncio.TimeoutError:
            retry_count += 1
            if page:
                try:
                    await page.close()
                except:
                    pass
            if retry_count < max_retries:
                await asyncio.sleep(0.5)  # Brief pause before retry
            
        except Exception as e:
            retry_count += 1
            if page:
                try:
                    await page.close()
                except:
                    pass
            if retry_count < max_retries:
                await asyncio.sleep(0.5)
    
    return "Error", "Max retries exceeded", False


async def check_numbers_async(numbers, delay_ms, progress_cb, status_cb, log_cb):
    """Main checking function with browser session management"""
    from playwright.async_api import async_playwright
    
    results = []
    total = len(numbers)
    error_streak = 0
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--disable-web-resources",
            ]
        )
        
        # Single context for all checks
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
        
        for idx, number in enumerate(numbers):
            status_cb(f"⏳ ({idx+1}/{total}) Checking: **{number}**")
            log_cb(f"({idx+1}/{total}) → {number}")
            
            # Adaptive delay based on error streak
            if error_streak > 0:
                wait_time = (delay_ms / 1000) * (1 + 0.5 * min(error_streak, 3))
                log_cb(f"  ⏱️ Delay: {wait_time:.1f}s (error recovery)")
                await asyncio.sleep(wait_time)
            else:
                await asyncio.sleep(delay_ms / 1000)
            
            try:
                # Check number with retry logic
                status, bill, should_retry = await check_single_number(
                    number, 
                    context,
                    delay_ms
                )
                
                if status == "Valid":
                    log_cb(f"  ✅ Valid — Bill: {bill}")
                    error_streak = 0
                elif status == "Invalid":
                    log_cb(f"  ❌ Invalid")
                    error_streak = 0
                elif status == "Unknown":
                    log_cb(f"  ❓ Unknown")
                    error_streak = 0
                else:  # Error
                    log_cb(f"  ⚠️ {bill}")
                    error_streak += 1
                    
                    # Context recovery: recreate context after 3 consecutive errors
                    if error_streak >= 3:
                        log_cb(f"  🔄 Recovering context after errors...")
                        try:
                            await context.close()
                        except:
                            pass
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
                        error_streak = 0
                        log_cb(f"  ✅ Context recovered")
                
                results.append({"number": number, "status": status, "bill": bill})
                
            except Exception as e:
                status = "Error"
                bill = str(e)[:100]
                log_cb(f"  ❌ Fatal error: {str(e)[:50]}")
                error_streak += 1
                results.append({"number": number, "status": status, "bill": bill})
                
                # Recover context on fatal error
                if error_streak >= 3:
                    log_cb(f"  🔄 Context recovery...")
                    try:
                        await context.close()
                    except:
                        pass
                    try:
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
                        error_streak = 0
                    except:
                        log_cb(f"  ❌ Context recovery failed")
            
            progress_cb((idx + 1) / total)
        
        try:
            await context.close()
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
                "Delay between requests (ms)", 800, 2500, 1200, step=100,
                help="Recommended: 1200ms. Lower = faster but more errors. Higher = slower but more stable."
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
