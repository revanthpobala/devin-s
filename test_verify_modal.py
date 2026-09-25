import asyncio
import sys
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900})
        await page.goto("http://127.0.0.1:8050/#desk", wait_until="networkidle")
        await page.wait_for_timeout(2500)
        
        # Click on NVDA's 'Local Dossier' button on desk
        nvda_btn = await page.query_selector("button:has-text('Local Dossier')")
        if nvda_btn:
            print("Clicking 'Local Dossier' button...")
            await nvda_btn.click()
            await page.wait_for_timeout(2500)
            
            modal_title = await page.inner_text("#modal-ticker-title")
            sys.stdout.buffer.write(f"Modal Title: {modal_title}\n".encode("utf-8"))
            
            active_tab = await page.query_selector("#modal-report-pane .tab-btn.active")
            active_tab_text = await active_tab.inner_text() if active_tab else "None"
            sys.stdout.buffer.write(f"Active Tab in Modal: {active_tab_text}\n".encode("utf-8"))
            
            modal_body = await page.inner_text("#modal-body")
            body_snippet = modal_body[:350].replace("\n", " -- ")
            sys.stdout.buffer.write(f"Modal Body Snippet: {body_snippet}\n".encode("utf-8"))
            
            shot_path = "C:/Users/Revanth/.gemini/antigravity-ide/brain/c2586413-b2b6-414e-ade3-bce1db32d02c/local_dossier_verified.png"
            await page.screenshot(path=shot_path)
            print("Screenshot saved to artifacts:", shot_path)
        else:
            print("No Local Dossier button found on desk")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
