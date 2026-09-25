/**
 * Utility & Formatting Helpers
 */
window.AppUtils = {
  /**
   * Escape HTML entities safely
   */
  escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  },

  /**
   * Safely render markdown with marked.js
   */
  renderMarkdown(text) {
    if (!text) return '';
    try {
      let cleanText = String(text);
      let rawLevelsJson = null;

      // Extract machine-readable watch_levels block so narrative markdown format is strictly respected
      const wlMatch = cleanText.match(/```(?:json)?(?::watch_levels|\s+watch_levels)?\s*(\{[\s\S]*?\})\s*```/i);
      if (wlMatch) {
        rawLevelsJson = wlMatch[1];
        cleanText = cleanText.replace(wlMatch[0], '').trim();
      }

      if (window.marked) {
        if (typeof window.marked.setOptions === 'function' && !window.marked._breaksConfigured) {
          window.marked.setOptions({ breaks: true, gfm: true });
          window.marked._breaksConfigured = true;
        }
        let html = typeof window.marked.parse === 'function'
          ? window.marked.parse(cleanText, { breaks: true, gfm: true })
          : window.marked(cleanText, { breaks: true, gfm: true });
        // Transform interactive action links into clickable button pills
        html = html.replace(/<a\s+href=["']action:ask\?prompt=([^"']+)["']>([\s\S]*?)<\/a>/gi, (match, promptEnc, label) => {
          const prompt = decodeURIComponent(promptEnc.replace(/\+/g, '%20'));
          return `<button class="ticker-pill-btn" onclick="AppChat.askActiveChat('${prompt.replace(/'/g, "\\'")}')" style="display:inline-flex; align-items:center; gap:4px; margin:4px 4px 4px 0; font-size:11.5px; padding:3px 9px; color:var(--cyan-glow); border-color:rgba(6,182,212,0.4); background:rgba(6,182,212,0.12); cursor:pointer;">${label}</button>`;
        });

        // If structured watch levels were extracted, append as a subtle collapsible drawer at the very bottom
        if (rawLevelsJson) {
          html += `
            <details style="margin-top:32px; border:1px solid var(--border); border-radius:6px; background:var(--bg-subtle); padding:8px 12px; font-size:11px;">
              <summary style="cursor:pointer; font-weight:700; color:var(--text-muted); user-select:none;">🔧 Raw Tactical Levels JSON (Extracted for Watchlist)</summary>
              <pre style="margin-top:8px; padding:10px; background:rgba(0,0,0,0.4); border-radius:4px; overflow-x:auto;"><code class="language-json">${this.escapeHtml(rawLevelsJson)}</code></pre>
            </details>
          `;
        }
        return html;
      }
    } catch (e) {
      console.warn('Error parsing markdown', e);
    }
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\n/g, '<br/>');
  },

  /**
   * Format numbers as currency $X.XX
   */
  formatCurrency(val, defaultVal = 'N/A') {
    if (val === null || val === undefined || isNaN(Number(val))) return defaultVal;
    return `$${Number(val).toFixed(2)}`;
  },

  /**
   * Format percentage with +/- sign
   */
  formatPct(val, defaultVal = '0.00%') {
    if (val === null || val === undefined || isNaN(Number(val))) return defaultVal;
    const num = Number(val);
    const sign = num >= 0 ? '+' : '';
    return `${sign}${num.toFixed(2)}%`;
  },

  /**
   * Class for PnL color
   */
  pnlColor(val) {
    if (val === null || val === undefined || isNaN(Number(val))) return 'var(--text-muted)';
    return Number(val) >= 0 ? 'var(--emerald-light)' : 'var(--rose-light)';
  },

  /**
   * Class for verdict badge
   */
  getVerdictBadgeClass(verdict) {
    const v = String(verdict || '').toUpperCase();
    if (v.includes('ENTER') || v.includes('BUY') || v.includes('PASS') || v.includes('IN_ZONE')) {
      return 'in_zone';
    }
    if (v.includes('AVOID') || v.includes('CUT') || v.includes('STOP') || v.includes('DANGER')) {
      return 'danger';
    }
    return 'stalking';
  },

  /**
   * Initialize and cache full company names mapping
   */
  async initCompanyNames() {
    if (window.COMPANY_NAMES && Object.keys(window.COMPANY_NAMES).length > 500) {
      return window.COMPANY_NAMES;
    }
    window.COMPANY_NAMES = window.COMPANY_NAMES || {};

    // 1. Try local storage cache
    try {
      const cached = localStorage.getItem('rev_company_names_v1');
      if (cached) {
        const parsed = JSON.parse(cached);
        if (parsed && typeof parsed === 'object' && Object.keys(parsed).length > 1000) {
          window.COMPANY_NAMES = parsed;
          this.decorateTickerTooltips();
        }
      }
    } catch (e) {}

    // 2. Fetch from static json (fastest loopback static asset) or API
    try {
      let res = await fetch('/static/data/company_names.json');
      if (!res.ok) {
        res = await fetch('/api/company-names');
      }
      if (res.ok) {
        const data = await res.json();
        if (data && typeof data === 'object') {
          window.COMPANY_NAMES = data;
          try {
            localStorage.setItem('rev_company_names_v1', JSON.stringify(data));
          } catch (err) {}
          this.decorateTickerTooltips();
        }
      }
    } catch (e) {
      console.warn('Unable to load company names mapping:', e);
    }
    return window.COMPANY_NAMES;
  },

  /**
   * Get full company name for any ticker symbol
   */
  getCompanyName(ticker) {
    if (!ticker) return '';
    const sym = String(ticker).toUpperCase().replace(/^\$/, '').trim();
    if (window.COMPANY_NAMES && window.COMPANY_NAMES[sym]) {
      return window.COMPANY_NAMES[sym];
    }
    // Common fallbacks if not yet loaded
    const quickMap = {
      'AMZN': 'Amazon.com Inc',
      'GOOGL': 'Alphabet Inc A',
      'GOOG': 'Alphabet Inc C',
      'AAPL': 'Apple Inc.',
      'NVDA': 'Nvidia Corp',
      'MSFT': 'Microsoft Corp',
      'META': 'Meta Platforms Inc.',
      'TSLA': 'Tesla, Inc.',
      'SLB': 'SLB Limited (Schlumberger)',
      'DELL': 'Dell Technologies Inc.',
      'YETI': 'YETI Holdings, Inc.',
      'AMD': 'Advanced Micro Devices',
      'SPY': 'SPDR S&P 500 ETF Trust',
      'QQQ': 'Invesco QQQ Trust',
      'IWM': 'iShares Russell 2000 ETF',
      'PLTR': 'Palantir Technologies',
      'VLTO': 'Veralto Corporation',
      'CAVA': 'CAVA Group, Inc.',
      'WMT': 'Walmart Inc.',
      'GM': 'General Motors Company',
      'PCG': 'PG&E Corporation',
      'ARES': 'Ares Management Corp'
    };
    return quickMap[sym] || sym;
  },

  /**
   * Render a ticker symbol with company name tooltip
   */
  renderTicker(ticker, options = {}) {
    if (!ticker) return '';
    const sym = String(ticker).toUpperCase().replace(/^\$/, '').trim();
    const name = this.getCompanyName(sym);
    const prefix = options.prefix !== undefined ? options.prefix : '$';
    const cls = options.className || 'ticker-with-tooltip';
    const style = options.style || '';
    const escapedName = this.escapeHtml(name);
    return `<span class="${cls}" title="${escapedName}" data-ticker="${sym}" style="cursor:help; ${style}">${prefix}${sym}</span>`;
  },

  /**
   * Retroactively decorate all tickers in DOM container with company name tooltips
   */
  decorateTickerTooltips(root = document) {
    try {
      const candidates = root.querySelectorAll('.ticker-pill-btn, .ticker-cell-sym, .radar-ticker-sym, .recent-job-row strong, .ticker-with-tooltip, .ticker-table-card, [data-ticker]');
      candidates.forEach(el => {
        let sym = el.getAttribute('data-ticker');
        if (!sym) {
          const txt = (el.textContent || '').trim().replace(/^\$/, '').replace(/🔍/g, '').trim();
          if (/^[A-Z]{1,6}$/.test(txt)) {
            sym = txt;
          }
        }
        if (sym) {
          const comp = this.getCompanyName(sym);
          if (comp && (!el.title || el.title.startsWith('Click to open') || el.title.length < comp.length)) {
            el.title = `${sym}: ${comp} · Hover for Live TradingView Chart`;
          }
          if (!el.getAttribute('data-ticker')) {
            el.setAttribute('data-ticker', sym);
          }
          el.classList.add('tv-symbol-hover');
        }
      });
    } catch (e) {
      console.warn('decorateTickerTooltips error:', e);
    }
  },

  showToast(msg, type = 'info') {
    let container = document.getElementById('cockpit-toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'cockpit-toast-container';
      container.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:999999;display:flex;flex-direction:column;gap:8px;pointer-events:none;';
      document.body.appendChild(container);
    }
    const t = document.createElement('div');
    const bg = type === 'error' ? '#ef4444' : (type === 'success' ? '#10b981' : '#0ea5e9');
    t.style.cssText = `background:${bg};color:#fff;padding:8px 14px;border-radius:6px;font-size:12px;font-weight:700;box-shadow:0 4px 12px rgba(0,0,0,0.3);transition:all 0.3s ease;opacity:0;transform:translateY(10px);`;
    t.innerText = msg;
    container.appendChild(t);
    requestAnimationFrame(() => { t.style.opacity = '1'; t.style.transform = 'translateY(0)'; });
    setTimeout(() => {
      t.style.opacity = '0';
      t.style.transform = 'translateY(-8px)';
      setTimeout(() => t.remove(), 300);
    }, 3000);
  }
};

// Ensure AppStatus.showToast aliases to AppUtils.showToast
if (typeof window !== 'undefined') {
  window.AppStatus = window.AppStatus || {};
  window.AppStatus.showToast = function(msg, type = 'info') {
    if (window.AppUtils && window.AppUtils.showToast) {
      window.AppUtils.showToast(msg, type);
    }
  };

  window.addEventListener('DOMContentLoaded', () => {
    if (window.AppUtils && window.AppUtils.initCompanyNames) {
      window.AppUtils.initCompanyNames();
    }
  });
}

