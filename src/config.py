import os
from pathlib import Path

# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"


def get_python_exe() -> str:
    """Return absolute path to virtual environment python executable (macOS + Windows)."""
    import sys

    win_py = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    nix_py = BASE_DIR / ".venv" / "bin" / "python"
    if win_py.exists():
        return str(win_py)
    if nix_py.exists():
        return str(nix_py)
    return sys.executable


# Gmail Credentials
GMAIL_EMAIL = os.getenv("GMAIL_EMAIL", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

# TradingView Email Settings
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "noreply@tradingview.com")

# Google Sheets Settings
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "TradingView Alerts Tracker")
GOOGLE_TRADES_SHEET_NAME = os.getenv("GOOGLE_TRADES_SHEET_NAME", "TradingView Trades Tracker")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")  # Can be empty, will fallback to name search
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", str(BASE_DIR / "service_account.json"))

# Script Mode Settings
LOOP_MODE = os.getenv("LOOP_MODE", "false").lower() in ("true", "1", "yes")
POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL", "60"))

# LLM Configuration
MIN_CONVICTON_FOR_DEEP_RESEARCH = int(os.getenv("MIN_CONVICTON_FOR_DEEP_RESEARCH", "5"))
# Reversion-mode candidates are conviction-gated on the Rev Zone score itself
# (Z1 threshold = 7, matching revanth-screener.pine's own "Rev Zone Long >= 7"
# convention), NOT the Buy/Sell trend score — see MIN_CONVICTON_FOR_DEEP_RESEARCH.
MIN_REV_ZONE_FOR_DEEP_RESEARCH = float(os.getenv("MIN_REV_ZONE_FOR_DEEP_RESEARCH", "7"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "8192"))
# LLM_LOCAL_CONCURRENCY: how many concurrent requests the local llama-server can
# serve. MUST match the server's --parallel flag (start_llm_server.bat, the
# watchdog, and the orchestrator all launch --parallel 4 with -c 32768 ->
# 8192 tokens/slot). The enrichment pool (the only stage that hits the local LLM)
# is capped at this so we never oversubscribe the GPU server. The triage prompt is
# ~6218 tokens, so per-slot context MUST exceed that (8192/slot) or llama-server
# returns HTTP 400 and the ticker falls back to deterministic with no LLM screen.
# Raise --parallel AND -c AND this together if VRAM headroom allows; if you drop
# to --parallel 3 you MUST also drop -c to ~24000 (8000/slot) and set this to 3.
LLM_LOCAL_CONCURRENCY = int(os.getenv("LLM_LOCAL_CONCURRENCY", "4"))
# LLM_TRIAGE_MAX_TOKENS: output budget for attempt 1 of the local triage ladder
# (attempts 2/3 use 2x/4x of this). Small by design — the triage JSON is tiny.
LLM_TRIAGE_MAX_TOKENS = int(os.getenv("LLM_TRIAGE_MAX_TOKENS", "2048"))

# Deep-research selection / enrichment tuning.
# DEEP_RESEARCH_CAP: how many flagged tickers are selected for deep research.
# Applied TWICE (consistently) — once at segregation (run_local_research.py, so
# the _DEEP_RESEARCH folder holds only the top-N) and once at the paid pass
# (deep_research.py, before calling Minimax). Both use the same
# deep_research_sort_key ranking, so the folder and the paid run agree.
DEEP_RESEARCH_CAP = int(os.getenv("DEEP_RESEARCH_CAP", "0"))
# ENRICH_TOP_N: optional cap on how many send-eligible tickers receive the local
# Qwen enrichment. 0 (the default) = NO CAP, enrich every eligible name.
# Uncapped is the right default because nothing in that pass costs money or burns a
# hard quota: Qwen is local, Finnhub news is 60/min, earnings is yfinance. The two
# quota-bound clients (Alpha Vantage 25/day, Adanos monthly) were already moved to
# the paid pass in deep_research.py. The only cost of enriching all ~127 survivors
# is wall-clock at LLM_LOCAL_CONCURRENCY, and a local read on a name the
# deterministic rank would have buried is exactly where an unexpected candidate
# surfaces. Set a positive value to restore a cap if the GPU box is the bottleneck.
ENRICH_TOP_N = int(os.getenv("ENRICH_TOP_N", "0"))
# TIER_A_MIN_EV_R: minimum expected-value ratio (ev_r) a ticker must clear to be
# eligible for deep research, even if it is a deterministic PASS. Prevents
# thin-EV names (e.g. ev_r 0.11) from crowding out high-EV names (e.g. 3.04).
TIER_A_MIN_EV_R = float(os.getenv("TIER_A_MIN_EV_R", "0.5"))
# WATCH_MIN_CONVICTION: a deterministic WATCH ticker (not in entry zone yet) is
# still eligible for deep research when its conviction meets this bar. Deep
# research (the paid pass) is the right tool to assess near-zone, high-quality,
# not-yet-triggered setups — so requiring a literal in-zone PASS would starve it
# of candidates most days. CUT / bad_data never qualify. Stored on the SAME 0-10
# scale as det_conv / MIN_CONVICTION_FOR_DEEP_RESEARCH (so 7.0 == conviction 70).
WATCH_MIN_CONVICTION = float(os.getenv("WATCH_MIN_CONVICTION", "7.0"))

# NVIDIA free model (used for the remote rescue attempt in local research).
# The user provisions a working free NVIDIA model via NVDIA_FREE_MODEL in .env;
# falls back to the project's documented default if unset.
NVDIA_FREE_MODEL = os.getenv("NVDIA_FREE_MODEL", "z-ai/glm-5.2")

# Output-token budget for the REMOTE rescue attempt (NVIDIA GLM / OpenRouter).
# Remote models don't hit the local thinking-truncation bug, so we allow a large
# budget (default 120k) so long triage prompts get a full, untruncated answer.
LLM_MAX_TOKENS_REMOTE = int(os.getenv("LLM_MAX_TOKENS_REMOTE", "120000"))

# Live position monitoring (data/positions.json state + per-ticker poll threads)
POSITION_POLL_INTERVAL = int(os.getenv("POSITION_POLL_INTERVAL", "60"))

# Adanos Market Sentiment API (free tier: 250 req/month). Provides news + Reddit
# retail sentiment/buzz per ticker. Key goes in .env as ADANOS_SOCIAL_SENTIMENT_API_KEY.
ADANOS_API_KEY = os.getenv("ADANOS_SOCIAL_SENTIMENT_API_KEY", "")
ADANOS_BASE_URL = os.getenv("ADANOS_BASE_URL", "https://api.adanos.org")

# Market Hours Settings (Mountain Time)
MARKET_OPEN_HOUR = int(os.getenv("MARKET_OPEN_HOUR", "7"))
MARKET_OPEN_MINUTE = int(os.getenv("MARKET_OPEN_MINUTE", "15"))
MARKET_CLOSE_HOUR = int(os.getenv("MARKET_CLOSE_HOUR", "20"))  # 8 PM MT
MARKET_CLOSE_MINUTE = int(os.getenv("MARKET_CLOSE_MINUTE", "0"))


def validate_config():
    """Validates that the essential config variables are set."""
    missing = []
    if not GMAIL_EMAIL:
        missing.append("GMAIL_EMAIL")
    if not GMAIL_APP_PASSWORD:
        missing.append("GMAIL_APP_PASSWORD")

    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        # We don't fail immediately, but warn the user or provide a setup guide.
        pass

    if missing:
        raise ValueError(f"Missing required environment variables in .env: {', '.join(missing)}")
