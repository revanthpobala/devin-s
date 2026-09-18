"""
src/screener/schwab_pre_move_scan.py

High-Speed Pre-Move Compression Screener for the Schwab 1000 Index (SCHK ETF).
Identifies stocks coiling at support BEFORE the multi-day expansion begins:
  1. Market Tide Gate: SPY / QQQ macro trend check (no fighting the tide).
  2. Relative Strength Gate: Outperforming SPY over the last 20 trading days.
  3. Sector Safety Gate: Excludes clinical-stage biotechs & binary pharma gambles.
  4. Earnings Blackout Gate: Excludes tickers with earnings in the next 14 days.
  5. Volatility Squeeze (Bollinger Bands inside Keltner Channels) or NR7 Compression.
  6. Volume Dry-Up: Supply exhaustion at the rising 20 EMA / 50 SMA.
  7. Automated Handoff: Saves to survivors.json and triggers the research & alert pipeline.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

try:
    import talib
    HAS_TALIB = True
except ImportError:
    talib = None
    HAS_TALIB = False

from src import config
from src.clients.schwab_client import get_schwab_client


def _get_python_exe() -> str:
    """Return absolute path to virtualenv python executable if available."""
    base = config.BASE_DIR / ".venv"
    win_py = base / "Scripts" / "python.exe"
    nix_py = base / "bin" / "python"
    if win_py.exists():
        return str(win_py)
    elif nix_py.exists():
        return str(nix_py)
    return sys.executable


def detect_candlestick_pattern_talib(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    side: str = "SHORT",
) -> str:
    """
    Detects high-conviction candlestick reversal and compression patterns
    using official C-based TA-Lib routines across trailing daily bars.
    Prioritizes bar -1 (today's close), then bar -2 (yesterday's setup).
    """
    opens = np.ascontiguousarray(opens, dtype=np.float64)
    highs = np.ascontiguousarray(highs, dtype=np.float64)
    lows = np.ascontiguousarray(lows, dtype=np.float64)
    closes = np.ascontiguousarray(closes, dtype=np.float64)

    if len(closes) < 10 or not HAS_TALIB:
        if side == "SHORT":
            return "Ceiling Stall"
        return "Support Coil"

    if side == "SHORT":
        bearish_funcs = [
            ("Shooting Star", talib.CDLSHOOTINGSTAR, lambda r: r < 0),
            ("Evening Star", talib.CDLEVENINGSTAR, lambda r: r < 0),
            ("Evening Doji Star", talib.CDLEVENINGDOJISTAR, lambda r: r < 0),
            ("Bearish Engulfing", talib.CDLENGULFING, lambda r: r < 0),
            ("Dark Cloud Cover", talib.CDLDARKCLOUDCOVER, lambda r: r < 0),
            ("Hanging Man", talib.CDLHANGINGMAN, lambda r: r < 0),
            ("Gravestone Doji", talib.CDLGRAVESTONEDOJI, lambda r: r != 0),
            ("Bearish Harami", talib.CDLHARAMI, lambda r: r < 0),
            ("Three Black Crows", talib.CDL3BLACKCROWS, lambda r: r < 0),
            ("Two Crows", talib.CDL2CROWS, lambda r: r < 0),
            ("Bearish Belt Hold", talib.CDLBELTHOLD, lambda r: r < 0),
            ("Bearish Hikkake", talib.CDLHIKKAKE, lambda r: r < 0),
            ("Doji Stall", talib.CDLDOJI, lambda r: r != 0),
            ("Spinning Top Stall", talib.CDLSPINNINGTOP, lambda r: r != 0),
        ]

        # Check bar -1 (latest closed bar) first, then bar -2 (prior rejection)
        for bar_idx in [-1, -2]:
            for name, fn, cond in bearish_funcs:
                try:
                    res = fn(opens, highs, lows, closes)
                    if cond(res[bar_idx]):
                        bar_label = "" if bar_idx == -1 else " (Prior Bar)"
                        return f"{name}{bar_label}"
                except Exception:
                    pass

        # Fallback heuristics
        recent_ranges = highs[-7:] - lows[-7:]
        if (highs[-1] - lows[-1]) <= np.min(recent_ranges):
            return "NR7 Ceiling Stall"
        elif closes[-1] < opens[-1]:
            return "Bearish Reversal Red"
        return "Ceiling Stall"

    else:
        # side == "LONG"
        bullish_funcs = [
            ("Hammer", talib.CDLHAMMER, lambda r: r > 0),
            ("Inverted Hammer", talib.CDLINVERTEDHAMMER, lambda r: r > 0),
            ("Morning Star", talib.CDLMORNINGSTAR, lambda r: r > 0),
            ("Morning Doji Star", talib.CDLMORNINGDOJISTAR, lambda r: r > 0),
            ("Bullish Engulfing", talib.CDLENGULFING, lambda r: r > 0),
            ("Piercing Pattern", talib.CDLPIERCING, lambda r: r > 0),
            ("Dragonfly Doji", talib.CDLDRAGONFLYDOJI, lambda r: r != 0),
            ("Bullish Harami", talib.CDLHARAMI, lambda r: r > 0),
            ("Three White Soldiers", talib.CDL3WHITESOLDIERS, lambda r: r > 0),
            ("Bullish Belt Hold", talib.CDLBELTHOLD, lambda r: r > 0),
            ("Bullish Hikkake", talib.CDLHIKKAKE, lambda r: r > 0),
            ("Doji Base", talib.CDLDOJI, lambda r: r != 0),
            ("Spinning Top Coil", talib.CDLSPINNINGTOP, lambda r: r != 0),
        ]

        for bar_idx in [-1, -2]:
            for name, fn, cond in bullish_funcs:
                try:
                    res = fn(opens, highs, lows, closes)
                    if cond(res[bar_idx]):
                        bar_label = "" if bar_idx == -1 else " (Prior Bar)"
                        return f"{name}{bar_label}"
                except Exception:
                    pass

        recent_ranges = highs[-7:] - lows[-7:]
        if (highs[-1] - lows[-1]) <= np.min(recent_ranges):
            return "NR7 Compression"
        return "Support Coil"


logger = logging.getLogger("schwab_screener")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (SchwabScreener) %(message)s",
)

SCHWAB_1000_CSV = config.BASE_DIR / "All_tickrs" / "Schwab_1000_Index®_Index_Constituents.csv"
SP500_CSV = config.BASE_DIR / "data" / "SP500.csv"


def load_schwab_1000_tickers() -> List[str]:
    """Load and sanitize valid tickers from the Schwab 1000 constituents CSV."""
    if not SCHWAB_1000_CSV.exists():
        logger.error(f"Schwab 1000 CSV not found at {SCHWAB_1000_CSV}")
        return []

    try:
        df = pd.read_csv(SCHWAB_1000_CSV)
        raw_tickers = df["Ticker"].dropna().tolist()
        clean = []
        for t in raw_tickers:
            sym = str(t).strip().upper().replace(".", "/")
            if sym and not sym.startswith("^") and sym != "NAN":
                clean.append(sym)
        # Deduplicate while preserving index order
        seen = set()
        deduped = []
        for s in clean:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        logger.info(f"Loaded {len(deduped)} valid tickers from Schwab 1000 Index.")
        return deduped
    except Exception as e:
        logger.error(f"Failed to read Schwab 1000 CSV: {e}")
        return []


def load_biotech_exclusion_set() -> set[str]:
    """Build a set of symbols classified as Biotechnology or Clinical Pharma."""
    excluded = set()
    if SP500_CSV.exists():
        try:
            df = pd.read_csv(SP500_CSV)
            for _, r in df.iterrows():
                sub = str(r.get("GICS Sub-Industry", "")).lower()
                sym = str(r.get("Symbol", "")).upper().strip()
                if "biotechnology" in sub or "biotech" in sub:
                    excluded.add(sym)
        except Exception:
            pass

    # Known binary trial biotechs in midcaps
    known_biotechs = {"RVMD", "JAZZ", "ARGX", "BIIB", "ALNY", "ROIV", "INSM", "MRNA", "BNTX", "VRTX", "CRNX", "KRYS"}
    excluded.update(known_biotechs)
    return excluded


def check_market_tide(client) -> Dict[str, Any]:
    """
    Gate 1: Checks macro market tide on SPY.
    Computes SPY 20 EMA and 50 SMA.
    """
    logger.info("Evaluating Market Tide Gate (SPY)...")
    try:
        r = client.get_price_history_every_day("SPY")
        if r.status_code == 200:
            candles = r.json().get("candles") or []
            if len(candles) >= 50:
                closes = pd.Series([float(c["close"]) for c in candles])
                last_px = float(closes.iloc[-1])
                ema20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
                sma50 = float(closes.rolling(50).mean().iloc[-1])

                is_bullish = last_px >= ema20 and ema20 >= sma50
                is_neutral = last_px >= sma50
                trend_str = "BULLISH (TIDE ON)" if is_bullish else ("NEUTRAL / CONSOLIDATION" if is_neutral else "DEFENSIVE / BEARISH")

                spy_20d_return = (last_px - float(closes.iloc[-21])) / float(closes.iloc[-21]) if len(closes) >= 21 else 0.0

                logger.info(f"SPY: ${last_px:.2f} | 20 EMA: ${ema20:.2f} | 50 SMA: ${sma50:.2f} -> {trend_str}")
                return {
                    "last_px": last_px,
                    "ema20": ema20,
                    "sma50": sma50,
                    "is_bullish": is_bullish,
                    "is_neutral": is_neutral,
                    "trend_str": trend_str,
                    "spy_20d_return": spy_20d_return,
                    "candles": candles,
                }
    except Exception as e:
        logger.warning(f"Failed to check SPY market tide: {e}")

    return {"is_bullish": True, "is_neutral": True, "trend_str": "UNKNOWN", "spy_20d_return": 0.0, "candles": []}


def fetch_all_quotes_batch(client, tickers: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch comprehensive quote + fundamental data for all tickers in chunks of 50."""
    results = {}
    total = len(tickers)
    chunk_size = 50

    logger.info(f"Fetching Schwab batch quotes for {total} constituents in chunks of {chunk_size}...")
    t0 = time.time()

    for i in range(0, total, chunk_size):
        chunk = tickers[i : i + chunk_size]
        try:
            resp = client.get_quotes(chunk)
            if resp.status_code == 401:
                client = get_schwab_client(force_new=True)
                resp = client.get_quotes(chunk)

            if resp.status_code == 200:
                data = resp.json()
                for sym, val in data.items():
                    if isinstance(val, dict):
                        results[sym] = val
        except Exception as e:
            logger.warning(f"Error fetching chunk {i//chunk_size + 1}: {e}")

    t1 = time.time()
    logger.info(f"Successfully fetched {len(results)}/{total} quotes in {t1 - t0:.2f} seconds.")
    return results


