"""Verify every onclick handler referenced in index.html or generated in JS resolves.

The click target can live in two places: `index.html` directly, or inside a template literal in a
JS renderer (the Today desk generates every handler it uses, so nothing about it is in the HTML).
Auditing only the HTML is how `AppDesk.triggerResearch` stayed broken: the button is built in
`desk.js`, threw a TypeError on every click, and the HTML audit never saw it.

Run: python tests/ui_wiring_audit.py
"""

from __future__ import annotations

import glob
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HTML = "web/index.html"
JS_FILES = sorted(glob.glob("web/static/js/*.js"))
JS = "".join(io.open(f, encoding="utf-8").read() for f in JS_FILES)

# Only a handler whose attribute body starts with the call, so `onclick="if (x) { A.b(); }"` is not
# read as a reference to `x.y`. Everything else is a compound statement, not a dispatch target.
_PREFIXED = re.compile(r"onclick=[\"'](?!document\.)(window\.)?([A-Za-z_$][\w$]*)\.([\w$]+)\s*\(")


def _handlers(text: str) -> set[str]:
    out = set()
    for m in _PREFIXED.finditer(text):
        out.add(f"{m.group(2)}.{m.group(3)}")
    return out


def _unresolved(handlers: set[str]) -> list[tuple[str, str]]:
    missing = []
    for h in sorted(handlers):
        obj, _, meth = h.partition(".")
        if f"window.{obj}" not in JS and f"{obj} =" not in JS and f"const {obj}" not in JS:
            missing.append((h, "object not defined"))
            continue
        if not re.search(rf"\b{re.escape(meth)}\s*\(", JS):
            missing.append((h, "method not defined"))
    return missing


html = io.open(HTML, encoding="utf-8").read()
html_handlers = _handlers(html)
js_handlers = set()
for f in JS_FILES:
    js_handlers |= _handlers(io.open(f, encoding="utf-8").read())

html_missing = _unresolved(html_handlers)
print(f"distinct onclick handlers in index.html: {len(html_handlers)}")
if html_missing:
    print("\nUNRESOLVED HANDLERS (HTML):")
    for h, why in html_missing:
        print(f"  {h}  ({why})")

js_only = js_handlers - html_handlers
js_missing = _unresolved(js_only)
print(f"distinct onclick handlers generated inside JS: {len(js_only)}")
if js_missing:
    print("\nUNRESOLVED HANDLERS (generated in JS):")
    for h, why in js_missing:
        print(f"  {h}  ({why})")

if html_missing or js_missing:
    sys.exit(1)
print("all handlers resolve to a defined object + method")

# Every getElementById call should have a matching id somewhere (page or dynamically created).
ids_referenced = set(re.findall(r"getElementById\(['\"]([\w-]+)['\"]\)", JS))
ids_present = set(re.findall(r"id=\"([\w-]+)\"", html)) | set(re.findall(r"id=[\"'`]\$?\{?['\"]?([\w-]+)", JS))
dangling = sorted(i for i in ids_referenced if i not in ids_present)
print(f"\nelement ids referenced by JS: {len(ids_referenced)}")
print(f"ids present in index.html:     {len(ids_present)}")
print(f"referenced but absent (may be created dynamically): {len(dangling)}")
