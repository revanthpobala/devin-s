"""Health worker: runs the slow system probes on a timer and writes data/health_snapshot.json.

The UI server's /api/status only reads that file, so a slow GPU/LLM/broker probe never holds a request.
Usage: python run_health_worker.py [--interval 5]
"""

import argparse
import json
import logging
import os
import time

from src.ui.routes.status import HEALTH_SNAPSHOT, collect_slow_probes

logger = logging.getLogger("health_worker")


def write_snapshot() -> None:
    probes = collect_slow_probes()
    tmp = HEALTH_SNAPSHOT.with_suffix(".tmp")
    tmp.write_text(json.dumps({"ts": time.time(), "probes": probes}, default=str), encoding="utf-8")
    os.replace(tmp, HEALTH_SNAPSHOT)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    HEALTH_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    while True:
        started = time.time()
        try:
            write_snapshot()
        except Exception as e:
            logger.warning("health snapshot failed: %s", e)
        if args.once:
            return
        time.sleep(max(0.5, args.interval - (time.time() - started)))


if __name__ == "__main__":
    main()
