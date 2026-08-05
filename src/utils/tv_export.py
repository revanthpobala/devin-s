import argparse
import os
import shutil
import signal
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
WORKLIST = os.path.join(HERE, "..", "export_worklist.csv")
PROFILE = os.path.join(ROOT, "tv_chrome_profile_1")
STATE = os.path.join(ROOT, ".tv-state.json")
OUTDIR = os.path.join(ROOT, "batty3")
CHART = "jPAQSlZC"

MENU_BTN = 'button[data-name="save-load-menu"]'
DL_ITEM = 'text=/Download chart data/i'
DL_BTN = 'button[data-qa-id="download-btn"]'
NO_SYMBOL = "text=/symbol doesn't exist/i"

GOTO_BTN = 'button[data-name="go-to-date"]'
GOTO_START = 'input[data-name="start-date-range"]'
GOTO_END = 'input[data-name="end-date-range"]'
GOTO_SUBMIT = 'button[data-name="submit-button"]'

MIN_COLS = 60            # healthy export is ~82 columns
MIN_ROWS = 260           # the scoring engine z-scores against 250 bars, so a
                         # shorter file cannot produce a usable score at all
MAX_BAR_GAP_DAYS = 4.0   # daily bars are 1 day apart, 3 over a weekend;
                         # weekly is 7 and monthly ~30

# A bare ticker resolves to whatever TradingView thinks is the best match, and
# for ambiguous ones that is a foreign listing - IPG came back as Australian
# IperionX and HAL as Hindustan Aeronautics priced in rupees. The exchange in
# the saved filename is the only place that surfaces, so it is checked.
US_EXCHANGES = {"BATS", "NYSE", "NASDAQ", "AMEX", "OTC", "ARCA", "CBOE"}


def ticker_of(name):
    """GOOGL from 'BATS_GOOGL, D.csv', SBNY from 'OTC_DLY_SBNY, D.csv'.

    The prefix is whatever exchange TradingView resolved to and is not the one
    that was asked for, and a delayed feed inserts a second segment - so take
    the last one rather than stripping a single leading field.
    """
    return name.split(",")[0].split("_")[-1].strip()


def done_tickers(outdir):
    """Any exchange prefix counts - a name can resolve somewhere unexpected."""
    out = set()
    if not os.path.isdir(outdir):
        return out
    for f in os.listdir(outdir):
        if f.endswith(".csv") and not f.startswith("_"):
            out.add(ticker_of(f))
    return out


def check(path, since):
    """Return (ok, ncols, nrows, first_year, bar_gap_days).

    Verified from the FILE, not the chart: the time axis is canvas-rendered so
    there is no DOM text to read back, and the CSV is the only authoritative
    statement of what was actually exported.
    """
    try:
        d = pd.read_csv(path)
    except Exception:
        return False, 0, 0, None, None
    ncols, nrows = len(d.columns), len(d)
    yr = gap = None
    try:
        t = pd.to_datetime(d["time"], utc=True, errors="coerce").dropna()
        if len(t) > 10:
            yr = int(t.min().year)
            gap = float(t.sort_values().diff().dt.total_seconds()
                        .median() / 86400.0)
    except Exception:
        pass
    # Reaching back to `since` is NOT required. Half the worklist listed after
    # 2017 - the 2020-21 IPOs and the GEHC/GEV/KVUE/OTIS/SOLV/VLTO spinoffs -
    # so "only reached 2021" is their whole life, not a truncated pull. Depth
    # is instead judged against what the scoring engine needs, and the calendar
    # step is what guarantees everything available was actually loaded.
    ok = (ncols >= MIN_COLS and nrows >= MIN_ROWS
          and gap is not None and gap <= MAX_BAR_GAP_DAYS)
    return ok, ncols, nrows, yr, gap


