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
import re
import unicodedata
from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

_ALNUM_RE = re.compile(r"[^a-z0-9+-]")

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
# Provenance: 2016-2026 bar universe cut points (close >= $20).
EXT_MAX = 25.0       # Ext Pct vs MA200 >= 25% (era-robust hard exclusion: -1.78% / -1.00% 21d excess)
# Ext Z Self Relative (stock's own historical extension distribution, already exported
# by the indicator) >= 2.5 -> -0.62% vs +0.69% SIG for the rest, on the mega-cap subset.
# Not yet statistically significant at n=15 names, but correct sign where the absolute
# EXT_MAX threshold above was wrong-signed for this population -- self-relative context
# generalizes better than a cross-sectional price/extension cutoff for large-cap-only use.
EXT_Z_SELF_MAX = 2.5
P_RICH = 125.4       # Price 2/3 quantile (2016-2026 bars, close >= $20; measured threshold)
HV_HIGH = 35.9       # HV20 80th percentile (ann %; measured threshold)

# AT-MARKET R:R LANE -- the only rule in this system that passed a sector AND ticker breadth test.
# Measured with the indicator's own stop/target, path-accurate (stop checked before target, 21 bars),
# R relative to the same-day universe. Gate = in long zone AND at-market R:R >= X AND fade off:
#   - >= 2  n=39,740  +0.116R  4/4 eras  12/12 sectors  69.9% of 519 names  <- PASS lane
#   - >= 5  n= 3,884  +0.252R  4/4 eras  both ticker halves  68.3% of 156 names
# 6.0 is stronger still (+0.369R) but only 33 names reach n>=10, so its breadth is unverifiable and
# it is deliberately NOT used -- the same standard that rejected PRIME and code 20 for breadth.
# Win rate FALLS as the ratio rises (34% at >=2, 23% at >=5): the edge is payoff, not hit rate.
# User specification: R:R >= 1.5 is accepted for the PASS lane.
RR_MKT_PASS = float(os.getenv("RR_MKT_PASS", "2.0"))
RR_MKT_STRONG = 3.0
# Set RR_LANE_ENABLED=0 to restore code-20-only PASS behaviour for an A/B comparison.
RR_LANE_ENABLED = os.getenv("RR_LANE_ENABLED", "1") not in ("0", "false", "False")

# Measured lanes, post-COVID (Mar-2020 to Jul-2026, 529 tickers, path-accurate 21 bars)
LANE_PRIORS = {
    "rr_at_market_lane_strong": (25.0, 0.13),
    "rr_at_market_lane": (30.0, 0.08),
    "reversal_buy_lane": (45.0, 0.08),
    "oversold_lane": (51.0, 0.06),
    "rsi2_setup_lane": (61.0, 0.06),
}

LANE_TO_SETUP_LANE = {
    "rr_at_market_lane_strong": "RR_SETUP_STRONG",
    "rr_at_market_lane": "RR_SETUP",
    "reversal_buy_lane": "CODE20",
    "oversold_lane": "OVERSOLD",
    "rsi2_setup_lane": "RSI2",
}


