"""
Offline LightGBM training script for the Watch Ranker model.

Trains on all years of the full_v2 corpus and outputs model artifacts to data/models/:
  - watch_ranker.txt (LightGBM Booster model text format)
  - watch_ranker_features.json (load-bearing ordered feature manifest)
  - watch_ranker_meta.json (training metadata timestamp, bar/ticker counts)

Usage:
  CORPUS=full_v2 python scripts/ml/train_watch_ranker.py [--out-dir data/models]
"""

import argparse
import json
import os
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import lightgbm as lgb

EXT_Z_SELF_MAX, P_RICH, HV_HIGH, REALVOL_MAX, RET10_MAX = 2.5, 125.4, 35.9, 42.8, 12.9

C = dict(
    close="close",
    buy="Buy Score",
    sell="Sell Score",
    stage="Stage 1 Base 2 Up 3 Top 4 Down",
    regime="Regime 0 Hlt 1 Ext 2 Clmx 3 Dist 4 Dn 5 Ign 6 Sqz",
    dirp="Dir Prob Pct Above 50 Bull",
    ext="Ext Pct vs MA200",
    extz="Ext Z Self Relative",
    exh="Exhaustion Gradient",
    hv20="HV20 Ann Pct",
    actL="Action Long Code",
    actS="Action Short Code",
    revl="Long Rev Zone",
    revs="Short Rev Zone",
    ma20="MA 20 Fast",
    ma50="MA 50 Mid",
    ma200="MA 200 Slow",
    wein="Weinstein MA 150",
)


