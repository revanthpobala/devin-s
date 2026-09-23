"""
src/screener/pine_screener_engine.py

Pure Python port of TradingView's rev-screener.pine (by Revanth Pobala).
Calculates exact mathematical indicators from historical daily bars:
  1. Weinstein Stage Detection (Stages 1-5 via 150 HMA slope + acceleration)
  2. Connors Mean Reversion Reversal Zone (Rev Zone Long / Short 0-10, Z1/Z0 extreme >= 7)
  3. Statistical Bayesian Sigma Engine (Z-scores for Vol, RSI, Velocity, Elasticity + Bayesian Posteriors)
  4. Volatility Squeeze & VCP Kinetic Energy (Bollinger Bands inside Keltner Channels + Linreg)
  5. Proxy Risk:Reward (Monotone cross-sectional factor vs 60d resistance & 10d swing low)
  6. Entry Rank (0-100 cross-sectional ranking factor)
  7. Put-Sell Timing Gate (Stage 2 + HV20 Low + Z-Velocity > 0)
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

try:
    import talib
    HAS_TALIB = True
except ImportError:
    talib = None
    HAS_TALIB = False

logger = logging.getLogger(__name__)


# =============================================================================
# Vectorized Technical Indicator Primitives
# =============================================================================

def _wma(series: pd.Series, period: int) -> pd.Series:
    """Weighted Moving Average (WMA)."""
    if len(series) < period:
        return pd.Series(np.nan, index=series.index)
    if HAS_TALIB:
        res = talib.WMA(series.values.astype(np.float64), timeperiod=period)
        return pd.Series(res, index=series.index)
    weights = np.arange(1, period + 1)
    denom = weights.sum()
    return series.rolling(period).apply(lambda p: np.dot(p, weights) / denom, raw=True)


def _hma(series: pd.Series, period: int) -> pd.Series:
    """Hull Moving Average (HMA = WMA(2*WMA(n/2) - WMA(n), sqrt(n)))."""
    half_len = int(period / 2)
    sqrt_len = int(math.sqrt(period))
    wma_half = _wma(series, half_len)
    wma_full = _wma(series, period)
    diff = 2.0 * wma_half - wma_full
    return _wma(diff, sqrt_len)


def _ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average (EMA)."""
    if HAS_TALIB:
        res = talib.EMA(series.values.astype(np.float64), timeperiod=period)
        return pd.Series(res, index=series.index)
    return series.ewm(span=period, adjust=False).mean()


