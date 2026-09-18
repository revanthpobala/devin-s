# Stock Market Agents

## Always-On Rules

- **Never delete files or folders without explicit permission first.** Always ask for confirmation before any destructive operation, including deletions, overwrites, or irreversible file changes.
- **Playwright is already available locally.** Never attempt to download, install, or fetch Playwright binaries or driver archives from external CDNs (e.g. Azure, Akamai, or Verizon CDN 404s). Use the local Playwright installation (`playwright 1.61.0`) and local Chrome/Chromium binaries directly via Python scripts.

---

## Email Alert Ingestor

**Purpose**: Core application logic for TradingView Alert Ingestor. Polls Gmail for alerts and routes them through position state, Sheets logging, and local LLM analysis.

**Entry Point**: `main.py`

**Capabilities**:
- Polls Gmail for TradingView alerts every `POLLING_INTERVAL` seconds during market hours
- Routes alerts through `PositionManager` (entry opens position + monitor thread, exit closes position + stops thread)
- Logs alerts to Google Sheets with full context (price, news, LLM decision)
- Runs local LLM inference (`gems/revanth-0dte.md` rules card) for every incoming alert, with the pre-alert open-position snapshot + live quote/VIX + Alpaca/Finnhub news injected as context
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
- Monitors market hours (7:15 AM - 2:30 PM MT / 9:15 AM - 4:30 PM ET Mon-Fri, covering NYSE/NASDAQ RTH plus pre-market warm-up & settlement)
- Starts/stops `llama-cpp-server` on port 8000 (Qwen3.5-9B-Q8_0.gguf, `-c 32768`, `--parallel 3`, `--reasoning off`)
- Starts/stops Email Alert Ingestor (`main.py --loop`)
- Watchdog: restarts wedged LLM server if `/health` becomes unresponsive
- Detects externally-managed LLM server on port 8000 (uses it instead of starting local)
- Frees all GPU/system resources outside market hours

**Usage**:
```bash
python run_market_orchestrator.py

# One-click (applies sleep prevention first)
scripts\launchers\start_market.bat
```

**Workflow**:
- Market Open: Starts LLM server + Email Alert Ingestor
- Market Close / Weekends: Terminates all services

---

## Swing Research Agent

**Purpose**: Scrape TradingView charts and generate survivor lists for swing trade candidates.

**Entry Point**: `run_swing_research.py`

**Capabilities**:
- Deterministic Cascade (`src/logic/deterministic_cascade.py`) → survivor list from the Trades sheet
- SPX mode: load all S&P 500 constituents from `EveryDay/SPX-constituents.csv` (1-year history) into the SWING-SPX sheet
- Parallel TradingView chart scraping via Playwright + Chrome profiles (`src/data/tv_scraper.py`, `src/logic/process_survivor.py:scrape_survivor_task`)
- Writes `data/raw/<date>/` artifacts per ticker: chart png (naked + 90d zoom), datawindow json/csv, news dossier
- Supports single-ticker override (`--ticker AAPL`) and forced re-scrape (`--force`)

**Usage**:
```bash
# Scrape phase for all survivors
python run_swing_research.py 2026-08-22

# Scrape phase for specific ticker
python run_swing_research.py --ticker AAPL

# All S&P 500 constituents (1-year history -> SWING-SPX sheet)
python run_swing_research.py --spx
```

---

## Schwab 1000 Autonomous Screener Engine

**Purpose**: Live two-stage constituent screening across 983 Schwab 1000 (SCHK) stocks for coiled ground-floor bases (long) and ceiling exhaustion (short), scoring via Pine script `rev-screener` indicators, and autonomous dispatch into deep research.

**Entry Point**: `src/screener/schwab_pre_move_scan.py`

