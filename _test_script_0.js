
    // Application State
    const State = {
      ticker: '',
      date: 'latest',
      reportData: null,
      activeTab: 'arb',
      copilotHistory: [],
      copilotSessionId: 'sess_' + Date.now() + '_' + Math.random().toString(36).substring(2, 7)
    };

    // Parse URL params (/research/TICKER, /research/TICKER/trades, /research/TICKER/DATE, or query params)
    function parseUrlParams() {
      const pathParts = window.location.pathname.split('/').filter(Boolean);
      let sym = '';
      let dt = '';

      if (pathParts.length >= 2 && pathParts[0].toLowerCase() === 'research') {
        sym = pathParts[1].toUpperCase();
        if (pathParts.length >= 3) {
          const sub = pathParts[2].toLowerCase();
          if (sub === 'trades') {
            State.activeTab = 'trades';
            State.explicitTab = true;
            dt = 'latest';
          } else if (/^\d{4}-\d{2}-\d{2}$/.test(sub)) {
            dt = sub;
          } else if (['arb', 'sum', 'ind', 'local', 'plan', 'chart', 'tv', 'flow', 'history'].includes(sub)) {
            State.activeTab = sub;
            State.explicitTab = true;
            dt = 'latest';
          }
        }
      }

      const urlParams = new URLSearchParams(window.location.search);
      if (!sym) sym = (urlParams.get('ticker') || urlParams.get('sym') || 'AAPL').toUpperCase();
      if (!dt) dt = urlParams.get('date') || 'latest';
      const qTab = (urlParams.get('tab') || '').toLowerCase();
      if (qTab) {
        State.activeTab = qTab;
        State.explicitTab = true;
      }

      State.ticker = sym;
      State.date = dt;
    }

    // Initialize Theme
    function initTheme() {
      const saved = localStorage.getItem('cockpit_theme') || 'light';
      document.documentElement.setAttribute('data-theme', saved);
      updateThemeBtn(saved);
    }

    function toggleTheme() {
      const current = document.documentElement.getAttribute('data-theme') || 'light';
      const next = current === 'light' ? 'dark' : 'light';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('cockpit_theme', next);
      updateThemeBtn(next);
    }

    function updateThemeBtn(theme) {
      const btn = document.getElementById('btn-theme-toggle');
      if (btn) btn.innerHTML = theme === 'light' ? '☀️' : '🌙';
    }

    // Load All 1,000 Constituents into quick datalist
    async function loadConstituentDatalist() {
      try {
        const res = await fetch('/api/tickers');
        if (res.ok) {
          const data = await res.json();
          const dl = document.getElementById('constituent-tickers');
          if (dl && data.tickers) {
            dl.innerHTML = data.tickers.map(t => `<option value="${t}">${t}</option>`).join('');
          }
        }
      } catch (e) {
        console.warn('Could not load constituent datalist:', e);
      }
    }

    // Load Company Name
    async function loadCompanyName(ticker) {
      try {
        const res = await fetch('/api/company-names');
        if (res.ok) {
          const map = await res.json();
          const name = map[ticker] || map[ticker.toUpperCase()] || '';
          if (name) {
            document.getElementById('hdr-company-name').innerText = name;
            document.getElementById('hdr-company-name').title = `${name} (${ticker})`;
          }
        }
      } catch (e) {}
    }

    function esc(s) {
      if (s === null || s === undefined) return '';
      return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }

    // Fetch and render report bundle
    async function loadReportBundle(ticker, date) {
      const contentEl = document.getElementById('dossier-content');
      contentEl.innerHTML = `<div style="padding:40px; text-align:center; color:var(--text-muted);">⏳ Loading research dossier for <strong>${esc(ticker)}</strong>...</div>`;

      document.getElementById('hdr-ticker-sym').innerText = `$${ticker}`;
      document.getElementById('copilot-ticker-badge').innerText = `$${ticker}`;
      document.getElementById('quick-ticker-input').value = ticker;
      document.getElementById('btn-open-cockpit').href = `/?ticker=${encodeURIComponent(ticker)}&modal=research`;

      loadCompanyName(ticker);

      try {
        const res = await fetch(`/api/report/${encodeURIComponent(date || 'latest')}/${encodeURIComponent(ticker)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        State.reportData = data;
        State.ticker = data.ticker || ticker;
        State.date = data.date || date;

        renderHeader(data);
        renderTacticalRibbon(data);
        renderDateSelector(data);

        // Determine default tab if not explicitly requested
        if (!State.explicitTab) {
          if (!data.has_deep_research && data.has_local_dossier) {
            State.activeTab = 'local';
          } else if (data.has_deep_research) {
            State.activeTab = 'arb';
          } else {
            State.activeTab = 'local';
          }
        }
        updateTradesCount(State.ticker);

        renderActiveTab();
      } catch (e) {
        if (State.activeTab === 'trades') {
          updateTradesCount(State.ticker);
          renderTradesTab(contentEl, State.ticker);
        } else {
          contentEl.innerHTML = `
            <div class="empty-state-box">
              <div class="empty-state-icon">⚠️</div>
              <div class="empty-state-title">Failed to Load Report</div>
              <div class="empty-state-desc">${e.message}</div>
              <button class="btn-action btn-primary-gradient" onclick="loadReportBundle(State.ticker, State.date)">Retry</button>
            </div>
          `;
        }
      }
    }

    function renderHeader(data) {
      // Live Price & Spot Diff
      const livePrice = data.live_price || data.spot_price;
      const spotPrice = data.spot_price;
      const livePriceEl = document.getElementById('hdr-live-price');
      const spotDiffEl = document.getElementById('hdr-spot-diff');

      if (livePrice) {
        livePriceEl.innerText = `$${Number(livePrice).toFixed(2)}`;
        if (spotPrice && Math.abs(livePrice - spotPrice) > 0.01) {
          const diff = livePrice - spotPrice;
          const diffPct = (diff / spotPrice) * 100;
          const sign = diff >= 0 ? '+' : '';
          const col = diff >= 0 ? '#10b981' : '#f43f5e';
          spotDiffEl.innerHTML = `<span style="color:${col}; font-weight:700;">${sign}$${diff.toFixed(2)} (${sign}${diffPct.toFixed(1)}%)</span> vs spot`;
        } else {
          spotDiffEl.innerText = '';
        }
      } else {
        livePriceEl.innerText = '--';
        spotDiffEl.innerText = '';
      }

      // Verdict & Conviction
      const wl = data.watch_levels || {};
      const verdict = (wl.verdict || (data.has_deep_research ? 'STALK' : 'UNRESEARCHED')).toUpperCase();
      const conviction = wl.conviction || '--';

      const vPill = document.getElementById('hdr-verdict-pill');
      vPill.innerText = verdict;
      vPill.className = 'verdict-badge';
      if (verdict.includes('ENTER') || verdict.includes('BUY')) vPill.classList.add('verdict-enter');
      else if (verdict.includes('STALK') || verdict.includes('RADAR')) vPill.classList.add('verdict-stalk');
      else if (verdict.includes('WATCH') || verdict.includes('MONITOR')) vPill.classList.add('verdict-watch');
      else vPill.classList.add('verdict-avoid');

      const cPill = document.getElementById('hdr-conviction-pill');
      cPill.innerText = conviction !== '--' ? `Conviction: ${conviction}/10` : 'Conviction: --';
    }

    function renderTacticalRibbon(data) {
      const ribbon = document.getElementById('tactical-ribbon');
      const wl = data.watch_levels;
      if (!wl) {
        ribbon.style.display = 'none';
        return;
      }

      const sp = wl.shares_plan || {};
      const op = wl.options_plan || {};
      const inv = wl.invalidation || {};

      const ezLow = sp.entry_zone_low;
      const ezHigh = sp.entry_zone_high;
      const stop = sp.tactical_stop || inv.price_level;
      const t1 = sp.target_1;
      const t2 = sp.target_2;

      if (!ezLow && !stop && !t1) {
        ribbon.style.display = 'none';
        return;
      }

      ribbon.style.display = 'grid';
      document.getElementById('tactical-entry').innerText = (ezLow && ezHigh) ? `$${Number(ezLow).toFixed(2)} – $${Number(ezHigh).toFixed(2)}` : '--';
      document.getElementById('tactical-stop').innerText = stop ? `$${Number(stop).toFixed(2)}` : '--';
      
      let tgText = '--';
      if (t1 && t2) tgText = `T1: $${Number(t1).toFixed(2)} | T2: $${Number(t2).toFixed(2)}`;
      else if (t1) tgText = `T1: $${Number(t1).toFixed(2)}`;
      document.getElementById('tactical-targets').innerText = tgText;

      document.getElementById('tactical-rr').innerText = wl.setup_lane ? `Lane: ${wl.setup_lane}` : 'Institutional Tactical Levels';
      document.getElementById('tactical-options').innerText = op.summary || op.structure || 'Cash/Shares Accumulation';
      document.getElementById('tactical-invalidation').innerText = inv.condition ? `Invalidation: ${inv.condition} ($${stop || '--'})` : '';
    }

    function renderDateSelector(data) {
      const sel = document.getElementById('report-date-select');
      const dates = data.available_dates || [data.date || 'latest'];
      sel.innerHTML = dates.map(d => `<option value="${d}" ${d === data.date ? 'selected' : ''}>${d}</option>`).join('');

      const histCountEl = document.getElementById('tab-hist-count');
      if (histCountEl) {
        histCountEl.innerText = (data.historical_timeline && data.historical_timeline.length) || dates.length || 0;
      }
    }

    function switchReportDate(newDate) {
      State.date = newDate;
      window.history.pushState({}, '', `/research/${State.ticker}/${newDate}`);
      loadReportBundle(State.ticker, newDate);
    }

    function switchTab(tab) {
      State.activeTab = tab;
      document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.id === `tab-${tab}-btn`);
      });
      if (tab === 'trades') {
        window.history.pushState({}, '', `/research/${State.ticker}/trades`);
      } else {
        window.history.pushState({}, '', `/research/${State.ticker}`);
      }
      renderActiveTab();
    }

    function renderActiveTab() {
      const tab = State.activeTab;
      const data = State.reportData;
      const bodyEl = document.getElementById('dossier-content');
      if (!bodyEl) return;

      // Ensure tab button reflects active state
      document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.id === `tab-${tab}-btn`);
      });

      if (tab === 'trades') {
        renderTradesTab(bodyEl, State.ticker);
        return;
      }

      if (!data) return;

      if (tab === 'arb') {
        const content = data.arbitration_md || data.summary_md || data.independent_md || data.local_dossier_md;
        if (content) {
          bodyEl.innerHTML = parseMarkdown(content);
        } else {
          renderPendingState(bodyEl, 'Senior PM Arbitration', 'Multi-model arbitration has not been synthesized yet for this date.');
        }
      } else if (tab === 'sum') {
        const content = data.summary_md || data.arbitration_md || data.independent_md || data.local_dossier_md;
        if (content) {
          bodyEl.innerHTML = parseMarkdown(content);
        } else {
          renderPendingState(bodyEl, 'Model A (Synthesis)', 'Model A synthesis has not been generated yet for this date.');
        }
      } else if (tab === 'ind') {
        const content = data.independent_md || data.arbitration_md || data.summary_md || data.local_dossier_md;
        if (content) {
          bodyEl.innerHTML = parseMarkdown(content);
        } else {
          renderPendingState(bodyEl, 'Model B (Independent)', 'Model B independent thesis is not available for this date.');
        }
      } else if (tab === 'local') {
        const content = data.local_dossier_md || data.summary_md || data.arbitration_md;
        if (content) {
          bodyEl.innerHTML = parseMarkdown(content);
        } else {
          renderPendingState(bodyEl, 'Local Dossier', 'No local triage dossier recorded.');
        }
      } else if (tab === 'plan') {
        renderTradePlanTab(bodyEl, data);
      } else if (tab === 'chart') {
        renderChartsTab(bodyEl, data);
      } else if (tab === 'tv') {
        renderTradingViewTab(bodyEl, data);
      } else if (tab === 'flow') {
        renderFlowTab(bodyEl, data);
      } else if (tab === 'history') {
        renderHistoryTab(bodyEl, data);
      }
    }

    function renderPendingState(el, title, desc) {
      el.innerHTML = `
        <div class="empty-state-box">
          <div class="empty-state-icon">🔬</div>
          <div class="empty-state-title">${title} Pending</div>
          <div class="empty-state-desc">${desc}</div>
          <div style="display:flex; justify-content:center; gap:10px;">
            <button class="btn-action btn-primary-gradient" onclick="triggerDeepResearch()">⚡ Run Deep Research Now</button>
            <button class="btn-action btn-secondary" onclick="switchTab('local')">📑 View Local Dossier</button>
          </div>
          <div class="progress-box" id="job-progress-box"></div>
        </div>
      `;
    }

    function renderChartsTab(el, data) {
      const zoomUrl = data.zoom_chart_url;
      const plainUrl = data.plain_chart_url;

      if (!zoomUrl && !plainUrl) {
        el.innerHTML = `
          <div class="empty-state-box">
            <div class="empty-state-icon">📸</div>
            <div class="empty-state-title">No Scraped Chart Screenshots</div>
            <div class="empty-state-desc">Screenshots are scraped via Playwright during Swing Research passes.</div>
            <button class="btn-action btn-primary-gradient" onclick="triggerDeepResearch()">⚡ Scrape &amp; Research Now</button>
          </div>
        `;
        return;
      }

      el.innerHTML = `
        <div class="chart-container">
          ${zoomUrl ? `
            <div class="chart-card">
              <div class="chart-card-header">
                <span>🔍 90-Day Zoom Tactical Chart (with Indicators &amp; Volume Nodes)</span>
                <span style="font-size:11px; color:var(--text-muted);">Click image to expand full-screen</span>
              </div>
              <div class="chart-img-wrap" onclick="openLightbox('${zoomUrl}')">
                <img src="${zoomUrl}" alt="${data.ticker} 90d Zoom Chart">
              </div>
            </div>
          ` : ''}
          ${plainUrl ? `
            <div class="chart-card">
              <div class="chart-card-header">
                <span>📊 Naked Daily Price Action Chart (1-Year Macro Structure)</span>
                <span style="font-size:11px; color:var(--text-muted);">Click image to expand full-screen</span>
              </div>
              <div class="chart-img-wrap" onclick="openLightbox('${plainUrl}')">
                <img src="${plainUrl}" alt="${data.ticker} Naked Chart">
              </div>
            </div>
          ` : ''}
        </div>
      `;
    }

    function renderTradingViewTab(el, data) {
      const sym = data.ticker || State.ticker;
      el.innerHTML = `
        <div style="height: 100%; min-height: 600px; display:flex; flex-direction:column;">
          <iframe 
            src="https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(sym)}&interval=D&hidesidetoolbar=0&symboledit=1&saveimage=1&toolbarbg=f1f3f6&studies=%5B%5D&theme=${document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light'}&style=1&timezone=America%2FDenver" 
            style="width: 100%; height: 650px; border: 1px solid var(--border); border-radius: var(--radius-md);" 
            frameborder="0" 
            allowfullscreen>
          </iframe>
        </div>
      `;
    }

    function renderTradePlanTab(el, data) {
      const wl = data.watch_levels || {};
      const sp = wl.shares_plan || {};
      const op = wl.options_plan || {};
      const ideas = data.trade_ideas || [];

      el.innerHTML = `
        <div style="display:flex; flex-direction:column; gap:20px; max-width:850px; margin:0 auto;">
          <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:var(--radius-lg); padding:20px;">
            <h2 style="margin-top:0;">🎯 Institutional Suggested Trade Blueprint</h2>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:14px;">
              <div>
                <div style="font-size:11px; font-weight:700; color:var(--text-muted);">EXECUTION TYPE</div>
                <div style="font-size:14px; font-weight:800; font-family:var(--font-mono); color:var(--cyan);">${sp.entry_type || 'n/a'}</div>
              </div>
              <div>
                <div style="font-size:11px; font-weight:700; color:var(--text-muted);">SETUP LANE</div>
                <div style="font-size:14px; font-weight:800; font-family:var(--font-mono); color:var(--text-main);">${wl.setup_lane || 'n/a'}</div>
              </div>
              <div>
                <div style="font-size:11px; font-weight:700; color:var(--text-muted);">ENTRY ACCUMULATION ZONE</div>
                <div style="font-size:14px; font-weight:800; font-family:var(--font-mono); color:var(--cyan);">$${sp.entry_zone_low ? Number(sp.entry_zone_low).toFixed(2) : '--'} – $${sp.entry_zone_high ? Number(sp.entry_zone_high).toFixed(2) : '--'}</div>
              </div>
              <div>
                <div style="font-size:11px; font-weight:700; color:var(--text-muted);">TACTICAL STOP LOSS</div>
                <div style="font-size:14px; font-weight:800; font-family:var(--font-mono); color:var(--rose);">$${sp.tactical_stop ? Number(sp.tactical_stop).toFixed(2) : '--'}</div>
              </div>
            </div>
          </div>

          ${ideas.length ? `
            <div>
              <h3>💡 Generated Execution Ideas</h3>
              <div style="display:flex; flex-direction:column; gap:10px; margin-top:10px;">
                ${ideas.map(idea => `
                  <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:var(--radius-md); padding:12px 16px;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                      <span style="font-weight:800; font-size:13px; color:var(--text-main);">${escapeHtml(idea.title)}</span>
                      <span class="pill" style="font-size:10px; font-weight:700;">${escapeHtml(idea.category)}</span>
                    </div>
                    <div style="font-size:12px; color:var(--text-muted); margin-top:6px;">${escapeHtml(idea.action)}</div>
                    <div style="font-family:var(--font-mono); font-size:11px; color:var(--cyan); margin-top:6px;">${escapeHtml(idea.metrics)}</div>
                  </div>
                `).join('')}
              </div>
            </div>
          ` : ''}
        </div>
      `;
    }

    async function renderFlowTab(el, data) {
      el.innerHTML = `<div style="padding:40px; text-align:center; color:var(--text-muted);">⏳ Loading options flow and volatility metrics...</div>`;
      try {
        const res = await fetch(`/api/options-flow/${encodeURIComponent(data.ticker)}`);
        if (res.ok) {
          const flow = await res.json();
          el.innerHTML = `
            <div style="max-width:850px; margin:0 auto;">
              <h2>🔥 Real-Time Options Flow &amp; Volatility Metrics</h2>
              <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px; margin:16px 0;">
                <div class="tactical-card">
                  <div class="tactical-card-label">IV Rank</div>
                  <div class="tactical-card-val" style="color:var(--amber);">${flow.iv_rank !== null && flow.iv_rank !== undefined ? Number(flow.iv_rank).toFixed(1) + '%' : '--'}</div>
                </div>
                <div class="tactical-card">
                  <div class="tactical-card-label">30-Day HV</div>
                  <div class="tactical-card-val">${flow.hv_30 !== null && flow.hv_30 !== undefined ? (Number(flow.hv_30) * 100).toFixed(1) + '%' : '--'}</div>
                </div>
                <div class="tactical-card">
                  <div class="tactical-card-label">IV-HV Spread</div>
                  <div class="tactical-card-val" style="color:var(--cyan);">${flow.iv_hv_spread !== null && flow.iv_hv_spread !== undefined ? flow.iv_hv_spread : '--'}</div>
                </div>
              </div>
              <pre style="background:var(--bg-subtle); padding:16px; border-radius:var(--radius-md); font-family:var(--font-mono); font-size:11.5px; overflow-x:auto;"><code>${JSON.stringify(flow, null, 2)}</code></pre>
            </div>
          `;
        } else {
          el.innerHTML = `<div style="padding:40px; text-align:center; color:var(--text-muted);">No options flow data available.</div>`;
        }
      } catch (e) {
        el.innerHTML = `<div style="padding:40px; text-align:center; color:var(--rose);">Failed loading flow: ${e.message}</div>`;
      }
    }

    function renderHistoryTab(el, data) {
      const timeline = data.historical_timeline || [];
      if (!timeline.length) {
        el.innerHTML = `<div style="padding:40px; text-align:center; color:var(--text-muted);">No historical research versions recorded for ${data.ticker}.</div>`;
        return;
      }

      el.innerHTML = `
        <div style="max-width:850px; margin:0 auto;">
          <h2>📜 Historical Research Dossier Timeline</h2>
          <div style="display:flex; flex-direction:column; gap:12px; margin-top:16px;">
            ${timeline.map(item => `
              <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:var(--radius-md); padding:14px 18px; display:flex; justify-content:space-between; align-items:center; cursor:pointer;" onclick="switchReportDate('${escapeHtml(item.date)}')">
                <div>
                  <div style="display:flex; align-items:center; gap:8px;">
                    <span style="font-family:var(--font-mono); font-weight:800; font-size:13px; color:var(--cyan);">${escapeHtml(item.date)}</span>
                    <span class="pill" style="font-size:10px; font-weight:700;">${escapeHtml(item.verdict || 'REVIEW')}</span>
                    <span style="font-family:var(--font-mono); font-size:11.5px; color:var(--text-muted);">$${item.spot ? Number(item.spot).toFixed(2) : '--'}</span>
                  </div>
                  <div style="font-size:11.5px; color:var(--text-muted); margin-top:4px;">${escapeHtml(item.preview || item.summary || 'Click to view full dossier for this session.')}</div>
                </div>
                <button class="btn-action btn-secondary" style="font-size:11px;">Inspect ➔</button>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }

    async function updateTradesCount(ticker) {
      if (!ticker) return;
      try {
        const res = await fetch(`/api/research/${encodeURIComponent(ticker)}/trades`);
        if (res.ok) {
          const data = await res.json();
          const countEl = document.getElementById('tab-trades-count');
          if (countEl) countEl.innerText = data.total_trades || 0;
        }
      } catch (e) {}
    }

    async function renderTradesTab(el, ticker) {
      el.innerHTML = `<div style="padding:40px; text-align:center; color:var(--text-muted);">⏳ Loading suggested trades for <strong>$${esc(ticker)}</strong>...</div>`;
      try {
        const res = await fetch(`/api/research/${encodeURIComponent(ticker)}/trades`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const trades = data.trades || [];
        const summary = data.summary || {};

        const countEl = document.getElementById('tab-trades-count');
        if (countEl) countEl.innerText = trades.length;

        if (trades.length === 0) {
          el.innerHTML = `
            <div class="empty-state-box">
              <div class="empty-state-icon">💼</div>
              <div class="empty-state-title">No Suggested Trades for $${esc(ticker)}</div>
              <div class="empty-state-desc">No swing research suggestions, alert ingestor positions, or broker executions recorded yet for $${esc(ticker)}.</div>
              <div style="display:flex; justify-content:center; gap:10px;">
                <button class="btn-action btn-primary-gradient" onclick="triggerDeepResearch()">⚡ Run Deep Research</button>
                <button class="btn-action btn-secondary" onclick="switchTab('plan')">🎯 View Suggested Plan</button>
              </div>
            </div>
          `;
          return;
        }

        const tableRows = trades.map(t => {
          const isShort = (t.side === 'SHORT');
          const sideBadge = isShort
            ? '<span class="pill red" style="font-size:9.5px; padding:1px 5px; font-weight:800;">SHORT</span>'
            : '<span class="pill green" style="font-size:9.5px; padding:1px 5px; font-weight:800;">LONG</span>';

          const st = (t.status || 'PENDING').toUpperCase();
          let stBadge = '<span class="pill" style="font-size:9.5px; padding:1px 6px;">CLOSED</span>';
          if (st === 'WIN') {
            stBadge = '<span class="pill green" style="font-size:9.5px; padding:2px 7px; font-weight:800; box-shadow:0 0 6px rgba(16,185,129,0.3);">🏆 WIN</span>';
          } else if (st === 'LOSS') {
            stBadge = '<span class="pill red" style="font-size:9.5px; padding:2px 7px; font-weight:800;">🛑 LOSS</span>';
          } else if (st === 'ACTIVE' || st === 'FILLED' || st === 'OPEN') {
            stBadge = '<span class="pill green pulse" style="font-size:9.5px; padding:2px 7px; font-weight:800;">ACTIVE</span>';
          } else if (st === 'PENDING') {
            stBadge = '<span class="pill amber" style="font-size:9.5px; padding:1px 6px; font-weight:700;">⏳ PENDING</span>';
          } else if (st.includes('REJECTED')) {
            stBadge = '<span class="pill" style="font-size:9px; padding:1px 5px; opacity:0.7;">EXCLUDED</span>';
          } else if (st.includes('TARGET')) {
            stBadge = '<span class="pill green" style="font-size:9.5px; padding:1px 6px; font-weight:800;">TARGET HIT</span>';
          } else if (st.includes('STOP')) {
            stBadge = '<span class="pill red" style="font-size:9.5px; padding:1px 6px; font-weight:800;">STOPPED</span>';
          }

          let pnlHtml = '<span style="color:var(--text-muted);">--</span>';
          if (t.r_net !== undefined && t.r_net !== null) {
            const rVal = Number(t.r_net);
            const rCol = rVal >= 0 ? '#10b981' : '#f43f5e';
            pnlHtml = `<span style="font-weight:800; font-family:var(--font-mono); font-size:12px; color:${rCol};">${rVal >= 0 ? '+' : ''}${rVal.toFixed(2)}R</span>`;
            if (t.gross_r !== undefined && t.gross_r !== null && t.gross_r !== t.r_net) {
              pnlHtml += `<div style="font-size:9.5px; color:var(--text-muted);">Gross: ${Number(t.gross_r).toFixed(2)}R</div>`;
            }
          } else if (t.pnl_dollars !== undefined && t.pnl_dollars !== null) {
            const dVal = Number(t.pnl_dollars);
            const dCol = dVal >= 0 ? '#10b981' : '#f43f5e';
            pnlHtml = `<span style="font-weight:800; font-family:var(--font-mono); font-size:12px; color:${dCol};">${dVal >= 0 ? '+' : ''}$${dVal.toFixed(2)}</span>`;
          }

          let entryRange = '--';
          if (t.entry_low && t.entry_high) {
            entryRange = (t.entry_low === t.entry_high) ? `$${Number(t.entry_low).toFixed(2)}` : `$${Number(t.entry_low).toFixed(2)} - $${Number(t.entry_high).toFixed(2)}`;
          } else if (t.entry_low) {
            entryRange = `$${Number(t.entry_low).toFixed(2)}`;
          } else if (t.breakout_level) {
            entryRange = `BO: $${Number(t.breakout_level).toFixed(2)}`;
          }

          const fillPrice = t.fill_price ? `$${Number(t.fill_price).toFixed(2)}` : '--';
          const exitPrice = t.exit_price ? `$${Number(t.exit_price).toFixed(2)}` : '--';
          const stopPrice = t.stop_loss ? `$${Number(t.stop_loss).toFixed(2)}` : '--';
          const targetPrice = t.target_1 ? `$${Number(t.target_1).toFixed(2)}` : '--';
          const t2Price = t.target_2 ? `$${Number(t.target_2).toFixed(2)}` : '';

          return `
            <tr style="border-bottom:1px solid var(--border);">
              <td style="padding:10px 12px; font-weight:700;">
                <div style="display:flex; align-items:center; gap:6px;">
                  <span style="color:var(--text-main); font-size:12.5px;">${esc(t.name)}</span>
                  <span class="pill" style="font-size:8.5px; padding:1px 5px; color:var(--text-muted);">${esc(t.source)}</span>
                  ${t.gate_status === 'PASS' ? '<span class="pill green" style="font-size:8px; padding:0 4px;">GATE PASS</span>' : (t.gate_status ? `<span class="pill" style="font-size:8px; padding:0 4px; opacity:0.7;">${esc(t.gate_status)}</span>` : '')}
                </div>
                <div style="font-size:10.5px; color:var(--text-muted); margin-top:2px;">Signal: ${esc((t.date || t.fill_date || '').substring(0, 10))} ${t.entry_type ? `· ${t.entry_type}` : ''}</div>
                ${t.notes ? `<div style="font-size:10px; color:var(--text-muted); margin-top:2px; font-style:italic;">${esc(t.notes)}</div>` : ''}
              </td>
              <td style="text-align:center; padding:10px 6px;">${sideBadge}</td>
              <td style="text-align:center; padding:10px 6px;">${stBadge}</td>
              <td style="font-family:var(--font-mono); font-size:11.5px; white-space:nowrap; padding:10px 8px;">
                <div style="font-weight:700; color:var(--cyan);">${entryRange}</div>
                ${stopPrice !== '--' ? `<span style="color:var(--rose); font-size:10.5px;">Stop: ${stopPrice}</span>` : ''}
                ${targetPrice !== '--' ? `<span style="color:var(--emerald); font-size:10.5px; margin-left:4px;">T1: ${targetPrice}</span>` : ''}
                ${t2Price ? `<span style="color:var(--emerald); font-size:10.5px; margin-left:4px;">T2: ${t2Price}</span>` : ''}
              </td>
              <td style="font-family:var(--font-mono); font-size:11.5px; white-space:nowrap; padding:10px 8px;">
                ${t.fill_price ? `<div style="font-weight:800; color:var(--text-main);">${fillPrice}</div><div style="font-size:10px; color:var(--text-muted);">${esc((t.fill_date || '').substring(0, 10))}</div>` : '<span style="color:var(--text-muted);">Unfilled</span>'}
              </td>
              <td style="font-family:var(--font-mono); font-size:11.5px; white-space:nowrap; padding:10px 8px;">
                ${t.exit_price ? `<div style="font-weight:800; color:var(--text-main);">${exitPrice}</div><div style="font-size:10px; color:var(--text-muted);">${esc((t.exit_date || '').substring(0, 10))}</div>` : '<span style="color:var(--text-muted);">--</span>'}
              </td>
              <td style="font-family:var(--font-mono); text-align:right; padding:10px 10px;">
                ${pnlHtml}
                ${t.bars_held ? `<div style="font-size:9.5px; color:var(--text-muted);">${t.bars_held} bars</div>` : ''}
              </td>
              <td style="font-size:11px; color:var(--text-muted); padding:10px 10px;">
                <div style="font-weight:600; color:var(--text-main);">${esc(t.exit_reason || (t.gate_reasons || '--'))}</div>
                ${t.mae_r !== undefined && t.mae_r !== null ? `<div style="font-size:9.5px; color:var(--text-muted);">MAE: ${Number(t.mae_r).toFixed(2)}R</div>` : ''}
              </td>
            </tr>
          `;
        }).join('');

        el.innerHTML = `
          <div style="display:flex; flex-direction:column; gap:16px;">
            <!-- Performance Scorecard -->
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(130px, 1fr)); gap:10px;">
              <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:var(--radius-md); padding:10px 14px;">
                <div style="font-size:10.5px; color:var(--text-muted); font-weight:700; text-transform:uppercase;">Total Suggestions</div>
                <div style="font-size:18px; font-weight:900; color:var(--cyan); font-family:var(--font-mono);">${summary.total_suggestions || trades.length}</div>
              </div>
              <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:var(--radius-md); padding:10px 14px;">
                <div style="font-size:10.5px; color:var(--text-muted); font-weight:700; text-transform:uppercase;">Win Rate</div>
                <div style="font-size:18px; font-weight:900; color:${(summary.win_rate_pct || 0) >= 50 ? 'var(--emerald)' : 'var(--amber)'}; font-family:var(--font-mono);">${summary.win_rate_pct || 0}%</div>
                <div style="font-size:9.5px; color:var(--text-muted);">${summary.wins || 0}W / ${summary.losses || 0}L (${summary.closed_trades || 0} closed)</div>
              </div>
              <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:var(--radius-md); padding:10px 14px;">
                <div style="font-size:10.5px; color:var(--text-muted); font-weight:700; text-transform:uppercase;">Total Net R</div>
                <div style="font-size:18px; font-weight:900; color:${(summary.total_r_net || 0) >= 0 ? 'var(--emerald)' : 'var(--rose)'}; font-family:var(--font-mono);">${(summary.total_r_net || 0) >= 0 ? '+' : ''}${summary.total_r_net || 0}R</div>
              </div>
              <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:var(--radius-md); padding:10px 14px;">
                <div style="font-size:10.5px; color:var(--text-muted); font-weight:700; text-transform:uppercase;">Avg R / Trade</div>
                <div style="font-size:18px; font-weight:900; color:${(summary.avg_r_net || 0) >= 0 ? 'var(--emerald)' : 'var(--rose)'}; font-family:var(--font-mono);">${(summary.avg_r_net || 0) >= 0 ? '+' : ''}${summary.avg_r_net || 0}R</div>
              </div>
              <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:var(--radius-md); padding:10px 14px;">
                <div style="font-size:10.5px; color:var(--text-muted); font-weight:700; text-transform:uppercase;">Active / Pending</div>
                <div style="font-size:18px; font-weight:900; color:var(--text-main); font-family:var(--font-mono);">${summary.active_trades || 0} <span style="font-size:12px; font-weight:600; color:var(--text-muted);">/ ${summary.pending_trades || 0}</span></div>
              </div>
            </div>

            <!-- Ledger Table -->
            <div style="background:var(--bg-surface); border:1px solid var(--border); border-radius:var(--radius-md); overflow:hidden;">
              <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px; padding:10px 16px; background:var(--bg-subtle); border-bottom:1px solid var(--border);">
                <div style="display:flex; align-items:center; gap:8px;">
                  <span style="font-size:13px; font-weight:800; color:var(--cyan); font-family:var(--font-heading);">💼 Suggested Trade Setups &amp; History for $${esc(ticker)}</span>
                  <span class="pill" style="font-size:10px; font-weight:700;">${trades.length} Setups</span>
                </div>
                <button class="btn-action btn-secondary" onclick="renderTradesTab(document.getElementById('dossier-content'), State.ticker)" style="font-size:11px;">🔄 Refresh</button>
              </div>

              <div style="overflow-x:auto;">
                <table class="data-table" style="width:100%; border-collapse:collapse; font-size:12px;">
                  <thead>
                    <tr style="background:var(--bg-subtle); border-bottom:1px solid var(--border); font-size:10.5px; text-transform:uppercase; color:var(--text-muted);">
                      <th style="text-align:left; padding:8px 10px;">Setup &amp; Signal Date</th>
                      <th style="text-align:center; padding:8px 6px;">Side</th>
                      <th style="text-align:center; padding:8px 6px;">Status</th>
                      <th style="text-align:left; padding:8px 8px;">Planned Levels (Entry / Stop / T1)</th>
                      <th style="text-align:left; padding:8px 8px;">Actual Fill</th>
                      <th style="text-align:left; padding:8px 8px;">Exit</th>
                      <th style="text-align:right; padding:8px 10px;">PnL (R-Return)</th>
                      <th style="text-align:left; padding:8px 10px;">Exit Reason / Note</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${tableRows}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        `;
      } catch (e) {
        el.innerHTML = `
          <div class="empty-state-box">
            <div class="empty-state-icon">⚠️</div>
            <div class="empty-state-title">Failed to Load Suggested Trades</div>
            <div class="empty-state-desc">${e.message}</div>
            <button class="btn-action btn-primary-gradient" onclick="renderTradesTab(document.getElementById('dossier-content'), State.ticker)">Retry</button>
          </div>
        `;
      }
    }

    // Markdown Parser Wrapper
    function parseMarkdown(mdText) {
      if (!mdText) return '';
      let cleanText = String(mdText).trim();

      // Strip raw watch levels JSON if present at top
      let rawLevelsJson = null;
      try {
        const wlMatch = cleanText.match(/```(?:json)?(?::watch_levels|\s+watch_levels)?\s*(\{[\s\S]*?"shares_plan"[\s\S]*?\})\s*```/);
        if (wlMatch) {
          rawLevelsJson = wlMatch[1];
          cleanText = cleanText.replace(wlMatch[0], '').trim();
        }
      } catch (e) {}

      let html = '';
      try {
        if (window.marked && typeof window.marked.parse === 'function') {
          html = window.marked.parse(cleanText, { breaks: true, gfm: true });
        } else if (typeof window.marked === 'function') {
          html = window.marked(cleanText, { breaks: true, gfm: true });
        } else {
          html = cleanText.replace(/\n/g, '<br/>');
        }
      } catch (err) {
        console.error('Markdown parse error:', err);
        html = cleanText.replace(/\n/g, '<br/>');
      }

      if (rawLevelsJson) {
        html += `
          <details style="margin-top:32px; border:1px solid var(--border); border-radius:6px; background:var(--bg-subtle); padding:10px 14px; font-size:11.5px;">
            <summary style="cursor:pointer; font-weight:700; color:var(--text-muted); user-select:none;">🔧 Raw Tactical Levels JSON (Extracted for Watchlist)</summary>
            <pre style="margin-top:10px; padding:12px; background:rgba(0,0,0,0.4); border-radius:4px; overflow-x:auto;"><code class="language-json">${esc(rawLevelsJson)}</code></pre>
          </details>
        `;
      }
      return html;
    }

    // Lightbox Controls
    function openLightbox(url) {
      const modal = document.getElementById('lightbox-modal');
      const img = document.getElementById('lightbox-img');
      img.src = url;
      modal.style.display = 'flex';
    }

    function closeLightbox() {
      document.getElementById('lightbox-modal').style.display = 'none';
    }

    // Real-Time Price Polling
    async function refreshLivePrice() {
      try {
        const res = await fetch(`/api/quote/${encodeURIComponent(State.ticker)}`);
        if (res.ok) {
          const q = await res.json();
          if (q.price) {
            document.getElementById('hdr-live-price').innerText = `$${Number(q.price).toFixed(2)}`;
            if (State.reportData && State.reportData.spot_price) {
              const diff = q.price - State.reportData.spot_price;
              const diffPct = (diff / State.reportData.spot_price) * 100;
              const sign = diff >= 0 ? '+' : '';
              const col = diff >= 0 ? '#10b981' : '#f43f5e';
              document.getElementById('hdr-spot-diff').innerHTML = `<span style="color:${col}; font-weight:700;">${sign}$${diff.toFixed(2)} (${sign}${diffPct.toFixed(1)}%)</span>`;
            }
          }
        }
      } catch (e) {}
    }

    // Trigger Research Run
    async function triggerDeepResearch() {
      const btn = document.getElementById('btn-run-deep');
      if (btn) btn.disabled = true;

      const pBox = document.getElementById('job-progress-box') || createProgressBox();
      pBox.style.display = 'block';
      pBox.innerHTML = `<div>🚀 Dispatched Deep Research pass for $${State.ticker}...</div>`;

      try {
        const res = await fetch('/api/research/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ticker: State.ticker, mode: 'full', force: true })
        });
        const data = await res.json();
        pBox.innerHTML += `<div>✅ Job Queued: ${data.job_id || 'Running'}. Polling execution...</div>`;

        // Poll for completion
        let pollCount = 0;
        const poller = setInterval(async () => {
          pollCount++;
          try {
            const lRes = await fetch('/api/logs');
            if (lRes.ok) {
              const lData = await lRes.json();
              const logLines = lData.lines || lData.logs;
              if (logLines && Array.isArray(logLines)) {
                pBox.innerHTML = logLines.slice(-12).map(l => `<div>${l}</div>`).join('');
                pBox.scrollTop = pBox.scrollHeight;
              }
            }
          } catch (e) {}

          if (pollCount > 60) {
            clearInterval(poller);
            if (btn) btn.disabled = false;
            loadReportBundle(State.ticker, State.date);
          }
        }, 2000);
      } catch (e) {
        pBox.innerHTML += `<div style="color:var(--rose);">❌ Error triggering research: ${e.message}</div>`;
        if (btn) btn.disabled = false;
      }
    }

    function createProgressBox() {
      let b = document.getElementById('job-progress-box');
      if (!b) {
        b = document.createElement('div');
        b.id = 'job-progress-box';
        b.className = 'progress-box';
        document.getElementById('dossier-content').appendChild(b);
      }
      return b;
    }

    // Sync Tastytrade Alerts
    async function syncTastytradeAlerts() {
      try {
        const res = await fetch(`/api/watchlist/sync-tt?ticker=${encodeURIComponent(State.ticker)}`, { method: 'POST' });
        if (res.ok) {
          alert(`✅ Registered Tastytrade cloud quote alerts for $${State.ticker}! Check your Tastytrade mobile app.`);
        } else {
          alert('Failed to register TT alerts: Server error');
        }
      } catch (e) {
        alert(`Error: ${e.message}`);
      }
    }

    // Copilot Controls
    function toggleCopilot() {
      const pane = document.getElementById('copilot-pane');
      pane.classList.toggle('collapsed');
    }

    function handleCopilotKey(e) {
      if (e.key === 'Enter') submitCopilotChat();
    }

    function sendCopilotPrompt(text) {
      document.getElementById('copilot-input').value = text;
      submitCopilotChat();
    }

    async function submitCopilotChat() {
      const input = document.getElementById('copilot-input');
      const q = (input.value || '').trim();
      if (!q) return;
      input.value = '';

      const msgs = document.getElementById('copilot-messages');
      msgs.innerHTML += `<div class="chat-bubble chat-user">${escapeHtml(q)}</div>`;
      msgs.innerHTML += `<div class="chat-bubble chat-assistant" id="copilot-loading-bubble">⏳ Thinking...</div>`;
      msgs.scrollTop = msgs.scrollHeight;

      try {
        const res = await fetch('/api/copilot/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompt: q,
            ticker: State.ticker,
            date: State.date,
            session_id: State.copilotSessionId,
            history: State.copilotHistory
          })
        });

        const loadingBubble = document.getElementById('copilot-loading-bubble');
        if (loadingBubble) loadingBubble.remove();

        if (res.ok) {
          const ans = await res.json();
          const ansText = ans.response || ans.answer || ans.content || 'No response';
          msgs.innerHTML += `<div class="chat-bubble chat-assistant">${parseMarkdown(ansText)}</div>`;
          State.copilotHistory.push({ role: 'user', content: q });
          State.copilotHistory.push({ role: 'assistant', content: ansText });
        } else {
          msgs.innerHTML += `<div class="chat-bubble chat-assistant" style="color:var(--rose);">Error communicating with Copilot.</div>`;
        }
      } catch (e) {
        const loadingBubble = document.getElementById('copilot-loading-bubble');
        if (loadingBubble) loadingBubble.remove();
        msgs.innerHTML += `<div class="chat-bubble chat-assistant" style="color:var(--rose);">Request failed: ${e.message}</div>`;
      }
      msgs.scrollTop = msgs.scrollHeight;
    }

    function handleSwitcherKey(e) {
      if (e.key === 'Enter') {
        const val = (e.target.value || '').trim().toUpperCase();
        if (val) {
          const sub = State.activeTab === 'trades' ? '/trades' : '';
          window.location.href = `/research/${val}${sub}`;
        }
      }
    }

    function escapeHtml(str) {
      return String(str || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }

    // App Startup
    document.addEventListener('DOMContentLoaded', () => {
      initTheme();
      parseUrlParams();
      loadConstituentDatalist();
      loadReportBundle(State.ticker, State.date);

      // Listen for browser Back/Forward navigation
      window.addEventListener('popstate', () => {
        parseUrlParams();
        renderActiveTab();
      });

      // Auto poll live price every 15 seconds
      setInterval(refreshLivePrice, 15000);
    });
  