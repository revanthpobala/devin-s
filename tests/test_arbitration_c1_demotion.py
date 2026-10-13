"""
tests/test_arbitration_c1_demotion.py

C2 from plan/no-lost-movers.md: arbitration applies C1 to the parsed judge output
before anything is persisted, and the judge prompt itself states that targets must
sit above the trigger.

The judge response below is the real stored META plan (2026-10-09): entry_type
LIMIT carrying breakout_level 747.60 with target_1 742.09 and target_2 747.60 —
both targets at or below the trigger. The demoted plan must persist WITHOUT the
broken trigger on every surface that stores or displays it.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

META_JUDGE_RESPONSE = """# META | SENIOR PM ARBITRATION

```json:watch_levels
{
  "ticker": "META",
  "verdict": "STALK",
  "conviction": 6,
  "side": "LONG",
  "shares_plan": {
    "side": "LONG",
    "entry_type": "LIMIT",
    "entry_zone_low": 710.43,
    "entry_zone_high": 715.95,
    "breakout_level": 747.6,
    "breakout_stop": 735.0,
    "tactical_stop": 698.5,
    "target_1": 742.09,
    "target_2": 747.6
  },
  "options_plan": {"actionable": false, "structure": "NONE"},
  "invalidation": {"condition": "DAILY_CLOSE_BELOW", "price_level": 698.5},
  "setup_lane": "JUDGE"
}
```

## THE JUDGE'S FINAL RULING
Stalk the zone.
"""


def _run_meta_arbitration(tmp_path: Path) -> dict:
    from src.logic.deep_research.arbitration import run_arbitration

    tdir = tmp_path / "triage"
    raw_dir = tmp_path / "raw" / "2026-10-09"  # the date dir; run_arbitration appends <TICKER>/ itself
    reports_dir = tmp_path / "reports" / "2026-10-09"
    for d in (tdir, raw_dir, reports_dir):
        d.mkdir(parents=True, exist_ok=True)

    dw = {"Close": 718.67, "rsi2_atr14": 12.0, "Long Stop Loss": 698.5, "Long Target": 742.09}

    with patch("src.logic.deep_research.arbitration.query_local_llm", return_value=META_JUDGE_RESPONSE), \
         patch("src.clients.earnings_client.get_next_earnings_days", return_value=None), \
         patch("src.tracking.suggested_trades_auditor.sync_suggested_trades_from_watch_targets"):
        run_arbitration(
            ticker="META",
            date_str="2026-10-09",
            clean_response="Model A report body",
            clean_ind_response="Model B independent report body " * 5,
            f_parsed={},
            dw_dict=dw,
            live_quote_block="META 718.67",
            earnings_fact_block="2026-10-22",
            alerts_list=[],
            macro_news="none",
            macro_grounded_block="none",
            active_pos_block="none",
            tdir=tdir,
            raw_dir=raw_dir,
            reports_dir=reports_dir,
            kind="NEW",
            setup_lane="JUDGE",
        )

    watch_files = [
        reports_dir / "META_watch_levels.json",
        raw_dir / "META" / "META_watch_levels.json",
    ]
    persisted = None
    for wf in watch_files:
        assert wf.exists(), f"{wf} not written"
        data = json.loads(wf.read_text(encoding="utf-8"))
        sp = data.get("shares_plan", {})
        # A broken trigger is never zeroed: it stays armed with targets rebuilt above it.
        assert sp.get("breakout_level", 0) == 747.6, f"{wf} lost the trigger"
        assert sp.get("targets_rebuilt") is True
        assert sp.get("target_1", 0) > 747.6
        # The limit-zone path is untouched.
        assert sp.get("entry_zone_low") == 710.43
        assert sp.get("entry_zone_high") == 715.95
        persisted = data
    return persisted


def test_arbitration_persisted_mega_plan_is_not_gate_rejected(tmp_path):
    persisted = _run_meta_arbitration(tmp_path)
    # META still fails its own gate — the rebuilt targets do not rescue the zone R:R.
    assert persisted["level_gate_rejected"] is True


def test_judge_prompt_states_targets_above_trigger():
    from src.logic.deep_research.arbitration import _build_judge_sys_prompt

    prompt = _build_judge_sys_prompt("META")
    assert "ABOVE breakout_level" in prompt
    # The prompt states the keep-and-rebuild rule, never the old strip behavior:
    # a broken trigger stays armed and only its targets move above it.
    assert "never zeroed" in prompt
    assert "stripped" not in prompt