**Capabilities**:
- **Stage 1 Fast Filter**: Batch-fetches 983 quotes via Schwab API, filters for liquidity ($15+, 500k+ vol), ATR volatility, distance from 52w high, and earnings blackout exclusions.
- **Stage 2 Technical Coiling & Rejection**:
  - Computes 20 EMA, 50 SMA, 200 SMA, 20d excess return vs SPY, Bollinger %b, Connors RSI-3, and NR7 volatility compression.
  - Classifies **Stan Weinstein Market Stages**: Stage 1 (Base), Stage 2 (Advancing), Stage 3 (Distribution), Stage 4 (Declining), Stage 5 (Recovery).
  - Calculates **Connors Extreme Reversal Zones** (Z0-Z3) with historical 86-91% win-rate edge.
  - Scores setups into **Priority Tiers**: `HIGH_PRIORITY` (score >= 75), `MEDIUM_PRIORITY` (score >= 55), `MONITOR`.
- **Autonomous Dispatch Pipeline** (`--autonomous`):
  - Automatically picks top high-priority setups (capped at `--auto-max 3`).
  - Sequentially runs parallel chart scraping (`run_swing_research.py --ticker <SYM>`), local triage (`run_local_research.py --ticker <SYM>`), deep research (`run_deep_research.py --ticker <SYM>`), and watch alerts synchronization (`run_watch_alerts.py --sync --once`).
  - Registers 24/7 cloud quote alerts with Tastytrade mobile push.
- **Continuous Autonomous Daemon & Slot Engine** (`run_continuous_screener.py`):
  - Continuously loops through the Schwab 1000 universe every `--interval` seconds (default 600s).
  - Enriches setups with real-time Tastytrade volatility metrics (IV Rank, IV Percentile, 30d HV, IV-HV spread) and auto-registers 24/7 cloud price alerts with mobile push notifications.
  - **Slot-Aware Deep Research**: Checks local GPU / process slot availability and automatically dispatches exactly **1** top-conviction setup at a time into deep research when the slot is free. If a deep research pass is already running, preserves the slot and holds candidates until it frees up.
  - One-click launch via `scripts\launchers\start_autonomous_scanner.bat`.

**Usage**:
```bash
# Continuous autonomous loop (scans every 10 min, runs 1 deep research in slot when qualified)
python run_continuous_screener.py

# Faster 5-minute loop with headless scraping
python run_continuous_screener.py --interval 300 --headless

# Single-pass scan, report opportunities, dispatch 1 if slot free, and exit
python run_continuous_screener.py --once

# One-shot manual scan for 10 long basing setups
python src/screener/schwab_pre_move_scan.py --side long --top 10

# One-shot manual scan for 10 prime short exhaustion setups
python src/screener/schwab_pre_move_scan.py --side short --top 10
```

---

## Local Research Agent

**Purpose**: Local-LLM triage, news research, and thesis generation for swing trade candidates (free path — no paid APIs).

**Entry Point**: `run_local_research.py`

**Capabilities**:
- Phase 2C-1: Deterministic prefilter (`src/logic/data_window_filter.py`) — era-robust, exclusion-first triage (PASS / WATCH / CUT); only action code 20 (REVERSAL BUY) is a validated PASS lane
- Phase 2C-2: Qwen enrichment (local LLM + Finnhub + Alpha Vantage + Adanos) — strict GBNF `json_schema` triage verdict; local-first, with remote rescue only on local failure
- Batch Google Sheets update for all ticker decisions
- Phase 2E: Segregate tickers into `data/triage/<date>/_DEEP_RESEARCH` (send_for_deep_research=True, cap applied via `deep_research_sort_key` + `rank_pass_tickers`) or `force/` (manual override)
- Rebuilds `data/<date>/consolidate/consolidated_results.json` ledger from per-ticker `_thesis.json` files (globbing both `out_dir` and the triage folders so reruns stay complete)

**Usage**:
```bash
# Local research for all survivors (after scrape phase)
python run_local_research.py 2026-08-22

# Local research for specific ticker
python run_local_research.py --ticker AAPL

# Force a ticker into deep research regardless of LLM gate
python run_local_research.py --ticker AAPL --force AAPL

# Regenerate all theses (ignore cache)
python run_local_research.py --regenerate
```

---

## Deep Research Agent

**Purpose**: Agentic deep research on tickers flagged by local triage. Runs a multi-pass flow: local bull/bear debate → paid (or local-vision) dual-report synthesis → independent report → senior-PM arbitration. Decomposed into a clean, modular 9-module package under `src/logic/deep_research/`.

