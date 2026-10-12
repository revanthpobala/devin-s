/**
 * Main Application Bootstrap & Lifecycle Orchestrator
 */
window.App = {
  switchDesk(desk) {
    if (desk === 'screener') {
      desk = 'alerts';
      if (window.AppAlerts && typeof window.AppAlerts.switchSubTab === 'function') {
        window.AppAlerts.switchSubTab('schwab');
      }
    }
    
    if (desk === 'ops') desk = 'logs';

    window.AppState.currentDesk = desk;
    
    const allDesks = ['today', 'swing', 'alerts', 'watchlist', 'trades', 'portfolio', 'intraday', 'journal', 'record', 'logs'];
    allDesks.forEach(d => {
      const pane = document.getElementById(`desk-pane-${d}`);
      if (pane) pane.style.display = (d === desk) ? 'flex' : 'none';
      const btn = document.getElementById(`btn-desk-${d}`);
      if (btn) btn.className = `desk-nav-btn ${d === desk ? 'active' : ''}`;
    });

    const ledgerBtn = document.getElementById('btn-desk-ledger');
    if (ledgerBtn) {
      if (desk === 'journal' || desk === 'record') {
        ledgerBtn.className = 'desk-nav-btn active';
      } else {
        ledgerBtn.className = 'desk-nav-btn';
      }
    }

    const uConsole = document.getElementById('universal-console-station');
    if (uConsole) {
      if (desk === 'today' || desk === 'journal' || desk === 'record') {
        uConsole.style.display = 'none';
      } else {
        uConsole.style.display = 'block';
      }
    }

    if (desk === 'today' && window.AppDesk) {
      window.AppDesk.loadToday();
    } else if (desk === 'journal' && window.AppDesk) {
      window.AppDesk.loadJournal();
    } else if (desk === 'record' && window.AppDesk) {
      window.AppDesk.loadRecord();
    } else if (desk === 'swing' && window.AppSwing) {
      window.AppSwing.refreshResearchQueue();
    } else if (desk === 'alerts' && window.AppAlerts) {
      if (window.AppAlerts._activeSubTab === 'schwab' && window.AppSwing) {
        window.AppSwing.loadSchwabScreener();
      } else {
        window.AppAlerts.loadAlerts();
      }
    } else if (desk === 'watchlist' && window.AppWatchlist) {
      window.AppWatchlist.loadWatchlist();
    } else if (desk === 'trades' && window.AppTrades) {
      window.AppTrades.loadSuggestedTrades();
      if (window.AppSwing && typeof window.AppSwing.loadCalibrationScoreboard === 'function') {
        window.AppSwing.loadCalibrationScoreboard();
      }
    } else if (desk === 'portfolio' && window.AppPortfolio) {
      window.AppPortfolio.loadPortfolio();
    } else if (desk === 'intraday' && window.AppIntraday) {
      window.AppIntraday.loadIntradayPositions();
    } else if (desk === 'logs' && window.AppLogs) {
      window.AppLogs.loadDedicatedLogs();
    }
  },

  async loadAllTickersDatalist() {
    try {
      const data = await window.AppApi.getTickers();
      const dl = document.getElementById('rev-tickers-datalist');
      if (dl && data.tickers) {
        dl.innerHTML = `
          <option value="AUTO">🤖 Auto-Detect from Prompt</option>
          <option value="GENERAL">🌐 General Market / Any Stock</option>
          ${data.tickers.map(t => `<option value="${t}">${t}</option>`).join('')}
        `;
      }
    } catch (e) {
      console.error('Failed to load tickers datalist', e);
    }
  },

  toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'light';
    const next = current === 'light' ? 'dark' : 'light';
    this.setTheme(next);
  },

  setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    try {
      localStorage.setItem('cockpit_theme', theme);
    } catch (e) {}
    const btn = document.getElementById('btn-theme-toggle');
    if (btn) {
      btn.innerHTML = theme === 'light' ? '☀️ Light' : '🌙 Dark';
    }
  },

  initTheme() {
    let saved = 'light';
    try {
      saved = localStorage.getItem('cockpit_theme') || 'light';
    } catch (e) {}
    this.setTheme(saved);
  },

  init() {
    console.log('🚀 REV TERMINAL booting...');

    // 0. Initialize theme (defaults to clean Light)
    this.initTheme();

    // 0b. Initialize active desk
    this.switchDesk('today');

    // 1. Initialize REV CHAT resize handlers
    if (window.AppChat) {
      window.AppChat.initChatResize();
    }

    // 2. Initial Data Loading
    if (window.AppUtils && window.AppUtils.initCompanyNames) {
      window.AppUtils.initCompanyNames();
    }
    if (window.AppStatus) window.AppStatus.updateStatus();
    if (window.AppWatchlist) window.AppWatchlist.loadWatchlist();
    if (window.AppTrades) window.AppTrades.loadSuggestedTrades();
    if (window.AppSwing) {
      window.AppSwing.refreshResearchQueue();
      window.AppSwing.loadTastytradeAlerts();
      window.AppSwing.loadReportArchive();
      window.AppSwing.loadCalibrationScoreboard();
      window.AppSwing.loadSchwabScreener();
    }
    if (window.AppLogs) window.AppLogs.loadLogs();
    if (window.AppAlerts) window.AppAlerts.loadAlerts();
    if (window.AppDesk) window.AppDesk.loadToday();
    this.loadAllTickersDatalist();

    // 3. Fast Poll Loop (2.5s) for Live Status, Logs, and Positions
    setInterval(() => {
      if (window.AppStatus) window.AppStatus.updateStatus();
      if (window.AppLogs) window.AppLogs.loadLogs();
      if (window.AppIntraday) {
        window.AppIntraday.loadIntradayPositions();
      }
      if (window.AppState.currentDesk === 'logs' && window.AppLogs) {
        window.AppLogs.loadDedicatedLogs();
      } else if (window.AppState.currentDesk === 'alerts' && window.AppAlerts) {
        window.AppAlerts.loadAlerts();
      }
    }, 2500);

    // 4. Slow Poll Loop (6s) for Watch Targets & Tastytrade Cloud Alerts
    setInterval(() => {
      if (window.AppWatchlist) {
        window.AppWatchlist.loadWatchlist();
      }
      if (window.AppState.currentDesk === 'trades' && window.AppTrades) {
        window.AppTrades.loadSuggestedTrades();
      }
      if (window.AppSwing) {
        window.AppSwing.loadTastytradeAlerts();
      }
      if (window.AppState.currentDesk === 'today' && window.AppDesk) {
        window.AppDesk.loadToday(true);
      }
    }, 6000);

    // 5. 1-Minute (60s) Auto-Refresh for Live Ticker Prices, Radar & Calibration Scoreboard
    setTimeout(() => {
      if (window.AppWatchlist) {
        window.AppWatchlist.pollRealtimeSilently();
      }
    }, 3000);

    setInterval(() => {
      if (window.AppWatchlist) {
        window.AppWatchlist.pollRealtimeSilently();
      }
      if (window.AppSwing) {
        window.AppSwing.loadCalibrationScoreboard();
      }
    }, 60000);

    // 6. Default REV CHAT focus is ALWAYS broad market on a fresh page load.
    //    A ticker may only be focused by an explicit action (deep link below, opening a dossier,
    //    the focus box, or a position query). Without this reset a stale deep link / last-opened
    //    report left the chat pinned to one symbol indefinitely.
    if (window.AppChat && typeof window.AppChat.clearRevChatFocus === 'function') {
      window.AppChat.clearRevChatFocus();
    }

    // 7. Deep Link Handler for Tickers & Research URLs (runs last so an explicit link can re-focus)
    this.handleDeepLinks();
  },

  handleDeepLinks() {
    try {
      const urlParams = new URLSearchParams(window.location.search);
      const pathParts = window.location.pathname.split('/').filter(Boolean);
      
      let directTicker = urlParams.get('ticker') || urlParams.get('sym');
      let directDate = urlParams.get('date');
      let directTab = urlParams.get('tab');

      // Check path /research/{ticker}
      if (!directTicker && pathParts.length >= 2 && pathParts[0].toLowerCase() === 'research') {
        directTicker = pathParts[1];
        if (pathParts.length >= 3 && !directDate) {
          directDate = pathParts[2];
        }
      }

      // Check hash #research/{ticker} or #report/{ticker}
      const hashMatch = window.location.hash.match(/#\/?(?:research|report|dossier)\/([A-Za-z0-9._-]+)(?:\/([0-9-]+))?/i);
      if (!directTicker && hashMatch) {
        directTicker = hashMatch[1];
        if (hashMatch[2] && !directDate) {
          directDate = hashMatch[2];
        }
      }

      // Check desk routing parameter (e.g. ?desk=trades, ?desk=radar, ?desk=screener, ?desk=portfolio, ?desk=watchlist)
      let directDesk = urlParams.get('desk') || (window.location.hash.startsWith('#desk-') ? window.location.hash.replace('#desk-', '') : (window.location.hash.startsWith('#') ? window.location.hash.slice(1) : null));
      if (!directDesk && pathParts.length >= 1) {
        const potentialDesk = pathParts[0].toLowerCase();
        if (['today', 'swing', 'radar', 'alerts', 'screener', 'watchlist', 'trades', 'portfolio', 'intraday', 'journal', 'record', 'logs'].includes(potentialDesk)) {
          directDesk = potentialDesk;
        }
      }

      if (directDesk) {
        directDesk = directDesk.toLowerCase().trim();
        if (directDesk === 'radar') directDesk = 'swing';
        if (directDesk === 'screener') directDesk = 'screener'; // switchDesk maps to alerts + schwab
        if (['today', 'swing', 'alerts', 'screener', 'watchlist', 'trades', 'portfolio', 'intraday', 'journal', 'record', 'logs'].includes(directDesk)) {
          this.switchDesk(directDesk);
        }
      }

      if (directTicker) {
        directTicker = directTicker.toUpperCase().trim();
        console.log(`[DeepLink] Auto-opening research dossier for $${directTicker}...`);
        setTimeout(() => {
          if (window.AppSwing && typeof window.AppSwing.openReportModal === 'function') {
            window.AppSwing.openReportModal(directDate || null, directTicker, directTab || null);
          }
        }, 500);
      }
    } catch (e) {
      console.warn('[DeepLink] Error parsing deep link:', e);
    }
  }
};

