import argparse
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

# load_exports lives in the data-windows checkout alongside the corpus.
_DW_SCRIPTS = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "data-windows", "scripts"
)
sys.path.insert(0, os.path.abspath(_DW_SCRIPTS))
try:
    from load_exports import load  # noqa: E402
except ImportError:
    print(
        f"ERROR: load_exports not found at {_DW_SCRIPTS}. Run on the machine with the corpus."
    )
    sys.exit(1)

OOPS_BULL_BIT = 1024  # Reversal Pattern Mask bit for OOPS_BULL (bible 8.3)


def _num(d, c):
    return (
        pd.to_numeric(d[c], errors="coerce")
        if c in d.columns
        else pd.Series(np.nan, index=d.index)
    )


def build_masks(d):
    close = _num(d, "close")
    ma200 = _num(d, "MA 200 Slow")
    buy = _num(d, "Buy Score")
    rvol = _num(d, "RVOL Vs Avg")
    rev = _num(d, "Long Rev Zone")
    ext = _num(d, "Ext Pct vs MA200")
    stage = _num(d, "Stage 1 Base 2 Up 3 Top 4 Down")
    actL = _num(d, "Action Long Code")
    mask = _num(d, "Reversal Pattern Mask").fillna(0).astype("int64")

    oops = (mask & OOPS_BULL_BIT) > 0
    reversal = (close < ma200) & (buy < 30) & (rvol > 1.5) & (rev >= 7)

    return {
        "baseline_all": pd.Series(True, index=d.index),
        "reversal": reversal,
        "reversal_deep": reversal & (ext <= -8),
        "reversal_deep_oops": reversal & (ext <= -8) & oops,
        "reversal_shallow": reversal & (ext > -8),
        "reversal_oops_any": reversal & oops,
        "prime_stage2": (buy >= 85) & (stage == 2),
        "strong_buy_mom": (buy >= 90),
        "ext_excl_25_60": (ext >= 25) & (ext < 60),
        "watch_code8": (actL.round() == 8),
    }

import numpy as np


def ticker_ci(sub, col="ex21", n=2000, seed=0):
    per = sub.groupby("ticker")[col].mean().dropna().values
    if len(per) < 15:
        return None
    rng = np.random.default_rng(seed)
    boot = [rng.choice(per, len(per), replace=True).mean() for _ in range(n)]
    return (
        float(per.mean()),
        float(np.percentile(boot, 2.5)),
        float(np.percentile(boot, 97.5)),
        len(per),
    )


