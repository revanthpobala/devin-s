"""
src/plugins/earnings_history_plugin.py

Plugin: Historical Earnings Reaction & Post-Earnings Announcement Drift (PEAD).
Analyzes prior earnings gap moves and post-earnings drift over the trailing 1-year history.
"""

from typing import Any, Dict, List
import pandas as pd
import numpy as np

from src.plugins.base_plugin import BaseAnalyticsPlugin


class EarningsHistoryPlugin(BaseAnalyticsPlugin):
    @property
    def name(self) -> str:
        return "earnings_history"

    @property
    def description(self) -> str:
        return "Analyzes historical quarterly earnings reaction moves and post-earnings drift (PEAD)."

    def run(self, ticker: str, df: pd.DataFrame, dw: Dict[str, Any]) -> Dict[str, Any]:
        if df.empty or len(df) < 40:
            return {}

        c_col = next((c for c in df.columns if c.lower() == "close"), None)
        o_col = next((c for c in df.columns if c.lower() == "open"), None)
        t_col = next((c for c in df.columns if c.lower() in ("time", "date")), None)

        if not c_col or not o_col:
            return {}

        df_calc = df.copy()
        df_calc["dt_str"] = df_calc[t_col].astype(str).str[:10] if t_col else [f"Bar-{i}" for i in range(len(df_calc))]
        closes = pd.to_numeric(df_calc[c_col], errors="coerce")
        opens = pd.to_numeric(df_calc[o_col], errors="coerce")

        prior_close = closes.shift(1)
        day_rets = ((closes - prior_close) / prior_close) * 100.0
        gaps = ((opens - prior_close) / prior_close) * 100.0

        earnings_events: List[Dict[str, Any]] = []

        # 1. Try pulling confirmed quarterly earnings dates & EPS surprises from yfinance
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            ed = t.get_earnings_dates(limit=8)
            if ed is not None and not ed.empty:
                # Filter for reported quarters (where Reported EPS is not NaN)
                reported = ed.dropna(subset=["Reported EPS"]).sort_index()
                for dt_idx, row in reported.iterrows():
                    ed_str = str(dt_idx)[:10]
                    # Find matching bar index in df
                    match_indices = df_calc.index[df_calc["dt_str"] >= ed_str].tolist()
                    if match_indices:
                        bar_idx = match_indices[0]
                        # If earnings reported after market close, reaction is on the following bar
                        if bar_idx < len(df_calc) - 1 and dt_idx.hour >= 16:
                            reaction_idx = bar_idx + 1 if bar_idx + 1 < len(df_calc) else bar_idx
                        else:
                            reaction_idx = bar_idx

                        c_i = closes.iloc[reaction_idx]
                        if pd.notna(c_i) and c_i > 0:
                            fwd_idx = min(reaction_idx + 5, len(df_calc) - 1)
                            fwd_5d = ((closes.iloc[fwd_idx] - c_i) / c_i) * 100.0 if fwd_idx > reaction_idx else 0.0
                            
                            eps_est = round(float(row["EPS Estimate"]), 2) if pd.notna(row.get("EPS Estimate")) else None
                            eps_act = round(float(row["Reported EPS"]), 2) if pd.notna(row.get("Reported EPS")) else None
                            surprise = round(float(row["Surprise(%)"]), 2) if pd.notna(row.get("Surprise(%)")) else None

                            earnings_events.append({
                                "date": ed_str,
                                "eps_reported": eps_act,
                                "eps_estimate": eps_est,
                                "surprise_pct": surprise,
                                "gap_pct": round(float(gaps.iloc[reaction_idx]), 2) if pd.notna(gaps.iloc[reaction_idx]) else 0.0,
                                "day_ret_pct": round(float(day_rets.iloc[reaction_idx]), 2) if pd.notna(day_rets.iloc[reaction_idx]) else 0.0,
                                "fwd_5d_drift_pct": round(float(fwd_5d), 2),
                            })
        except Exception:
            pass

        # 2. Fallback: If no yfinance earnings, detect via volume shock and price gap
        if not earnings_events:
            v_col = next((c for c in df.columns if c.lower() == "volume"), None)
            rvol_col = next((c for c in df.columns if "rvol" in c.lower()), None)
            rvols = pd.to_numeric(df[rvol_col], errors="coerce") if rvol_col else pd.Series(dtype=float)

            for i in range(20, len(df_calc) - 5):
                c_i = closes.iloc[i]
                c_i_5 = closes.iloc[i + 5]
                if pd.isna(c_i) or c_i <= 0 or pd.isna(c_i_5):
                    continue

                rvol_val = rvols.iloc[i] if not rvols.empty and pd.notna(rvols.iloc[i]) else 0.0
                gap_val = abs(gaps.iloc[i]) if pd.notna(gaps.iloc[i]) else 0.0
                ret_val = day_rets.iloc[i] if pd.notna(day_rets.iloc[i]) else 0.0

                if (rvol_val >= 2.0 and gap_val >= 2.5) or gap_val >= 4.0:
                    fwd_5d = ((c_i_5 - c_i) / c_i) * 100.0
                    earnings_events.append({
                        "date": df_calc["dt_str"].iloc[i],
                        "eps_reported": None,
                        "eps_estimate": None,
                        "surprise_pct": None,
                        "gap_pct": round(float(gap_val), 2),
                        "day_ret_pct": round(float(ret_val), 2),
                        "fwd_5d_drift_pct": round(float(fwd_5d), 2),
                    })

        recent_events = earnings_events[-4:] if len(earnings_events) >= 4 else earnings_events
        abs_moves = [abs(e["day_ret_pct"]) for e in recent_events if pd.notna(e.get("day_ret_pct"))]
        median_move = round(float(np.median(abs_moves)), 2) if abs_moves else 0.0

        return {
            "_earnings_reaction_events": recent_events,
            "_historical_median_catalyst_move_pct": median_move,
            "_catalyst_move_summary": f"Historical median 1-day earnings reaction: ±{median_move}% (n={len(recent_events)} quarterly reports)",
        }
