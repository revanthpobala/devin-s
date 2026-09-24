window.AppDesk = {
  async loadToday() {
    try {
      const data = await window.AppApi.request('/api/desk/today');
      
      let html = `<div style="display:flex; flex-direction:column; gap:20px;">`;
      
      // Found (Research Queue)
      html += `<div class="station-card">
        <h3 style="margin-top:0; color:var(--cyan);"><span style="margin-right:8px;">🔍</span>Found Today</h3>
        <table class="data-table" style="width:100%; border-collapse:collapse; font-size:12px;">
          <thead>
            <tr style="background:var(--bg-subtle); border-bottom:1px solid var(--border);">
              <th style="padding:8px; text-align:left;">Ticker</th>
              <th style="padding:8px; text-align:left;">Setup</th>
              <th style="padding:8px; text-align:center;">Status</th>
              <th style="padding:8px; text-align:left;">Reason</th>
              <th style="padding:8px; text-align:center;">Action</th>
            </tr>
          </thead>
          <tbody>`;
      if (!data.found || data.found.length === 0) {
        html += `<tr><td colspan="5" style="text-align:center; padding:16px; color:var(--text-muted);">No candidates found today.</td></tr>`;
      } else {
        data.found.forEach(r => {
          let badge = `<span class="badge" style="background:var(--bg-subtle);">${r.status || 'UNKNOWN'}</span>`;
          if (r.status === 'PASS') badge = `<span class="badge" style="color:var(--green); border-color:var(--green);">PASS</span>`;
          if (r.status === 'WATCH') badge = `<span class="badge" style="color:var(--amber); border-color:var(--amber);">WATCH</span>`;
          if (r.status === 'CUT') badge = `<span class="badge" style="color:var(--rose-light); border-color:var(--rose-light);">CUT</span>`;
          
          let launchBtn = `<button class="btn secondary" onclick="AppSwing.launchResearch('${r.ticker}')" style="font-size:10px; padding:2px 6px;" title="Deep research calls /api/research/run">Deep Research</button>`;
          
          html += `<tr style="border-bottom:1px solid var(--border);">
            <td style="padding:8px; font-weight:700;"><a href="javascript:void(0)" onclick="AppSwing.openReportModal('${r.ticker}', '${new Date().toISOString().split('T')[0]}')">${r.ticker}</a></td>
            <td style="padding:8px;">${r.setup || '-'}</td>
            <td style="padding:8px; text-align:center;">${badge}</td>
            <td style="padding:8px;">${r.reason || '-'}</td>
            <td style="padding:8px; text-align:center;">${launchBtn}</td>
          </tr>`;
        });
      }
      html += `</tbody></table></div>`;
      
      // Actionable
      html += `<div class="station-card">
        <h3 style="margin-top:0; color:var(--green);"><span style="margin-right:8px;">🎯</span>Actionable (In Zone / Dist &le; 1.5%)</h3>
        <table class="data-table" style="width:100%; border-collapse:collapse; font-size:12px;">
          <thead>
            <tr style="background:var(--bg-subtle); border-bottom:1px solid var(--border);">
              <th style="padding:8px; text-align:left;">Ticker</th>
              <th style="padding:8px; text-align:left;">Lane</th>
              <th style="padding:8px; text-align:center;">Status</th>
              <th style="padding:8px; text-align:right;">Dist %</th>
              <th style="padding:8px; text-align:center;">Live R:R</th>
              <th style="padding:8px; text-align:right;">Entry Zone</th>
              <th style="padding:8px; text-align:right;">Stop</th>
              <th style="padding:8px; text-align:right;">Target</th>
            </tr>
          </thead>
          <tbody>`;
      if (!data.actionable || data.actionable.length === 0) {
        html += `<tr><td colspan="8" style="text-align:center; padding:16px; color:var(--text-muted);">No actionable suggestions.</td></tr>`;
      } else {
        data.actionable.forEach(r => {
          let distStr = r.dist !== null ? r.dist.toFixed(2) + '%' : '-';
          let statusBadge = `<span class="badge" style="background:var(--bg-subtle);">${r.status || 'STALKING'}</span>`;
          if (r.status === 'IN_ZONE') statusBadge = `<span class="badge" style="color:var(--cyan); border-color:var(--cyan);">IN_ZONE</span>`;
          if (r.status === 'IN_TRADE') statusBadge = `<span class="badge" style="color:var(--purple); border-color:var(--purple);">IN_TRADE</span>`;
          
          html += `<tr style="border-bottom:1px solid var(--border);">
            <td style="padding:8px; font-weight:700;"><a href="javascript:void(0)" onclick="AppSwing.openReportModal('${r.ticker}', '${new Date().toISOString().split('T')[0]}')">${r.ticker}</a></td>
            <td style="padding:8px;">${r.lane || '-'}</td>
            <td style="padding:8px; text-align:center;">${statusBadge}</td>
            <td style="padding:8px; text-align:right;">${distStr}</td>
            <td style="padding:8px; text-align:center; font-weight:700;">${r.live_rr ? r.live_rr.toFixed(2) : '-'}</td>
            <td style="padding:8px; text-align:right;">${r.entry_low ? r.entry_low.toFixed(2) : ''} - ${r.entry_high ? r.entry_high.toFixed(2) : ''}</td>
            <td style="padding:8px; text-align:right; color:var(--rose-light);">${r.stop ? r.stop.toFixed(2) : ''}</td>
            <td style="padding:8px; text-align:right; color:var(--green);">${r.target ? r.target.toFixed(2) : ''}</td>
          </tr>`;
        });
      }
      html += `</tbody></table></div>`;
      
      // Stalking
      html += `<div class="station-card">
        <h3 style="margin-top:0; color:var(--amber);"><span style="margin-right:8px;">⏳</span>Stalking</h3>
        <table class="data-table" style="width:100%; border-collapse:collapse; font-size:12px;">
          <thead>
            <tr style="background:var(--bg-subtle); border-bottom:1px solid var(--border);">
              <th style="padding:8px; text-align:left;">Ticker</th>
              <th style="padding:8px; text-align:left;">Lane</th>
              <th style="padding:8px; text-align:right;">Dist %</th>
            </tr>
          </thead>
          <tbody>`;
      if (!data.stalking || data.stalking.length === 0) {
        html += `<tr><td colspan="3" style="text-align:center; padding:16px; color:var(--text-muted);">No stalking suggestions.</td></tr>`;
      } else {
        data.stalking.forEach(r => {
          let distStr = r.dist !== null ? r.dist.toFixed(2) + '%' : '-';
          html += `<tr style="border-bottom:1px solid var(--border);">
            <td style="padding:8px; font-weight:700;">${r.ticker}</td>
            <td style="padding:8px;">${r.lane || '-'}</td>
            <td style="padding:8px; text-align:right;">${distStr}</td>
          </tr>`;
        });
      }
      html += `</tbody></table></div>`;
      
      html += `</div>`;
      const cont = document.getElementById('today-content-container');
      if (cont) cont.innerHTML = html;
      
    } catch (err) {
      console.error(err);
      const cont = document.getElementById('today-content-container');
      if (cont) cont.innerHTML = `<div style="color:var(--rose-light);">Failed to load Today desk: ${err}</div>`;
    }
  },
  
  async loadJournal(page = 1) {
    try {
      const statusFilter = document.getElementById('journal-filter-status') ? document.getElementById('journal-filter-status').value : '';
      const laneFilter = document.getElementById('journal-filter-lane') ? document.getElementById('journal-filter-lane').value : '';
      const includeRejected = document.getElementById('journal-filter-rejected') ? (document.getElementById('journal-filter-rejected').checked ? 1 : 0) : 0;
      
      const query = new URLSearchParams({ page, include_rejected: includeRejected });
      if (statusFilter) query.set('status', statusFilter);
      if (laneFilter) query.set('lane', laneFilter);
      
      const data = await window.AppApi.request(`/api/desk/journal?${query.toString()}`);
      
      let html = `<div style="display:flex; flex-direction:column; gap:16px;">`;
      
      html += `<div class="station-card" style="display:flex; gap:10px; align-items:center; padding:12px;">
        <h3 style="margin:0; margin-right:auto;"><span style="margin-right:8px;">📖</span>Journal</h3>
        <select id="journal-filter-status" onchange="AppDesk.loadJournal()" style="padding:4px 8px; font-size:12px;">
          <option value="">All Statuses</option>
          <option value="OPEN" ${statusFilter === 'OPEN' ? 'selected' : ''}>Open</option>
          <option value="FILLED" ${statusFilter === 'FILLED' ? 'selected' : ''}>Filled</option>
          <option value="CLOSED" ${statusFilter === 'CLOSED' ? 'selected' : ''}>Closed</option>
          <option value="EXPIRED" ${statusFilter === 'EXPIRED' ? 'selected' : ''}>Expired</option>
          <option value="REJECTED" ${statusFilter === 'REJECTED' ? 'selected' : ''}>Rejected</option>
        </select>
        <select id="journal-filter-lane" onchange="AppDesk.loadJournal()" style="padding:4px 8px; font-size:12px;">
          <option value="">All Lanes</option>
          <option value="RR_SETUP" ${laneFilter === 'RR_SETUP' ? 'selected' : ''}>RR_SETUP</option>
          <option value="CODE20" ${laneFilter === 'CODE20' ? 'selected' : ''}>CODE20</option>
          <option value="OVERSOLD" ${laneFilter === 'OVERSOLD' ? 'selected' : ''}>OVERSOLD</option>
        </select>
        <label style="font-size:12px; display:flex; align-items:center; gap:4px;">
          <input type="checkbox" id="journal-filter-rejected" onchange="AppDesk.loadJournal()" ${includeRejected ? 'checked' : ''}> Include Rejected
        </label>
        <button class="btn secondary" onclick="AppDesk.loadJournal(${page - 1})" ${page <= 1 ? 'disabled' : ''}>Prev</button>
        <button class="btn secondary" onclick="AppDesk.loadJournal(${page + 1})" ${data.items.length < 50 ? 'disabled' : ''}>Next</button>
      </div>`;
      
      html += `<div class="station-card">
        <table class="data-table" style="width:100%; border-collapse:collapse; font-size:12px;">
          <thead>
            <tr style="background:var(--bg-subtle); border-bottom:1px solid var(--border);">
              <th style="padding:8px; text-align:left;">Date</th>
              <th style="padding:8px; text-align:left;">Ticker</th>
              <th style="padding:8px; text-align:left;">Lane</th>
              <th style="padding:8px; text-align:center;">Status</th>
              <th style="padding:8px; text-align:right;">R_Net</th>
              <th style="padding:8px; text-align:right;">Bars Held</th>
              <th style="padding:8px; text-align:left;">Exit Reason</th>
              <th style="padding:8px; text-align:center;">Notes</th>
            </tr>
          </thead>
          <tbody>`;
          
      if (!data.items || data.items.length === 0) {
        html += `<tr><td colspan="8" style="text-align:center; padding:16px; color:var(--text-muted);">No journal entries found.</td></tr>`;
      } else {
        data.items.forEach(r => {
          let rNet = r.r_net !== null ? r.r_net.toFixed(2) : '-';
          let rColor = 'inherit';
          if (r.r_net > 0) rColor = 'var(--green)';
          else if (r.r_net < 0) rColor = 'var(--rose-light)';
          
          let badgeColor = 'var(--bg-subtle)';
          if (r.derived_status === 'OPEN') badgeColor = 'var(--cyan)';
          if (r.derived_status === 'FILLED') badgeColor = 'var(--purple)';
          if (r.derived_status === 'CLOSED') badgeColor = 'var(--border)';
          if (r.derived_status === 'EXPIRED') badgeColor = 'var(--amber)';
          if (r.derived_status === 'REJECTED') badgeColor = 'var(--rose-light)';
          
          let noteIcon = r.notes ? '📝' : '➕';
          let drawerId = `journal-drawer-${r.id}`;
          
          html += `<tr style="border-bottom:1px solid var(--border); cursor:pointer;" onclick="document.getElementById('${drawerId}').style.display = document.getElementById('${drawerId}').style.display === 'none' ? 'table-row' : 'none'">
            <td style="padding:8px;">${r.date.substring(0, 10)}</td>
            <td style="padding:8px; font-weight:700;"><a href="javascript:void(0)" onclick="event.stopPropagation(); AppSwing.openReportModal('${r.ticker}', '${r.date.substring(0, 10)}')">${r.ticker}</a></td>
            <td style="padding:8px;">${r.lane || '-'}</td>
            <td style="padding:8px; text-align:center;"><span class="badge" style="border-color:${badgeColor}; color:${badgeColor};">${r.derived_status}</span></td>
            <td style="padding:8px; text-align:right; color:${rColor}; font-weight:700;">${rNet}</td>
            <td style="padding:8px; text-align:right;">${r.bars_held || '-'}</td>
            <td style="padding:8px;">${r.exit_reason || '-'}</td>
            <td style="padding:8px; text-align:center;"><button class="btn secondary" onclick="event.stopPropagation(); AppDesk.editNotes(${r.id}, \`${(r.notes || '').replace(/"/g, '&quot;')}\`)" style="padding:2px 6px; font-size:10px;" title="Edit Notes">${noteIcon}</button></td>
          </tr>
          <tr id="${drawerId}" style="display:none; background:var(--bg-main);">
            <td colspan="8" style="padding:16px;">
              <div style="display:flex; gap:20px; flex-wrap:wrap;">
                <div style="flex:1; min-width:200px;">
                  <h4 style="margin-top:0; color:var(--text-muted);">Plan vs Outcome</h4>
                  <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; font-size:11px;">
                    <div><b>Entry Plan:</b> ${r.plan_entry || '-'}</div>
                    <div><b>Fill:</b> ${r.fill_price || '-'}</div>
                    <div><b>Stop Plan:</b> ${r.plan_stop || '-'}</div>
                    <div><b>Exit:</b> ${r.exit_price || '-'}</div>
                    <div><b>T1 Plan:</b> ${r.plan_t1 || '-'}</div>
                    <div><b>MAE R:</b> ${r.mae_r !== null ? r.mae_r.toFixed(2) : '-'}</div>
                  </div>
                  <div style="margin-top:10px;">
                    <b>Notes:</b>
                    <div style="background:var(--bg-subtle); padding:8px; border-radius:4px; margin-top:4px; font-size:11px; white-space:pre-wrap;">${r.notes || 'No notes.'}</div>
                  </div>
                </div>
                <div style="flex:1; min-width:200px;">
                  <h4 style="margin-top:0; color:var(--text-muted);">Chart Snapshot</h4>
                  <img src="/api/charts/${r.date.substring(0, 10)}/${r.ticker}/90d" style="width:100%; border-radius:4px; border:1px solid var(--border);" onerror="this.style.display='none'">
                </div>
              </div>
            </td>
          </tr>`;
        });
      }
      
      html += `</tbody></table></div>`;
      html += `</div>`;
      
      const cont = document.getElementById('journal-content-container');
      if (cont) cont.innerHTML = html;
      
    } catch (err) {
      console.error(err);
      const cont = document.getElementById('journal-content-container');
      if (cont) cont.innerHTML = `<div style="color:var(--rose-light);">Failed to load Journal: ${err}</div>`;
    }
  },
  
  async editNotes(id, currentNotes) {
    const newNotes = prompt("Edit Notes:", currentNotes);
    if (newNotes !== null) {
      try {
        await window.AppApi.request(`/api/desk/journal/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ notes: newNotes })
        });
        this.loadJournal();
      } catch (err) {
        alert("Failed to update notes: " + err);
      }
    }
  },
  
  async loadRecord() {
    try {
      const data = await window.AppApi.request('/api/desk/record');
      
      let html = `<div style="display:flex; flex-direction:column; gap:20px;">`;
      
      html += `<div class="station-card">
        <h3 style="margin-top:0;"><span style="margin-right:8px;">🏆</span>Record KPIs</h3>
        <div style="display:flex; gap:20px; flex-wrap:wrap;">
          <div style="background:var(--bg-surface); padding:16px; border-radius:8px; border:1px solid var(--border); flex:1; min-width:150px; text-align:center;">
            <div style="font-size:11px; color:var(--text-muted); text-transform:uppercase;">Win Rate</div>
            <div style="font-size:24px; font-weight:800; color:${data.win_pct >= 50 ? 'var(--green)' : 'var(--text-main)'};">${data.win_pct ? data.win_pct.toFixed(1) : 0}%</div>
            <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">${data.won_count} / ${data.total_scored} won</div>
          </div>
          <div style="background:var(--bg-surface); padding:16px; border-radius:8px; border:1px solid var(--border); flex:1; min-width:150px; text-align:center;">
            <div style="font-size:11px; color:var(--text-muted); text-transform:uppercase;">Total R Net</div>
            <div style="font-size:24px; font-weight:800; color:${data.sum_r >= 0 ? 'var(--green)' : 'var(--rose-light)'};">${data.sum_r > 0 ? '+' : ''}${data.sum_r ? data.sum_r.toFixed(2) : 0} R</div>
            <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">${data.mean_r ? data.mean_r.toFixed(2) : 0} avg / ${data.median_r ? data.median_r.toFixed(2) : 0} med</div>
          </div>
          <div style="background:var(--bg-surface); padding:16px; border-radius:8px; border:1px solid var(--border); flex:1; min-width:150px; text-align:center;">
            <div style="font-size:11px; color:var(--text-muted); text-transform:uppercase;">Avg Bars Held</div>
            <div style="font-size:24px; font-weight:800;">${data.avg_bars_held || 0}</div>
          </div>
          <div style="background:var(--bg-surface); padding:16px; border-radius:8px; border:1px solid var(--border); flex:1; min-width:150px; text-align:center;">
            <div style="font-size:11px; color:var(--text-muted); text-transform:uppercase;">Avg MAE R</div>
            <div style="font-size:24px; font-weight:800; color:var(--rose-light);">${data.avg_mae_r || 0} R</div>
          </div>
        </div>
      </div>`;
      
      // Equity Curve
      html += `<div class="station-card">
        <h3 style="margin-top:0;"><span style="margin-right:8px;">📈</span>Equity Curve (Cumulative R)</h3>
        <div style="height:250px; width:100%; border-bottom:1px solid var(--border); display:flex; align-items:flex-end; padding-bottom:10px; gap:4px; overflow-x:auto;">`;
      if (data.equity_curve && data.equity_curve.length > 0) {
        let maxR = Math.max(...data.equity_curve.map(c => Math.abs(c.cum_r)), 1);
        data.equity_curve.forEach(c => {
          let h = Math.max(5, (Math.abs(c.cum_r) / maxR) * 200);
          let color = c.cum_r >= 0 ? 'var(--green)' : 'var(--rose-light)';
          html += `<div style="width:20px; height:${h}px; background:${color}; opacity:0.8; flex-shrink:0;" title="${c.date}: ${c.cum_r} R"></div>`;
        });
      } else {
        html += `<div style="width:100%; text-align:center; color:var(--text-muted);">No equity curve data yet.</div>`;
      }
      html += `</div></div>`;
      
      // Lanes Stats
      html += `<div class="station-card">
        <h3 style="margin-top:0;"><span style="margin-right:8px;">🛣️</span>Lanes</h3>
        <table class="data-table" style="width:100%; border-collapse:collapse; font-size:12px;">
          <thead>
            <tr style="background:var(--bg-subtle); border-bottom:1px solid var(--border);">
              <th style="padding:8px; text-align:left;">Lane</th>
              <th style="padding:8px; text-align:right;">N</th>
              <th style="padding:8px; text-align:right;">Win %</th>
              <th style="padding:8px; text-align:right;">Mean R</th>
              <th style="padding:8px; text-align:right;">Total R</th>
            </tr>
          </thead>
          <tbody>`;
      if (!data.lanes || Object.keys(data.lanes).length === 0) {
        html += `<tr><td colspan="5" style="text-align:center; padding:16px; color:var(--text-muted);">No lane data.</td></tr>`;
      } else {
        for (let lane in data.lanes) {
          let ld = data.lanes[lane];
          let wColor = ld.win_pct >= 50 ? 'var(--green)' : 'var(--text-main)';
          let rColor = ld.sum_r >= 0 ? 'var(--green)' : 'var(--rose-light)';
          // No green on < 50% win lanes except realized winners
          if (ld.win_pct < 50) rColor = 'var(--text-main)'; // Neutralize sum_r color if win_pct < 50
          html += `<tr style="border-bottom:1px solid var(--border);">
            <td style="padding:8px; font-weight:700;">${lane}</td>
            <td style="padding:8px; text-align:right;">${ld.total}</td>
            <td style="padding:8px; text-align:right; color:${wColor};">${ld.win_pct ? ld.win_pct.toFixed(1) : 0}%</td>
            <td style="padding:8px; text-align:right;">${ld.mean_r ? ld.mean_r.toFixed(2) : 0}</td>
            <td style="padding:8px; text-align:right; color:${rColor}; font-weight:700;">${ld.sum_r > 0 ? '+' : ''}${ld.sum_r ? ld.sum_r.toFixed(2) : 0}</td>
          </tr>`;
        }
      }
      html += `</tbody></table>`;
      
      if (data.total_scored < 100) {
        html += `<div style="margin-top:10px; padding:8px; background:var(--bg-subtle); border:1px dashed var(--amber); color:var(--amber); font-size:11px; text-align:center;">
          ⚠️ n < 100: metrics are not statistically significant
        </div>`;
      }
      
      html += `</div>`;
      
      html += `</div>`;
      const cont = document.getElementById('record-content-container');
      if (cont) cont.innerHTML = html;
      
    } catch (err) {
      console.error(err);
      const cont = document.getElementById('record-content-container');
      if (cont) cont.innerHTML = `<div style="color:var(--rose-light);">Failed to load Record: ${err}</div>`;
    }
  }
};
