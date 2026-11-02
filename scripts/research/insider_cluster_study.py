"""Insider cluster-buy study on SEC Form 345 quarterly datasets. Pre-registered gate, same engine as pead_study.py.

Source: https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{YYYYqN}_form345.zip (2006 onward).
Only original Form 4 filings (DOCUMENT_TYPE 4, amendments excluded), dated on FILING_DATE.

Frozen definition:
  purchase : open-market code P, shares * price >= $25,000 per filing (joint filings count as one insider).
  trigger  : at least 2 distinct insiders with qualifying purchases filed within 30 calendar days, dated on the
             filing that completes the cluster. Refractory 30 days per ticker after a trigger.
  entry    : open of the first session strictly after the trigger filing date. Hold 21 bars.
  relative, cost, stop path, eras, halves and gate: identical to pead_study.py.
Variants: (a) any single qualifying purchase, no cluster; (c) negative control, sale cluster (code S, same rules).

Usage: python scripts/research/insider_cluster_study.py [--download-only]
"""

from __future__ import annotations

import argparse
import bisect
import csv
import io
import json
import logging
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict, deque
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pead_study as ps  # noqa: E402

logger = logging.getLogger("insider_study")
URL = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{q}_form345.zip"
DATA_DIR = ps.ROOT / "data" / "research" / "form345"
MIN_VALUE = 25_000.0
WINDOW_DAYS = 30
MIN_INSIDERS = 2
REFRACTORY_DAYS = 30


