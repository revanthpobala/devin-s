"""
src/logic/data_window_filter.py

Revanth Data Window Pre-Filter — a DETERMINISTIC, field-only triage.

Input : one TradingView Data Window scrape per ticker (dict keyed by the
        indicator's plot LABEL string, e.g. "Dir Prob % (>50 bull)").
Output: a triage verdict (PASS / WATCH / CUT) plus both the long and short
        trade plans, decided purely by comparing the indicator's own fields.
        Nothing external is fetched — no DDGS, no LLM, no weights invented.

Every constant is a cut point the indicator already uses (see the
"GROUNDED THRESHOLDS" table in the spec). The filter is the gate that
replaces the DDGS-driven LLM triage: if the data window says PASS we use
Alpaca news downstream; if CUT we skip news research entirely.

Two sides (long and short) are assessed independently — each has its own
exported zone / stop / target / rev score — and the stronger verdict wins.
Reversion and breakout fire against / ahead of the trend, so they skip the
agreement gate.
"""

import json
import logging
import unicodedata
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
# 1. Data Window label mapping
# ---------------------------------------------------------------------------
# The Data Window JSON is keyed by TradingView's indicator label strings. We
# match by stable case-insensitive substrings so minor label drift does not
# break parsing. The "price" is the last bar's Close (the crosshair sits on the
# latest bar when scraped).
_FIELD_LABELS = {
    "price": ("close",),
    "ma20": ("ma 20",),
    "ma50": ("ma 50",),
    "ma200": ("ma 200",),
    "weinstein": ("weinstein",),
    "buy": ("buy score",),
    "sell": ("sell score",),
    "stage": ("stage (1=", "stage 1 base", "stage 1"),
    "long_zbot": ("long entry zone bot",),
    "long_ztop": ("long entry zone top",),
    "long_stop_loss": ("long stop loss",),
    "long_target": ("long target",),
    "short_zbot": ("short entry zone bot",),
    "short_ztop": ("short entry zone top",),
    "short_stop_loss": ("short stop loss",),
    "short_target": ("short target",),
    "rev_l": ("long rev zone",),
    "rev_s": ("short rev zone",),
    "ext_pct": ("ext%", "ext pct"),
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
    "mtf_short": ("mtf short aligned",),
    "energy_state": ("energy state",),
    "energy_ivrank": ("energy iv rank",),
    "energy_iv30": ("energy iv30 (ann %)", "energy iv30 ann %",),
    "iv_hv_spread": ("energy iv-hv spread", "energy iv-hv spread (ivs)", "energy iv hv spread"),
    "hv20": ("hv20 (ann %)", "hv20 ann %",),
    "adx": ("adx (14",),
    "di_plus": ("dmi +di", "dmi di plus"),
    "di_minus": ("dmi -di", "dmi di minus"),
    "win_prob": ("win prob", "dir prob"),
    "rr_to_target": ("r:r to target",),
    "ev_r": ("expected value", "expected value (r)"),
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
    "total_sigma": ("total sigma",),
}

# Bit legends for the three label-recency mask fields exported by the indicator.
# Each bit means "that label fired within the last 30 bars". The paired *_age
# field gives bars-since the freshest member of the group (None/blank = nothing
# fresh). NOTE: in the bear group, BEAR_WEAKNESS is a BULLISH hidden-accumulation
# signal (bit 16) - it is not a bearish warning despite the group name.
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

# ACTION-state code enum (the Row 8 "Supreme" cell, per side, as a number). Pure
# status enum — decode to the named state here. BUY/SELL, ACCEL/BREAKDOWN, TOP/BOT
# WARNING, BLOW-OFF/CAPITULATION are merged per-pair (the side is the field name).
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
}
# Codes 1-5 = CONFIRMED, actionable entries (PRIME/ACTION/POWER/POWER-EXT/LOW R:R).
# 6-7 = unconfirmed thrust; 8-10 = not triggered; 11-18 = caution/danger.
_ACTION_ACTIONABLE_CODES = {1, 2, 3, 4, 5}


def decode_action(val: Optional[float]) -> Optional[str]:
    """Map an ACTION-state code number to its named state (None if absent)."""
    if val is None:
        return None
    return _ACTION_CODES.get(int(round(val)), "NONE")


