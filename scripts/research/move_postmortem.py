"""Earnings-move size and big-drop post-mortem on the full_v7 daily corpus.

1. Earnings move: reaction m = close[r]/close[r-1]-1 on the first session after each SEC 8-K Item 2.02 filing.
   Prediction known at close r-1: median |m| of the ticker's previous 8 reactions (at least 4). Reports how well it
   ranks the actual size, the multiples that cover 80/95% of moves, and which pre-event conditions change size/sign.
2. Big drops: one-session close-to-close <= -10%. Each is labelled EARNINGS / MARKET / INDUSTRY / IDIOSYNCRATIC and
   pre-conditions (all read at the prior close) are scored as lift over the base rate, per era.
3. Case cards for named drops (default INTC 2026-10-30).

Industry is today's Yahoo industry snapshot (data/universe/profiles.json), not point-in-time membership.
Universe is today's surviving names. Usage: python scripts/research/move_postmortem.py [--case INTC:2026-10-30]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pead_study import DEFAULT_CORPUS, ERAS, OUT_DIR, load_item202, reaction_mask  # noqa: E402

PROFILES = Path("/Volumes/Stock/data/universe/profiles.json")
COLS = {"Ext Z Self Relative": "extz", "Energy IV Rank Pct": "ivr", "HV20 Ann Pct": "hv20",
        "Ext Pct vs MA200": "ext200"}
DROP = -0.10
WIDE = -0.03
HIST_N, HIST_MIN = 8, 4


def load(corpus: Path) -> dict:
    frames = {}
    for f in glob.glob(str(corpus / "BATS_*, D.csv")):
        t = os.path.basename(f).split("BATS_")[1].split(",")[0]
        d = pd.read_csv(f, usecols=lambda c: c in {"time", "close", *COLS})
        d["time"] = pd.to_datetime(d["time"])
        d = d.drop_duplicates("time").set_index("time").sort_index()
        d = d[d["close"] > 0]
        if len(d) > 300:
            frames[t] = d.rename(columns=COLS)
    idx = sorted(set().union(*[set(d.index) for d in frames.values()]))
    tk = sorted(frames)
    P = {k: pd.DataFrame({t: frames[t][k] if k in frames[t] else np.nan for t in tk}).reindex(idx)
         for k in ["close", *COLS.values()]}
    c = P["close"]
    P["ret1"] = c / c.shift(1) - 1
    P["run21"] = c / c.shift(21) - 1
    P["run63"] = c / c.shift(63) - 1
    return P


def industry_means(P: dict, ind: dict[str, str], col: str) -> pd.DataFrame:
    """Same-industry mean of `col` excluding the name itself (NaN when fewer than 3 peers)."""
    x = P[col]
    out = pd.DataFrame(np.nan, index=x.index, columns=x.columns)
    for g in set(ind.values()):
        cols = [t for t in x.columns if ind.get(t) == g]
        if len(cols) < 4:
            continue
        sub = x[cols]
        s, n = sub.sum(axis=1, min_count=1), sub.notna().sum(axis=1)
        for t in cols:
            out[t] = (s - sub[t].fillna(0)) / (n - sub[t].notna())
    return out


def era_of(y: int) -> str:
    return next((f"{a}-{b}" for a, b in ERAS if a <= y <= b), f"{ERAS[0][0]}-{ERAS[0][1]}")


def earnings_moves(P: dict, react: pd.DataFrame, ctx: dict) -> pd.DataFrame:
    rows = []
    for t in P["close"].columns:
        hist = []
        for r in np.where(react[t].values)[0]:
            m = P["ret1"][t].iat[r]
            if not np.isfinite(m):
                continue
            pred = float(np.median(np.abs(hist[-HIST_N:]))) if len(hist) >= HIST_MIN else np.nan
            row = {"ticker": t, "date": P["close"].index[r], "m": m, "pred": pred,
                   "hist_down_share": float(np.mean(np.array(hist[-HIST_N:]) < 0)) if len(hist) >= HIST_MIN else np.nan}
            for k, df in ctx.items():
                row[k] = df[t].iat[r - 1]
            row["ind_same_day"] = ctx["_ind1"][t].iat[r] if "_ind1" in ctx else np.nan
            rows.append(row)
            hist.append(m)
    e = pd.DataFrame(rows)
    e["year"] = pd.to_datetime(e["date"]).dt.year
    e["era"] = e["year"].map(era_of)
    return e


def bucket_table(e: pd.DataFrame, col: str, q=(0.2, 0.8)) -> dict:
    """Bottom / middle / top quintile of a pre-event field: size vs own history, down share, -2x tail, per era."""
    s = e.dropna(subset=[col, "pred"])
    lo, hi = s[col].quantile(q[0]), s[col].quantile(q[1])
    out = {"cuts": [float(lo), float(hi)]}
    for name, sub in (("low", s[s[col] <= lo]), ("mid", s[(s[col] > lo) & (s[col] < hi)]), ("high", s[s[col] >= hi])):
        ratio = sub["m"].abs() / sub["pred"]
        tail = sub["m"] <= -2 * sub["pred"]
        eras = sub.assign(tail=tail).groupby("era")["tail"].mean().round(4).to_dict()
        out[name] = {"n": int(len(sub)), "median_size_vs_hist": round(float(ratio.median()), 3),
                     "down_share": round(float((sub["m"] < 0).mean()), 3),
                     "p_down_2x_hist": round(float(tail.mean()), 4), "p_down_2x_by_era": eras,
                     "mean_move": round(float(sub["m"].mean()), 4)}
    return out


def drop_lifts(P: dict, react: pd.DataFrame, conds: dict[str, pd.DataFrame]) -> dict:
    """P(next-session drop <= -10%) given a condition at today's close, vs the base rate; per era."""
    nxt = P["ret1"].shift(-1)
    drop = nxt <= DROP
    valid = nxt.notna()
    earn_next = react.shift(-1, fill_value=False).astype(bool)
    years = pd.Series(P["close"].index.year, index=P["close"].index)
    res = {}
    base = float(drop[valid].sum().sum() / valid.sum().sum())
    for name, m in {"earnings_next_session": earn_next, **conds}.items():
        m = m.fillna(False).astype(bool) & valid
        n = int(m.sum().sum())
        if not n:
            continue
        p = float((drop & m).sum().sum() / n)
        era = {}
        for a, b in ERAS:
            rows = (years >= a) & (years <= b)
            mm, vv = m[rows.values], valid[rows.values]
            ne = int(mm.sum().sum())
            pb = float((drop[rows.values] & vv).sum().sum() / vv.sum().sum())
            era[f"{a}-{b}"] = round(float((drop[rows.values] & mm).sum().sum() / ne) / pb, 2) if ne and pb else None
        res[name] = {"n": n, "p_drop": round(p, 5), "lift": round(p / base, 2), "lift_by_era": era,
                     "lift_stable": all(v is not None and v > 1 for v in era.values())}
        mx = m & ~earn_next
        nx = int(mx.sum().sum())
        if name != "earnings_next_session" and nx:
            res[name]["lift_ex_earnings"] = round(float((drop & mx).sum().sum() / nx) / base, 2)
    return {"base_rate": round(base, 5), "conditions": res}


