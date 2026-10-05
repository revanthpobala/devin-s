"""
ENTRY and RISK alert tiers: what actually reaches the phone, and what is deliberately silent.

Two hard rules shape everything here.

1. Every number in a push is a MEASURED number with its sample size attached. "RR 14" is not an
   edge; "RR 14 off a 0.2-ATR stop" is a stop inside noise that produces 14:1 on paper and loses
   money in practice. The corpus median stop is 0.69 ATR and 65% of stops get hit.

2. NEVER PUSH. Shorts (every measured cohort negative -- levels only), 0DTE desk signals
   (grade A measures -0.097R on n=20), MEDIUM/HIGH priority tier alone (flat to negative,
   post-2020 non-PB onsets +0.012R = noise), A+ Trend Long / Early Action Long unless mapped to a
   measured lane, WATCH_SHADOW below RR 2, and superforecasting probabilities (Brier 0.253 vs a
   0.25 coin flip = no skill). A push channel that carries these trains you to ignore it.

The ENTRY gate is deliberately narrow, and it is the ONLY push that can open a position:

    in the long zone (Zone RR Flags Pack bit 0)
  & Long R:R valid (bit 2)
  & R:R at market >= 2
  & fade OFF (Signal Pack bit 2, which is INVERTED in the Pine)
  & PB funnel (Signal Pack bit 5 / mask 32)

PB is a reliable exclusion and R:R tier is not a substitute for it: R:R 2-3 with PB (+0.072R) beats
R:R >= 3 without PB (+0.030R). So a non-PB onset never pushes -- it goes to the digest.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src import config
from src.logic.data_window_filter import lane_prior
from src.tracking import rr_config
from src.tracking.notify import notify

logger = logging.getLogger(__name__)

# --- thresholds ---------------------------------------------------------------------------------
# The actionable floor and the HI-RR tag are user-tunable from the desk UI; both read live from
# src.tracking/rr_config so the desk, the triage step, the screener and this gate agree.
# STOP_ATR_MIN is measured and lives in rr_config next to them, but is deliberately NOT tunable.
STOP_ATR_MIN = rr_config.STOP_ATR_MIN
RISK_PER_SHARE_MAX_PCT = 5.0  # stocks
RISK_PER_SHARE_MAX_PCT_ETF = 3.0
ETFS = ("SPY", "QQQ", "IWM", "DIA", "TQQQ", "VOO", "VTI", "ARKK", "SMH", "XLK", "XLF", "XLE")

# Fade set: the action codes the Pine uses to mark "do not buy here".
FADE_SET_CODES = (11, 12, 16)   # EXTENDED / STRETCHED / BLOW-OFF-CAPITULATION
EXT_Z_TRIM = 2.0                # self-relative extension that triggers a trim

# Lane priors quoted in every push body, so the reader sees the base rate before the ticker.
LANE_PRIOR_TEXT = {
    ("rr_at_market_lane", True): "PB R:R 2-3: 34% win / +0.07R",
    ("rr_at_market_lane", False): "no-PB R:R 2-3: 32% win / -0.005R",
    ("rr_at_market_lane_strong", True): "PB R:R >=3: 26% win / +0.15R",
    ("rr_at_market_lane_strong", False): "no-PB R:R >=3: 23% win / +0.03R",
}


# --- data-window field names -----------------------------------------------------------------------
DW_ZONE_RR_PACK = "Zone RR Flags Pack"
DW_SIGNAL_PACK = "Signal Pack"
DW_CLOSE = "close"
DW_STOP = "stop_loss"
DW_TARGET = "target_1"
DW_ATR = "atr14"
DW_EXT_Z = "Ext Z Self Relative"
DW_REGIME = "Regime 0 Hlt 1 Ext 2 Clmx 3 Dist 4 Dn 5 Ign 6 Sqz"
DW_EXP_MOVE = "Exp Move Pct 21b"
DW_ZONE_BOT = "Long Entry Zone Bot"
DW_ZONE_TOP = "Long Entry Zone Top"
DW_LONG_CODE = "Action Long Code"


def _f(dw: Dict[str, Any], key: str) -> Optional[float]:
    val = dw.get(key)
    if val is None:
        return None
    try:
        out = float(val)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


# --- pack decoding (canonical, mirrors data_window_filter) ---------------------------------------

def decode_zone_rr_flags(pack: Optional[float]) -> Dict[str, bool]:
    """bit 0 long in zone · bit 1 short in zone · bit 2 long R:R valid · bit 3 short R:R valid."""
    if pack is None:
        return {"long_in_zone": False, "short_in_zone": False,
                "long_rr_valid": False, "short_rr_valid": False, "present": False}
    try:
        m = int(round(float(pack)))
    except (TypeError, ValueError):
        return {"long_in_zone": False, "short_in_zone": False,
                "long_rr_valid": False, "short_rr_valid": False, "present": False}
    return {
        "long_in_zone": bool(m & 1),
        "short_in_zone": bool(m & 2),
        "long_rr_valid": bool(m & 4),
        "short_rr_valid": bool(m & 8),
        "present": True,
    }


def decode_signal_pack(pack: Optional[float]) -> Dict[str, Any]:
    """bit 0 strongBuy · 1 strongSell · 2 NOT-fade (INVERTED) · 3 topping · 4 bottoming · 5 PB.

    Bit 2 is emitted as `not fadeZoneLong`, so 0 means the fade / DO-NOT-CHASE gate is ACTIVE.
    That state measures -0.038R era-stable and is an exclusion.
    """
    if pack is None:
        return {"strong_buy": None, "strong_sell": None, "fade_long": None,
                "is_topping": None, "is_bottoming": None, "pb_funnel": None, "present": False}
    try:
        m = int(round(float(pack)))
    except (TypeError, ValueError):
        return {"strong_buy": None, "strong_sell": None, "fade_long": None,
                "is_topping": None, "is_bottoming": None, "pb_funnel": None, "present": False}
    return {
        "strong_buy": bool(m & 1),
        "strong_sell": bool(m & 2),
        "fade_long": not bool(m & 4),
        "is_topping": bool(m & 8),
        "is_bottoming": bool(m & 16),
        "pb_funnel": bool(m & 32),
        "present": True,
    }


@dataclass
class Onset:
    """One candidate bar, decoded and measured. Everything downstream reads this, not raw packs."""
    ticker: str
    date: str
    close: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    atr: Optional[float] = None
    ext_z: Optional[float] = None
    regime: Optional[int] = None
    exp_move_pct: Optional[float] = None
    zone_bot: Optional[float] = None
    zone_top: Optional[float] = None
    action_code: Optional[int] = None
    long_in_zone: bool = False
    long_rr_valid: bool = False
    fade_long: Optional[bool] = None
    pb_funnel: Optional[bool] = None
    packs_present: bool = False

    # measured, derived
    rr_at_market: Optional[float] = None
    stop_width_atr: Optional[float] = None
    risk_pct: Optional[float] = None
    lane: Optional[str] = None
    lane_prior_win: Optional[float] = None
    lane_prior_ev: Optional[float] = None

    def __post_init__(self):
        min_rr = rr_config.min_rr()
        hi_rr = rr_config.hi_rr()
        if self.close and self.target and self.stop and self.close > self.stop:
            self._rr_raw = (self.target - self.close) / (self.close - self.stop)
            self.rr_at_market = round(self._rr_raw, 2)
        else:
            self._rr_raw = None
        # Keep the RAW width for the flag and round only for display. Rounding first turns a
        # 0.6965-ATR stop into 0.70 and lets it past a < 0.7 test -- i.e. the exact
        # artifact-R:R stop this flag exists to suppress walks through unflagged.
        if self.atr and self.atr > 0 and self.close and self.stop:
            self._stop_width_raw = abs(self.close - self.stop) / self.atr
            self.stop_width_atr = round(self._stop_width_raw, 2)
        else:
            self._stop_width_raw = None
            self.stop_width_atr = None
        if self.close and self.stop and self.close > self.stop:
            self.risk_pct = round((self.close - self.stop) / self.close * 100.0, 2)
        if self._rr_raw is None:
            self.lane = None
        elif self._rr_raw >= hi_rr:
            self.lane = "rr_at_market_lane_strong"
        elif self._rr_raw >= min_rr:
            self.lane = "rr_at_market_lane"
        else:
            self.lane = None
        prior = lane_prior(self.lane, self.pb_funnel)
        if prior:
            self.lane_prior_win, self.lane_prior_ev = prior

    # --- gate ---
    @property
    def in_zone(self) -> bool:
        return bool(self.long_in_zone)

    @property
    def rr_ok(self) -> bool:
        raw = getattr(self, "_rr_raw", self.rr_at_market)
        return raw is not None and raw >= rr_config.min_rr()

    @property
    def fade_off(self) -> bool:
        """None (no Signal Pack) is NOT fade-off -- an unmeasured bar must not be promoted."""
        return self.fade_long is False

    @property
    def is_pb(self) -> bool:
        return self.pb_funnel is True

    @property
    def stop_tight(self) -> bool:
        raw = getattr(self, "_stop_width_raw", None)
        if raw is None:
            return False
        return raw < STOP_ATR_MIN

    @property
    def risk_cap_pct(self) -> float:
        return RISK_PER_SHARE_MAX_PCT_ETF if (self.ticker or "").upper() in ETFS else RISK_PER_SHARE_MAX_PCT

    @property
    def over_risk_cap(self) -> bool:
        return self.risk_pct is not None and self.risk_pct > self.risk_cap_pct

    def entry_gate(self) -> Tuple[bool, List[str]]:
        """The ENTRY push predicate. Returns (qualifies, reasons_it_did_not)."""
        fails: List[str] = []
        min_rr = rr_config.min_rr()
        if not self.packs_present:
            return False, ["no Zone RR Flags Pack / Signal Pack on this bar (unmeasured)"]
        if not self.in_zone:
            fails.append("not in the long zone")
        if not self.long_rr_valid:
            fails.append("long R:R not marked valid")
        if not self.rr_ok:
            fails.append(f"RR@mkt {self.rr_at_market} < {min_rr}")
        if self.stop_tight:
            fails.append(f"stop width {self.stop_width_atr} ATR < {STOP_ATR_MIN} floor")
        if not self.fade_off:
            fails.append("fade gate active (do not chase)")
        if not self.is_pb:
            fails.append("not PB funnel (measured exclusion -> digest only)")
        if self.action_code in (17, 18):
            fails.append(f"action code {self.action_code} (PARABOLIC/TOXIC)")
        if self.ext_z is not None and self.ext_z >= 2.5:
            fails.append(f"ext_z {self.ext_z} >= 2.5")
        return (not fails), fails

    def next_open_verdict(self, open_px: Optional[float]) -> Tuple[bool, str]:
        """The next-open rule. Skip if the open lands at/below the stop or at/above the target."""
        if open_px is None:
            return True, "no open known; treat as an alert, confirm at the bell"
        if self.stop is not None and open_px <= self.stop:
            return False, f"SKIP: open ${open_px:.2f} is at/below stop ${self.stop:.2f}"
        if self.target is not None and open_px >= self.target:
            return False, f"SKIP: open ${open_px:.2f} is at/above target ${self.target:.2f}"
        if self.over_risk_cap:
            return False, (
                f"SKIP: risk {self.risk_pct:.1f}% exceeds the {self.risk_cap_pct:.0f}% cap"
            )
        return True, f"tradeable at open ${open_px:.2f}"


def onset_from_datawindow(ticker: str, dw: Dict[str, Any], date: str = "") -> Onset:
    """Build an Onset from a Data Window snapshot (the canonical field set)."""
    zr = decode_zone_rr_flags(_f(dw, DW_ZONE_RR_PACK))
    sp = decode_signal_pack(_f(dw, DW_SIGNAL_PACK))
    regime_raw = _f(dw, DW_REGIME)
    code_raw = _f(dw, DW_LONG_CODE)
    return Onset(
        ticker=(ticker or "").upper(),
        date=date or str(dw.get("bar_date") or ""),
        close=_f(dw, DW_CLOSE),
        stop=_f(dw, DW_STOP),
        target=_f(dw, DW_TARGET),
        atr=_f(dw, DW_ATR),
        ext_z=_f(dw, DW_EXT_Z),
        regime=int(regime_raw) if regime_raw is not None else None,
        exp_move_pct=_f(dw, DW_EXP_MOVE),
        zone_bot=_f(dw, DW_ZONE_BOT),
        zone_top=_f(dw, DW_ZONE_TOP),
        action_code=int(code_raw) if code_raw is not None else None,
        long_in_zone=zr["long_in_zone"],
        long_rr_valid=zr["long_rr_valid"],
        fade_long=sp["fade_long"],
        pb_funnel=sp["pb_funnel"],
        packs_present=zr["present"] and sp["present"],
    )


# --- ENTRY ------------------------------------------------------------------------------------

def _num(value: Optional[float], fmt: str = ".2f") -> str:
    """Format a possibly-absent measurement.

    atr14, spot and the entry prices are separate columns and any of them can be missing on a
    snapshot that still passes the gate. Formatting None raises TypeError, and a raise inside
    fire_entry() takes down every OTHER onset in the sweep with it -- one malformed bar silently
    disabling the whole ENTRY tier.
    """
    return "–" if value is None else format(value, fmt)


def entry_body(o: Onset, open_px: Optional[float] = None) -> str:
    """The push body. Every claim carries its measurement."""
    tradeable, note = o.next_open_verdict(open_px)
    prior = LANE_PRIOR_TEXT.get((o.lane, bool(o.pb_funnel)), "prior not measured for this lane")

    prices = (
        f"close ${_num(o.close)} · stop ${_num(o.stop)} · target ${_num(o.target)}"
        if (o.close and o.stop and o.target) else ""
    )
    lines = [
        f"**{o.ticker}**" + (f" {prices}" if prices else " (no persisted levels)"),
        "",
        f"- R:R @ market: **{_num(o.rr_at_market, '.1f')}**"
        + (f" · lane: {o.lane}" if o.lane else ""),
        f"- Stop width: **{_num(o.stop_width_atr)} ATR**"
        + ("  ⚠️ inside noise (<0.7 ATR) — the R:R is an artifact" if o.stop_tight
           else "  (no ATR at signal — width unmeasured)" if o.stop_width_atr is None else ""),
        f"- Risk per share: {_num(o.risk_pct, '.1f')}% of price (cap {o.risk_cap_pct:.0f}%)",
        f"- Lane prior: {prior}",
        "",
        f"_Lane prior is a base rate, not a forecast: this setup wins roughly "
        f"1 time in {round(100 / o.lane_prior_win) if o.lane_prior_win else '?'}. "
        f"Size fixed-fractional._" if o.lane_prior_win else "_No lane prior on file._",
        "",
        f"**Next open:** {note}",
    ]
    if not tradeable:
        lines.append("")
        lines.append("_Do not chase this open._")
    return "\n".join(lines)


def fire_entry(onsets: List[Onset], date: str, open_px: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Fire ENTRY pushes for qualifying PB onsets. Non-PB onsets are returned for the digest.

    Daily close only: this runs on the settled bar, never intraday. A push that can fire mid-session
    is a push that fires on noise.
    """
    pushed: List[str] = []
    digest_only: List[Dict[str, Any]] = []
    seen_tickers = set()

    for o in onsets:
        if o.ticker in seen_tickers:
            continue
        seen_tickers.add(o.ticker)

        qualifies, fails = o.entry_gate()
        if qualifies:
            key = f"ENTRY:{o.ticker}:{date}"
            hi_rr = o.rr_at_market is not None and o.rr_at_market >= rr_config.hi_rr()
            title = f"{o.ticker} RR {o.rr_at_market} @ market" + (" · HI-RR" if hi_rr else "")
            body = entry_body(o, (open_px or {}).get(o.ticker))
            if notify("ENTRY", title, body, dedupe_key=key):
                pushed.append(o.ticker)
            else:
                digest_only.append({
                    "ticker": o.ticker,
                    "rr_at_market": o.rr_at_market,
                    "pb": bool(o.pb_funnel),
                    "stop_width_atr": o.stop_width_atr,
                    "reasons": ["Suppressed by quiet hours / dedupe"],
                })
            continue

        # Not a push, but a real onsets worth recording for the digest when the RR clears the bar.
        if o.packs_present and o.in_zone and o.rr_ok:
            digest_only.append({
                "ticker": o.ticker,
                "rr_at_market": o.rr_at_market,
                "pb": bool(o.pb_funnel),
                "stop_width_atr": o.stop_width_atr,
                "reasons": fails,
            })

    return {
        "pushed": pushed,
        "digest_candidates": digest_only,
        "checked": len(seen_tickers),
    }


