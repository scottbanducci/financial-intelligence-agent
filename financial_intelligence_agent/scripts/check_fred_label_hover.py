"""Real Chromium smoke test for application-wide dataset horizontal hover scrolling.

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
        put_entry(db,Descriptor(
           "NYFED_PD:LONG_PD","REALLYLONG Primary Dealer Statistics net financing and positions "*12,
           "NYFED_PD","Millions of USD","weekly","Primary Dealer Statistics",
           "https://markets.newyorkfed.org/api/pd/get/LONG_PD.json"))
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
                if distance<=15:
                    print("DEBUG scroll JS globals:",page.evaluate("""() => ({
                        labelsInstalled:window.__datasetLongLabelScrollInstalled,
                        yInstalled:window.__analystVisibleYAutoscaleInstalled,
                        scripts:Array.from(document.querySelectorAll('script')).length,
                        overflow:Array.from(document.querySelectorAll('[role="listbox"] [data-item-hl]'))
                          .map(el=>({c:el.clientWidth,s:el.scrollWidth,left:el.scrollLeft}))
                    })"""),flush=True)
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
                # Same hover functionality is installed globally, so a
                # NY Fed dropdown can reveal long dealer descriptions too.
                page.get_by_role("tab",name="NY Fed",exact=True).click()
                nysearch=page.get_by_placeholder(
                    "repo, reverse repo, agency MBS, liquidity swaps, primary dealer, SOFR...")
                nysearch.fill("LONG_PD")
                nysearch.press("Enter")
                field=page.locator('[data-testid="stSelectbox"]').filter(
                    has=page.get_by_text("Dataset",exact=True))
                field.locator("input").wait_for(timeout=20000)
                page.wait_for_function("""() => Array.from(document.querySelectorAll(
                    '[data-testid="stSelectbox"]')).some(el =>
                    el.textContent.includes("Dataset") &&
                    (el.querySelector("input")?.value || "").includes("REALLYLONG"))""",
                    timeout=20000)
                field.locator("input").click()
                nyoption=page.locator('[role="listbox"] [role="option"]').filter(
                    has_text="REALLYLONG").first
                nyoption.wait_for(timeout=15000)
                nylab=nyoption.locator("[data-item-hl]")
                nyoption.hover()
                page.wait_for_timeout(2500)
                nydist=nylab.evaluate("el => el.scrollLeft")
                assert nydist>15,f"NY Fed dataset options did not scroll: {nydist}"
                print("PASS: NY Fed dataset dropdown hover-scroll",nydist)

                # Search-result inventories use accessible, text-based table
                # cells rather than a canvas grid where hover is opaque.
                page.get_by_text("Data catalog",exact=True).first.click()
                catsearch=page.get_by_role("textbox",name="Search ID, title, description or release")
                catsearch.fill("LONG_SERIES")
                catsearch.press("Enter")
                catalog_cell=page.locator(".dataset-label-viewport").filter(
                    has_text="REALLYLONG").first
                catalog_cell.wait_for(timeout=15000)
                # The Data Catalog page can rerender after Enter. Hover once
                # the search settles, and rehover if the Streamlit layout
                # changed under the pointer during the initial animation.
                page.wait_for_timeout(900)
                catdist=0
                for _ in range(3):
                    catalog_cell.scroll_into_view_if_needed()
                    catalog_cell.hover()
                    page.wait_for_timeout(1250)
                    catdist=catalog_cell.evaluate("el => el.scrollLeft")
                    if catdist>15:break
                assert catdist>15,f"Data Catalog metadata label did not scroll: {catdist}"
                print("PASS: Data Catalog dataset labels hover-scroll",catdist)
                # Exercise the shared event handler in Data transformations,
                # where options represent workspace datasets rather than
                # FRED/NY Fed publisher catalog records.
                page.get_by_text("Live Data Studio",exact=True).first.click()
                page.get_by_role("tab",name="FFIEC / CSV upload",exact=True).click()
                uploader=page.locator('[data-testid="stFileUploader"] input[type="file"]').first
                uploader.set_input_files({
                    "name":"local-fixture.csv","mimeType":"text/csv",
                    "buffer":b"observation_date,value\n2026-05-01,1000.0\n2026-10-01,1.0\n2026-10-02,1.2\n"
                })
                key="REALLYLONGTRANSFORMDATASET_"+("X"*47)
                series_key=page.get_by_role("textbox",name="Series key")
                series_key.fill(key)
                series_key.press("Enter")
                page.get_by_role("button",name="Validate & add to workspace").click()
                page.get_by_text("Data transformations",exact=True).first.click()
                operand=page.locator('[data-testid="stSelectbox"]').filter(
                    has=page.get_by_text("2 · First dataset",exact=True))
                operand.locator("input").wait_for(timeout=15000)
                operand.locator("input").click()
                transform_option=page.locator('[role="listbox"] [role="option"]').filter(
                    has_text="REALLYLONGTRANSFORMDATASET").first
                transform_option.wait_for(timeout=15000)
                transform_option.hover()
                operand_label=transform_option.locator("[data-item-hl]")
                page.wait_for_timeout(1700)
                dx=operand_label.evaluate("el => el.scrollLeft")
                assert dx>10,f"Transformation operand option did not scroll: {dx}"
                print("PASS: Data transformations operand dropdown hover-scroll",dx)
                gd=page.locator(".js-plotly-plot").last
                gd.wait_for(timeout=15000)
                page.evaluate("""async () => {
                    const plots=[...document.querySelectorAll('.js-plotly-plot')];
                    const gd=plots[plots.length-1];
                    await window.Plotly.relayout(gd,{
                        'xaxis.range':['2026-09-01','2026-10-15'],
                        'yaxis.range':[0,1100]
                    });
                }""")
                print("AUTOSCALE_DEBUG",gd.evaluate("""el => ({
                    plotlyType:typeof window.Plotly,
                    relayoutType:typeof window.Plotly?.relayout,
                    graphX:el._fullLayout.xaxis.range.slice(),
                    typeX:typeof el.data[0]?.x,
                    lenX:el.data[0]?.x?.length,
                    headX:el.data[0]?.x?.[0],
                    headY:el.data[0]?.y?.[0],
                    count:el.data.length,
                    handlerInstalled:window.__analystVisibleYAutoscaleInstalled,
                    calcCount:el.calcdata?.length,
                    calcFirst:(el.calcdata?.[0]||[]).slice(0,3).map(p=>({
                        keys:Object.keys(p).slice(0,18),x:p.x,y:p.y,s:p.s
                    })),
                    encodedY:el.data?.[0]?.y && Object.keys(el.data[0].y).slice(0,10),
                    btn:!!el.querySelector('.modebar-btn[data-title="Autoscale"]')
                })"""),flush=True)
                before=gd.evaluate("el => ({x:el._fullLayout.xaxis.range.slice(), y:el._fullLayout.yaxis.range.slice()})")
                autoscale=gd.locator('.modebar-btn[data-title="Autoscale"]')
                autoscale.click(force=True)
                page.wait_for_timeout(550)
                after=gd.evaluate("el => ({x:el._fullLayout.xaxis.range.slice(), y:el._fullLayout.yaxis.range.slice()})")
                assert after["x"]==before["x"],f"Autoscale reset horizontal zoom: {before} -> {after}"
                assert after["y"][1]<10,f"Autoscale did not fit visible small values: {after}"
                print("PASS: Plotly Autoscale fits Y to visible dates while preserving X zoom",after,flush=True)

            finally:browser.close()
    finally:
        server.terminate()
        try:server.wait(timeout=8)
        except subprocess.TimeoutExpired:server.kill();server.wait(timeout=8)

if __name__=="__main__":
    main()
