#!/usr/bin/env python3
"""Count connect timeouts per host per hour in backend.log — run on the rig.

Usage:
    python scripts/research/count_timeouts.py [path/to/backend.log]
    python scripts/research/count_timeouts.py --sockets   # TIME_WAIT sockets right now
"""
import re
import subprocess
import sys
from collections import Counter

TS = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2})")
HOST = re.compile(r"host='([^']+)'|https?://([^/\s:]+)")
CLIENT = re.compile(r"(schwab|tastytrade|alpaca|finnhub|alpha ?vantage|openrouter|nvidia|adanos|bls|yfinance|polygon)", re.I)


def count_log(path: str) -> None:
    per_hour = Counter()
    for line in open(path, errors="ignore"):
        low = line.lower()
        if "timeout" not in low or "connect" not in low:
            continue
        m = HOST.search(line)
        host = (m.group(1) or m.group(2)).lower() if m else None
        if not host:
            c = CLIENT.search(line)
            host = c.group(1).lower() if c else "?"
        ts = TS.search(line)
        hour = f"{ts.group(1)} {ts.group(2)}h" if ts else "?"
        per_hour[(hour, host)] += 1
    if not per_hour:
        print(f"no connect timeouts found in {path}")
        return
    print(f"{'hour':14s} {'host':42s} count")
    for (hour, host), n in sorted(per_hour.items()):
        print(f"{hour:14s} {host:42s} {n}")


def count_sockets() -> None:
    out = subprocess.run(["netstat", "-an"], capture_output=True, text=True).stdout
    counts = Counter()
    for line in out.splitlines():
        if "TIME_WAIT" not in line:
            continue
        cols = line.split()
        remote = cols[2] if len(cols) >= 4 else "?"
        counts[remote.split(":")[0]] += 1
    print(f"TIME_WAIT sockets: {sum(counts.values())}")
    for host, n in counts.most_common(15):
        print(f"  {host:42s} {n}")


if __name__ == "__main__":
    if "--sockets" in sys.argv:
        count_sockets()
    else:
        count_log(sys.argv[1] if len(sys.argv) > 1 else "logs/backend.log")
