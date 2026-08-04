"""
src/logic/data_window_filter.py

Revanth Data Window Pre-Filter — an ERA-ROBUST, EXCLUSION-FIRST triage engine.

Input : one TradingView Data Window scrape per ticker (dict keyed by indicator label).
Output: a triage verdict (PASS / WATCH / CUT) plus both long & short trade plans.

HONESTY & VALIDATION CONSTRAINTS:
1. Mean edge is tail-driven: medians barely move across all rules (baseline -0.14%,
   best rule -0.07%, REVERSAL BUY +0.08% 21d excess returns).
2. Population limitation: validation numbers were measured over all 1.89M bars,
   NOT the screener-conditioned population the filter actually receives. On the
   Long Ignition proxy population, no rule is significantly positive.
3. Sole PASS Lane: only action code 20 (REVERSAL BUY) showed era-robust positive
   edge in both 2006-2015 (+0.61%) and 2016-2026 (+0.72%) eras. All other non-excluded
   setups clear to WATCH, ordered deterministically by an unvalidated tiebreak score.
"""

import json
import logging
import os
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Every Unicode dash / minus variant TradingView (or a copy-paste) may emit.
_DASH_CODEPOINTS = {
    0x2212,
    0x2010,
    0x2011,
    0x2012,
    0x2013,
    0x2014,
    0x2015,
    0x2043,
    0xFE58,
    0xFE63,
    0xFF0D,
}


def normalize_number_str(val) -> str:
    """NFKC folds exotic spaces/compatibility forms; any Unicode dash/minus -> '-'."""
    s = unicodedata.normalize("NFKC", str(val))
    return "".join("-" if ord(ch) in _DASH_CODEPOINTS else ch for ch in s)


# ---------------------------------------------------------------------------
# 0. Measured Era-Robust Constants
# ---------------------------------------------------------------------------
# Provenance: 2016-2026 bar universe cut points.
EXT_MAX = 25.0       # Ext Pct vs MA200 >= 25% (era-robust hard exclusion: -1.78% / -1.00% 21d excess)
P_RICH = 65.0        # Price 2/3 quantile (2016-2026 bars; post-inflation/drift price threshold)
HV_HIGH = 45.0       # HV20 80th percentile (ann %; high-volatility threshold)


# ---------------------------------------------------------------------------
# 1. Data Window label mapping
# ---------------------------------------------------------------------------
# Keyed by TradingView indicator label strings. Substring match tolerates minor label drift.
_FIELD_LABELS = {
    "price": ("close",),
    "ma20": ("ma 20",),
    "ma50": ("ma 50",),
    "ma200": ("ma 200",),
    "weinstein": ("weinstein",),
    "buy": ("buy score",),
    "sell": ("sell score",),
    "stage": ("stage (1=", "stage 1 base", "stage 1"),
    "stage_age_bars": ("stage age bars", "stage age",),
    "long_zbot": ("long entry zone bot",),
    "long_ztop": ("long entry zone top",),
    "long_stop_loss": ("long stop loss",),
    "long_target": ("long target",),
    "long_target_t1": ("long target t1 waypoint", "long target t1",),
    "long_entry": ("long entry",),
    "long_in_zone": ("long in zone",),
    "long_rr_valid": ("long rr valid", "long r:r valid",),
    "short_zbot": ("short entry zone bot",),
    "short_ztop": ("short entry zone top",),
    "short_stop_loss": ("short stop loss",),
    "short_target": ("short target",),
    "short_target_t1": ("short target t1 waypoint", "short target t1",),
    "short_entry": ("short entry",),
    "short_in_zone": ("short in zone",),
    "short_rr_valid": ("short rr valid", "short r:r valid",),
    "entry_at_market": ("entry at market",),
    "rev_l": ("long rev zone",),
    "rev_s": ("short rev zone",),
    "ext_pct": ("ext%", "ext pct"),
    "ext_z_self": ("ext z self relative", "ext z self",),
    "exhaustion": ("exhaustion gradient",),
    "regime": ("regime (", "regime 0 hlt"),
    "dir_prob": ("dir prob",),
    "ignition_long": ("long ignition",),
    "bear_mask": ("bear warning mask",),
    "rev_mask": ("reversal pattern mask",),
    "weak_mask": ("weak level mask",),
    "bear_age": ("bear warning age",),
    "rev_age": ("reversal pattern age",),
    "weak_age": ("weak level age",),
    "action_long": ("action long code",),
    "action_short": ("action short code",),
    "mtf_long": ("mtf long aligned",),
    "energy_state": ("energy state",),
    "energy_ivrank": ("energy iv rank",),
    "energy_iv30": ("energy iv30 (ann %)", "energy iv30 ann %",),
    "iv_hv_spread": ("energy iv-hv spread", "energy iv-hv spread (ivs)", "energy iv hv spread"),
    "hv20": ("hv20 (ann %)", "hv20 ann %",),
    "adx": ("adx (14",),
    "di_plus": ("dmi +di", "dmi di plus"),
    "di_minus": ("dmi -di", "dmi di minus"),
    "rr_to_target": ("r:r to target",),
    "vp_poc": ("vp poc", "poc",),
    "vp_vah": ("vp vah", "vah",),
    "vp_val": ("vp val", "val",),
    "vp_hvn_above": ("vp hvn above",),
    "vp_hvn_below": ("vp hvn below",),
    "rvol": ("rvol (vs avg)",),
    "sprint_ema": ("sprint line ema",),
    "hull_baseline": ("hull baseline (hma 20)", "hull baseline hma 20", "hull baseline hma", "hull baseline hwa 20",),
    "golden_cross": ("golden cross",),
    "death_cross": ("death cross",),
    "zone0_long": ("zone 0 long",),
    "zone0_short": ("zone 0 short",),
    "avwap_resistance": ("avwap resistance",),
    "avwap_support": ("avwap support",),
    "exp_move_pct": ("exp move pct",),
    "z_velocity": ("z velocity",),
    "z_elasticity": ("z elasticity",),
    "trend_bars_up": ("trend bars up",),
    "buy_sigma_evidence": ("buy sigma evidence",),
    "sell_sigma_evidence": ("sell sigma evidence",),
}

