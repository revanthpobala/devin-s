import sys
import numpy as np
from pathlib import Path
from PIL import Image

def candle_column_profile(path):
    im = Image.open(path).convert("RGB")
    a = np.asarray(im)
    gray = a.mean(axis=2).astype(int)
    # background = mode luminance
    mode = int(np.argmax(np.bincount(gray.ravel())))
    # per-column fraction of pixels differing strongly from bg = "ink" (candles/grid/text)
    col_ink = ((np.abs(gray - mode) > 25).mean(axis=0))
    w = len(col_ink)
    # split into left/center/right thirds
    t = w // 3
    left = col_ink[:t].mean()
    mid = col_ink[t:2*t].mean()
    right = col_ink[2*t:].mean()
    peak = int(np.argmax(col_ink))
    print(f"{Path(path).name}: ink% left={left:.3f} mid={mid:.3f} right={right:.3f}  peak_col={peak}/{w}")
    return col_ink

if __name__ == "__main__":
    paths = sys.argv[1:]
    profiles = [candle_column_profile(p) for p in paths]
    if len(profiles) == 2:
        diff = np.abs(profiles[0] - profiles[1]).mean()
        print(f"mean column-ink difference between the two: {diff:.4f}  (0=identical view, >0.01=visibly different)")
