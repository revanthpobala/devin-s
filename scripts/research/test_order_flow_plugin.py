"""
scripts/research/test_order_flow_plugin.py

Tests the OrderFlowPlugin and its integration with PluginManager and LLM Tool Dispatch.
"""

import sys
import json
from pathlib import Path
import pandas as pd

# Add workspace root
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.plugins.order_flow_plugin import OrderFlowPlugin
from src.plugins.plugin_manager import plugin_manager
from src.clients.llm_client import run_quantitative_plugin_tool

def main():
    print("=" * 60)
    print("TESTING ORDER FLOW & INSTITUTIONAL VOLUME PROFILE PLUGIN")
    print("=" * 60)

    for ticker in ["CRWD", "CRWV"]:
        date_str = "2026-08-16"
        csv_path = BASE_DIR / "data" / "raw" / date_str / ticker / f"{ticker}_datawindow.csv"
        dw_path = BASE_DIR / "data" / "raw" / date_str / ticker / f"{ticker}_datawindow.json"

        if not csv_path.exists():
            print(f"Skipping {ticker} (CSV not found)")
            continue

        df = pd.read_csv(csv_path)
        dw = json.loads(dw_path.read_text(encoding="utf-8")) if dw_path.exists() else {}

        print(f"\n--- Testing OrderFlowPlugin directly on {ticker} ---")
        plugin = OrderFlowPlugin()
        res = plugin.run(ticker, df, dw)
        for k, v in res.items():
            print(f"  {k}: {v}")

        assert "_vol_accumulation_ratio_20d" in res, "Missing 20d accumulation ratio"
        assert "_chaikin_money_flow_20d" in res, "Missing Chaikin Money Flow"
        assert "_obv_20d_trend" in res, "Missing OBV trend"
        assert "_order_flow_summary" in res, "Missing order flow summary"
        print(f"[PASS] Direct plugin run on {ticker} succeeded!")

        print(f"\n--- Testing Tool Dispatch via run_quantitative_plugin_tool on {ticker} ---")
        tool_out = run_quantitative_plugin_tool(ticker, plugin_name="order_flow", date_str=date_str)
        print("Tool Output Snippet:\n" + tool_out[:400] + "...")
        assert "Order Flow" in tool_out or "accumulation" in tool_out.lower()
        print(f"[PASS] Tool dispatch on {ticker} succeeded!")

    print("\n--- Testing PluginManager.run_all() across all 4 plugins ---")
    all_plugins = plugin_manager.list_plugins()
    print(f"Registered Plugins ({len(all_plugins)}): {[p['name'] for p in all_plugins]}")
    assert any(p["name"] == "order_flow" for p in all_plugins), "order_flow not registered in PluginManager"
    
    sample_res = plugin_manager.run_all("CRWD", df, dw)
    print("Sample enriched keys from run_all():", sorted([k for k in sample_res.keys() if k.startswith("_")]))
    assert "_order_flow_summary" in sample_res
    print("[PASS] PluginManager.run_all() succeeded!")

    print("\n" + "=" * 60)
    print("ALL ORDER FLOW PLUGIN INTEGRATION TESTS PASSED!")
    print("=" * 60)

if __name__ == "__main__":
    main()