def null_pass(d, mask, real_mean, n_shuffle=50, seed=0):
    """Label-shuffle null: permute ex21 within date, recompute the bucket mean.

    The null distribution is centered on the universe/baseline offset (not 0),
    so the honest test is whether the REAL mean falls OUTSIDE the signed [2.5,
    97.5] null band - i.e. the bucket differs from what random-membership-in-
    this-universe would produce. This makes 'sig' baseline-relative, so a
    bucket merely sitting at the -0.30 universe offset is NOT flagged.
    """
    rng = np.random.default_rng(seed)
    ex = d["ex21"].values
    idx = d.groupby("dt").indices
    bmask = mask.values
    nulls = []
    for _ in range(n_shuffle):
        sh = ex.copy()
        for _, ii in idx.items():
            sh[ii] = rng.permutation(sh[ii])
        nulls.append(np.nanmean(sh[bmask]))
    lo, hi = np.percentile(nulls, 2.5), np.percentile(nulls, 97.5)
    return bool(real_mean < lo or real_mean > hi)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="compute + print, do not write the artifact",
    )
    ap.add_argument("--min-price", type=float, default=20.0)
    ap.add_argument(
        "--out-dir",
        default=os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "models"
        ),
    )
    args = ap.parse_args()

    d = load(verbose=True)
    d = (
        d[pd.to_numeric(d["close"], errors="coerce") >= args.min_price]
        .dropna(subset=["ex21"])
        .copy()
    )
    d["dt"] = d["date"].dt.strftime("%Y-%m-%d")
    d = d.reset_index(drop=True)

    masks = build_masks(d)

    # Universe/baseline offset: date-neutral demean uses the FULL universe (incl. sub-$20),
    # so the >=$20 set sits at a nonzero baseline. Report and test edge RELATIVE to it.
    # Ticker-clustered baseline (per-ticker mean, then averaged) so it is on the SAME footing
    # as the bucket means from ticker_ci - a bar-level baseline would not be comparable.
    baseline_mean = float(d.groupby("ticker")["ex21"].mean().mean())
    d["yr"] = d["date"].dt.year
    ERAS = [(2006, 2011), (2011, 2016), (2016, 2021), (2021, 2027)]
    buckets = {}

    print(
        f"\nbaseline (>=${args.min_price:.0f} universe) ex21 = {baseline_mean:+.3f}  -- edge is measured vs THIS, not 0\n"
    )
    print(
        f"{'bucket':22s}{'n':>9}{'tk':>5}{'ex21':>8}{'edge':>7}{'med':>7}{'p10':>7}{'p90':>7}{'P_up':>6}  CI            sig"
    )

    for name, m in masks.items():
        sub = d[m]
        if len(sub) < 150:
            print(f"  {name:20s} n={len(sub):6d} thin")
            continue

        ci = ticker_ci(sub)
        if ci is None:
            continue

        mean, lo, hi, ntk = ci
        edge = mean - baseline_mean
        med = float(sub["ex21"].median())
        p10 = float(sub["ex21"].quantile(0.1))
        p90 = float(sub["ex21"].quantile(0.9))
        p_up = float((pd.to_numeric(sub["fwd21"], errors="coerce") > 0).mean())

        # sig = baseline-relative AND economically material. The signed label-shuffle null band
        # gets vanishingly tight on 800k-bar buckets, so a trivial +-0.05 edge would otherwise
        # flag "significant". Require |edge vs baseline| >= MIN_EDGE so flat states read flat.
        MIN_EDGE = 0.20
        sig = (
            False
            if name == "baseline_all"
            else bool(abs(edge) >= MIN_EDGE and null_pass(d, m, mean))
        )

        # STABILITY: per-5y-era edge (vs baseline). A big single-number edge can hide that it is
        # episodic (negative in some eras). eras_pos + min_era_edge expose that; reliability tags
        # 'episodic' when any era is negative or the mean is far above the median (tail-driven).
        era_edges = []
        for lo_y, hi_y in ERAS:
            es = sub[(sub["yr"] >= lo_y) & (sub["yr"] < hi_y)]
            per_e = es.groupby("ticker")["ex21"].mean().dropna()
            # ticker-clustered per-era edge (same weighting as ex21_mean), needs >=15 tickers
            era_edges.append(
                round(float(per_e.mean()) - baseline_mean, 2)
                if len(per_e) >= 15
                else None
            )

        vals = [e for e in era_edges if e is not None]
        eras_pos = sum(1 for e in vals if e > 0)
        min_era = min(vals) if vals else None
        max_era = max(vals) if vals else None
        tail_driven = (mean - med) > 0.5

        # Sign-aware reliability: a positive (buy) edge is 'episodic' if it turns negative in any era
        # or is tail-driven; a negative (exclusion) edge is a 'steady_exclusion' if negative in every
        # era, else 'episodic_exclusion' (the exclusion sometimes fails).
        if not sig:
            reliability = "n/a"
        elif edge > 0:
            reliability = (
                "episodic"
                if (min_era is not None and min_era < 0) or tail_driven
                else "steady"
            )
        else:
            reliability = (
                "steady_exclusion"
                if (max_era is not None and max_era < 0)
                else "episodic_exclusion"
            )

        buckets[name] = {
            "n": int(len(sub)),
            "n_tk": int(ntk),
            "ex21_mean": round(mean, 3),
            "edge_vs_baseline": round(edge, 3),
            "ex21_med": round(med, 3),
            "p10": round(p10, 2),
            "p90": round(p90, 2),
            "p_up": round(p_up, 3),
            "ci": [round(lo, 3), round(hi, 3)],
            "sig": sig,
            "era_edges_5y": era_edges,
            "eras_positive": f"{eras_pos}/{len(vals)}",
            "min_era_edge": min_era,
            "reliability": reliability,
        }

        print(
            f"  {name:20s}{len(sub):9d}{ntk:5d}{mean:+8.2f}{edge:+7.2f}{med:+7.2f}{p10:+7.1f}{p90:+7.1f}{p_up:6.2f}  [{lo:+.2f},{hi:+.2f}]  {'SIG' if sig else ''}"
        )

    if args.dry_run:
        print("\n[dry-run] not writing artifact.")
        return

    os.makedirs(args.out_dir, exist_ok=True)
    out = os.path.join(args.out_dir, "state_response.json")
    meta = os.path.join(args.out_dir, "state_response_meta.json")

    with open(out, "w", encoding="utf-8") as f:
        json.dump({"buckets": buckets}, f, indent=2)

    with open(meta, "w", encoding="utf-8") as f:
        json.dump(
            {
                "built_at": datetime.now(timezone.utc).isoformat(),
                "corpus": os.environ.get("CORPUS", "full_v2"),
                "min_price": args.min_price,
                "n_bars": int(len(d)),
                "n_tickers": int(d["ticker"].nunique()),
                "date_range": [
                    str(d["date"].min().date()),
                    str(d["date"].max().date()),
                ],
                "baseline_ex21": round(baseline_mean, 3),
                "metric": "date-neutral ex21; edge = mean - baseline; ticker-bootstrap CI; "
                "sig = signed label-shuffle null (50), baseline-relative",
            },
            f,
            indent=2,
        )

    print(f"\nwrote {out}\nwrote {meta}")


if __name__ == "__main__":
    main()