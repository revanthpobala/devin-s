import sys
from pathlib import Path
from PIL import Image
import numpy as np

def analyze(path):
    im = Image.open(path).convert("RGB")
    a = np.asarray(im)
    h, w, _ = a.shape
    # background in TradingView charts is near-black (#131722) or white for some themes
    # measure how much of the image is "non-background" (variation)
    # Use luminance std per row/col to find where content (candles/UI) is.
    gray = a.mean(axis=2)
    row_std = gray.std(axis=1)
    col_std = gray.std(axis=0)
    # content rows = std above threshold
    content_rows = np.where(row_std > 8)[0]
    content_cols = np.where(col_std > 8)[0]
    def span(x):
        return (int(x[0]), int(x[-1]), len(x)) if len(x) else (None, None, 0)
    r0, r1, rn = span(content_rows)
    c0, c1, cn = span(content_cols)
    # overall fraction of pixels that differ from the mode color (background)
    hist = np.bincount(gray.ravel().astype(int), minlength=256)
    mode_color = int(np.argmax(hist))
    diff = np.abs(gray - mode_color).mean()
    print(f"{Path(path).name}: size={w}x{h} bg_lum={mode_color} mean_abs_diff={diff:.1f}")
    print(f"   content_rows: {r0}..{r1} (of {h})  content_cols: {c0}..{c1} (of {w})")
    print(f"   non_bg_fraction: {diff/255:.2%}")

if __name__ == "__main__":
    for f in sys.argv[1:]:
        analyze(f)
