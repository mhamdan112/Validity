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

async def check_single_number(number, context):
    """Check ONE number - simple, fast, reliable"""
    from playwright.async_api import expect as aexpect
    
    TARGET = "https://eand.ae/ecare/c/quick-pay"
    page = None
    
    try:
        # Fresh page only
        page = await asyncio.wait_for(context.new_page(), timeout=10)
        
        await page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>false});"
        )
        
        # Load page - ONE attempt only, fail fast
        try:
            await asyncio.wait_for(
                page.goto(TARGET, wait_until="domcontentloaded"),
                timeout=20
            )
        except:
            return "Error", "Page load failed"
        
        # Wait for page to settle
        try:
            await asyncio.wait_for(
                page.wait_for_load_state("networkidle"),
                timeout=15
            )
        except:
            pass  # Continue anyway
        
        await asyncio.sleep(0.3)
        
        # Find input - quick scan
        input_sel = None
        for sel in ['input[type="tel"]', 'input[placeholder*="number" i]', 
                    'input[placeholder*="account" i]', 'input[placeholder*="phone" i]',
                    'input[type="text"]']:
            try:
                if await page.locator(sel).count() > 0:
                    input_sel = sel
                    break
            except:
                pass
        
        if not input_sel:
            return "Error", "Input not found"
        
        # Fill number
        try:
            inp = page.locator(input_sel).first
            await inp.click(timeout=3)
            await asyncio.sleep(0.2)
            await inp.fill(number, timeout=3)
            await asyncio.sleep(0.3)
        except:
            return "Error", "Fill failed"
        
        # Submit - try multiple ways but fast
        submitted = False
        
        # Try button click
        for btn_text in ["Next", "Submit", "Check"]:
            try:
                btn = page.locator(f'button:has-text("{btn_text}")').first
                if await btn.count() > 0:
                    await btn.click(timeout=3)
                    submitted = True
                    break
            except:
                pass
        
        # Try generic submit button
        if not submitted:
            try:
                btn = page.locator('button[type="submit"]').first
                if await btn.count() > 0:
                    await btn.click(timeout=3)
                    submitted = True
            except:
                pass
        
        # Try Enter key
        if not submitted:
            try:
                await page.keyboard.press("Enter", delay=50)
                submitted = True
            except:
                pass
        
        if not submitted:
            return "Error", "Submit failed"
        
        await asyncio.sleep(0.8)
        
        # Wait for result - STRICT TIMEOUT
        valid_loc = page.locator("#amountPaid")
        invalid_loc = page.locator("text=Invalid account number")
        
        try:
            await asyncio.wait_for(
                aexpect(valid_loc.or_(invalid_loc)).to_be_visible(),
                timeout=12  # Strict 12s timeout
            )
        except asyncio.TimeoutError:
            return "Error", "Server timeout"
        except:
            return "Error", "Response failed"
        
        await asyncio.sleep(0.2)
        
        # Check result
        try:
            if await valid_loc.is_visible(timeout=1):
                try:
                    bill = await valid_loc.input_value(timeout=1)
                except:
                    bill = "N/A"
                return "Valid", bill
        except:
            pass
        
        try:
            if await invalid_loc.is_visible(timeout=1):
                return "Invalid", ""
        except:
            pass
        
        return "Unknown", ""
        
    except asyncio.TimeoutError:
        return "Error", "Operation timeout"
    except Exception as e:
        return "Error", str(e)[:50]
    finally:
        if page:
            try:
                await page.close()
            except:
                pass


async def check_numbers_async(numbers, delay_ms, progress_cb, status_cb, log_cb):
    """Main checking - fresh context every 10 numbers, NO endless retries"""
    from playwright.async_api import async_playwright
    
    results = []
    total = len(numbers)
    
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
        
        context = None
        batch_count = 0
        
        for idx, number in enumerate(numbers):
            # Create fresh context every 10 numbers to prevent corruption
            if batch_count == 0:
                if context:
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
                log_cb(f"🔄 Fresh context #{(idx // 10) + 1}")
            
            batch_count += 1
            if batch_count >= 10:
                batch_count = 0
            
            status_cb(f"⏳ ({idx+1}/{total}) Checking: **{number}**")
            log_cb(f"({idx+1}/{total}) → {number}")
            
            # Standard delay
            await asyncio.sleep(delay_ms / 1000)
            
            try:
                # Simple, fast check - NO retries
                status, bill = await check_single_number(number, context)
                
                if status == "Valid":
                    log_cb(f"  ✅ Valid — Bill: {bill}")
                elif status == "Invalid":
                    log_cb(f"  ❌ Invalid")
                elif status == "Unknown":
                    log_cb(f"  ❓ Unknown")
                else:
                    log_cb(f"  ⚠️ {bill}")
                
                results.append({"number": number, "status": status, "bill": bill})
                
            except Exception as e:
                status = "Error"
                bill = str(e)[:80]
                log_cb(f"  ❌ Error: {str(e)[:60]}")
                results.append({"number": number, "status": status, "bill": bill})
            
            progress_cb((idx + 1) / total)
        
        if context:
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
                "Delay between requests (ms)", 1500, 4000, 2000, step=100,
                help="Default: 2000ms. Increase if website is slow or rate-limiting."
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
