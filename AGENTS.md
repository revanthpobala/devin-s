# Stock Market Agents

## Always-On Rules

- **Never delete files or folders without explicit permission first.** Always ask for confirmation before any destructive operation, including deletions, overwrites, or irreversible file changes.

---

## Email Alert Ingestor

**Purpose**: Core application logic for TradingView Alert Ingestor. Polls Gmail for alerts and routes them through position state, Sheets logging, and local LLM analysis.

**Entry Point**: `main.py`

**Capabilities**:
- Polls Gmail for TradingView alerts every `POLLING_INTERVAL` seconds during market hours
- Routes alerts through `PositionManager` (entry opens position + monitor thread, exit closes position + stops thread)
- Logs alerts to Google Sheets with full context (price, news, LLM decision)
- Runs local LLM inference (`revanth-0dte.md` rules card) for Intraday alerts only
- Supports Swing (screener candidates via `survivors.json`) and Intraday strategies
- Rehydrates open positions from `data/positions.json` on restart

**Usage**:
```bash
# Continuous loop mode (run by orchestrator)
python main.py --loop

# One-shot processing mode
python main.py --once
```

**One-Click Launch**: `scripts/launchers/start_tracker.bat`

**Config**:
- `POLLING_INTERVAL` — seconds between Gmail polling cycles
- `POSITION_POLL_INTERVAL` — seconds between quote polls per open position (default 60)

---

## Market Orchestrator

**Purpose**: Coordinate all services and manage the overall trading workflow based on market hours.

**Entry Point**: `run_market_orchestrator.py`

**Capabilities**:
- Monitors market hours (7:15 AM - 8:00 PM MT Mon-Fri)
- Starts/stops `llama-cpp-server` on port 8000 (Qwen3.5-9B-Q8_0.gguf, --parallel 4, --reasoning off)
- Starts/stops Email Alert Ingestor (`main.py --loop`)
- Watchdog: restarts wedged LLM server if `/health` becomes unresponsive
- Detects externally-managed LLM server on port 8000 (uses it instead of starting local)
- Frees all GPU/system resources outside market hours

**Usage**:
```bash
python run_market_orchestrator.py
```

**Workflow**:
- Market Open: Starts LLM server + Email Alert Ingestor
- Market Close / Weekends: Terminates all services

---

## Swing Research Agent

**Purpose**: Scrape TradingView charts and generate survivor lists for swing trade candidates.

**Entry Point**: `run_swing_research.py`

**Capabilities**:
- Deterministic Cascade (`deterministic_cascade.py`) → survivor list
- Parallel TradingView chart scraping via Chrome (`src/logic/process_survivor.py:scrape_survivor_task`)
- Writes `data/raw/<date>/survivors.json` with ticker, source, and sheet row index
- Supports single-ticker override (`--ticker AAPL`)

**Usage**:
```bash
# Scrape phase for all survivors
python run_swing_research.py 2026-07-24

# Scrape phase for specific ticker
python run_swing_research.py --ticker AAPL
```

---

## Local Research Agent

**Purpose**: Local-LLM triage, news research, and thesis generation for swing trade candidates.

**Entry Point**: `run_local_research.py`

**Capabilities**:
- Phase 2C-1: Deterministic prefilter (cheap, no LLM) — ranks all survivors, selects top-N
- Phase 2C-2: Qwen enrichment (expensive, top-N only) — local LLM + Finnhub + Alpha Vantage + Adanos
- Batch Google Sheets update for all ticker decisions
- Phase 2E: Segregate tickers into `data/triage/<date>/_DEEP_RESEARCH` (send_for_deep_research=True) or `force/` (manual override)
- Rebuilds `consolidated_results.json` ledger from per-ticker `_thesis.json` files

**Usage**:
```bash
# Local research for all survivors (after scrape phase)
python run_local_research.py 2026-07-24

# Local research for specific ticker
python run_local_research.py --ticker AAPL

# Force a ticker into deep research regardless of LLM gate
python run_local_research.py --ticker AAPL --force AAPL

# Regenerate all theses (ignore cache)
python run_local_research.py --regenerate
```

---

## Deep Research Agent

**Purpose**: Paid Minimax-powered deep research on tickers flagged by local triage.

**Entry Point**: `src/logic/deep_research.py`

**Capabilities**:
- Processes tickers in `data/triage/<date>/_DEEP_RESEARCH/` (capped at `DEEP_RESEARCH_CAP`, default 8)
- Generates enriched thesis with Minimax LLM
- Writes `_thesis.json` per ticker
- Social sentiment analysis via Google Grounding
- Re-ranks candidates using full triage record (contradiction-aware)

**Usage**:
```bash
# Run deep research on today's triage folder
python src/logic/deep_research.py 2026-07-24
```

---

## Position State & Monitor

**Purpose**: Single source of truth for OPEN positions, with a live polling loop that re-evaluates each open trade against live quotes.

**Entry Point**: `main.py` (started by `run_market_orchestrator.py`)

**Capabilities**:
- `data/positions.json` is the authoritative record (ticker, side, entry, stop, target, last eval)
- ENTRY alert → opens a position + spawns a per-ticker monitor thread
- EXIT alert (TradingView) → closes the position + stops the thread (LLM never authorizes exits)
- Each monitor polls live quotes every `POSITION_POLL_INTERVAL` sec, hard-checks stop/target deterministically
- On tracker restart, monitors are rehydrated from `data/positions.json`

