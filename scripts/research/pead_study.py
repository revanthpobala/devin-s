"""Post-earnings drift (PEAD) study on the full_v7 daily corpus. Pre-registered gate; the gate is not tuned after a run.

Earnings dates: SEC 8-K Item 2.02 filings (results of operations), from data.sec.gov submissions. Full history
back to 2006 is free here; yfinance EPS surprise only covers about 4 quarters, so variant (b) cannot run and is
reported as NOT_RUN.

Frozen definition (reaction day r = first session strictly after the 8-K filing date; the 8-K timing is
unknown, so after-close releases are assumed and pre-market releases are missed):
  trigger  : gap = open[r]/close[r-1]-1 >= +5%, volume[r] >= 2x mean(volume over the 20 bars before r),
             and close[r+2] >= open[r] (gap held through day 3).
  entry    : open[r+3] (day-4 open). Hold 21 bars: exit close[r+3+20].
  relative : hold return minus the mean hold return of all names entering on the same date.
  cost     : 10 bps round trip subtracted before the gate.
  stop path: 2 ATR14 stop below entry, checked on daily lows, stop-first, gap-through exits at the open. Reported only.
Variants: (a) gap and volume without the hold condition; (c) mirror, gap <= -5% with volume and NO hold (negative control);
(b) gap plus EPS surprise > 0, NOT_RUN.

Gate (all required, primary trigger only): mean rel > 0 in each of 4 eras, mean rel > 0 in both ticker-hash halves,
median rel >= 0, n >= 500, fire rate < 5% of name-days, mean rel - 0.001 > 0.

Shared engine (panel, entry-mask events, gate) is imported by insider_cluster_study.py.
Usage: python scripts/research/pead_study.py [--corpus DIR] [--refresh]
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
import time
import urllib.request
import zlib
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.clients.sec_edgar_client import SEC_HEADERS  # noqa: E402

# Only the User-Agent: the client also sends a data.sec.gov Host header, which makes www.sec.gov return 404.
SEC_UA = {"User-Agent": SEC_HEADERS["User-Agent"]}

logger = logging.getLogger("pead_study")
DEFAULT_CORPUS = Path("/Users/ei6201/Mine/Pine/full_v7")
CIK_MAP = Path("/Volumes/Stock/data/sec_cik_map.json")
CACHE = ROOT / "data" / "research" / "sec_8k_item202.json"
OUT_DIR = ROOT / "data" / "research"

GAP_MIN = 0.05
VOL_MULT = 2.0
HOLD_BARS = 21
ENTRY_OFFSET = 3  # r+3 is the day-4 open
COST = 0.001
STOP_ATR = 2.0
FIRE_MAX = 0.05
N_MIN = 500
ERAS = [(2006, 2011), (2012, 2016), (2017, 2021), (2022, 2026)]


def _get_json(url: str) -> dict:
    """System TLS trust store (certifi fails on this Mac); three attempts, then raise."""
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=SEC_UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(1 + attempt)
    raise RuntimeError(f"SEC fetch failed {url}: {last}")


def fetch_item202(cik: str) -> list[str]:
    """Filing dates of 8-Ks with Item 2.02, all pages of the submissions index."""
    d = _get_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    pages = [d["filings"]["recent"]] + [_get_json(f"https://data.sec.gov/submissions/{p['name']}")
                                        for p in d["filings"].get("files", [])]
    out = []
    for r in pages:
        for form, fd, items in zip(r["form"], r["filingDate"], r["items"]):
            if form == "8-K" and "2.02" in (items or "").split(","):
                out.append(fd)
    time.sleep(0.12)  # SEC fair-access limit is 10 requests per second
    return sorted(set(out))


def load_item202(tickers: list[str], refresh: bool) -> dict[str, list[str]]:
    if CACHE.exists() and not refresh:
        return json.loads(CACHE.read_text())
    cmap = json.loads(CIK_MAP.read_text())
    res: dict[str, list[str]] = {}
    for i, t in enumerate(tickers, 1):
        cik = cmap.get(t)
        res[t] = fetch_item202(cik) if cik else []  # any SEC failure aborts: a partial cache would shrink the sample
        if i % 50 == 0:
            logger.info("8-K %d/%d", i, len(tickers))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(res))
    return res


def load_prices(corpus: Path) -> dict[str, pd.DataFrame]:
    out = {}
    for f in glob.glob(str(corpus / "BATS_*, D.csv")):
        t = os.path.basename(f).split("BATS_")[1].split(",")[0]
        d = pd.read_csv(f, usecols=["time", "open", "high", "low", "close", "Volume"])
        d["time"] = pd.to_datetime(d["time"])
        d = d.drop_duplicates("time").set_index("time").sort_index()
        d = d[d["close"] > 0]
        if len(d) > 300:
            out[t] = d
    return out


def panel(prices: dict[str, pd.DataFrame]) -> dict:
    """Aligned matrices (dates x tickers) plus the universe-relative 21-bar hold return."""
    idx = sorted(set().union(*[set(d.index) for d in prices.values()]))
    tk = sorted(prices)
    P = {c: pd.DataFrame({t: prices[t][c] for t in tk}).reindex(idx) for c in ["open", "high", "low", "close", "Volume"]}
    o, h, lo, c, v = (P[k] for k in ["open", "high", "low", "close", "Volume"])
    tr = np.fmax.reduce([(h - lo).values, (h - c.shift(1)).abs().values, (lo - c.shift(1)).abs().values])
    hold21 = c.shift(-HOLD_BARS) / o - 1
    uni = hold21.mean(axis=1)
    return {"idx": idx, "tk": tk, "o": o, "lo": lo, "c": c, "v": v,
            "gap": o / c.shift(1) - 1, "volx": v / v.rolling(20).mean().shift(1),
            "atr": pd.DataFrame(tr, index=idx, columns=tk).rolling(14).mean().shift(1),
            "hold21": hold21, "rel21": hold21.sub(uni, axis=0), "names_days": int(c.notna().sum().sum())}


def reaction_mask(pn: dict, items: dict[str, list[str]], min_gap_bars: int = 10) -> pd.DataFrame:
    """True on the first session strictly after each filing date, deduped to one per ticker per min_gap_bars."""
    idx, tk = pn["idx"], pn["tk"]
    m = pd.DataFrame(False, index=idx, columns=tk)
    idx_arr = np.array(idx, dtype="datetime64[ns]")
    for t in tk:
        last = -10**9
        for fd in items.get(t, []):
            r = int(np.searchsorted(idx_arr, np.datetime64(fd), side="right"))
            if r >= len(idx) or r - last < min_gap_bars:
                continue
            m.iat[r, tk.index(t)] = True
            last = r
    return m


def events(pn: dict, entry_mask: pd.DataFrame) -> pd.DataFrame:
    """Events at entry-bar index e (entry_mask True at e). Rows carry rel and raw hold return and the stop-path return."""
    idx, tk = pn["idx"], pn["tk"]
    m = entry_mask & pn["c"].shift(-HOLD_BARS).notna() & pn["o"].notna()
    arrs = {t: (pn["o"][t].values, pn["lo"][t].values, pn["c"][t].values, pn["atr"][t].values) for t in tk}
    rows = []
    for t in tk:
        oa, la, ca, aa = arrs[t]
        for e in np.where(m[t].values)[0]:
            rows.append((t, idx[e], float(pn["rel21"][t].iat[e]), float(pn["hold21"][t].iat[e]),
                         stop_return(oa, la, ca, aa, e)))
    return pd.DataFrame(rows, columns=["ticker", "entry", "rel", "ret", "stop_ret"])


def pead_masks(pn: dict, items: dict[str, list[str]]) -> dict[str, pd.DataFrame]:
    """Entry-bar masks for the three PEAD variants (trigger masks at r, shifted to r+3)."""
    react = reaction_mask(pn, items)
    gap, volx, o, c = pn["gap"], pn["volx"], pn["o"], pn["c"]
    held = c.shift(-2) >= o
    gap_up = (gap >= GAP_MIN) & (volx >= VOL_MULT) & react
    gap_dn = (gap <= -GAP_MIN) & (volx >= VOL_MULT) & react
    def at_entry(mask):
        return mask.shift(ENTRY_OFFSET, fill_value=False).astype(bool)
    return {"primary": at_entry(gap_up & held), "a_gap_vol_no_hold": at_entry(gap_up),
            "c_mirror_gap_down_no_hold": at_entry(gap_dn & ~held)}


def stop_return(o, lo, c, atr, e):
    """2 ATR stop below the entry open, daily lows, stop-first; open below stop exits at the open."""
    if not np.isfinite(atr[e - 1]) or e + HOLD_BARS >= len(o):
        return np.nan
    entry = o[e]
    stop = entry - STOP_ATR * atr[e - 1]
    for d in range(e, e + HOLD_BARS + 1):
        if o[d] <= stop:
            return o[d] / entry - 1
        if lo[d] <= stop:
            return stop / entry - 1
    return c[e + HOLD_BARS] / entry - 1


def summarize(df: pd.DataFrame, names_days: int) -> dict:
    if df.empty:
        return {"n": 0}
    df = df.dropna(subset=["rel"]).copy()
    df["year"] = pd.to_datetime(df["entry"]).dt.year
    df["half"] = df["ticker"].map(lambda t: zlib.crc32(t.encode()) % 2)
    era = []
    for a, b in ERAS:
        s = df[(df.year >= a) & (df.year <= b)]["rel"]
        era.append({"era": f"{a}-{b}", "n": int(len(s)), "mean_rel": float(s.mean()) if len(s) else None})
    halves = [{"half": h, "n": int((df.half == h).sum()), "mean_rel": float(df[df.half == h].rel.mean())} for h in (0, 1)]
    has_stop = df.stop_ret.notna().any()
    return {
        "n": int(len(df)),
        "tickers": int(df.ticker.nunique()),
        "win_rate_rel": float((df.rel > 0).mean()),
        "mean_rel": float(df.rel.mean()),
        "median_rel": float(df.rel.median()),
        "mean_rel_net_10bps": float(df.rel.mean() - COST),
        "mean_ret": float(df.ret.mean()),
        "stop_path_mean_ret": float(df.stop_ret.mean()) if has_stop else None,
        "stop_path_mean_ret_net_10bps": float(df.stop_ret.mean() - COST) if has_stop else None,
        "fire_rate": float(len(df) / names_days),
        "eras": era,
        "halves": halves,
    }


def gate(s: dict) -> dict:
    checks = {
        "era_positive_all_4": bool(s.get("eras") and all(e["n"] and e["mean_rel"] > 0 for e in s["eras"])),
        "halves_positive_both": bool(s.get("halves") and all(h["n"] and h["mean_rel"] > 0 for h in s["halves"])),
        "median_nonneg": bool(s.get("median_rel", -1) >= 0),
        "n_min_500": bool(s.get("n", 0) >= N_MIN),
        "fire_rate_under_5pct": bool(s.get("fire_rate", 1) < FIRE_MAX),
        "positive_after_10bps": bool(s.get("mean_rel_net_10bps", -1) > 0),
    }
    checks["PASS"] = all(checks.values())
    return checks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    prices = load_prices(Path(args.corpus))
    items = load_item202(sorted(prices), args.refresh)
    pn = panel(prices)
    masks = pead_masks(pn, items)

    report = {
        "study": "pead_full_v7",
        "run_date": date.today().isoformat(),
        "corpus": str(args.corpus),
        "universe_names": len(prices),
        "names_with_item202": sum(1 for t in prices if items.get(t)),
        "survivorship_note": "universe is today's surviving large-cap names; delisted names are absent",
        "protocol": {"gap_min": GAP_MIN, "vol_mult": VOL_MULT, "hold_bars": HOLD_BARS, "entry_offset": ENTRY_OFFSET,
                     "cost_round_trip": COST, "stop_atr": STOP_ATR, "fire_max": FIRE_MAX, "n_min": N_MIN,
                     "eras": ERAS},
        "variants": {name: summarize(events(pn, m), pn["names_days"]) for name, m in masks.items()},
    }
    report["variants"]["b_gap_plus_eps_surprise"] = {
        "status": "NOT_RUN", "reason": "EPS surprise history not available for full period from free sources"}
    report["gate_primary"] = gate(report["variants"]["primary"])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"pead_study_{date.today():%Y%m%d}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({"primary": report["variants"]["primary"], "gate": report["gate_primary"]}, indent=2, default=str))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