def export_one(task):
    """Runs in its own process: launch, export one ticker, tear down."""
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

    (tk, sym, outdir, wait_s, timeout_ms, headless, w, h, slot,
     since, attempts, interval, start_date, settle_s, end_date) = task
    res = {"ticker": tk, "symbol": sym, "ok": False, "reason": "", "info": ""}

    signal.signal(signal.SIGINT, signal.SIG_IGN)

    x, y = 40 + (slot % 3) * 60, 40 + (slot % 3) * 40

    ui_ms = min(timeout_ms, 20000)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled",
                      f"--window-size={w},{h}",
                      f"--window-position={x},{y}"])
            try:
                ctx = browser.new_context(
                    storage_state=STATE if os.path.exists(STATE) else None,
                    accept_downloads=True,
                    no_viewport=True)
                page = ctx.new_page()
                page.goto(f"https://www.tradingview.com/chart/{CHART}/"
                          f"?symbol={sym}&interval={interval}",
                          wait_until="domcontentloaded", timeout=timeout_ms)

                try:
                    page.wait_for_selector(NO_SYMBOL, timeout=8000)
                    res["reason"] = "symbol does not exist"
                    return res
                except PWTimeout:
                    pass

                dest = name = None
                ncols = nrows = 0
                yr = gap = None
                for attempt in range(1, attempts + 1):

                    time.sleep(wait_s * attempt)

                    page.wait_for_selector(GOTO_BTN, state="visible",
                                           timeout=timeout_ms)
                    page.click(GOTO_BTN, timeout=ui_ms)
                    page.fill(GOTO_START, start_date, timeout=ui_ms)
                    page.fill(GOTO_END, end_date or time.strftime("%Y-%m-%d"),
                              timeout=ui_ms)
                    page.click(GOTO_SUBMIT, timeout=ui_ms)

                    time.sleep(settle_s)

                    page.click(MENU_BTN, timeout=ui_ms)
                    page.click(DL_ITEM, timeout=ui_ms)
                    with page.expect_download(timeout=timeout_ms) as info:
                        page.click(DL_BTN, timeout=ui_ms)
                    dl = info.value

                    name = (dl.suggested_filename
                            or f"{sym.replace(':', '_')}, 1D.csv")

                    if ticker_of(name) != tk:
                        res["reason"] = (f"charted {ticker_of(name)}, not {tk}"
                                         " - symbol never applied")
                        return res

                    ex = name.split("_")[0] if "_" in name else ""
                    if ex and ex not in US_EXCHANGES:
                        res["reason"] = (f"resolved to {ex} - foreign listing,"
                                         " set the exchange in the worklist")
                        return res

                    dest = os.path.join(outdir, name)
                    dl.save_as(dest)

                    ok, ncols, nrows, yr, gap = check(dest, since)
                    if ok:
                        res["ok"] = True
                        res["info"] = (f"{name}  {ncols}c {nrows}r  "
                                       f"from {yr}  gap {gap:.1f}d  a{attempt}")
                        return res
                    if attempt < attempts:
                        try:
                            os.remove(dest)
                        except OSError:
                            pass

                sus = os.path.join(outdir, "_suspect")
                os.makedirs(sus, exist_ok=True)
                if dest and os.path.exists(dest):
                    shutil.move(dest, os.path.join(sus, name))

                bad_tf = gap is not None and gap > MAX_BAR_GAP_DAYS
                why = ("NOT DAILY: %.1fd bars" % gap if bad_tf else
                       "MISSING INDICATOR? only %d cols (need >=%d) - is 'Rev - Enhanced v2' on the chart?"
                       % (ncols, MIN_COLS) if ncols < MIN_COLS else
                       "too few bars: %d rows (need >=%d)" % (nrows, MIN_ROWS) if nrows < MIN_ROWS else
                       "no parsable timestamps" if gap is None else "unknown")
                res["reason"] = f"{why}, {ncols}c/{nrows}r from {yr} -> _suspect"
                return res
            except PWTimeout as e:
                res["reason"] = f"timeout: {str(e)[:70]}"
                try:
                    page.screenshot(path=os.path.join(outdir, f"_error_{tk}.png"))
                except Exception:
                    pass
                return res
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception as e:
        res["reason"] = f"{type(e).__name__}: {str(e)[:70]}"
        return res


def symbol_for(row):
    ex = row.get("exchange")
    if isinstance(ex, str) and ex.strip() and ex.strip().lower() != "nan":
        return f"{ex.strip()}:{row['ticker']}"
    return str(row["ticker"])


def kill_workers(pool):
    """Best-effort teardown of worker processes and their browsers.

    POSIX can kill an entire process group (Chromium runs under a node driver
    child, so killing only the workers would orphan the windows). Windows has
    no process groups or os.killpg, so fall back to terminating the pool's
    worker processes directly.
    """
    if hasattr(os, "killpg"):
        try:
            os.killpg(os.getpgid(0), signal.SIGKILL)
            return
        except Exception:
            pass
    procs = getattr(pool, "_processes", {})
    for p in procs.values():
        try:
            p.terminate()
        except Exception:
            pass