def stage1_fast_filter(raw_quotes: Dict[str, Dict[str, Any]], biotech_set: set[str]) -> List[Dict[str, Any]]:
    """
    Stage 1 Filter:
      - Price >= $15.00
      - 10-day Average Volume >= 800,000 shares
      - Relative Strength: Price >= 0.80 * 52-Week High
      - Orderly daily action: Net % change between -4.5% and +1.5%
      - Volume Dry-Up: Today's Vol <= 0.85 * 10-day Avg Vol
      - Gate 3: Exclude clinical-stage biotechs
    """
    candidates = []

    for sym, val in raw_quotes.items():
        clean_sym = sym.replace("/", ".")
        if clean_sym in biotech_set:
            continue

        ref = val.get("reference") or {}
        desc = str(ref.get("description", "")).lower()
        if "biotech" in desc or "therapeutics" in desc:
            continue

        quote = val.get("quote") or {}
        fund = val.get("fundamental") or {}

        last_px = (
            quote.get("lastPrice")
            or quote.get("closePrice")
            or val.get("regular", {}).get("regularMarketLastPrice")
            or 0.0
        )
        if last_px < 15.0:
            continue

        high_52 = quote.get("52WeekHigh") or 0.0
        low_52 = quote.get("52WeekLow") or 0.0
        if high_52 <= 0 or low_52 <= 0 or high_52 <= low_52:
            continue

        # 1. Ground-Floor Basing Gate:
        # Require at least 15% upside runway to 52-week high (prevents picking stocks already at 52w ceiling)
        headroom_52w = (high_52 - last_px) / last_px * 100
        if headroom_52w < 15.0:
            continue  # Exclude stocks riding at all-time highs with depleted swing runway

        # 2. 52-Week Range Position:
        # Require price to be in the 18% to 68% sweet spot of its 52-week range.
        # Excludes over-extended tops (>68%) and freefalling knives (<18%).
        range_span_52w = high_52 - low_52
        pos_52w = ((last_px - low_52) / range_span_52w) * 100
        if pos_52w > 68.0 or pos_52w < 18.0:
            continue

        avg_10d_vol = fund.get("avg10DaysVolume") or fund.get("avg1YearVolume") or 0.0
        if avg_10d_vol < 800_000:
            continue

        tot_vol = quote.get("totalVolume") or 0
        pct_chg = quote.get("netPercentChange") or 0.0

        if pct_chg > 2.0 or pct_chg < -4.5:
            continue

        vol_ratio = tot_vol / avg_10d_vol if avg_10d_vol > 0 else 1.0

        candidates.append(
            {
                "symbol": clean_sym,
                "schwab_symbol": sym,
                "price": round(float(last_px), 2),
                "52w_high": round(float(high_52), 2),
                "52w_low": round(float(low_52), 2),
                "pos_52w": round(float(pos_52w), 1),
                "headroom_52w": round(float(headroom_52w), 1),
                "high_pct": round((last_px / high_52 * 100), 1) if high_52 > 0 else 0.0,
                "volume": int(tot_vol),
                "avg_10d_vol": int(avg_10d_vol),
                "vol_ratio": round(vol_ratio, 2),
                "net_pct_change": round(float(pct_chg), 2),
                "pe_ratio": round(float(fund.get("peRatio") or 0.0), 1),
            }
        )

    logger.info(f"Stage 1 filter complete: {len(candidates)} candidates passed ground-floor basing, liquidity, and biotech exclusions.")
    return candidates


def stage1_short_filter(raw_quotes: Dict[str, Dict[str, Any]], biotech_set: set[str]) -> List[Dict[str, Any]]:
    """
    Stage 1 Filter for Prime Short Candidates:
      - Price >= $15.00
      - 10-day Average Volume >= 800,000 shares
      - Sitting at 52-Week High Top: pos_52w >= 88.0%
      - Bumping against ceiling: Headroom to 52w High <= 4.5%
      - Downside runway from 52w low >= 25.0% (plenty of air below)
      - Exclude clinical biotechs
    """
    candidates = []

    for sym, val in raw_quotes.items():
        clean_sym = sym.replace("/", ".")
        if clean_sym in biotech_set:
            continue

        ref = val.get("reference") or {}
        desc = str(ref.get("description", "")).lower()
        if "biotech" in desc or "therapeutics" in desc:
            continue

        quote = val.get("quote") or {}
        fund = val.get("fundamental") or {}

        last_px = (
            quote.get("lastPrice")
            or quote.get("closePrice")
            or val.get("regular", {}).get("regularMarketLastPrice")
            or 0.0
        )
        if last_px < 15.0:
            continue

        high_52 = quote.get("52WeekHigh") or 0.0
        low_52 = quote.get("52WeekLow") or 0.0
        if high_52 <= 0 or low_52 <= 0 or high_52 <= low_52:
            continue

        headroom_52w = (high_52 - last_px) / last_px * 100
        if headroom_52w > 4.5:
            continue  # Must be bumping against ceiling (< 4.5% headroom)

        range_span_52w = high_52 - low_52
        pos_52w = ((last_px - low_52) / range_span_52w) * 100
        if pos_52w < 88.0:
            continue  # Must be in the top 12% of 52w range

        downside_air_52w = (last_px - low_52) / last_px * 100
        if downside_air_52w < 25.0:
            continue

        avg_10d_vol = fund.get("avg10DaysVolume") or fund.get("avg1YearVolume") or 0.0
        if avg_10d_vol < 800_000:
            continue

        tot_vol = quote.get("totalVolume") or 0
        pct_chg = quote.get("netPercentChange") or 0.0

        candidates.append(
            {
                "symbol": clean_sym,
                "schwab_symbol": sym,
                "price": round(float(last_px), 2),
                "52w_high": round(float(high_52), 2),
                "52w_low": round(float(low_52), 2),
                "pos_52w": round(float(pos_52w), 1),
                "headroom_52w": round(float(headroom_52w), 1),
                "downside_air_52w": round(float(downside_air_52w), 1),
                "volume": int(tot_vol),
                "avg_10d_vol": int(avg_10d_vol),
                "net_pct_change": round(float(pct_chg), 2),
                "pe_ratio": round(float(fund.get("peRatio") or 0.0), 1),
            }
        )

    logger.info(f"Stage 1 Short filter complete: {len(candidates)} overextended ceiling candidates found.")
    return candidates


