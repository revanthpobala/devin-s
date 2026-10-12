"""Verify every onclick handler referenced in index.html exists in the JS sources."""
from __future__ import annotations

import glob
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HTML = "web/index.html"
JS = "".join(io.open(f, encoding="utf-8").read() for f in sorted(glob.glob("web/static/js/*.js")))

html = io.open(HTML, encoding="utf-8").read()

# onclick="AppDesk.setTodaySubTab('radar')" / onclick="AppChat.askRevChat('...$')"
# Only namespaced Foo.bar( handlers; bare calls like document.getElementById( are not dispatch targets.
handlers = re.findall(r"onclick=\"((?!document\.)[A-Za-z_$][\w$]*\.[\w$]+)\s*\(", html)
uniq = sorted(set(handlers))

missing = []
for h in uniq:
    obj, _, meth = h.partition(".")
    if f"window.{obj}" not in JS and f"{obj} =" not in JS and f"const {obj}" not in JS:
        missing.append((h, "object not defined"))
        continue
    if not re.search(rf"\b{re.escape(meth)}\s*\(", JS):
        missing.append((h, "method not defined"))

print(f"distinct onclick handlers in index.html: {len(uniq)}")
if missing:
    print("\nUNRESOLVED HANDLERS:")
    for h, why in missing:
        print(f"  {h}  ({why})")
    sys.exit(1)
print("all handlers resolve to a defined object + method")

# Every getElementById call should have a matching id somewhere (page or dynamically created).
ids_referenced = set(re.findall(r"getElementById\(['\"]([\w-]+)['\"]\)", JS))
ids_present = set(re.findall(r"id=\"([\w-]+)\"", html))
dangling = sorted(i for i in ids_referenced if i not in ids_present)
print(f"\nelement ids referenced by JS: {len(ids_referenced)}")
print(f"ids present in index.html:     {len(ids_present)}")
print(f"referenced but absent (may be created dynamically): {len(dangling)}")