**Entry Point**: `run_deep_research.py` (wrapper) / `src/logic/deep_research/pipeline.py:run_deep_research`

**Architecture (`src/logic/deep_research/`)**:
- `pipeline.py`: Central orchestration flow, runner dispatch, and pass lifecycle.
- `artifact_loader.py`: Ingests naked & 90d chart screenshots, datawindow csv/json, and historical dossiers.
- `live_fetcher.py`: Gathers live quotes, real-time news, and options market data.
- `prefetch.py`: Prefetches all remote and local artifacts asynchronously ahead of prompt execution.
- `context_builder.py`: Compiles debate transcripts, flags, technical context, and macro posture.
- `debate.py`: Free local bull/bear agent clash with round-robin rebuttals cached to `<ticker>_debate_v2.json`.
- `pass2.py`: Multimodal synthesis with vision & tool-calling loop (Meta AI / OpenRouter / local Qwen-vision).
- `tv_strategies.py`: Ingests and formats TradingView options strategy finder setups.
- `arbitration.py`: Senior Quantitative PM arbitration cross-examines dual reports and emits binding levels.
- `output_writer.py`: Writes `<ticker>_summary.md`, `_independent.md`, `_arbitration.md`, and Google Sheets sync.

**Capabilities**:
- Processes tickers in `data/triage/<date>/_DEEP_RESEARCH/` (and `force/`), ranked by `rank_pass_tickers`; capped at `DEEP_RESEARCH_CAP` (0 = uncapped, rank still sets order)
- COST GATE: paid passes run ONLY for tickers the free local triage flagged `send_for_deep_research == true`; an explicit `--ticker` is always run
- **Debate (local, free)**: Bull vs Bear agents + rebuttals on the full data payload (Data Window, live quote, news dossier, fresh/macro news, social sentiment, GEX, engine math, flags, earnings). Cached to `<ticker>_debate_v2.json`.
- **Pass 2 (paid or local-vision)**: full multimodal payload (naked + 90d chart images) with tool calling. Provider: Meta AI (`META_AI_API_KEY`) → OpenRouter (`OPENROUTER_MODEL`, default `minimax/minimax-m3`) → fallback to local GPU (Qwen3.8-27B + mmproj vision).
- **Pass 2-IND**: independent macro & technical thesis (separate gem: `independent_gem.md`)
- **Pass 2-JUDGE**: local senior-PM Ponytail arbitration cross-examines both reports → final binding directive; appended to both reports
- Writes per ticker: `<ticker>_gemini_thesis.md`, `_deep_context.json`, `reports/<date>/<ticker>_summary.md` (+ `_independent.md`, `_arbitration.md`), updates the Google Sheet verdict, and runs the local thesis-drift consistency check vs the prior thesis
- `--local` forces 100% local inference (no remote API calls)

**Usage**:
```bash
# Run deep research on today's triage folder
python run_deep_research.py 2026-08-22

# Single ticker (auto-scrapes + local-researches first if no artifacts exist)
python run_deep_research.py --ticker META

# Force tickers through local research first, then deep research
python run_deep_research.py --force META,AMD

# 100% local (no paid calls)
python run_deep_research.py 2026-08-22 --local
```

---

## Watchlist & Trigger Alert Engine

**Purpose**: Keep track of generated research reports, extract structured tactical levels (Entry Zone, Stop Loss, Targets, Options structures, Invalidation rules), poll live prices, and dispatch real-time trigger alerts to console, SQLite, and Google Sheets.

**Entry Point**: `run_watch_alerts.py`