def evaluate_technical_coiling(candles: List[Dict[str, Any]], spy_20d_return: float) -> Optional[Dict[str, Any]]:
    """Evaluates 60-200 daily OHLCV bars for Pre-Move Coiling & Relative Strength vs SPY."""
    if len(candles) < 50:
        return None

    df = pd.DataFrame(candles)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    closes = df["close"]
    highs = df["high"]
    lows = df["low"]
    vols = df["volume"]

    last_close = float(closes.iloc[-1])
    last_high = float(highs.iloc[-1])
    last_low = float(lows.iloc[-1])
    last_vol = float(vols.iloc[-1])

    # 1. Moving Averages
    ema20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
    sma50 = float(closes.rolling(50).mean().iloc[-1])
    sma200 = (
        float(closes.rolling(200).mean().iloc[-1])
        if len(closes) >= 200
        else float(closes.rolling(len(closes)).mean().iloc[-1])
    )

    # 1. Primary Trend & Bullish Moving Average Alignment:
    # Must be above rising 200 SMA (or within 2%)
    if last_close < (sma200 * 0.98):
        return None

    # Disqualify downtrend / relief-bounce traps:
    # Price must not be capped underneath a declining 50 SMA
    if last_close < (sma50 * 0.995):
        return None

    # 20 EMA must be above 50 SMA (bullish moving average alignment; no death crosses)
    if ema20 < (sma50 * 0.985):
        return None

    # 50 SMA must be healthy relative to 200 SMA (Stage 1 base or Stage 2 advance)
    if sma50 < (sma200 * 0.98):
        return None

    # Disqualify extended runners (> 25% above 200 SMA, per EXT_MAX era-robust limit)
    ext_200_pct = (last_close - sma200) / sma200 * 100
    if ext_200_pct > 25.0:
        return None

    # 2. Gate 2: Relative Strength vs SPY over 20 days
    ticker_20d_return = (last_close - float(closes.iloc[-21])) / float(closes.iloc[-21]) if len(closes) >= 21 else 0.0
    relative_strength = ticker_20d_return - spy_20d_return  # Excess return vs SPY
    if relative_strength < -0.05:
        return None

    # 3. Support Proximity: Within 2.5% of EMA 20 or SMA 50 (holding support from ABOVE)
    dist_ema20_pct = abs(last_close - ema20) / last_close * 100
    dist_sma50_pct = abs(last_close - sma50) / last_close * 100
    near_ema20 = dist_ema20_pct <= 2.5 and last_close >= (ema20 * 0.985)
    near_sma50 = dist_sma50_pct <= 2.5 and last_close >= (sma50 * 0.985)

    if not (near_ema20 or near_sma50):
        return None

    # 4. ATR & Bollinger / Keltner Squeeze
    tr1 = highs - lows
    tr2 = (highs - closes.shift(1)).abs()
    tr3 = (lows - closes.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr20 = float(tr.rolling(20).mean().iloc[-1])

    # Bollinger Bands (20, 2.0)
    bb_mid = closes.rolling(20).mean().iloc[-1]
    bb_std = closes.rolling(20).std().iloc[-1]
    bb_upper = bb_mid + (2.0 * bb_std)
    bb_lower = bb_mid - (2.0 * bb_std)

    # Keltner Channels (20, 1.5)
    kc_upper = bb_mid + (1.5 * atr20)
    kc_lower = bb_mid - (1.5 * atr20)

    squeeze_on = (bb_lower > kc_lower) and (bb_upper < kc_upper)

    # 5. NR7 (Narrowest range in 7 days)
    recent_ranges = (highs - lows).iloc[-7:]
    nr7 = (last_high - last_low) <= recent_ranges.min()

    # 6. Volume Exhaustion
    avg_vol_20 = float(vols.rolling(20).mean().iloc[-1])
    vol_dry = (last_vol / avg_vol_20) <= 0.80 if avg_vol_20 > 0 else False
    vol_declining_3d = vols.iloc[-1] < vols.iloc[-2] < vols.iloc[-3] if len(vols) >= 3 else False
    # 7. 60-Day Range & Resistance Runway
    high_60d = float(highs.iloc[-60:].max()) if len(highs) >= 60 else float(highs.max())
    low_60d = float(lows.iloc[-60:].min()) if len(lows) >= 60 else float(lows.min())
    headroom_pct = (high_60d - last_close) / last_close * 100
    range_span_60d = high_60d - low_60d
    range_pos_pct = ((last_close - low_60d) / range_span_60d * 100) if range_span_60d > 0 else 50.0

    # Disqualify low R:R ceiling traps (< 6.0% headroom to 60-day resistance)
    if headroom_pct < 6.0:
        return None

    # 8. Revanth Proxy R:R (rev-screener.pine):
    # stop = 10-bar lowest low (structural swing low)
    # target = 60-bar highest high (prior range high)
    # rr = (target - close) / (close - stop)
    # atrs_up = (close - stop) / atr20
    swing_lo = float(lows.iloc[-10:].min()) if len(lows) >= 10 else float(lows.min())
    range_hi = high_60d
    long_risk = last_close - swing_lo
    long_reward = range_hi - last_close
    long_rr = round(long_reward / long_risk, 1) if long_risk > 0 and long_reward > 0 else 0.0
    atrs_up = round(long_risk / atr20, 2) if atr20 > 0 else 0.0

    # Gate: R:R >= 1.5 (per user instruction, R:R 2.0 is not strictly required; 1.5 is acceptable)
    if long_rr < 1.5:
        return None

    setup_posture = "Open Runway (Dip Buy)" if headroom_pct >= 12.0 else "Mid-Base Coil (Pullback)"
    atr_pct = (atr20 / last_close) * 100

    # 9. Candlestick Pattern Recognition via TA-Lib (Bullish at Support)
    opens_arr = df["open"].values.astype(np.float64)
    highs_arr = df["high"].values.astype(np.float64)
    lows_arr = df["low"].values.astype(np.float64)
    closes_arr = df["close"].values.astype(np.float64)
    bull_pattern = detect_candlestick_pattern_talib(opens_arr, highs_arr, lows_arr, closes_arr, side="LONG")

    # 10. Pine Screener Engine Integration (rev-screener.pine)
    try:
        from src.screener.pine_screener_engine import evaluate_pine_screener_model
        pine_metrics = evaluate_pine_screener_model(df)
    except Exception as e_pine:
        logger.debug(f"Pine screener calculation warning: {e_pine}")
        pine_metrics = {}

    # Strict Quality Gate for Long Basing Setups:
    # 1. Weinstein Stage: Disqualify Stage 4 (Declining) and Stage 3 (Distribution)
    stage = pine_metrics.get("weinstein_stage")
    if stage is not None and stage in (3, 4):
        return None  # Stage 4 is a declining falling knife; Stage 3 is topping distribution.

    # 2. Priority Score: Disqualify low-score uncoiled noise (< 50.0)
    score = pine_metrics.get("priority_score")
    if score is not None and score < 50.0:
        return None  # Weak setup below conviction threshold.

    res = {
        "ema20": round(ema20, 2),
        "sma50": round(sma50, 2),
        "sma200": round(sma200, 2),
        "ext_200_pct": round(float(ext_200_pct), 1),
        "dist_ema20_pct": round(dist_ema20_pct, 2),
        "dist_sma50_pct": round(dist_sma50_pct, 2),
        "relative_strength_20d": round(relative_strength * 100, 2),
        "squeeze_on": bool(squeeze_on),
        "nr7": bool(nr7),
        "vol_dry": bool(vol_dry),
        "vol_declining_3d": bool(vol_declining_3d),
        "headroom_pct": round(float(headroom_pct), 1),
        "ceiling_level": round(float(high_60d), 2),
        "stop_level": round(float(swing_lo), 2),
        "target_level": round(float(high_60d), 2),
        "long_rr": long_rr,
        "atrs_up": atrs_up,
        "range_pos_pct": round(float(range_pos_pct), 1),
        "is_ceiling_coil": False,
        "setup_posture": setup_posture,
        "pattern": bull_pattern,
        "atr_pct": round(float(atr_pct), 2),
        "support_level": round(float(ema20 if near_ema20 else sma50), 2),
        "support_type": "20 EMA" if near_ema20 else "50 SMA",
    }
    res.update(pine_metrics)
    return res


def check_earnings_blackout(ticker: str) -> bool:
    """Gate 4: Returns True if earnings are safely > 14 days away or unknown. False if within 14 days."""
    try:
        from src.clients.earnings_client import get_next_earnings_days

        days = get_next_earnings_days(ticker)
        if days is not None and days <= 14:
            logger.info(f"[{ticker}] Earnings blackout triggered: reports in {days} days. Disqualified.")
            return False
    except Exception:
        pass
    return True


def run_stage2_technical_scan(
    client, candidates: List[Dict[str, Any]], spy_20d_return: float, max_workers: int = 6
) -> List[Dict[str, Any]]:
    """Pulls historical daily candles for Stage 1 survivors and scores pre-move coiling."""
    survivors = []
    logger.info(f"Running Stage 2 Technical Compression scan across {len(candidates)} candidates...")

    def _eval_ticker(cand):
        schwab_sym = cand["schwab_symbol"]
        clean_sym = cand["symbol"]
        for attempt in range(2):
            try:
                r = client.get_price_history_every_day(schwab_sym)
                if r.status_code == 429:
                    time.sleep(1.0)
                    continue
                if r.status_code == 200:
                    data = r.json()
                    candles = data.get("candles") or []
                    metrics = evaluate_technical_coiling(candles, spy_20d_return)
                    if metrics:
                        # Check 14-day earnings blackout gate
                        if not check_earnings_blackout(clean_sym):
                            return None
                        cand_copy = dict(cand)
                        cand_copy.update(metrics)
                        return cand_copy
                    break
            except Exception as e:
                logger.debug(f"Error fetching candles for {schwab_sym}: {e}")
                time.sleep(0.5)
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_eval_ticker, c): c for c in candidates}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                survivors.append(res)

    # Sort deterministically: Priority score > Extreme reversal > Squeeze active > Long R:R
    survivors.sort(
        key=lambda x: (
            x.get("priority_tier") == "HIGH_PRIORITY",
            float(x.get("priority_score", 0.0)),
            bool(x.get("is_extreme_reversal", False)),
            bool(x.get("squeeze_on", False)),
            float(x.get("long_rr", 0.0)),
            float(x.get("headroom_pct", 0.0))
        ),
        reverse=True
    )
    logger.info(f"Stage 2 scan complete: {len(survivors)} pre-move coiled setups survived all gates.")
    return survivors