def classify_drops(P: dict, react: pd.DataFrame, uni1: pd.Series, ind1: pd.DataFrame) -> pd.DataFrame:
    r = P["ret1"]
    rows = []
    for t in r.columns:
        for i in np.where((r[t] <= DROP).values)[0]:
            if react[t].iat[i]:
                k = "EARNINGS"
            elif uni1.iat[i] <= WIDE:
                k = "MARKET"
            elif ind1[t].iat[i] <= WIDE:
                k = "INDUSTRY"
            else:
                k = "IDIOSYNCRATIC"
            rows.append({"ticker": t, "date": r.index[i], "ret": float(r[t].iat[i]), "kind": k})
    d = pd.DataFrame(rows)
    d["era"] = pd.to_datetime(d["date"]).dt.year.map(era_of)
    return d


SURPRISE_CACHE = OUT_DIR / "eps_surprise.json"
SURPRISE_MATCH_DAYS = 4
SURPRISE_BAND = 2.0


def surprise_bucket(pct: float) -> str:
    return "MISS" if pct < -SURPRISE_BAND else "BEAT" if pct > SURPRISE_BAND else "IN_LINE"


def attach_surprise(e: pd.DataFrame, cache_path: Path = SURPRISE_CACHE) -> pd.DataFrame:
    """Join the nearest reported quarter (within SURPRISE_MATCH_DAYS of the reaction date) onto each event."""
    e = e.copy()
    e["surprise_pct"] = np.nan
    e["surprise_bucket"] = None
    if not cache_path.is_file():
        return e
    cache = json.loads(cache_path.read_text())
    for t, g in e.groupby("ticker"):
        qs = cache.get(t, {}).get("quarters") or []
        rows = []
        for q in qs:
            try:
                rows.append((pd.Timestamp(q["reportedDate"]), float(q["surprisePercentage"])))
            except (KeyError, TypeError, ValueError):
                continue
        if not rows:
            continue
        for ix, d in zip(g.index, pd.to_datetime(g["date"])):
            near = min(rows, key=lambda r: abs((r[0] - d).days))
            if abs((near[0] - d).days) <= SURPRISE_MATCH_DAYS:
                e.at[ix, "surprise_pct"] = near[1]
                e.at[ix, "surprise_bucket"] = surprise_bucket(near[1])
    return e


