# Playwright Rule: Local Availability Only

**CRITICAL DIRECTIVE FROM USER:**
- **Playwright is already available locally:** The Python environment has `playwright` (version 1.61.0) and local browser binaries installed and fully operational.
- **NEVER attempt to download or fetch Playwright binaries/drivers from external CDNs:** Do NOT call external installers, driver downloaders, or subagents that attempt to fetch driver zip archives (such as `playwright-*.zip` from `playwright.azureedge.net`, Akamai, or Verizon CDNs, which return 404).
- **Execution Method:** For all browser automation, chart scraping, UI verification, or testing tasks, execute directly via local Python scripts using the existing `playwright` installation (`from playwright.sync_api import sync_playwright` or `from playwright.async_api import async_playwright`) and local Chrome profiles.
