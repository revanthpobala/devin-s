import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from src.ui.services.opportunity_service import (
    get_live_market_pulse,
    collect_active_deep_research_opportunities,
    get_actionable_alerts_stream,
)
from src.ui.services.feedback_service import get_feedback_loop_data

pulse = get_live_market_pulse()
print("Market phase:", pulse.get("market_phase"), "SPY:", pulse.get("spy"))

opps = collect_active_deep_research_opportunities(lookback_days=14)
print("Opps count:", len(opps))
if opps:
    print("Top opp:", opps[0]["ticker"], "Spot:", opps[0].get("spot_price"), "Dist:", opps[0].get("dist_pct"), "State:", opps[0].get("state_label"), "RR:", opps[0].get("live_rr"))

alerts = get_actionable_alerts_stream(limit=20)
print("Actionable alerts count:", len(alerts))
if alerts:
    print("Top alert:", alerts[0]["symbol"], "Action:", alerts[0]["action"], "State:", alerts[0].get("state_label"))

feedback = get_feedback_loop_data()
print("Open positions:", len(feedback.get("open_positions", [])))
print("Closed positions:", len(feedback.get("closed_positions", [])))
print("Scorecard:", feedback.get("scorecard"))