_BEAR_MASK_BITS = {
    1: "TOP",
    2: "RSI_CASCADE",
    4: "INTERNAL_WEAKNESS",
    8: "EXTREME_EXTENSION",
    16: "BEAR_WEAKNESS",
}

_REV_MASK_BITS = {
    1: "KEY_REV_BULL",
    2: "KEY_REV_BEAR",
    4: "SWEEP_BULL",
    8: "SWEEP_BEAR",
    16: "FAILSWEEP_BULL",
    32: "FAILSWEEP_BEAR",
    64: "TRAP_BULL",
    128: "TRAP_BEAR",
    256: "HIKKAKE_BULL",
    512: "HIKKAKE_BEAR",
    1024: "OOPS_BULL",
    2048: "OOPS_BEAR",
}

_WEAK_MASK_BITS = {1: "RESISTANCE_WEAKENED", 2: "SUPPORT_WEAKENED"}

# ACTION-state code enum (Row 8 Supreme cell)
_ACTION_CODES = {
    0: "NONE",
    1: "PRIME",
    2: "ACTION",
    3: "POWER MOVE",
    4: "POWER (EXT)",
    5: "LOW R:R",
    6: "ACCELERATION/BREAKDOWN",
    7: "EARLY",
    8: "WATCH",
    9: "FORMING",
    10: "WAIT",
    11: "EXTENDED",
    12: "STRETCHED",
    13: "VOLATILE",
    14: "COUNTER-TREND",
    15: "TOP/BOT WARNING",
    16: "BLOW-OFF/CAPITULATION",
    17: "PARABOLIC",
    18: "TOXIC RISK",
    19: "SCREEN BLOCK",
    20: "REVERSAL BUY",
    21: "CHASE",
}

_ACTION_ACTIONABLE_CODES = {1, 2, 3, 4}  # Codes 1-4 = CONFIRMED actionable entries (dropped 5)
_ACTION_HARD_CUT_CODES = {17, 18}         # Parabolic / Toxic risk hard cuts
_ACTION_SOFT_CAUTION_CODES = {11, 12, 13, 16, 19}  # Caution/demotion states


def decode_action(val: Optional[float]) -> Optional[str]:
    """Map an ACTION-state code number to its named state (None if absent)."""
    if val is None:
        return None
    return _ACTION_CODES.get(int(round(val)), "NONE")


def action_is_actionable(val: Optional[float]) -> bool:
    """True only for a CONFIRMED, triggered entry (codes 1-4)."""
    return val is not None and int(round(val)) in _ACTION_ACTIONABLE_CODES


_CORE_FIELDS = [
    "price",
    "ma20",
    "ma50",
    "ma200",
    "weinstein",
    "buy",
    "sell",
    "stage",
    "dir_prob",
    "regime",
    "ext_pct",
    "exhaustion",
    "rev_l",
    "rev_s",
]

