/**
 * TradingView Live Chart Hover Engine
 * Universally provides instant, interactive TradingView chart previews on hovering any stock ticker symbol across the entire application.
 */
(function() {
  'use strict';

  // Comprehensive set of non-ticker words & UI labels to avoid false positives
  const RESERVED_WORDS = new Set([
    'PASS', 'WAIT', 'CUT', 'LONG', 'SHORT', 'BUY', 'SELL', 'EXIT', 'ENTER',
    'DATE', 'TIME', 'PRICE', 'ACTION', 'SCORE', 'GRADE', 'INFO', 'AI', 'CLOSE',
    'TOTAL', 'ACTIVE', 'IN_ZONE', 'STALKING', 'STOP', 'TARGET', 'STATUS',
    'OPEN', 'TYPE', 'SIDE', 'ENTRY', 'SPOT', 'PNL', 'HIGH', 'LOW', 'VOLUME',
    'INSPECT', 'QUERY', 'RESTORE', 'DETAILS', 'DISMISS', 'VIEW', 'LOGS',
    'RADAR', 'SCREEN', 'TRADES', 'SWING', 'INTRADAY', 'PORT', 'LIVE', 'SYNC',
    'CHART', 'EXP', 'STRIKE', 'DELTA', 'GAMMA', 'THETA', 'VEGA', 'CALL', 'PUT',
    'BEAR', 'BULL', 'HOLD', 'SETUP', 'BIAS', 'NOTE', 'DESC', 'RISK', 'PROFIT',
    'LOSS', 'FEES', 'NET', 'MAX', 'MIN', 'AVG', 'LAST', 'BID', 'ASK', 'SIZE',
    'VOL', 'IV', 'HV', 'HV30', 'ATR', 'RSI', 'EMA', 'SMA', 'MACD', 'BB',
    'ALL', 'AUTO', 'BATS', 'NYSE', 'AMEX', 'CBOE', 'EDIT', 'DONE', 'SAVE',
    'BACK', 'NEXT', 'PREV', 'HOME', 'PAGE', 'HELP', 'TEST', 'RUN', 'TASK',
    'MODE', 'NEW', 'HOT', 'COLD', 'TRUE', 'FALSE', 'NULL', 'NONE', 'NAN',
    'DAY', 'WEEK', 'YEAR', '1D', '5M', '15M', '1H', '4H', 'TODAY', 'RTH',
    'ETH', 'USD', 'USDT', 'BTC', 'API', 'URL', 'APP', 'OK', 'NO', 'YES',
    'THE', 'FOR', 'AND', 'NOR', 'BUT', 'YET', 'SO', 'AT', 'BY', 'IN', 'OF', 'ON', 'TO', 'UP',
    'TAB', 'BTN', 'DESK', 'ROWS', 'ITEM', 'TAG', 'USER', 'BOT', 'CHAT', 'DOC', 'FILE', 'NAME',
    'EXEC', 'ZONE', 'TIER', 'CODE', 'LOAD', 'POST', 'SEND', 'REFRESH',
    'COPY', 'DATA', 'BASE', 'MENU', 'CONF', 'SET', 'FIND', 'LIST', 'SHOW', 'HIDE', 'SORT',
    'CASH', 'COST', 'GAIN', 'DRAW', 'STAT', 'TERM', 'SEEK', 'RANK', 'RATE',
    'ZERO', 'PEAK', 'DIP', 'GAP', 'ALERT', 'PUSH', 'RESET', 'CLEAR', 'APPLY'
  ]);

  window.AppTvHover = {
    _cardEl: null,
    _iframeEl: null,
    _loadingEl: null,
    _currentSym: '',
    _currentExchange: 'AUTO',
    _currentInterval: 'D',
    _currentTarget: null,
    _hoverTimer: null,
    _hideTimer: null,
    _isCardHovered: false,
    _isTargetHovered: false,
    _initialized: false,

    init() {
      if (this._initialized) return;
      this._initialized = true;

      try {
        const saved = localStorage.getItem('tv_hover_exchange');
        if (saved && saved !== 'BATS') {
          this._currentExchange = saved;
        } else {
          this._currentExchange = 'AUTO';
          localStorage.setItem('tv_hover_exchange', 'AUTO');
        }
      } catch (e) {
        this._currentExchange = 'AUTO';
      }

      this._injectStyles();
      this._createCardElement();
      this._bindGlobalEvents();
    },

    _injectStyles() {
      if (document.getElementById('tv-hover-engine-styles')) return;
      const style = document.createElement('style');
      style.id = 'tv-hover-engine-styles';
      style.textContent = `
        .tv-hover-card {
          position: fixed !important;
          z-index: 99999 !important;
          width: 580px;
          height: 400px;
          background: rgba(15, 23, 42, 0.98);
          backdrop-filter: blur(18px);
          -webkit-backdrop-filter: blur(18px);
          border: 1px solid rgba(6, 182, 212, 0.5);
          border-radius: 10px;
          box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.9), 0 0 24px rgba(6, 182, 212, 0.3);
          display: flex;
          flex-direction: column;
          overflow: hidden;
          pointer-events: auto;
          opacity: 0;
          transform: translateY(6px) scale(0.98);
          transition: opacity 0.16s cubic-bezier(0.16, 1, 0.3, 1), transform 0.16s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .tv-hover-card.visible {
          opacity: 1;
          transform: translateY(0) scale(1);
        }
        .tv-hover-header {
          padding: 8px 12px;
          background: rgba(30, 41, 59, 0.9);
          border-bottom: 1px solid rgba(51, 65, 85, 0.6);
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
          user-select: none;
          flex-shrink: 0;
        }
        .tv-hover-title-group {
          display: flex;
          align-items: center;
          gap: 8px;
          overflow: hidden;
          min-width: 0;
        }
        .tv-hover-sym {
          font-family: 'JetBrains Mono', monospace;
          font-size: 15px;
          font-weight: 800;
          color: #38bdf8;
          letter-spacing: 0.5px;
        }
        .tv-hover-name {
          font-size: 11px;
          font-weight: 600;
          color: #94a3b8;
          max-width: 140px;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .tv-hover-price {
          display: inline-flex;
          align-items: center;
          margin-left: 2px;
        }
        .tv-hover-live-pill {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          padding: 2px 6px;
          border-radius: 4px;
          background: rgba(16, 185, 129, 0.12);
          border: 1px solid rgba(16, 185, 129, 0.3);
          font-family: 'JetBrains Mono', monospace;
          font-size: 10.5px;
          font-weight: 700;
          color: #34d399;
          white-space: nowrap;
        }
        .tv-hover-live-pill .dot.live-pulse {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: #10b981;
          box-shadow: 0 0 6px #10b981;
          animation: tvLivePulse 1.8s infinite ease-in-out;
        }
        @keyframes tvLivePulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.35; transform: scale(0.75); }
        }
        .tv-hover-actions {
          display: flex;
          align-items: center;
          gap: 6px;
          flex-shrink: 0;
        }
        .tv-hover-exchange-select {
          background: rgba(15, 23, 42, 0.7);
          border: 1px solid rgba(51, 65, 85, 0.7);
          color: #cbd5e1;
          font-family: 'JetBrains Mono', monospace;
          font-size: 10.5px;
          font-weight: 700;
          border-radius: 4px;
          padding: 2px 4px;
          cursor: pointer;
          outline: none;
        }
        .tv-hover-exchange-select:hover, .tv-hover-exchange-select:focus {
          border-color: #38bdf8;
          color: #38bdf8;
        }
        .tv-hover-exchange-select option {
          background: #0f172a;
          color: #f1f5f9;
        }
        .tv-hover-intervals {
          display: flex;
          align-items: center;
          background: rgba(15, 23, 42, 0.6);
          border: 1px solid rgba(51, 65, 85, 0.6);
          border-radius: 4px;
          padding: 1px;
        }
        .tv-hover-int-btn {
          border: none;
          background: transparent;
          color: #94a3b8;
          font-family: 'JetBrains Mono', monospace;
          font-size: 10px;
          font-weight: 700;
          padding: 2px 5px;
          border-radius: 3px;
          cursor: pointer;
          transition: all 0.12s ease;
        }
        .tv-hover-int-btn:hover {
          color: #f1f5f9;
          background: rgba(255, 255, 255, 0.08);
        }
        .tv-hover-int-btn.active {
          color: #0f172a;
          background: #38bdf8;
          font-weight: 800;
        }
        .tv-hover-btn-action {
          display: inline-flex;
          align-items: center;
          gap: 3px;
          padding: 2px 7px;
          background: rgba(255, 255, 255, 0.06);
          border: 1px solid rgba(51, 65, 85, 0.7);
          border-radius: 4px;
          color: #e2e8f0;
          font-size: 10.5px;
          font-weight: 700;
          text-decoration: none;
          cursor: pointer;
          transition: all 0.12s ease;
        }
        .tv-hover-btn-action:hover {
          background: rgba(6, 182, 212, 0.2);
          border-color: #38bdf8;
          color: #38bdf8;
        }
        .tv-hover-chart-body {
          position: relative;
          flex: 1;
          background: #0f172a;
          overflow: hidden;
        }
        .tv-hover-iframe {
          width: 100%;
          height: 100%;
          border: none;
          display: block;
        }
        .tv-hover-loading {
          position: absolute;
          inset: 0;
          background: rgba(15, 23, 42, 0.9);
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 8px;
          color: #38bdf8;
          font-family: 'JetBrains Mono', monospace;
          font-size: 11px;
          font-weight: 600;
          pointer-events: none;
          transition: opacity 0.2s ease;
          z-index: 5;
        }
        .tv-hover-loading .dot.pulse {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          background: #38bdf8;
          animation: tvLivePulse 1.2s infinite ease-in-out;
        }
      `;
      document.head.appendChild(style);
    },

    _createCardElement() {
      let card = document.getElementById('tv-hover-card');
      if (!card) {
        card = document.createElement('div');
        card.id = 'tv-hover-card';
        card.className = 'tv-hover-card';
        card.style.display = 'none';
        card.innerHTML = `
          <div class="tv-hover-header">
            <div class="tv-hover-title-group">
              <span class="tv-hover-sym" id="tv-hover-sym">$SPY</span>
              <span class="tv-hover-name" id="tv-hover-name">Market</span>
              <span class="tv-hover-price" id="tv-hover-price" style="display:none;"></span>
            </div>
            <div class="tv-hover-actions">
              <select id="tv-hover-exchange" class="tv-hover-exchange-select" onchange="AppTvHover.setExchange(this.value)" title="Exchange routing prefix (saved in settings)">
                <option value="AUTO" selected>AUTO</option>
                <option value="NASDAQ">NASDAQ</option>
                <option value="NYSE">NYSE</option>
                <option value="AMEX">AMEX</option>
                <option value="BATS">BATS</option>
              </select>
              <div class="tv-hover-intervals">
                <button class="tv-hover-int-btn" data-int="5" onclick="AppTvHover.setInterval('5')">5m</button>
                <button class="tv-hover-int-btn" data-int="15" onclick="AppTvHover.setInterval('15')">15m</button>
                <button class="tv-hover-int-btn" data-int="60" onclick="AppTvHover.setInterval('60')">1h</button>
                <button class="tv-hover-int-btn active" data-int="D" onclick="AppTvHover.setInterval('D')">1D</button>
              </div>
              <button class="tv-hover-btn-action" onclick="AppTvHover.openFullModal()" title="Expand to Full Interactive Modal">⛶ Full</button>
              <a class="tv-hover-btn-action" id="tv-hover-link-ext" target="_blank" href="#" title="Open directly in TradingView">↗</a>
            </div>
          </div>
          <div class="tv-hover-chart-body">
            <div class="tv-hover-loading" id="tv-hover-loading">
              <span class="dot pulse"></span>
              <span>Loading Live Chart...</span>
            </div>
            <iframe id="tv-hover-iframe" class="tv-hover-iframe" frameborder="0" scrolling="no" allowtransparency="true"></iframe>
          </div>
        `;
        document.body.appendChild(card);
      }

      this._cardEl = card;
      this._iframeEl = document.getElementById('tv-hover-iframe');
      this._loadingEl = document.getElementById('tv-hover-loading');

      if (this._iframeEl) {
        this._iframeEl.addEventListener('load', () => {
          if (this._loadingEl) this._loadingEl.style.opacity = '0';
        });
      }

      card.addEventListener('mouseenter', () => {
        this._isCardHovered = true;
        clearTimeout(this._hideTimer);
      });

      card.addEventListener('mouseleave', () => {
        this._isCardHovered = false;
        this._scheduleHide(200);
      });
    },

    _bindGlobalEvents() {
      // Global delegated mouseover listener
      document.addEventListener('mouseover', (e) => {
        const symbolInfo = this._resolveSymbolFromEvent(e);
        if (!symbolInfo) return;

        const { sym, el } = symbolInfo;

        // If mouse is already inside the active element and showing this symbol, keep alive
        if (this._currentTarget === el && this._cardEl && this._cardEl.classList.contains('visible') && this._currentSym === sym) {
          this._isTargetHovered = true;
          clearTimeout(this._hideTimer);
          return;
        }

        this._isTargetHovered = true;
        clearTimeout(this._hideTimer);

        if (this._currentSym === sym && this._cardEl && this._cardEl.classList.contains('visible')) {
          this._currentTarget = el;
          return;
        }

        clearTimeout(this._hoverTimer);
        // Snappy dwell time (140ms): instant response when pausing on a ticker, avoids quick swipe triggers
        this._hoverTimer = setTimeout(() => {
          if (this._isTargetHovered) {
            this.show(el, sym);
          }
        }, 140);
      }, true);

      // Global mouseout listener
      document.addEventListener('mouseout', (e) => {
        const symbolInfo = this._resolveSymbolFromEvent(e);
        if (!symbolInfo) return;

        // CRITICAL ANTI-FLICKER: If moving to a child of the same element or into the hover card, do NOT cancel
        const related = e.relatedTarget;
        if (related) {
          if (symbolInfo.el.contains(related)) {
            return;
          }
          if (this._cardEl && this._cardEl.contains(related)) {
            return;
          }
        }

        this._isTargetHovered = false;
        clearTimeout(this._hoverTimer);
        this._scheduleHide(220);
      }, true);

      // Close on Escape or click outside
      window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') this.hide();
      });

      document.addEventListener('mousedown', (e) => {
        if (this._cardEl && this._cardEl.style.display !== 'none') {
          if (!this._cardEl.contains(e.target) && (!this._currentTarget || !this._currentTarget.contains(e.target))) {
            this.hide();
          }
        }
      });
    },

    _resolveSymbolFromEvent(e) {
      const target = e.target;
      if (!target) return null;

      // Priority 1: Explicit data attributes on target or ancestors
      const dataEl = target.closest('[data-ticker], [data-symbol], [data-tv-symbol], [data-sym], [data-target-ticker], [data-ticker-sym]');
      if (dataEl) {
        let raw = dataEl.getAttribute('data-ticker') ||
                  dataEl.getAttribute('data-symbol') ||
                  dataEl.getAttribute('data-tv-symbol') ||
                  dataEl.getAttribute('data-sym') ||
                  dataEl.getAttribute('data-target-ticker') ||
                  dataEl.getAttribute('data-ticker-sym');
        const sym = this._cleanTicker(raw, true);
        if (sym) return { sym, el: dataEl };
      }

      // Priority 2: Dedicated ticker classes
      const classEl = target.closest('.ticker-pill-btn, .ticker-cell-sym, .radar-ticker-sym, .target-sym-text, .ticker-with-tooltip, .ticker-table-card, .tv-symbol-hover, .ticker-badge, .opp-ticker, .ticker-tag, .asset-ticker, .hero-ticker, .ticker-symbol, .ticker-hero-sym, .ticker-name, .stock-sym, .opp-head-ticker');
      if (classEl) {
        let sym = this._cleanTicker(classEl.getAttribute('data-ticker') || classEl.textContent, true);
        if (sym) return { sym, el: classEl };
      }

      // Priority 3: Links to research (/research/XYZ)
      const linkEl = target.closest('a[href*="/research/"]');
      if (linkEl) {
        const href = linkEl.getAttribute('href') || '';
        const m = href.match(/\/research\/([A-Za-z0-9_-]+)/);
        if (m && m[1]) {
          const sym = this._cleanTicker(m[1], true);
          if (sym) return { sym, el: linkEl };
        }
      }

      // Priority 4: Exact cashtag: $TICKER
      const inlineEl = target.closest('span, strong, b, a, button, em, code, mark, h1, h2, h3, h4, td, div.badge, div.tag');
      if (inlineEl) {
        const txt = (inlineEl.textContent || '').trim();
        const cashMatch = txt.match(/^\$([A-Z]{1,6})$/i);
        if (cashMatch) {
          const sym = this._cleanTicker(cashMatch[1], true);
          if (sym) return { sym, el: inlineEl };
        }
      }

      // Priority 5: Table cell inside column labeled Symbol/Ticker/Underlying/Asset
      const td = target.closest('td');
      if (td && td.parentElement) {
        const tr = td.parentElement;
        const cellIndex = Array.prototype.indexOf.call(tr.children, td);
        const table = tr.closest('table');
        if (table) {
          const th = table.querySelector(`thead tr th:nth-child(${cellIndex + 1})`);
          if (th) {
            const thText = (th.textContent || '').toUpperCase();
            if (thText.includes('SYMBOL') || thText.includes('TICKER') || thText.includes('UNDERLYING') || thText.includes('ASSET') || thText.includes('SYM')) {
              const sym = this._cleanTicker(td.getAttribute('data-ticker') || td.textContent, true);
              if (sym) return { sym, el: td };
            }
          }
        }
      }

      // Priority 6: Inline element whose trimmed text is 1-5 letters in uppercase
      if (inlineEl) {
        const txt = (inlineEl.textContent || '').trim();
        if (/^\$?[A-Z]{1,5}$/.test(txt)) {
          const isCash = txt.startsWith('$');
          const sym = this._cleanTicker(txt, isCash);
          if (sym) return { sym, el: inlineEl };
        }
      }

      // Priority 7: Caret range word extraction from text node
      if (document.caretRangeFromPoint && e.clientX && e.clientY) {
        try {
          const range = document.caretRangeFromPoint(e.clientX, e.clientY);
          if (range && range.startContainer && range.startContainer.nodeType === Node.TEXT_NODE) {
            const nodeText = range.startContainer.textContent;
            const offset = range.startOffset;
            let start = offset;
            while (start > 0 && /[A-Za-z0-9$]/.test(nodeText[start - 1])) start--;
            let end = offset;
            while (end < nodeText.length && /[A-Za-z0-9$]/.test(nodeText[end])) end++;
            const word = nodeText.slice(start, end).trim();
            if (word.startsWith('$') && word.length >= 2 && word.length <= 7) {
              const sym = this._cleanTicker(word, true);
              if (sym) {
                const parentEl = range.startContainer.parentElement || target;
                return { sym, el: parentEl };
              }
            }
          }
        } catch (err) {}
      }

      return null;
    },

    _cleanTicker(txt, isCashtag = false) {
      if (!txt) return null;
      const clean = String(txt).replace(/^\$/, '').replace(/🔍/g, '').replace(/[^A-Z]/gi, '').toUpperCase().trim();
      if (!clean) return null;

      // 1-letter tickers (e.g. C, F, T, V) require cashtag or explicit attribute to avoid random letters
      if (clean.length === 1) {
        return isCashtag ? clean : null;
      }

      if (clean.length >= 2 && clean.length <= 5) {
        if (!RESERVED_WORDS.has(clean) || (isCashtag && ['RUN', 'ALL', 'NEW', 'NOW', 'OUT', 'NET'].includes(clean))) {
          return clean;
        }
      }
      return null;
    },

    _scheduleHide(delay = 200) {
      clearTimeout(this._hideTimer);
      this._hideTimer = setTimeout(() => {
        if (!this._isCardHovered && !this._isTargetHovered) {
          this.hide();
        }
      }, delay);
    },

    _getPrefixedSymbol(sym) {
      if (!sym) return '';
      if (sym.includes(':')) return sym;
      const exch = (this._currentExchange || 'AUTO').trim().toUpperCase();
      if (!exch || exch === 'AUTO') {
        return sym;
      }
      return `${exch}:${sym}`;
    },

    setExchange(exchange, updateFrame = true) {
      this._currentExchange = (exchange || 'AUTO').trim().toUpperCase();
      try {
        localStorage.setItem('tv_hover_exchange', this._currentExchange);
      } catch (e) {}

      const selectEl = document.getElementById('tv-hover-exchange');
      if (selectEl && selectEl.value !== this._currentExchange) {
        selectEl.value = this._currentExchange;
      }

      if (this._currentSym) {
        const fullSym = this._getPrefixedSymbol(this._currentSym);
        const extLink = document.getElementById('tv-hover-link-ext');
        if (extLink) {
          extLink.href = `https://www.tradingview.com/chart/jPAQSlZC/?symbol=${encodeURIComponent(fullSym)}&interval=${this._currentInterval || 'D'}`;
        }
        if (updateFrame) {
          this._loadChartFrame(this._currentSym, this._currentInterval || 'D');
        }
      }
    },

    show(targetEl, sym) {
      if (!this._cardEl) this._createCardElement();
      this._currentTarget = targetEl;
      this._currentSym = sym;

      // Restore saved exchange preference
      try {
        const savedExch = localStorage.getItem('tv_hover_exchange');
        if (savedExch && savedExch !== 'BATS') {
          this._currentExchange = savedExch;
        } else {
          this._currentExchange = 'AUTO';
        }
      } catch (e) {
        this._currentExchange = 'AUTO';
      }

      const selectEl = document.getElementById('tv-hover-exchange');
      if (selectEl) {
        selectEl.value = this._currentExchange || 'AUTO';
      }

      // Default timeframe
      const isIntradayDesk = (window.AppState && window.AppState.currentDesk === 'intraday');
      let defaultInterval = isIntradayDesk ? '15' : 'D';

      // Update Title & Company Info
      const symEl = document.getElementById('tv-hover-sym');
      const nameEl = document.getElementById('tv-hover-name');
      const priceEl = document.getElementById('tv-hover-price');

      if (symEl) symEl.textContent = `$${sym}`;
      if (nameEl) {
        const comp = this._getCompanyName(sym);
        nameEl.textContent = comp || 'Equities';
        nameEl.title = comp || sym;
      }

      // Live spot price if cached
      if (priceEl) {
        priceEl.style.display = 'none';
        this._loadSpotPrice(sym, priceEl);
      }

      // External link with current exchange prefix
      const fullSym = this._getPrefixedSymbol(sym);
      const extLink = document.getElementById('tv-hover-link-ext');
      if (extLink) {
        extLink.href = `https://www.tradingview.com/chart/jPAQSlZC/?symbol=${encodeURIComponent(fullSym)}&interval=${this._currentInterval || defaultInterval}`;
      }

      // Interval pills
      this.setInterval(this._currentInterval || defaultInterval, false);

      // Load Chart iframe
      this._loadChartFrame(sym, this._currentInterval);

      // Viewport-aware positioning
      this._positionCard(targetEl);

      // Reveal with animation
      this._cardEl.style.display = 'flex';
      requestAnimationFrame(() => {
        if (this._cardEl) this._cardEl.classList.add('visible');
      });
    },

    hide() {
      clearTimeout(this._hoverTimer);
      clearTimeout(this._hideTimer);
      if (this._cardEl) {
        this._cardEl.classList.remove('visible');
        setTimeout(() => {
          if (!this._cardEl.classList.contains('visible')) {
            this._cardEl.style.display = 'none';
          }
        }, 180);
      }
      this._isCardHovered = false;
      this._isTargetHovered = false;
      this._currentTarget = null;
    },

    setInterval(interval, updateFrame = true) {
      this._currentInterval = interval;

      // Update button active classes
      const btns = this._cardEl ? this._cardEl.querySelectorAll('.tv-hover-int-btn') : [];
      btns.forEach(b => {
        if (b.getAttribute('data-int') === interval) {
          b.classList.add('active');
        } else {
          b.classList.remove('active');
        }
      });

      const fullSym = this._getPrefixedSymbol(this._currentSym);
      const extLink = document.getElementById('tv-hover-link-ext');
      if (extLink && this._currentSym) {
        extLink.href = `https://www.tradingview.com/chart/jPAQSlZC/?symbol=${encodeURIComponent(fullSym)}&interval=${interval}`;
      }

      if (updateFrame && this._currentSym) {
        this._loadChartFrame(this._currentSym, interval);
      }
    },

    openFullModal() {
      const sym = this._currentSym;
      const int = this._currentInterval || 'D';
      this.hide();
      if (window.AppSwing && typeof window.AppSwing.openTradingViewModal === 'function') {
        window.AppSwing.openTradingViewModal(sym, int);
      } else {
        window.open(`/research/${encodeURIComponent(sym)}`, '_blank');
      }
    },

    _loadChartFrame(sym, interval) {
      if (!this._iframeEl) return;

      const fullSym = this._getPrefixedSymbol(sym);
      const expectedSrc = `https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(fullSym)}&interval=${encodeURIComponent(interval)}&hidesidetoolbar=1&symboledit=0&saveimage=0&toolbarbg=0f172a&studies=%5B%5D&theme=dark&style=1&timezone=America%2FNew_York`;

      if (this._iframeEl.src !== expectedSrc) {
        if (this._loadingEl) this._loadingEl.style.opacity = '1';
        this._iframeEl.src = expectedSrc;
      }
    },

    _getCompanyName(sym) {
      if (window.AppUtils && typeof window.AppUtils.getCompanyName === 'function') {
        return window.AppUtils.getCompanyName(sym);
      }
      if (window.COMPANY_NAMES && window.COMPANY_NAMES[sym]) {
        return window.COMPANY_NAMES[sym];
      }
      const quick = {
        'AAPL': 'Apple Inc.', 'NVDA': 'NVIDIA Corp', 'MSFT': 'Microsoft Corp',
        'AMZN': 'Amazon.com Inc.', 'GOOG': 'Alphabet Inc.', 'GOOGL': 'Alphabet Inc.',
        'META': 'Meta Platforms', 'TSLA': 'Tesla Inc.', 'PL': 'Palantir Technologies',
        'AMD': 'Advanced Micro Devices', 'SPY': 'SPDR S&P 500 ETF', 'QQQ': 'Invesco QQQ Trust'
      };
      return quick[sym] || sym;
    },

    _loadSpotPrice(sym, priceEl) {
      if (!priceEl) return;
      if (window.AppApi && typeof window.AppApi.getTickerQuote === 'function') {
        window.AppApi.getTickerQuote(sym).then(q => this._renderPriceBadge(q, sym, priceEl)).catch(() => {});
      } else {
        fetch(`/api/quote?ticker=${encodeURIComponent(sym)}`)
          .then(r => r.ok ? r.json() : null)
          .then(data => {
            if (data && this._currentSym === sym) {
              const q = data.quote || data;
              this._renderPriceBadge(q, sym, priceEl);
            }
          })
          .catch(() => {});
      }
    },

    _renderPriceBadge(q, sym, priceEl) {
      if (!q || !priceEl || this._currentSym !== sym) return;
      const p = Number(q.price || q.last || q.close);
      if (!p || isNaN(p)) return;
      const chg = q.net_percent_change !== undefined ? Number(q.net_percent_change) : (q.change_pct !== undefined ? Number(q.change_pct) : null);
      let chgHtml = '';
      if (chg !== null && !isNaN(chg)) {
        const chgColor = chg >= 0 ? '#10b981' : '#ef4444';
        chgHtml = ` <span style="color:${chgColor};font-size:10.5px;font-weight:700;">(${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%)</span>`;
      }
      const src = q.source ? `${q.source} Live` : 'Live';
      priceEl.innerHTML = `<span class="tv-hover-live-pill" title="0-delay real-time quote directly from ${src} stream"><span class="dot live-pulse"></span>Live $${p.toFixed(2)}${chgHtml}</span>`;
      priceEl.style.display = 'inline-flex';
    },

    _positionCard(targetEl) {
      if (!this._cardEl || !targetEl) return;

      const rect = targetEl.getBoundingClientRect();
      const cardW = 580;
      const cardH = 400;
      const padding = 12;

      let left = rect.right + padding;
      let top = rect.top - 20;

      // Horizontal boundary detection
      if (left + cardW > window.innerWidth - padding) {
        left = rect.left - cardW - padding;
      }
      if (left < padding) {
        left = Math.max(padding, window.innerWidth - cardW - padding);
      }

      // Vertical boundary detection
      if (top + cardH > window.innerHeight - padding) {
        top = window.innerHeight - cardH - padding;
      }
      if (top < padding) {
        top = padding;
      }

      this._cardEl.style.left = `${Math.round(left)}px`;
      this._cardEl.style.top = `${Math.round(top)}px`;
    }
  };

  // Initialize as soon as DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => window.AppTvHover.init());
  } else {
    window.AppTvHover.init();
  }
})();