def action_is_actionable(val: Optional[float]) -> bool:
    """True only for a CONFIRMED, triggered entry (codes 1-5)."""
    return val is not None and int(round(val)) in _ACTION_ACTIONABLE_CODES


# Fields whose absence invalidates the whole record (STEP 0 core set). The
# opposite side's trade levels may be absent (no setup that side) and are NOT
# part of the core set.
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

# Tokens that mean "no value" (TradingView renders absent zones as the empty-set
# glyph, others as N/A / None). We treat all of these as None, never as 0.
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
    # Strip units that sometimes trail numbers: %, commas, and the C/O/H/L tags.
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
    """Parse mask/age fields tolerating OCR dot-noise as thousands separators.

    Bitmask and age fields have no fractional bits. Any dot is therefore OCR
    noise: either a trailing ".0+" zero-padding (e.g. "8.00" -> "8") or a
    thousands separator inserted by the OCR engine (e.g. "2.410.00" -> 2410).
    Per user convention, the remaining dot is treated as a comma (European
    thousands separator) before stripping. If the reconstructed integer exceeds
    ``max_val``, trailing zeros are stripped as a secondary OCR-noise recovery.
    """
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
    """Decode a recency bitmask float into the names of the fresh labels.

    Empty / missing (None) yields []. Bits are matched via integer AND on the
    rounded value, so 2241.0 -> ["KEY_REV_BULL", "TRAP_BULL", "TRAP_BEAR",
    "OOPS_BEAR"]. Names are returned in ascending bit order.
    """
    if val is None:
        return []
    m = int(round(val))
    return [name for bit, name in sorted(bits.items()) if m & bit]