def quarters(end: date) -> list[str]:
    return [f"{y}q{q}" for y in range(2006, end.year + 1) for q in range(1, 5) if (y, q) <= (end.year, (end.month - 1) // 3 + 1)]


def download(q: str) -> str:
    """'ok', 'cached', or 'missing' (HTTP 404: not published). Other failures raise."""
    path = DATA_DIR / f"{q}_form345.zip"
    if path.exists() and path.stat().st_size > 0:
        return "cached"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(URL.format(q=q), headers=ps.SEC_UA)
            with urllib.request.urlopen(req, timeout=120) as r:
                tmp = path.with_suffix(".part")
                tmp.write_bytes(r.read())
                tmp.rename(path)
            time.sleep(0.2)
            return "ok"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "missing"
            last = e
        except Exception as e:
            last = e
        time.sleep(2 + attempt * 3)
    raise RuntimeError(f"download failed {q}: {last}")


def _rows(z: zipfile.ZipFile, name: str):
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"), delimiter="\t")


def parse_zip(path: Path) -> list[tuple[int, str, str, str, float]]:
    """(issuer_cik, filing_date ISO, insider key, code, value) for qualifying P and S filings."""
    with zipfile.ZipFile(path) as z:
        subs = {}
        for r in _rows(z, "SUBMISSION.tsv"):
            if r["DOCUMENT_TYPE"] == "4":
                subs[r["ACCESSION_NUMBER"]] = (int(r["ISSUERCIK"]),
                                               datetime.strptime(r["FILING_DATE"], "%d-%b-%Y").date().isoformat())
        owners: dict[str, set] = defaultdict(set)
        for r in _rows(z, "REPORTINGOWNER.tsv"):
            if r["ACCESSION_NUMBER"] in subs and r["RPTOWNERCIK"]:
                owners[r["ACCESSION_NUMBER"]].add(r["RPTOWNERCIK"])
        value: dict[tuple[str, str], float] = defaultdict(float)
        for r in _rows(z, "NONDERIV_TRANS.tsv"):
            acc = r["ACCESSION_NUMBER"]
            if acc in subs and r["TRANS_CODE"] in ("P", "S"):
                try:
                    value[(acc, r["TRANS_CODE"])] += float(r["TRANS_SHARES"]) * float(r["TRANS_PRICEPERSHARE"])
                except (TypeError, ValueError):
                    continue
    out = []
    for (acc, code), v in value.items():
        if v < MIN_VALUE:
            continue
        cik, fd = subs[acc]
        key = "|".join(sorted(owners.get(acc, {acc})))
        out.append((cik, fd, key, code, v))
    return out


def load_all(end: date) -> tuple[list, dict]:
    rows, cov = [], {"requested": [], "missing": [], "parsed": []}
    for q in quarters(end):
        cov["requested"].append(q)
        status = download(q)
        if status == "missing":
            cov["missing"].append(q)
            continue
        rows.extend(parse_zip(DATA_DIR / f"{q}_form345.zip"))
        cov["parsed"].append(q)
    logger.info("form345: %d qualifying filings from %d quarters", len(rows), len(cov["parsed"]))
    return rows, cov


def triggers(events: list[tuple[str, str]]) -> list[str]:
    """events: sorted (filing_date, insider). Returns trigger filing dates for MIN_INSIDERS in WINDOW_DAYS."""
    out, win, last = [], deque(), None
    for fd, who in events:
        d = date.fromisoformat(fd)
        win.append((d, who))
        while win and (d - win[0][0]).days > WINDOW_DAYS:
            win.popleft()
        if len({w for _, w in win}) >= MIN_INSIDERS and (last is None or (d - last).days > REFRACTORY_DAYS):
            out.append(fd)
            last = d
    return out


def entry_masks(pn: dict, rows: list, cik_of: dict[str, int]) -> dict[str, pd.DataFrame]:
    idx, tk = pn["idx"], pn["tk"]
    tkset = set(tk)
    by_cik = {v: k for k, v in cik_of.items() if k in tkset}
    buckets: dict[str, dict[str, list]] = {"P": defaultdict(list), "S": defaultdict(list)}
    for cik, fd, who, code, _ in rows:
        t = by_cik.get(cik)
        if t:
            buckets[code][t].append((fd, who))
    masks = {k: pd.DataFrame(False, index=idx, columns=tk) for k in ("primary", "a_single_purchase", "c_mirror_sale_cluster")}
    for t in tk:
        p = sorted(buckets["P"].get(t, []))
        s = sorted(buckets["S"].get(t, []))
        for fd in triggers(p):
            _mark(masks["primary"], idx, t, fd)
        for fd, _ in p:
            _mark(masks["a_single_purchase"], idx, t, fd)
        for fd in triggers(s):
            _mark(masks["c_mirror_sale_cluster"], idx, t, fd)
    return masks


def _mark(m: pd.DataFrame, idx: list, t: str, filing_date: str) -> None:
    """Entry bar = first session strictly after the filing date."""
    e = bisect.bisect_right(idx, pd.Timestamp(filing_date))
    if e < len(idx):
        m.iat[e, m.columns.get_loc(t)] = True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--corpus", default=str(ps.DEFAULT_CORPUS))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    rows, cov = load_all(date.today())
    if args.download_only:
        print(json.dumps({k: len(v) for k, v in cov.items()}))
        return 0

    prices = ps.load_prices(Path(args.corpus))
    cmap = json.loads(ps.CIK_MAP.read_text())
    cik_of = {t: int(cmap[t]) for t in prices if t in cmap}
    pn = ps.panel(prices)
    masks = entry_masks(pn, rows, cik_of)

    report = {
        "study": "insider_cluster_form345",
        "run_date": date.today().isoformat(),
        "corpus": str(args.corpus),
        "universe_names": len(prices),
        "names_with_cik": len(cik_of),
        "coverage": {"quarters_requested": len(cov["requested"]), "quarters_parsed": len(cov["parsed"]),
                     "quarters_missing": cov["missing"]},
        "survivorship_note": "universe is today's surviving large-cap names; delisted names are absent",
        "protocol": {"min_value": MIN_VALUE, "min_insiders": MIN_INSIDERS, "window_days": WINDOW_DAYS,
                     "refractory_days": REFRACTORY_DAYS, "hold_bars": ps.HOLD_BARS, "cost_round_trip": ps.COST,
                     "stop_atr": ps.STOP_ATR, "fire_max": ps.FIRE_MAX, "n_min": ps.N_MIN, "eras": ps.ERAS},
        "variants": {name: ps.summarize(ps.events(pn, m), pn["names_days"]) for name, m in masks.items()},
    }
    report["gate_primary"] = ps.gate(report["variants"]["primary"])
    ps.OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = ps.OUT_DIR / f"insider_cluster_{date.today():%Y%m%d}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({"primary": report["variants"]["primary"], "gate": report["gate_primary"],
                      "coverage": report["coverage"]}, indent=2, default=str))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
