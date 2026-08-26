"""One-off verification: scrape artifacts for META -> full parse + bit-mask demask.

Run:  python scripts/research/verify_meta_decode.py 2026-08-22
Confirms every Data Window field is present and the bit masks demask correctly
against the Pine's plot definitions.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.logic.data_window_filter import (
    parse_data_window,
    run_data_window_filter,
    deep_research_sort_key,
    _BEAR_MASK_BITS,
    _REV_MASK_BITS,
    _WEAK_MASK_BITS,
)

date = sys.argv[1] if len(sys.argv) > 1 else "2026-08-22"
raw_path = Path(__file__).resolve().parents[2] / "data" / "raw" / date / "META" / "META_datawindow.json"
raw = json.loads(raw_path.read_text())

f = parse_data_window(raw)
v = run_data_window_filter("META", raw)

print("RAW KEYS CAPTURED:", len(raw))
missing = sorted(k for k, vv in f.items() if vv is None)
present = [k for k, vv in f.items() if vv is not None]
print(f"PARSED FIELDS: {len(present)} present / {len(missing)} None")
print()

print("=== CORE FIELDS (the bad_data bug) ===")
for k in ["buy", "sell", "price", "ma50", "ma200", "weinstein", "dir_prob", "stage", "regime", "ext_pct", "exhaustion", "rev_l", "rev_s"]:
    print(f"  {k:14} = {f.get(k)}")
print()

print("=== VERDICT ===")
for k in ["triage", "reason", "chosen_side", "mode", "conviction", "ev_r", "win_prob", "rr", "action", "in_zone", "no_fresh_long"]:
    print(f"  {k:14} = {v.get(k)}")
print()


def show(name, val, bits):
    iv = int(round(val)) if val is not None else 0
    dec = [n for b, n in sorted(bits.items()) if iv & b]
    print(f"  {name:24} raw={str(val):>10}  bits={dec if dec else '(none)'}")


print("=== BIT-MASK DEMASK (raw -> decoded) ===")
show("Zone RR Flags", f.get("zone_rr_flags"), {1: "long_in_zone", 2: "short_in_zone", 4: "long_rr_valid", 8: "short_rr_valid"})
show("Signal Pack", f.get("signal_pack"), {1: "strongBuy", 2: "strongSell", 4: "NOT-fade", 8: "isTopping", 16: "isBottoming"})
show("Bear Warning Mask", f.get("bear_mask"), _BEAR_MASK_BITS)
show("Reversal Pattern Mask", f.get("rev_mask"), _REV_MASK_BITS)
show("Weak Level Mask", f.get("weak_mask"), _WEAK_MASK_BITS)
fl = f.get("fade_long")
print(f"  {'fade_long':24} raw={str(fl):>10}  -> " + ("FADE OFF" if fl == 0 else ("FADE ON" if fl == 1 else "UNKNOWN")))
print(f"  {'long_in_zone':24} = {f.get('long_in_zone')}   short_in_zone = {f.get('short_in_zone')}")
print()

print("=== PREMOVE PACK bits ===")
pre = int(round(f.get("premove_pack") or 0))
print(f"  raw = {f.get('premove_pack')}")
print(f"  darvas state (v%8)              = {pre % 8}")
print(f"  darvas box quality ((//8)%8)*20 = {((pre // 8) % 8) * 20}")
print(f"  squeeze release dir ((//64)%4)-1 = {((pre // 64) % 4) - 1}")
for bit, label in [
    (256, "RS Leader"), (512, "isAccelerating"), (1024, "marketBullish"),
    (2048, "isPowerBreakout"), (4096, "adLineBullish"), (8192, "bullFlag"),
    (16384, "impulseGreen"), (32768, "isNear52WHigh"), (65536, "breadthBull"),
]:
    if pre & bit:
        print(f"  {label}: SET")
print()
print("sort_key:", deep_research_sort_key(v))