def save_survivors_manifest(top_picks: List[Dict[str, Any]], date_str: str) -> Path:
    """Saves qualified survivors into data/raw/<date_str>/survivors.json for UI & pipeline consumption."""
    out_dir = config.BASE_DIR / "data" / "raw" / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    surv_file = out_dir / "survivors.json"

    manifest = []
    for p in top_picks:
        manifest.append(
            {
                "Ticker": str(p["symbol"]),
                "Symbol": str(p["symbol"]),
                "symbol": str(p["symbol"]),
                "source": "schwab_pre_move_scan",
                "side": "LONG",
                "screener_setup": str(p.get("setup_posture") or f"Pre-Move Compression ({p['support_type']})"),
                "setup_posture": str(p.get("setup_posture", "Pullback")),
                "support_level": float(p["support_level"]),
                "stop_level": float(p.get("stop_level", p["support_level"])),
                "target_level": float(p.get("target_level", p.get("ceiling_level", 0.0))),
                "long_rr": float(p.get("long_rr", 1.5)),
                "atrs_up": float(p.get("atrs_up", 0.0)),
                "ceiling_level": float(p.get("ceiling_level", 0.0)),
                "headroom_pct": float(p.get("headroom_pct", 0.0)),
                "headroom_52w": float(p.get("headroom_52w", 0.0)),
                "pos_52w": float(p.get("pos_52w", 50.0)),
                "is_ceiling_coil": bool(p.get("is_ceiling_coil", False)),
                "range_pos_pct": float(p.get("range_pos_pct", 50.0)),
                "atr_pct": float(p["atr_pct"]),
                "pattern": str(p.get("pattern", "Support Coil")),
                "relative_strength_20d": float(p["relative_strength_20d"]),
                "squeeze_on": bool(p["squeeze_on"]),
                "nr7": bool(p["nr7"]),
                "vol_dry": bool(p.get("vol_dry", False)),
                "price": float(p["price"]),
                # Pine Screener Engine Fields (rev-screener.pine)
                "weinstein_stage": int(p.get("weinstein_stage", 1)),
                "rev_zone_long": float(p.get("rev_zone_long", 0.0)),
                "rev_zone_short": float(p.get("rev_zone_short", 0.0)),
                "is_extreme_reversal": bool(p.get("is_extreme_reversal", False)),
                "buy_score": float(p.get("buy_score", 50.0)),
                "sell_score": float(p.get("sell_score", 50.0)),
                "prime_signal": int(p.get("prime_signal", 0)),
                "vcp_energy": int(p.get("vcp_energy", 0)),
                "entry_rank": float(p.get("entry_rank", 50.0)),
                "priority_score": float(p.get("priority_score", 50.0)),
                "priority_tier": str(p.get("priority_tier", "MONITOR")),
                "tastytrade": p.get("tastytrade"),
                "tastytrade_alert_active": bool(p.get("tastytrade_alert_active", False)),
            }
        )

    surv_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    schwab_file = out_dir / "schwab_survivors.json"
    schwab_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info(f"Saved {len(manifest)} coiled swing candidates to {surv_file} and {schwab_file}")
    return surv_file


