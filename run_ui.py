"""
Unified Stock Trading Operations Cockpit UI Server.
Modular entry point delegating to src.ui application package.
"""

from __future__ import annotations

import argparse
import threading
import webbrowser
import uvicorn

from src.ui.app import create_app
from src.ui.state import init_db as _init_db, get_db as _get_db
from src.ui.routes.research import (
    ResearchRequest,
    trigger_research,
    get_research_queue,
    extract_report_card as _extract_report_card,
    find_chart_path as _find_chart_path,
)
from src.ui.routes.copilot import (
    CopilotChatRequest,
    SaveChatMessageRequest,
    ExecutePythonRequest,
    _save_chat_turn,
    get_copilot_chat_sessions_endpoint,
    get_copilot_chat_history_endpoint,
    delete_copilot_chat_session_endpoint,
    copilot_execute_python_endpoint,
)
from src.ui.services.copilot_context import (
    _build_copilot_context_and_tools,
    _build_single_ticker_context,
    _extract_targeted_schwab_strikes,
    _detect_tickers_from_prompt,
    _detect_ticker_metadata,
    _get_company_name,
)

app = create_app()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stock Trading Operations Cockpit UI")
    parser.add_argument("--port", type=int, default=8050, help="Port to run the UI server on (default 8050)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open web browser")
    parser.add_argument("--reload", action="store_true", help="Enable live reloading for development")
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.port}"
    print(f"\n=======================================================")
    print(f"STOCK TRADING OPERATIONS COCKPIT LIVE AT:")
    print(f">> {url}")
    print(f"=======================================================\n")

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run("run_ui:app", host="127.0.0.1", port=args.port, reload=args.reload, log_level="warning")
