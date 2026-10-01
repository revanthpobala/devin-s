import asyncio
import os
import sys
from playwright.async_api import async_playwright

async def main():
    artifact_dir = "C:/Users/Revanth/.gemini/antigravity-ide/brain/dea65433-e697-46b0-a65d-efc33d1c48cd"
    os.makedirs(artifact_dir, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 960})

        print("Navigating to http://127.0.0.1:8055/research/CAT...")
        await page.goto("http://127.0.0.1:8055/research/CAT", wait_until="networkidle")
        await page.wait_for_timeout(3000)

        # Check ticker title
        sym = await page.inner_text("#hdr-ticker-sym")
        print(f"Header Ticker: {sym}")

        company = await page.inner_text("#hdr-company-name")
        print(f"Company: {company}")

        verdict = await page.inner_text("#hdr-verdict-pill")
        print(f"Verdict: {verdict}")

        shot1 = f"{artifact_dir}/research_cat_main.png"
        await page.screenshot(path=shot1, full_page=False)
        print(f"Saved screenshot: {shot1}")

        # Click on Trade Plan tab
        plan_btn = await page.query_selector("#tab-plan-btn")
        if plan_btn:
            print("Clicking Trade Plan tab...")
            await plan_btn.click()
            await page.wait_for_timeout(1000)
            shot2 = f"{artifact_dir}/research_cat_plan.png"
            await page.screenshot(path=shot2, full_page=False)
            print(f"Saved screenshot: {shot2}")

        # Click on Scraped Charts tab
        chart_btn = await page.query_selector("#tab-chart-btn")
        if chart_btn:
            print("Clicking Scraped Charts tab...")
            await chart_btn.click()
            await page.wait_for_timeout(1000)
            shot3 = f"{artifact_dir}/research_cat_charts.png"
            await page.screenshot(path=shot3, full_page=False)
            print(f"Saved screenshot: {shot3}")

        # Now test navigating to an unresearched constituent (e.g. ZTS)
        print("Navigating to http://127.0.0.1:8055/research/ZTS...")
        await page.goto("http://127.0.0.1:8055/research/ZTS", wait_until="networkidle")
        await page.wait_for_timeout(2500)
        shot4 = f"{artifact_dir}/research_zts.png"
        await page.screenshot(path=shot4, full_page=False)
        print(f"Saved screenshot: {shot4}")

        await browser.close()
        print("Playwright test complete!")

if __name__ == "__main__":
    asyncio.run(main())
