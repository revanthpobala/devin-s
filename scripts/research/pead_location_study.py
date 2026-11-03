"""PEAD x location study: does an earnings gap that held add lift to the in-zone, at-market R:R >= 2 rule?

Frozen before the run (shares the pead_study engine, 8-K Item 2.02 dates, full_v7, rel = 21-bar hold return minus the
same-date universe mean, entry at the next open, 10 bps cost):
  PEAD signal D = r+2 with the primary trigger (gap >= +5%, volume >= 2x, close[r+2] >= open[r]).
  Base rule     : signal bar s with long-in-zone (Zone RR Flags Pack bit 0), at-market R:R >= 2 =
                  (Long Target - close) / (close - Long Stop Loss), fade gate off (Signal Pack bit 2 set); first onset only.
  PEAD window   : s within 10 bars after D (inclusive).
Variants (entry open of s+1):
  pead_all         PEAD signal, no location filter (reproduces pead_study primary)
  pead_rr2         PEAD signal and at-market R:R >= 2 on D
  pead_inzone      PEAD signal and long-in-zone on D
  pead_below_avwap PEAD signal and close < AVWAP Support on D
  pullback_in_win  base-rule onset inside the PEAD window
  base_outside     base-rule onset outside any PEAD window (control)
  base_all         base-rule onset anywhere
Interaction lift = pullback_in_win - base_outside. No threshold is tuned after the run.
Usage: python scripts/research/pead_location_study.py [--corpus DIR]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import zlib
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "research"))
import pead_study as ps  # noqa: E402

WINDOW = 10
EXTRA = ["Long Target", "Long Stop Loss", "Zone RR Flags Pack", "Signal Pack", "AVWAP Support"]


def load_extra(corpus: Path, pn: dict) -> dict[str, pd.DataFrame]:
    cols: dict[str, dict] = {k: {} for k in EXTRA}
    for t in pn["tk"]:
        d = pd.read_csv(corpus / f"BATS_{t}, D.csv", usecols=["time"] + EXTRA)
        d["time"] = pd.to_datetime(d["time"])
        d = d.drop_duplicates("time").set_index("time").sort_index()
        for k in EXTRA:
            cols[k][t] = d[k]
    return {k: pd.DataFrame(v).reindex(index=pn["idx"], columns=pn["tk"]) for k, v in cols.items()}


def onset(sig: pd.DataFrame) -> pd.DataFrame:
    return sig & ~sig.shift(1, fill_value=False).astype(bool)


def lift_table(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    """Mean rel of a minus mean rel of b, overall, per era and per ticker half."""
    def view(df):
        df = df.dropna(subset=["rel"]).copy()
        df["year"] = pd.to_datetime(df["entry"]).dt.year
        df["half"] = df["ticker"].map(lambda t: zlib.crc32(t.encode()) % 2)
        return df
    a, b = view(a), view(b)
    out = {"overall": float(a.rel.mean() - b.rel.mean()), "eras": [], "halves": []}
    for lo, hi in ps.ERAS:
        sa, sb = a[(a.year >= lo) & (a.year <= hi)].rel, b[(b.year >= lo) & (b.year <= hi)].rel
        out["eras"].append({"era": f"{lo}-{hi}", "n_a": int(len(sa)), "lift": float(sa.mean() - sb.mean()) if len(sa) else None})
    for h in (0, 1):
        sa, sb = a[a.half == h].rel, b[b.half == h].rel
        out["halves"].append({"half": h, "n_a": int(len(sa)), "lift": float(sa.mean() - sb.mean()) if len(sa) else None})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(ps.DEFAULT_CORPUS))
    args = ap.parse_args()
    corpus = Path(args.corpus)

    prices = ps.load_prices(corpus)
    items = ps.load_item202(sorted(prices), False)
    pn = ps.panel(prices)
    x = load_extra(corpus, pn)
    c, o = pn["c"], pn["o"]

    tgt, stp = x["Long Target"], x["Long Stop Loss"]
    rr = ((tgt - c) / (c - stp)).where((c > stp) & (tgt > c))
    rr2 = (rr >= 2.0).fillna(False)
    flags = x["Zone RR Flags Pack"].fillna(0).astype(int)
    inzone = (flags % 2 == 1)
    fade_off = (x["Signal Pack"].fillna(0).astype(int) // 4 % 2 == 1)
    below_avwap = (c < x["AVWAP Support"]).fillna(False)

    react = ps.reaction_mask(pn, items)
    held = c.shift(-2) >= o
    gap_up = (pn["gap"] >= ps.GAP_MIN) & (pn["volx"] >= ps.VOL_MULT) & react
    pead_d = (gap_up & held).shift(2, fill_value=False).astype(bool)
    in_win = pead_d.astype(int).rolling(WINDOW + 1, min_periods=1).max().astype(bool)

    base = onset(inzone & rr2 & fade_off)
    sigs = {
        "pead_all": pead_d,
        "pead_rr2": pead_d & rr2,
        "pead_inzone": pead_d & inzone,
        "pead_below_avwap": pead_d & below_avwap,
        "pullback_in_win": base & in_win,
        "base_outside": base & ~in_win,
        "base_all": base,
    }
    evs = {k: ps.events(pn, m.shift(1, fill_value=False).astype(bool)) for k, m in sigs.items()}
    report = {
        "study": "pead_location_full_v7", "run_date": date.today().isoformat(), "corpus": str(corpus),
        "protocol": {"window_bars": WINDOW, "base_rule": "in-zone & at-market RR>=2 & fade off, first onset",
                     "entry": "open of s+1", "cost_round_trip": ps.COST, "eras": ps.ERAS},
        "variants": {k: ps.summarize(e, pn["names_days"]) for k, e in evs.items()},
        "lift": {
            "pullback_in_win_vs_base_outside": lift_table(evs["pullback_in_win"], evs["base_outside"]),
            "pead_rr2_vs_pead_all": lift_table(evs["pead_rr2"], evs["pead_all"]),
            "pead_inzone_vs_pead_all": lift_table(evs["pead_inzone"], evs["pead_all"]),
            "pead_below_avwap_vs_pead_all": lift_table(evs["pead_below_avwap"], evs["pead_all"]),
        },
    }
    out = ps.OUT_DIR / f"pead_location_{date.today():%Y%m%d}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    for k, s in report["variants"].items():
        print(f"{k:18s} n={s.get('n', 0):6d} tk={s.get('tickers', 0):4d} mean_rel={s.get('mean_rel', float('nan')):+.4f} "
              f"med={s.get('median_rel', float('nan')):+.4f} stop_path={s.get('stop_path_mean_ret') or float('nan'):+.4f} "
              f"eras={[round(e['mean_rel'], 4) if e['mean_rel'] is not None else None for e in s.get('eras', [])]} "
              f"halves={[round(h['mean_rel'], 4) for h in s.get('halves', [])]}")
    for k, v in report["lift"].items():
        print(f"LIFT {k}: {v['overall']:+.4f} eras={[(e['n_a'], None if e['lift'] is None else round(e['lift'], 4)) for e in v['eras']]} "
              f"halves={[(h['n_a'], None if h['lift'] is None else round(h['lift'], 4)) for h in v['halves']]}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
