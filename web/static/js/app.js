/**
 * Main Application Bootstrap & Lifecycle Orchestrator
 */
window.App = {
  switchDesk(desk) {
    window.AppState.currentDesk = desk;
    ['swing', 'watchlist', 'intraday', 'logs'].forEach(d => {
      const pane = document.getElementById(`desk-pane-${d}`);
      const btn = document.getElementById(`btn-desk-${d}`);
      if (pane) pane.style.display = (d === desk) ? 'flex' : 'none';
      if (btn) btn.className = `desk-nav-btn ${d === desk ? 'active' : ''}`;
    });

    if (desk === 'watchlist' && window.AppWatchlist) {
      window.AppWatchlist.loadWatchlist();
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
    console.log('🚀 Initializing Stock Trading Operations Cockpit...');

    // 0. Initialize theme (defaults to clean Light)
    this.initTheme();

    // 1. Initialize REV CHAT resize handlers
    if (window.AppChat) {
      window.AppChat.initChatResize();
    }

    // 2. Initial Data Loading
    if (window.AppStatus) window.AppStatus.updateStatus();
    if (window.AppWatchlist) window.AppWatchlist.loadWatchlist();
    if (window.AppSwing) {
      window.AppSwing.refreshResearchQueue();
      window.AppSwing.loadTastytradeAlerts();
      window.AppSwing.loadReportArchive();
      window.AppSwing.loadCalibrationScoreboard();
    }
    if (window.AppLogs) window.AppLogs.loadLogs();
    this.loadAllTickersDatalist();

    // 3. Fast Poll Loop (2.5s) for Live Status, Logs, and Positions
    setInterval(() => {
      if (window.AppStatus) window.AppStatus.updateStatus();
      if (window.AppLogs) window.AppLogs.loadLogs();
      if (window.AppState.currentDesk === 'intraday' && window.AppIntraday) {
        window.AppIntraday.loadIntradayPositions();
      } else if (window.AppState.currentDesk === 'logs' && window.AppLogs) {
        window.AppLogs.loadDedicatedLogs();
      }
    }, 2500);

    // 4. Slow Poll Loop (6s) for Watch Targets & Tastytrade Cloud Alerts
    setInterval(() => {
      if (window.AppWatchlist) {
        window.AppWatchlist.loadWatchlist();
      }
      if (window.AppSwing) {
        window.AppSwing.loadTastytradeAlerts();
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
  }
};

// Start application when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  window.App.init();
});
