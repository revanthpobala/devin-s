/**
 * Operations Consoles & Multi-Channel Terminal Controller
 */
window.AppLogs = {
  async loadLogs() {
    try {
      const data = await window.AppApi.getLogs('all');
      const box = document.getElementById('console-box');
      if (box && data.logs && data.logs.length > 0) {
        box.innerText = data.logs.join('\n');
        box.scrollTop = box.scrollHeight;
      }
    } catch (e) {}
  },

  switchLogChannel(channel) {
    window.AppState.currentLogChannel = channel;
    window.AppState.selectedJobId = '';
    const jobSel = document.getElementById('select-job-log');
    if (jobSel) jobSel.value = '';

    ['orch', 'research', 'watch', 'all'].forEach(c => {
      const btn = document.getElementById(`tab-chan-${c}`);
      if (btn) btn.className = `tab-btn ${(c === channel || (c === 'orch' && channel === 'orchestrator')) ? 'active' : ''}`;
    });

    const pill = document.getElementById('log-channel-status-pill');
    if (pill) pill.innerHTML = `<span class="dot pulse"></span>Channel: ${channel.toUpperCase()}`;

    this.loadDedicatedLogs();
  },

  selectSpecificJobLog(jobId) {
    if (!jobId) return;
    window.AppState.selectedJobId = jobId;
    window.AppState.currentLogChannel = '';

    ['orch', 'research', 'watch', 'all'].forEach(c => {
      const btn = document.getElementById(`tab-chan-${c}`);
      if (btn) btn.className = 'tab-btn';
    });

    const pill = document.getElementById('log-channel-status-pill');
    if (pill) pill.innerHTML = `<span class="dot pulse"></span>Job Log: ${jobId}`;

    this.loadDedicatedLogs();
  },

  clearChannelLogs() {
    const box = document.getElementById('dedicated-terminal-box');
    if (box) box.innerText = 'Console view cleared.';
  },

  async loadDedicatedLogs() {
    try {
      const channel = window.AppState.currentLogChannel || 'all';
      const jobId = window.AppState.selectedJobId || null;
      const data = await window.AppApi.getLogs(channel, jobId);
      const box = document.getElementById('dedicated-terminal-box');
      if (!box) return;

      // Populate Job Selector dropdown
      const jobSel = document.getElementById('select-job-log');
      if (jobSel && data.jobs && jobSel.options.length <= 1) {
        data.jobs.forEach(j => {
          const opt = document.createElement('option');
          opt.value = j.job_id;
          opt.innerText = `${j.ticker} (${j.mode}) - ${j.status}`;
          jobSel.appendChild(opt);
        });
      }

      if (data.logs && data.logs.length > 0) {
        box.innerText = data.logs.join('\n');
        const autoScroll = document.getElementById('chk-autoscroll');
        if (autoScroll && autoScroll.checked) {
          box.scrollTop = box.scrollHeight;
        }
      } else {
        box.innerText = `(No logs recorded yet for channel: ${window.AppState.currentLogChannel || window.AppState.selectedJobId})`;
      }
    } catch (e) {
      console.error('Failed loading dedicated logs', e);
    }
  }
};
