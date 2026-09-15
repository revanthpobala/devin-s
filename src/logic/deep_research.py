"""src/logic/deep_research.py — backward-compatibility shim.

The actual implementation lives in the ``src.logic.deep_research`` package.
All public symbols are re-exported here so existing callers are unaffected.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on path when this file is run directly
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Re-export everything that was previously defined at module level
from src.logic.deep_research.pipeline import run_deep_research, run_ponytail_pm_review  # noqa: F401
from src.logic.deep_research.prefetch import prefetch_deep_research_context  # noqa: F401
from src.logic.deep_research.artifact_loader import (  # noqa: F401
    load_triage_record as _load_triage_record,
    _dossier_stale_check as _dossier_stale,
)
from src.logic.deep_research.context_builder import (  # noqa: F401
    format_flags_block as _format_flags_block,
    format_engine_math_block as _format_engine_math_block,
    format_triggers_block as _format_triggers_block,
    format_scenario_block as _format_scenario_block,
    format_state_response_block as _format_state_response_block,
    format_unmasked_recency_block as _format_unmasked_recency_block,
)

if __name__ == "__main__":
    import re
    import argparse
    from datetime import datetime
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Run deep research phase")
    parser.add_argument(
        "date",
        nargs="?",
        default=None,
        help="Target date (YYYY-MM-DD). If a non-date token is given, "
        "it is treated as --ticker using today's date.",
    )
    parser.add_argument("--ticker", type=str, help="Run only on a specific ticker")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Force 100 percent local LLM inference (no OpenRouter/remote API calls)",
    )

    args = parser.parse_args()

    target_date = args.date
    target_ticker = args.ticker
    if target_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date) and not target_ticker:
        target_ticker = target_date
        target_date = None

    run_deep_research(
        target_date or datetime.now().strftime("%Y-%m-%d"),
        target_ticker,
        force_local=args.local,
    )