_EMPTY_TOKENS = {"", "∅", "⌀", "none", "n/a", "na", "-", "—", "null", "nan"}


def _alnum(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9+-]", "", str(s).lower())


def _match_label(raw: dict, *needles) -> Optional[str]:
    for key in raw:
        cleaned = _alnum(key)
        if any(_alnum(n) in cleaned for n in needles):
            return key
    return None


def _num(val) -> Optional[float]:
    """Parse a Data Window cell to float, or None for empty/missing tokens."""
    if val is None:
        return None
    s = normalize_number_str(val).strip()
    if s.lower() in _EMPTY_TOKENS:
        return None
    s = s.replace("%", "").replace(",", "").replace(" ", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


_MASK_CLASS_FIELDS = frozenset({"bear_mask", "rev_mask", "weak_mask", "bear_age", "rev_age", "weak_age"})

_MAX_MASK = {
    "bear_mask": 31,
    "rev_mask": 4095,
    "weak_mask": 3,
    "bear_age": 9999,
    "rev_age": 9999,
    "weak_age": 9999,
}


def _num_mask(val, max_val: Optional[int] = None) -> Optional[float]:
    if val is None:
        return None
    s = normalize_number_str(val).strip()
    if s.lower() in _EMPTY_TOKENS:
        return None
    s = s.replace("%", "").replace(",", "").replace(" ", "")
    if not s:
        return None

    if "." in s:
        head, _, tail = s.rpartition(".")
        if all(ch == "0" for ch in tail):
            s = head

    s = s.replace(".", ",")
    candidates = [s.replace(",", "")]

    if max_val is not None:
        try:
            f = float(candidates[0])
        except ValueError:
            pass
        else:
            iv = int(f)
            if iv > max_val and iv > 0:
                stripped = candidates[0].rstrip("0")
                if stripped and stripped != candidates[0]:
                    candidates.append(stripped)

    for candidate in candidates:
        try:
            f = float(candidate)
        except ValueError:
            continue
        if f < 0 or not f.is_integer():
            continue
        if max_val is not None and int(f) > max_val:
            continue
        return f
    return None


def parse_data_window(raw: dict) -> Dict[str, Optional[float]]:
    """Key the Data Window by label and return a normalized field dict.

    Missing / empty cells become None (never 0). Unknown labels are ignored.
    """
    f = {}
    for field, needles in _FIELD_LABELS.items():
        if field in raw:
            f[field] = _num_mask(raw[field], _MAX_MASK.get(field)) if field in _MASK_CLASS_FIELDS else _num(raw[field])
            continue
        key = _match_label(raw, *needles)
        if key is None:
            f[field] = None
        else:
            f[field] = _num_mask(raw[key], _MAX_MASK.get(field)) if field in _MASK_CLASS_FIELDS else _num(raw[key])
    return f


def _decode_mask(val: Optional[float], bits: Dict[int, str]) -> List[str]:
    if val is None:
        return []
    m = int(round(val))
    return [name for bit, name in sorted(bits.items()) if m & bit]


def decode_recency(f: Dict[str, Optional[float]]) -> Dict[str, Any]:
    return {
        "warnings_fresh": _decode_mask(f.get("bear_mask"), _BEAR_MASK_BITS),
        "warnings_age": f.get("bear_age"),
        "reversals_fresh": _decode_mask(f.get("rev_mask"), _REV_MASK_BITS),
        "reversals_age": f.get("rev_age"),
        "weak_levels": _decode_mask(f.get("weak_mask"), _WEAK_MASK_BITS),
        "weak_levels_age": f.get("weak_age"),
    }


# ---------------------------------------------------------------------------
# 2. Assess one side
# ---------------------------------------------------------------------------
def _assess_side(side: str, f: Dict[str, Optional[float]]) -> Dict[str, Any]:
    price = f["price"]
    act_code_val = f.get("action_long") if side == "long" else f.get("action_short")
    act_code = int(round(act_code_val)) if act_code_val is not None else 0

    if side == "long":
        score = f["buy"]
        rev = f["rev_l"]
        ign = f["ignition_long"] or 0.0
        zbot, ztop = f["long_zbot"], f["long_ztop"]
        tgt = f["long_target"]
        stop = f["long_stop_loss"]
        in_zone_exported = f.get("long_in_zone")
        ma_ok = f["ma50"] is not None and f["ma200"] is not None
        stack_ok = bool(ma_ok and price > f["ma50"] > f["ma200"] and price > f["weinstein"])
        dir_ok = (f["dir_prob"] or 0.0) > 55
        rev_ok = (rev or 0.0) >= 10 and int(round(f["stage"] or 0)) in (3, 4)
        ext_hostile = (f["ext_pct"] or 0.0) > 60
        in_zone = bool(in_zone_exported == 1) if in_zone_exported is not None else bool(zbot is not None and ztop is not None and zbot <= price <= ztop)
        missed = bool(zbot is not None and price > ztop)
        chased = bool(act_code == 21 or (missed and f["ma20"] is not None and price > f["ma20"]))
    else:  # short
        score = f["sell"]
        rev = f["rev_s"]
        ign = 0.0
        zbot, ztop = f["short_zbot"], f["short_ztop"]
        tgt = f["short_target"]
        stop = f["short_stop_loss"]
        in_zone_exported = f.get("short_in_zone")
        ma_ok = f["ma50"] is not None and f["ma200"] is not None
        stack_ok = bool(ma_ok and price < f["ma50"] < f["ma200"] and price < f["weinstein"])
        dir_ok = (f["dir_prob"] or 0.0) < 45
        rev_ok = (rev or 0.0) >= 10 and int(round(f["stage"] or 0)) in (1, 2)
        ext_hostile = (f["ext_pct"] or 0.0) < -60
        in_zone = bool(in_zone_exported == 1) if in_zone_exported is not None else bool(zbot is not None and ztop is not None and zbot <= price <= ztop)
        missed = bool(ztop is not None and price < zbot)
        chased = bool(act_code == 21 or (missed and f["ma20"] is not None and price < f["ma20"]))

    # Risk / reward calculation (reading actual exported stop)
    risk = reward = None
    if stop is not None and tgt is not None and price is not None:
        if side == "long":
            risk = price - stop
            reward = tgt - price
        else:
            risk = stop - price
            reward = price - tgt
    rr = (reward / risk) if (risk is not None and risk > 0) else None

    # Dominant side attribution
    ev_side = "long" if (f["buy"] or 0.0) >= (f["sell"] or 0.0) else "short"
    if side == ev_side:
        if rr is None:
            rr = f.get("rr_to_target")
        dir_p = f.get("dir_prob")
        if dir_p is not None:
            win_prob = dir_p if side == "long" else (100.0 - dir_p)
            if rr is not None and rr > 0:
                ev_r = ((win_prob / 100.0) * rr) - (1.0 - (win_prob / 100.0))
            else:
                ev_r = None
        else:
            win_prob = None
            ev_r = None
    else:
        win_prob = None
        ev_r = None

    # Mode selection
    if rev_ok or act_code == 20:
        mode = "REVERSION_" + side.upper()
    elif ign == 1:
        mode = "BREAKOUT_LONG"
    elif dir_ok and stack_ok and (score or 0.0) >= 65:
        mode = "TREND_" + side.upper()
    else:
        mode = "NONE"

    return {
        "side": side,
        "mode": mode,
        "triage": None,
        "reason": "no_setup" if mode == "NONE" else "setup",
        "score": score,
        "rev": rev,
        "rr": rr,
        "ev_r": ev_r,
        "win_prob": win_prob,
        "in_zone": in_zone,
        "missed": missed,
        "chased": chased,
        "flags": [],
        "stack_ok": stack_ok,
        "dir_ok": dir_ok,
        "act_code": act_code,
        "stop": stop,
        "target": tgt,
    }


# ---------------------------------------------------------------------------
# 3. Winner selection + soft flags
# ---------------------------------------------------------------------------
def _choose_winner(L: dict, S: dict, f: Dict[str, Optional[float]]) -> dict:
    if L["mode"] == "NONE" and S["mode"] == "NONE":
        buy = f["buy"] or 0.0
        sell = f["sell"] or 0.0
        W = L if buy >= sell else S
        W["mode"] = "NONE"
    else:
        candidates = [s for s in (L, S) if s["mode"] != "NONE"]
        W = max(candidates, key=lambda s: (s["act_code"] == 20, s["score"] or 0.0))

    cs = W["side"]
    opp = (f["sell"] if cs == "long" else f["buy"]) or 0.0
    ext_pct = f["ext_pct"] or 0.0
    exhaustion = f["exhaustion"] or 0.0
    stage = int(round(f["stage"] or 0))
    regime = int(round(f["regime"] or 0))

    if cs == "long" and ext_pct >= 20 and exhaustion >= 0.3:
        W["flags"].append("exhaustion")
    if cs == "short" and ext_pct <= -20 and exhaustion >= 0.3:
        W["flags"].append("oversold")
    if opp >= 60:
        W["flags"].append("churn")
    if W["chased"]:
        W["flags"].append("chased")
    elif W["missed"]:
        W["flags"].append("pullback")
    if (cs == "long" and stage == 4) or (cs == "short" and stage == 2):
        W["flags"].append("stage_lag")
    if regime == 1:
        W["flags"].append("extended")
    if regime == 6:
        W["flags"].append("squeeze")

    return W


def tiebreak_score(rec: Dict[str, Any], f: Optional[Dict[str, Optional[float]]] = None) -> float:
    """Unvalidated tiebreak heuristic for deterministic WATCH candidate sorting.

    NOTE: Era-dependent (-0.02 pre-2016 vs +0.59 post-2016). Exists solely to make
    DEEP_RESEARCH_CAP selection deterministic, NOT as a proven predictor of alpha.
    """
    d = f if f is not None else rec
    price = d.get("price") or 0.0
    hv20 = d.get("hv20") or 0.0
    ext_pct = d.get("ext_pct") or 0.0
    ext_z = d.get("ext_z_self") or 0.0

    score = 0.0
    if price < P_RICH:
        score += 2.0
    if hv20 < HV_HIGH:
        score += 2.0
    score -= (ext_pct / 10.0)
    score -= ext_z
    return score


def _log_data_window_scrape(ticker: str, raw: dict, verdict: dict) -> None:
    try:
        log_dir = os.path.join("data", "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "data_window_scrapes.jsonl")
        from datetime import timezone
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ticker": ticker,
            "raw": raw,
            "verdict": {
                "triage": verdict.get("triage"),
                "mode": verdict.get("mode"),
                "reason": verdict.get("reason"),
                "chosen_side": verdict.get("chosen_side"),
                "action": verdict.get("action"),
            },
        }
        with open(log_file, "a", encoding="utf-8") as f_out:
            f_out.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.debug(f"Failed to log data window scrape: {e}")


# ---------------------------------------------------------------------------
# 4. Orchestrator
# ---------------------------------------------------------------------------
def run_data_window_filter(
    ticker: str, raw: dict, realvol_10d: Optional[float] = None, ret_10d: Optional[float] = None
) -> Dict[str, Any]:
    """Run the era-robust pre-filter for one ticker. Returns STEP 5 output dict."""
    f = parse_data_window(raw)

    # STEP 0 — validate core fields
    if any(f.get(field) is None for field in _CORE_FIELDS):
        logger.info(f"[{ticker}] Data Window pre-filter: CUT (bad_data) — missing core field")
        verdict = {
            "ticker": ticker,
            "chosen_side": None,
            "mode": "NONE",
            "triage": "CUT",
            "reason": "bad_data",
            "conviction": None,
            "rev": None,
            "rr": None,
            "ev_r": None,
            "win_prob": None,
            "in_zone": None,
            "missed": None,
            "dir_prob": f.get("dir_prob"),
            "regime": f.get("regime"),
            "flags": [],
            "long_plan": _plan(f, "long"),
            "short_plan": _plan(f, "short"),
            "recency": decode_recency(f),
            "action_long": decode_action(f.get("action_long")),
            "action_short": decode_action(f.get("action_short")),
            "action": None,
            "action_actionable": False,
            "mtf_long": f.get("mtf_long"),
            "mtf_short": None,
            "realvol_10d": realvol_10d,
            "ret_10d": ret_10d,
            "bad_data": True,
        }
        _log_data_window_scrape(ticker, raw, verdict)
        return verdict

    L = _assess_side("long", f)
    S = _assess_side("short", f)
    W = _choose_winner(L, S, f)

    price = f["price"]
    ext_pct = f["ext_pct"] or 0.0
    hv20 = f["hv20"] or 0.0
    stage = int(round(f["stage"] or 0))
    act_code = W["act_code"]

    # HARD EXCLUSIONS (CUT)
    if ext_pct >= EXT_MAX:
        triage, reason = "CUT", "extreme_extension"
    elif price >= P_RICH and hv20 >= HV_HIGH:
        triage, reason = "CUT", "rich_high_volatility"
    elif realvol_10d is not None and realvol_10d >= 42.8:
        triage, reason = "CUT", "high_10d_volatility"
    elif ret_10d is not None and ret_10d >= 12.9:
        triage, reason = "CUT", "high_10d_return"
    elif act_code in _ACTION_HARD_CUT_CODES:
        triage, reason = "CUT", "parabolic_or_toxic"
    elif stage == 0:
        triage, reason = "CUT", "warmup_stage_0"
    elif W["target"] is None and W["chased"]:
        triage, reason = "CUT", "chasing_without_target"
    # SINGLE PASS LANE: action code 20 (REVERSAL BUY)
    elif act_code == 20:
        triage = "PASS"
        reason = "reversal_buy_lane"
    else:
        # All other non-excluded setups clear to WATCH
        triage = "WATCH"
        reason = W["reason"] if W["reason"] != "setup" else "constructible_watch"

    # Soft demotions / caution flags
    flags = list(W["flags"])
    if act_code in _ACTION_SOFT_CAUTION_CODES:
        flags.append("soft_caution_action")
    if (f.get("ext_z_self") or 0.0) >= 1.5:
        flags.append("ext_z_self_elevated")

    conviction = W["score"]
    if triage == "PASS":
        conviction_str = "HIGH" if (W["rev"] or 0.0) >= 10 else "MED"
    else:
        conviction_str = "HIGH" if (conviction or 0.0) >= 75 else ("MED" if (conviction or 0.0) >= 50 else "LOW")

    verdict = {
        "ticker": ticker,
        "chosen_side": W["side"],
        "mode": W["mode"],
        "triage": triage,
        "reason": reason,
        "conviction": conviction,
        "conviction_str": conviction_str,
        "rev": W["rev"],
        "rr": W["rr"],
        "ev_r": W["ev_r"],
        "win_prob": W["win_prob"],
        "in_zone": W["in_zone"],
        "missed": W["missed"],
        "dir_prob": f["dir_prob"],
        "regime": f["regime"],
        "flags": flags,
        "long_plan": _plan(f, "long"),
        "short_plan": _plan(f, "short"),
        "recency": decode_recency(f),
        "action_long": decode_action(f.get("action_long")),
        "action_short": decode_action(f.get("action_short")),
        "action": decode_action(f.get("action_long") if W["side"] == "long" else f.get("action_short")),
        "action_actionable": action_is_actionable(f.get("action_long") if W["side"] == "long" else f.get("action_short")),
        "mtf_long": f.get("mtf_long"),
        "mtf_short": None,
        "realvol_10d": realvol_10d,
        "ret_10d": ret_10d,
        "tiebreak": tiebreak_score(W, f),
        "bad_data": False,
    }

    _log_data_window_scrape(ticker, raw, verdict)
    return verdict


def _plan(f: Dict[str, Optional[float]], side: str) -> Dict[str, Optional[float]]:
    if side == "long":
        return {
            "zone": [f["long_zbot"], f["long_ztop"]],
            "target": f["long_target"],
        }
    return {
        "zone": [f["short_zbot"], f["short_ztop"]],
        "target": f["short_target"],
    }


# ---------------------------------------------------------------------------
# 5. RANK — sort candidates for deep research selection
# ---------------------------------------------------------------------------
def deep_research_sort_key(rec: Dict[str, Any]) -> Tuple[int, int, int, float, float, float]:
    """THE single ranking key for deep-research selection.

    Priority order:
    1. PASS verdict (REVERSAL BUY lane) outranks WATCH/CUT.
    2. REVERSAL BUY action code (action == "REVERSAL BUY").
    3. Actionable now (`in_zone == True`).
    4. Deterministic tiebreak score (unvalidated heuristic).
    5. Directional edge |dir_prob - 50|.
    6. Conviction score.
    """
    if not rec:
        return (0, 0, 0, 0.0, 0.0, 0.0)

    # Unpack nested triage dict if outer record passed
    if isinstance(rec.get("triage"), dict):
        rec = rec["triage"]

    is_pass = 1 if rec.get("triage") == "PASS" else 0
    is_rev_buy = 1 if rec.get("action") == "REVERSAL BUY" else 0
    in_zone = 1 if bool(rec.get("in_zone")) else 0
    tb = float(rec.get("tiebreak") or 0.0)
    dir_edge = abs((rec.get("dir_prob") or 50) - 50)
    conviction = float(rec.get("conviction") or 0.0)

    # Adjust tiebreak if news contradiction/negative present
    if rec.get("news_contradiction"):
        tb -= 10.0
    elif rec.get("news_negative"):
        tb -= 2.0

    return (is_pass, is_rev_buy, in_zone, tb, dir_edge, conviction)


def rank_pass_tickers(pass_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort candidates via `deep_research_sort_key`."""
    return sorted(pass_records, key=deep_research_sort_key, reverse=True)


# ---------------------------------------------------------------------------
# 6. Alpaca news + sentiment gate
# ---------------------------------------------------------------------------
def fetch_alpaca_news(ticker: str) -> List[str]:
    items = []
    try:
        from datetime import datetime, timedelta
        import requests

        alpaca_key = os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_KEY_ID")
        alpaca_secret = os.getenv("ALPACA_SECRET_KEY")

        if alpaca_key and alpaca_secret:
            base_url = os.getenv("ALPACA_API_URL", "https://data.alpaca.markets")
            base_url = base_url.replace("paper-api.alpaca.markets", "data.alpaca.markets").replace(
                "api.alpaca.markets", "data.alpaca.markets"
            ).split("/v2")[0]

            headers = {"APCA-API-KEY-ID": alpaca_key, "APCA-API-SECRET-KEY": alpaca_secret}
            end_date = datetime.now()
            start_date = end_date - timedelta(days=3)
            params = {
                "symbols": ticker,
                "start": start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": 10,
            }
            resp = requests.get(f"{base_url}/v1beta1/news", headers=headers, params=params, timeout=5)
            if resp.status_code == 200:
                items = [art.get("headline", "") for art in resp.json().get("news", []) if art.get("headline")]
                if items:
                    return items

        try:
            from src.clients.news_client import _fetch_finnhub_news
            finnhub_ctx = _fetch_finnhub_news(ticker, days=3)
            if finnhub_ctx:
                items = [block.split("\nSummary:")[0] for block in finnhub_ctx.split("\n\n") if block.strip()]
                if items:
                    return items
        except Exception:
            pass

        import yfinance as yf
        yf_news = yf.Ticker(ticker).news
        if yf_news:
            items = [art.get("title", "") for art in yf_news if art.get("title")]

    except Exception as e:
        logger.warning(f"[{ticker}] News fetch failed: {e}")

    return items


def _parse_sentiment(raw: str, ticker: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    if "</think>" in raw:
        raw = raw.split("</think>")[-1].strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except Exception:
        return None
    label = str(data.get("sentiment", "neutral")).lower()
    if label not in ("positive", "neutral", "negative"):
        label = "neutral"
    return {"label": label, "summary": str(data.get("summary", "")).strip()}


def classify_sentiment(ticker: str, headlines: List[str]) -> Dict[str, Any]:
    if not headlines:
        return {"label": "neutral", "summary": "no recent news"}
    blob = "\n".join(f"- {h}" for h in headlines[:10])
    system_prompt = (
        "You are a concise equity-news sentiment classifier. Given recent "
        "headlines for a stock, output a single sentiment label and a one-line "
        "summary. Respond with JSON only: "
        '{"sentiment": "positive"|"neutral"|"negative", "summary": "<one line>"}'
    )
    user_prompt = f"TICKER: {ticker}\nRECENT HEADLINES:\n{blob}"

    from src.clients.llm_client import query_local_llm

    try:
        raw = query_local_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            json_mode=True,
            max_tokens=150,
            use_tools=False,
            disable_thinking=True,
            json_schema={
                "type": "object",
                "properties": {
                    "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
                    "summary": {"type": "string"},
                },
                "required": ["sentiment", "summary"],
            },
        )
        res = _parse_sentiment(raw, ticker)
        if res:
            return res
    except Exception as e:
        logger.warning(f"[{ticker}] local sentiment classification failed: {e}")
    return {"label": "neutral", "summary": "classification unavailable"}


def triage_ticker(
    ticker: str,
    data_window: dict,
    fetch_news: bool = True,
    realvol_10d: Optional[float] = None,
    ret_10d: Optional[float] = None,
) -> Dict[str, Any]:
    verdict = run_data_window_filter(ticker, data_window, realvol_10d=realvol_10d, ret_10d=ret_10d)
    sentiment: Dict[str, Any] = {"label": "neutral", "summary": "", "headlines": []}
    if fetch_news:
        headlines = fetch_alpaca_news(ticker)
        sentiment["headlines"] = headlines
        if headlines:
            sentiment.update(classify_sentiment(ticker, headlines))
    verdict["sentiment"] = sentiment

    technical_pass = verdict["triage"] == "PASS"
    sentiment_negative = sentiment.get("label") == "negative"
    verdict["news_negative"] = sentiment_negative
    pursue = technical_pass
    if technical_pass and sentiment_negative:
        pursue_reason = "data_window_pass_with_negative_news"
    elif technical_pass:
        pursue_reason = "pass"
    else:
        pursue_reason = verdict["reason"] or "no_setup"

    verdict["pursue"] = pursue
    verdict["pursue_reason"] = pursue_reason
    return verdict


# ---------------------------------------------------------------------------
# Self-test (spec test cases)
# ---------------------------------------------------------------------------
def _self_test() -> None:
    cases = {
        # Case 1: REVERSAL BUY lane (action_long=20, rev_l=10) -> PASS
        "REV_BUY": dict(
            price=150.0,
            ma20=145.0,
            ma50=140.0,
            ma200=130.0,
            weinstein=135.0,
            buy=85.0,
            sell=20.0,
            stage=3,
            dir_prob=75.0,
            regime=0,
            ext_pct=5.0,
            exhaustion=0.1,
            rev_l=10.0,
            rev_s=0.0,
            action_long=20.0,
            long_zbot=148.0,
            long_ztop=152.0,
            long_stop_loss=140.0,
            long_target=170.0,
            long_in_zone=1.0,
        ),
        # Case 2: Hard exclusion CUT (ext_pct >= 25) -> CUT extreme_extension
        "EXT_CUT": dict(
            price=200.0,
            ma20=180.0,
            ma50=160.0,
            ma200=150.0,
            weinstein=155.0,
            buy=90.0,
            sell=10.0,
            stage=2,
            dir_prob=80.0,
            regime=0,
            ext_pct=30.0,  # >= 25.0 cut
            exhaustion=0.2,
            rev_l=0.0,
            rev_s=0.0,
            action_long=2.0,
            long_zbot=195.0,
            long_ztop=205.0,
            long_stop_loss=185.0,
            long_target=230.0,
        ),
        # Case 3: Zoneless RR-Valid trap bar (rr_valid=1 but long_in_zone=0, no zone) -> WATCH
        "RR_TRAP": dict(
            price=100.0,
            ma20=98.0,
            ma50=95.0,
            ma200=90.0,
            weinstein=92.0,
            buy=70.0,
            sell=40.0,
            stage=2,
            dir_prob=60.0,
            regime=0,
            ext_pct=4.0,
            exhaustion=0.0,
            rev_l=0.0,
            rev_s=0.0,
            action_long=2.0,
            long_rr_valid=1.0,
            long_in_zone=0.0,
            long_zbot=None,
            long_ztop=None,
            long_stop_loss=90.0,
            long_target=120.0,
        ),
        # Case 4: Stage 0 warm-up bar -> CUT warmup_stage_0
        "STAGE0": dict(
            price=50.0,
            ma20=48.0,
            ma50=45.0,
            ma200=40.0,
            weinstein=42.0,
            buy=70.0,
            sell=30.0,
            stage=0,  # Warm-up bar
            dir_prob=65.0,
            regime=0,
            ext_pct=2.0,
            exhaustion=0.1,
            rev_l=0.0,
            rev_s=0.0,
            action_long=1.0,
            long_zbot=49.0,
            long_ztop=51.0,
            long_stop_loss=45.0,
            long_target=60.0,
        ),
    }

    expected = {
        "REV_BUY": ("long", "REVERSION_LONG", "PASS", "reversal_buy_lane"),
        "EXT_CUT": ("long", "TREND_LONG", "CUT", "extreme_extension"),
        "RR_TRAP": ("long", "TREND_LONG", "WATCH", "constructible_watch"),
        "STAGE0": ("long", "TREND_LONG", "CUT", "warmup_stage_0"),
    }

    ok = True
    for tk, fields in cases.items():
        out = run_data_window_filter(tk, fields)
        exp_side, exp_mode, exp_triage, exp_reason = expected[tk]
        checks = [
            ("chosen_side", out["chosen_side"], exp_side),
            ("mode", out["mode"], exp_mode),
            ("triage", out["triage"], exp_triage),
            ("reason", out["reason"], exp_reason),
        ]
        for name, got, exp in checks:
            if got != exp:
                ok = False
                logger.error(f"SELF-TEST {tk} {name}: got {got!r}, expected {exp!r}")
        logger.info(
            f"SELF-TEST {tk}: side={out['chosen_side']} mode={out['mode']} "
            f"triage={out['triage']} reason={out['reason']} rr={out['rr']}"
        )
    logger.info("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    _self_test()