**Capabilities**:
- **Report Level Extractor** (`src/logic/report_level_extractor.py`): Ingests markdown reports (`_summary.md` + `_arbitration.md`) and extracts structured levels via embedded `json:watch_levels` block or deterministic regex fallback to `<ticker>_watch_levels.json`.
- **SQLite Watch Database** (`src/tracking/watch_manager.py` -> `data/research_watch.db`): Stores active watch targets, live price tracking, distance to entry %, and alert audit logs.
- **Tastytrade Cloud Alerts Integration** (`src/clients/tastytrade_client.py`): Automatically registers 24/7 cloud quote alerts (`POST /quote-alerts`) on Tastytrade's cloud servers (Entry Zone, Stop Loss, Targets 1 & 2), triggering instant mobile push notifications and SMS to the **Tastytrade Mobile App** on your phone with zero local machine runtime.
- **Google Sheets Mirror**: Synchronizes live watch targets, distance %, and status badges (`🎯 IN ZONE`, `⏳ STALKING`, `🛑 INVALIDATED`, `🏁 TARGET HIT`) to the dedicated **`WATCH-TRIGGERS`** tab in the Trades spreadsheet.
- **Trigger Alert Daemon**: Polls real-time prices (Alpaca / Yahoo) every interval and dispatches deduplicated alerts on state transitions (`STALKING` -> `IN_ZONE` -> `TARGET_HIT` / `STOP_BREACHED`).

**Usage**:
```bash
# Sync/index reports into SQLite, Google Sheets & register Tastytrade cloud alerts
python run_watch_alerts.py --sync --once

# Continuous live price polling & alert loop during market hours
python run_watch_alerts.py --loop --interval 60

# Re-index reports for a specific date
python run_watch_alerts.py --sync --date 2026-08-29
```

---

## Position State & Monitor

**Purpose**: Single source of truth for OPEN positions, with a live polling loop that re-evaluates each open trade against live quotes.

**Entry Point**: `main.py` (started by `run_market_orchestrator.py`)

**Capabilities**:
- `data/positions.json` is the authoritative record (ticker, side, entry, stop, target, last eval)
- ENTRY alert → opens a position + spawns a per-ticker monitor thread
- EXIT alert (TradingView) → routes through the **Institutional Exit Veto Engine** (`skills/exit_management_and_veto.md`):
  - **Tier 1 (Scale Trim / Target Hit)**: Automatically locks profit on 50% at Target 1 ($R \ge 1.5$) and ratchets remaining stop to Break-Even + $0.05 buffer (Golden Lock rule: never turn a winning trade into a loss). Closes remainder at Target 2.
  - **Tier 2 (Catastrophic Circuit Breaker)**: Mandatory market exit if drawdown reaches $\ge 1.25\times$ 5m ATR or 30% option premium loss.
  - **Tier 3 (Technical Invalidation vs Wick Tap)**: Vetoes false exits if price wicks through invalidation but candle closes structurally intact above VWAP/POC. Confirms exit if 5m candle closes beyond line on volume.
  - **Tier 4 (Midday Chop Stagnation Kill)**: Scratches stagnant positions open $\ge 35$ minutes between 11:15 MT and 12:45 MT without Target 1 to prevent theta bleed.
  - **Tier 5 (EOD Mandatory Flatten)**: Flattens all 0DTE and intraday scalps before 13:45 MT (3:45 PM ET).
- **Execution Validator & Bar-Based Fill Simulator** (`src/tracking/execution_validator.py`):
  - Deterministic evaluation of trade setups via pure chronological bar-walking (`evaluate_setup_lifecycle_bars`).
  - Audits exact limit fills, breakout triggers, gap-through-stop at open pricing, and stop-first conservative ambiguity resolution.
  - Supports RSI2 opening-ceiling validation, maximum holding period time exits, and episode index tracking across setup lifecycles.
- **Durable Alert Outbox & Schema v2 Event Accounting** (`src/tracking/alert_db.py`, `src/tracking/position_state.py`):
  - Schema v2 with `schema_meta` migration and immutable `trade_events` audit trail.
  - Quantity-aware position accounting (`remaining_quantity`, scale-outs, fee and slippage tracking).
  - Durable alert routing stages (`RECEIVED` -> `ROUTED` -> `COMPLETED`) with startup outbox replay to prevent dropped alerts.
- Each monitor polls live quotes every `POSITION_POLL_INTERVAL` sec, hard-checks stop/target deterministically (no LLM); the local LLM only writes a playbook/commentary string
- On tracker restart, monitors are rehydrated from `data/positions.json`

