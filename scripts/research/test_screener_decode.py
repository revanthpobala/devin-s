"""Verify alert_parser decodes the rev-screener.pine JSON payloads correctly."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.logic.alert_parser import parse_alert

# Replicate the PositionManager exit heuristic on decoded screener alerts:
# a NEUTRAL/LONG side must NEVER be misread as an exit event.
import src.tracking.position_monitor as pm

PAYLOADS = {
    "research_reversal": {
        "subject": "META - Research Reversal (Any Stage)",
        "body": json.dumps({
            "ticker": "META", "setup": "Research Reversal (Any Stage)", "side": "LONG",
            "price": 612.34, "stage": 4, "buy": 38.2, "sell": 22.1, "prime": 0,
            "revL": 8.5, "revS": 0.0, "vcp": 0, "dir_prob": 41.3, "entry_rank": 34.7,
            "proxy_rr": 2.41, "atrs_up": 0.62, "hv20_low": True, "put_ok": False,
            "time": 1756224000,
        }),
    },
    "stagnation_neutral": {
        "subject": "AES - HV20 Low (Research feed)",
        "body": json.dumps({
            "ticker": "AES", "setup": "Stagnation (Research)", "side": "NEUTRAL",
            "price": 14.02, "stage": 2, "buy": 61.0, "sell": 18.4, "prime": 1,
            "revL": 0.0, "revS": 0.0, "vcp": 1, "dir_prob": 58.9, "entry_rank": 71.2,
            "proxy_rr": 4.02, "atrs_up": 0.31, "hv20_low": True, "put_ok": True,
            "time": 1756224000,
        }),
    },
    "ignition_with_emoji": {
        # The alertcondition title 7 has a rocket emoji in its message; the JSON
        # payload itself is clean, but TV prepends the message text to the body.
        "subject": "IFF - Early Ignition",
        "body": "IFF - Early Ignition: RS leader breaking out of a base \U0001F680 @ 98.11\n\n"
        + json.dumps({
            "ticker": "IFF", "setup": "Early Ignition", "side": "LONG",
            "price": 98.11, "stage": 2, "buy": 74.0, "sell": 29.3, "prime": 1,
            "revL": 0.0, "revS": 0.0, "vcp": 2, "dir_prob": 62.0, "entry_rank": 55.4,
            "proxy_rr": 1.87, "atrs_up": 1.44, "hv20_low": False, "put_ok": False,
            "time": 1756224000,
        }),
    },
    "nasdaq_prefix": {
        # TV emails sometimes prefix the ticker with the exchange in the body.
        "subject": "NASDAQ:AAPL - Research Reversal (Any Stage)",
        "body": json.dumps({
            "ticker": "AAPL", "setup": "Research Reversal (Any Stage)", "side": "LONG",
            "price": 241.05, "stage": 3, "buy": 30.1, "sell": 40.2, "prime": 0,
            "revL": 7.5, "revS": 0.0, "vcp": 0, "dir_prob": 44.0, "entry_rank": 22.0,
            "proxy_rr": 3.10, "atrs_up": 0.40, "hv20_low": False, "put_ok": False,
            "time": 1756224000,
        }),
    },
}

KEYS = [
    "symbol", "strategy", "action", "alert_price", "setup", "side", "stage",
    "buy", "sell", "prime", "revL", "revS", "vcp", "dir_prob", "entry_rank",
    "proxy_rr", "atrs_up", "hv20_low", "put_ok", "time",
]


def main():
    failures = []
    for name, p in PAYLOADS.items():
        parsed = parse_alert(p["subject"], p["body"])
        print(f"===== {name} =====")
        for k in KEYS:
            print(f"  {k:14s} = {parsed.get(k)!r}")

        # Assertions
        if parsed["strategy"] != "Daily":
            failures.append(f"{name}: strategy={parsed['strategy']!r}, expected Daily")
        if parsed["symbol"] not in {"META", "AES", "IFF", "AAPL"}:
            failures.append(f"{name}: symbol={parsed['symbol']!r}")
        if parsed["alert_price"] is None:
            failures.append(f"{name}: alert_price is None")
        if not parsed.get("setup"):
            failures.append(f"{name}: setup missing")
        if parsed.get("hv20_low") is not (p["body"] and "true" in p["body"]):
            pass  # hv20_low check below
        expected_hv = json.loads(re.search(r"\{.*\}", p["body"], re.DOTALL).group(0))["hv20_low"]
        if parsed.get("hv20_low") is not expected_hv:
            failures.append(f"{name}: hv20_low={parsed.get('hv20_low')!r}, expected {expected_hv!r}")
        if isinstance(parsed.get("proxy_rr"), str):
            failures.append(f"{name}: proxy_rr is str not float: {parsed.get('proxy_rr')!r}")
        # action must equal the side from JSON (LONG/NEUTRAL)
        expected_side = json.loads(re.search(r"\{.*\}", p["body"], re.DOTALL).group(0))["side"]
        if parsed.get("action") != expected_side:
            failures.append(f"{name}: action={parsed.get('action')!r}, expected {expected_side!r}")
        # A screener alert must NEVER trip the exit heuristic (no position close)
        if pm._is_exit_event(parsed):
            failures.append(f"{name}: _is_exit_event returned True — would close a position!")
    print()
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("All decode checks passed.")


if __name__ == "__main__":
    main()
