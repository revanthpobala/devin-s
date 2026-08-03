import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from src import config

# Setup logging
os.makedirs(config.LOGS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (Orchestrator) %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOGS_DIR / "orchestrator.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("orchestrator")

unsloth_process = None


def _get_python_exe() -> str:
    """Return absolute path to virtualenv python executable (macOS + Windows)."""
    base = config.BASE_DIR / ".venv"
    win_py = base / "Scripts" / "python.exe"
    nix_py = base / "bin" / "python"
    if win_py.exists():
        return str(win_py)
    elif nix_py.exists():
        return str(nix_py)
    return sys.executable


def _get_llama_server_exe() -> str:
    """Return absolute path to llama-server binary (macOS + Windows)."""
    base = config.BASE_DIR / "llama-cpp-server"
    win_exe = base / "llama-server.exe"
    nix_exe = base / "llama-server"
    if win_exe.exists():
        return str(win_exe)
    elif nix_exe.exists():
        return str(nix_exe)
    return "llama-server"


def is_market_hours() -> bool:
    """Check if current Mountain Time is within configured market hours (Monday-Friday)."""
    now_mt = datetime.now(ZoneInfo("America/Denver"))
    if now_mt.weekday() >= 5:  # Saturday or Sunday
        return False
    start_time = now_mt.replace(
        hour=getattr(config, "MARKET_OPEN_HOUR", 7),
        minute=getattr(config, "MARKET_OPEN_MINUTE", 15),
        second=0,
        microsecond=0,
    )
    end_time = now_mt.replace(
        hour=getattr(config, "MARKET_CLOSE_HOUR", 20),
        minute=getattr(config, "MARKET_CLOSE_MINUTE", 0),
        second=0,
        microsecond=0,
    )
    return start_time <= now_mt <= end_time


def _ping_llm_health(timeout: float = 5.0) -> bool:
    """Return True if the local llama-server /health endpoint responds in time."""
    try:
        import requests

        r = requests.get("http://127.0.0.1:8000/health", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def _restart_llm_server():
    """Terminate + relaunch the llama_cpp server. Returns the new process or None."""
    global unsloth_process
    if unsloth_process is not None and unsloth_process.poll() is None:
        logger.warning("Watchdog: terminating wedged LLM server...")
        unsloth_process.terminate()
        try:
            unsloth_process.wait(timeout=15)
        except Exception:
            unsloth_process.kill()
    gguf_path = os.path.join(config.BASE_DIR, "models", "Qwen3.5-9B-Q8_0.gguf")
    llama_server_exe = _get_llama_server_exe()
    unsloth_process = subprocess.Popen(
        [
            llama_server_exe,
            "-m",
            gguf_path,
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "-c",
            "24000",
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
        ],
        cwd=config.BASE_DIR,
    )
    logger.info(
        f"Watchdog: LLM server restarted (PID {unsloth_process.pid}). Waiting 15s for init..."
    )
    time.sleep(15)


def main():
    global unsloth_process
    logger.info("Starting Market Hours Orchestrator...")
    logger.info("Control window: Monday-Friday, 7:15 AM - 8:00 PM Mountain Time.")
    logger.info("LLM: llama_cpp.server on port 8000.")

    python_exe = _get_python_exe()
    tracker_process = None
    external_llm_running = False

    try:
        while True:
            active = is_market_hours()

            if active:
                import socket

                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    port_in_use = s.connect_ex(("127.0.0.1", 8000)) == 0

                if port_in_use:
                    if not external_llm_running and unsloth_process is None:
                        logger.info(
                            "LLM server is already running independently on port 8000. Using existing process."
                        )
                        external_llm_running = True
                else:
                    external_llm_running = False

                # Start LLM Server if not running and not external
                if not external_llm_running and (
                    unsloth_process is None or unsloth_process.poll() is not None
                ):
                    if unsloth_process is not None:
                        logger.warning("LLM server exited unexpectedly — restarting...")
                    else:
                        logger.info("Market is open. Starting llama_cpp API Server...")

                    gguf_path = os.path.join(config.BASE_DIR, "models", "Qwen3.5-9B-Q8_0.gguf")
                    llama_server_exe = _get_llama_server_exe()
                    # --reasoning off: Qwen3.5 via llama-server cannot emit BOTH clean JSON
                    # content AND a thinking trace. With thinking OFF we can also enforce a
                    # strict GBNF json_schema on local calls -> near-zero parse failures.
                    # -c 32768 / --parallel 4 -> 8192 tokens/slot (32768/4). The triage prompt
                    # is ~6218 tokens; the old -c 24000/--parallel 4 (6000/slot) was BELOW that,
                    # so llama-server returned HTTP 400 and tickers fell back to deterministic
                    # with no LLM screen. 8192/slot gives headroom. (Safe no-VRAM-change alt:
                    # --parallel 3 -> 8000/slot.) LLM_LOCAL_CONCURRENCY=4 is kept in sync with
                    # --parallel 4. The deterministic filter (not the LLM) owns the verdict.
                    unsloth_process = subprocess.Popen(
                        [
                            llama_server_exe,
                            "-m",
                            gguf_path,
                            "--host",
                            "127.0.0.1",
                            "--port",
                            "8000",
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
                        ],
                        cwd=config.BASE_DIR,
                    )
                    logger.info(
                        f"LLM server started (PID {unsloth_process.pid}). Waiting 15s for initialization..."
                    )
                    time.sleep(15)

                # Watchdog: a wedged (but not crashed) llama-server returns no health
                # or hangs; restart it so triage doesn't silently fail for the rest of
                # the run. Runs every iteration while we OWN the server (not external).
                if (
                    not external_llm_running
                    and unsloth_process is not None
                    and unsloth_process.poll() is None
                ):
                    if not _ping_llm_health(timeout=5.0):
                        logger.warning("Watchdog: LLM /health unresponsive — server may be wedged.")
                        _restart_llm_server()

                # Start Email Alert Ingestor Loop if not already running
                if tracker_process is None or tracker_process.poll() is not None:
                    if tracker_process is not None:
                        logger.warning("Email Alert Ingestor exited unexpectedly — restarting...")
                    else:
                        logger.info("Market is open. Starting Email Alert Ingestor Loop...")

                    tracker_process = subprocess.Popen(
                        [python_exe, "main.py", "--loop"],
                        cwd=os.path.dirname(os.path.abspath(__file__)),
                    )
                    logger.info(f"Email Alert Ingestor started (PID {tracker_process.pid}).")

            else:
                # Outside market hours — shut down tracker to free resources
                if tracker_process is not None and tracker_process.poll() is None:
                    logger.info("Market is closed. Shutting down Email Alert Ingestor...")
                    tracker_process.terminate()
                    tracker_process.wait()
                    tracker_process = None
                    logger.info("Ingestor stopped.")

                if unsloth_process is not None and unsloth_process.poll() is None:
                    logger.info("Shutting down LLM Server to free resources...")
                    unsloth_process.terminate()
                    unsloth_process.wait()
                    unsloth_process = None
                    logger.info("LLM server stopped.")

                # Log status occasionally
                now_mt = datetime.now(ZoneInfo("America/Denver"))
                if now_mt.minute % 15 == 0 and now_mt.second < 30:
                    logger.info("Outside market hours. Orchestrator idling...")

            time.sleep(30)

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Shutting down all active services...")
        if tracker_process is not None and tracker_process.poll() is None:
            tracker_process.terminate()
            tracker_process.wait()
            logger.info("Email Alert Ingestor terminated.")

        if unsloth_process is not None and unsloth_process.poll() is None:
            unsloth_process.terminate()
            unsloth_process.wait()
            logger.info("LLM server terminated.")

        logger.info("All child processes terminated. Exiting orchestrator.")
        sys.exit(0)


if __name__ == "__main__":
    main()
