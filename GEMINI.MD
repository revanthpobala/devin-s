# Gemini Behavioral Rules

**CRITICAL DIRECTIVES FROM USER:**
1. You are strictly FORBIDDEN to delete any files without explicitly asking for permission and receiving confirmation from the user first. Always maintain files so that work can be recovered. Do not wipe or purge directories autonomously.
2. **Playwright is already available locally:** NEVER attempt to fetch, download, or install Playwright binaries, drivers, or browser packages from external CDNs (e.g. Azure, Akamai, or Verizon CDN URLs like `playwright-1.57.0-win32_x64.zip` that return 404). Playwright is pre-installed locally (`playwright 1.61.0`). Always use the local Playwright installation and local Chrome/Chromium binaries directly via Python scripts.