def _sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average (SMA)."""
    if HAS_TALIB:
        res = talib.SMA(series.values.astype(np.float64), timeperiod=period)
        return pd.Series(res, index=series.index)
    return series.rolling(period).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (RSI)."""
    if HAS_TALIB:
        res = talib.RSI(series.values.astype(np.float64), timeperiod=period)
        return pd.Series(res, index=series.index)
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs)).fillna(100.0)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range (ATR)."""
    if HAS_TALIB:
        res = talib.ATR(
            high.values.astype(np.float64),
            low.values.astype(np.float64),
            close.values.astype(np.float64),
            timeperiod=period,
        )
        return pd.Series(res, index=close.index)
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def _calc_z(series: pd.Series, length: int) -> pd.Series:
    """Z-Score: (value - mean) / stdev over rolling window."""
    mean = series.rolling(length).mean()
    std = series.rolling(length).std()
    z = (series - mean) / std.replace(0, np.nan)
    return z.fillna(0.0)


def _linreg_last(series: pd.Series, length: int) -> float:
    """Linear regression value at current bar (slope * length + intercept)."""
    if len(series) < length:
        return 0.0
    vals = series.iloc[-length:].values
    if np.isnan(vals).any():
        return 0.0
    x = np.arange(length)
    x_mean = x.mean()
    y_mean = vals.mean()
    cov = np.sum((x - x_mean) * (vals - y_mean))
    var = np.sum((x - x_mean) ** 2)
    slope = cov / var if var > 0 else 0.0
    intercept = y_mean - slope * x_mean
    return float(slope * (length - 1) + intercept)


# =============================================================================
# 1. Weinstein Stage Detection (Synced to rev-screener.pine)
# =============================================================================

def calc_weinstein_stage(close: pd.Series, rsi_val: float) -> int:
    """
    Weinstein Stage Detection synced to rev-screener.pine / revanth-enhanced-indicator.pine:
      Stage 1: Basing (not above HMA150, not falling, stabilizing)
      Stage 2: Advancing (above HMA150 and rising)
      Stage 3: Distribution (above HMA150 and flat or distribution RSI)
      Stage 4: Declining (below falling HMA150, lower lows, or breakdown)
      Stage 5: Recovery (above falling HMA150 but accelerating upwards)
    """
    n = len(close)
    if n < 40:
        return 1

    hma_len = min(150, n)
    w_ma = _hma(close, hma_len)

    # Need at least 21 bars of valid HMA
    valid_hma = w_ma.dropna()
    if len(valid_hma) < 21:
        # Fallback to SMA50 slope
        sma50 = _sma(close, min(50, n))
        if len(sma50.dropna()) >= 10:
            c = float(close.iloc[-1])
            m = float(sma50.iloc[-1])
            m_prev = float(sma50.iloc[-10])
            if c > m and m > m_prev:
                return 2
            elif c < m and m < m_prev:
                return 4
            return 1
        return 1

    curr_close = float(close.iloc[-1])
    c1 = float(close.iloc[-2]) if n >= 2 else curr_close
    c5 = float(close.iloc[-6]) if n >= 6 else curr_close
    c7 = float(close.iloc[-8]) if n >= 8 else curr_close
    c10 = float(close.iloc[-11]) if n >= 11 else curr_close

    hma_curr = float(valid_hma.iloc[-1])
    hma_1 = float(valid_hma.iloc[-2])
    hma_10 = float(valid_hma.iloc[-11]) if len(valid_hma) >= 11 else float(valid_hma.iloc[0])
    hma_11 = float(valid_hma.iloc[-12]) if len(valid_hma) >= 12 else float(valid_hma.iloc[0])
    hma_20 = float(valid_hma.iloc[-21]) if len(valid_hma) >= 21 else float(valid_hma.iloc[0])

    ma_up = hma_curr > hma_10
    above = curr_close > hma_curr
    rise = ma_up and (hma_1 > hma_11)
    fall = (not ma_up) and (hma_1 < hma_11)
    flat = (not rise) and (not fall)

    w_slope = (hma_curr - hma_10) / max(hma_10, 1.0) * 100.0
    w_slope_prev = (hma_10 - hma_20) / max(hma_20, 1.0) * 100.0
    is_accelerating = (w_slope - w_slope_prev) > 0

    crash = (curr_close < c1 * 0.80) or (curr_close < c5 * 0.70)
    st2 = above and rise
    st4d = (not above) and (fall or curr_close < c10)
    st3 = above and flat
    st1 = (not above) and (not fall) and (curr_close >= c10)
    dist = st3 and (rsi_val < 45)
    sharp = ((curr_close - c7) / c7 * 100.0 < -5.0) and above
    st4_rally = above and fall and (not is_accelerating)
    st4_rec = above and fall and is_accelerating

    if crash or dist or st4d or st4_rally:
        return 4
    elif st2:
        return 2
    elif st4_rec:
        return 5
    elif sharp or st3:
        return 3
    elif st1:
        return 1
    return 1


# =============================================================================
# 2. Bayesian Conversion (Sigma -> Probability)
# =============================================================================

def calc_bayes_probability(sigma: float, stage: int, is_buy: bool = True) -> float:
    """Bayesian Score (Prior from Weinstein Stage + Likelihood from Sigma)."""
    if is_buy:
        prior = 0.44 if stage == 2 else (0.25 if stage == 5 else (-0.44 if stage == 4 else (-0.1 if stage == 3 else 0.0)))
    else:
        prior = 0.18 if stage == 4 else (-0.44 if stage in (2, 5) else (0.1 if stage == 3 else 0.0))

    post = (sigma + prior) / 2.0
    # Sigmoid logistic scaling
    prob = 100.0 / (1.0 + math.exp(-1.5 * post))
    return float(np.clip(prob, 0.0, 100.0))


# =============================================================================
# 3. Volatility Squeeze & Kinetic VCP Energy
# =============================================================================

def detect_volatility_squeeze(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    bb_len: int = 20,
    bb_mult: float = 2.0,
    kc_len: int = 20,
    kc_mult: float = 1.5,
) -> Tuple[bool, int, float]:
    """
    Bollinger Bands inside Keltner Channels + Linear Regression Momentum:
      Returns (sqz_on: bool, vcp_energy: int [-2 to 2], momentum_val: float)
      vcp_energy:
         1 = Squeeze active (coiling)
         2 = Bullish release (fired upwards)
        -2 = Bearish release (fired downwards)
         0 = No squeeze
    """
    if len(close) < max(bb_len, kc_len) + 2:
        return False, 0, 0.0

    basis = close.rolling(bb_len).mean()
    bb_dev = bb_mult * close.rolling(bb_len).std()
    upper_bb = basis + bb_dev
    lower_bb = basis - bb_dev

    atr_kc = _atr(high, low, close, kc_len)
    upper_kc = basis + kc_mult * atr_kc
    lower_kc = basis - kc_mult * atr_kc

    sqz_series = (lower_bb > lower_kc) & (upper_bb < upper_kc)
    sqz_on = bool(sqz_series.iloc[-1])
    sqz_on_prev = bool(sqz_series.iloc[-2]) if len(sqz_series) >= 2 else False
    sqz_off = not sqz_on

    # Linear regression of close - basis
    diff_basis = close - basis
    mom_val = _linreg_last(diff_basis, bb_len)

    release_dir = 0
    if sqz_off and sqz_on_prev:
        release_dir = 2 if mom_val > 0 else -2
    elif sqz_on:
        release_dir = 1

    return sqz_on, release_dir, round(mom_val, 4)


# =============================================================================
# 4. Connors Reversal Zone Score (revZoneLong / revZoneShort)
# =============================================================================

def calc_reversal_zone_scores(
    df: pd.DataFrame,
    stage: int,
    rsi2: float,
    rsi14: float,
    z_elast: float,
) -> Tuple[float, float, bool]:
    """
    Exact mathematical implementation of rev-screener.pine lines 214-266:
    Connors Mean Reversion 5-Tier Scoring:
      Tier 1: RSI(2) extreme (< 10 Long, > 90 Short) confirmed by RSI(14)
      Tier 2: Structure & Lower/Upper Bollinger Band touch
      Tier 3: Stochastic cross (K over D) & MACD crossover
      Tier 4: Consecutive down/up bars (3-6 sessions)
      Tier 5: 200 SMA trend filter
    Returns:
      (rev_long: float 0-10, rev_short: float 0-10, is_extreme_reversal: bool [>= 7.0])
    """
    if len(df) < 20:
        return 0.0, 0.0, False

    close = df["close"]
    open_p = df["open"]
    high = df["high"]
    low = df["low"]

    curr_close = float(close.iloc[-1])
    curr_open = float(open_p.iloc[-1])
    curr_high = float(high.iloc[-1])
    curr_low = float(low.iloc[-1])

    # 1. Consecutive bars
    consec_down = 0
    consec_up = 0
    n = len(df)
    for i in range(min(7, n)):
        idx = -(i + 1)
        c = float(close.iloc[idx])
        o = float(open_p.iloc[idx])
        if c < o:
            consec_down += 1
        else:
            break
    for i in range(min(7, n)):
        idx = -(i + 1)
        c = float(close.iloc[idx])
        o = float(open_p.iloc[idx])
        if c > o:
            consec_up += 1
        else:
            break

    # Tier 1: RSI extremes
    rsi_base_l = (3.0 if rsi2 < 10 else (2.0 if rsi2 < 15 else (1.0 if rsi2 < 25 else 0.0))) + \
                 (2.0 if rsi14 < 25 else (1.0 if rsi14 < 30 else 0.0))
    rsi_base_s = (3.0 if rsi2 > 90 else (2.0 if rsi2 > 85 else (1.0 if rsi2 > 75 else 0.0))) + \
                 (2.0 if rsi14 > 75 else (1.0 if rsi14 > 70 else 0.0))

    # RSI14 must confirm direction
    rsi_confirm_l = (rsi_base_l > 0) and (rsi14 < 40)
    rsi_confirm_s = (rsi_base_s > 0) and (rsi14 > 60)

    # Stage gating: no short reversals in Stage 4/5; no long reversals in Stage 3
    stage_valid_l = (stage != 3)
    stage_valid_s = (stage != 4) and (stage != 5)

    # Tier 2: Bollinger Bands
    bb_basis = _sma(close, 20)
    bb_dev = 2.0 * close.rolling(20).std()
    bb_up = float(bb_basis.iloc[-1] + bb_dev.iloc[-1])
    bb_low = float(bb_basis.iloc[-1] - bb_dev.iloc[-1])

    # Tier 3: Stochastic & MACD
    # Stoch 14, 3, 3
    lowest14 = low.rolling(14).min()
    highest14 = high.rolling(14).max()
    k_raw = 100.0 * (close - lowest14) / (highest14 - lowest14).replace(0, np.nan)
    k_val = _sma(k_raw, 3)
    d_val = _sma(k_val, 3)

    stoch_os = float(k_val.iloc[-1]) < 20.0 if not np.isnan(k_val.iloc[-1]) else False
    stoch_ob = float(k_val.iloc[-1]) > 80.0 if not np.isnan(k_val.iloc[-1]) else False
    k_cross_d = (float(k_val.iloc[-1]) > float(d_val.iloc[-1])) and (float(k_val.iloc[-2]) <= float(d_val.iloc[-2])) if len(k_val) >= 2 else False
    k_cross_d_dn = (float(k_val.iloc[-1]) < float(d_val.iloc[-1])) and (float(k_val.iloc[-2]) >= float(d_val.iloc[-2])) if len(k_val) >= 2 else False

    # MACD 12, 26, 9
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    macd_line = ema12 - ema26
    macd_sig = _ema(macd_line, 9)
    macd_bull = (float(macd_line.iloc[-1]) > float(macd_sig.iloc[-1])) and (float(macd_line.iloc[-2]) <= float(macd_sig.iloc[-2])) if len(macd_line) >= 2 else False
    macd_bear = (float(macd_line.iloc[-1]) < float(macd_sig.iloc[-1])) and (float(macd_line.iloc[-2]) >= float(macd_sig.iloc[-2])) if len(macd_line) >= 2 else False

    # Tier 5: 200 SMA
    sma200_val = float(_sma(close, min(200, n)).iloc[-1])

    # Assemble raw scores
    rev_long = 0.0
    if rsi_confirm_l and stage_valid_l:
        rev_long = (
            rsi_base_l +
            (1.5 if curr_close < bb_low else 0.0) +
            (1.5 if (stoch_os and k_cross_d) else 0.0) +
            (1.5 if macd_bull else 0.0) +
            (2.0 if z_elast < -2.0 else (1.0 if z_elast < -1.5 else 0.0)) +
            (2.0 if consec_down >= 5 else (1.0 if consec_down >= 4 else 0.0)) +
            (2.0 if curr_close > sma200_val else 0.0)
        )

    rev_short = 0.0
    if rsi_confirm_s and stage_valid_s:
        rev_short = (
            rsi_base_s +
            (1.5 if curr_close > bb_up else 0.0) +
            (1.5 if (stoch_ob and k_cross_d_dn) else 0.0) +
            (1.5 if macd_bear else 0.0) +
            (2.0 if z_elast > 2.0 else (1.0 if z_elast > 1.5 else 0.0)) +
            (2.0 if consec_up >= 5 else (1.0 if consec_up >= 4 else 0.0)) +
            (2.0 if curr_close < sma200_val else 0.0)
        )

    # KILL FILTERS (from main indicator's calcReversionScore)
    # 1. Close Location Rule: If close is in bottom 25% of bar range (long) or top 25% (short), zero out
    range_bar = curr_high - curr_low
    if range_bar > 0:
        if curr_close < (curr_low + range_bar * 0.25):
            rev_long = 0.0
        if curr_close > (curr_high - range_bar * 0.25):
            rev_short = 0.0

    # 2. RSI Power Trend: If RSI is trending AGAINST the reversal direction, zero out
    rsi_series = _rsi(close, 14)
    rsi_ma_series = _sma(rsi_series, 14)
    rsi_ma = float(rsi_ma_series.iloc[-1]) if not np.isnan(rsi_ma_series.iloc[-1]) else rsi14
    if rsi14 < 30 and rsi14 < rsi_ma:
        rev_long = 0.0
    if rsi14 > 70 and rsi14 > rsi_ma:
        rev_short = 0.0

    # 3. Z2 Floor: Scores < 4.0 are zeroed out (noise floor)
    if rev_long < 4.0:
        rev_long = 0.0
    if rev_short < 4.0:
        rev_short = 0.0

    is_extreme = (rev_long >= 7.0) or (rev_short >= 7.0)
    return round(rev_long, 1), round(rev_short, 1), is_extreme


# =============================================================================
# 5. Full Pine Screener Analysis Engine
# =============================================================================

def evaluate_pine_screener_model(
    df: pd.DataFrame,
    benchmark_df: Optional[pd.DataFrame] = None,
    side: str = "LONG",
) -> Dict[str, Any]:
    """
    Computes the complete suite of rev-screener.pine indicators for a single ticker's daily DataFrame.
    DataFrame must have columns: ['open', 'high', 'low', 'close', 'volume'] sorted chronologically.
    Args:
        df: Daily OHLCV DataFrame.
        benchmark_df: Optional benchmark DataFrame (unused).
        side: "LONG" or "SHORT" — mirrors scoring components for the given direction.
    Returns:
      - weinstein_stage (1 to 5)
      - rev_zone_long (0.0 to 10.0)
      - rev_zone_short (0.0 to 10.0)
      - is_extreme_reversal (bool, rev_zone >= 7.0)
      - buy_score (0 to 100)
      - sell_score (0 to 100)
      - prime_signal (-2 to +2)
      - vcp_energy (-2 to +2)
      - proxy_rr (float R:R)
      - atrs_above_stop (float) — distance from current price to the side's stop in ATRs
      - entry_rank (0 to 100)
      - priority_score (0 to 100 composite conviction)
      - priority_tier ('HIGH_PRIORITY', 'MEDIUM_PRIORITY', 'MONITOR')
    """
    n = len(df)
    if n < 30:
        return {
            "weinstein_stage": 1,
            "rev_zone_long": 0.0,
            "rev_zone_short": 0.0,
            "is_extreme_reversal": False,
            "buy_score": 50.0,
            "sell_score": 50.0,
            "prime_signal": 0,
            "vcp_energy": 0,
            "proxy_rr": 1.5,
            "atrs_above_stop": 1.5,
            "entry_rank": 50.0,
            "priority_score": 50.0,
            "priority_tier": "MONITOR",
        }

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_p = df["open"].astype(float)
    volume = df["volume"].astype(float)

    curr_close = float(close.iloc[-1])

    # Moving Averages
    ma20 = float(_sma(close, 20).iloc[-1])
    ma50 = float(_ema(close, 50).iloc[-1])
    ma200 = float(_sma(close, min(200, n)).iloc[-1])
    atr14 = float(_atr(high, low, close, 14).iloc[-1])
    rsi14 = float(_rsi(close, 14).iloc[-1])
    rsi2 = float(_rsi(close, 2).iloc[-1])

    up_trend = (curr_close > ma50) and (ma20 > ma50)
    down_trend = (curr_close < ma50) and (ma20 < ma50)

    # 1. Weinstein Stage
    stage = calc_weinstein_stage(close, rsi14)

    # 2. Z-Scores & Sigma Scoring
    z_vol = float(_calc_z(volume, 20).iloc[-1])
    if z_vol < 0 and up_trend:
        z_vol = 0.0  # Don't penalize low volume in uptrend pullbacks

    z_rsi = float(_calc_z(_rsi(close, 14), 14).iloc[-1]) * -1.0  # Lower RSI = higher buy score
    diff_ma50 = close - _ema(close, 50)
    z_trend = float(_calc_z(diff_ma50, min(50, n)).iloc[-1])

    roc10 = close.pct_change(10) * 100.0
    z_vel = float(_calc_z(roc10, min(250, n)).iloc[-1])

    dist_ma20 = (close - _sma(close, 20)) / _sma(close, 20) * 100.0
    z_elast = float(_calc_z(dist_ma20, min(250, n)).iloc[-1])

    # Exhaustion & Elasticity Penalties
    vel_pen = -(1.0 / (1.0 + math.exp(-2.0 * (abs(z_vel) - 2.0))))
    elast_sc = max(-1.0, min(1.0, math.exp(-0.5 * ((abs(z_elast) - 0.5) ** 2)) - 0.5))

    # Buy / Sell Sigma
    buy_cats = (1 if up_trend else 0) + (1 if (rsi14 < 70 and rsi14 > float(_rsi(close, 14).iloc[-2])) else 0) + (1 if stage in (2, 5) else 0)
    buy_sigma = z_vol + z_rsi + z_trend + vel_pen + elast_sc + (2.0 if buy_cats > 0 else 0.0) + (-2.0 if stage == 4 else 0.0)

    sell_cats = (1 if down_trend else 0) + (1 if rsi14 > 70 else 0) + (1 if stage == 4 else 0)
    sell_sigma = z_vol + (-z_rsi) + (-z_trend) + vel_pen + elast_sc + (2.0 if sell_cats > 0 else 0.0) + (-2.0 if stage in (2, 5) else 0.0)

    # Bayesian Probabilities
    buy_score = calc_bayes_probability(buy_sigma, stage, is_buy=True)
    sell_score = calc_bayes_probability(sell_sigma, stage, is_buy=False)

    # 3. Volatility Squeeze & VCP Energy
    sqz_on, vcp_energy, mom_val = detect_volatility_squeeze(close, high, low, bb_len=20, kc_len=20)

    # 4. Connors Reversal Zone
    rev_long, rev_short, is_extreme = calc_reversal_zone_scores(df, stage, rsi2, rsi14, z_elast)

    # 5. Proxy Risk:Reward & Swing Low (The #1 Measured Factor)
    swing_lo = float(low.iloc[-10:].min())
    swing_hi = float(high.iloc[-10:].max())
    range_hi = float(high.iloc[-min(60, n):].max())
    range_lo = float(low.iloc[-min(60, n):].min())
    is_short = side.upper() == "SHORT"
    if is_short:
        risk = max(swing_hi - curr_close, 0.01)
        reward = max(curr_close - range_lo, 0.01)
        proxy_rr = round(reward / risk, 2)
        atrs_up = round((swing_hi - curr_close) / max(atr14, 0.01), 2)
    else:
        risk = max(curr_close - swing_lo, 0.01)
        reward = max(range_hi - curr_close, 0.01)
        proxy_rr = round(reward / risk, 2)
        atrs_up = round((curr_close - swing_lo) / max(atr14, 0.01), 2)

    # 6. Prime Signal
    is_blowoff = abs(z_vel) > 2.0
    no_warnings = not is_blowoff
    prime_signal = 0
    if no_warnings and buy_score >= 85 and stage in (2, 5):
        prime_signal = 2
    elif no_warnings and buy_score >= 70 and stage in (2, 5):
        prime_signal = 1
    elif no_warnings and sell_score >= 85 and stage == 4:
        prime_signal = -2
    elif no_warnings and sell_score >= 70 and stage == 4:
        prime_signal = -1

    # 7. Entry Rank (0 to 100)
    def clip01(v: float, lo: float, hi: float) -> float:
        return float(np.clip((v - lo) / (hi - lo), 0.0, 1.0))

    ret252 = float(curr_close / close.iloc[-min(252, n)] - 1.0)
    ret63 = float(curr_close / close.iloc[-min(63, n)] - 1.0)
    ret21 = float(curr_close / close.iloc[-min(21, n)] - 1.0)
    lo252 = float(low.iloc[-min(252, n):].min())
    hi252 = float(high.iloc[-min(252, n):].max())
    pos252 = (curr_close - lo252) / (hi252 - lo252) if hi252 > lo252 else 0.5
    ext_pct = (curr_close - ma200) / ma200 * 100.0 if ma200 > 0 else 0.0

    entry_rank = round(
        (clip01(ret252, -0.40, 1.00) +
         clip01(pos252, 0.0, 1.0) +
         clip01(ext_pct, -30.0, 60.0) +
         clip01(ret63, -0.25, 0.45) +
         (1.0 - clip01(ret21, -0.15, 0.20))) / 5.0 * 100.0,
        1,
    )

    # 8. Composite Conviction / Priority Score (0 to 100)
    # Rewards Stage 2/1 basing (LONG) or Stage 4/1/5 (SHORT), Rev Zone >= 7,
    # Volatility Squeeze, High R:R, and Bayesian buy/sell sigma.
    score = 0.0

    # A. Weinstein Stage (+20 pts, mirrored for SHORT)
    if is_short:
        if stage == 4:
            score += 20.0
        elif stage in (1, 5):
            score += 15.0
    else:
        if stage == 2:
            score += 20.0
        elif stage in (1, 5):
            score += 15.0

    # B. Reversal Zone / Connors Mean Reversion (+25 pts)
    if is_short:
        if rev_short >= 7.0:
            score += 25.0
        elif rev_short >= 5.0:
            score += 15.0
    else:
        if rev_long >= 7.0:
            score += 25.0
        elif rev_long >= 5.0:
            score += 15.0

    # C. Squeeze / Compression (+20 pts)
    if sqz_on:
        score += 20.0
    elif vcp_energy == 2:
        score += 18.0

    # D. Proxy Risk:Reward (+20 pts)
    if proxy_rr >= 3.0:
        score += 20.0
    elif proxy_rr >= 2.2:
        score += 15.0
    elif proxy_rr >= 1.8:
        score += 10.0

    # E. Stop Proximity (ATRs Above Stop <= 1.0) (+15 pts)
    if atrs_up <= 0.5:
        score += 15.0
    elif atrs_up <= 1.0:
        score += 10.0

    # F. Bayesian Sigma (buy_score/sell_score) (+5 pts each direction)
    # High buy_score confirms long conviction; high sell_score confirms short conviction.
    if is_short:
        if sell_score >= 80.0:
            score += 5.0
        elif sell_score >= 65.0:
            score += 2.5
        if buy_score >= 80.0:
            score -= 5.0
    else:
        if buy_score >= 80.0:
            score += 5.0
        elif buy_score >= 65.0:
            score += 2.5
        if sell_score >= 80.0:
            score -= 5.0

    # G. Prime Signal (+3/+5 for aligned direction)
    # prime_signal >= 1 confirms long conviction; <= -1 confirms short conviction.
    if is_short:
        if prime_signal <= -2:
            score += 5.0
        elif prime_signal <= -1:
            score += 3.0
    else:
        if prime_signal >= 2:
            score += 5.0
        elif prime_signal >= 1:
            score += 3.0

    priority_score = round(float(np.clip(score, 0.0, 100.0)), 1)
    if priority_score >= 75.0:
        priority_tier = "HIGH_PRIORITY"
    elif priority_score >= 55.0:
        priority_tier = "MEDIUM_PRIORITY"
    else:
        priority_tier = "MONITOR"

    return {
        "weinstein_stage": stage,
        "rev_zone_long": rev_long,
        "rev_zone_short": rev_short,
        "is_extreme_reversal": is_extreme,
        "buy_score": round(buy_score, 1),
        "sell_score": round(sell_score, 1),
        "prime_signal": prime_signal,
        "vcp_energy": vcp_energy,
        "proxy_rr": proxy_rr,
        "atrs_above_stop": atrs_up,
        "entry_rank": entry_rank,
        "priority_score": priority_score,
        "priority_tier": priority_tier,
    }
