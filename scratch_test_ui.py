import re

def test_markup():
    # 1. Test research.html has tab-pnl-btn next to tab-dp-btn
    with open("web/research.html", "r", encoding="utf-8") as f:
        html = f.read()
    assert 'id="tab-dp-btn"' in html
    assert 'id="tab-pnl-btn"' in html
    # Check tab-pnl-btn comes right after tab-dp-btn
    dp_pos = html.find('id="tab-dp-btn"')
    pnl_pos = html.find('id="tab-pnl-btn"')
    assert 0 < pnl_pos - dp_pos < 180, "tab-pnl-btn should be right next to tab-dp-btn in research.html"
    assert "function renderPnlMapTab" in html
    assert "switchTab('pnl')" in html
    assert "binding-levels-collapsible" in html

    # 2. Test swing.js has pnl in switchDossierTab
    with open("web/static/js/swing.js", "r", encoding="utf-8") as f:
        swing_js = f.read()
    assert "'pnl'" in swing_js
    assert "tab === 'pnl'" in swing_js
    assert "TradeLevelGraph.renderLevelGraphHtml" in swing_js

    # 3. Test utils.js has collapsible binding levels
    with open("web/static/js/utils.js", "r", encoding="utf-8") as f:
        utils_js = f.read()
    assert "binding-levels-collapsible" in utils_js
    assert "<!--\\s*binding-levels\\s*-->" in utils_js or "binding-levels" in utils_js

    # 4. Test modal HTML files have tab-pnl-btn next to tab-dp-btn
    modal_files = [
        "web/today.html", "web/watchlist.html", "web/trades.html",
        "web/radar.html", "web/portfolio.html", "web/logs.html",
        "web/journal.html", "web/intraday.html", "web/index.html", "web/alerts.html"
    ]
    for mf in modal_files:
        with open(mf, "r", encoding="utf-8") as f:
            content = f.read()
        assert 'id="tab-dp-btn"' in content, f"Missing tab-dp-btn in {mf}"
        assert 'id="tab-pnl-btn"' in content, f"Missing tab-pnl-btn in {mf}"
        dp_p = content.find('id="tab-dp-btn"')
        pnl_p = content.find('id="tab-pnl-btn"')
        assert 0 < pnl_p - dp_p < 180, f"tab-pnl-btn not right next to tab-dp-btn in {mf}"

    print("ALL 4 CHECKS PASSED PERFECTLY!")

if __name__ == "__main__":
    test_markup()