def decode_recency(f: Dict[str, Optional[float]]) -> Dict[str, Any]:
    """Turn the 3 mask + 3 age fields into named per-type flags and ages.

    Back-compatible: a Data Window scraped before these fields existed yields
    empty lists and None ages. The age is bars-since the freshest member of
    each group (None = nothing fired in the last 30 bars). BEAR_WEAKNESS inside
    warnings_fresh is a BULLISH signal - see _BEAR_MASK_BITS.
    """
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
    if side == "long":
        score = f["buy"]
        rev = f["rev_l"]
        ign = f["ignition_long"] or 0.0
        zbot, ztop = f["long_zbot"], f["long_ztop"]
        tgt = f["long_target"]
        # ma20 is entry TIMING, NOT part of the trend stack (spec STEP 1).
        ma_ok = f["ma50"] is not None and f["ma200"] is not None
        stack_ok = bool(ma_ok and price > f["ma50"] > f["ma200"] and price > f["weinstein"])
        dir_ok = (f["dir_prob"] or 0.0) > 55
        rev_ok = (rev or 0.0) >= 10 and int(round(f["stage"] or 0)) in (3, 4)
        ext_hostile = (f["ext_pct"] or 0.0) > 60
        in_zone = bool(zbot is not None and ztop is not None and zbot <= price <= ztop)
        missed = bool(zbot is not None and price > ztop)
        chased = bool(missed and (f["ma20"] is not None and price > f["ma20"]))
    else:  # short
        score = f["sell"]
        rev = f["rev_s"]
        ign = 0.0
        zbot, ztop = f["short_zbot"], f["short_ztop"]
        tgt = f["short_target"]
        # ma20 is entry TIMING, NOT part of the trend stack (spec STEP 1).
        ma_ok = f["ma50"] is not None and f["ma200"] is not None
        stack_ok = bool(ma_ok and price < f["ma50"] < f["ma200"] and price < f["weinstein"])
        dir_ok = (f["dir_prob"] or 0.0) < 45
        rev_ok = (rev or 0.0) >= 10 and int(round(f["stage"] or 0)) in (1, 2)
        ext_hostile = (f["ext_pct"] or 0.0) < -60
        in_zone = bool(zbot is not None and ztop is not None and zbot <= price <= ztop)
        missed = bool(ztop is not None and price < zbot)
        chased = bool(missed and (f["ma20"] is not None and price < f["ma20"]))

    # Risk / reward for THIS side (null when stop/target missing).
    risk = reward = None
    stop = None
    if stop is not None and tgt is not None and price is not None:
        if side == "long":
            risk = price - stop
            reward = tgt - price
        else:
            risk = stop - price
            reward = price - tgt
    rr = (reward / risk) if (risk is not None and risk > 0) else None
    if rr is None:
        rr = f.get("rr_to_target")
    # The engine exports Win Prob / R:R / EV for the DOMINANT-score side ONLY (Pine picks long|short
    # by buyScore vs sellScore), so attribute them to that side and leave the other side's None.
    ev_side = "long" if (f["buy"] or 0.0) >= (f["sell"] or 0.0) else "short"
    ev_r = f.get("ev_r") if side == ev_side else None
    win_prob = f.get("win_prob") if side == ev_side else None
    # Mode (first match wins).
    if rev_ok:
        mode = "REVERSION_" + side.upper()
    elif ign == 1:
        mode = "BREAKOUT_LONG"
    elif dir_ok and stack_ok and (score or 0.0) >= 65:
        mode = "TREND_" + side.upper()
    else:
        return {
            "side": side,
            "mode": "NONE",
            "triage": None,
            "reason": "no_setup",
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
        }

    regime = int(round(f["regime"] or 0))
    exhaustion = f["exhaustion"] or 0.0
    climax = (regime == 2) or (exhaustion > 0.7) or ext_hostile
    no_room = reward is not None and reward <= 0
    triage = "PASS"
    reason = "setup"
    flags: List[str] = []

    if mode.startswith("REVERSION"):
        flags.append("counter_trend_high_risk")
    else:  # TREND_* and BREAKOUT_LONG are both with-trend directional entries — SAME regime gates.
        # (Previously BREAKOUT_LONG only checked climax and skipped distribution, so an Ignition=1
        # name in Regime 3 distribution — the EA case — bypassed the check the TREND path applies.)
        if mode == "BREAKOUT_LONG" and (f["dir_prob"] or 0.0) < 55:
            triage, reason = "WATCH", "weak_breakout"
        if climax:
            triage, reason = "WATCH", "climax_blocked"
        if regime == 3:
            triage, reason = "WATCH", "distribution"

        # NO ROOM — target already behind current price (reward<=0). A hard structural fail in ANY mode:
        # there is no upside left to research, however strong the trend/score. Catches chased breakouts
        # whose Pine EV is stale-positive because it was computed from the zone entry, not current price.
        if no_room:
            triage, reason = "WATCH", "no_room"
        # NEGATIVE EV — never acceptable, in-zone or not. The entry-zone top is anchored to the current
        # bar, so a breakout on a green day sits in_zone by construction (missed == False) and would
        # otherwise skip an EV check entirely. Fall back to the R:R<1 rule only when EV wasn't exported
        # (older Data Window scrape / opposite-of-dominant side) AND the entry was actually missed.
        elif ev_r is not None and ev_r <= 0:
            triage, reason = "WATCH", "negative_ev"
        elif missed and ev_r is None and rr is not None and rr < 1.0:
            triage, reason = "WATCH", "poor_rr_outside_zone"
    return {
        "side": side,
        "mode": mode,
        "triage": triage,
        "reason": reason,
        "score": score,
        "rev": rev,
        "rr": rr,
        "ev_r": ev_r,
        "win_prob": win_prob,
        "in_zone": in_zone,
        "missed": missed,
        "chased": chased,
        "flags": flags,
        "stack_ok": stack_ok,
        "dir_ok": dir_ok,
    }


# ---------------------------------------------------------------------------
# 3. Winner selection + soft flags
# ---------------------------------------------------------------------------
_RANK = {"PASS": 3, "WATCH": 2, "CUT": 1, None: 1}


def _choose_winner(L: dict, S: dict, f: Dict[str, Optional[float]]) -> dict:
    """STEP 3 — pick the stronger side, then annotate soft flags (STEP 4)."""
    if L["mode"] == "NONE" and S["mode"] == "NONE":
        # No qualifying setup either side — reuse an assessed object so the
        # location / rr / score survive, and attach a fallback triage.
        rev_l = f["rev_l"] or 0.0
        rev_s = f["rev_s"] or 0.0
        buy = f["buy"] or 0.0
        sell = f["sell"] or 0.0
        if rev_l >= 7 or rev_s >= 7:
            W = L if rev_l >= rev_s else S
            W["triage"], W["reason"] = "WATCH", "reversal_forming"
        elif max(buy, sell) >= 50:
            W = L if buy >= sell else S
            W["triage"], W["reason"] = "WATCH", "moderate_no_setup"
        else:
            W = L if buy >= sell else S
            W["triage"], W["reason"] = "CUT", "no_edge"
        W["mode"] = "NONE"
    else:
        candidates = [s for s in (L, S) if s["mode"] != "NONE"]
        W = max(candidates, key=lambda s: (_RANK.get(s["triage"], 1), s["score"] or 0.0))

    # --- STEP 4: soft flags on the winning side (annotate only) ---
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
    if (W.get("mode") or "").startswith("REVERSION"):
        if cs == "long" and stage == 4:
            W["flags"].append("reversal_against_stage")
        elif cs == "short" and stage == 2:
            W["flags"].append("reversal_against_stage")
    if regime == 1:
        W["flags"].append("extended")
    if regime == 6:
        W["flags"].append("squeeze")

    return W


