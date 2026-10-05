/**
 * Status and Hardware Operations Controller
 */
window.AppStatus = {
  // Session filter for the research-queue panels. Empty means "all dates" (the live view).
  _queueDate: '',
  _queueDates: [],
  setQueueDate(d) {
    this._queueDate = d || '';
    this.loadJobs();
  },

  _sessionRows: [],

  _todayStr() {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  },

  _shiftMonth(delta) {
    const now = new Date();
    const base = (!delta || !this._calMonth)
      ? new Date(now.getFullYear(), now.getMonth(), 1, 12)
      : new Date(`${this._calMonth}-01T12:00:00`);
    base.setMonth(base.getMonth() + (delta || 0));
    this._calMonth = `${base.getFullYear()}-${String(base.getMonth() + 1).padStart(2, '0')}`;
    this.renderSessionHistory(this._sessionRows || []);
  },

  _todayCard(sessions, esc) {
    const today = this._todayStr();
    const s = sessions.find((x) => x.session === today) || null;
    const selected = this._queueDate === today;
    const alerts = s ? (s.alerts || 0) : 0;
    const intraday = s ? (s.intraday || 0) : 0;
    const graded = s ? (s.graded || 0) : 0;
    const deepFail = s ? (s.deep_failed || 0) : 0;
    const live = s ? ((s.deep_running || 0) + (s.deep_queued || 0)) : 0;

    const statusTxt = !s
      ? '<span style="color:var(--text-muted);">no alerts yet</span>'
      : (graded >= alerts && alerts > 0)
        ? `<span style="color:var(--green); font-weight:800;">100% graded</span>`
        : `<span style="color:var(--amber); font-weight:800;">${alerts - graded} ungraded</span>`;

    return `
      <div onclick="AppStatus.openSessionModal('${today}')"
           title="Open ${today}'s queue"
           style="flex:1; min-width:210px; cursor:pointer; padding:10px 12px; border-radius:8px;
                  border:1px solid ${selected ? 'var(--cyan)' : 'var(--border)'};
                  background:${selected ? 'rgba(56,189,248,0.10)' : 'var(--bg-subtle)'};">
        <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
          <span style="font-size:10px; font-weight:800; letter-spacing:0.6px; color:var(--cyan);">TODAY</span>
          <span style="font-family:var(--font-mono); font-size:10px; color:var(--text-muted);">${esc(today)}</span>
        </div>
        <div style="font-size:20px; font-weight:900; font-family:var(--font-mono);
                    color:var(--text-main); margin:2px 0 4px;">${alerts}</div>
        <div style="font-size:10px; color:var(--text-muted); margin-bottom:3px;">
          alerts ingested · click for the queue</div>
        <div style="display:flex; gap:10px; flex-wrap:wrap; font-size:10.5px;
                    font-family:var(--font-mono); font-weight:700;">
          <span style="color:var(--amber);">${intraday} 0DTE</span>
          <span style="color:var(--green);">${graded} graded</span>
          ${live ? `<span style="color:var(--cyan);">${live} deep active</span>` : ''}
          ${deepFail ? `<span style="color:var(--rose-light);">${deepFail} deep failed</span>` : ''}
        </div>
        <div style="font-size:10px; margin-top:4px;">${statusTxt}</div>
      </div>`;
  },

  _calendarGrid(sessions, esc) {
    const byDate = {};
    (sessions || []).forEach((s) => { byDate[s.session] = s; });

    const month = this._calMonth || this._todayStr().slice(0, 7);
    const first = new Date(`${month}-01T12:00:00`);
    const year = first.getFullYear();
    const mon = first.getMonth();
    const daysInMonth = new Date(year, mon + 1, 0).getDate();
    const lead = first.getDay();

    let maxAlerts = 1;
    for (let d = 1; d <= daysInMonth; d++) {
      const k = `${year}-${String(mon + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      if (byDate[k]) maxAlerts = Math.max(maxAlerts, byDate[k].alerts || 0);
    }

    const cells = [];
    for (let i = 0; i < lead; i++) {
      cells.push('<div style="min-height:52px;"></div>');
    }
    for (let d = 1; d <= daysInMonth; d++) {
      const key = `${year}-${String(mon + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      const s = byDate[key];
      const isToday = key === this._todayStr();
      const on = this._queueDate === key;

      const frac = s ? Math.min(1, (s.alerts || 0) / maxAlerts) : 0;
      const bg = !s
        ? 'transparent'
        : `rgba(56,189,248,${(0.05 + frac * 0.28).toFixed(3)})`;
      const ungraded = s ? Math.max(0, (s.alerts || 0) - (s.graded || 0)) : 0;
      const fail = s ? (s.deep_failed || 0) : 0;

      const cellTitle = s
        ? `${s.alerts} alerts · ${s.intraday} 0DTE · ${s.graded} graded`
        : `No alerts on ${key}`;
      cells.push(`
        <div onclick="AppStatus.openSessionModal('${key}')"
             title="${esc(cellTitle)} — click for the queue"
             style="min-height:52px; border-radius:5px; padding:3px 4px; cursor:pointer;
                    background:${bg};
                    border:1px solid ${on ? 'var(--cyan)' : isToday ? 'var(--green)' : 'transparent'};
                    ${isToday ? 'box-shadow:inset 0 0 0 1px rgba(16,185,129,0.55);' : ''}">
          <div style="font-size:10px; font-weight:${isToday ? 900 : 600}; font-family:var(--font-mono);
                      color:${isToday ? 'var(--green)' : 'var(--text-muted)'};">
            ${d}${isToday ? ' ●' : ''}</div>
          ${s ? `<div style="font-size:11px; font-weight:800; font-family:var(--font-mono);
                            color:var(--text-main);">${s.alerts || 0}</div>
                 <div style="font-size:9px; font-family:var(--font-mono); color:var(--amber);">
                   ${s.intraday || 0}D</div>
                 ${ungraded ? `<div style="font-size:9px; font-family:var(--font-mono);
                                         color:var(--rose-light);">-${ungraded}g</div>` : ''}
                 ${fail ? `<div style="font-size:9px; font-family:var(--font-mono);
                                        color:var(--rose-light);">✗${fail}</div>` : ''}`
        : `<div style="font-size:11px; color:var(--text-muted); opacity:0.28;">·</div>`}
        </div>`);
    }

    const label = first.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
    return `
      <div>
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
          <span style="font-size:11px; font-weight:800; color:var(--text-main);">${esc(label)}</span>
          <div style="display:flex; gap:4px; align-items:center;">
            <span style="font-size:9px; color:var(--text-muted); margin-right:4px;">
              shade = alert volume · D = 0DTE · -Ng = ungraded · ✗ = deep failed</span>
            <button class="btn secondary" onclick="AppStatus._shiftMonth(-1)"
                    style="padding:1px 7px; font-size:11px;">◀</button>
            <button class="btn secondary" onclick="AppStatus._shiftMonth(0)"
                    style="padding:1px 7px; font-size:10px;">This month</button>
            <button class="btn secondary" onclick="AppStatus._shiftMonth(1)"
                    style="padding:1px 7px; font-size:11px;">▶</button>
          </div>
        </div>
        <div style="display:grid; grid-template-columns:repeat(7, 1fr); gap:3px;">
          ${['S', 'M', 'T', 'W', 'T', 'F', 'S'].map((d) => `
            <div style="text-align:center; font-size:9px; font-weight:800;
                        color:var(--text-muted); padding-bottom:2px;">${d}</div>`).join('')}
          ${cells.join('')}
        </div>
      </div>`;
  },

  _sessionModalEl: null,

  async openSessionModal(session) {
    const day = session || this._todayStr();
    let host = document.getElementById('session-modal');
    if (!host) {
      host = document.createElement('div');
      host.id = 'session-modal';
      host.style.cssText = 'position:fixed; inset:0; z-index:9999; display:none; '
        + 'align-items:center; justify-content:center; background:rgba(0,0,0,0.72); padding:24px;';
      host.onclick = (e) => { if (e.target === host) this.closeSessionModal(); };
      document.body.appendChild(host);
    }
    host.style.display = 'flex';
    host.innerHTML = `
      <div style="background:var(--bg-card,#121722); border:1px solid var(--border); border-radius:10px;
                  width:min(1180px,96vw); max-height:88vh; display:flex; flex-direction:column;
                  box-shadow:0 24px 64px rgba(0,0,0,0.6);">
        <div style="display:flex; justify-content:space-between; align-items:center; gap:12px;
                    padding:12px 16px; border-bottom:1px solid var(--border);">
          <div>
            <div style="font-size:13px; font-weight:900; color:var(--text-main);">
              SESSION ${day}</div>
            <div id="session-modal-sub" style="font-size:10.5px; color:var(--text-muted); margin-top:2px;">
              Loading…</div>
          </div>
          <div style="display:flex; gap:6px; align-items:center;">
            <select id="session-modal-strategy"
                    onchange="AppStatus.renderSessionModalBody('${day}')"
                    style="background:var(--bg-input,#0b0f16); color:var(--text-main);
                           border:1px solid var(--border); border-radius:4px; padding:3px 6px; font-size:11px;">
              <option value="">All lanes</option>
              <option value="Intraday">0DTE / Intraday</option>
              <option value="Daily">Daily</option>
              <option value="Swing">Swing</option>
            </select>
            <button class="btn secondary" onclick="AppStatus.closeSessionModal()"
                    style="padding:3px 10px; font-size:12px;">✕ Close</button>
          </div>
        </div>
        <div id="session-modal-body" style="overflow-y:auto; padding:12px 16px;"></div>
      </div>`;
    this._sessionModalEl = host;

    const [hist, jobs] = await Promise.all([
      window.AppApi.getAlertsHistory(day).catch(() => ({ alerts: [] })),
      window.AppApi.getJobs(day).catch(() => ({ jobs: [], local_queue: [], deep_queue: [] })),
    ]);
    this._sessionCache = { day, hist, jobs };
    this.renderSessionModalBody(day);
  },

  closeSessionModal() {
    if (this._sessionModalEl) this._sessionModalEl.style.display = 'none';
  },

  renderSessionModalBody(day) {
    const body = document.getElementById('session-modal-body');
    const sub = document.getElementById('session-modal-sub');
    if (!body || !this._sessionCache || this._sessionCache.day !== day) return;

    const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');

    const sel = document.getElementById('session-modal-strategy');
    const filter = sel ? sel.value : '';
    const all = (this._sessionCache.hist.alerts || []).slice();
    const rows = filter
      ? all.filter((a) => String(a.strategy || '').toLowerCase() === filter.toLowerCase())
      : all;

    const jobs = this._sessionCache.jobs;
    const deep = jobs.deep_queue || [];
    const local = jobs.local_queue || [];
    const deepDone = deep.filter((j) => j.status === 'COMPLETED').length;
    const deepFail = deep.filter((j) => j.status === 'FAILED').length;
    const deepRun = deep.filter((j) => j.status === 'RUNNING' || j.status === 'QUEUED').length;

    if (sub) {
      sub.innerHTML = `${all.length} alert(s)${filter ? ` · filtered to ${rows.length}` : ''} · `
        + `${local.length} local job(s) · ${deepRun} deep active / ${deepDone} done / ${deepFail} failed`;
    }

    const gradePill = (a) => {
      const g = (a.grade || '').trim();
      if (!g) return '<span class="pill" style="font-size:9px;">–</span>';
      const tone = g === 'A' ? 'green' : g === 'B' ? 'cyan' : 'amber';
      return `<span class="pill ${tone}" style="font-size:9px; font-weight:800;">${esc(g)}</span>`;
    };

    const rowHtml = rows.map((a) => {
      const decided = !!(a.llm_decision && String(a.llm_decision).trim());
      const decision = decided
        ? `<span style="color:var(--text-main);">${esc(String(a.llm_decision).slice(0, 80))}</span>`
        : `<span style="color:var(--text-muted); font-style:italic;">no verdict${
            a.routing_stage === 'ARCHIVED' ? ' — archived by design' : ''}</span>`;
      return `
        <tr style="border-bottom:1px solid var(--border);">
          <td style="padding:3px 6px; font-weight:800; color:var(--cyan);">${esc(a.symbol || '—')}</td>
          <td style="padding:3px 6px;">${esc(a.action || a.event || '—')}</td>
          <td style="padding:3px 6px;">${esc(a.strategy || '—')}</td>
          <td style="padding:3px 6px; color:var(--text-muted);">${esc(a.setup || '—')}</td>
          <td style="padding:3px 6px; text-align:center;">${gradePill(a)}</td>
          <td style="padding:3px 6px; font-size:10.5px;">${decision}</td>
          <td style="padding:3px 6px; color:var(--text-muted); font-size:10px;">${esc(String(a.timestamp || '').slice(11, 19))}</td>
        </tr>`;
    }).join('');

    const jobRows = (list, label) => list.length ? `
      <div style="margin-top:10px;">
        <div style="font-size:10px; font-weight:800; color:var(--text-muted); margin-bottom:3px;">${label}</div>
        ${list.slice(0, 25).map((j) => `
          <div style="display:flex; gap:8px; align-items:center; font-size:10.5px;
                      font-family:var(--font-mono); padding:1px 0;">
            <span style="font-weight:800; color:var(--cyan); min-width:52px;">${esc(j.ticker)}</span>
            <span style="color:var(--text-muted);">${esc(j.stage || j.stage_detail || '')}</span>
            <span style="color:${j.status === 'FAILED' ? 'var(--rose-light)'
              : j.status === 'COMPLETED' ? 'var(--green)' : 'var(--amber)'};">${esc(j.status)}</span>
          </div>`).join('')}
      </div>` : '';

    body.innerHTML = `
      ${!rows.length ? `<div style="text-align:center; padding:20px; color:var(--text-muted);">
          No alerts recorded for ${esc(day)}${filter ? ` in the ${esc(filter)} lane` : ''}.</div>` : `
        <div style="overflow-x:auto;">
          <table style="width:100%; border-collapse:collapse; font-size:11px;
                        font-family:'JetBrains Mono', monospace;">
            <thead>
              <tr style="color:var(--text-muted); font-size:9.5px; text-transform:uppercase;
                         border-bottom:1px solid var(--border);">
                <th style="text-align:left; padding:4px 6px;">Ticker</th>
                <th style="text-align:left; padding:4px 6px;">Action</th>
                <th style="text-align:left; padding:4px 6px;">Lane</th>
                <th style="text-align:left; padding:4px 6px;">Setup</th>
                <th style="text-align:center; padding:4px 6px;">Gr</th>
                <th style="text-align:left; padding:4px 6px;">Local research</th>
                <th style="text-align:right; padding:4px 6px;">Time</th>
              </tr>
            </thead>
            <tbody>${rowHtml}</tbody>
          </table>
        </div>`}
      ${jobRows(local, 'LOCAL RESEARCH JOBS')}
      ${jobRows(deep, 'DEEP RESEARCH JOBS')}
      <div style="margin-top:12px; display:flex; gap:8px;">
        <button class="btn primary" style="font-size:11px; padding:4px 12px;"
                onclick="AppStatus.closeSessionModal(); AppStatus.setQueueDate('${day}');">
          Filter the queues to ${esc(day)}</button>
      </div>`;
  },

  renderSessionHistory(sessions) {
    const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const rows = sessions || [];
    const selected = this._queueDate;

    const head = `
      <div style="display:flex; justify-content:space-between; align-items:center; gap:10px;
                  border-bottom:1px solid var(--border); padding-bottom:6px; flex-wrap:wrap;">
        <div>
          <div style="font-size:11.5px; font-weight:800; color:var(--text-main); display:flex; align-items:center; gap:6px;">
            <span>🗓 SESSION HISTORY</span>
            <span class="pill cyan" style="font-size:9.5px; padding:1px 5px;">Per-Date Ledger</span>
          </div>
          <div style="font-size:10px; color:var(--text-muted); margin-top:2px;">
            Click a day to filter the queues below. Click it again to clear.
          </div>
        </div>
        <div style="display:flex; align-items:center; gap:6px;">
          <select id="queue-date-select" onchange="AppStatus.setQueueDate(this.value)"
                  style="background:var(--bg-input, #0b0f16); color:var(--text-main);
                         border:1px solid var(--border); border-radius:4px; padding:3px 6px;
                         font-size:11px; font-weight:700; font-family:var(--font-mono);">
            <option value="">All dates</option>
            ${(this._queueDates || []).map((d) => `<option value="${esc(d)}"${d === selected ? ' selected' : ''}>${esc(d)}</option>`).join('')}
          </select>
          <button class="btn secondary" onclick="AppStatus.setQueueDate('')"
                  title="Show every date in the queue panels again"
                  style="padding:2px 7px; font-size:10px; font-weight:700;">All dates</button>
        </div>
      </div>`;

    return `
      <div style="background:var(--bg-card, #121722); border:1px solid var(--border);
                  border-radius:8px; padding:10px 12px; display:flex; flex-direction:column; gap:10px;">
        ${head}
        ${this._todayCard(rows, esc)}
        ${this._calendarGrid(rows, esc)}
      </div>`;
  },

  async loadSessions() {
    try {
      const data = await window.AppApi.getSessions(45);
      this._sessionRows = data.sessions || [];
      const host = document.getElementById('session-history-container');
      if (host) host.innerHTML = this.renderSessionHistory(this._sessionRows);
    } catch (e) {
      console.error('Failed loading session history', e);
    }
  },

  async updateStatus() {
    try {
      const data = await window.AppApi.getStatus();

      // 1. Market Hours Ribbon
      const mPill = document.getElementById('pill-market');
      const mTxt = document.getElementById('txt-market');
      if (mPill && mTxt) {
        if (data.market_open) {
          mPill.className = 'pill green';
          mTxt.innerText = data.market_status_text || `🟢 REGULAR MARKET OPEN (${(data.time_mt || '').split(' ')[1] || ''} MT)`;
        } else if (data.market_phase === 'AFTER_HOURS' || data.market_phase === 'PRE_MARKET') {
          mPill.className = 'pill amber';
          mTxt.innerText = data.market_status_text || '🟡 EXTENDED HOURS';
        } else {
          mPill.className = 'pill red';
          mTxt.innerText = data.market_status_text || '🔴 MARKET CLOSED';
        }
      }

      // 2. LLM Port 8000 Online Check
      const llmPill = document.getElementById('pill-llm');
      const llmTxt = document.getElementById('txt-llm');
      if (llmPill && llmTxt) {
        if (data.llm_online) {
          llmPill.className = 'pill green';
          llmTxt.innerText = '🟢 LLM Online (Port 8000)';
        } else {
          llmPill.className = 'pill red';
          llmTxt.innerText = '🔴 LLM Offline';
        }
      }

      // 3. Tastytrade Status
      const tastyTxt = document.getElementById('txt-tasty');
      if (tastyTxt) {
        tastyTxt.innerText = `Tastytrade (${data.tasty_alert_count || 0} Alerts)`;
      }

      // 3b. Schwab OAuth 7-Day Lifecycle Indicator
      const schwabPill = document.getElementById('pill-schwab');
      const schwabTxt = document.getElementById('txt-schwab');
      if (schwabPill && schwabTxt && data.schwab_status) {
        const s = data.schwab_status;
        window._lastSchwabStatus = s;
        if (!s.configured) {
          schwabPill.className = 'pill';
          schwabTxt.innerText = '⚪ Schwab Unset';
        } else if (s.valid) {
          if (s.status === 'EXPIRING_SOON') {
            schwabPill.className = 'pill amber pulse';
            schwabTxt.innerText = `⚠️ Schwab (${s.hours_remaining}h left)`;
          } else {
            schwabPill.className = 'pill green';
            schwabTxt.innerText = `🟢 Schwab (${s.days_remaining}d)`;
          }
        } else {
          schwabPill.className = 'pill red';
          schwabTxt.innerText = '🔴 Schwab Expired';
        }
      }

      // 4. Gmail Ingestor Tracker Status
      const trPill = document.getElementById('pill-tracker');
      const trTxt = document.getElementById('txt-tracker');
      const trBtn = document.getElementById('btn-toggle-tracker');
      const trBtnIntraday = document.getElementById('btn-intraday-tracker');
      const trTxtIntraday = document.getElementById('val-ingestor-txt');

      if (data.tracker_running) {
        if (trPill) trPill.className = 'pill green';
        if (trTxt) trTxt.innerText = `🟢 Ingestor (PID ${data.tracker_pid})`;
        if (trBtn) {
          trBtn.className = 'btn danger';
          trBtn.innerText = '⏹ Stop Ingestor';
          trBtn.dataset.running = 'true';
        }
        if (trBtnIntraday) {
          trBtnIntraday.className = 'btn danger';
          trBtnIntraday.innerText = '⏹ Stop';
        }
        if (trTxtIntraday) trTxtIntraday.innerText = `Running (PID ${data.tracker_pid})`;
      } else {
        if (trPill) trPill.className = 'pill';
        if (trTxt) trTxt.innerText = '⚪ Ingestor: Stopped';
        if (trBtn) {
          trBtn.className = 'btn';
          trBtn.innerText = '▶ Start Market Ingestor';
          trBtn.dataset.running = 'false';
        }
        if (trBtnIntraday) {
          trBtnIntraday.className = 'btn';
          trBtnIntraday.innerText = '▶ Start';
        }
        if (trTxtIntraday) trTxtIntraday.innerText = 'main.py --loop (idle)';
      }

      // 5. Research Stage Running Indicator
      const rInd = document.getElementById('research-indicator');
      const rTxt = document.getElementById('research-stage-txt');
      if (rInd && rTxt) {
        if (data.research_state && data.research_state.is_running) {
          rInd.style.display = 'inline-flex';
          rTxt.innerText = `${data.research_state.current_ticker} - ${data.research_state.stage}`;
        } else {
          rInd.style.display = 'none';
        }
      }

      // 6. Live VIX Volatility Reading + Tape Regime
      const vixEl = document.getElementById('val-vix');
      const regimePill = document.getElementById('intraday-regime-pill');
      const regimeTxt = document.getElementById('intraday-regime-txt');
      if (vixEl) {
        if (data.vix_price) {
          const v = data.vix_price;
          let label = 'NORMAL', color = '#fbbf24', pClass = 'pill amber';
          if (v > 40) { label = 'CRISIS'; color = '#fb7185'; pClass = 'pill red'; }
          else if (v >= 30) { label = 'EXTREME FEAR'; color = '#fb7185'; pClass = 'pill red'; }
          else if (v >= 25) { label = 'HIGH VOL'; color = '#fb7185'; pClass = 'pill red'; }
          else if (v >= 20) { label = 'CAUTION ZONE'; color = '#fbbf24'; pClass = 'pill amber'; }
          else if (v >= 15) { label = 'NORMAL'; color = '#fbbf24'; pClass = 'pill amber'; }
          else if (v >= 12) { label = 'LOW VOL'; color = '#34d399'; pClass = 'pill green'; }
          else { label = 'TOO CALM'; color = '#38bdf8'; pClass = 'pill cyan'; }

          vixEl.innerText = `${v.toFixed(2)} (${label})`;
          vixEl.style.color = color;
          if (regimeTxt) {
            const nyTime = new Date().toLocaleTimeString('en-US', {
              timeZone: 'America/New_York',
              hour: '2-digit',
              minute: '2-digit'
            });
            regimeTxt.innerText = `Regime: VIX ${label} · Live ${nyTime} ET`;
          }
          if (regimePill) regimePill.className = pClass;
        } else {
          vixEl.innerText = '--.-- (fetching)';
          vixEl.style.color = '#94a3b8';
          if (regimeTxt && regimeTxt.innerText.includes('Loading')) {
            regimeTxt.innerText = 'Regime: Active Monitoring';
          }
        }
      }

      // 7. GPU VRAM & Utilization Metrics
      const gpuContainer = document.getElementById('gpu-cards-container');
      const vramPill = document.getElementById('vram-summary-pill');
      if (gpuContainer && vramPill) {
        if (data.gpu_stats && data.gpu_stats.length > 0) {
          let maxPct = 0;
          gpuContainer.innerHTML = data.gpu_stats.map(g => {
            const pct = Number(g.pct_used ?? 0);
            const usedGb = (Number(g.used_mb ?? 0) / 1024).toFixed(1);
            const totalGb = (Number(g.total_mb ?? 0) / 1024).toFixed(1);
            if (pct > maxPct) maxPct = pct;
            let color = '#34d399';
            if (pct > 90) color = '#fb7185';
            else if (pct > 75) color = '#fbbf24';

            return `
              <div style="background:var(--bg-subtle); padding:10px 14px; border-radius:8px; border:1px solid var(--border);">
                <div style="display:flex; justify-content:space-between; font-size:12px; font-family:'JetBrains Mono', monospace; margin-bottom:6px;">
                  <span style="font-weight:700; color:var(--text-main);">GPU ${g.index}: ${g.name}</span>
                  <span style="color:${color}; font-weight:700;">${usedGb} GB / ${totalGb} GB (${pct}%)</span>
                </div>
                <div style="height:6px; background:var(--border); border-radius:999px; overflow:hidden; margin-bottom:5px;">
                  <div style="height:100%; width:${pct}%; background:${color}; border-radius:999px;"></div>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:10px; color:var(--text-muted); font-family:'JetBrains Mono', monospace;">
                  <span>Utilization: ${g.util_pct ?? 0}%</span>
                  <span>Temp: ${g.temp_c ?? '--'}°C</span>
                </div>
              </div>
            `;
          }).join('');

          if (maxPct > 90) {
            vramPill.className = 'pill red';
            vramPill.innerText = `⚠️ VRAM HEAVY (${maxPct}%)`;
          } else {
            vramPill.className = 'pill green';
            vramPill.innerText = `🟢 VRAM STABLE (${maxPct}%)`;
          }
        } else {
          gpuContainer.innerHTML = '<div style="color:var(--text-muted); font-size:12px;">No NVIDIA GPUs detected. Running in CPU mode.</div>';
          vramPill.innerText = 'CPU Mode';
          vramPill.className = 'pill';
        }
      }

      // 8. Active Research Tasks + per-session ledger
      await this.loadSessions();
      await this.loadJobs();

    } catch (e) {
      console.error('Status update error', e);
    }
  },

  async loadJobs() {
    try {
      // Escape helpers for safe interpolation into HTML attributes and JS strings
      const escHtml = (s) => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
      const escJsAttr = (s) => String(s ?? '').replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

      const data = await window.AppApi.getJobs(this._queueDate);
      this._queueDates = data.available_dates || [];
      const targets = [
        document.getElementById('active-procs-container')
      ].filter(Boolean);
      if (targets.length === 0) return;

      const localJobs = data.local_queue || (data.jobs || []).filter(j => j.mode === 'local_only');
      const deepJobs = data.deep_queue || (data.jobs || []).filter(j => j.mode !== 'local_only');

      // Cap the finished/filtered rows. This was 6, which is why only a handful of tickers were ever
      // visible. The full totals are shown in each header, so a cap is never mistaken for
      // "that is all of them".
      const RECENT_CAP = 25;

      const activeLocal = localJobs.filter(j => j.status === 'RUNNING');
      const queuedLocal = localJobs.filter(j => j.status === 'QUEUED');
      const finishedLocal = localJobs.filter(j => j.status !== 'RUNNING' && j.status !== 'QUEUED');
      const recentLocal = finishedLocal.slice(0, RECENT_CAP);

      const activeDeep = deepJobs.filter(j => j.status === 'RUNNING');
      const queuedDeep = deepJobs.filter(j => j.status === 'QUEUED');
      const finishedDeep = deepJobs.filter(j => j.status !== 'RUNNING' && j.status !== 'QUEUED');
      const recentDeep = finishedDeep.slice(0, RECENT_CAP);

      const localMax = data.local_slots_max || 2;
      const deepMax = data.deep_slots_max || 2;
      const localUsed = data.local_slots_used !== undefined ? data.local_slots_used : activeLocal.length;
      const deepUsed = data.deep_slots_used !== undefined ? data.deep_slots_used : activeDeep.length;

      const localSlotColor = localUsed >= localMax ? 'red' : (localUsed > 0 ? 'amber' : 'green');
      const deepSlotColor = deepUsed >= deepMax ? 'red' : (deepUsed > 0 ? 'amber' : 'green');

      // Helper to render Local Queue table
      const renderLocalQueue = () => {
        let rowsHtml = '';
        if (activeLocal.length > 0) {
          activeLocal.forEach(j => {
            const startDt = new Date(j.started_at);
            const elapsedMin = Math.floor((new Date() - startDt) / 60000);
            const elapsedSec = Math.floor(((new Date() - startDt) % 60000) / 1000);
            const timeStr = `${elapsedMin}m ${elapsedSec}s`;
            const comp = (window.AppUtils ? AppUtils.getCompanyName(j.ticker) : j.ticker) || j.ticker || '';
            const stageLabel = j.stage_detail || j.stage || 'Local Triage';

            rowsHtml += `
              <tr style="border-bottom:1px solid var(--border); background:rgba(16, 185, 129, 0.05);">
                <td style="padding:6px 8px;">
                  <span class="pill green pulse" style="font-size:9px; padding:1px 5px;">ACTIVE</span>
                  <strong style="color:var(--blue); cursor:help; margin-left:4px;" title="${escHtml(comp)}" data-ticker="${escHtml(j.ticker)}">$${escHtml(j.ticker)}</strong>
                </td>
                <td style="padding:6px 8px; color:var(--text-main); font-size:11px;">${escHtml(stageLabel)}</td>
                <td style="padding:6px 8px; color:var(--amber); font-size:11px; white-space:nowrap;">⏳ ${timeStr}</td>
                <td style="padding:6px 8px; text-align:right; white-space:nowrap;">
                  <button class="btn secondary" onclick="AppStatus.showJobLogModal('${escJsAttr(j.job_id)}', '${escJsAttr(j.ticker)}', null)" style="padding:1px 6px; font-size:10px;">📋 Log</button>
                  <button class="btn danger" onclick="AppStatus.killJob('${escJsAttr(j.job_id)}')" style="padding:1px 6px; font-size:10px;">🛑 Kill</button>
                </td>
              </tr>
            `;
          });
        }

        if (queuedLocal.length > 0) {
          queuedLocal.forEach((q, idx) => {
            const comp = (window.AppUtils ? AppUtils.getCompanyName(q.ticker) : q.ticker) || q.ticker || '';
            rowsHtml += `
              <tr style="border-bottom:1px solid var(--border); opacity:0.85;">
                <td style="padding:5px 8px;">
                  <span class="pill amber" style="font-size:9px; padding:1px 5px;">#${idx + 1} QUEUE</span>
                  <strong style="color:var(--text-main); cursor:help; margin-left:4px;" title="${escHtml(comp)}" data-ticker="${escHtml(q.ticker)}">$${escHtml(q.ticker)}</strong>
                </td>
                <td style="padding:5px 8px; color:var(--text-muted); font-size:10.5px;">${escHtml(q.stage_detail || 'Waiting for local slot')}</td>
                <td style="padding:5px 8px; color:var(--text-muted); font-size:10.5px;">Pending</td>
                <td style="padding:5px 8px; text-align:right;">
                  <button class="btn secondary" onclick="AppStatus.killJob('${escJsAttr(q.job_id)}')" style="padding:1px 6px; font-size:10px;">Cancel</button>
                </td>
              </tr>
            `;
          });
        }

        if (recentLocal.length > 0) {
          recentLocal.forEach(j => {
            const isFailed = (j.status === 'FAILED' || j.status === 'KILLED');
            const timeDisplay = AppStatus.formatJobTime(j.completed_at || j.started_at);
            const comp = (window.AppUtils ? AppUtils.getCompanyName(j.ticker) : j.ticker) || j.ticker || '';
            const detailStr = j.stage_detail || '';
            let verdictBadge = '';
            if (detailStr.includes('PASS') || j.stage === 'DONE') {
              verdictBadge = `<span class="pill green" style="font-size:9px; padding:1px 6px; font-weight:700;">🎯 PASS → Enqueued Deep</span>`;
            } else if (detailStr.includes('WATCH')) {
              verdictBadge = `<span class="pill amber" style="font-size:9px; padding:1px 6px;">⏹️ WATCH (Filtered Out)</span>`;
            } else if (detailStr.includes('CUT')) {
              verdictBadge = `<span class="pill red" style="font-size:9px; padding:1px 6px;">⏹️ CUT (Filtered Out)</span>`;
            } else if (isFailed) {
              verdictBadge = `<span class="pill red" style="font-size:9px; padding:1px 6px;">❌ FAILED</span>`;
            } else {
              verdictBadge = `<span class="pill gray" style="font-size:9px; padding:1px 6px;">⚖️ LOCAL DONE</span>`;
            }

            rowsHtml += `
              <tr style="border-bottom:1px solid var(--border);">
                <td style="padding:5px 8px;">
                  <strong style="color:var(--text-main); cursor:pointer;" onclick="AppStatus.openResearchDossier('${escJsAttr(j.ticker)}', '${escJsAttr(j.completed_at || j.started_at || '')}')" title="${escHtml(comp)}" data-ticker="${escHtml(j.ticker)}">$${escHtml(j.ticker)}</strong>
                </td>
                <td style="padding:5px 8px;">${verdictBadge}</td>
                <td style="padding:5px 8px; color:var(--text-muted); font-size:10.5px; white-space:nowrap;">${timeDisplay}</td>
                <td style="padding:5px 8px; text-align:right; white-space:nowrap;">
                  <button class="btn secondary" onclick="AppStatus.showJobLogModal('${escJsAttr(j.job_id)}', '${escJsAttr(j.ticker)}', null)" style="padding:1px 6px; font-size:10px;">📋 Log</button>
                  <button class="btn primary" onclick="AppStatus.restartResearch('${escJsAttr(j.ticker)}', 'deep_only', '${escJsAttr(j.target_date || '')}')" title="Force into Deep Research" style="padding:1px 6px; font-size:10px; background:linear-gradient(135deg, #2563eb, #1d4ed8); color:#fff; border:none; border-radius:3px;">⚡ Deep</button>
                </td>
              </tr>
            `;
          });
        }

        if (!rowsHtml) {
          rowsHtml = `<tr><td colspan="4" style="text-align:center; padding:16px 8px; color:var(--text-muted); font-size:11px;">No local triage tasks active or queued. Incoming alerts will queue here automatically.</td></tr>`;
        }

        return `
          <div style="background:var(--bg-card, #121722); border:1px solid var(--border); border-radius:8px; padding:10px 12px; display:flex; flex-direction:column; gap:8px;">
            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--border); padding-bottom:6px;">
              <div>
                <div style="font-size:11.5px; font-weight:800; color:var(--text-main); display:flex; align-items:center; gap:6px;">
                  <span>⚖️ LOCAL RESEARCH QUEUE</span>
                  <span class="pill cyan" style="font-size:9.5px; padding:1px 5px;">Alert Triage Gate</span>
                </div>
<div style="font-size:10px; color:var(--text-muted); margin-top:2px;">Fast filter & Qwen news. Only SATISFIED setups enter Deep Research.</div>
                </div>
                <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap; justify-content:flex-end;">
                  <span style="font-size:9.5px; color:var(--text-muted); font-weight:700;"
                        title="Active / queued / finished${this._queueDate ? ' — ' + escHtml(this._queueDate) : ' — all dates'}">
                    ${activeLocal.length}A · ${queuedLocal.length}Q · ${finishedLocal.length} done
                    ${finishedLocal.length > recentLocal.length ? ` (showing ${recentLocal.length})` : ''}</span>
                  <span class="pill ${localSlotColor}" style="font-size:10px; padding:2px 6px; font-weight:700;">${localUsed} / ${localMax} Slots${queuedLocal.length > 0 ? ` (${queuedLocal.length} Q)` : ''}</span>
                </div>
            </div>
            <div style="overflow-x:auto;">
              <table style="width:100%; border-collapse:collapse; font-size:11px; font-family:'JetBrains Mono', monospace;">
                <thead>
                  <tr style="color:var(--text-muted); font-size:9.5px; text-transform:uppercase; border-bottom:1px solid var(--border); background:rgba(255,255,255,0.02);">
                    <th style="text-align:left; padding:4px 8px;">Ticker</th>
                    <th style="text-align:left; padding:4px 8px;">Stage / Verdict</th>
                    <th style="text-align:left; padding:4px 8px;">Time</th>
                    <th style="text-align:right; padding:4px 8px;">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  ${rowsHtml}
                </tbody>
              </table>
            </div>
          </div>
        `;
      };

      // Helper to render Deep Queue table
      const renderDeepQueue = () => {
        let rowsHtml = '';
        if (activeDeep.length > 0) {
          activeDeep.forEach(j => {
            const startDt = new Date(j.started_at);
            const elapsedMin = Math.floor((new Date() - startDt) / 60000);
            const elapsedSec = Math.floor(((new Date() - startDt) % 60000) / 1000);
            const timeStr = `${elapsedMin}m ${elapsedSec}s`;
            const comp = (window.AppUtils ? AppUtils.getCompanyName(j.ticker) : j.ticker) || j.ticker || '';
            const stageLabel = j.stage_detail ? `${j.stage} — ${j.stage_detail}` : j.stage;

            rowsHtml += `
              <tr style="border-bottom:1px solid var(--border); background:rgba(168, 85, 247, 0.05);">
                <td style="padding:6px 8px;">
                  <span class="pill purple pulse" style="font-size:9px; padding:1px 5px;">ACTIVE</span>
                  <strong style="color:var(--blue); cursor:help; margin-left:4px;" title="${escHtml(comp)}" data-ticker="${escHtml(j.ticker)}">$${escHtml(j.ticker)}</strong>
                </td>
                <td style="padding:6px 8px; color:var(--text-main); font-size:11px;">${escHtml(stageLabel)}</td>
                <td style="padding:6px 8px; color:var(--amber); font-size:11px; white-space:nowrap;">⏳ ${timeStr}</td>
                <td style="padding:6px 8px; text-align:right; white-space:nowrap;">
                  <button class="btn secondary" onclick="AppStatus.showJobLogModal('${escJsAttr(j.job_id)}', '${escJsAttr(j.ticker)}', null)" style="padding:1px 6px; font-size:10px;">📋 Log</button>
                  <button class="btn danger" onclick="AppStatus.killJob('${escJsAttr(j.job_id)}')" style="padding:1px 6px; font-size:10px;">🛑 Kill</button>
                </td>
              </tr>
            `;
          });
        }

        if (queuedDeep.length > 0) {
          queuedDeep.forEach((q, idx) => {
            const comp = (window.AppUtils ? AppUtils.getCompanyName(q.ticker) : q.ticker) || q.ticker || '';
            const qStage = q.stage_detail || 'Waiting for GPU slot';
            rowsHtml += `
              <tr style="border-bottom:1px solid var(--border); opacity:0.85;">
                <td style="padding:5px 8px;">
                  <span class="pill purple" style="font-size:9px; padding:1px 5px;">#${idx + 1} QUEUE</span>
                  <strong style="color:var(--text-main); cursor:help; margin-left:4px;" title="${escHtml(comp)}" data-ticker="${escHtml(q.ticker)}">$${escHtml(q.ticker)}</strong>
                </td>
                <td style="padding:5px 8px; color:var(--text-muted); font-size:10.5px;">${escHtml(qStage)}</td>
                <td style="padding:5px 8px; color:var(--text-muted); font-size:10.5px;">Pending</td>
                <td style="padding:5px 8px; text-align:right;">
                  <button class="btn secondary" onclick="AppStatus.killJob('${escJsAttr(q.job_id)}')" style="padding:1px 6px; font-size:10px;">Cancel</button>
                </td>
              </tr>
            `;
          });
        }

        if (recentDeep.length > 0) {
          recentDeep.forEach(j => {
            const isFailed = (j.status === 'FAILED' || j.status === 'KILLED');
            const timeDisplay = AppStatus.formatJobTime(j.completed_at || j.started_at);
            const comp = (window.AppUtils ? AppUtils.getCompanyName(j.ticker) : j.ticker) || j.ticker || '';
            let stBadge = '';
            if (j.stage === 'DONE' || j.status === 'COMPLETED') {
              stBadge = `<span class="pill purple" style="font-size:9px; padding:1px 6px; font-weight:700;">✅ ARBITRATION DONE</span>`;
            } else if (isFailed) {
              stBadge = `<span class="pill red" style="font-size:9px; padding:1px 6px; font-weight:700;">❌ FAILED</span>`;
            } else {
              stBadge = `<span class="pill gray" style="font-size:9px; padding:1px 6px;">[${escHtml(j.stage || j.status)}]</span>`;
            }

            rowsHtml += `
              <tr style="border-bottom:1px solid var(--border);">
                <td style="padding:5px 8px;">
                  <strong style="color:var(--text-main); cursor:pointer;" onclick="AppStatus.openResearchDossier('${escJsAttr(j.ticker)}', '${escJsAttr(j.completed_at || j.started_at || '')}')" title="${escHtml(comp)}" data-ticker="${escHtml(j.ticker)}">$${escHtml(j.ticker)}</strong>
                </td>
                <td style="padding:5px 8px;">${stBadge}</td>
                <td style="padding:5px 8px; color:var(--text-muted); font-size:10.5px; white-space:nowrap;">${timeDisplay}</td>
                <td style="padding:5px 8px; text-align:right; white-space:nowrap;">
                  <button class="btn secondary" onclick="AppStatus.openResearchDossier('${escJsAttr(j.ticker)}', '${escJsAttr(j.completed_at || j.started_at || '')}')" style="padding:1px 6px; font-size:10px; color:var(--blue);">📖 Dossier ↗</button>
                  <button class="btn secondary" onclick="AppStatus.showJobLogModal('${escJsAttr(j.job_id)}', '${escJsAttr(j.ticker)}', null)" style="padding:1px 6px; font-size:10px;">📋 Log</button>
                  ${isFailed ? `<button class="btn primary" onclick="AppStatus.restartResearch('${escJsAttr(j.ticker)}', 'deep_only', '${escJsAttr(j.target_date || '')}')" style="padding:1px 6px; font-size:10px; background:linear-gradient(135deg, #2563eb, #1d4ed8); color:#fff; border:none; border-radius:3px;">🔄 Retry</button>` : ''}
                </td>
              </tr>
            `;
          });
        }

        if (!rowsHtml) {
          rowsHtml = `<tr><td colspan="4" style="text-align:center; padding:16px 8px; color:var(--text-muted); font-size:11px;">No deep research tasks active or queued. Tickers satisfied by local triage will appear here.</td></tr>`;
        }

        return `
          <div style="background:var(--bg-card, #121722); border:1px solid var(--border); border-radius:8px; padding:10px 12px; display:flex; flex-direction:column; gap:8px;">
            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--border); padding-bottom:6px;">
              <div>
                <div style="font-size:11.5px; font-weight:800; color:var(--text-main); display:flex; align-items:center; gap:6px;">
                  <span>🔬 DEEP RESEARCH QUEUE</span>
                  <span class="pill purple" style="font-size:9.5px; padding:1px 5px;">Arbitration & Options</span>
                </div>
                <div style="font-size:10px; color:var(--text-muted); margin-top:2px;">Debate + Vision Synthesis + PM Arbitration (Capped at 35).</div>
              </div>
              <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap; justify-content:flex-end;">
                <span style="font-size:9.5px; color:var(--text-muted); font-weight:700;"
                      title="Active / queued / finished${this._queueDate ? ' — ' + escHtml(this._queueDate) : ' — all dates'}">
                  ${activeDeep.length}A · ${queuedDeep.length}Q · ${finishedDeep.length} done
                  ${finishedDeep.length > recentDeep.length ? ` (showing ${recentDeep.length})` : ''}</span>
                <span class="pill ${deepSlotColor}" style="font-size:10px; padding:2px 6px; font-weight:700;">${deepUsed} / ${deepMax} Slots${queuedDeep.length > 0 ? ` (${queuedDeep.length} Q)` : ''}</span>
              </div>
            </div>
            <div style="overflow-x:auto;">
              <table style="width:100%; border-collapse:collapse; font-size:11px; font-family:'JetBrains Mono', monospace;">
                <thead>
                  <tr style="color:var(--text-muted); font-size:9.5px; text-transform:uppercase; border-bottom:1px solid var(--border); background:rgba(255,255,255,0.02);">
                    <th style="text-align:left; padding:4px 8px;">Ticker</th>
                    <th style="text-align:left; padding:4px 8px;">Stage / Detail</th>
                    <th style="text-align:left; padding:4px 8px;">Time</th>
                    <th style="text-align:right; padding:4px 8px;">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  ${rowsHtml}
                </tbody>
              </table>
            </div>
          </div>
        `;
      };

      const finalHtml = `
        <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; margin-bottom:10px;">
          <div style="font-size:11px; color:var(--text-muted);">
            Showing one session at a time. Slot counts stay live across all dates.
          </div>
          <label style="display:flex; align-items:center; gap:5px; font-size:10.5px; font-weight:700; color:var(--text-muted);">
            <span>SESSION DATE</span>
            <select id="queue-date-select" onchange="AppStatus.setQueueDate(this.value)"
                    style="background:var(--bg-input, #0b0f16); color:var(--text-main); border:1px solid var(--border);
                           border-radius:4px; padding:3px 6px; font-size:11px; font-weight:700; font-family:var(--font-mono);">
              <option value="">All dates</option>
              ${(this._queueDates || []).map(d => `<option value="${escHtml(d)}"${d === this._queueDate ? ' selected' : ''}>${escHtml(d)}</option>`).join('')}
            </select>
          </label>
        </div>
        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(460px, 1fr)); gap:12px; width:100%;">
          ${renderLocalQueue()}
          ${renderDeepQueue()}
        </div>
      `;

      targets.forEach(c => {
        c.innerHTML = finalHtml;
        if (window.AppUtils && window.AppUtils.decorateTickerTooltips) {
          window.AppUtils.decorateTickerTooltips(c);
        }
      });
    } catch (e) {
      console.error('Failed loading jobs', e);
    }
  },

  async restartResearch(ticker, mode = 'full', date = '') {
    try {
      const res = await window.AppApi.triggerResearch(ticker, mode, date, true);
      if (res.status === 'started') {
        alert(`🚀 Restarted deep research for ${ticker} in open slot!`);
      } else if (res.status === 'queued') {
        alert(`📥 Slots full. ${ticker} queued for research and will auto-dispatch when a slot opens.`);
      } else {
        alert(`Research status for ${ticker}: ${res.status}`);
      }
      await this.loadJobs();
      await this.updateStatus();
    } catch (e) {
      alert(`Restart Error: ${e.message}`);
    }
  },

  async killJob(jobId) {
    if (!confirm(`Terminate research job ${jobId}? This will stop the subprocess and free VRAM.`)) return;
    try {
      await window.AppApi.killJob(jobId);
      await this.loadJobs();
      await this.updateStatus();
    } catch (e) {
      alert(`Kill Job Error: ${e.message}`);
    }
  },

  _logPollTimer: null,

  _stopLogPoll() {
    if (this._logPollTimer) {
      clearInterval(this._logPollTimer);
      this._logPollTimer = null;
    }
  },

  async showJobLogModal(jobId, ticker, errorMsg) {
    this._stopLogPoll();
    const escHtml = (s) => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    let modal = document.getElementById('job-log-modal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'job-log-modal';
      modal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:999999;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(3px);';
      modal.onclick = (e) => { if (e.target === modal) { modal.style.display = 'none'; AppStatus._stopLogPoll(); } };
      document.body.appendChild(modal);
    }
    modal.style.display = 'flex';
    console.log('[AppStatus] showJobLogModal opened for', jobId, ticker);

    const errHtml = errorMsg
      ? `<div style="background:rgba(239,68,68,0.12); border:1px solid rgba(239,68,68,0.4); border-radius:6px; padding:10px 14px; margin-bottom:12px;">
           <div style="font-size:10px; color:#f87171; font-weight:800; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px;">❌ Failure Reason</div>
           <div style="font-size:12.5px; color:#fecaca; font-family:'JetBrains Mono',monospace; white-space:pre-wrap;">${escHtml(errorMsg)}</div>
         </div>`
      : '';

    const liveDot = `<span id="job-log-live-dot" style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#22c55e;margin-right:4px;animation:pulse 1.5s infinite;"></span>`;

    modal.innerHTML = `
      <div style="background:var(--bg-surface,#1e222d); border:1px solid var(--border,#2a2e39); border-radius:12px; width:85%; max-width:760px; max-height:85vh; display:flex; flex-direction:column; box-shadow:0 16px 48px rgba(0,0,0,0.6); color:var(--text-main,#d1d4dc); font-family:'Inter',sans-serif;">
        <div style="display:flex; justify-content:space-between; align-items:center; padding:14px 18px; border-bottom:1px solid var(--border,#2a2e39); flex-shrink:0;">
          <div>
            <div style="font-size:13px; font-weight:800; color:var(--text-main);">${liveDot}📋 Job Log — $${escHtml(ticker)}</div>
            <div style="display:flex; align-items:center; gap:8px; margin-top:2px;">
              <span id="job-log-stage" style="font-size:10.5px; color:var(--cyan,#38bdf8); font-family:'JetBrains Mono',monospace; font-weight:700;"></span>
              <span style="font-size:10.5px; color:var(--text-muted,#6b7280); font-family:'JetBrains Mono',monospace;">${escHtml(jobId)}</span>
              <a href="/api/logs/raw/${encodeURIComponent(jobId)}" target="_blank" rel="noopener"
                 style="font-size:10.5px; color:#60a5fa; text-decoration:none; font-weight:600; display:inline-flex; align-items:center; gap:3px;"
                 title="Open full log file in new tab">
                📄 Open Full File ↗
              </a>
            </div>
          </div>
          <button onclick="document.getElementById('job-log-modal').style.display='none'; AppStatus._stopLogPoll()" style="background:none; border:none; color:var(--text-muted); font-size:18px; cursor:pointer; padding:4px 8px; border-radius:4px;">✕</button>
        </div>
        <div id="job-log-scroll" style="padding:14px 18px; overflow-y:auto; flex:1;">
          ${errHtml}
          <div id="job-log-banner"></div>
          <pre id="job-log-pre" style="background:#0a0e17; border:1px solid var(--border,#2a2e39); border-radius:8px; padding:14px; font-size:11.5px; line-height:1.65; font-family:'JetBrains Mono',monospace; color:#a5f3fc; white-space:pre-wrap; word-break:break-all;">⏳ Loading log…</pre>
        </div>
      </div>
    `;

    let prevLineCount = 0;
    const pollInterval = 2500; // refresh every 2.5s while open

    const doPoll = async () => {
      try {
        const data = await window.AppApi.getLogs('all', jobId);
        const pre = document.getElementById('job-log-pre');
        if (!pre) return this._stopLogPoll();
        const lines = (data.logs || []);
        const newContent = lines.length > 0 ? lines.join('\n') : '(No log content recorded for this job.)';

        // Update stage indicator in header
        const stageEl = document.getElementById('job-log-stage');
        if (stageEl && data.job) {
          const stage = data.job.stage || 'UNKNOWN';
          const detail = data.job.stage_detail ? ` — ${data.job.stage_detail}` : '';
          stageEl.innerText = `Stage: ${stage}${detail}`;
        }

        // If we have a job status, stop polling when it's no longer RUNNING
        if (data.job && data.job.status !== 'RUNNING') {
          pre.innerText = newContent;
          const dot = document.getElementById('job-log-live-dot');
          if (dot) dot.style.display = 'none';

          // Show a status banner for non-running jobs so the user knows why it stopped
          const banner = document.getElementById('job-log-banner');
          if (banner) {
            const status = data.job.status;
            if (status === 'KILLED') {
              banner.innerHTML = '<div style="background:rgba(245,158,11,0.12); border:1px solid rgba(245,158,11,0.4); border-radius:6px; padding:10px 14px; margin-bottom:12px; font-size:12px; color:#fbbf24; font-weight:700;">🛑 JOB KILLED — Terminated by user via UI. Output below was captured before termination.</div>';
            } else if (status === 'FAILED') {
              banner.innerHTML = '<div style="background:rgba(239,68,68,0.12); border:1px solid rgba(239,68,68,0.4); border-radius:6px; padding:10px 14px; margin-bottom:12px; font-size:12px; color:#fca5a5; font-weight:700;">❌ JOB FAILED — ' + escHtml(data.job.error_message || 'Process terminated before generating report') + '</div>';
            } else if (status === 'COMPLETED') {
              banner.innerHTML = '<div style="background:rgba(16,185,129,0.12); border:1px solid rgba(16,185,129,0.4); border-radius:6px; padding:10px 14px; margin-bottom:12px; font-size:12px; color:#34d399; font-weight:700;">✅ JOB COMPLETED — Research finished successfully.</div>';
            }
          }
          this._stopLogPoll();
          return;
        }

        // Append only new lines to avoid scroll jump
        if (lines.length > prevLineCount && prevLineCount > 0) {
          const newLines = lines.slice(prevLineCount).join('\n');
          pre.innerText += '\n' + newLines;
        } else {
          pre.innerText = newContent;
        }
        prevLineCount = lines.length;

        // Auto-scroll if user is near the bottom
        const scrollEl = document.getElementById('job-log-scroll');
        if (scrollEl) {
          const nearBottom = scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight < 80;
          if (nearBottom) scrollEl.scrollTop = scrollEl.scrollHeight;
        }
      } catch (e) {
        // Network hiccup — keep polling
      }
    };

    await doPoll(); // initial load
    this._logPollTimer = setInterval(doPoll, pollInterval);
  },

  async killProcess(pid) {
    if (!confirm(`Terminate process PID ${pid}? This will stop the task and free memory.`)) return;
    try {
      await window.AppApi.killProcess(pid);
      await this.updateStatus();
    } catch (e) {
      alert(`Kill Process Error: ${e.message}`);
    }
  },

  async toggleTracker() {
    const btn = document.getElementById('btn-toggle-tracker');
    const isRunning = btn && btn.dataset.running === 'true';
    try {
      if (isRunning) {
        await window.AppApi.stopOrchestrator();
      } else {
        await window.AppApi.startOrchestrator();
      }
      await this.updateStatus();
      if (window.AppLogs) window.AppLogs.loadLogs();
    } catch (e) {
      alert(`Ingestor Action Error: ${e.message}`);
    }
  },

  async syncAlerts() {
    try {
      await window.AppApi.syncAlerts();
      alert('Alert sync triggered in background.');
      if (window.AppSwing) {
        window.AppSwing.loadWatchTargets();
        window.AppSwing.loadTastytradeAlerts();
      }
      if (window.AppLogs) window.AppLogs.loadLogs();
    } catch (e) {
      alert(`Sync error: ${e.message}`);
    }
  },

  formatJobTime(isoStr) {
    if (!isoStr) return '';
    try {
      let s = String(isoStr).trim();
      if (!s.includes('Z') && !s.includes('+')) {
        s += 'Z';
      }
      const d = new Date(s);
      if (isNaN(d.getTime())) return isoStr.substring(11, 19);

      const timeStr = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true });
      const diffSec = Math.floor((Date.now() - d.getTime()) / 1000);
      let ago = '';
      if (diffSec >= 0 && diffSec < 60) {
        ago = `${diffSec}s ago`;
      } else if (diffSec >= 60 && diffSec < 3600) {
        ago = `${Math.floor(diffSec / 60)}m ago`;
      } else if (diffSec >= 3600 && diffSec < 86400) {
        ago = `${Math.floor(diffSec / 3600)}h ago`;
      }

      return ago ? `${timeStr} (${ago})` : timeStr;
    } catch (e) {
      return (isoStr || '').substring(11, 19);
    }
  },

  openResearchDossier(ticker, dateStr) {
    if (!ticker) return;
    let reportDate = window.AppState.currentArchiveDate;
    if (dateStr) {
      try {
        let s = String(dateStr).trim();
        if (!s.includes('Z') && !s.includes('+')) s += 'Z';
        const d = new Date(s);
        if (!isNaN(d.getTime())) {
          const year = d.getFullYear();
          const month = String(d.getMonth() + 1).padStart(2, '0');
          const day = String(d.getDate()).padStart(2, '0');
          reportDate = `${year}-${month}-${day}`;
        }
      } catch (e) {}
    }

    if (window.AppSwing && typeof window.AppSwing.openReportModal === 'function') {
      window.AppSwing.openReportModal(reportDate || null, ticker);
    }
  },

  showSchwabInfo() {
    const s = window._lastSchwabStatus || {};
    let modal = document.getElementById('schwab-info-modal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'schwab-info-modal';
      modal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:999999;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);';
      modal.onclick = (e) => { if (e.target === modal) modal.style.display = 'none'; };
      document.body.appendChild(modal);
    }
    
    const isValid = s.valid;
    const statusColor = isValid ? (s.status === 'EXPIRING_SOON' ? '#f59e0b' : '#10b981') : '#ef4444';
    const statusBadge = isValid ? (s.status === 'EXPIRING_SOON' ? '⚠️ EXPIRING SOON' : '🟢 ACTIVE & CONNECTED') : '🔴 EXPIRED';

    modal.innerHTML = `
      <div style="background:var(--bg-surface, #1e222d); border:1px solid var(--border, #2a2e39); border-radius:12px; padding:22px; max-width:440px; width:90%; box-shadow:0 12px 36px rgba(0,0,0,0.5); color:var(--text-main, #d1d4dc); font-family:'Inter',sans-serif;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
          <span style="font-size:15px; font-weight:800; display:flex; align-items:center; gap:8px;">🏦 Charles Schwab API</span>
          <button onclick="document.getElementById('schwab-info-modal').style.display='none'" style="background:none; border:none; color:var(--text-muted, #787b86); font-size:18px; cursor:pointer;">✕</button>
        </div>
        <div style="margin-bottom:16px; padding:10px 14px; background:rgba(0,0,0,0.25); border-radius:8px; border-left:4px solid ${statusColor};">
          <div style="font-size:11px; text-transform:uppercase; color:var(--text-muted, #787b86); font-weight:700;">OAuth 7-Day Lifecycle Status</div>
          <div style="font-size:14px; font-weight:800; color:${statusColor}; margin-top:2px;">${statusBadge}</div>
        </div>
        <div style="display:flex; flex-direction:column; gap:8px; font-size:12.5px; margin-bottom:18px;">
          <div style="display:flex; justify-content:space-between;"><span style="color:var(--text-muted, #787b86);">Token Created:</span><strong>${s.created_at || 'N/A'}</strong></div>
          <div style="display:flex; justify-content:space-between;"><span style="color:var(--text-muted, #787b86);">Expires At:</span><strong>${s.expires_at || 'N/A'}</strong></div>
          <div style="display:flex; justify-content:space-between;"><span style="color:var(--text-muted, #787b86);">Time Remaining:</span><strong style="color:${statusColor};">${s.days_remaining !== undefined ? s.days_remaining + ' days (' + s.hours_remaining + 'h)' : 'N/A'}</strong></div>
        </div>
        <div style="font-size:11.5px; color:var(--text-muted, #787b86); line-height:1.5; margin-bottom:16px; background:var(--bg-subtle, rgba(255,255,255,0.03)); padding:10px; border-radius:6px;">
          💡 <em>Access tokens refresh automatically every 30 mins in the background. Per Charles Schwab retail developer policy, refresh tokens require browser re-auth once every 7 days.</em>
        </div>
        <div style="display:flex; flex-direction:column; gap:8px;">
          <div style="font-size:11px; font-weight:700; color:var(--text-muted, #787b86);">TO RENEW TOKEN (15 SECONDS):</div>
          <div style="background:#0f172a; padding:8px 12px; border-radius:6px; font-family:monospace; font-size:12px; color:#38bdf8; display:flex; justify-content:space-between; align-items:center;">
            <span>python setup_schwab.py</span>
            <button onclick="navigator.clipboard.writeText('python setup_schwab.py'); window.AppUtils && window.AppUtils.showToast('Copied to clipboard!', 'success');" style="background:#1e293b; border:1px solid #334155; color:#fff; padding:2px 8px; border-radius:4px; font-size:10.5px; cursor:pointer;">Copy</button>
          </div>
        </div>
      </div>
    `;
    modal.style.display = 'flex';
  }
};