def do_login():
    from playwright.sync_api import sync_playwright
    os.makedirs(PROFILE, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            PROFILE, headless=False, accept_downloads=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--window-size=1280,800", "--window-position=40,40"],
            no_viewport=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"https://www.tradingview.com/chart/{CHART}/",
                  wait_until="domcontentloaded")
        print("\nIf you are already signed in, just press Enter.")
        input("Press Enter once the chart is loaded and you are signed in... ")
        ctx.storage_state(path=STATE)
        ctx.close()
    print(f"session saved -> {STATE}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tier", type=int, nargs="*")
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--wait", type=float, default=45.0)
    ap.add_argument("--timeout", type=int, default=90000)
    ap.add_argument("--outdir", default=OUTDIR)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--width", type=int, default=1280,
                    help="browser width; 1280x800 fits every MacBook")
    ap.add_argument("--height", type=int, default=800)
    ap.add_argument("--since", type=int, default=2017,
                    help="export must reach back to this year or earlier")
    ap.add_argument("--attempts", type=int, default=2,
                    help="download retries (each waits longer)")
    ap.add_argument("--interval", default="D")
    ap.add_argument("--start", default="2017-01-01",
                    help="date the chart is scrolled back to before download")
    ap.add_argument("--end", default=None,
                    help="right edge of the viewport (YYYY-MM-DD). Defaults to "
                         "today. Pin it to make a scrape reproducible: two runs "
                         "on different days otherwise cover different windows "
                         "and the bar counts will not line up.")
    ap.add_argument("--settle", type=float, default=25.0,
                    help="seconds to let the pulled-in history recompute")
    ap.add_argument("--redo", action="store_true")
    args = ap.parse_args()

    if args.login:
        return do_login()

    if not os.path.exists(STATE):
        print("no saved session - run:  python tv_export.py --login")
        return 1

    os.makedirs(args.outdir, exist_ok=True)
    w = pd.read_csv(WORKLIST)
    if args.tier:
        w = w[w.tier.isin(args.tier)]
    if args.tickers:
        w = w[w.ticker.isin(args.tickers)]
    w = w.sort_values("order")

    have = set() if args.redo else done_tickers(args.outdir)
    todo = w[~w.ticker.isin(have)]
    if args.limit:
        todo = todo.head(args.limit)

    print(f"worklist {len(w)}  done {len(have)}  to export {len(todo)}  "
          f"workers {args.workers}")
    if not len(todo):
        return 0

    tasks = [(r["ticker"], symbol_for(r), args.outdir, args.wait,
              args.timeout, args.headless, args.width, args.height, i,
              args.since, args.attempts, args.interval, args.start,
              args.settle, args.end)
             for i, (_, r) in enumerate(todo.iterrows())]

    ok = 0
    fails = []
    t0 = time.time()
    pool = ProcessPoolExecutor(max_workers=args.workers)
    try:
        futs = {pool.submit(export_one, t): t[0] for t in tasks}
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            if r["ok"]:
                ok += 1
                print(f"[{i}/{len(tasks)}] {r['ticker']:6s} OK    {r['info']}",
                      flush=True)
            else:
                fails.append(r)
                print(f"[{i}/{len(tasks)}] {r['ticker']:6s} FAIL  "
                      f"{r['reason']}", flush=True)
        pool.shutdown(wait=True)
    except KeyboardInterrupt:
        # The default `with` exit waits for in-flight tickers, so Ctrl-C used to
        # hang for minutes with windows still up. Drop the queue, then kill the
        # process group: Playwright runs Chromium under a node driver, so
        # killing only the workers orphans the windows instead of closing them.
        print("\nstopping - killing browsers", flush=True)
        pool.shutdown(wait=False, cancel_futures=True)
        kill_workers(pool)

    mins = (time.time() - t0) / 60
    print(f"\n{ok} ok, {len(fails)} failed in {mins:.1f} min")
    if fails:
        p = os.path.join(args.outdir, "_failures.csv")
        pd.DataFrame(fails).to_csv(p, index=False)
        print(f"failures -> {p}")
        bad = [f["ticker"] for f in fails if "does not exist" in f["reason"]]
        if bad:
            print("bad symbols (fix exchange in export_worklist.csv): "
                  + " ".join(bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())