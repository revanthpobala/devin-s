/**
 * Centralized API Client
 */
window.AppApi = {
  async request(url, options = {}) {
    const res = await fetch(url, options);
    if (!res.ok) throw new Error(`API request failed: ${res.status} ${res.statusText}`);
    return await res.json();
  },

  async getStatus() {
    const res = await fetch('/api/status');
    if (!res.ok) throw new Error(`Failed to fetch status: ${res.status}`);
    return await res.json();
  },

  async getJobs() {
    const res = await fetch('/api/jobs');
    if (!res.ok) throw new Error(`Failed to fetch jobs: ${res.status}`);
    return await res.json();
  },

  async killJob(jobId) {
    const res = await fetch(`/api/jobs/${jobId}/kill`, { method: 'POST' });
    if (!res.ok) throw new Error(`Failed to kill job: ${res.status}`);
    return await res.json();
  },

  async killProcess(pid) {
    const res = await fetch('/api/processes/kill', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pid })
    });
    if (!res.ok) throw new Error(`Failed to terminate process: ${res.status}`);
    return await res.json();
  },

  async startOrchestrator() {
    const res = await fetch('/api/orchestrator/start', { method: 'POST' });
    if (!res.ok) throw new Error(`Failed to start orchestrator: ${res.status}`);
    return await res.json();
  },

  async stopOrchestrator() {
    const res = await fetch('/api/orchestrator/stop', { method: 'POST' });
    if (!res.ok) throw new Error(`Failed to stop orchestrator: ${res.status}`);
    return await res.json();
  },

  async getWatchTargets() {
    const res = await fetch('/api/watch-targets');
    if (!res.ok) throw new Error(`Failed to fetch watch targets: ${res.status}`);
    return await res.json();
  },

  async getSuggestedTrades() {
    const res = await fetch('/api/trades/suggested');
    if (!res.ok) throw new Error(`Failed to fetch suggested trades: ${res.status}`);
    return await res.json();
  },

  async getTradesAudit(params = {}) {
    const q = new URLSearchParams();
    if (params.tab) q.set('tab', params.tab);
    if (params.search) q.set('search', params.search);
    if (params.page) q.set('page', params.page);
    if (params.page_size) q.set('page_size', params.page_size);
    if (params.window !== undefined) q.set('window', params.window);
    const qs = q.toString() ? `?${q.toString()}` : '';
    const res = await fetch(`/api/trades/audit${qs}`);
    if (!res.ok) throw new Error(`Failed to fetch trades audit: ${res.status}`);
    return await res.json();
  },

  async evaluateTradesAudit(window = 100) {
    const res = await fetch(`/api/trades/audit/evaluate?window=${encodeURIComponent(window)}`, { method: 'POST' });
    if (!res.ok) throw new Error(`Failed to evaluate trades audit: ${res.status}`);
    return await res.json();
  },

  async getCompanyNames() {
    const res = await fetch('/api/company-names');
    if (!res.ok) throw new Error(`Failed to fetch company names: ${res.status}`);
    return await res.json();
  },

  async getResearchQueue(date) {
    const q = date ? `?date=${encodeURIComponent(date)}` : '';
    const res = await fetch(`/api/research/queue${q}`);
    if (!res.ok) throw new Error(`Failed to fetch research queue: ${res.status}`);
    return await res.json();
  },

  async getSchwabScreenerCandidates(date, side = 'long') {
    const params = new URLSearchParams();
    if (date) params.append('date', date);
    if (side) params.append('side', side);
    const q = params.toString() ? `?${params.toString()}` : '';
    const res = await fetch(`/api/screener/schwab-pre-move${q}`);
    if (!res.ok) throw new Error(`Failed to fetch Schwab screener: ${res.status}`);
    return await res.json();
  },

  async runSchwabScreenerScan(top = 5, autoScrape = false, side = 'long') {
    const res = await fetch('/api/screener/run-schwab-scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ top, auto_scrape: autoScrape, side })
    });
    if (!res.ok) throw new Error(`Failed to run Schwab screener: ${res.status}`);
    return await res.json();
  },

  async runAutonomousScreenerScan(top = 10, side = 'long', autoMax = 3) {
    const res = await fetch('/api/screener/run-autonomous-scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ top, side, autonomous: true, auto_max: autoMax })
    });
    if (!res.ok) throw new Error(`Failed to run autonomous screener scan: ${res.status}`);
    return await res.json();
  },

  async getSchwabScanStatus() {
    try {
      const res = await fetch('/api/screener/schwab-scan-status');
      if (!res.ok) return { running: false };
      return await res.json();
    } catch (e) {
      return { running: false };
    }
  },

  async pollWatchTargets() {
    const res = await fetch('/api/watch-targets/poll', { method: 'POST' });
    if (!res.ok) throw new Error(`Failed to trigger watch targets poll: ${res.status}`);
    return await res.json();
  },

  async deleteWatchTarget(ticker, date) {
    const res = await fetch('/api/watch-targets/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker, date })
    });
    if (!res.ok) throw new Error(`Failed to delete watch target: ${res.status}`);
    return await res.json();
  },

  async getTastytradeAlerts() {
    const res = await fetch('/api/tastytrade-alerts');
    if (!res.ok) throw new Error(`Failed to fetch Tastytrade alerts: ${res.status}`);
    return await res.json();
  },

  async getWatchAlerts(limit = 50, ticker = '') {
    const q = ticker ? `?limit=${limit}&ticker=${encodeURIComponent(ticker)}` : `?limit=${limit}`;
    const res = await fetch(`/api/watch-alerts${q}`);
    if (!res.ok) throw new Error(`Failed to fetch watch alerts: ${res.status}`);
    return await res.json();
  },

  async getAlertsHistory(limit = 1000, date = null, symbol = null, strategy = null) {
    let url = `/api/alerts/history?limit=${limit}`;
    if (date) url += `&date=${encodeURIComponent(date)}`;
    if (symbol) url += `&symbol=${encodeURIComponent(symbol)}`;
    if (strategy) url += `&strategy=${encodeURIComponent(strategy)}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Failed to fetch alert history: ${res.status}`);
    return await res.json();
  },

  async checkGmailAlerts() {
    const res = await fetch('/api/alerts/check-gmail', { method: 'POST' });
    if (!res.ok) throw new Error(`Failed to trigger Gmail check: ${res.status}`);
    return await res.json();
  },

  async createTastytradeAlert(data) {
    const res = await fetch('/api/tastytrade-alerts/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    if (!res.ok) throw new Error(`Failed to create alert: ${res.status}`);
    return await res.json();
  },

  async modifyTastytradeAlert(data) {
    const res = await fetch('/api/tastytrade-alerts/modify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    if (!res.ok) throw new Error(`Failed to modify alert: ${res.status}`);
    return await res.json();
  },

  async deleteTastytradeAlert(alertId) {
    const res = await fetch(`/api/tastytrade-alerts/${alertId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`Failed to delete alert: ${res.status}`);
    return await res.json();
  },

  async deleteAllTastytradeAlerts() {
    const res = await fetch('/api/tastytrade-alerts/all', { method: 'DELETE' });
    if (!res.ok) throw new Error(`Failed to delete all alerts: ${res.status}`);
    return await res.json();
  },

  async getReports(date = null) {
    const url = date ? `/api/reports?date=${encodeURIComponent(date)}` : '/api/reports';
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Failed to fetch reports: ${res.status}`);
    return await res.json();
  },

  async getReportBundle(date, ticker) {
    if (date && !ticker) {
      ticker = date;
      date = 'latest';
    }
    const d = date || 'latest';
    const res = await fetch(`/api/report/${encodeURIComponent(d)}/${encodeURIComponent(ticker)}`);
    if (!res.ok) throw new Error(`Failed to fetch report bundle: ${res.status}`);
    return await res.json();
  },

  async getTickerQuote(ticker) {
    const res = await fetch(`/api/quote/${encodeURIComponent(ticker)}`);
    if (!res.ok) throw new Error(`Failed to fetch quote: ${res.status}`);
    return await res.json();
  },

  async getQuotes(tickers = []) {
    const results = {};
    await Promise.all(tickers.map(async sym => {
      try {
        const q = await this.getTickerQuote(sym);
        if (q) results[sym] = q;
      } catch (e) {}
    }));
    return results;
  },

  async getOptionsFlow(ticker, refresh = false) {
    const url = `/api/options-flow/${encodeURIComponent(ticker)}${refresh ? '?refresh=true' : ''}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Failed to fetch options flow: ${res.status}`);
    return await res.json();
  },

  async getPositions() {
    const res = await fetch('/api/positions');
    if (!res.ok) throw new Error(`Failed to fetch positions: ${res.status}`);
    return await res.json();
  },

  async closePosition(ticker) {
    const res = await fetch(`/api/positions/${encodeURIComponent(ticker)}/close`, { method: 'POST' });
    if (!res.ok) throw new Error(`Failed to close position: ${res.status}`);
    return await res.json();
  },

  async flattenIntraday() {
    const res = await fetch('/api/positions/flatten-intraday', { method: 'POST' });
    if (!res.ok) throw new Error(`Failed to flatten intraday: ${res.status}`);
    return await res.json();
  },

  async simulate0DTE(payload) {
    const res = await fetch('/api/intraday/simulate-eval', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error(`Failed to evaluate 0DTE: ${res.status}`);
    return await res.json();
  },

  async triggerResearch(ticker, mode = 'full', date = null, force = false) {
    const res = await fetch('/api/research/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker, mode, date, force })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Failed to run research (${res.status})`);
    return data;
  },

  async syncAlerts() {
    const res = await fetch('/api/alerts/sync', { method: 'POST' });
    if (!res.ok) throw new Error(`Failed to trigger sync: ${res.status}`);
    return await res.json();
  },

  async getLogs(channel = 'all', jobId = null) {
    let url = `/api/logs?channel=${encodeURIComponent(channel)}`;
    if (jobId) url = `/api/logs?job_id=${encodeURIComponent(jobId)}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Failed to fetch logs: ${res.status}`);
    return await res.json();
  },

  async getTickers() {
    const res = await fetch('/api/tickers');
    if (!res.ok) throw new Error(`Failed to fetch tickers: ${res.status}`);
    return await res.json();
  }
};

