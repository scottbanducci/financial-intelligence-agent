"""Real Chromium smoke test for FRED horizontal hover scrolling.

Run from the financial_intelligence_agent directory after installing the app
and Playwright Chromium; no API key or remote provider call is needed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.live.catalog_index import get_connection,put_entry,seed
from financial_intelligence_agent.live.providers import Descriptor

PORT=8597
TEST_NAME="REALLYLONG Securities and Federal Reserve Economic Time Series Metadata "*12
TEST_ID="FRED:LONG_SERIES"

def prepare_seed():
    db=get_connection()
    try:
        seed(db)
        put_entry(db,Descriptor(
           TEST_ID,TEST_NAME,"FRED","percent","daily",
           "Test","https://fred.stlouisfed.org/series/LONG_SERIES",
           "FRED_realtime_vintage"))
        db.commit()
    finally:db.close()

def main():
    prepare_seed()
    env={**os.environ,"ANALYST_DISABLE_CATALOG_WARM":"1"}
    server=subprocess.Popen(
       [sys.executable,"-m","streamlit","run","app.py",
        "--server.headless=true",f"--server.port={PORT}",
        "--browser.gatherUsageStats=false"],
       cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
    try:
        health=f"http://127.0.0.1:{PORT}/_stcore/health"
        for _ in range(70):
            if server.poll() is not None:
                raise RuntimeError("Streamlit exited before browser test")
            try:
                with urlopen(health,timeout=1) as response:
                    if response.status==200:break
            except Exception:time.sleep(.3)
        else:raise RuntimeError("Streamlit server did not become ready")
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=["--no-sandbox"])
            try:
                page=browser.new_page(viewport={"width":1240,"height":900})
                page.goto(f"http://127.0.0.1:{PORT}",wait_until="domcontentloaded")
                page.get_by_role("tab",name="FRED",exact=True).click(timeout=30000)
                search=page.get_by_placeholder("e.g. H.8, commercial bank credit, SOFR")
                search.fill("LONG_SERIES")
                # Streamlit commits text_input only on Enter/blur. Wait for
                # that rerun to complete BEFORE opening the transient dropdown.
                search.press("Enter")
                series=page.locator('[data-testid="stSelectbox"]').filter(
                    has=page.get_by_text("Series",exact=True))
                series.locator("input").wait_for(timeout=20000)
                page.wait_for_function("""() => Array.from(document.querySelectorAll(
                    '[data-testid="stSelectbox"]')).some(el =>
                    el.textContent.includes("Series") &&
                    (el.querySelector("input")?.value || "").includes("REALLYLONG"))""",
                    timeout=20000)
                series.locator("input").click()
                option=page.locator('[role="listbox"][aria-label="Series"] [role="option"]').filter(
                    has_text="REALLYLONG").first
                option.wait_for(timeout=15000)
                label=option.locator("[data-item-hl]")
                try:
                    option.hover(timeout=6500)
                except Exception:
                    print("DEBUG options:",
                          page.locator('[role="listbox"] [role="option"]').all_text_contents()[:5])
                    print("DEBUG series inputs:",
                          page.locator('[data-testid="stSelectbox"] input').evaluate_all(
                          "(els) => els.map(el => el.value)"))
                    raise
                initial=label.evaluate("el => ({width:el.clientWidth, scroll:el.scrollWidth})")
                assert initial["scroll"]>initial["width"]+20,initial
                page.wait_for_timeout(2500)
                distance=label.evaluate("el => el.scrollLeft")
                assert distance>15,f"Series dropdown did not scroll on hover: {distance}"
                print("PASS: native Series dropdown option scrolls horizontally on hover",distance)

                page.get_by_text("Metadata for indexed series",exact=True).click()
                viewport=page.locator(".fred-label-viewport").filter(has_text="REALLYLONG").first
                viewport.wait_for(timeout=10000)
                viewport.hover()
                meta_initial=viewport.evaluate("el => ({width:el.clientWidth, scroll:el.scrollWidth})")
                assert meta_initial["scroll"]>meta_initial["width"]+20,meta_initial
                page.wait_for_timeout(3500)
                meta_distance=viewport.evaluate("el => el.scrollLeft")
                assert meta_distance>15,f"Metadata label did not scroll on hover: {meta_distance}"
                print("PASS: metadata label scrolls horizontally on hover",meta_distance)
            finally:browser.close()
    finally:
        server.terminate()
        try:server.wait(timeout=8)
        except subprocess.TimeoutExpired:server.kill();server.wait(timeout=8)

if __name__=="__main__":
    main()
