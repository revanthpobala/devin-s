/**
 * Status and Hardware Operations Controller
 */
window.AppStatus = {
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
          vixEl.innerText = '--.-- (no data)';
          vixEl.style.color = '#94a3b8';
        }
      }

      // 7. GPU VRAM & Utilization Metrics
      const gpuContainer = document.getElementById('gpu-cards-container');
      const vramPill = document.getElementById('vram-summary-pill');
      if (gpuContainer && vramPill) {
        if (data.gpu_stats && data.gpu_stats.length > 0) {
          let maxPct = 0;
          gpuContainer.innerHTML = data.gpu_stats.map(g => {
            if (g.pct > maxPct) maxPct = g.pct;
            let color = '#34d399';
            if (g.pct > 90) color = '#fb7185';
            else if (g.pct > 75) color = '#fbbf24';

            return `
              <div style="background:var(--bg-subtle); padding:10px 14px; border-radius:8px; border:1px solid var(--border);">
                <div style="display:flex; justify-content:space-between; font-size:12px; font-family:'JetBrains Mono', monospace; margin-bottom:6px;">
                  <span style="font-weight:700; color:var(--text-main);">GPU ${g.index}: ${g.name}</span>
                  <span style="color:${color}; font-weight:700;">${g.used_gb} GB / ${g.total_gb} GB (${g.pct}%)</span>
                </div>
                <div style="height:6px; background:var(--border); border-radius:999px; overflow:hidden; margin-bottom:5px;">
                  <div style="height:100%; width:${g.pct}%; background:${color}; border-radius:999px;"></div>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:10px; color:var(--text-muted); font-family:'JetBrains Mono', monospace;">
                  <span>Utilization: ${g.util}%</span>
                  <span>Temp: ${g.temp}°C</span>
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

      // 8. Active Research Tasks
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

      const data = await window.AppApi.getJobs();
      const targets = [
        document.getElementById('active-procs-container'),
        document.getElementById('active-procs-container-logs')
      ].filter(Boolean);
      if (targets.length === 0) return;

      const activeJobs = (data.jobs || []).filter(j => j.status === 'RUNNING');
      const queuedJobs = (data.jobs || []).filter(j => j.status === 'QUEUED');
      const recentJobs = (data.jobs || []).filter(j => j.status !== 'RUNNING' && j.status !== 'QUEUED').slice(0, 8);

      const slotCount = activeJobs.length;
      const slotColor = slotCount >= (data.max_concurrent || 2) ? 'red' : (slotCount === 1 ? 'amber' : 'green');
      const queuedBadge = queuedJobs.length > 0 ? ` (${queuedJobs.length} Queued)` : '';

      let html = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; font-size:11px; font-family:'JetBrains Mono', monospace;">
          <span style="color:var(--text-muted); font-weight:700;">CONCURRENCY SLOTS:</span>
          <span class="pill ${slotColor}" style="font-size:10px; padding:2px 6px;">${slotCount} / ${data.max_concurrent || 2} Slots In Use${queuedBadge}</span>
        </div>
      `;

      if (activeJobs.length > 0) {
        activeJobs.forEach(j => {
          const startDt = new Date(j.started_at);
          const elapsedMin = Math.floor((new Date() - startDt) / 60000);
          const elapsedSec = Math.floor(((new Date() - startDt) % 60000) / 1000);
          const timeStr = `${elapsedMin}m ${elapsedSec}s`;
          const compActive = (window.AppUtils ? AppUtils.getCompanyName(j.ticker) : j.ticker) || j.ticker || '';
          const stageLabel = j.stage_detail ? `${j.stage} — ${j.stage_detail}` : j.stage;

          html += `
            <div style="display:flex; align-items:center; justify-content:space-between; background:var(--bg-subtle); border:1px solid var(--border); padding:8px 12px; border-radius:8px; font-family:'JetBrains Mono', monospace; font-size:12px; margin-bottom:6px;">
              <div style="display:flex; align-items:center; gap:8px;">
                <span class="pill green pulse" style="font-size:10px; padding:2px 6px;">ACTIVE</span>
                <strong style="font-weight:700; color:var(--blue); cursor:help;" title="${String(compActive).replace(/"/g, '&quot;')}" data-ticker="${j.ticker || ''}">$${j.ticker || ''}</strong>
                <span style="color:var(--text-muted); font-size:11px;">(${stageLabel})</span>
              </div>
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="color:var(--amber); font-size:11px;">⏳ ${timeStr}</span>
                <button class="btn secondary" onclick="AppStatus.showJobLogModal('${escJsAttr(j.job_id)}', '${escJsAttr(j.ticker || '')}', null)" title="View live log stream" style="padding:2px 8px; font-size:10.5px;">📋 Log</button>
                <button class="btn danger" onclick="AppStatus.killJob('${j.job_id}')" style="padding:2px 8px; font-size:11px;">🛑 Kill</button>
              </div>
            </div>
          `;
        });
      }

      if (queuedJobs.length > 0) {
        html += `<div style="font-size:10px; color:var(--text-muted); margin-top:6px; margin-bottom:4px; font-weight:700; text-transform:uppercase;">⏳ Queued (Auto-Dispatches When Slot Frees)</div>`;
        queuedJobs.forEach((q, idx) => {
          const compQueued = (window.AppUtils ? AppUtils.getCompanyName(q.ticker) : q.ticker) || q.ticker || '';
          html += `
            <div style="display:flex; align-items:center; justify-content:space-between; background:var(--bg-subtle); border:1px dashed var(--border); padding:6px 12px; border-radius:6px; font-family:'JetBrains Mono', monospace; font-size:11.5px; margin-bottom:4px;">
              <div style="display:flex; align-items:center; gap:8px;">
                <span class="pill purple" style="font-size:9px; padding:1px 5px; font-weight:700;">#${idx + 1} QUEUE</span>
                <strong style="color:var(--text-main); font-size:12px; cursor:help;" title="${String(compQueued).replace(/"/g, '&quot;')}" data-ticker="${q.ticker || ''}">$${q.ticker || ''}</strong>
                <span style="color:var(--text-muted); font-size:10.5px;">(Waiting for slot)</span>
              </div>
              <button class="btn secondary" onclick="AppStatus.killJob('${q.job_id}')" style="padding:1px 7px; font-size:10.5px;">Cancel</button>
            </div>
          `;
        });
      }

      if (recentJobs.length > 0) {
        html += `<div style="font-size:10px; color:var(--text-muted); margin-top:8px; margin-bottom:4px; font-weight:700; text-transform:uppercase;">Recent Finished Research (Click to Open Dossier)</div>`;
        recentJobs.forEach(j => {
          const isFailed = (j.status === 'FAILED' || j.status === 'KILLED');
          const stColor = j.status === 'COMPLETED' ? 'green' : (j.status === 'KILLED' ? 'amber' : 'red');
          const timeDisplay = this.formatJobTime(j.completed_at || j.started_at);
          const rawTime = j.completed_at || j.started_at || '';
          
          // Reason kept strictly in tooltip on status pill (no inline table clutter)
          const errReason = j.error_message 
            ? j.error_message.replace(/"/g, '&quot;') 
            : (isFailed ? 'Research process terminated or interrupted' : 'Deep research completed successfully');
          const pillTitle = ` title="${errReason}"`;

          // Company name tooltip on ticker
          const compName = (window.AppUtils ? AppUtils.getCompanyName(j.ticker) : j.ticker) || j.ticker || '';
          const compTitle = String(compName).replace(/"/g, '&quot;');

          const restartBtn = isFailed ? `
            <button class="btn primary"
                    onclick="event.stopPropagation(); AppStatus.restartResearch('${escJsAttr(j.ticker)}', '${escJsAttr(j.mode || 'full')}', '${escJsAttr(j.target_date || '')}')"
                    title="Retry deep research for ${escHtml(j.ticker)} (forces fresh run)"
                    style="padding:2px 8px; font-size:10.5px; font-weight:700; display:inline-flex; align-items:center; gap:3px; background:linear-gradient(135deg, #2563eb, #1d4ed8); border:none; color:#fff; border-radius:4px; box-shadow:0 1px 4px rgba(37,99,235,0.4); cursor:pointer;">
              🔄 Restart
            </button>
          ` : '';

          const errFirstLine = isFailed && j.error_message
            ? escHtml(j.error_message.split('\n')[0])
            : '';

          const rowOnclick = isFailed
            ? `AppStatus.showJobLogModal('${escJsAttr(j.job_id)}', '${escJsAttr(j.ticker)}', ${JSON.stringify(escHtml(j.error_message || ''))})`
            : `AppStatus.openResearchDossier('${escJsAttr(j.ticker)}', '${escJsAttr(rawTime)}')`;
          const rowTitle = isFailed
            ? `Click to view ${escHtml(j.ticker)} failure logs`
            : `Click to open ${escHtml(j.ticker)} (${compTitle}) research dossier`;
          const rightAction = isFailed
            ? `<button class="view-logs-btn" data-job-id="${escHtml(j.job_id)}" data-ticker="${escHtml(j.ticker)}" data-error="${escHtml(j.error_message || '')}" style="color:var(--red-light,#f87171); font-size:11px; font-weight:700; white-space:nowrap; background:none; border:none; cursor:pointer; padding:2px 4px; font-family:'JetBrains Mono',monospace;">📋 View Logs</button>`
            : `<span style="color:var(--blue); font-size:11px; font-weight:700; display:inline-flex; align-items:center; gap:3px; white-space:nowrap;">📖 Dossier ↗</span>`;

          html += `
            <div class="recent-job-row"
                 onclick="${rowOnclick}"
                 title="${rowTitle}"
                 style="background:var(--bg-subtle); border:1px solid var(--border); padding:7px 12px; border-radius:6px; font-family:'JetBrains Mono', monospace; font-size:11px; margin-bottom:5px; cursor:pointer; transition:all 0.15s ease;">
              <div style="display:flex; align-items:center; justify-content:space-between;">
                <div style="display:flex; align-items:center; gap:8px;">
                  <span class="pill ${stColor}" style="font-size:9px; padding:2px 6px; cursor:help;"${pillTitle}>${j.status}${j.error_message ? ' ⚠️' : ''}</span>
                  <strong style="font-weight:700; font-size:12.5px; color:var(--text-main); cursor:help;" title="${compTitle}" data-ticker="${escHtml(j.ticker)}">$${escHtml(j.ticker)}</strong>
                  <span style="color:var(--text-muted); font-size:10.5px;">[${j.mode}]</span>
                </div>
                <div style="display:flex; align-items:center; gap:8px;">
                  <span style="color:var(--text-muted); font-size:11px; font-weight:600; white-space:nowrap;" title="${rawTime}">${timeDisplay}</span>
                  ${restartBtn}
                  ${rightAction}
                </div>
              </div>
              ${errFirstLine ? `<div style="margin-top:4px; padding:3px 8px; background:rgba(239,68,68,0.08); border-left:2px solid rgba(239,68,68,0.5); border-radius:3px; font-size:10.5px; color:#fca5a5; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${errReason}">${errFirstLine}</div>` : ''}
            </div>
          `;
        });
      }

      targets.forEach(c => {
        c.innerHTML = html;
        if (window.AppUtils && window.AppUtils.decorateTickerTooltips) {
          window.AppUtils.decorateTickerTooltips(c);
        }

        // Bind View Logs buttons via event delegation
        c.querySelectorAll('.view-logs-btn').forEach(btn => {
          btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const jobId = btn.dataset.jobId;
            const ticker = btn.dataset.ticker;
            const error = btn.dataset.error || '';
            console.log('[AppStatus] View Logs clicked:', jobId, ticker);
            AppStatus.showJobLogModal(jobId, ticker, error);
          });
        });
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
