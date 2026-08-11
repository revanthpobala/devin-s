"""Inference logic for historical state responses (edge vs baseline).
Returns None on any missing artifact, import failure, or if disabled.
"""

import json
import logging
import os
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

try:
    from src import config
    _DEFAULT_MODEL_DIR = str(config.BASE_DIR / "data" / "models")
except Exception:
    _DEFAULT_MODEL_DIR = os.path.join("data", "models")

MODEL_DIR = os.getenv("RESPONSE_MODEL_DIR", _DEFAULT_MODEL_DIR)
ENABLED = os.getenv("RESPONSE_MODEL_ENABLED", "1").lower() not in ("0", "false", "no")

_buckets = None
_load_failed = False

def _load():
    global _buckets, _load_failed
    if _buckets is not None or _load_failed:
        return
    try:
        mp = os.path.join(MODEL_DIR, "state_response.json")
        with open(mp, "r", encoding="utf-8") as fh:
            _buckets = json.load(fh).get("buckets", {})
        logger.info(f"response_model loaded: {len(_buckets)} buckets from {mp}")
    except Exception as e:
        _load_failed = True
        logger.warning(f"response_model unavailable: {e}")

def bucket_for(f: Dict[str, Optional[float]]) -> str:
    """Classifies a data window dict into its highest-priority historical state bucket."""
    close = f.get("price")
    ma200 = f.get("ma200")
    buy = f.get("buy")
    rvol = f.get("rvol")
    rev = f.get("rev_l")
    ext = f.get("ext_pct")
    stage = f.get("stage")
    actL = f.get("action_long")
    
    mask = int(f.get("rev_mask") or 0)
    oops = bool(mask & 1024)
    
    reversal = (
        close is not None and ma200 is not None and close < ma200
        and (buy or 99) < 30 and (rvol or 0) > 1.5 and (rev or 0) >= 7
    )
    
    if reversal and (ext or 0) <= -8 and oops: return "reversal_deep_oops"
    if reversal and (ext or 0) <= -8:        return "reversal_deep"
    if reversal and oops:                    return "reversal_oops_any"
    if reversal:                             return "reversal"
    if ext is not None and 25 <= ext < 60:   return "ext_excl_25_60"
    if (buy or 0) >= 85 and round(stage or 0) == 2: return "prime_stage2"
    if (buy or 0) >= 90:                     return "strong_buy_mom"
    if round(actL or 0) == 8:                return "watch_code8"
    return "baseline_all"

def state_response(f: Dict[str, Optional[float]]) -> Optional[Dict[str, Any]]:
    """Returns the historical edge/reliability record for the given data window."""
    if not ENABLED:
        return None
    _load()
    if not _buckets:
        return None
        
    bucket_name = bucket_for(f)
    record = _buckets.get(bucket_name)
    if record:
        record = dict(record)
        record["bucket"] = bucket_name
        return record
    return None
