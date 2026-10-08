"""Standalone LLM-server watchdog.

Pings the local llama-server /health endpoint on an interval; if it is
unresponsive (wedged under memory pressure) or the process has died, it
relaunches the server using the SAME launch args as start_llm_server.bat
(-c 32768, --parallel 4, --reasoning off; 8192 tokens/slot so the ~6218-token
triage prompt fits without an HTTP 400). This turns "run died at
ticker 90" into "self-healed", with no external dependency.

Usage:
    python scripts/launchers/llm_watchdog.py
    python scripts/launchers/llm_watchdog.py --interval 30 --timeout 5
"""

import argparse
import os
import subprocess
import sys
import time

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LLAMA_EXE = os.path.join(BASE_DIR, "llama-cpp-server", "llama-server.exe")
GGUF = os.path.join(BASE_DIR, "models", "Qwen3.5-9B-Q8_0.gguf")
PORT = 8000

LAUNCH_ARGS = [
    LLAMA_EXE,
    "-m",
    GGUF,
    "--host",
    "127.0.0.1",
    "--port",
    str(PORT),
    "-c",
    "32768",
    "-fa",
    "on",
    "-ctk",
    "q8_0",
    "-ctv",
    "q8_0",
    "-ngl",
    "999",
    "-a",
    "gpt-4",
    "--parallel",
    "4",
    "--reasoning",
    "off",
]


def launch() -> subprocess.Popen:
    print(f"[watchdog] launching llama-server ({' '.join(LAUNCH_ARGS)})")
    proc = subprocess.Popen(LAUNCH_ARGS, cwd=BASE_DIR)
    print(f"[watchdog] started PID {proc.pid}; waiting 15s for init...")
    time.sleep(15)
    return proc


def healthy(timeout: float) -> bool:
    try:
        r = requests.get(f"http://127.0.0.1:{PORT}/health", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--timeout", type=float, default=5.0)
    args = ap.parse_args()

    if not os.path.exists(LLAMA_EXE):
        print(f"[watchdog] ERROR: llama-server not found at {LLAMA_EXE}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(GGUF):
        print(f"[watchdog] ERROR: GGUF not found at {GGUF}", file=sys.stderr)
        sys.exit(1)

    proc = None
    print(f"[watchdog] monitoring llama-server on port {PORT} (interval={args.interval}s)")
    try:
        while True:
            # Detect an external server we don't own: if port is up but we never
            # launched a process, attach to it (don't restart someone else's).
            if proc is None:
                if healthy(args.timeout):
                    print("[watchdog] external llama-server already up; monitoring only.")
                else:
                    proc = launch()
            else:
                if proc.poll() is not None:
                    print("[watchdog] process exited; relaunching.")
                    proc = launch()
                elif not healthy(args.timeout):
                    print("[watchdog] /health unresponsive (wedged); restarting.")
                    proc.terminate()
                    try:
                        proc.wait(timeout=15)
                    except Exception:
                        proc.kill()
                    proc = launch()
                else:
                    print(f"[watchdog] healthy (PID {proc.pid}).")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("[watchdog] stopping.")
        if proc is not None and proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    main()