// ============================================================================
// Settings & System Status modal — one home for services, appearance, and diagnostics.
// The top ribbon stays compact: status is visible at a glance, actions live here.
// ============================================================================
window.AppSettings = {
  _clockTimer: null,

  open() {
    const m = document.getElementById('settings-modal');
    if (!m) return;
    m.style.display = 'flex';
    this._mirrorStatus();
    this._startClock();
  },

  close() {
    const m = document.getElementById('settings-modal');
    if (m) m.style.display = 'none';
    this._stopClock();
  },

  toggle() {
    const m = document.getElementById('settings-modal');
    if (m && m.style.display !== 'none') this.close();
    else this.open();
  },

  _startClock() {
    if (this._clockTimer) return;
    const tick = () => {
      const el = document.getElementById('st-clock');
      if (el) {
        const now = new Date();
        el.innerText = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      }
    };
    tick();
    this._clockTimer = setInterval(tick, 1000);
  },

  _stopClock() {
    if (this._clockTimer) {
      clearInterval(this._clockTimer);
      this._clockTimer = null;
    }
  },

  // Mirror the ribbon pills into the modal's status grid so one place shows everything.
  _mirrorStatus() {
    const map = [
      ['txt-market', 'st-market'],
      ['txt-llm', 'st-llm'],
      ['txt-tasty', 'st-tasty'],
      ['txt-schwab-modal', 'st-schwab'],
      ['txt-tracker', 'st-tracker'],
    ];
    map.forEach(([from, to]) => {
      const a = document.getElementById(from);
      const b = document.getElementById(to);
      if (a && b) b.innerText = a.innerText;
    });
  }
};

// Start application when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  window.App.init();
});