def category_table(e: pd.DataFrame, col: str) -> dict:
    s = e.dropna(subset=[col, "pred"])
    out = {}
    for name, sub in s.groupby(col):
        tail = sub["m"] <= -2 * sub["pred"]
        out[str(name)] = {"n": int(len(sub)), "median_size_vs_hist": round(float((sub["m"].abs() / sub["pred"]).median()), 3),
                          "down_share": round(float((sub["m"] < 0).mean()), 3), "p_down_2x_hist": round(float(tail.mean()), 4),
                          "p_down_2x_by_era": sub.assign(tail=tail).groupby("era")["tail"].mean().round(4).to_dict()}
    return out


def case_card(P, e, ind, ind1, uni1, ctx, ticker: str, day: str) -> dict:
    ix = P["close"].index.get_indexer([pd.Timestamp(day)])[0]
    if ix < 1 or ticker not in P["close"].columns:
        return {"ticker": ticker, "date": day, "error": "not in corpus"}
    prev = {k: (None if pd.isna(v := df[ticker].iat[ix - 1]) else round(float(v), 4)) for k, df in ctx.items()}
    peers = [t for t in P["close"].columns if ind.get(t) == ind.get(ticker) and t != ticker]
    pr = P["ret1"].iloc[ix][peers].dropna().sort_values()
    ev = e[(e.ticker == ticker) & (e.date == P["close"].index[ix])]
    out = {"ticker": ticker, "date": day, "industry": ind.get(ticker), "move": round(float(P["ret1"][ticker].iat[ix]), 4),
           "universe_same_day": round(float(uni1.iat[ix]), 4),
           "industry_same_day_ex_self": round(float(ind1[ticker].iat[ix]), 4),
           "peer_moves": {k: round(float(v), 4) for k, v in pr.items()},
           "prior_close_conditions": prev,
           "ctx_percentile_vs_universe": {k: round(float((df.iloc[ix - 1] < df[ticker].iat[ix - 1]).mean()), 3)
                                          for k, df in ctx.items() if pd.notna(df[ticker].iat[ix - 1])}}
    if len(ev):
        row = ev.iloc[0]
        out["surprise"] = {"bucket": row.surprise_bucket, "pct": None if pd.isna(row.surprise_pct) else float(row.surprise_pct)}
        hist = e[(e.ticker == ticker) & (e.date < row.date)].tail(HIST_N)
        out["earnings"] = {"own_hist_median_abs": round(float(row.pred), 4) if pd.notna(row.pred) else None,
                           "move_vs_hist": round(abs(row.m) / row.pred, 2) if row.pred else None,
                           "prior_reactions": {str(d.date()): round(float(m), 4) for d, m in zip(hist.date, hist.m)}}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    ap.add_argument("--case", action="append", default=[])
    ap.add_argument(
        "--export",
        default="/Volumes/Stock/data/earnings_move_forecast.json",
        help="Write earnings_move_forecast.json (empty string to skip)",
    )
    args = ap.parse_args()
    cases = args.case or ["INTC:2026-10-30"]

    P = load(Path(args.corpus))
    tk = list(P["close"].columns)
    items = load_item202(tk, False)
    react = reaction_mask({"idx": list(P["close"].index), "tk": tk, "c": P["close"]}, items)
    ind = {t: v[1].get("industry") for t, v in json.loads(PROFILES.read_text()).items() if isinstance(v, list)}
    uni1 = P["ret1"].mean(axis=1)
    ind1 = industry_means(P, ind, "ret1")
    ind63 = industry_means(P, ind, "run63")
    uni63 = P["run63"].mean(axis=1)
    ctx = {"run21": P["run21"], "run63_rel": P["run63"].sub(uni63, axis=0), "industry_run63_rel": ind63.sub(uni63, axis=0),
           "extz": P["extz"], "ivr": P["ivr"], "hv20": P["hv20"], "ext200": P["ext200"]}

    e = earnings_moves(P, react, {**ctx, "_ind1": ind1})
    e = e.drop(columns=["_ind1"], errors="ignore")
    e = attach_surprise(e)
    s = e.dropna(subset=["pred"])
    ratio = s["m"].abs() / s["pred"]
    rank_by_era = {era: round(float(g["m"].abs().corr(g["pred"], method="spearman")), 3) for era, g in s.groupby("era")}
    hv_rank = {era: round(float(g["m"].abs().corr(g["hv20"], method="spearman")), 3) for era, g in s.dropna(subset=["hv20"]).groupby("era")}

    def top(df, q=0.9):
        return df.ge(df.quantile(q, axis=1), axis=0) & df.notna()

    conds = {"run63_rel_top_decile": top(ctx["run63_rel"]), "industry_run63_rel_top_decile": top(ctx["industry_run63_rel"]),
             "extz_gt_2": P["extz"] > 2, "ivr_gt_80": P["ivr"] > 80, "below_ma200": P["ext200"] < 0,
             "run21_bottom_decile": P["run21"].le(P["run21"].quantile(0.1, axis=1), axis=0)}
    drops = classify_drops(P, react, uni1, ind1)

    latest = e.sort_values("date").groupby("ticker").tail(HIST_N)
    k80, k95 = float(ratio.quantile(0.8)), float(ratio.quantile(0.95))
    forecast = {t: {"median_abs": round(float(g.m.abs().median()), 4), "p80_abs": round(float(g.m.abs().median()) * k80, 4),
                    "p95_abs": round(float(g.m.abs().median()) * k95, 4), "down_share": round(float((g.m < 0).mean()), 2),
                    "n_hist": int(len(g)), "last": str(g.date.max().date())}
                for t, g in latest.groupby("ticker") if len(g) >= HIST_MIN}

    report = {
        "study": "move_postmortem", "run_date": date.today().isoformat(), "corpus": args.corpus,
        "notes": ["industry = today's snapshot, not point-in-time", "survivors only", "reaction = close-to-close on the first session after the 8-K"],
        "earnings": {
            "n": int(len(e)), "n_with_history": int(len(s)), "tickers": int(e.ticker.nunique()),
            "median_abs_move": round(float(e.m.abs().median()), 4),
            "own_history_rank_corr_by_era": rank_by_era, "hv20_rank_corr_by_era": hv_rank,
            "size_vs_hist_quantiles": {q: round(float(ratio.quantile(q)), 3) for q in (0.5, 0.8, 0.9, 0.95, 0.99)},
            "p_down_2x_hist": round(float((s.m <= -2 * s.pred).mean()), 4),
            "conditions": {c: bucket_table(e, c) for c in ["run21", "run63_rel", "industry_run63_rel", "extz", "ivr", "hist_down_share"]},
            "surprise": {"coverage": round(float(e.surprise_bucket.notna().mean()), 4),
                         "buckets": category_table(e, "surprise_bucket")},
        },
        "big_drops": {
            "n": int(len(drops)), "by_kind": drops.kind.value_counts().to_dict(),
            "by_kind_era": drops.groupby(["era", "kind"]).size().unstack(fill_value=0).to_dict(orient="index"),
            "by_ticker": drops.ticker.value_counts().head(200).to_dict(),
            "lifts": drop_lifts(P, react, conds),
        },
        "cases": [case_card(P, e, ind, ind1, uni1, ctx, *c.split(":")) for c in cases],
        "forecast": forecast,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"move_postmortem_{date.today():%Y%m%d}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    if args.export != "":
        export_path = Path(args.export)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        asof = P["close"].index.max()
        if hasattr(asof, "date"):
            asof = asof.date().isoformat()
        else:
            asof = pd.Timestamp(asof).date().isoformat()
        payload = {
            "asof": asof,
            "k80": k80,
            "k95": k95,
            "base_p_down_2x": report["earnings"]["p_down_2x_hist"],
            "conditions": report["earnings"]["conditions"],
            "tickers": forecast,
        }
        export_path.write_text(json.dumps(payload, indent=2))
        print(f"wrote {export_path}")
    print(json.dumps({k: report[k] for k in ("earnings", "big_drops", "cases")}, indent=1, default=str))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