**Files**:
- `src/tracking/position_state.py` — atomic load/save/upsert/close of `data/positions.json` with quantity-aware scaling
- `src/tracking/alert_db.py` — SQLite database with Schema v2, immutable `trade_events`, and durable routing outbox
- `src/tracking/position_monitor.py` — `PositionManager` (queue router) + `PositionMonitor` (per-ticker thread)
- `src/tracking/execution_validator.py` — chronological OHLC execution verification & lifecycle fill simulation
- `src/tracking/alert_evaluator.py` — real-time local LLM triage & exit veto decision engine
- `src/tracking/sheets_tracker.py` — Google Sheets mirror (Alerts, Trades, SWING-SPX sheets)

**Config**:
- `POSITION_POLL_INTERVAL` (default 60) — seconds between quote polls per open position

---

## LLM Server

**Purpose**: Local GPU LLM inference via `llama-cpp-server` (llama-server.exe), OpenAI-compatible API on port 8000.

**Profiles** (all in `scripts/launchers/`):
- `start_llm_server.bat` — Qwen3.5-9B-Q8_0, `-c 32768`, `--parallel 3` (~10923 ctx/slot). The tracker/triage default.
- `start_llm_server_markewt_orch.bat` — Qwen3.5-9B, `-c 130000`, `--parallel 1` (single 130k slot; for very large deep-research payloads; serializes concurrency)
- `start_llm_server_qwen38_27b_q4.bat` / `_q6.bat` — Qwen3.8-27B UD-Q4/Q6_K + `--mmproj` vision projector, full `-c 262144` (256k slot), `-b 2048 -ub 2048`, `--split-mode row`, `-ctk q4_0 -ctv q4_0` (fits inside 32GB dual GPU VRAM with ~8.5GB headroom; high-throughput 30k+ token prompt ingestion). Used for local vision deep research.

**Common flags**: `--host 127.0.0.1 --port 8000 -fa on -ctk q8_0 -ctv q8_0 -ngl 999 --reasoning off --jinja`. `--reasoning off` is required so clean JSON + GBNF `json_schema` response formats work (no thinking trace to conflict).

**Sync rule**: `LLM_LOCAL_CONCURRENCY` in `.env` MUST match the server's `--parallel` flag, and per-slot context (`-c / --parallel`) must exceed the largest prompt (~6.2k triage, ~8.6k deep-research).

**Lifecycle**: Managed by `run_market_orchestrator.py` (auto-start/stop/healthcheck); any externally-managed server on port 8000 is adopted instead of started.

---

## LLM Clients

**Purpose**: Python wrapper clients for LLM inference across backends, with a native tool-calling loop.

**Files**: `src/clients/llm_client.py`

**Capabilities**:
- `query_local_llm()` — single entry point; provider priority: Meta AI (`META_AI_API_KEY`) → OpenRouter (`OPENROUTER_KEY`/`OPENROUTER_MODEL`) → local llama-server (local-first when `use_openrouter=False`)
- Vision: `image_paths` (base64 PNG, resized to 640px) for multimodal chart analysis
- Structured output: `json_schema` (GBNF-compiled on the local server) and `json_mode`
- Local concurrency throttled by `LLM_LOCAL_CONCURRENCY` semaphore
- **Tool-calling loop** (`use_tools=True`) with these tools: `fetch_earnings_calendar`, `search_web` (Brave+DDG+Parallel), `fetch_finnhub_news`, `fetch_alpaca_news`, `get_realtime_quote`, `fetch_options_chain`, `scrape_tradingview_options_finder` (TV Strategy Finder + volume charts), `fetch_historical_zone_and_regime_analytics`, `fetch_tastytrade_volatility_and_options` (IV Rank, HV/IV spread, option liquidity rating, short borrow rate), `run_quantitative_plugin`, `execute_python_code` (sandboxed: pre-loaded `df` 300 bars × 85 indicators, `dw`, numpy/pandas/scipy), `fetch_prior_research`, `detect_candlestick_patterns`
- `query_qwen()` (`src/clients/qwen_client.py`) — DashScope Qwen API (cloud, optional vision)

---

