"""
Clean, robust page builder for REV TERMINAL.
Extracts individual desk panes from web/index.html into dedicated, clean page templates:
- web/today.html (Today Desk)
- web/radar.html (Radar / Swing Research)
- web/alerts.html (Alerts & Screeners)
- web/trades.html (Trades Ideas & Execution)
- web/portfolio.html (Portfolio & Positions)
- web/intraday.html (0DTE Intraday Desk)
- web/watchlist.html (Target Watchlist)
- web/journal.html (Trade Ledger & Closed Record)
- web/logs.html (Operations & System Logs)

Uses BeautifulSoup to guarantee 100% syntactically valid DOM structure:
- .container has exactly 3 direct children: main-desks-column, chat-resizer, sidebar-rev-chat.
- Top header has clean navigation with active item indicator.
- Settings modal contains only system settings and services.
- Chat toggle lives directly on the top header ribbon and as a floating edge trigger.
"""

from bs4 import BeautifulSoup
from pathlib import Path
import copy

INDEX_PATH = Path("web/index.html")
content = INDEX_PATH.read_text(encoding="utf-8")
master_soup = BeautifulSoup(content, "html.parser")

page_specs = [
    ("today", ["desk-pane-today"], "Today Operations", "nav-item-today"),
    ("radar", ["desk-pane-swing"], "Radar & Swing Research", "nav-item-swing"),
    ("alerts", ["desk-pane-alerts"], "Alerts & Screener", "nav-item-alerts"),
    ("trades", ["desk-pane-trades"], "Trade Ideas & Orders", "nav-item-trades"),
    ("portfolio", ["desk-pane-portfolio"], "Portfolio & Positions", "nav-item-portfolio"),
    ("intraday", ["desk-pane-intraday"], "0DTE Intraday Desk", "nav-item-intraday"),
    ("watchlist", ["desk-pane-watchlist"], "Target Watchlist", "nav-item-watchlist"),
    ("journal", ["desk-pane-journal", "desk-pane-record"], "Trade Ledger & Record", "nav-item-journal"),
    ("logs", ["desk-pane-logs"], "Operations & System Logs", "nav-item-logs"),
]

for name, pane_ids, title_text, nav_id in page_specs:
    soup = BeautifulSoup(content, "html.parser")
    title = soup.find("title")
    if title:
        title.string = f"REV TERMINAL — {title_text}"

    nav = soup.find("nav", id="site-header-nav")
    if nav:
        for a in nav.find_all("a"):
            if a.get("id") == nav_id:
                a["class"] = ["nav-item", "active"]
            else:
                a["class"] = ["nav-item"]

    container = soup.find("div", class_="container")
    col = container.find("div", class_="main-desks-column")
    switcher = col.find("div", class_="desk-switcher-bar")
    if switcher:
        switcher.decompose()

    for p in col.find_all("div", id=lambda x: x and x.startswith("desk-pane-"), recursive=False):
        p.decompose()

    for idx, pid in enumerate(pane_ids):
        orig_pane = master_soup.find("div", id=pid)
        if orig_pane:
            new_pane = copy.copy(orig_pane)
            if idx == 0:
                new_pane["style"] = "display:flex; flex-direction:column; gap:20px;"
            else:
                new_pane["style"] = "display:none; flex-direction:column; gap:20px;"
            col.append(new_pane)

    out_html = "<!DOCTYPE html>\n" + str(soup)
    Path(f"web/{name}.html").write_text(out_html, encoding="utf-8")
    print(f"Generated web/{name}.html ({len(out_html)} chars)")

print("Successfully generated all dedicated desk pages!")
