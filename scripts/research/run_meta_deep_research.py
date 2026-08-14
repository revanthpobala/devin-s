import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

# Ensure repo root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

# Ensure single concurrency for 16GB single slot
os.environ["LLM_LOCAL_CONCURRENCY"] = "1"

load_dotenv()

from src.logic.deep_research import run_deep_research

def main():
    ticker = "META"
    date_str = "2026-08-14"
    
    print("=" * 75)
    print(f"=== LAUNCHING LOCAL AGENTIC DEEP RESEARCH: {ticker} ({date_str}) ===")
    print("=" * 75)
    print("Engine: Qwen3.8-27B-UD-Q4_K_XL + mmproj-Qwen3.8-27B-F16 (Local GPU)")
    print("Inputs: META_chart.png, META_chart_zoom.png, META_datawindow.json, META_tv_strategies.json\n")

    start_time = time.time()
    try:
        run_deep_research(date_str, ticker, force_local=True)
        elapsed = time.time() - start_time
        print("\n" + "=" * 75)
        print(f"=== DEEP RESEARCH FINISHED IN {elapsed:.1f}s ===")
        print("=" * 75 + "\n")
        
        # Read and display the generated report
        report_path = BASE_DIR / "reports" / date_str / f"{ticker}_summary.md"
        raw_report_path = BASE_DIR / "data" / "raw" / date_str / f"{ticker}_gemini_thesis.md"
        
        final_path = report_path if report_path.exists() else raw_report_path
        if final_path.exists():
            print("--- [GENERATED PORTFOLIO MANAGER THESIS] ---")
            print(final_path.read_text(encoding="utf-8"))
            print("--------------------------------------------")
        else:
            print(f"Report file not found at {final_path}")

    except Exception as e:
        print(f"\n[ERROR] Deep Research failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