## Analytics Plugins

**Purpose**: Decoupled quantitative analytics plugins that enrich Data Window snapshots and are callable by the LLM via the `run_quantitative_plugin` tool.

**Files**: `src/plugins/`

| Plugin | File |
|---|---|
| Order Flow (volume accumulation, CMF, VP liquidity nodes) | `order_flow_plugin.py` |
| Earnings History | `earnings_history_plugin.py` |
| Squeeze Expansion | `squeeze_expansion_plugin.py` |
| HTF Confluence | `htf_confluence_plugin.py` |
| Candlestick Patterns (pin bars, shooting stars, gap fill retests, inside days, engulfing) | `candlestick_patterns_plugin.py` |
| Tastytrade Volatility (IV Rank, IV Percentile, 30d/60d/90d HV, IV-HV spread, liquidity stars, borrow rate) | `tastytrade_plugin.py` |
| Schwab Portfolio & Flow (live positions, cost basis, unrealized P/L, institutional options sweeps) | `schwab_plugin.py` |

**Registry**: `plugin_manager.py` — `PluginManager.run_all(ticker, df, dw)` / `enrich_datawindow_with_plugins()`.

---

## Data Clients

**Purpose**: External data source integrations.

**Files** (`src/clients/`):
- `gmail_client.py` — Gmail IMAP (TradingView alert emails)
- `price_client.py` — Real-time price quotes (multiple exchanges)
- `news_client.py` — Alpaca/Finnhub news headlines
- `news_researcher.py` — Multi-pass local-LLM news synthesis (free)
- `search_client.py` — Web search via DuckDuckGo / Brave
- `google_grounding_client.py` — Google Grounding (Gemini + web search)
- `finnhub_client.py` — Finnhub API (macro, earnings)
- `alphavantage_client.py` — Alpha Vantage (technical data)
- `adanos_client.py` — Adanos API (social sentiment; 250 req/month free tier)
- `earnings_client.py` — Earnings data
- `macro_client.py` — Macro context builder
- `options_client.py` — Live options chains (Alpaca snapshot, yfinance fallback) + quote tools for the deep-research LLM
- `schwab_client.py` — Schwab API (unusual options flow scan); first-time auth via `setup_schwab.py`
- `kalshi_client.py` — Kalshi prediction markets (RSA-signed requests; key at `kalshi/tradingview.txt`)
- `qwen_client.py` — DashScope Qwen cloud API

---

## Data Layer

**Purpose**: Fetch and normalize market data.

**Files** (`src/data/`):
- `tv_scraper.py` — TradingView chart scraper (Playwright + Chrome profile): naked + 90d zoom screenshots, Data Window JSON/CSV (300 daily bars × ~85 indicator columns)
- `tv_options_scraper.py` — TradingView Options Suite: Strategy Finder (spreads with max P/L, R:R, breakevens) → `{symbol}_tv_strategies.json` + volume heatmap/expiration/strike screenshots
- `csv_adapter.py` — CSV integrity checks + Data Window snapshot conversion, trailing 10d volatility/return
- `artifact_cache.py` — TTL-based artifact cache under `data/artifacts/<date>/<ticker>/` (quotes, news, options, debate transcripts)
- `backfill_news.py` — Backfill news for historical dates
- `import_all_history.py` — Import all historical TradingView alerts from Gmail

**Commands**:
```bash
# Backfill news data for historical dates
python src/data/backfill_news.py --date 2026-07-09

# Import all historical TradingView alerts from Gmail
python src/data/import_all_history.py
```

---

## Deterministic Logic

**Purpose**: The exclusion-first math layer. The deterministic filter owns the trading verdict; the LLM never overrides it.

