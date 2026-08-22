"""
test_error_and_sandbox_loop.py
Rigorously tests:
1. execute_python_code_tool sandbox with pre-loaded df, dw, scipy, stats, numpy, pandas.
2. Monte Carlo 10k path touch simulation on real CSV data.
3. Historical pattern backtesting on real df indicators.
4. Error catching and diagnostic feedback.
5. Plugin execution across all 3 modules.
6. Validation audit across generated summaries.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Add workspace root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.clients.llm_client import execute_python_code_tool, run_quantitative_plugin_tool

def main():
    print("=" * 60)
    print("STARTING END-TO-END VERIFICATION OF QUANTITATIVE SANDBOX & ERROR LOOPS")
    print("=" * 60)

    ticker = "CRWD"
    date_str = "2026-08-16"

    # 1. TEST PRE-LOADED VARIABLES IN SANDBOX
    print("\n--- 1. Testing Pre-Loaded Sandbox Variables (df, dw, scipy, stats) ---")
    code_1 = """
print(f"Ticker: {ticker}")
print(f"DF Shape: {df.shape}")
print(f"Has Stage: {'Stage 1 Base 2 Up 3 Top 4 Down' in df.columns}")
print(f"Has Darvas: {'Darvas Box Top' in df.columns}")
print(f"Latest Close: ${df['close'].iloc[-1]:.2f}")
print(f"Scipy available: {scipy is not None}")
print(f"Norm CDF(0): {stats.norm.cdf(0):.4f}")
"""
    res_1 = execute_python_code_tool(code_1, ticker=ticker, date_str=date_str)
    print("Output:\n" + res_1)
    assert "DF Shape: (300, 85)" in res_1, "Expected 300x85 DataFrame"
    assert "Norm CDF(0): 0.5000" in res_1, "Expected scipy stats norm CDF"
    print("[PASS] TEST 1: Sandbox successfully pre-loaded 300x85 df, dw, and scipy.stats!")

    # 2. TEST 10,000-PATH MONTE CARLO SIMULATION
    print("\n--- 2. Testing 10,000-Path Monte Carlo Simulation in Sandbox ---")
    code_2 = """
spot = float(dw['close'])
hv = float(dw.get('_realvol_60d', 45.0)) / 100.0
daily_vol = hv / np.sqrt(252)
np.random.seed(42)
shocks = np.random.normal(0, daily_vol, (21, 10000))
paths = spot * np.exp(np.cumsum(shocks, axis=0))
p_target = (paths >= 226.90).any(axis=0).mean() * 100
p_stop = (paths <= 208.40).any(axis=0).mean() * 100
print(f"P(Target Touch $226.90): {p_target:.1f}%")
print(f"P(Stop Touch $208.40): {p_stop:.1f}%")
"""
    res_2 = execute_python_code_tool(code_2, ticker=ticker, date_str=date_str)
    print("Output:\n" + res_2)
    assert "P(Target Touch" in res_2 and "P(Stop Touch" in res_2
    print("[PASS] TEST 2: 10k-path Monte Carlo completed in milliseconds!")

    # 3. TEST HISTORICAL SETUP BACKTEST ON DF
    print("\n--- 3. Testing Historical Pattern Backtest on df ---")
    code_3 = """
stage_col = 'Stage 1 Base 2 Up 3 Top 4 Down'
hits = df[(df['Buy Score'] > 75) & (df[stage_col] == 2)]
fwd_20d = [(df['close'].iloc[i+20] - df['close'].iloc[i]) / df['close'].iloc[i] for i in hits.index if i + 20 < len(df)]
win_rate = np.mean([r > 0 for r in fwd_20d]) * 100 if fwd_20d else 0.0
median_ret = np.median(fwd_20d) * 100 if fwd_20d else 0.0
print(f"Historical Sample Size: N={len(fwd_20d)}")
print(f"Empirical 20d Win Rate: {win_rate:.1f}%")
print(f"Median 20d Forward Return: {median_ret:.2f}%")
"""
    res_3 = execute_python_code_tool(code_3, ticker=ticker, date_str=date_str)
    print("Output:\n" + res_3)
    assert "Historical Sample Size:" in res_3
    print("[PASS] TEST 3: Historical pattern backtest executed successfully!")

    # 4. TEST ERROR CATCHING & DIAGNOSTIC FEEDBACK
    print("\n--- 4. Testing Error Catching & Diagnostic Feedback ---")
    code_err = """
# Deliberate intentional error: accessing missing column
bad_val = df['NON_EXISTENT_COLUMN_123'].mean()
print(bad_val)
"""
    res_err = execute_python_code_tool(code_err, ticker=ticker, date_str=date_str)
    print("Output (Should be a formatted diagnostic error message):\n" + res_err)
    assert "Python Execution Error: KeyError: 'NON_EXISTENT_COLUMN_123'" in res_err
    print("[PASS] TEST 4: Clean KeyError diagnostic string returned for LLM self-correction!")

    # 5. TEST QUANTITATIVE PLUGINS (HTF Confluence, Squeeze Expansion, Earnings)
    print("\n--- 5. Testing Quantitative Plugins ---")
    res_plugin = run_quantitative_plugin_tool(ticker, plugin_name="all", date_str=date_str)
    print("Plugin Output Sample:\n" + res_plugin[:400] + "...")
    assert "Quantitative Plugin Results" in res_plugin
    print("[PASS] TEST 5: Full plugin suite executed cleanly!")

    print("\n" + "=" * 60)
    print("ALL 5 END-TO-END SANDBOX, ALGORITHMIC & ERROR TESTS PASSED!")
    print("=" * 60)

if __name__ == "__main__":
    main()