# ---------------------------------------------------------------------------
# 1. Data Window label mapping
# ---------------------------------------------------------------------------
# Keyed by TradingView indicator label strings. Substring match tolerates minor label drift.
_FIELD_LABELS = {
    "price": ("close",),
    "ma20": ("ma 20 fast", "ma 20",),
    "ma50": ("ma 50 mid", "ma 50",),
    "ma200": ("ma 200 slow", "ma 200",),
    "weinstein": ("weinstein ma 150", "weinstein",),
    # The Pine renamed these exports from "Buy Score"/"Sell Score" to
    # "Long Setup Score"/"Short Pressure Score" (lines 5971-5972). The old
    # labels are kept for pre-rename scrapes. CRITICAL: buy/sell are in
    # _CORE_FIELDS, so without the new label EVERY current scrape parses
    # buy/sell as None and the whole pipeline CUTs as bad_data.
    "buy": ("long setup score", "buy score",),
    "sell": ("short pressure score", "sell score",),
    "stage": ("context stage age pack", "stage age pack", "stage 1 base 2 up 3 top 4 down", "stage (1=", "stage 1 base", "stage 1"),
    "stage_age_bars": ("context stage age pack", "stage age bars", "stage age",),
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
    "entry_at_market": ("entry at market 0no 1l 2s 3both", "entry at market",),
    "rev_l": ("long rev zone",),
    "rev_s": ("short rev zone",),
    "ext_pct": ("ext pct vs ma200", "ext%", "ext pct"),
    "ext_z_self": ("ext z self relative", "ext z self",),
    "exhaustion": ("exhaustion gradient",),
    "regime": ("regime 0 hlt 1 ext 2 clmx 3 dist 4 dn 5 ign 6 sqz", "regime (", "regime 0 hlt"),
    # The Pine renamed this export from "Dir Prob Pct Above 50 Bull" to
    # "Evidence Bias Pct Above 50 Bull" (stateEvidenceBias shrunk toward 50 by rrHaircut).
    # The old label is kept for pre-rename scrapes. WITHOUT the new label, current
    # scrapes parse dir_prob as None -> win_prob/ev_r abstain, dir_ok can never be
    # True (TREND mode dies), and the RR-lane PASS can be mis-gated. The Pine's own
    # comments (line ~5961) say Evidence Bias has NO discriminating power in
    # path-accurate R:R — treat it as a single-name context input, never a ranker.
    "dir_prob": (
        "evidence bias pct above 50 bull",
        "evidence bias",
        "dir prob pct above 50 bull",
        "dir prob",
    ),
    "ignition_long": ("long ignition fresh breakout", "long ignition",),
    "bear_mask": ("bear warning mask",),
    "rev_mask": ("reversal pattern mask",),
    "weak_mask": ("weak level mask",),
    "bear_age": ("bear warning age",),
    "rev_age": ("reversal pattern age",),
    "weak_age": ("weak level age",),
    "action_long": ("action long code",),
    "action_short": ("action short code",),
    "mtf_long": ("mtf long aligned 0 to 3", "mtf long aligned",),
    "energy_state": ("energy state 3 exp 2 warm 1 sqz 0 dorm", "energy state",),
    "energy_ivrank": ("energy iv rank pct", "energy iv rank",),
    "energy_iv30": ("energy iv30 ann pct", "energy iv30 (ann %)", "energy iv30 ann %",),
    "iv_hv_spread": ("energy iv hv spread", "energy iv-hv spread", "energy iv-hv spread (ivs)"),
    "hv20": ("hv20 ann pct", "hv20 (ann %)", "hv20 ann %",),
    "adx": ("adx 14", "adx (14",),
    "di_plus": ("dmi di plus", "dmi +di"),
    "di_minus": ("dmi di minus", "dmi -di"),
    "rr_to_target": ("rr to target", "r:r to target"),
    "vp_poc": ("vp poc", "poc",),
    "vp_vah": ("vp vah", "vah",),
    "vp_val": ("vp val", "val",),
    "vp_hvn_above": ("vp hvn above",),
    "vp_hvn_below": ("vp hvn below",),
    "rvol": ("rvol vs avg", "rvol (vs avg)",),
    "sprint_ema": ("sprint line ema",),
    "hull_baseline": ("hull baseline hma", "hull baseline (hma 20)", "hull baseline hma 20", "hull baseline hwa 20",),
    "rsi2_protocol": ("rsi2 protocol version", "protocol version",),
    "rsi2_events_pack": ("rsi2 events pack", "events pack",),
    "rsi2_exit_fill": ("rsi2 exit fill",),
    "rsi2_net_r": ("rsi2 exit net r",),
    "rsi2_val": ("rsi2 rsi2", "rsi2",),
    "rsi2_atr14": ("rsi2 atr14",),
    "prev_ext_z": ("prev ext z", "prev_ext_z",),
    "golden_cross": ("golden cross",),
    "death_cross": ("death cross",),
    "zone0_long": ("zone 0 long",),
    "zone0_short": ("zone 0 short",),
    "avwap_resistance": ("avwap resistance",),
    "avwap_support": ("avwap support",),
    "exp_move_pct": ("exp move pct 21b", "exp move pct",),
    "z_volume": ("z volume",),
    "z_rsi": ("z rsi",),
    "z_velocity": ("z velocity",),
    "z_elasticity": ("z elasticity",),
    "trend_bars_up": ("trend bars up",),
    "buy_sigma_evidence": ("buy sigma evidence", "buy_sigma_evidence",),
    "sell_sigma_evidence": ("sell sigma evidence", "sell_sigma_evidence",),
    "zone_rr_flags": ("zone rr flags pack",),
    "signal_pack": ("signal pack",),
    "premove_pack": ("premove pack",),
    "darvas_box_top": ("darvas box top",),
    "buy_category_pack": ("buy category pack",),
    "sell_category_pack": ("sell category pack",),
    "long_rr_at_market": ("long rr at market",),
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


@lru_cache(maxsize=4096)
def _alnum_cached(s: str) -> str:
    return _ALNUM_RE.sub("", s.lower())


def _alnum(s: str) -> str:
    """Normalize a label for matching. Memoized: '_match_label' calls this for every raw key x every
    needle across two passes, so a single parse_data_window did ~20k regex substitutions and any
    historical replay was ~4ms/row. The label vocabulary is tiny and fixed, so caching is free.
    Behaviour is identical -- same regex, same casefold."""
    return _alnum_cached(str(s))


def _match_label(raw: dict, *needles) -> Optional[str]:
    # Pass 1: Exact match on normalized alphanumeric string (prevents key collisions)
    for key in raw:
        cleaned = _alnum(key)
        if any(_alnum(n) == cleaned for n in needles):
            return key
    # Pass 2: Substring match fallback for partial/extended labels
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
        else:
            return None

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
        val = None
        if field in raw:
            val = _num_mask(raw[field], _MAX_MASK.get(field)) if field in _MASK_CLASS_FIELDS else _num(raw[field])
        if val is None:
            for needle in needles:
                key = _match_label(raw, needle)
                if key is not None:
                    candidate = _num_mask(raw[key], _MAX_MASK.get(field)) if field in _MASK_CLASS_FIELDS else _num(raw[key])
                    if candidate is not None:
                        val = candidate
                        break
        f[field] = val

    # Ensure RSI2 parameters are saved when protocol-2 is active (only swapped into long_stop_loss/target if lane is RSI2)
    if f.get("rsi2_protocol") == 2.0 or f.get("rsi2_events_pack") is not None:
        rsi2_stop_key = _match_label(raw, "rsi2 fixed stop", "rsi2 stop")
        if rsi2_stop_key and raw.get(rsi2_stop_key) is not None:
            stop_v = _num(raw[rsi2_stop_key])
            if stop_v is not None:
                f["rsi2_fixed_stop"] = stop_v

        rsi2_target_key = _match_label(raw, "rsi2 fixed target", "rsi2 target")
        if rsi2_target_key and raw.get(rsi2_target_key) is not None:
            tgt_v = _num(raw[rsi2_target_key])
            if tgt_v is not None:
                f["rsi2_fixed_target"] = tgt_v

        rsi2_entry_key = _match_label(raw, "rsi2 entry or opening ceiling", "rsi2 entry")
        if rsi2_entry_key and raw.get(rsi2_entry_key) is not None:
            ent_v = _num(raw[rsi2_entry_key])
            if ent_v is not None:
                f["rsi2_entry"] = ent_v

    f["_rr_mkt_deliberately_absent"] = bool(raw.get("_rr_mkt_deliberately_absent"))

    # Unpack zone_rr_flags if present and individual flag fields are missing
    flags_pack = f.get("zone_rr_flags")
    if flags_pack is not None:
        m = int(round(flags_pack))
        if f.get("long_in_zone") is None:
            f["long_in_zone"] = 1.0 if (m & 1) else 0.0
        if f.get("short_in_zone") is None:
            f["short_in_zone"] = 1.0 if (m & 2) else 0.0
        if f.get("long_rr_valid") is None:
            f["long_rr_valid"] = 1.0 if (m & 4) else 0.0
        if f.get("short_rr_valid") is None:
            f["short_rr_valid"] = 1.0 if (m & 8) else 0.0

    # Unpack Context Stage Age Pack if present
    stage_pack_key = _match_label(raw, "context stage age pack", "stage age pack")
    if stage_pack_key and raw.get(stage_pack_key) is not None:
        try:
            sp = int(round(float(raw[stage_pack_key])))
            f["stage"] = float(sp % 8)
            f["stage_age_bars"] = float(sp // 8)
        except Exception:
            pass

    # Decode RSI2 events pack if present
    events_pack = f.get("rsi2_events_pack")
    if events_pack is not None:
        ep = int(round(events_pack))
        f["rsi2_setup_event"] = bool(ep & 1)
        f["rsi2_armed_event"] = bool(ep & 2)
        f["rsi2_entry_event"] = bool(ep & 4)
        f["rsi2_has_exit_fill"] = bool(ep & 8)
        f["rsi2_recovery_event"] = bool(ep & 16)
        f["rsi2_exit_code"] = (ep >> 5) & 7
        f["rsi2_skip_code"] = (ep >> 8) & 3
        f["rsi2_state_code"] = (ep >> 10) & 7

    # Signal Pack bits: 1 strongBuy  2 strongSell  4 NOT-fade  8 isTopping  16 isBottoming.
    # BIT 2 IS INVERTED in the Pine ('not fadeZoneLong ? 4 : 0'), so bit2 == 0 means the fade /
    # DO NOT CHASE gate is ACTIVE. That state measures -0.038R era-stable, so it is an exclusion.
    # fade_long stays None when the column is absent (pre-2026-08-13 scrapes have no Signal Pack):
    # treating missing as "no fade" would silently promote the bars this is meant to exclude.
    sig_pack = f.get("signal_pack")
    if sig_pack is not None:
        m = int(round(sig_pack))
        f["strong_buy"] = 1.0 if (m & 1) else 0.0
        f["strong_sell"] = 1.0 if (m & 2) else 0.0
        f["fade_long"] = 0.0 if (m & 4) else 1.0
        f["is_topping"] = 1.0 if (m & 8) else 0.0
        f["is_bottoming"] = 1.0 if (m & 16) else 0.0
    else:
        f["fade_long"] = None

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

    # Risk / reward, AT MARKET (reading the actual exported stop) -- never the zone-entry ratio.
    risk = reward = None
    if stop is not None and tgt is not None and price is not None:
        if side == "long":
            risk = price - stop
            reward = tgt - price
        else:
            risk = stop - price
            reward = price - tgt

    # Prefer the indicator's own at-market ratio on the long side so this can never drift from the
    # .pine's longRRatMkt; the recomputation is the fallback for pre-2026-08-13 scrapes.
    # Do not recompute rr when the injected RR@mkt was deliberately absent.
    if side == "long":
        exported_rr_mkt = f.get("long_rr_at_market")
        if exported_rr_mkt is not None and exported_rr_mkt > 0:
            rr = exported_rr_mkt
        elif f.get("_rr_mkt_deliberately_absent"):
            rr = None
        elif stop is not None and tgt is not None and price is not None and price > stop and tgt > price:
            rr = (reward / risk) if (risk is not None and risk > 0) else None
        else:
            rr = None
    else:
        rr = (reward / risk) if (risk is not None and risk > 0) else None

    # For strong momentum / stage-2 names above the entry zone, calculate momentum R:R with a tight structural stop
    tight_stop = None
    momentum_rr = None
    if side == "long" and tgt is not None and price is not None and tgt > price:
        tight_stop = max(zbot or 0.0, f.get("ma20") or 0.0, price * 0.96)
        if 0 < tight_stop < price:
            m_risk = price - tight_stop
            m_reward = tgt - price
            if m_risk > 0:
                momentum_rr = round(m_reward / m_risk, 3)

    # Dominant side attribution.
    # THE `side == ev_side` GUARD IS LOAD-BEARING -- do not flatten it. ev_r may only be computed for
    # the side the scores actually favour. Without it, a bar with Sell 90 > Buy 70 still produces
    # ev_r = 1.4 for the LONG side, which clears TIER_A_MIN_EV_R (0.5) and buys paid research on a
    # sell-dominant setup. The non-dominant side must return None so the EV gate ABSTAINS.
    # NO 'rr_to_target' FALLBACK. That field is exported as
    # `buyScore >= sellScore ? longRR : shortRR` -- the DOMINANT side's ratio, measured from the
    # ZONE entry, not at market. It overstates the at-market ratio on 93.7% of bars (median
    # +2.11 R; AMZN 2026-08-13 zone 4.12 vs at-market 0.59) and on a sell-dominant bar it is the
    # SHORT ratio entirely. Since it feeds ev_r -> TIER_A_MIN_EV_R, leaving rr as None is
    # correct: the EV gate then abstains instead of acting on the wrong side of the book.
    ev_side = "long" if ((f["buy"] or 0.0) >= (f["sell"] or 0.0)) else "short"

    # Mode selection
    if side == "long" and (f.get("rsi2_setup_event") or f.get("rsi2_armed_event") or f.get("rsi2_state_code") == 2):
        mode = "RSI2_LONG"
    elif rev_ok or act_code == 20:
        mode = "REVERSION_" + side.upper()
    elif ign == 1:
        mode = "BREAKOUT_LONG"
    elif dir_ok and stack_ok and (score or 0.0) >= 65:
        mode = "TREND_" + side.upper()
    else:
        mode = "NONE"

    # Post-COVID: stop using Dir Prob and per-stock RSI2 stats for win_prob / ev_r (both measured no edge).
    # Replaced by empirical LANE_PRIORS assigned after triage decides.
    win_prob = None
    ev_r = None

    return {
        "side": side,
        "mode": mode,
        "triage": None,
        "reason": "no_setup" if mode == "NONE" else "setup",
        "score": score,
        "rev": rev,
        "rr": rr,
        "momentum_rr": momentum_rr,
        "tight_stop": tight_stop,
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
    # SHORT IS LEVELS-ONLY. Measured on 534 names with the engine's own short geometry: shorting
    # anything -0.084R in short zone -0.095R short R:R >= 2 -0.101R short-RR-valid -0.108R
    # (era-stable NEGATIVE, 24.7% win) Sell Score >= 96 -0.113R. Every tightening makes it WORSE,
    # which is the signature of a real negative edge rather than noise -- and the states the engine
    # rates highest are the worst ones. The short zone/stop/target stay in 'short_plan' because they
    # are honest resistance/support structure (useful for a long's target and for strike selection),
    # but a long candidate must win whenever one exists.
    if L["mode"] == "NONE" and S["mode"] == "NONE":
        buy = f["buy"] or 0.0
        sell = f["sell"] or 0.0
        W = L if buy >= sell else S
        W["mode"] = "NONE"
    else:
        candidates = [s for s in (L, S) if s["mode"] != "NONE"]
        long_candidates = [s for s in candidates if s["side"] == "long"]
        if long_candidates:
            W = max(long_candidates, key=lambda s: (s["act_code"] == 20, s["score"] or 0.0))
        else:
            W = max(candidates, key=lambda s: s["score"] or 0.0)
            W["flags"].append("short_levels_only")

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

    is_proto2 = (f.get("rsi2_protocol") == 2.0) or (f.get("rsi2_events_pack") is not None)
    if is_proto2 and f.get("stage") is None:
        f["stage"] = 1.0  # default to Stage 1 basing for Protocol 2 setups without legacy stage

    core_fields_to_check = [fld for fld in _CORE_FIELDS if not (is_proto2 and fld == "stage")]
    if any(f.get(field) is None for field in core_fields_to_check):
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
            "ext_pct": f.get("ext_pct"),
            "rank_model_score": None,
            "bad_data": True,
            "triggers": None,
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

    # CUT MEANS "NO TRADE OF ANY KIND IS CONSTRUCTIBLE" -- it drops the name from the pipeline
    # entirely, so it is reserved for absurd/unbuildable states, NOT for merely negative ones.
    ext_z_self = f.get("ext_z_self") or 0.0
    rr_mkt = W["rr"]
    fade_long = f.get("fade_long")
    # No fresh LONG entry, but the name stays alive for a structure read.
    no_fresh_long = (fade_long == 1.0) or (ext_z_self >= EXT_Z_SELF_MAX) or (act_code == 17)
    is_rsi2_setup = bool(
        W.get("mode") == "RSI2_LONG"
        or f.get("rsi2_setup_event")
        or f.get("rsi2_armed_event")
        or (f.get("rsi2_state_code") == 2 and W["side"] == "long")
    )

    if act_code == 18:
        triage, reason = "CUT", "toxic_geometry"       # stop inside the noise floor: unbuildable
    elif stage == 0 and not is_rsi2_setup:
        triage, reason = "CUT", "warmup_stage_0"       # no history: nothing is computable
    elif W["target"] is None and W["chased"] and not is_rsi2_setup:
        triage, reason = "CUT", "chasing_without_target"  # no target: no plan to construct
    # Code 20 (REVERSAL BUY): requires at-market R:R >= 2.0 per Phase 6
    elif act_code == 20 and rr_mkt is not None and rr_mkt >= RR_MKT_PASS:
        triage = "PASS"
        reason = "reversal_buy_lane"
    # THE BREADTH-VERIFIED LANE. Mirrors the chart's slate/teal callout exactly: in long zone AND
    # at-market R:R >= 2 AND fade off.
    elif (
        RR_LANE_ENABLED
        and W["side"] == "long"
        and not no_fresh_long
        and W["in_zone"]
        and rr_mkt is not None
        and rr_mkt >= RR_MKT_PASS
        and act_code not in _ACTION_SOFT_CAUTION_CODES
    ):
        triage = "PASS"
        reason = "rr_at_market_lane_strong" if rr_mkt >= RR_MKT_STRONG else "rr_at_market_lane"
    # RSI2 branch ranked below at-market R:R lane; requires not no_fresh_long per Phase 6
    elif is_rsi2_setup and not no_fresh_long:
        triage = "PASS"
        reason = "rsi2_setup_lane"
    # OVERSOLD branch: extZ <= -2.0, act_code != 18, valid stop < price < target
    elif (
        W["side"] == "long"
        and ext_z_self <= -2.0
        and act_code != 18
        and W["stop"] is not None
        and W["target"] is not None
        and W["stop"] < price < W["target"]
    ):
        triage = "PASS"
        reason = "oversold_lane"
    elif no_fresh_long:
        # Negative for BUYING, but not unbuildable -- and measurably the best premium-SELLING state.
        triage = "WATCH"
        reason = "structure_only_no_fresh_long"
    else:
        # All other non-excluded setups clear to WATCH
        triage = "WATCH"
        reason = W["reason"] if W["reason"] != "setup" else "constructible_watch"

    # Soft demotions / caution flags
    flags = list(W["flags"])
    prev_ez = f.get("prev_ext_z")
    if prev_ez is not None and prev_ez > -2.0 and ext_z_self <= -2.0:
        flags.append("oversold_first_bar")
    if act_code in _ACTION_SOFT_CAUTION_CODES:
        flags.append("soft_caution_action")
    if fade_long == 1.0:
        flags.append("fade_do_not_chase")
    if ext_z_self >= EXT_Z_SELF_MAX:
        flags.append("extreme_extension_self_relative")
    if act_code == 17:
        flags.append("parabolic")
    if (f.get("ext_z_self") or 0.0) >= 1.5:
        flags.append("ext_z_self_elevated")
    if ret_10d is not None and ret_10d >= 12.9:
        flags.append("hot_10d_return")
    if realvol_10d is not None and realvol_10d >= 42.8:
        flags.append("hot_10d_volatility")
    if price >= P_RICH and hv20 >= HV_HIGH:
        flags.append("rich_high_volatility")

    # STRUCTURE READ -- what the context supports when a fresh long is off the table. Direction and
    # "will price reach a level" are ORTHOGONAL questions answered by different fields: the zone /
    # Rev Zone / stage terms that carry the long edge are noise for the touch question, while Ext Z
    # and IV rank -- flat for direction -- are the two that carry it (bible §17.2). Strike rules in
    # Exp Move Pct 21b, never a fixed %OTM: at a fixed distance a high IV rank makes assignment
    # MORE likely (24.3% vs 20.9% at 10% OTM) but LESS likely per unit of expected move (15.6% vs
    # 23.9% at 1.5x). This is timing/strike guidance only -- §17.5 shows the overlay's expectancy is
    # NOT established (it needs real option prices), so never present it as free income.
    iv_rank = f.get("energy_ivrank")
    exp_move = f.get("exp_move_pct")
    premium_rich = iv_rank is not None and iv_rank >= 80
    structure = None
    if no_fresh_long:
        # Extended / faded: the widest measured margin on the CALL side.
        structure = "call_credit_or_covered_call" if premium_rich else "call_side_no_fresh_long"
    elif ext_z_self <= -1.5 or (W["rev"] or 0.0) >= 10:
        # Washed out: put side, and P(DN touch) < P(UP touch) at every distance (30.2% vs 38.2%).
        structure = "cash_secured_put_or_put_credit" if premium_rich else "put_side_watch"
    elif premium_rich and (W["chased"] or W["missed"] or (rr_mkt is not None and rr_mkt < RR_MKT_PASS)):
        # Rich premium on a dead-geometry / chased bar: call credit or covered call
        structure = "call_credit_or_covered_call"
    elif iv_rank is not None and iv_rank <= 20:
        structure = "debit_long_premium_cheap"

    structure_strikes = None
    if structure is not None and exp_move and price:
        # 1.25x / 1.5x ExpMove: the two rungs whose touch odds are tabulated in bible §17.1.
        structure_strikes = {
            "call_1_25x": round(price * (1 + exp_move * 1.25 / 100.0), 2),
            "call_1_50x": round(price * (1 + exp_move * 1.50 / 100.0), 2),
            "put_1_25x": round(price * (1 - exp_move * 1.25 / 100.0), 2),
            "put_1_50x": round(price * (1 - exp_move * 1.50 / 100.0), 2),
        }

    conviction = W["score"]
    if triage == "PASS":
        conviction_str = "HIGH" if ((W["rev"] or 0.0) >= 10 or is_rsi2_setup) else "MED"
    else:
        conviction_str = "HIGH" if (conviction or 0.0) >= 75 else ("MED" if (conviction or 0.0) >= 50 else "LOW")

    bar_date = raw.get("bar_date") or raw.get("time") or raw.get("Time") or raw.get("Date")

    try:
        from src.logic.watch_ranker import score_data_window

        rank_model_score = score_data_window(f)
    except Exception:
        rank_model_score = None

    # Assign post-COVID empirical priors for PASS lanes; None for WATCH/CUT
    if triage == "PASS" and reason in LANE_PRIORS:
        win_prob, ev_r = LANE_PRIORS[reason]
    else:
        win_prob, ev_r = None, None

    if triage == "WATCH":
        setup_lane = "WATCH_SHADOW"
    else:
        setup_lane = LANE_TO_SETUP_LANE.get(reason)

    if setup_lane == "RSI2":
        if f.get("rsi2_fixed_stop") is not None:
            f["long_stop_loss"] = f["rsi2_fixed_stop"]
        if f.get("rsi2_fixed_target") is not None:
            f["long_target"] = f["rsi2_fixed_target"]
        if f.get("rsi2_entry") is not None:
            f["long_entry"] = f["rsi2_entry"]

    verdict = {
        "ticker": ticker,
        "bar_date": str(bar_date) if bar_date is not None else None,
        "chosen_side": W["side"],
        "mode": W["mode"],
        "triage": triage,
        "reason": reason,
        "setup_lane": setup_lane,
        "conviction": conviction,
        "conviction_str": conviction_str,
        "rev": W["rev"],
        "rr": W["rr"],
        "ev_r": ev_r,
        "win_prob": win_prob,
        "lane_prior_win": win_prob,
        "lane_prior_ev": ev_r,
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
        "ext_pct": f.get("ext_pct"),
        "tiebreak": tiebreak_score(W, f),
        "rank_model_score": rank_model_score,
        "bad_data": False,
        # Structure read (see the note above): no_fresh_long says "do not BUY here", which is not
        # the same as "no trade". Consumers should read these instead of re-deriving them.
        "no_fresh_long": no_fresh_long,
        "fade_long": f.get("fade_long"),
        "long_bot": f.get("long_zbot"),
        "long_top": f.get("long_ztop"),
        "structure": structure,
        "structure_strikes": structure_strikes,
        "iv_rank": iv_rank,
        "exp_move_pct": exp_move,
        "rr_at_market": f.get("long_rr_at_market"),
        "momentum_rr": W.get("momentum_rr"),
        "tight_stop": W.get("tight_stop"),
        "protocol_version": int(f["rsi2_protocol"]) if f.get("rsi2_protocol") is not None else 1,
        "rsi2_events_pack": int(f["rsi2_events_pack"]) if f.get("rsi2_events_pack") is not None else None,
        "rsi2_setup_event": f.get("rsi2_setup_event", False),
        "rsi2_armed_event": f.get("rsi2_armed_event", False),
    }

    # Buy-Trigger Gap Engine: compute how far the current bar is from each
    # actionable state.  Feature-flagged via TRIGGERS_ENABLED env.
    try:
        from src.logic.trigger_gaps import compute_triggers
        verdict["triggers"] = compute_triggers(f)
    except Exception:
        verdict["triggers"] = None

    _log_data_window_scrape(ticker, raw, verdict)
    return verdict


def _plan(f: Dict[str, Optional[float]], side: str) -> Dict[str, Optional[float]]:
    if side == "long":
        return {
            "zone": [f["long_zbot"], f["long_ztop"]],
            "stop": f["long_stop_loss"],
            "target": f["long_target"],
        }
    return {
        "zone": [f["short_zbot"], f["short_ztop"]],
        "stop": f["short_stop_loss"],
        "target": f["short_target"],
    }
# ---------------------------------------------------------------------------
# 5. RANK — sort candidates for deep research selection
# ---------------------------------------------------------------------------
def deep_research_sort_key(rec: Dict[str, Any]) -> Tuple[int, int, float, int, float, float, float]:
    """THE single ranking key for deep-research selection.

    Priority order (all derived from the gem/bible measured rules, NOT from
    cross-sectional ranking fields the gem forbids):
    1. PASS verdict outranks WATCH/CUT.
    2. REVERSAL BUY action code (action == "REVERSAL BUY") — the one measured
       counter-trend lane (code 20, era-robust +0.61/+0.72 across both eras).
    3. `rr_at_market` (Long RR At Market) — the field the gem's ⚖️ R:R callout
       gates on. Measured: +0.116R at >=2 (4/4 eras, 12/12 sectors), +0.252R at
       >=5. This is the only continuous field with a measured, era-stable,
       breadth-verified edge, so it orders candidates.
    4. `is_rsi2` — RSI2 pullback long candidate.
    5. `ev_r` — expected-value ratio (win_prob * rr - (1-win_prob)), deterministic
       and side-guarded. Ties the rr_at_market order.
    6. `ext_pct` — DEMOTED to a tiebreak.
    7. `conviction` — final deterministic tiebreak.
    """
    if not rec:
        return (0, 0, -1e9, 0, -1e9, 0.0, 0.0)

    # Unpack nested triage dict if outer record passed
    if isinstance(rec.get("triage"), dict):
        rec = rec["triage"]

    # THIS KEY IS DIRECTIONAL-ONLY. For an income/premium-selling candidate the
    # useful ordering is IV rank / Ext Z / ExpMove, which is close to the OPPOSITE
    # ranking -- one key cannot order two different trade types. Income names are
    # already excluded from the paid pass upstream (_deep_research_gate), so this
    # is a belt-and-braces guard: if one ever reaches here, sort it last.
    if rec.get("no_fresh_long"):
        return (0, 0, -1e9, 0, -1e9, 0.0, 0.0)

    is_pass = 1 if rec.get("triage") == "PASS" else 0
    is_rev_buy = 1 if rec.get("action") == "REVERSAL BUY" else 0
    is_rsi2 = 1 if rec.get("mode") == "RSI2_LONG" else 0

    # rr_at_market: the measured alpha field (gem ⚖️ R:R callout). 0 = invalid
    # (4.5% of bars); treat as the lowest possible value so it sorts last.
    rr_mkt_raw = rec.get("rr_at_market")
    rr_mkt = float(rr_mkt_raw) if rr_mkt_raw is not None and float(rr_mkt_raw) > 0 else 0.0

    # ev_r: expected-value ratio, side-guarded (None when the side doesn't match
    # the dominant score direction). None -> 0 so it doesn't crash the sort.
    ev_r_raw = rec.get("ev_r")
    ev_r = float(ev_r_raw) if ev_r_raw is not None else 0.0

    # News penalties on the R-scale: a contradiction should cost more than the
    # gap between adjacent candidates (single-digit R), not merely nudge.
    if rec.get("news_contradiction"):
        ev_r -= 3.0
    elif rec.get("news_negative"):
        ev_r -= 0.75

    # ext_pct: DEMOTED to tiebreak. Hard-penalize the gem/bible exclusion band
    # (25-60% = -0.71% ex21 SIG) so even on a tie the key never promotes a
    # gem-0%-size name over a clean one.
    ext_pct = float(rec.get("ext_pct") or 0.0)
    if 25.0 <= ext_pct < 60.0:
        ext_pct -= 100.0  # push the whole exclusion band below every clean name

    conviction = float(rec.get("conviction") or 0.0)

    return (is_pass, rr_mkt, is_rev_buy, is_rsi2, ev_r, ext_pct, conviction)

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
    # Anything not CUT is worth pursuing. This used to be PASS-only, which made the
    # WATCH branch of process_survivor._deep_research_gate's `quality_pass`
    # unreachable: `send` ANDs quality_pass with `pursue`, so a WATCH could satisfy
    # WATCH_MIN_CONVICTION and still never be promoted. PASS is only the code-20
    # REVERSAL BUY lane (~0.9 names/day across 490), so deep research was being fed
    # from <1% of the pool while 300-380 WATCH names/day were discarded unexamined.
    pursue = verdict["triage"] in ("PASS", "WATCH")
    if technical_pass and sentiment_negative:
        pursue_reason = "data_window_pass_with_negative_news"
    elif technical_pass:
        pursue_reason = "pass"
    elif pursue:
        pursue_reason = verdict["reason"] or "constructible_watch"
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
        # Case 2: Hard exclusion CUT (ext_z_self >= 2.5) -> CUT extreme_extension_self_relative
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
            ext_pct=30.0,
            ext_z_self=3.0,  # >= 2.5 cut
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
        # Case 6: The measured at-market R:R lane, in-zone + rr 3.0 + fade off -> PASS
        "RR_LANE": dict(
            price=100.0, ma20=98.0, ma50=95.0, ma200=90.0, weinstein=92.0,
            buy=80.0, sell=30.0, stage=2, dir_prob=60.0, regime=0,
            ext_pct=11.0, exhaustion=0.1, rev_l=0.0, rev_s=0.0,
            action_long=1.0,
            long_zbot=99.0, long_ztop=101.0,
            long_stop_loss=95.0, long_target=115.0,  # At-market RR = 15/5 = 3.0
            zone_rr_flags=5.0,   # bit0 in-zone + bit2 rr-valid
            signal_pack=4.0,     # bit2 set -> fade OFF
        ),
        # Case 7: Identical bar with the fade gate ACTIVE (signal_pack bit2 clear) -> CUT
        "RR_FADE": dict(
            price=100.0, ma20=98.0, ma50=95.0, ma200=90.0, weinstein=92.0,
            buy=80.0, sell=30.0, stage=2, dir_prob=60.0, regime=0,
            ext_pct=11.0, exhaustion=0.1, rev_l=0.0, rev_s=0.0,
            action_long=1.0,
            long_zbot=99.0, long_ztop=101.0,
            long_stop_loss=95.0, long_target=115.0,
            zone_rr_flags=5.0,
            signal_pack=0.0,     # bit2 clear -> fade ACTIVE
            energy_ivrank=86.0,  # Premium rich -> should resolve to a credit/covered-call read
            exp_move_pct=15.0,   # Exercises the ExpMove strike ladder
        ),
        # Case 8: 'rr_to_target' must NOT be used as a fallback -- here Sell > Buy so that field is
        # the SHORT ratio; rr and ev_r must both stay None (asserted after the loop).
        # Case 9: Sell-dominant bar where the LONG side still wins side-selection. ev_r must be None
        # -- the `side == ev_side` guard. Without it this bar reports ev_r 1.4 and buys paid research
        # on a setup the scores say is short. The triage verdict alone does not catch this.
        "EV_SIDE_GUARD": dict(
            price=100.0, ma20=98.0, ma50=95.0, ma200=90.0, weinstein=92.0,
            buy=70.0, sell=90.0, stage=2, dir_prob=60.0, regime=0,
            ext_pct=4.0, exhaustion=0.0, rev_l=0.0, rev_s=0.0,
            action_long=1.0, action_short=8.0,
            long_zbot=99.0, long_ztop=101.0,
            long_stop_loss=95.0, long_target=115.0,
            zone_rr_flags=5.0, signal_pack=4.0,
        ),
        "RR_NO_FALLBACK": dict(
            price=100.0, ma20=98.0, ma50=95.0, ma200=90.0, weinstein=92.0,
            buy=40.0, sell=90.0, stage=2, dir_prob=45.0, regime=0,
            ext_pct=4.0, exhaustion=0.0, rev_l=0.0, rev_s=0.0,
            action_long=6.0, action_short=8.0,
            rr_to_target=3.0,
        ),
        # Case 5: Scraped TV Data Window for CAT (raw string labels, unicode minus, ∅ empty zones) -> WATCH
        "CAT": {
            "Date": "Fri 07 Aug '26",
            "Open": "865.63",
            "High": "868.00",
            "Low": "836.01",
            "Close": "842.19",
            "Change": "−14.77 (−1.72%)",
            "Volume": "2.44 M",
            "VP POC": "886.47",
            "VP VAH": "1,022.14",
            "VP VAL": "759.03",
            "VP HVN Above": "870.03",
            "VP HVN Below": "787.81",
            "RVOL Vs Avg": "0.7706",
            "Energy IV30 Ann Pct": "53.64",
            "Energy IV Rank Pct": "79.76",
            "Energy IV HV Spread": "23.08",
            "Energy State 3 Exp 2 Warm 1 Sqz 0 Dorm": "3.00",
            "HV20 Ann Pct": "43.58",
            "ADX 14": "19.92",
            "DMI DI Plus": "23.77",
            "DMI DI Minus": "26.43",
            "POC": "886.47",
            "VAH": "1,022.14",
            "VAL": "759.03",
            "Sprint Line EMA": "850.10",
            "Hull Baseline HMA": "830.97",
            "MA 20 Fast": "870.12",
            "MA 50 Mid": "886.84",
            "MA 200 Slow": "757.51",
            "Weinstein MA 150": "965.18",
            "Golden Cross": "0.0000",
            "Death Cross": "0.0000",
            "Zone 0 Long": "0.0000",
            "Zone 0 Short": "0.0000",
            "AVWAP Resistance": "892.18",
            "AVWAP Support": "834.77",
            "Buy Score": "63.24",
            "Sell Score": "56.53",
            "Stage 1 Base 2 Up 3 Top 4 Down": "4.00",
            "Stage Age Bars": "24.00",
            "Long Entry": "834.77",
            "Long Entry Zone Bot": "829.49",
            "Long Entry Zone Top": "840.05",
            "Long Stop Loss": "808.37",
            "Long Target": "888.87",
            "Short Entry": "860.39",
            "Short Entry Zone Bot": "∅",
            "Short Entry Zone Top": "∅",
            "Short Stop Loss": "886.79",
            "Short Target": "794.59",
            "Entry At Market 0No 1L 2S 3Both": "0.0000",
            "Long Rev Zone": "0.0000",
            "Short Rev Zone": "3.00",
            "Ext Pct vs MA200": "11.18",
            "Exhaustion Gradient": "0.0802",
            "Ext Z Self Relative": "−1.37",
            "Regime 0 Hlt 1 Ext 2 Clmx 3 Dist 4 Dn 5 Ign 6 Sqz": "4.00",
            "Exp Move Pct 21b": "12.58",
            "Dir Prob Pct Above 50 Bull": "20.67",
            "Long Ignition Fresh Breakout": "0.0000",
            "RR To Target": "2.05",
            "Long Target T1 Waypoint": "888.87",
            "Short Target T1 Waypoint": "794.59",
            "Zone RR Flags Pack": "12.00",
            "Action Long Code": "8.00",
            "Action Short Code": "8.00",
            "MTF Long Aligned 0 To 3": "1.0000",
            "Bear Warning Mask": "1.0000",
            "Reversal Pattern Mask": "20.00",
            "Weak Level Mask": "0.0000",
            "Bear Warning Age": "19.00",
            "Reversal Pattern Age": "7.00",
            "Weak Level Age": "∅",
            "Z Volume": "0.0000",
            "Z RSI": "−0.0787",
            "Z Velocity": "−1.19",
            "Z Elasticity": "−1.36",
            "Trend Bars Up": "0.0000",
            "Buy Sigma Evidence": "0.8090",
            "Sell Sigma Evidence": "1.52",
            "Premove Pack": "1,088.00",
            "Darvas Box Top": "935.00",
            "Buy Category Pack": "65,536.00",
            "Sell Category Pack": "18.00",
        },
    }

    expected = {
        "REV_BUY": ("long", "REVERSION_LONG", "PASS", "reversal_buy_lane"),
        # Extension is negative for BUYING but is the best premium-SELLING context, so it is a
        # structure-only WATCH now, not a CUT. CUT would drop the name and forfeit that trade.
        "EXT_CUT": ("long", "TREND_LONG", "WATCH", "structure_only_no_fresh_long"),
        "RR_TRAP": ("long", "TREND_LONG", "WATCH", "constructible_watch"),
        "STAGE0": ("long", "TREND_LONG", "CUT", "warmup_stage_0"),
        "CAT": ("long", "NONE", "WATCH", "no_setup"),
        "RR_LANE": ("long", "TREND_LONG", "PASS", "rr_at_market_lane_strong"),
        "RR_FADE": ("long", "TREND_LONG", "WATCH", "structure_only_no_fresh_long"),
        "RR_NO_FALLBACK": ("short", "NONE", "WATCH", "no_setup"),
        "EV_SIDE_GUARD": ("long", "TREND_LONG", "PASS", "rr_at_market_lane_strong"),
    }

    ok = True
    results = {}
    for tk, fields in cases.items():
        out = run_data_window_filter(tk, fields)
        results[tk] = out
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

    # A triage check alone cannot catch the rr_to_target fallback coming back: the verdict would
    # still be WATCH while ev_r was silently built from the SHORT side's zone ratio. Assert directly.
    nf = results.get("RR_NO_FALLBACK", {})
    for name in ("rr", "ev_r"):
        if nf.get(name) is not None:
            ok = False
            logger.error(f"SELF-TEST RR_NO_FALLBACK {name}: got {nf.get(name)!r}, expected None "
                         f"(rr_to_target must never be used as a fallback)")

    # Assert lane prior semantics on EV_SIDE_GUARD (strong lane prior EV = 0.13)
    esg = results.get("EV_SIDE_GUARD", {})
    if esg.get("ev_r") != 0.13:
        ok = False
        logger.error(f"SELF-TEST EV_SIDE_GUARD ev_r: got {esg.get('ev_r')!r}, expected 0.13 "
                     f"(lane prior semantics for rr_at_market_lane_strong)")

    # A blocked long must still carry a usable structure read, otherwise the demotion-instead-of-CUT
    # is pointless -- the whole reason for not CUTting is that a trade remains constructible.
    for tk in ("RR_FADE", "EXT_CUT"):
        r = results.get(tk, {})
        if not r.get("no_fresh_long"):
            ok = False
            logger.error(f"SELF-TEST {tk}: no_fresh_long should be True")
        if not r.get("structure"):
            ok = False
            logger.error(f"SELF-TEST {tk}: expected a structure read, got {r.get('structure')!r}")
        logger.info(f"SELF-TEST {tk}: structure={r.get('structure')!r} strikes={r.get('structure_strikes')!r}")

    logger.info("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    _self_test()