**Files**:
- `src/tracking/position_state.py` — atomic load/save/upsert/close of `data/positions.json`
- `src/tracking/position_monitor.py` — `PositionManager` (queue router) + `PositionMonitor` (per-ticker thread)

**Config**:
- `POSITION_POLL_INTERVAL` (default 60) — seconds between quote polls per open position

---

## LLM Server

**Purpose**: Run local GPU-accelerated LLM inference for trade analysis and triage.

**Binary**: `llama-cpp-server` (llama-server.exe on Windows)

**Configuration**:
- Model: Qwen3.5-9B-Q8_0.gguf
- Port: 8000
- GPU: Automatic CUDA detection (`--ngl 999`)
- API: llama-server (not FastAPI) with `/health` endpoint
- `--parallel 4` / `-c 32768` → 8192 tokens/slot
- `--reasoning off` — no thinking trace, clean JSON output only

**Lifecycle**: Managed by `run_market_orchestrator.py` (auto-start/stop/healthcheck)

---

## LLM Clients

**Purpose**: Python wrapper clients for LLM inference across different backends.

**Files**: `src/clients/llm_client.py`

**Capabilities**:
- `query_local_llm()` — llama-cpp-server inference
- `query_gemini()` — Google Gemini API
- Supports system prompt + user prompt pattern
- Handles markdown code fence stripping from model output

---

## Data Clients

**Purpose**: External data source integrations.

**Files**:
- `src/clients/gmail_client.py` — Gmail IMAP (TradingView alert emails)
- `src/clients/price_client.py` — Real-time price quotes (multiple exchanges)
- `src/clients/news_client.py` — Alpaca/Finnhub news headlines
- `src/clients/search_client.py` — Web search via DuckDuckGo / Brave
- `src/clients/google_grounding_client.py` — Google Grounding (Gemini + web search)
- `src/clients/finnhub_client.py` — Finnhub API (macro, earnings)
- `src/clients/alphavantage_client.py` — Alpha Vantage (technical data)
- `src/clients/adanos_client.py` — Adanos API (social sentiment)
- `src/clients/earnings_client.py` — Earnings data
- `src/clients/macro_client.py` — Macro context builder
- `src/clients/options_client.py` — Options chain data

---

## Data Research Agent

**Purpose**: Fetch and analyze market data from TradingView, Alpaca, and other sources.

**Capabilities**:
- Scrape TradingView charts and data via Chrome browser automation (`src/data/tv_scraper.py`)
- Retrieve historical price data via `src/clients/price_client.py`
- Analyze technical indicators and market conditions
- Consolidate data windows for research
- Fetch real-time news from Alpaca API for sentiment analysis
- Build macro context for market analysis

**Commands**:
```bash
# Run daily TradingView chart scraping
python src/data/tv_scraper.py

# Backfill news data for historical dates
python src/data/backfill_news.py --date 2026-07-09

# Import all historical TradingView alerts from Gmail
python src/data/import_all_history.py
```

---

## Data Processing Agent

**Purpose**: Process and clean market data, generate survivor lists and analysis reports.

**Capabilities**:
- Clean and normalize market data from various sources
- Generate survivor lists of active stocks/tickers
- Filter and aggregate data windows
- Process alerts through deterministic cascade logic
- Create analysis reports and finetune datasets for LLMs
- Parse TradingView alert emails and extract structured data

**Commands**:
```bash
# Process survivor data
python src/logic/process_survivor.py

# Run deep research analysis
python src/logic/deep_research.py

# Execute deterministic cascade analysis
python src/logic/deterministic_cascade.py
```

---

## Attribution Agent

**Purpose**: Build attribution datasets and analyze trade performance.

**Entry Point**: `scripts/attribution/`

**Commands**:
```bash
# Build attribution dataset
python scripts/attribution/build_dataset.py

# Run attribution analysis
python scripts/attribution/attribution.py
```

---

## Model Setup Agent

**Purpose**: Download and set up LLM models for local inference.

**Entry Point**: `scripts/llm_setup/`

**Capabilities**:
- Download GGUF models (Llama, DeepSeek, etc.)
- Generate finetune datasets for custom models
- Set up Unsloth for 4-bit quantization

**Commands**:
```bash
# Download any model by name
python scripts/llm_setup/download_model.py --model-name "model-name"

# Generate finetune dataset
python scripts/llm_setup/generate_finetune_data.py
```

---

## Utility Scripts

### Email Inspector
```bash
python scripts/mail/inspect_emails.py
```
Inspect raw TradingView alert emails in Gmail.

### Test LLM Call
```bash
python scripts/llm_setup/test_llm_call.py
```
Test local LLM connectivity and inference.

### Fix Imports
```bash
python scripts/dev/update_imports.py
```
Automatically fix Python import paths in scripts.

### Regenerate News
```bash
python scripts/research/regenerate_news.py
```
Regenerate news data for existing alerts.

### LLM Watchdog
```bash
python scripts/launchers/llm_watchdog.py
```
Monitor LLM server health and auto-restart if wedged.