**Files** (`src/logic/`):
- `data_window_filter.py` — Revanth Data Window pre-filter: parses the TV scrape, decodes action codes/masks, emits PASS/WATCH/CUT + long & short trade plans; `deep_research_sort_key` / `rank_pass_tickers` rank candidates
- `trigger_gaps.py` — Buy-trigger gap engine: deterministic distance-to-buy per gate (feature flag `TRIGGERS_ENABLED`)
- `scenario_model.py` — One-step scenario projection (zone/stop/target per candidate price)
- `watch_ranker.py` — Learned (LightGBM) ordering for WATCH candidates competing for paid slots (flag `RANK_MODEL_ENABLED`; never affects PASS/WATCH/CUT)
- `response_model.py` — Historical state-response lookup (edge vs baseline buckets)
- `buy_precedent.py` — Last Code-20 (REVERSAL BUY) performance for a ticker
- `strike_validator.py` — Deterministic option-structure geometry validation (kills naked-leg/ITM-credit rationalizations)
- `thesis_drift.py` — Local-only consistency check between today's thesis and the prior one (contradictions vs expected setup changes)
- `deterministic_cascade.py` — Survivor list builder (screener → cascade → sheet rows)
- `process_survivor.py` — Per-ticker scrape task + local triage task (`prefilter_ticker`, `generate_thesis_task`)
- `alert_parser.py` — TradingView alert email → structured dict
- `deep_research.py` — Deep research pipeline (see Deep Research Agent)

---

## ML Models & Training

**Purpose**: Offline trained models used by the deterministic layer.

**Artifacts**: `data/models/` (`watch_ranker.txt` LightGBM + feature manifest, `state_response.json`)

**Commands** (`scripts/ml/`):
```bash
# Train the Watch Ranker (requires the full_v2 corpus checkout)
CORPUS=full_v2 python scripts/ml/train_watch_ranker.py --out-dir data/models

# Build the state-response model buckets
python scripts/ml/build_state_response.py
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

**Entry Point**: `scripts/dev/`, `scripts/llm_setup/`

**Commands**:
```bash
# Download Qwen3.8-27B GGUF + mmproj vision projector (hf-transfer)
python scripts/dev/download_qwen38.py

# Download latest Qwen
python scripts/dev/download_latest_qwen.py

# Download any model by name
python scripts/llm_setup/download_model.py --model-name "model-name"

# Generate finetune dataset
python scripts/llm_setup/generate_finetune_data.py
```

Models land in `models/`; vision deep research requires the matching `mmproj-*.gguf` projector.

---

## Report Evaluation

**Purpose**: Score deep-research reports against fixtures across 4 buckets: (A) Data Window literal transcription, (B) deterministic decode/derivation, (C) measured-claim attribution/citations, (D) posture agreement with the deterministic verdict.

**Commands** (`scripts/eval/`):
```bash
python scripts/eval/score_reports.py
python scripts/eval/validate_report.py
```

---

## Triage Utilities

**Purpose**: Re-triage and watch-trigger tooling on persisted artifacts.

**Commands** (`scripts/triage/`):
```bash
# Re-run data window filter over existing artifacts
python scripts/triage/retriage_datawindows.py

# Validate trigger-gap computation
python scripts/triage/validate_trigger_gaps.py

# Print open buy triggers for a date
python scripts/triage/watch_triggers.py 2026-08-22
```

---

## Stock Trading Cockpit & Copilot WebUI

**Purpose**: Modern, unified full-stack trading operations cockpit and interactive AI Copilot (`run_ui.py`). Modular FastAPI backend mounted under `src/ui/` with real-time SSE streaming, asynchronous research execution queues, background process supervision, and dynamic charts.

**Entry Point**: `run_ui.py` (server launcher) / `src/ui/app.py:create_app`

**Architecture (`src/ui/`)**:
- `app.py`: Central FastAPI factory, CORS & Cache-Control headers, static mounts (`web/`).
- `routes/alerts.py`: TradingView alerts history, Gmail 1-shot ingestor triggers, local triage verdicts.
- `routes/copilot.py`: REV CHAT interactive copilot, SSE token streaming, sandbox Python execution (`/api/copilot/execute-python`), session persistence in SQLite.
- `routes/intraday.py`: Open position state, live P&L streaming, emergency flatten commands, 0DTE trade simulator.
- `routes/portfolio.py`: Live Schwab brokerage holdings, positions, balances, day P&L.
- `routes/research.py`: Dossier viewing, multi-pass report tabs (`_summary.md`, `_independent.md`, `_arbitration.md`), chart image streaming, async research queue dispatch.
- `routes/screener.py`: Live Schwab 1000 constituent scanner trigger, autonomous continuous screener status, filter overrides.
- `routes/status.py`: Process supervisor status, port diagnostics, live system log streaming.
- `routes/trades.py`: Daily survivor cascade rows, action codes, and trade plans.
- `routes/watchlist.py`: Research watch targets, trigger alert rules, distance-to-entry tracking, and Tastytrade cloud alert registration.
- `services/copilot_context.py`: Multi-modal market context compiler (ingests live Schwab holdings, 90d options sweeps, Tastytrade IV metrics, datawindows, reports, and real-time quotes).
- `services/daemon_manager.py`: Start/stop/inspect lifecycle supervisor for background daemons (`main.py`, `continuous_screener`).
- `services/research_queue.py`: Multi-slot research task queue manager with process/thread isolation and database rehydration.
- `state.py`: SQLite schema migrations (`data/research_watch.db`), process maps, and shared runtime buffers.

**Usage**:
```bash
# Launch Cockpit UI on port 8080 with auto-browser launch
python run_ui.py

