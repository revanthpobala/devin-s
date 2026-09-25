/**
 * TradingView Live Chart Hover Engine
 * Universally provides instant, interactive TradingView chart previews on hovering any stock ticker symbol.
 */
(function() {
  'use strict';

  const RESERVED_WORDS = new Set([
    'PASS', 'WAIT', 'CUT', 'LONG', 'SHORT', 'BUY', 'SELL', 'EXIT', 'ENTER',
    'DATE', 'TIME', 'PRICE', 'ACTION', 'SCORE', 'GRADE', 'INFO', 'AI', 'CLOSE',
    'TOTAL', 'ACTIVE', 'IN_ZONE', 'STALKING', 'STOP', 'TARGET', 'STATUS',
    'OPEN', 'TYPE', 'SIDE', 'ENTRY', 'SPOT', 'PNL', 'HIGH', 'LOW', 'VOLUME',
    'INSPECT', 'QUERY', 'RESTORE', 'DETAILS', 'DISMISS', 'VIEW', 'LOGS'
  ]);

  window.AppTvHover = {
    _cardEl: null,
    _iframeEl: null,
    _loadingEl: null,
    _currentSym: '',
    _currentExchange: 'BATS',
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
        if (saved) this._currentExchange = saved;
      } catch (e) {}

      this._createCardElement();
      this._bindGlobalEvents();
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
                <option value="BATS">BATS</option>
                <option value="NASDAQ">NASDAQ</option>
                <option value="NYSE">NYSE</option>
                <option value="AMEX">AMEX</option>
                <option value="AUTO">AUTO</option>
              </select>
              <div class="tv-hover-intervals">
                <button class="tv-hover-int-btn" data-int="5" onclick="AppTvHover.setInterval('5')">5m</button>
                <button class="tv-hover-int-btn" data-int="15" onclick="AppTvHover.setInterval('15')">15m</button>
                <button class="tv-hover-int-btn" data-int="60" onclick="AppTvHover.setInterval('60')">1h</button>
                <button class="tv-hover-int-btn active" data-int="D" onclick="AppTvHover.setInterval('D')">1D</button>
              </div>
              <button class="tv-hover-btn-action" onclick="AppTvHover.openFullModal()" title="Expand to Full Interactive Modal">⛶ Full</button>
              <a class="tv-hover-btn-action" id="tv-hover-link-ext" target="_blank" href="#" title="Open directly in TradingView (loads with your real-time subscription & indicators)">↗</a>
            </div>
          </div>
          <div class="tv-hover-chart-body">
            <div class="tv-hover-loading" id="tv-hover-loading">
              <span class="dot pulse" style="background:var(--cyan-glow, #38bdf8);"></span>
              <span>Loading Chart...</span>
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
        this._scheduleHide(180);
      });
    },

    _bindGlobalEvents() {
      // Global delegated mouseover listener
      document.addEventListener('mouseover', (e) => {
        const symbolInfo = this._resolveSymbolFromEvent(e);
        if (!symbolInfo) return;

        const { sym, el } = symbolInfo;
        this._isTargetHovered = true;
        clearTimeout(this._hideTimer);

        if (this._currentSym === sym && this._cardEl && this._cardEl.style.display !== 'none') {
          // Already showing this symbol
          this._currentTarget = el;
          return;
        }

        clearTimeout(this._hoverTimer);
        this._hoverTimer = setTimeout(() => {
          if (this._isTargetHovered) {
            this.show(el, sym);
          }
        }, 220); // 220ms hover dwell time for instant response without cursor flickers
      }, true);

      // Global mouseout listener
      document.addEventListener('mouseout', (e) => {
        const symbolInfo = this._resolveSymbolFromEvent(e);
        if (!symbolInfo) return;

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
      if (!target || !target.closest) return null;

      // Priority 1: explicitly marked ticker containers
      const el = target.closest('[data-ticker], [data-symbol], [data-tv-symbol], .ticker-pill-btn, .ticker-cell-sym, .radar-ticker-sym, .ticker-with-tooltip, .ticker-table-card, .tv-symbol-hover');
      if (el) {
        let sym = el.getAttribute('data-ticker') || el.getAttribute('data-symbol') || el.getAttribute('data-tv-symbol');
        if (!sym) {
          sym = (el.textContent || '').trim().replace(/^\$/, '').replace(/🔍/g, '').trim().toUpperCase();
        }
        sym = this._cleanTicker(sym);
        if (sym) return { sym, el };
      }

      // Priority 2: table cell inside a column labeled Symbol or Ticker
      const td = target.closest('td');
      if (td && td.parentElement) {
        const tr = td.parentElement;
        const cellIndex = Array.prototype.indexOf.call(tr.children, td);
        const table = tr.closest('table');
        if (table) {
          const th = table.querySelector(`thead tr th:nth-child(${cellIndex + 1})`);
          if (th) {
            const thText = (th.textContent || '').toUpperCase();
            if (thText.includes('SYMBOL') || thText.includes('TICKER')) {
              let sym = td.getAttribute('data-ticker') || (td.textContent || '').trim().replace(/^\$/, '').replace(/🔍/g, '').trim().toUpperCase();
              sym = this._cleanTicker(sym);
              if (sym) return { sym, el: td };
            }
          }
        }
      }

      return null;
    },

    _cleanTicker(txt) {
      if (!txt) return null;
      const clean = txt.replace(/[^A-Z]/gi, '').toUpperCase();
      if (clean.length >= 1 && clean.length <= 6 && !RESERVED_WORDS.has(clean)) {
        return clean;
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
      const exch = (this._currentExchange || 'BATS').trim().toUpperCase();
      if (!exch || exch === 'AUTO') {
        return sym;
      }
      return `${exch}:${sym}`;
    },

    setExchange(exchange, updateFrame = true) {
      this._currentExchange = (exchange || 'BATS').trim().toUpperCase();
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
        if (savedExch) this._currentExchange = savedExch;
      } catch (e) {}

      const selectEl = document.getElementById('tv-hover-exchange');
      if (selectEl) {
        selectEl.value = this._currentExchange || 'BATS';
      }

      // Intelligent default timeframe based on active desk
      const isIntradayDesk = (window.AppState && window.AppState.currentDesk === 'intraday');
      let defaultInterval = isIntradayDesk ? '15' : 'D';
      
      // Update Title & Company Info
      const symEl = document.getElementById('tv-hover-sym');
      const nameEl = document.getElementById('tv-hover-name');
      const priceEl = document.getElementById('tv-hover-price');

      if (symEl) symEl.textContent = `$${sym}`;
      if (nameEl) {
        const comp = window.AppUtils ? AppUtils.getCompanyName(sym) : '';
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

      // Load Chart iframe with exchange prefix
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

    _loadSpotPrice(sym, priceEl) {
      if (!priceEl) return;
      if (window.AppApi && typeof window.AppApi.getTickerQuote === 'function') {
        window.AppApi.getTickerQuote(sym).then(q => {
          if (q && q.price && this._currentSym === sym) {
            const p = Number(q.price);
            const chg = q.net_percent_change !== undefined ? Number(q.net_percent_change) : (q.change_pct !== undefined ? Number(q.change_pct) : null);
            let chgHtml = '';
            if (chg !== null) {
              const chgColor = chg >= 0 ? '#10b981' : '#ef4444';
              chgHtml = ` <span style="color:${chgColor};font-size:10px;">(${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%)</span>`;
            }
            const src = q.source ? `${q.source} Live` : 'Live';
            priceEl.innerHTML = `<span class="tv-hover-live-pill" title="0-delay real-time quote directly from ${src} stream"><span class="dot live-pulse"></span>Live $${p.toFixed(2)}${chgHtml}</span>`;
            priceEl.style.display = 'inline-flex';
          }
        }).catch(() => {});
      }
    },

    _positionCard(targetEl) {
      if (!this._cardEl || !targetEl) return;

      const rect = targetEl.getBoundingClientRect();
      const cardW = 560;
      const cardH = 390;
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
