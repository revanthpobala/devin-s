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
      const data = await window.AppApi.getJobs();
      const container = document.getElementById('active-procs-container');
      if (!container) return;

      const activeJobs = (data.jobs || []).filter(j => j.status === 'RUNNING');
      const recentJobs = (data.jobs || []).filter(j => j.status !== 'RUNNING').slice(0, 3);

      const slotCount = activeJobs.length;
      const slotColor = slotCount >= (data.max_concurrent || 2) ? 'red' : (slotCount === 1 ? 'amber' : 'green');

      let html = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; font-size:11px; font-family:'JetBrains Mono', monospace;">
          <span style="color:var(--text-muted); font-weight:700;">CONCURRENCY SLOTS:</span>
          <span class="pill ${slotColor}" style="font-size:10px; padding:2px 6px;">${slotCount} / ${data.max_concurrent || 2} Slots In Use</span>
        </div>
      `;

      if (activeJobs.length > 0) {
        activeJobs.forEach(j => {
          const startDt = new Date(j.started_at);
          const elapsedMin = Math.floor((new Date() - startDt) / 60000);
          const elapsedSec = Math.floor(((new Date() - startDt) % 60000) / 1000);
          const timeStr = `${elapsedMin}m ${elapsedSec}s`;

          html += `
            <div style="display:flex; align-items:center; justify-content:space-between; background:var(--bg-subtle); border:1px solid var(--border); padding:8px 12px; border-radius:8px; font-family:'JetBrains Mono', monospace; font-size:12px; margin-bottom:6px;">
              <div style="display:flex; align-items:center; gap:8px;">
                <span class="pill green pulse" style="font-size:10px; padding:2px 6px;">ACTIVE</span>
                <span style="font-weight:700; color:var(--blue);">${j.ticker}</span>
                <span style="color:var(--text-muted); font-size:11px;">(${j.stage})</span>
              </div>
              <div style="display:flex; align-items:center; gap:10px;">
                <span style="color:var(--amber); font-size:11px;">⏳ ${timeStr}</span>
                <button class="btn danger" onclick="AppStatus.killJob('${j.job_id}')" style="padding:2px 8px; font-size:11px;">🛑 Kill</button>
              </div>
            </div>
          `;
        });
      }

      if (recentJobs.length > 0) {
        html += `<div style="font-size:10px; color:var(--text-muted); margin-top:8px; margin-bottom:4px; font-weight:700; text-transform:uppercase;">Recent Finished Research (Click to Open Dossier)</div>`;
        recentJobs.forEach(j => {
          const stColor = j.status === 'COMPLETED' ? 'green' : (j.status === 'KILLED' ? 'amber' : 'red');
          const timeDisplay = this.formatJobTime(j.completed_at || j.started_at);
          const rawTime = j.completed_at || j.started_at || '';
          html += `
            <div class="recent-job-row"
                 onclick="AppStatus.openResearchDossier('${j.ticker}', '${rawTime}')"
                 title="Click to open ${j.ticker} dossier & interactive copilot window"
                 style="display:flex; align-items:center; justify-content:space-between; background:var(--bg-subtle); border:1px solid var(--border); padding:7px 12px; border-radius:6px; font-family:'JetBrains Mono', monospace; font-size:11px; margin-bottom:5px; cursor:pointer; transition:all 0.15s ease;">
              <div style="display:flex; align-items:center; gap:8px;">
                <span class="pill ${stColor}" style="font-size:9px; padding:1px 5px;">${j.status}</span>
                <strong style="font-weight:700; font-size:12.5px; color:var(--text-main);">$${j.ticker}</strong>
                <span style="color:var(--text-muted); font-size:10.5px;">[${j.mode}]</span>
              </div>
              <div style="display:flex; align-items:center; gap:10px;">
                <span style="color:var(--text-muted); font-size:11px; font-weight:600;" title="${rawTime}">${timeDisplay}</span>
                <span style="color:var(--blue); font-size:11px; font-weight:700; display:inline-flex; align-items:center; gap:3px;">📖 Dossier ↗</span>
              </div>
            </div>
          `;
        });
      }

      container.innerHTML = html;
    } catch (e) {
      console.error('Failed loading jobs', e);
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
  }
};