# ---------------------------------------------------------------------------
# 4. Orchestrator
# ---------------------------------------------------------------------------
def run_data_window_filter(ticker: str, raw: dict) -> Dict[str, Any]:
    """Run the full pre-filter for one ticker. Returns the STEP 5 output dict.

    On missing core fields (STEP 0) returns a CUT("bad_data") verdict with both
    plans included (levels may be null) so downstream code can still record it.
    """
    f = parse_data_window(raw)

    # STEP 0 — validate: core fields must exist (never default to 0).
    if any(f.get(field) is None for field in _CORE_FIELDS):
        logger.info(f"[{ticker}] Data Window pre-filter: CUT (bad_data) — missing core field")
        return {
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
            "mtf_short": f.get("mtf_short"),
            "bad_data": True,
        }

    L = _assess_side("long", f)
    S = _assess_side("short", f)
    W = _choose_winner(L, S, f)

    return {
        "ticker": ticker,
        "chosen_side": W["side"],
        "mode": W["mode"],
        "triage": W["triage"],
        "reason": W["reason"],
        "conviction": W["score"],
        "rev": W["rev"],
        "rr": W["rr"],
        "ev_r": W["ev_r"],
        "win_prob": W["win_prob"],
        "in_zone": W["in_zone"],
        "missed": W["missed"],
        "dir_prob": f["dir_prob"],
        "regime": f["regime"],
        "flags": W["flags"],
        "long_plan": _plan(f, "long"),
        "short_plan": _plan(f, "short"),
        "recency": decode_recency(f),
        "action_long": decode_action(f.get("action_long")),
        "action_short": decode_action(f.get("action_short")),
        "action": decode_action(
            f.get("action_long") if W["side"] == "long" else f.get("action_short")
        ),
        "action_actionable": action_is_actionable(
            f.get("action_long") if W["side"] == "long" else f.get("action_short")
        ),
        "mtf_long": f.get("mtf_long"),
        "mtf_short": f.get("mtf_short"),
        "bad_data": False,
    }


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
# 5. RANK — sort PASS names when they exceed the deep-research budget
# ---------------------------------------------------------------------------
def deep_research_sort_key(rec: Dict[str, Any]) -> Tuple[int, float, float, float, float]:
    """THE single ranking key for deep-research selection.

    Used by BOTH the enrichment top-N pick (run_local_research) AND the paid
    cap-of-N (deep_research.py). Having one function here is what stops the two
    stages from ranking by different criteria (they used to, so a news-penalised
    name could sink at enrichment then rank right back in at the paid cap).

    TWO-TIER ranking, sorted DESC:
      Block 1 (actionable NOW): in_zone == True. These are real, triggerable
        setups. Within the block, ranked by ev_r (the genuine edge from the zone
        entry), with news folded in as a SOFT penalty:
            − 1.0R  if news_contradiction
            − 0.25R if news_negative
      Block 2 (chased / pullback / not-yet-in-zone): in_zone == False. Strictly
        BELOW every actionable name. Their ev_r is a MIRAGE — Pine computes it
        from the zone entry price, so it stays high after price has left the
        zone. We therefore rank these by rr (reward/risk from the CURRENT price),
        never by ev_r, and they can only fill cap slots left empty by Block 1.

    The ACTION state is deliberately NOT a ranking factor. It is a poor predictor
    of research VALUE: a not-yet-triggered FORMING may be quiet accumulation (a
    high-value discovery), while a "triggered" ACCELERATION can be a blow-off trap
    (MSFT printed ACCELERATION and dumped the next day). Pre-ranking research by
    ACTION would push the paid pass toward late/trap signals and away from the
    accumulation setups it exists to catch. ACTION stays INFORMATIONAL (the LLM
    reads it, it's stored for logs) — ranking is pure economic edge, and the
    fundamental research + human judgment decide.

    Final tie-breakers (within a block): |dir_prob − 50| (edge), then conviction.
    News flags are read off the record; absent at enrichment time, applied later.

    Returns a tuple whose FIRST element forces all in-zone names above all
    non-in-zone names regardless of their ev_r/rr magnitude.
    """
    in_zone = bool(rec.get("in_zone"))
    regime = int(round(rec.get("regime") or 0))
    ev_r = rec.get("ev_r")
    rr = rec.get("rr") or 0.0
    dir_edge = abs((rec.get("dir_prob") or 50) - 50)
    conviction = rec.get("conviction") or 0.0

    if in_zone:
        # Block 1: actionable now. Rank by ev_r (genuine from-zone edge).
        adj_ev = ev_r if ev_r is not None else float("-inf")
        if rec.get("news_contradiction"):
            adj_ev -= 1.0
        if rec.get("news_negative"):
            adj_ev -= 0.25
        # (1, ...) > (0, ...) guarantees every in-zone name outranks every
        # non-in-zone name; secondary key ev_r.
        return (1, adj_ev, dir_edge, conviction, 0.0)
    else:
        # Block 2: chased / pullback. Strictly below in-zone. ev_r here is stale
        # (measured from the zone entry, not current price) — rank by rr instead.
        return (0, rr, dir_edge, conviction, 0.0)