# Launch on custom port
python run_ui.py --port 8080 --no-browser
```

---

## Quantitative Strategy Skills & Risk Playbooks

**Purpose**: Systematic institutional knowledge base and quantitative playbooks loaded into local LLMs (`src/tracking/alert_evaluator.py`, `gems/revanth-gem-local.md`, `gems/revanth-0dte.md`).

**Files** (`skills/`):
- `exit_management_and_veto.md`: 5-Tier institutional exit hierarchy (Target 1 scale trim 50% + BE+ lock, catastrophic ATR loss cap, technical invalidation vs wick tap, midday chop stagnation kill, EOD mandatory flatten).
- `execution_timing_gates.md`: Time-of-day execution permissions and blackouts (Midday lull gate 11:15-12:45 MT, Power Hour index trend acceleration exception for QQQ/SPY, 15m opening range filter).
- `catastrophe_risk_controls.md`: Capital protection circuit breakers, maximum $1.25\times$ 5m ATR stop distance, 3 consecutive intraday loss day pause, slippage limits.
- `weinstein_stage_rules.md`: Stan Weinstein 4-Stage cycle classification (Stage 1 Base, Stage 2 Markup, Stage 3 Distribution, Stage 4 Markdown, Stage 5 Recovery) and multi-timeframe moving average confluence (20 EMA, 50 SMA, 200 SMA).
- `options_spread_architect.md`: Structure selection mapped to Tastytrade IV Rank (<30 Long Debit/Singles, 30-50 Defined-Risk Spreads, >50 Credit Spreads/Short Financing), Expected Move (EM) bounds, and strike delta selection.
- `postmortem_learnings.md`: Empirical session audit findings (+683 capital saved by AI guardrails, lunch lull failure clustering analysis, index power hour exceptions).

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

### Test LLM Tools
```bash
python test_llm_tools.py
```
Exercise the tool-calling loop (Kalshi prediction markets + earnings lookup).

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

---

## Research & Test Scripts

One-off experiments and targeted tests (not part of the daily pipeline). Highlights:

- `scripts/research/run_meta_deep_research.py` — local agentic deep research benchmark (Qwen3.8-27B vision, `force_local=True`)
- `scripts/research/compare_meta_deep_research.py` — local vs frontier-cloud deep research A/B benchmark
- `scripts/research/test_llm_code_interpreter.py` — verify the LLM drives `execute_python_code` against `datawindow.csv`
- `scripts/research/test_vision_chart.py` / `test_vision_targeted_levels.py` — chart vision checks
- `scripts/research/test_candlestick_patterns_plugin.py` / `test_order_flow_plugin.py` — plugin unit checks
- `scripts/dev/test_meta_vision.py` / `test_vision_recognition.py` — vision recognition dev tests
- `test_finnhub.py`, `test_kalshi.py` — client smoke tests

**Unit tests**: `tests/` (pytest) — trigger gaps, scenario model, response model, price client, position state, CSV adapter, config.
```bash
pytest
```