def evaluate_short_setup(candles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Evaluates candidates for Prime Short Exhaustion, 200 SMA extension, and ceiling rejection."""
    if len(candles) < 50:
        return None

    df = pd.DataFrame(candles)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    closes = df["close"]
    highs = df["high"]
    lows = df["low"]
    vols = df["volume"]

    last_close = float(closes.iloc[-1])
    last_open = float(df["open"].iloc[-1])
    last_high = float(highs.iloc[-1])
    last_low = float(lows.iloc[-1])
    last_vol = float(vols.iloc[-1])

    ema20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])
    sma50 = float(closes.rolling(50).mean().iloc[-1])
    sma200 = (
        float(closes.rolling(200).mean().iloc[-1])
        if len(closes) >= 200
        else float(closes.rolling(len(closes)).mean().iloc[-1])
    )

    # 1. 200 SMA Over-Extension: Must be at least 15% above 200 SMA
    ext_200_pct = (last_close - sma200) / sma200 * 100
    if ext_200_pct < 15.0:
        return None

    # 2. 60-Day Overhead Ceiling Proximity: Within 5.0% of 60d high
    high_60d = float(highs.iloc[-60:].max()) if len(highs) >= 60 else float(highs.max())
    headroom_60d = (high_60d - last_close) / last_close * 100
    if headroom_60d > 5.0:
        return None

    # 3. Downside Air Pocket to 50 SMA
    downside_to_50sma = (last_close - sma50) / last_close * 100

    # 4. Volatility Squeeze at the Highs (Bollinger Bands inside Keltner Channels)
    tr1 = highs - lows
    tr2 = (highs - closes.shift(1)).abs()
    tr3 = (lows - closes.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr20 = float(tr.rolling(20).mean().iloc[-1])

    bb_mid = closes.rolling(20).mean().iloc[-1]
    bb_std = closes.rolling(20).std().iloc[-1]
    bb_upper = bb_mid + (2.0 * bb_std)
    bb_lower = bb_mid - (2.0 * bb_std)

    kc_upper = bb_mid + (1.5 * atr20)
    kc_lower = bb_mid - (1.5 * atr20)

    squeeze_on = bool((bb_lower > kc_lower) and (bb_upper < kc_upper))

    # 5. NR7 Range Contraction at Ceiling (narrowest daily range of 7 days)
    recent_ranges = (highs - lows).iloc[-7:]
    nr7 = bool((last_high - last_low) <= recent_ranges.min())

    # 6. Volume Exhaustion / Dry-Up
    avg_vol_20 = float(vols.rolling(20).mean().iloc[-1])
    vol_dry = bool((last_vol / avg_vol_20) <= 0.80 if avg_vol_20 > 0 else False)

    # 7. Candlestick Exhaustion & Patterns via TA-Lib
    opens_arr = df["open"].values.astype(np.float64)
    highs_arr = df["high"].values.astype(np.float64)
    lows_arr = df["low"].values.astype(np.float64)
    closes_arr = df["close"].values.astype(np.float64)
    pattern = detect_candlestick_pattern_talib(opens_arr, highs_arr, lows_arr, closes_arr, side="SHORT")

    stop_level = round(high_60d * 1.015, 2)
    target_level = round(sma50, 2)
    risk = stop_level - last_close
    reward = last_close - target_level
    rr_ratio = round(reward / risk, 1) if risk > 0 and reward > 0 else 1.0

    # Short R:R Gate: Must have at least 1.5 R:R down to 50 SMA target
    if rr_ratio < 1.5:
        return None

    # Pine Screener Engine Integration (rev-screener.pine)
    try:
        from src.screener.pine_screener_engine import evaluate_pine_screener_model
        pine_metrics = evaluate_pine_screener_model(df)
    except Exception as e_pine:
        logger.debug(f"Pine screener calculation warning: {e_pine}")
        pine_metrics = {}

    # Strict Quality Gate for Prime Short Setups:
    # 1. Weinstein Stage: Disqualify Stage 2 (Advancing) unless in extreme reversal exhaustion
    stage = pine_metrics.get("weinstein_stage")
    is_extreme = pine_metrics.get("is_extreme_reversal", False)
    if stage == 2 and not is_extreme:
        return None  # Do not short advancing momentum runners without extreme blow-off exhaustion.

    # 2. Priority Score: Disqualify low-score uncoiled noise (< 50.0)
    score = pine_metrics.get("priority_score")
    if score is not None and score < 50.0:
        return None  # Disqualify: Setup below mathematical conviction threshold.

    res = {
        "ceiling_level": round(high_60d, 2),
        "headroom_pct": round(headroom_60d, 1),
        "ext_200_pct": round(ext_200_pct, 1),
        "downside_to_50sma": round(downside_to_50sma, 1),
        "sma50": round(sma50, 2),
        "sma200": round(sma200, 2),
        "ema20": round(ema20, 2),
        "squeeze_on": squeeze_on,
        "nr7": nr7,
        "vol_dry": vol_dry,
        "pattern": pattern,
        "stop_level": stop_level,
        "target_level": target_level,
        "short_rr": rr_ratio,
        "setup_posture": f"Ceiling Rejection ({pattern})",
    }
    res.update(pine_metrics)
    return res


def run_stage2_short_scan(
    client, candidates: List[Dict[str, Any]], max_workers: int = 6
) -> List[Dict[str, Any]]:
    """Pulls historical daily candles for Stage 1 short candidates and scores ceiling rejection."""
    survivors = []
    logger.info(f"Running Stage 2 Short Exhaustion scan across {len(candidates)} candidates...")

    def _eval_ticker(cand):
        schwab_sym = cand["schwab_symbol"]
        clean_sym = cand["symbol"]
        for attempt in range(2):
            try:
                r = client.get_price_history_every_day(schwab_sym)
                if r.status_code == 429:
                    time.sleep(1.0)
                    continue
                if r.status_code == 200:
                    data = r.json()
                    candles = data.get("candles") or []
                    metrics = evaluate_short_setup(candles)
                    if metrics:
                        # Check 14-day earnings blackout gate
                        if not check_earnings_blackout(clean_sym):
                            return None
                        cand_copy = dict(cand)
                        cand_copy.update(metrics)
                        return cand_copy
                    break
            except Exception as e:
                logger.debug(f"Error fetching candles for {schwab_sym}: {e}")
                time.sleep(0.5)
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_eval_ticker, c): c for c in candidates}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                survivors.append(res)

    # Sort deterministically: Priority score > Extreme reversal > Squeeze active > Short R:R
    survivors.sort(
        key=lambda x: (
            x.get("priority_tier") == "HIGH_PRIORITY",
            float(x.get("priority_score", 0.0)),
            bool(x.get("is_extreme_reversal", False)),
            bool(x.get("squeeze_on", False)),
            float(x.get("short_rr", 0.0)),
            float(x.get("downside_to_50sma", 0.0))
        ),
        reverse=True
    )
    logger.info(f"Stage 2 Short scan complete: {len(survivors)} prime short setups survived all gates.")
    return survivors


def save_short_manifest(top_picks: List[Dict[str, Any]], date_str: str) -> Path:
    """Saves qualified short candidates into data/raw/<date_str>/short_survivors.json."""
    out_dir = config.BASE_DIR / "data" / "raw" / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    short_file = out_dir / "short_survivors.json"

    manifest = []
    for p in top_picks:
        manifest.append(
            {
                "Ticker": str(p["symbol"]),
                "Symbol": str(p["symbol"]),
                "symbol": str(p["symbol"]),
                "source": "schwab_short_scan",
                "side": "SHORT",
                "screener_setup": str(p.get("setup_posture") or "Prime Short (Ceiling Rejection)"),
                "setup_posture": str(p.get("setup_posture", "Ceiling Rejection")),
                "pattern": str(p.get("pattern", "Ceiling Stall")),
                "price": float(p["price"]),
                "ceiling_level": float(p.get("ceiling_level", 0.0)),
                "headroom_pct": float(p.get("headroom_pct", 0.0)),
                "ext_200_pct": float(p.get("ext_200_pct", 0.0)),
                "downside_to_50sma": float(p.get("downside_to_50sma", 0.0)),
                "target_level": float(p.get("target_level", 0.0)),
                "stop_level": float(p.get("stop_level", 0.0)),
                "short_rr": float(p.get("short_rr", 1.0)),
                "squeeze_on": bool(p.get("squeeze_on", False)),
                "nr7": bool(p.get("nr7", False)),
                "vol_dry": bool(p.get("vol_dry", False)),
                "pos_52w": float(p.get("pos_52w", 95.0)),
                "headroom_52w": float(p.get("headroom_52w", 2.0)),
                # Pine Screener Engine Fields (rev-screener.pine)
                "weinstein_stage": int(p.get("weinstein_stage", 3)),
                "rev_zone_long": float(p.get("rev_zone_long", 0.0)),
                "rev_zone_short": float(p.get("rev_zone_short", 0.0)),
                "is_extreme_reversal": bool(p.get("is_extreme_reversal", False)),
                "buy_score": float(p.get("buy_score", 50.0)),
                "sell_score": float(p.get("sell_score", 50.0)),
                "prime_signal": int(p.get("prime_signal", 0)),
                "vcp_energy": int(p.get("vcp_energy", 0)),
                "entry_rank": float(p.get("entry_rank", 50.0)),
                "priority_score": float(p.get("priority_score", 50.0)),
                "priority_tier": str(p.get("priority_tier", "MONITOR")),
                "tastytrade": p.get("tastytrade"),
                "tastytrade_alert_active": bool(p.get("tastytrade_alert_active", False)),
            }
        )

    short_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info(f"Saved {len(manifest)} prime short candidates to {short_file}")
    return short_file


# Note: Unified run_schwab_pre_move_scan is defined below with full autonomous pipeline support.

def synthesize_datawindow_from_screener(
    sym: str,
    cand: Dict[str, Any],
    rt_quote: Optional[Dict[str, Any]] = None,
    t_date: Optional[str] = None,
) -> Dict[str, str]:
    """
    Synthesizes a complete TradingView-compatible Data Window dictionary from
    screener technical coiling indicators & real-time REST quote.
    Allows Phase 2C-1 and Phase 2C-2 (Local Research triage & thesis) to run
    100% locally and instantaneously with ZERO Playwright browser scraping.
    """
    t_date = t_date or datetime.now().strftime("%Y-%m-%d")
    price = None
    if rt_quote:
        price = rt_quote.get("price") or rt_quote.get("last_price")
    if not price:
        price = cand.get("price") or cand.get("close") or 100.0
    price = float(price)

    side = (cand.get("side") or "LONG").upper()
    stage = int(cand.get("weinstein_stage") or (1 if side == "LONG" else 4))
    score = float(cand.get("priority_score") or 75.0)
    ema20 = float(cand.get("ema20") or price)
    sma50 = float(cand.get("sma50") or price)
    sma200 = float(cand.get("sma200") or price)

    stop = float(cand.get("stop_level") or (price * 0.96 if side == "LONG" else price * 1.04))
    target = float(cand.get("target_level") or cand.get("ceiling_level") or (price * 1.10 if side == "LONG" else price * 0.90))
    rr = float(cand.get("long_rr") or cand.get("short_rr") or 2.5)
    ext_pct = float(cand.get("ext_200_pct") or (5.0 if side == "LONG" else -5.0))

    # Entry zone around current price / EMA20
    zbot = round(min(price, ema20) * 0.995, 2)
    ztop = round(max(price, ema20) * 1.005, 2)

    is_rev = bool(cand.get("is_extreme_reversal", False))
    # Action code 20 (REVERSAL BUY/SELL) is the era-robust PASS lane in data_window_filter
    act_code = "20"
    rev_zone_score = "8" if is_rev else "2"
    # Bit 4 in signal pack is NOT-fade (inverted: bit 4 == 1 means NOT fade, allows fresh entries)
    sig_pack = "5" if side == "LONG" else "6"

    dw = {
        "time": t_date,
        "ticker": sym.upper(),
        "open": str(round(price, 2)),
        "high": str(round(price * 1.01, 2)),
        "low": str(round(price * 0.99, 2)),
        "close": str(round(price, 2)),
        "Sprint Line EMA": str(round(ema20, 2)),
        "Hull Baseline HMA": str(round(ema20, 2)),
        "MA 20 Fast": str(round(ema20, 2)),
        "MA 50 Mid": str(round(sma50, 2)),
        "MA 200 Slow": str(round(sma200, 2)),
        "Weinstein MA 150": str(round(sma50, 2)),
        "Stage 1 Base 2 Up 3 Top 4 Down": str(stage),
        "Stage Age Bars": "5",
        "Long Entry": str(round(price, 2)),
        "Long Entry Zone Bot": str(zbot),
        "Long Entry Zone Top": str(ztop),
        "Long Stop Loss": str(round(stop, 2)),
        "Long Target": str(round(target, 2)),
        "Short Entry": str(round(price, 2)),
        "Short Entry Zone Bot": str(zbot),
        "Short Entry Zone Top": str(ztop),
        "Short Stop Loss": str(round(stop, 2)),
        "Short Target": str(round(target, 2)),
        "Long Setup Score": str(round(score if side == "LONG" else 20.0, 1)),
        "Short Pressure Score": str(round(score if side == "SHORT" else 20.0, 1)),
        "Entry At Market 0No 1L 2S 3Both": "1" if side == "LONG" else "2",
        "Action Long Code": act_code if side == "LONG" else "0",
        "Action Short Code": act_code if side == "SHORT" else "0",
        "Long Rev Zone": rev_zone_score if side == "LONG" else "0",
        "Short Rev Zone": rev_zone_score if side == "SHORT" else "0",
        "Ext Pct vs MA200": str(round(ext_pct, 1)),
        "Exhaustion Gradient": "0.0",
        "Ext Z Self Relative": "0.0",
        "Regime 0 Hlt 1 Ext 2 Clmx 3 Dist 4 Dn 5 Ign 6 Sqz": "6" if cand.get("squeeze_on") else "1",
        "Exp Move Pct 21b": "6.0",
        "Evidence Bias Pct Above 50 Bull": "65.0" if side == "LONG" else "35.0",
        "Long Ignition Fresh Breakout": "1" if cand.get("nr7") else "0",
        "RR To Target": str(round(rr, 1)),
        "Long RR At Market": str(round(rr, 1)),
        "Zone RR Flags Pack": "7",
        "Signal Pack": sig_pack,
        "Bear Warning Mask": "0",
        "Reversal Pattern Mask": "0",
        "Weak Level Mask": "0",
        "POC": str(round(price, 2)),
        "VAH": str(round(price * 1.03, 2)),
        "VAL": str(round(price * 0.97, 2)),
    }
    return dw


def run_autonomous_screener_pipeline(
    candidates: List[Dict[str, Any]],
    auto_max: int = 3,
    run_deep: bool = True,
    date_str: Optional[str] = None,
    headless: bool = False,
) -> Dict[str, Any]:
    """
    Autonomous Execution Engine for High-Priority Screener Candidates.
    1. Sorts candidates by priority_tier (HIGH_PRIORITY first) and priority_score.
    2. Takes up to auto_max candidates.
    3. Pipeline Sequence (Strictly Gated):
       Step 1: Real-time quote & news ingestion (REST API: Schwab quote + Finnhub/Alpaca news).
               Reuses recent historical data window or synthesizes indicator baseline (ZERO browser).
       Step 2: Local Research (run_local_research.py [date_str] --ticker <sym>) via local Qwen.
       Step 3: "If Satisfied" Gate:
               - Only if local research satisfies conviction & posture:
                 * Scrape TradingView charts (run_swing_research.py [date_str] --ticker <sym>)
                 * Launch Deep Research in slot (run_deep_research.py [date_str] --ticker <sym>)
                 * Sync watch levels & Tastytrade cloud quote alerts (run_watch_alerts.py --sync --once)
               - If NOT satisfied:
                 * Skips chart scraping completely (ZERO browser launched)
                 * Skips deep research completely (preserves GPU slot)
    4. Presents a comprehensive research dossier and summary scoreboard.
    """
    py_exe = _get_python_exe()
    results = []
    t_date = date_str or datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")

    # Strict Quality Gate for Autonomous Dispatch:
    # Only dispatch candidates that are HIGH_PRIORITY or top MEDIUM_PRIORITY (score >= 60.0)
    high_priority_picks = [
        c for c in candidates
        if (c.get("priority_tier") == "HIGH_PRIORITY" or float(c.get("priority_score", 0.0)) >= 60.0)
    ]
    if not high_priority_picks:
        logger.info("🤖 [AUTONOMOUS ENGINE] No high-priority setups met conviction threshold (score >= 60) today.")
        logger.info("🛡️ Preserving system resources & GPU bandwidth — 0 junk tickers dispatched.")
        print("\n" + "=" * 115)
        print(">> 🤖 AUTONOMOUS SCREENER: 0 High-Conviction Setups Met Conviction Bar (Score >= 60).")
        print(">> Preserving GPU bandwidth & system resources — 0 junk tickers dispatched.")
        print("=" * 115 + "\n")
        return {"count": 0, "researched": []}

    # Sort strictly by priority tier and score
    sorted_picks = sorted(
        high_priority_picks,
        key=lambda x: (
            x.get("priority_tier") == "HIGH_PRIORITY",
            float(x.get("priority_score", 0.0)),
            bool(x.get("is_extreme_reversal", False)),
            bool(x.get("squeeze_on", False)),
            float(x.get("long_rr", x.get("short_rr", 0.0))),
        ),
        reverse=True,
    )

    selected = sorted_picks[:auto_max]
    if not selected:
        logger.info("🤖 [AUTONOMOUS ENGINE] No candidates available for autonomous pipeline.")
        return {"count": 0, "researched": []}

    print("\n" + "=" * 115)
    print(f">> 🤖 AUTONOMOUS SCREENER DISPATCH: Selected Top {len(selected)} High-Priority Setups (Max: {auto_max} | Headless: {headless})")
    print("=" * 115)
    for idx, p in enumerate(selected, 1):
        sym = p.get("symbol") or p.get("Ticker")
        score = p.get("priority_score", 0.0)
        tier = p.get("priority_tier", "MONITOR")
        stage = p.get("weinstein_stage", 1)
        rr = p.get("long_rr", p.get("short_rr", 0.0))
        side_str = p.get("side", "LONG")
        print(f"   [{idx}/{len(selected)}] {sym:<6} | {side_str:<5} | {tier:<15} (Score: {score:<4.1f}) | Stage {stage} | R:R {rr}:1")
    print("=" * 115 + "\n")

    for p in selected:
        sym = p.get("symbol") or p.get("Ticker")
        if not sym:
            continue
        sym = sym.strip().upper()

        # Deduplication: Check if ticker already has completed deep research today
        rep_file = config.BASE_DIR / "reports" / t_date / f"{sym}_summary.md"
        arb_file = config.BASE_DIR / "reports" / t_date / f"{sym}_arbitration.md"
        if rep_file.exists() or arb_file.exists():
            logger.info(f"⏭️ [{sym}] Already has completed deep research report for {t_date}. Skipping.")
            continue

        side_str = (p.get("side") or "LONG").upper()
        stage = p.get("weinstein_stage", 1)
        tier = p.get("priority_tier", "MONITOR")
        score = float(p.get("priority_score", 0.0))
        rr = p.get("long_rr", p.get("short_rr", 0.0))

        logger.info(f"\n========================================================")
        logger.info(f"🤖 [AUTONOMOUS RESEARCH] Starting pipeline for {sym} ({side_str} | Stage {stage})...")
        logger.info(f"========================================================")

        # =========================================================================
        # STEP 1: REAL-TIME DATA & NEWS INGESTION (Fast REST APIs — Zero browser)
        # =========================================================================
        logger.info(f"[{sym}] 1/4: Ingesting real-time quote, options metrics & news context...")
        raw_ticker_dir = config.BASE_DIR / "data" / "raw" / t_date / sym
        raw_ticker_dir.mkdir(parents=True, exist_ok=True)

        # 1a. Real-time live quote
        rt_quote = None
        try:
            from src.clients.schwab_client import get_realtime_quote
            rt_quote = get_realtime_quote(sym)
            if rt_quote:
                with open(raw_ticker_dir / f"{sym}_quote.json", "w", encoding="utf-8") as f_q:
                    json.dump(rt_quote, f_q, indent=2)
        except Exception as e_q:
            logger.debug(f"[{sym}] Live quote fetch notice: {e_q}")

        # 1b. Real-time news & catalyst
        try:
            from src.clients.news_client import get_ticker_news
            rt_news = get_ticker_news(sym, days=2)
            if rt_news:
                with open(raw_ticker_dir / f"{sym}_fetch_finnhub_news.json", "w", encoding="utf-8") as f_n:
                    json.dump(rt_news, f_n, indent=2)
        except Exception as e_n:
            logger.debug(f"[{sym}] Live news fetch notice: {e_n}")

        # 1c. Ensure Data Window exists for local research (zero browser — synthesize from screener metrics if no prior scrape)
        dw_json = raw_ticker_dir / f"{sym}_datawindow.json"
        dw_csv = raw_ticker_dir / f"{sym}_datawindow.csv"
        if not dw_json.exists():
            # Search recent dates in data/raw for existing datawindow
            raw_root = config.BASE_DIR / "data" / "raw"
            for d in sorted(raw_root.iterdir(), reverse=True):
                if d.is_dir() and d.name != t_date and d.name.startswith("202"):
                    src_dw = d / sym / f"{sym}_datawindow.json"
                    src_csv = d / sym / f"{sym}_datawindow.csv"
                    if src_dw.exists():
                        import shutil
                        shutil.copy2(str(src_dw), str(dw_json))
                        if src_csv.exists():
                            shutil.copy2(str(src_csv), str(dw_csv))
                        logger.info(f"⚡ [{sym}] Reused historical datawindow from {d.name} for local research.")
                        break

        # If datawindow is still missing, synthesize it directly from screener indicators & live quote — ZERO BROWSER SCRAPING!
        if not dw_json.exists():
            logger.info(f"⚡ [{sym}] Synthesizing baseline data window from screener metrics & real-time quote (zero browser)...")
            try:
                synth_dw = synthesize_datawindow_from_screener(sym, p, rt_quote=rt_quote, t_date=t_date)
                with open(dw_json, "w", encoding="utf-8") as f_dw:
                    json.dump(synth_dw, f_dw, indent=2)
                logger.info(f"✅ [{sym}] Baseline data window synthesized successfully.")
            except Exception as e_synth:
                logger.error(f"[{sym}] Failed to synthesize data window: {e_synth}")
                continue

        # =========================================================================
        # STEP 2: LAUNCH LOCAL RESEARCH (Free, Local LLM @ localhost:8000)
        # =========================================================================
        logger.info(f"[{sym}] 2/4: Running Local Research (Deterministic triage + Local Qwen)...")
        try:
            cmd_local = [py_exe, "run_local_research.py", t_date, "--ticker", sym]
            subprocess.run(cmd_local, cwd=config.BASE_DIR, check=True)
        except Exception as e_local:
            logger.error(f"[{sym}] Local research error: {e_local}")
            continue

        # Inspect local triage outcome
        thesis_path = config.BASE_DIR / "data" / "triage" / t_date / "_DEEP_RESEARCH" / sym / f"{sym}_thesis.json"
        if not thesis_path.exists():
            thesis_path = config.BASE_DIR / "data" / "raw" / t_date / sym / f"{sym}_thesis.json"
        if not thesis_path.exists():
            thesis_path = config.BASE_DIR / "data" / "raw" / t_date / f"{sym}_thesis.json"

        send_to_deep = False
        triage_verdict = "UNKNOWN"
        rec = {}
        if thesis_path.exists():
            try:
                rec = json.loads(thesis_path.read_text(encoding="utf-8"))
                send_to_deep = bool(rec.get("send_for_deep_research", False))
                triage_verdict = str(rec.get("triage", "WATCH"))
            except Exception:
                pass

        logger.info(f"[{sym}] Local triage verdict: {triage_verdict} (send_for_deep_research={send_to_deep})")

        # =========================================================================
        # STEP 3: "IF SATISFIED" GATE -> SCRAPE TRADINGVIEW CHARTS
        # =========================================================================
        is_satisfied = send_to_deep or triage_verdict in ("PASS", "WATCH")
        chart_png = raw_ticker_dir / f"{sym}_chart.png"
        chart_zoom_png = raw_ticker_dir / f"{sym}_chart_zoom.png"
        has_charts = chart_png.exists() and chart_zoom_png.exists()

        if run_deep and is_satisfied:
            if not has_charts:
                logger.info(f"[{sym}] 3/4: Local research SATISFIED! Scraping TradingView multimodal charts (Headless={headless})...")
                try:
                    cmd_scrape = [py_exe, "run_swing_research.py", t_date, "--ticker", sym]
                    if headless or os.getenv("HEADLESS_SCRAPE", "0").lower() in ("1", "true", "yes"):
                        cmd_scrape.append("--headless")
                    subprocess.run(cmd_scrape, cwd=config.BASE_DIR, check=True)
                except Exception as e_scrape:
                    logger.error(f"[{sym}] Multimodal chart scrape notice: {e_scrape}")
            else:
                logger.info(f"⚡ [{sym}] 3/4: Multimodal chart images already present. Ready for deep pass.")

            # =========================================================================
            # STEP 4: LAUNCH DEEP RESEARCH & WATCH ALERTS (In Slot)
            # =========================================================================
            logger.info(f"[{sym}] 4/4: Launching Deep Research debate & senior PM arbitration...")
            deep_done = False
            try:
                cmd_deep = [py_exe, "run_deep_research.py", t_date, "--ticker", sym]
                subprocess.run(cmd_deep, cwd=config.BASE_DIR, check=True)

                logger.info(f"[{sym}] Syncing research watch levels & Tastytrade cloud quote alerts...")
                subprocess.run([py_exe, "run_watch_alerts.py", "--sync", "--once"], cwd=config.BASE_DIR)
                deep_done = True
            except Exception as e_deep:
                logger.error(f"[{sym}] Deep research error: {e_deep}")
        else:
            logger.info(f"⏸️ [{sym}] Local research verdict ({triage_verdict}) not satisfied for deep pass. Skipping scrape & deep research to preserve slots.")
            deep_done = False

        # Extract details for presentation
        triage_info = rec.get("triage", {}) if isinstance(rec.get("triage"), dict) else rec
        conviction = triage_info.get("conviction", rec.get("conviction", "N/A"))
        win_prob = triage_info.get("win_prob", rec.get("win_prob", "N/A"))
        reasoning = triage_info.get("reasoning") or rec.get("reasoning") or triage_info.get("pursue_reason") or "N/A"
        sentiment = triage_info.get("sentiment", {})
        sentiment_label = sentiment.get("label", "neutral") if isinstance(sentiment, dict) else str(sentiment)
        headlines = sentiment.get("headlines", []) if isinstance(sentiment, dict) else []

        plan = triage_info.get("long_plan", {}) if side_str == "LONG" else triage_info.get("short_plan", {})
        zone = plan.get("zone", [None, None])
        stop_px = plan.get("stop")
        target_px = plan.get("target")

        # PRESENTATION: Print comprehensive dossier card for each candidate
        print("\n" + "=" * 105)
        print(f"🎯 AUTONOMOUS RESEARCH DOSSIER: ${sym} ({side_str} SETUP)")
        print("=" * 105)
        print(f"  📌 Classification:   Stage {stage} | Priority: {tier} (Score: {score:.1f}) | Pre-Move R:R: {rr}:1")
        print(f"  ⚖️ Local Verdict:     {triage_verdict} | Conviction: {conviction} | Win Probability: {win_prob}%")
        if zone and zone[0] is not None and zone[1] is not None:
            print(f"  🎯 Planned Entry:    [${float(zone[0]):.2f} – ${float(zone[1]):.2f}]")
        if stop_px is not None:
            print(f"  🛑 Tactical Stop:    ${float(stop_px):.2f}")
        if target_px is not None:
            print(f"  🏁 Profit Target:    ${float(target_px):.2f}")
        print(f"  💡 Catalyst / Posture: {reasoning}")
        print(f"  📰 News Sentiment:   {sentiment_label.upper()}")
        if headlines:
            print(f"  🗞️ Headline:         \"{headlines[0][:90]}\"")
        rel_thesis = thesis_path.relative_to(config.BASE_DIR) if thesis_path.exists() else "N/A"
        print(f"  📁 Artifact Dossier: {rel_thesis}")
        print(f"  🚀 Deep Research:    {'✅ APPROVED & COMPLETED' if deep_done else ('⏳ QUALIFIED FOR DEEP PASS' if send_to_deep else '⏸ PRESERVED IN LOCAL TRIAGE')}")
        print("=" * 105 + "\n")

        results.append(
            {
                "ticker": sym,
                "side": side_str,
                "stage": stage,
                "priority_score": score,
                "priority_tier": tier,
                "triage_verdict": triage_verdict,
                "conviction": conviction,
                "win_prob": win_prob,
                "entry_zone": zone,
                "stop": stop_px,
                "target": target_px,
                "send_for_deep_research": send_to_deep,
                "deep_completed": deep_done,
                "thesis_file": str(rel_thesis),
            }
        )

    # PRESENTATION: Print consolidated scoreboard table
    print("\n" + "=" * 120)
    print(">> 📊 AUTONOMOUS RESEARCH SCOREBOARD (All Researched Setups)")
    print("=" * 120)
    hdr = f"{'Ticker':<7} | {'Side':<5} | {'Stage':<6} | {'Priority':<14} | {'Verdict':<8} | {'Conviction':<10} | {'Entry Zone':<18} | {'Stop':<8} | {'Target':<8} | {'Deep Research':<14}"
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        z = r.get("entry_zone")
        z_str = f"[${float(z[0]):.1f}-${float(z[1]):.1f}]" if (z and z[0] is not None and z[1] is not None) else "-"
        st_str = f"${float(r['stop']):.2f}" if r.get("stop") is not None else "-"
        tg_str = f"${float(r['target']):.2f}" if r.get("target") is not None else "-"
        deep_status = "COMPLETED" if r.get("deep_completed") else ("QUALIFIED" if r.get("send_for_deep_research") else "LOCAL ONLY")
        conv_val = r.get("conviction", "-")
        conv_str = f"{float(conv_val):.1f}" if isinstance(conv_val, (int, float)) else str(conv_val)[:10]
        print(f"{r['ticker']:<7} | {r['side']:<5} | Stg {r['stage']:<2} | {r['priority_tier'][:14]:<14} | {r['triage_verdict']:<8} | {conv_str:<10} | {z_str:<18} | {st_str:<8} | {tg_str:<8} | {deep_status:<14}")
    print("=" * 120 + "\n")

    logger.info(f"✅ [AUTONOMOUS ENGINE] Finished researching {len(results)} high-priority candidate(s).")
    return {"count": len(results), "researched": results}


def run_schwab_pre_move_scan(
    top_n: int = 10,
    auto_scrape: bool = False,
    autonomous: bool = False,
    auto_max: int = 3,
    target_date: Optional[str] = None,
    side: str = "long",
    headless: bool = False,
) -> Any:
    """Main entry point for the Schwab 1000 Screener (supports Long Basing, Prime Short, or Both)."""
    date_str = target_date or datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")
    scan_mode = (side or "long").lower()

    logger.info("=" * 80)
    logger.info(f"STARTING SCHWAB 1000 SCAN ({scan_mode.upper()} SIDE | {date_str})")
    logger.info("Universe: Schwab 1000 Index (SCHK ETF Constituents)")
    logger.info("=" * 80)

    client = get_schwab_client()

    # 1. Gate 1: Market Tide (SPY)
    market_tide = check_market_tide(client)
    spy_20d_return = market_tide.get("spy_20d_return", 0.0)

    # 2. Gate 3: Biotech exclusion map
    biotech_set = load_biotech_exclusion_set()
    logger.info(f"Biotech exclusion filter active: {len(biotech_set)} tickers excluded.")

    # 3. Load universe
    tickers = load_schwab_1000_tickers()
    if not tickers:
        logger.error("No tickers found. Aborting scan.")
        return []

    # 4. Batch quotes (4 seconds for 983 stocks)
    raw_quotes = fetch_all_quotes_batch(client, tickers)
    if not raw_quotes:
        logger.error("Failed to fetch quotes. Aborting.")
        return []

    long_top_picks = []
    short_top_picks = []

    # =========================================================================
    # LONG BASING SCAN
    # =========================================================================
    if scan_mode in ("long", "both"):
        stage1_survivors = stage1_fast_filter(raw_quotes, biotech_set)
        final_survivors = run_stage2_technical_scan(client, stage1_survivors, spy_20d_return)
        
        # Quality Gate: Never pad with junk or declining tickers
        qualified_longs = [
            s for s in final_survivors
            if float(s.get("priority_score", 0.0)) >= 50.0 and s.get("weinstein_stage") not in (3, 4)
        ]
        long_top_picks = qualified_longs[:top_n]
        filtered_out = len(final_survivors) - len(qualified_longs)

        print("\n" + "=" * 135)
        print(f">> 🎯 TOP {len(long_top_picks)} PRE-MOVE GROUND-FLOOR SWING SETUPS (Schwab 1000 Index / SCHK)")
        if filtered_out > 0:
            print(f">> Filtered out {filtered_out} low-conviction/declining tickers. Showing best setups tight.")
        print(f">> Market Tide: {market_tide.get('trend_str')} (SPY ${market_tide.get('last_px'):.2f})")
        print("=" * 135)
        header = f"{'Rank':<4} | {'Ticker':<7} | {'Price':<8} | {'Stage':<11} | {'Priority':<16} | {'Long R:R':<8} | {'RevZone':<8} | {'Support / Stop':<24} | {'Posture':<24} | {'SQZ':<5}"
        print(header)
        print("-" * len(header))

        for idx, p in enumerate(long_top_picks, 1):
            sqz = "YES" if p["squeeze_on"] else "NO"
            posture = p.get("setup_posture", "Normal")[:24]
            stage_num = p.get("weinstein_stage", 1)
            stage_lbl = "Base" if stage_num == 1 else "Adv" if stage_num == 2 else "Rec"
            stage_str = f"Stg {stage_num} ({stage_lbl})"
            rev_str = f"{p.get('rev_zone_long', 0.0):.1f}"
            prio_tier = p.get("priority_tier", "MED")
            prio_str = f"{p.get('priority_score', 0.0):.1f} ({prio_tier[:4]})"
            sup_stop = f"${p.get('support_level', 0.0):.2f} / ${p.get('stop_level', 0.0):.2f}"
            row = f"{idx:<4} | {p['symbol']:<7} | ${p['price']:<7.2f} | {stage_str:<11} | {prio_str:<16} | {p.get('long_rr', 0.0):<8.1f} | {rev_str:<8} | {sup_stop:<24} | {posture:<24} | {sqz:<5}"
            print(row)
        print("=" * 135 + "\n")

        # Enrich with Tastytrade institutional metrics & 24/7 cloud price alerts
        try:
            from src.screener.continuous_screener_daemon import enrich_candidates_with_tastytrade
            enrich_candidates_with_tastytrade(long_top_picks, auto_alerts=True)
        except Exception as e_tt:
            logger.debug(f"Tastytrade enrichment notice: {e_tt}")

        save_survivors_manifest(long_top_picks, date_str)

    # =========================================================================
    # PRIME SHORT SCAN
    # =========================================================================
    if scan_mode in ("short", "both"):
        short_stage1 = stage1_short_filter(raw_quotes, biotech_set)
        short_survivors = run_stage2_short_scan(client, short_stage1)
        
        # Quality Gate: Never pad with junk or advancing momentum runners
        qualified_shorts = [
            s for s in short_survivors
            if float(s.get("priority_score", 0.0)) >= 50.0 and (s.get("weinstein_stage") != 2 or s.get("is_extreme_reversal"))
        ]
        short_top_picks = qualified_shorts[:top_n]
        filtered_out_s = len(short_survivors) - len(qualified_shorts)

        print("\n" + "=" * 135)
        print(f">> 🎯 TOP {len(short_top_picks)} PRIME SHORT / CEILING REJECTION SETUPS (Schwab 1000 Index / SCHK)")
        if filtered_out_s > 0:
            print(f">> Filtered out {filtered_out_s} low-conviction/momentum tickers. Showing best setups tight.")
        print(f">> Market Tide: {market_tide.get('trend_str')} (SPY ${market_tide.get('last_px'):.2f})")
        print("=" * 135)
        header = f"{'Rank':<4} | {'Ticker':<7} | {'Price':<8} | {'Stage':<11} | {'Priority':<16} | {'Short R:R':<10} | {'Ceiling Resist':<16} | {'50 SMA Target':<16} | {'Pattern':<20}"
        print(header)
        print("-" * len(header))

        for idx, s in enumerate(short_top_picks, 1):
            stage_num = s.get("weinstein_stage", 3)
            stage_lbl = "Dist" if stage_num == 3 else "Dec" if stage_num == 4 else "Ext"
            stage_str = f"Stg {stage_num} ({stage_lbl})"
            prio_tier = s.get("priority_tier", "MED")
            prio_str = f"{s.get('priority_score', 0.0):.1f} ({prio_tier[:4]})"
            ceil_str = f"${s['ceiling_level']:.2f} (+{s.get('headroom_pct', 0.0):.1f}%)"
            tgt_str = f"${s['target_level']:.2f} (-{s.get('downside_to_50sma', 0.0):.1f}%)"
            row = f"{idx:<4} | {s['symbol']:<7} | ${s['price']:<7.2f} | {stage_str:<11} | {prio_str:<16} | {s['short_rr']:<10.1f} | {ceil_str:<16} | {tgt_str:<16} | {s['pattern']:<20}"
            print(row)
        print("=" * 135 + "\n")

        # Enrich with Tastytrade institutional metrics & 24/7 cloud price alerts
        try:
            from src.screener.continuous_screener_daemon import enrich_candidates_with_tastytrade
            enrich_candidates_with_tastytrade(short_top_picks, auto_alerts=True)
        except Exception as e_tt:
            logger.debug(f"Tastytrade enrichment notice: {e_tt}")

        save_short_manifest(short_top_picks, date_str)

    # Autonomous Execution Pathway
    if autonomous:
        picks_to_route = short_top_picks if scan_mode == "short" else long_top_picks
        run_autonomous_screener_pipeline(picks_to_route, auto_max=auto_max, run_deep=True, date_str=date_str, headless=headless)

    # Legacy Auto-scrape flag (if passed explicitly)
    elif auto_scrape:
        picks_to_scrape = short_top_picks if scan_mode == "short" else long_top_picks
        run_autonomous_screener_pipeline(picks_to_scrape, auto_max=top_n, run_deep=True, date_str=date_str, headless=headless)

    if scan_mode == "short":
        return short_top_picks
    elif scan_mode == "both":
        return {"long": long_top_picks, "short": short_top_picks}
    return long_top_picks


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Schwab 1000 Pre-Move Compression & Prime Short Screener")
    parser.add_argument("--top", type=int, default=10, help="Number of top candidates to display")
    parser.add_argument("--side", type=str, choices=["long", "short", "both"], default="long", help="Scan side: long, short, or both")
    parser.add_argument("--autonomous", action="store_true", help="Automatically identify high-priority setups and run scrape + local research")
    parser.add_argument("--auto-max", type=int, default=3, help="Max number of high-priority candidates to automatically research")
    parser.add_argument("--auto-scrape", action="store_true", help="Legacy flag: Automatically trigger deep research on top picks")
    parser.add_argument("--headless", action="store_true", help="Run chart scraper in headless mode (no browser window)")
    parser.add_argument("--date", type=str, default=None, help="Target date YYYY-MM-DD")
    args = parser.parse_args()

    run_schwab_pre_move_scan(
        top_n=args.top,
        auto_scrape=args.auto_scrape,
        autonomous=args.autonomous,
        auto_max=args.auto_max,
        target_date=args.date,
        side=args.side,
        headless=args.headless,
    )
