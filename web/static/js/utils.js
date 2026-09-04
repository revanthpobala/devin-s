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
      if (window.marked && typeof window.marked.parse === 'function') {
        let html = window.marked.parse(text);
        // Transform interactive action links into clickable button pills
        html = html.replace(/<a\s+href=["']action:ask\?prompt=([^"']+)["']>([\s\S]*?)<\/a>/gi, (match, promptEnc, label) => {
          const prompt = decodeURIComponent(promptEnc.replace(/\+/g, '%20'));
          return `<button class="ticker-pill-btn" onclick="AppChat.askActiveChat('${prompt.replace(/'/g, "\\'")}')" style="display:inline-flex; align-items:center; gap:4px; margin:4px 4px 4px 0; font-size:11.5px; padding:3px 9px; color:var(--cyan-glow); border-color:rgba(6,182,212,0.4); background:rgba(6,182,212,0.12); cursor:pointer;">${label}</button>`;
        });
        return html;
      }
    } catch (e) {
      console.warn('Error parsing markdown', e);
    }
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
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
  }
};