# --- RISK -------------------------------------------------------------------------------------

@dataclass
class RiskEvent:
    ticker: str
    kind: str            # STOP_CLOSE | GAP_THROUGH_STOP | TRIM_EXTENSION
    message: str
    dedupe_key: str
    detail: Dict[str, Any] = field(default_factory=dict)
    # Measured stop width, when known. A RISK alert that explains a stop-out without saying how
    # wide the stop was leaves the reader unable to tell a real break from a stop that sat inside
    # daily noise all along.
    stop_width_atr: Optional[float] = None


def _stop_width_line(ev: RiskEvent) -> str:
    v = ev.stop_width_atr
    if v is None:
        return "- stop width: unknown (no ATR at signal — cannot judge the stop)"
    if v < STOP_ATR_MIN:
        return (
            f"- stop width: **{v:.2f} ATR** ⚠️ inside noise (<{STOP_ATR_MIN} ATR). "
            f"The corpus median is {STOP_ATR_MIN} ATR and 65% of stops are hit — this one was "
            f"already inside the noise band, so the break was not a surprise to the tape."
        )
    return f"- stop width: **{v:.2f} ATR** (vs {STOP_ATR_MIN} ATR corpus median)"


def evaluate_risk(
    ticker: str,
    planned_stop: Optional[float],
    today_close: Optional[float],
    prev_close: Optional[float] = None,
    action_code: Optional[int] = None,
    ext_z: Optional[float] = None,
    date: str = "",
    held: bool = True,
    atr: Optional[float] = None,
) -> List[RiskEvent]:
    """RISK checks for a held position.

    Decisions are made at the close, so a close below the stop fires "EXIT at open" while an
    intraday touch does not: the plan is a closing decision, and firing on the touch turns every
    shakeout into an exit. A gap THROUGH the stop is a different event and is called out as such,
    because there is no decision left to make.

    `atr` is the ATR at the signal bar, carried through so the alert can say how wide the stop
    actually was. It never changes the decision -- only the explanation.
    """
    events: List[RiskEvent] = []
    ticker = (ticker or "").upper()
    if not ticker or not held:
        return events

    width = None
    if atr and atr > 0 and planned_stop and today_close:
        width = round(abs(today_close - planned_stop) / atr, 2)

    stop = planned_stop
    close = today_close
    if stop and close and stop > 0:
        if close <= stop:
            gapped = bool(prev_close and prev_close <= stop)
            kind = "GAP_THROUGH_STOP" if gapped else "STOP_CLOSE"
            msg = (
                f"{ticker} gapped through stop, exit"
                if gapped else f"{ticker} closed ${close:.2f} below the ${stop:.2f} stop — EXIT at open"
            )
            events.append(RiskEvent(
                ticker, kind, msg, f"RISK:{kind}:{ticker}:{date}",
                {"stop": stop, "close": close, "prev_close": prev_close},
                stop_width_atr=width,
            ))
            return events  # stop breached: extension/trim advice is moot

    in_fade_set = action_code in FADE_SET_CODES
    extended = ext_z is not None and ext_z >= EXT_Z_TRIM
    if in_fade_set or extended:
        why = []
        if in_fade_set:
            why.append(f"action code {action_code} (EXTENDED/STRETCHED/BLOW-OFF)")
        if extended:
            why.append(f"Ext Z {ext_z:.2f} >= {EXT_Z_TRIM}")
        events.append(RiskEvent(
            ticker, "TRIM_EXTENSION",
            f"{ticker} is in the fade set — trim or sell a covered call",
            f"RISK:TRIM_EXTENSION:{ticker}:{date}",
            {"reasons": why, "ext_z": ext_z, "action_code": action_code, "link": "INCOME"},
            stop_width_atr=width,
        ))
    return events