def num(d: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(d[col], errors="coerce") if col in d.columns else pd.Series(np.nan, index=d.index)


def build_gate(d: pd.DataFrame) -> pd.DataFrame:
    """Vectorised reproduction of run_data_window_filter's triage, long side."""
    g = pd.DataFrame(index=d.index)
    for k, col in C.items():
        g[k] = num(d, col)
    g["ticker"], g["date"], g["ex21"] = d["ticker"], d["date"], d["ex21"]

    lr = np.log(g["close"]).groupby(d["ticker"]).diff()
    g["realvol_10d"] = (
        lr.groupby(d["ticker"]).rolling(10).std().reset_index(level=0, drop=True) * np.sqrt(252) * 100
    )
    g["ret_10d"] = g["close"].groupby(d["ticker"]).pct_change(10) * 100

    core = [
        "close",
        "ma20",
        "ma50",
        "ma200",
        "wein",
        "buy",
        "sell",
        "stage",
        "dirp",
        "regime",
        "ext",
        "exh",
        "revl",
        "revs",
    ]
    ok = g[core].notna().all(axis=1)

    act = g["actL"].where(g["buy"] >= g["sell"], g["actS"])
    cut = (
        (g["extz"] >= EXT_Z_SELF_MAX)
        | ((g["close"] >= P_RICH) & (g["hv20"] >= HV_HIGH))
        | act.isin([17, 18])
        | (g["stage"] == 0)
        | (g["realvol_10d"] >= REALVOL_MAX)
        | (g["ret_10d"] >= RET10_MAX)
    )
    g["watch"] = ok & ~cut & (act != 20)
    g["long_side"] = g["buy"] >= g["sell"]
    return g


COL_TO_FIELD = {
    "Buy Score": "buy",
    "Sell Score": "sell",
    "Stage 1 Base 2 Up 3 Top 4 Down": "stage",
    "Stage Age Bars": "stage_age_bars",
    "Regime 0 Hlt 1 Ext 2 Clmx 3 Dist 4 Dn 5 Ign 6 Sqz": "regime",
    "Dir Prob Pct Above 50 Bull": "dir_prob",
    "Ext Pct vs MA200": "ext_pct",
    "Ext Z Self Relative": "ext_z_self",
    "Exhaustion Gradient": "exhaustion",
    "Exp Move Pct 21b": "exp_move_pct",
    "HV20 Ann Pct": "hv20",
    "ADX 14": "adx",
    "DMI DI Plus": "di_plus",
    "DMI DI Minus": "di_minus",
    "RVOL Vs Avg": "rvol",
    "Energy IV30 Ann Pct": "energy_iv30",
    "Energy IV Rank Pct": "energy_ivrank",
    "Energy IV HV Spread": "iv_hv_spread",
    "Energy State 3 Exp 2 Warm 1 Sqz 0 Dorm": "energy_state",
    "Z Velocity": "z_velocity",
    "Z Elasticity": "z_elasticity",
    "Trend Bars Up": "trend_bars_up",
    "Buy Sigma Evidence": "buy_sigma_evidence",
    "Sell Sigma Evidence": "sell_sigma_evidence",
    "RR To Target": "rr_to_target",
    "MTF Long Aligned 0 To 3": "mtf_long",
    "Action Long Code": "action_long",
    "Action Short Code": "action_short",
    "Long Rev Zone": "rev_l",
    "Short Rev Zone": "rev_s",
    "Long In Zone": "long_in_zone",
    "Short In Zone": "short_in_zone",
    "Long RR Valid": "long_rr_valid",
    "Short RR Valid": "short_rr_valid",
    "Entry At Market 0No 1L 2S 3Both": "entry_at_market",
    "Long Ignition Fresh Breakout": "ignition_long",
    "Bear Warning Mask": "bear_mask",
    "Reversal Pattern Mask": "rev_mask",
    "Weak Level Mask": "weak_mask",
    "Bear Warning Age": "bear_age",
    "Reversal Pattern Age": "rev_age",
    "Weak Level Age": "weak_age",
}

COL_TO_FIELD_VS = {
    "MA 20 Fast": "ma20",
    "MA 50 Mid": "ma50",
    "MA 200 Slow": "ma200",
    "Weinstein MA 150": "weinstein",
    "Sprint Line EMA": "sprint_ema",
    "Hull Baseline HMA": "hull_baseline",
    "AVWAP Resistance": "avwap_resistance",
    "AVWAP Support": "avwap_support",
    "VP POC": "vp_poc",
    "VP VAH": "vp_vah",
    "VP VAL": "vp_val",
    "Long Entry Zone Bot": "long_zbot",
    "Long Entry Zone Top": "long_ztop",
    "Long Stop Loss": "long_stop_loss",
    "Long Target": "long_target",
}


def main():
    parser = argparse.ArgumentParser(description="Train LightGBM Watch Ranker model.")
    parser.add_argument(
        "--out-dir",
        default="data/models",
        help="Target directory for trained artifacts (watch_ranker.txt, watch_ranker_features.json)",
    )
    args = parser.parse_args()

    try:
        from load_exports import load
    except ImportError:
        print("ERROR: load_exports.py not found in PYTHONPATH. Run from data-windows checkout where corpus lives.")
        return

    print("Loading corpus...")
    d = load()
    print("Building vectorised triage gate...")
    g = build_gate(d)

    X = pd.DataFrame(index=d.index)
    for col, fld in COL_TO_FIELD.items():
        X[fld] = num(d, col)

    close = num(d, "close")
    for col, fld in COL_TO_FIELD_VS.items():
        X[f"vs_{fld}"] = (num(d, col) / close - 1.0) * 100.0

    X = X.astype("float32")

    pool = g["watch"] & g["long_side"] & g["ex21"].notna()
    g_sub, X_sub = g[pool], X[pool]
    y = g_sub.groupby("date")["ex21"].rank(pct=True)

    print(f"Training LightGBM on {len(X_sub)} bars across {g_sub['ticker'].nunique()} tickers...")
    model = lgb.LGBMRegressor(
        n_estimators=300,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=200,
        subsample=0.8,
        colsample_bytree=0.8,
        verbose=-1,
        random_state=0,
    )
    model.fit(X_sub, y)

    os.makedirs(args.out_dir, exist_ok=True)
    mp = os.path.join(args.out_dir, "watch_ranker.txt")
    fp = os.path.join(args.out_dir, "watch_ranker_features.json")
    meta_path = os.path.join(args.out_dir, "watch_ranker_meta.json")

    model.booster_.save_model(mp)
    with open(fp, "w", encoding="utf-8") as f_out:
        json.dump({"features": list(X_sub.columns)}, f_out, indent=2)

    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "corpus": os.environ.get("CORPUS", "full_v2"),
        "n_bars": int(len(X_sub)),
        "n_tickers": int(g_sub["ticker"].nunique()),
        "n_features": int(len(X_sub.columns)),
    }
    with open(meta_path, "w", encoding="utf-8") as f_out:
        json.dump(meta, f_out, indent=2)

    print(f"Training complete! Model artifacts written to {args.out_dir}:")
    print(f"  - {mp}")
    print(f"  - {fp}")
    print(f"  - {meta_path}")


if __name__ == "__main__":
    main()
