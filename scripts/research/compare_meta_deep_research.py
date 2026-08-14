import os
import sys
import time
import shutil
from pathlib import Path
from dotenv import load_dotenv

# Ensure repo root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

# Ensure single concurrency for 16GB single slot local inference
os.environ["LLM_LOCAL_CONCURRENCY"] = "1"

load_dotenv()

from src.logic.deep_research import run_deep_research

def main():
    ticker = "META"
    date_str = "2026-08-14"
    reports_dir = BASE_DIR / "reports" / date_str
    reports_dir.mkdir(parents=True, exist_ok=True)
    summary_path = reports_dir / f"{ticker}_summary.md"
    
    local_output_path = reports_dir / f"{ticker}_local_qwen38.md"
    remote_output_path = reports_dir / f"{ticker}_gpt56.md"

    print("=" * 80)
    print(f"=== DEEP RESEARCH BENCHMARK: {ticker} ({date_str}) ===")
    print("=" * 80)
    print("Engine 1: Local Qwen3.8-27B (Vision + CoT reasoning on RTX 5070 Ti)")
    print("Engine 2: Frontier Cloud LLM (OpenRouter / GPT-5.6 / Minimax)")
    print("=" * 80 + "\n")

    # Step 1: Run with Local Qwen3.8-27B
    print(f"--- [PHASE 1/2] RUNNING LOCAL QWEN 3.8 27B (+ VISION & REASONING) ---")
    start_local = time.time()
    try:
        run_deep_research(date_str, ticker, force_local=True)
        local_elapsed = time.time() - start_local
        print(f"\n[SUCCESS] Local Deep Research completed in {local_elapsed:.1f}s")
        if summary_path.exists():
            shutil.copy(summary_path, local_output_path)
            print(f"[SAVED] Local thesis saved to: {local_output_path}")
    except Exception as e:
        print(f"\n[ERROR] Local Deep Research failed: {e}")
        import traceback
        traceback.print_exc()

    # Step 2: Run with Remote GPT / Frontier Model
    print(f"\n--- [PHASE 2/2] RUNNING FRONTIER CLOUD LLM (GPT 5.6 / OPENROUTER) ---")
    start_remote = time.time()
    try:
        run_deep_research(date_str, ticker, force_local=False)
        remote_elapsed = time.time() - start_remote
        print(f"\n[SUCCESS] Remote Deep Research completed in {remote_elapsed:.1f}s")
        if summary_path.exists():
            shutil.copy(summary_path, remote_output_path)
            print(f"[SAVED] Remote thesis saved to: {remote_output_path}")
    except Exception as e:
        print(f"\n[ERROR] Remote Deep Research failed: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 80)
    print("=== BENCHMARK EXECUTION COMPLETE ===")
    print("=" * 80 + "\n")

if __name__ == "__main__":
    main()