def fire_risk(events: List[RiskEvent]) -> List[str]:
    """Push each RISK event once per (tier, ticker, date)."""
    sent: List[str] = []
    for e in events:
        lines = [e.message, "", f"- kind: `{e.kind}`", _stop_width_line(e)]
        if e.kind == "TRIM_EXTENSION":
            for r in e.detail.get("reasons", []):
                lines.append(f"- reason: {r}")
            lines.append("")
            lines.append("_See the INCOME tier for covered-call strike guidance._")
        if notify("RISK", e.message, "\n".join(lines), dedupe_key=e.dedupe_key):
            sent.append(e.ticker)
    return sent


# --- sweep ------------------------------------------------------------------------------------

def load_onsets_for_date(date: str, base: Optional[Path] = None) -> List[Onset]:
    """Load every persisted Data Window for a date and decode it into Onsets."""
    root = (base or config.BASE_DIR) / "data" / "triage" / date
    onsets: List[Onset] = []
    if not root.exists():
        return onsets
    for dw_path in root.glob("**/*_datawindow.json"):
        try:
            dw = json.loads(dw_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(dw, dict) or "close" not in dw:
            continue
        ticker = str(dw.get("ticker") or dw_path.parent.name).upper()
        onsets.append(onset_from_datawindow(ticker, dw, date))
    return onsets


def run_daily_alert_sweep(
    date: Optional[str] = None,
    notify_enabled: bool = True,
) -> Dict[str, Any]:
    """After-close sweep: ENTRY pushes for PB onsets, RISK pushes, OPS. Digest is separate."""
    date = date or datetime.now().strftime("%Y-%m-%d")
    onsets = load_onsets_for_date(date)

    entry = fire_entry(onsets, date) if notify_enabled else {
        "pushed": [], "digest_candidates": [], "checked": len(onsets),
    }

    # Load held positions from positions.json
    held_tickers = set()
    try:
        from src.tracking.position_state import load_positions
        pos_data = load_positions()
        if isinstance(pos_data, dict):
            open_pos = pos_data.get("open_positions", pos_data)
            if isinstance(open_pos, dict):
                held_tickers = {t.upper() for t in open_pos.keys()}
            elif isinstance(open_pos, list):
                held_tickers = {p.get("ticker", "").upper() for p in open_pos if isinstance(p, dict)}
    except Exception as e:
        logger.warning(f"Failed to load positions for alert sweep: {e}")

    risk_events: List[RiskEvent] = []
    for o in onsets:
        if o.ticker not in held_tickers:
            continue
        risk_events.extend(evaluate_risk(
            ticker=o.ticker,
            planned_stop=o.stop,
            today_close=o.close,
            action_code=o.action_code,
            ext_z=o.ext_z,
            date=date,
            held=True,
            atr=o.atr,
        ))
    risk_sent = fire_risk(risk_events) if notify_enabled else []

    return {
        "date": date,
        "onsets_scanned": len(onsets),
        "entry_pushed": entry["pushed"],
        "entry_digest_candidates": entry["digest_candidates"],
        "risk_pushed": risk_sent,
        "risk_events": [
            {"ticker": e.ticker, "kind": e.kind, "message": e.message} for e in risk_events
        ],
    }