def rank_pass_tickers(pass_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort deep-research candidates via the shared ``deep_research_sort_key``.

    TWO-TIER: every in_zone==True (actionable now) name outranks every
    in_zone==False (chased/pullback) name. Within-tier ordering:
      - in-zone: by ev_r (genuine edge from the zone entry)
      - non-in-zone: by rr (reward/risk from CURRENT price) — ev_r is a mirage
        for these because Pine measures it from the zone entry, not current price.
    dir_prob edge and conviction are final tie-breakers. This keeps deep research
    (paid) on triggerable setups first; chased names only fill leftover cap slots.
    """
    return sorted(pass_records, key=deep_research_sort_key, reverse=True)


# ---------------------------------------------------------------------------
# 6. Alpaca news + sentiment gate
# ---------------------------------------------------------------------------
def fetch_alpaca_news(ticker: str) -> List[str]:
    """Basic news fetch: return recent headline strings (no scraping).

    Uses Alpaca (if configured) -> Finnhub -> Yahoo Finance as fallbacks.
    Cheap and rate-limit-free — this is the *only* news source for the pre-filter;
    the heavy DDGS scrape is intentionally avoided here.
    """
    items = []
    try:
        import os
        from datetime import datetime, timedelta

        import requests

        alpaca_key = os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_KEY_ID")
        alpaca_secret = os.getenv("ALPACA_SECRET_KEY")

        # 1. Try Alpaca if keys exist
        if alpaca_key and alpaca_secret:
            base_url = os.getenv("ALPACA_API_URL", "https://data.alpaca.markets")
            base_url = base_url.replace("paper-api.alpaca.markets", "data.alpaca.markets").replace(
                "api.alpaca.markets", "data.alpaca.markets"
            )
            base_url = base_url.split("/v2")[0]

            headers = {"APCA-API-KEY-ID": alpaca_key, "APCA-API-SECRET-KEY": alpaca_secret}
            end_date = datetime.now()
            start_date = end_date - timedelta(days=3)
            params = {
                "symbols": ticker,
                "start": start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": 10,
            }
            resp = requests.get(
                f"{base_url}/v1beta1/news", headers=headers, params=params, timeout=5
            )
            if resp.status_code == 200:
                items = [
                    art.get("headline", "")
                    for art in resp.json().get("news", [])
                    if art.get("headline")
                ]
                if items:
                    return items

        # 2. Try Finnhub fallback
        try:
            from src.clients.news_client import _fetch_finnhub_news

            finnhub_ctx = _fetch_finnhub_news(ticker, days=3)
            if finnhub_ctx:
                items = [
                    block.split("\nSummary:")[0]
                    for block in finnhub_ctx.split("\n\n")
                    if block.strip()
                ]
                if items:
                    return items
        except Exception:
            pass

        # 3. Try Yahoo Finance fallback
        import yfinance as yf

        yf_news = yf.Ticker(ticker).news
        if yf_news:
            items = [art.get("title", "") for art in yf_news if art.get("title")]

    except Exception as e:
        logger.warning(f"[{ticker}] News fetch failed: {e}")

    return items


def _parse_sentiment(raw: str, ticker: str) -> Optional[Dict[str, Any]]:
    """Extract {label, summary} from a model response, or None if unparseable."""
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
    """Classify recent-headline sentiment with the LOCAL 9B only.

    Reliability-first: we removed the OpenRouter/free race because the remote
    free tier rate-limits under the 148-ticker sweep and was an unreliable
    dependency. The local Qwen is unlimited (no rate limit, ever) and runs
    thinking-off with a tiny JSON schema, so this is both free and stable.
    Falls back to neutral only if the local call itself fails.

    Returns {"label": "positive"|"neutral"|"negative", "summary": str}.
    """
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


def triage_ticker(ticker: str, data_window: dict, fetch_news: bool = True) -> Dict[str, Any]:
    """Combined pre-filter: Data Window verdict + basic Alpaca news sentiment.

    The Data Window decides whether a trade setup exists (PASS / WATCH / CUT).
    News sentiment is ONE INPUT, NOT A VETO: a negative news tone is recorded
    as `news_negative` (informational) and may trim conviction downstream, but
    it can NEVER block a ticker on its own. The deterministic technical verdict
    owns `pursue`; news only annotates.
    """
    verdict = run_data_window_filter(ticker, data_window)
    sentiment: Dict[str, Any] = {"label": "neutral", "summary": "", "headlines": []}
    if fetch_news:
        headlines = fetch_alpaca_news(ticker)
        sentiment["headlines"] = headlines
        if headlines:
            sentiment.update(classify_sentiment(ticker, headlines))
    verdict["sentiment"] = sentiment

    technical_pass = verdict["triage"] == "PASS"
    sentiment_negative = sentiment.get("label") == "negative"
    # News cannot veto: pursue follows the technical verdict. A negative news
    # tone is surfaced as a flag, not a blocker.
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
        "MSFT": dict(
            price=488.61,
            ma20=410.43,
            ma50=402.63,
            ma200=421.77,
            weinstein=395.38,
            buy=98.08,
            sell=56.97,
            stage=3,
            dir_prob=95.78,
            regime=3,
            ext_pct=15.89,
            exhaustion=0.4794,
            rev_l=7,
            rev_s=0,
            ignition_long=0,
            long_zbot=480.14,
            long_ztop=484.34,
            long_stop=458.21,
            long_target=550.24,
            short_zbot=None,
            short_ztop=None,
            short_stop=502.15,
            short_target=437.23,
        ),
    }
    expected = {
        "MSFT": ("long", "NONE", "WATCH", ["chased"]),
    }
    ok = True
    for tk, fields in cases.items():
        out = run_data_window_filter(tk, fields)
        exp_side, exp_mode, exp_triage, exp_flags = expected[tk]
        checks = [
            ("chosen_side", out["chosen_side"], exp_side),
            ("mode", out["mode"], exp_mode),
            ("triage", out["triage"], exp_triage),
            ("flags", out["flags"], exp_flags),
        ]
        for name, got, exp in checks:
            if got != exp:
                ok = False
                logger.error(f"SELF-TEST {tk} {name}: got {got!r}, expected {exp!r}")
        # Combined triage (no network): pursue == (data window PASS) when news
        # sentiment is neutral (fetch_news=False keeps sentiment neutral).
        tr = triage_ticker(tk, fields, fetch_news=False)
        exp_pursue = out["triage"] == "PASS"
        if tr["pursue"] != exp_pursue:
            ok = False
            logger.error(f"SELF-TEST {tk} pursue: got {tr['pursue']!r}, expected {exp_pursue!r}")
        logger.info(
            f"SELF-TEST {tk}: side={out['chosen_side']} mode={out['mode']} "
            f"triage={out['triage']} "
            f"pursue={tr['pursue']} flags={out['flags']} rr={out['rr']}"
        )
    logger.info("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    _self_test()
