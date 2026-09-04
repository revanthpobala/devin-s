/**
 * REV CHAT Sidebar & Modal Dossier Copilot Controller with SQLite 15-Day History Persistence
 */
window.AppChat = {
  _revSessionId: null,
  _modalSessionId: null,
  _sidebarAttachedBoard: null,
  _sidebarAttachedImage: null,
  _modalAttachedBoard: null,
  _modalAttachedImage: null,
  _isSendingRevMsg: false,
  _isSendingModalMsg: false,

  escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  },

  // ---- Active Board & Vision Telemetry ----
  getActiveBoardContext(source) {
    const reportModal = document.getElementById('report-modal');
    const isModalOpen = reportModal && reportModal.style.display !== 'none';

    // 1. Check if user selected text anywhere on the screen
    const sel = window.getSelection() ? window.getSelection().toString().trim() : '';
    if (sel && sel.length > 5) {
      return {
        name: `Selection (${sel.slice(0, 22)}...)`,
        context: `**USER HIGHLIGHTED SELECTION FROM SCREEN:**\n"""\n${sel.slice(0, 3000)}\n"""`
      };
    }

    // 2. If report modal is open or source is modal
    if (isModalOpen || source === 'modal') {
      const sym = window.AppState.currentReportData ? window.AppState.currentReportData.ticker : '';
      const currentTab = window.AppSwing && window.AppSwing._currentTab ? window.AppSwing._currentTab : 'suggested';

      if (currentTab === 'optflow') {
        const optPane = document.getElementById('pane-tab-optflow');
        const rows = optPane ? optPane.querySelectorAll('tr, .flow-row') : [];
        let tableText = '';
        if (rows.length > 0) {
          const lines = [];
          rows.forEach((r, idx) => {
            if (idx > 30) return;
            const text = r.innerText.replace(/\s+/g, ' ').trim();
            if (text) lines.push(text);
          });
          tableText = lines.join('\n');
        } else if (optPane) {
          tableText = optPane.innerText.slice(0, 3500);
        }
        return {
          name: `Options Flow (${sym || 'Active'})`,
          context: `**CURRENTLY VISIBLE OPTIONS FLOW TABLE FOR ${sym}:**\n${tableText || 'Options Flow view active.'}`
        };
      } else if (currentTab === 'arb') {
        const arbPane = document.getElementById('pane-tab-arb');
        return {
          name: `PM Arbitration (${sym || 'Active'})`,
          context: `**CURRENTLY VISIBLE PM ARBITRATION RULING FOR ${sym}:**\n${arbPane ? arbPane.innerText.slice(0, 3500) : ''}`
        };
      } else if (currentTab === 'modela') {
        const pane = document.getElementById('pane-tab-modela');
        return {
          name: `Model A Synthesis (${sym || 'Active'})`,
          context: `**CURRENTLY VISIBLE MODEL A SYNTHESIS DOSSIER FOR ${sym}:**\n${pane ? pane.innerText.slice(0, 3500) : ''}`
        };
      } else if (currentTab === 'modelb') {
        const pane = document.getElementById('pane-tab-modelb');
        return {
          name: `Model B Independent (${sym || 'Active'})`,
          context: `**CURRENTLY VISIBLE MODEL B INDEPENDENT REPORT FOR ${sym}:**\n${pane ? pane.innerText.slice(0, 3500) : ''}`
        };
      } else {
        const pane = document.getElementById('pane-tab-suggested');
        return {
          name: `Suggested Position (${sym || 'Active'})`,
          context: `**CURRENTLY VISIBLE SUGGESTED POSITION SETUP FOR ${sym}:**\n${pane ? pane.innerText.slice(0, 3500) : ''}`
        };
      }
    } else {
      // Main dashboard view (e.g. Watchlist or Swing Desk)
      const currentDesk = window.App && window.App.currentDesk ? window.App.currentDesk : 'swing';
      if (currentDesk === 'watchlist') {
        const table = document.getElementById('watchlist-table');
        return {
          name: 'Watchlist Radar Table',
          context: `**ACTIVE RADAR & STALKING TABLE VIEW:**\n${table ? table.innerText.slice(0, 3000) : ''}`
        };
      }
      return {
        name: `${currentDesk.toUpperCase()} Desk`,
        context: `**ACTIVE DESK:** User is viewing the ${currentDesk.toUpperCase()} operations desk.`
      };
    }
  },

  toggleActiveBoardSync(source = 'sidebar') {
    const isSidebar = source === 'sidebar';
    const boardKey = isSidebar ? '_sidebarAttachedBoard' : '_modalAttachedBoard';

    if (this[boardKey]) {
      this[boardKey] = null;
    } else {
      this[boardKey] = this.getActiveBoardContext(source);
    }
    this.updateAttachmentUI(source);
  },

  removeBoardSync(source = 'sidebar') {
    const isSidebar = source === 'sidebar';
    this[isSidebar ? '_sidebarAttachedBoard' : '_modalAttachedBoard'] = null;
    this.updateAttachmentUI(source);
  },

  removeImageAttachment(source = 'sidebar') {
    const isSidebar = source === 'sidebar';
    this[isSidebar ? '_sidebarAttachedImage' : '_modalAttachedImage'] = null;
    this.updateAttachmentUI(source);
  },

  updateAttachmentUI(source = 'sidebar') {
    const isSidebar = source === 'sidebar';
    const container = document.getElementById(isSidebar ? 'rev-chat-attachments' : 'modal-chat-attachments');
    const boardBadge = document.getElementById(isSidebar ? 'rev-chat-badge-board' : 'modal-chat-badge-board');
    const boardName = document.getElementById(isSidebar ? 'rev-chat-board-name' : 'modal-chat-board-name');
    const imageBadge = document.getElementById(isSidebar ? 'rev-chat-badge-image' : 'modal-chat-badge-image');
    const syncBtn = document.getElementById(isSidebar ? 'btn-rev-sync-board' : 'btn-modal-sync-board');

    const attachedBoard = this[isSidebar ? '_sidebarAttachedBoard' : '_modalAttachedBoard'];
    const attachedImage = this[isSidebar ? '_sidebarAttachedImage' : '_modalAttachedImage'];

    if (container) {
      const hasAny = Boolean(attachedBoard || attachedImage);
      container.style.display = hasAny ? 'flex' : 'none';
    }

    if (boardBadge && boardName) {
      if (attachedBoard) {
        boardBadge.style.display = 'inline-flex';
        boardName.innerText = attachedBoard.name || 'Active Screen';
        if (syncBtn) syncBtn.style.color = 'var(--cyan-glow)';
      } else {
        boardBadge.style.display = 'none';
        if (syncBtn) syncBtn.style.color = '';
      }
    }

    if (imageBadge) {
      imageBadge.style.display = attachedImage ? 'inline-flex' : 'none';
    }
  },

  async captureActiveBoardSnapshot(source = 'sidebar') {
    const isSidebar = source === 'sidebar';
    const reportModal = document.getElementById('report-modal');
    const isModalOpen = reportModal && reportModal.style.display !== 'none';

    let targetEl = null;
    if (isModalOpen || source === 'modal') {
      const activePane = document.querySelector('#report-modal .tab-pane.active') || document.querySelector('#report-modal .modal-content');
      targetEl = activePane || reportModal;
    } else {
      targetEl = document.querySelector('.main-desks-column') || document.body;
    }

    if (!targetEl) return;

    try {
      if (typeof html2canvas === 'function') {
        const snapBtn = document.getElementById(isSidebar ? 'btn-rev-snap-view' : 'btn-modal-snap-view');
        if (snapBtn) snapBtn.innerText = '⏳';

        const canvas = await html2canvas(targetEl, {
          scale: 1.0,
          useCORS: true,
          logging: false,
          backgroundColor: '#0b111e'
        });
        const dataUrl = canvas.toDataURL('image/png', 0.85);
        this[isSidebar ? '_sidebarAttachedImage' : '_modalAttachedImage'] = dataUrl;
        this.updateAttachmentUI(source);

        if (snapBtn) snapBtn.innerText = '📸';
      } else {
        const fileInput = document.getElementById(isSidebar ? 'rev-chat-file-input' : 'modal-chat-file-input');
        if (fileInput) fileInput.click();
      }
    } catch (e) {
      console.warn('Canvas snapshot error, falling back to file picker:', e);
      const fileInput = document.getElementById(isSidebar ? 'rev-chat-file-input' : 'modal-chat-file-input');
      if (fileInput) fileInput.click();
    }
  },

  handleImageFileUpload(event, source = 'sidebar') {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      const isSidebar = source === 'sidebar';
      this[isSidebar ? '_sidebarAttachedImage' : '_modalAttachedImage'] = e.target.result;
      this.updateAttachmentUI(source);
    };
    reader.readAsDataURL(file);
    event.target.value = '';
  },

  initPasteListeners() {
    const handlePaste = (e, source) => {
      const items = (e.clipboardData || (e.originalEvent && e.originalEvent.clipboardData))?.items;
      if (!items) return;
      for (const item of items) {
        if (item.type.indexOf('image') !== -1) {
          const blob = item.getAsFile();
          const reader = new FileReader();
          reader.onload = (event) => {
            const isSidebar = source === 'sidebar';
            this[isSidebar ? '_sidebarAttachedImage' : '_modalAttachedImage'] = event.target.result;
            this.updateAttachmentUI(source);
          };
          reader.readAsDataURL(blob);
          e.preventDefault();
          break;
        }
      }
    };

    const revInput = document.getElementById('rev-chat-input');
    if (revInput && !revInput._hasPasteListener) {
      revInput._hasPasteListener = true;
      revInput.addEventListener('paste', (e) => handlePaste(e, 'sidebar'));
    }

    const modalInput = document.getElementById('modal-chat-input');
    if (modalInput && !modalInput._hasPasteListener) {
      modalInput._hasPasteListener = true;
      modalInput.addEventListener('paste', (e) => handlePaste(e, 'modal'));
    }
  },

  // ---- Width Resizing & Presets for Main Sidebar ----
  applyChatWidth(px) {
    const clamped = Math.max(300, Math.min(px, window.innerWidth * 0.65));
    document.documentElement.style.setProperty('--chat-w', `${clamped}px`);
    return clamped;
  },

  setChatPreset(px) {
    this.applyChatWidth(px);
    try {
      localStorage.setItem('rev_chat_w', px);
    } catch (e) {}
  },

  initChatResize() {
    this.initPasteListeners();
    try {
      const saved = parseFloat(localStorage.getItem('rev_chat_w'));
      if (saved >= 300) this.applyChatWidth(saved);
    } catch (e) {}

    const resizer = document.getElementById('chat-resizer');
    let dragging = false;
    if (!resizer) return;

    resizer.addEventListener('mousedown', (e) => {
      e.preventDefault();
      dragging = true;
      resizer.classList.add('active');
      document.body.classList.add('chat-resizing');
    });

    window.addEventListener('mousemove', (e) => {
      if (!dragging) return;
      this.applyChatWidth(window.innerWidth - e.clientX);
    });

    window.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      resizer.classList.remove('active');
      document.body.classList.remove('chat-resizing');
      try {
        localStorage.setItem('rev_chat_w', getComputedStyle(document.documentElement).getPropertyValue('--chat-w'));
      } catch (err) {}
    });
  },

  // ---- Modal Copilot Width Resizing & Dragging ----
  applyModalChatWidth(px) {
    const container = document.getElementById('modal-split-container');
    const drawer = document.getElementById('modal-copilot-drawer');
    if (!drawer) return;
    const maxW = container ? container.clientWidth * 0.68 : window.innerWidth * 0.65;
    const clamped = Math.max(280, Math.min(px, maxW));
    drawer.style.width = `${clamped}px`;
    drawer.style.flex = 'none';
    return clamped;
  },

  initModalChatResize() {
    try {
      const saved = parseFloat(localStorage.getItem('modal_copilot_w'));
      if (saved >= 280) this.applyModalChatWidth(saved);
    } catch (e) {}

    const resizer = document.getElementById('modal-chat-resizer');
    const drawer = document.getElementById('modal-copilot-drawer');
    const modalContent = document.querySelector('#report-modal .modal-content');
    if (!resizer || !drawer || resizer._hasResizeListener) return;

    resizer._hasResizeListener = true;
    let dragging = false;

    resizer.addEventListener('mousedown', (e) => {
      e.preventDefault();
      dragging = true;
      resizer.classList.add('active');
      document.body.classList.add('chat-resizing');
    });

    window.addEventListener('mousemove', (e) => {
      if (!dragging || !modalContent) return;
      const rect = modalContent.getBoundingClientRect();
      const newWidth = rect.right - e.clientX;
      this.applyModalChatWidth(newWidth);
    });

    window.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      resizer.classList.remove('active');
      document.body.classList.remove('chat-resizing');
      try {
        if (drawer) {
          localStorage.setItem('modal_copilot_w', parseFloat(drawer.style.width) || 420);
        }
      } catch (err) {}
    });
  },

  // ---- 15-Day Prior Chat History & Session Selection ----
  async loadPriorSessions(days = 15, ticker = null) {
    try {
      let url = `/api/copilot/chats/sessions?days=${days}`;
      if (ticker) url += `&ticker=${encodeURIComponent(ticker)}`;
      const res = await fetch(url);
      const data = await res.json();
      return (data && data.sessions) ? data.sessions : [];
    } catch (e) {
      console.error('Failed to load prior chat sessions:', e);
      return [];
    }
  },

  async togglePriorChats(target = 'modal') {
    const isModal = target === 'modal';
    const panel = document.getElementById(isModal ? 'modal-prior-chats-panel' : 'sidebar-prior-chats-panel');
    const listEl = document.getElementById(isModal ? 'modal-prior-chats-list' : 'sidebar-prior-chats-list');
    if (!panel || !listEl) return;

    const isOpen = panel.style.display !== 'none';
    if (isOpen) {
      panel.style.display = 'none';
      return;
    }

    panel.style.display = 'block';
    listEl.innerHTML = '<div style="color:var(--text-muted); font-size:11.5px; text-align:center; padding:10px;">⏳ Loading 15-day chats...</div>';

    const sym = isModal ? ((window.AppState.currentReportData && window.AppState.currentReportData.ticker) || null) : null;
    const sessions = await this.loadPriorSessions(15, isModal ? sym : null);

    if (!sessions || sessions.length === 0) {
      listEl.innerHTML = `
        <div style="color:var(--text-muted); font-size:11.5px; text-align:center; padding:12px;">
          No prior chats in the last 15 days. Ask a question below to start a thread!
        </div>
      `;
      return;
    }

    listEl.innerHTML = sessions.map(s => {
      const isCurrent = (isModal && s.session_id === this._modalSessionId) || (!isModal && s.session_id === this._revSessionId);
      const activeBorder = isCurrent ? 'border:1px solid var(--cyan-glow); background:rgba(6,182,212,0.14);' : 'border:1px solid rgba(51,65,85,0.4); background:rgba(15,23,42,0.7);';
      const promptSnippet = (s.first_prompt || '').replace(/</g, '&lt;').slice(0, 75);
      const timeStr = this.formatRelativeTime(s.updated_at || s.created_at);

      return `
        <div style="display:flex; align-items:center; justify-content:space-between; padding:7px 9px; border-radius:6px; ${activeBorder} cursor:pointer; gap:8px; transition:all 0.15s ease;" onclick="AppChat.selectSession('${s.session_id}', '${target}')">
          <div style="display:flex; flex-direction:column; gap:2px; flex:1; min-width:0;">
            <div style="display:flex; align-items:center; gap:6px;">
              <span class="pill cyan" style="font-size:9.5px; font-weight:700; padding:1px 5px;">${s.ticker || 'STOCK'}</span>
              <span style="font-size:10px; color:var(--text-muted);">${s.date || ''}</span>
              <span style="font-size:9.5px; color:var(--emerald-light); font-weight:600;">${s.message_count || 1} msgs</span>
              <span style="font-size:9.5px; color:var(--text-muted); margin-left:auto;">${timeStr}</span>
            </div>
            <div style="font-size:11.5px; color:var(--text-main); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-weight:500;">
              ${promptSnippet || 'Chat Session'}
            </div>
          </div>
          <button class="btn secondary" onclick="AppChat.deleteSession('${s.session_id}', '${target}', event)" style="padding:2px 6px; font-size:10px; color:var(--rose-light); border-color:rgba(244,63,94,0.3); background:rgba(244,63,94,0.1); flex-shrink:0;" title="Delete session">🗑️</button>
        </div>
      `;
    }).join('');
  },

  formatRelativeTime(isoStr) {
    if (!isoStr) return '';
    try {
      const d = new Date(isoStr);
      const now = new Date();
      const diffMs = now - d;
      const diffMins = Math.floor(diffMs / 60000);
      if (diffMins < 1) return 'Just now';
      if (diffMins < 60) return `${diffMins}m ago`;
      const diffHours = Math.floor(diffMins / 60);
      if (diffHours < 24) return `${diffHours}h ago`;
      const diffDays = Math.floor(diffHours / 24);
      if (diffDays === 1) return 'Yesterday';
      if (diffDays < 15) return `${diffDays}d ago`;
      return d.toLocaleDateString();
    } catch (e) {
      return '';
    }
  },

  async selectSession(sessionId, target = 'modal') {
    const isModal = target === 'modal';
    try {
      const res = await fetch(`/api/copilot/chats/history?session_id=${encodeURIComponent(sessionId)}`);
      const data = await res.json();
      const messages = (data && data.messages) ? data.messages : [];

      if (isModal) {
        this._modalSessionId = sessionId;
        window.AppState.modalChatHistory = messages.map(m => ({ role: m.role, content: m.content, date: m.date, created_at: m.created_at }));
        const sym = (window.AppState.currentReportData && window.AppState.currentReportData.ticker) || 'STOCK';
        const date = (window.AppState.currentReportData && window.AppState.currentReportData.date) || '';
        const key = `${sym}_${date}`;
        if (!window.AppState.modalChatHistories) window.AppState.modalChatHistories = {};
        window.AppState.modalChatHistories[key] = window.AppState.modalChatHistory;
        this.renderModalCopilotChat();
        const panel = document.getElementById('modal-prior-chats-panel');
        if (panel) panel.style.display = 'none';
      } else {
        this._revSessionId = sessionId;
        window.AppState.revChatHistory = messages.map(m => ({ role: m.role, content: m.content, date: m.date, created_at: m.created_at }));
        this.renderRevChat();
        const panel = document.getElementById('sidebar-prior-chats-panel');
        if (panel) panel.style.display = 'none';
      }
    } catch (e) {
      alert(`Failed to load session: ${e.message}`);
    }
  },

  async deleteSession(sessionId, target = 'modal', evt = null) {
    if (evt) evt.stopPropagation();
    if (!confirm('Permanently delete this saved chat session from database?')) return;
    try {
      await fetch(`/api/copilot/chats/session/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
      if (this._modalSessionId === sessionId) this.startNewChat('modal');
      if (this._revSessionId === sessionId) this.startNewChat('sidebar');
      this.togglePriorChats(target);
    } catch (e) {
      alert(`Delete error: ${e.message}`);
    }
  },

  startNewChat(target = 'modal') {
    const isModal = target === 'modal';
    if (isModal) {
      const sym = (window.AppState.currentReportData && window.AppState.currentReportData.ticker) || 'STOCK';
      const date = (window.AppState.currentReportData && window.AppState.currentReportData.date) || '';
      this._modalSessionId = `sess_${sym}_${date}_${Date.now()}`;
      window.AppState.modalChatHistory = [];
      const key = `${sym}_${date}`;
      if (!window.AppState.modalChatHistories) window.AppState.modalChatHistories = {};
      window.AppState.modalChatHistories[key] = [];
      this.renderModalCopilotChat();
      const panel = document.getElementById('modal-prior-chats-panel');
      if (panel) panel.style.display = 'none';
    } else {
      this._revSessionId = `sess_REV_${Date.now()}`;
      window.AppState.revChatHistory = [];
      this.renderRevChat();
      const panel = document.getElementById('sidebar-prior-chats-panel');
      if (panel) panel.style.display = 'none';
    }
  },

  // ---- REV CHAT Sidebar Logic ----
  clearRevChat() {
    window.AppState.revChatHistory = [];
    this._revSessionId = `sess_REV_${Date.now()}`;
    this.renderRevChat();
  },

  renderRevChat() {
    const thread = document.getElementById('rev-chat-thread');
    if (!thread) return;

    const history = window.AppState.revChatHistory || [];
    if (history.length === 0) {
      thread.innerHTML = `
        <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px; padding:10px 12px;">
          <div style="font-weight:700; color:var(--blue); font-size:11px; margin-bottom:4px;">⚡ REV CHAT READY</div>
          <div style="color:var(--text-muted); font-size:12px;">Ask any question about today's research runs, what was learned, what to do today, tactical levels, or stock setups ($WMT, $MSFT, $AMZN). Live quotes & options available on demand!</div>
        </div>
      `;
      return;
    }

    thread.innerHTML = history.map(m => {
      if (m.role === 'user') {
        return `<div style="align-self:flex-end; max-width:85%; background:var(--blue); color:#ffffff; border-radius:10px; padding:8px 12px; font-size:12.5px; line-height:1.4; box-shadow:0 1px 3px rgba(0,0,0,0.08);">${m.content}</div>`;
      } else {
        return `<div style="align-self:flex-start; max-width:92%; background:var(--bg-subtle); border:1px solid var(--border); border-radius:10px; padding:10px 14px; font-size:12.5px; line-height:1.5; color:var(--text-main); box-shadow:var(--shadow-card);">
          <div style="font-size:10px; font-weight:700; color:var(--blue); margin-bottom:4px;">⚡ REV CHAT</div>
          <div>${window.AppUtils.renderMarkdown(m.content)}</div>
        </div>`;
      }
    }).join('');
    thread.scrollTop = thread.scrollHeight;
  },

  updateSidebarFocusBadge(ticker) {
    const badge = document.getElementById('rev-chat-focus-badge');
    if (!badge) return;
    const t = (ticker || (window.AppState.currentReportData ? window.AppState.currentReportData.ticker : '') || (document.getElementById('ticker-input')?.value) || '').trim().toUpperCase();
    if (t && !['GENERAL', 'AUTO', 'NONE', 'ALL'].includes(t)) {
      badge.innerText = `🎯 Focus: $${t}`;
      badge.className = 'pill blue';
      badge.title = `Main chat is focused on $${t}. Click to switch to broad market.`;
    } else {
      badge.innerText = `🎯 Focus: Market`;
      badge.className = 'pill';
      badge.title = `Main chat is in broad market mode.`;
    }
  },

  clearSidebarFocus() {
    const input = document.getElementById('ticker-input');
    if (input) input.value = '';
    window.AppState.revChatFocusTicker = '';
    this.updateSidebarFocusBadge('');
  },

  askActiveChat(prompt) {
    const reportModal = document.getElementById('report-modal');
    const isModalOpen = reportModal && reportModal.style.display !== 'none' && !reportModal.classList.contains('minimized');
    if (isModalOpen) {
      this.askModalCopilot(prompt);
    } else {
      this.askRevChat(prompt);
    }
  },

  askRevChat(prompt) {
    const input = document.getElementById('rev-chat-input');
    if (prompt.endsWith('$')) {
      if (input) {
        input.value = prompt;
        input.focus();
        input.setSelectionRange(prompt.length, prompt.length);
      }
    } else {
      if (input) input.value = '';
      this.sendRevChatMsg(prompt);
    }
  },

  askModalCopilot(prompt) {
    const input = document.getElementById('modal-chat-input');
    if (prompt.endsWith('$')) {
      if (input) {
        input.value = prompt;
        input.focus();
        input.setSelectionRange(prompt.length, prompt.length);
      }
    } else {
      if (input) input.value = '';
      this.sendModalCopilotMsg(prompt);
    }
  },

  handleRevChatBtnClick() {
    const btn = document.getElementById('btn-rev-chat-send');
    if (window.AppState.isRevChatStreaming && btn && btn.innerText.includes('Stop')) {
      this.stopRevChatStream();
    } else {
      this.sendRevChatMsg();
    }
  },

  stopRevChatStream() {
    if (window.AppState.activeRevReader) {
      try {
        window.AppState.activeRevReader.cancel();
      } catch (e) {}
      window.AppState.activeRevReader = null;
    }
    if (window.AppState.revChatAbortController) {
      try {
        window.AppState.revChatAbortController.abort();
      } catch (e) {}
      window.AppState.revChatAbortController = null;
    }
    window.AppState.isRevChatStreaming = false;
    const sendBtn = document.getElementById('btn-rev-chat-send');
    if (sendBtn) {
      sendBtn.className = 'btn';
      sendBtn.innerText = '💬 Send';
      sendBtn.disabled = false;
    }
  },

  async sendRevChatMsg(customPrompt = null) {
    if (this._isSendingRevMsg) return;
    this._isSendingRevMsg = true;

    try {
      const input = document.getElementById('rev-chat-input');
      const imageData = this._sidebarAttachedImage || null;
      let question = customPrompt || (input ? input.value.trim() : '');

      if (!question && imageData) {
        question = "Please analyze this attached chart / snapshot and advise on the setup and trade execution.";
      }

      if (!question) {
        if (input) {
          input.focus();
          input.placeholder = "Please type a question or snap/paste a chart...";
        }
        return;
      }

      // If an earlier response is currently streaming, cleanly cancel it before starting the new turn
      if (window.AppState.isRevChatStreaming) {
        this.stopRevChatStream();
        await new Promise(r => setTimeout(r, 60));
      }

      if (input) input.value = '';
      const date = window.AppState.currentArchiveDate || '';
      const thread = document.getElementById('rev-chat-thread');

      if (!window.AppState.revChatHistory) window.AppState.revChatHistory = [];
      if (!this._revSessionId) this._revSessionId = `sess_REV_${Date.now()}`;

      // Snapshot prior history turns BEFORE appending the new question (clean role & content only)
      const cleanHistory = (window.AppState.revChatHistory || [])
        .filter(m => (m.role === 'user' || m.role === 'assistant') && m.content)
        .map(m => ({ role: m.role, content: typeof m.content === 'string' ? m.content : String(m.content) }));

      window.AppState.revChatHistory.push({ role: 'user', content: question });

      const boardContext = this._sidebarAttachedBoard ? this._sidebarAttachedBoard.context : null;
      const boardName = this._sidebarAttachedBoard ? this._sidebarAttachedBoard.name : null;

      if (thread) {
        const userBubble = document.createElement('div');
        userBubble.style = 'align-self:flex-end; max-width:85%; background:var(--blue); color:#ffffff; border-radius:10px; padding:8px 12px; font-size:12.5px; line-height:1.4; box-shadow:0 1px 3px rgba(0,0,0,0.08);';
        let bubbleContent = `<div style="white-space:pre-wrap;">${this.escapeHtml(question)}</div>`;
        if (boardName) {
          bubbleContent = `<div style="font-size:10px; opacity:0.85; margin-bottom:4px; font-weight:700;">📌 ${boardName}</div>` + bubbleContent;
        }
        if (imageData) {
          bubbleContent += `<div style="margin-top:6px;"><img src="${imageData}" style="max-height:85px; max-width:100%; border-radius:6px; border:1px solid rgba(255,255,255,0.35);" /></div>`;
        }
        userBubble.innerHTML = bubbleContent;
        thread.appendChild(userBubble);
        thread.scrollTop = thread.scrollHeight;
      }

      // Reset image attachment after staging to message
      this._sidebarAttachedImage = null;
      this.updateAttachmentUI('sidebar');

      window.AppState.revChatAbortController = new AbortController();
      window.AppState.isRevChatStreaming = true;

      const sendBtn = document.getElementById('btn-rev-chat-send');
      if (sendBtn) {
        sendBtn.className = 'btn danger';
        sendBtn.innerText = '⏹ Stop';
        sendBtn.disabled = false;
      }

      // Resolve active ticker for main chat
      let activeTicker = (
        (window.AppState.currentReportData ? window.AppState.currentReportData.ticker : '') ||
        (document.getElementById('ticker-input') ? document.getElementById('ticker-input').value : '') ||
        ''
      ).trim().toUpperCase();
      if (['GENERAL', 'AUTO', 'NONE', 'ALL'].includes(activeTicker)) activeTicker = '';

      const chatHeaderTitle = activeTicker ? `⚡ REV CHAT ($${activeTicker})` : '⚡ REV CHAT';

      const assistantBubble = document.createElement('div');
      assistantBubble.style = 'align-self:flex-start; max-width:92%; background:var(--bg-subtle); border:1px solid var(--border); border-radius:10px; padding:10px 14px; font-size:12.5px; line-height:1.5; color:var(--text-main); box-shadow:var(--shadow-card);';
      assistantBubble.innerHTML = `<div style="font-size:10px; font-weight:700; color:var(--blue); margin-bottom:6px; display:flex; justify-content:space-between; align-items:center;"><span>${chatHeaderTitle}</span><button class="btn danger" onclick="AppChat.stopRevChatStream()" style="padding:1px 6px; font-size:9.5px; font-weight:700; cursor:pointer;">⏹ Stop</button></div><div><em>Connecting to local model stream...</em></div>`;
      if (thread) {
        thread.appendChild(assistantBubble);
        thread.scrollTop = thread.scrollHeight;
      }

      let fullAnswer = '';
      try {
        const response = await fetch('/api/copilot/chat/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: window.AppState.revChatAbortController.signal,
          body: JSON.stringify({
            question,
            ticker: activeTicker,
            date,
            history: cleanHistory,
            session_id: this._revSessionId,
            board_context: boardContext,
            image_data: imageData
          })
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);

        const reader = response.body.getReader();
        window.AppState.activeRevReader = reader;
        const decoder = new TextDecoder('utf-8');
        let buffer = '';

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split('\n');
          buffer = lines.pop();

          for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith('data: ')) {
              const jsonStr = trimmed.slice(6);
              try {
                const data = JSON.parse(jsonStr);
                if (data.session_id) this._revSessionId = data.session_id;
                if (data.status === 'connecting') {
                  assistantBubble.innerHTML = `<div style="font-size:10px; font-weight:700; color:var(--blue); margin-bottom:6px; display:flex; justify-content:space-between; align-items:center;"><span>${chatHeaderTitle}</span><button class="btn danger" onclick="AppChat.stopRevChatStream()" style="padding:1px 6px; font-size:9.5px; font-weight:700; cursor:pointer;">⏹ Stop</button></div><div><em>Analyzing real-time market data...</em></div>`;
                } else if (data.token) {
                  fullAnswer += data.token;
                  assistantBubble.innerHTML = `<div style="font-size:10px; font-weight:700; color:var(--emerald-light); margin-bottom:6px; display:flex; justify-content:space-between; align-items:center;"><span>${chatHeaderTitle}</span><button class="btn danger" onclick="AppChat.stopRevChatStream()" style="padding:1px 6px; font-size:9.5px; font-weight:700; cursor:pointer;">⏹ Stop</button></div><div>${window.AppUtils.renderMarkdown(fullAnswer)}</div>`;
                  if (thread) thread.scrollTop = thread.scrollHeight;
                } else if (data.error) {
                  fullAnswer += `\n\n❌ **Error:** ${data.error}`;
                  assistantBubble.innerHTML = `<div style="font-size:10px; font-weight:700; color:var(--rose-light); margin-bottom:4px;">❌ ERROR</div><div>${window.AppUtils.renderMarkdown(fullAnswer)}</div>`;
                }
              } catch (parseErr) {}
            }
          }
        }

        assistantBubble.innerHTML = `<div style="font-size:10px; font-weight:700; color:var(--emerald-light); margin-bottom:4px;">${chatHeaderTitle}</div><div>${window.AppUtils.renderMarkdown(fullAnswer)}</div>`;
        if (fullAnswer.trim()) {
          window.AppState.revChatHistory.push({ role: 'assistant', content: fullAnswer });
        }
      } catch (e) {
        const isAbort = e.name === 'AbortError' || (window.AppState.revChatAbortController && window.AppState.revChatAbortController.signal.aborted);
        if (fullAnswer.trim()) {
          const savedText = isAbort ? `${fullAnswer}\n\n*[⏹ Stream stopped by user]*` : `${fullAnswer}\n\n*[⚠️ Connection interrupted: ${e.message}]*`;
          window.AppState.revChatHistory.push({ role: 'assistant', content: savedText });
          assistantBubble.innerHTML = `<div style="font-size:10px; font-weight:700; color:var(--emerald-light); margin-bottom:4px;">⚡ REV CHAT</div><div>${window.AppUtils.renderMarkdown(savedText)}</div>`;
        } else {
          const errText = isAbort ? '*[⏹ Stream cancelled]*' : `❌ Error: ${e.message}`;
          window.AppState.revChatHistory.push({ role: 'assistant', content: errText });
          assistantBubble.innerHTML = `<div style="font-size:10px; font-weight:700; color:var(--rose-light); margin-bottom:4px;">❌ NOTICE</div><div>${window.AppUtils.renderMarkdown(errText)}</div>`;
        }
      }
    } catch (topErr) {
      console.error("sendRevChatMsg error:", topErr);
    } finally {
      this._isSendingRevMsg = false;
      window.AppState.isRevChatStreaming = false;
      window.AppState.revChatAbortController = null;
      window.AppState.activeRevReader = null;
      const sendBtn = document.getElementById('btn-rev-chat-send');
      if (sendBtn) {
        sendBtn.className = 'btn';
        sendBtn.innerText = '💬 Send';
        sendBtn.disabled = false;
      }
      const thread = document.getElementById('rev-chat-thread');
      if (thread) thread.scrollTop = thread.scrollHeight;
    }
  },

  // ---- Modal Dossier Copilot Chat ----
  clearModalChat() {
    const sym = (window.AppState.currentReportData && window.AppState.currentReportData.ticker) || 'STOCK';
    const date = (window.AppState.currentReportData && window.AppState.currentReportData.date) || '';
    const key = `${sym}_${date}`;
    if (!window.AppState.modalChatHistories) window.AppState.modalChatHistories = {};
    window.AppState.modalChatHistories[key] = [];
    window.AppState.modalChatHistory = [];
    this._modalSessionId = `sess_${sym}_${date}_${Date.now()}`;
    this.renderModalCopilotChat();
  },

  async initModalChatForTicker(ticker, date) {
    if (!ticker) return;
    const key = `${ticker}_${date || ''}`;
    try {
      const res = await fetch(`/api/copilot/chats/history?ticker=${encodeURIComponent(ticker)}&date=${encodeURIComponent(date || '')}`);
      const data = await res.json();
      if (data && data.messages && data.messages.length > 0) {
        window.AppState.modalChatHistory = data.messages.map(m => ({ role: m.role, content: m.content, date: m.date, created_at: m.created_at }));
        this._modalSessionId = data.messages[0].session_id || `sess_${ticker}_${date}_${Date.now()}`;
      } else {
        window.AppState.modalChatHistory = [];
        this._modalSessionId = `sess_${ticker}_${date}_${Date.now()}`;
      }
      if (!window.AppState.modalChatHistories) window.AppState.modalChatHistories = {};
      window.AppState.modalChatHistories[key] = window.AppState.modalChatHistory;
    } catch (e) {
      console.debug('Could not load ticker chat history from DB:', e);
    }
    this.renderModalCopilotChat();
    this.initModalChatResize();
  },

  renderModalCopilotChat() {
    const sym = (window.AppState.currentReportData && window.AppState.currentReportData.ticker) || 'STOCK';
    const date = (window.AppState.currentReportData && window.AppState.currentReportData.date) || '';
    const thread = document.getElementById('modal-chat-thread');
    if (!thread) return;

    const key = `${sym}_${date}`;
    if (!window.AppState.modalChatHistories) window.AppState.modalChatHistories = {};
    if (!window.AppState.modalChatHistory || window.AppState.modalChatHistory.length === 0) {
      window.AppState.modalChatHistory = window.AppState.modalChatHistories[key] || [];
    }

    const todayStr = new Date().toISOString().slice(0, 10);
    const isPastDossier = Boolean(date && date < todayStr);
    const hasHistory = window.AppState.modalChatHistory && window.AppState.modalChatHistory.length > 0;

    let historyHtml = window.AppState.modalChatHistory.map(m => {
      if (m.role === 'user') {
        return `<div style="align-self:flex-end; max-width:85%; background:var(--blue); color:#ffffff; border-radius:10px; padding:8px 12px; font-size:12.5px; line-height:1.4; box-shadow:0 1px 3px rgba(0,0,0,0.08);">${m.displayHtml || m.content}</div>`;
      } else {
        return `<div style="align-self:flex-start; max-width:92%; background:var(--bg-subtle); border:1px solid var(--border); border-radius:10px; padding:10px 14px; font-size:12.5px; line-height:1.5; color:var(--text-main); box-shadow:var(--shadow-card);">
          <div style="font-size:10px; font-weight:700; color:var(--blue); margin-bottom:4px;">🤖 COPILOT ANALYSIS</div>
          <div>${window.AppUtils.renderMarkdown(m.content)}</div>
        </div>`;
      }
    }).join('');

    if (hasHistory && isPastDossier) {
      historyHtml += `
        <div style="text-align:center; margin:10px 0; font-size:10.5px; font-weight:700; color:var(--blue); letter-spacing:0.5px; border-top:1px dashed var(--border); padding-top:8px; display:flex; align-items:center; justify-content:center; gap:6px;">
          <span>⚡</span>
          <span>RESUMING FROM ${date} — CURRENT EVENTS & LIVE TAPE PAST THIS CHAT ACTIVE</span>
        </div>
      `;
    }

    if (!historyHtml) {
      historyHtml = `
        <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px; padding:12px 14px;">
          <div style="font-weight:700; color:var(--blue); font-size:11px; margin-bottom:4px;">🤖 ACTIVE DOSSIER: ${sym} (${date})</div>
          <div style="font-size:12px; color:var(--text-muted); line-height:1.5;">Ask follow-up questions on this report. Answers are grounded in the active dossier context and live real-time quotes.</div>
        </div>
      `;
    }
    thread.innerHTML = historyHtml;
    thread.scrollTop = thread.scrollHeight;
  },

  handleModalCopilotBtnClick() {
    const btn = document.getElementById('btn-modal-chat-send');
    if (window.AppState.isModalChatStreaming && btn && btn.innerText.includes('Stop')) {
      this.stopModalCopilotStream();
    } else {
      this.sendModalCopilotMsg();
    }
  },

  stopModalCopilotStream() {
    if (window.AppState.modalChatAbortController) {
      try {
        window.AppState.modalChatAbortController.abort();
      } catch (e) {}
      window.AppState.modalChatAbortController = null;
    }
    window.AppState.isModalChatStreaming = false;
    const btn = document.getElementById('btn-modal-chat-send');
    if (btn) {
      btn.className = 'btn';
      btn.innerText = '💬 Send';
      btn.disabled = false;
    }
  },

  async sendModalCopilotMsg(customPrompt = null) {
    if (this._isSendingModalMsg) return;
    this._isSendingModalMsg = true;

    try {
      const input = document.getElementById('modal-chat-input');
      const imageData = this._modalAttachedImage || null;
      let question = customPrompt || (input ? input.value.trim() : '');

      if (!question && imageData) {
        question = "Please analyze this attached chart / snapshot and advise on the setup and trade execution.";
      }

      if (!question) {
        if (input) {
          input.focus();
          input.placeholder = "Please type a question or snap/paste a chart...";
        }
        return;
      }

      if (window.AppState.isModalChatStreaming) {
        this.stopModalCopilotStream();
        await new Promise(r => setTimeout(r, 60));
      }

      if (input) input.value = '';

      let sym = window.AppState.currentReportData ? window.AppState.currentReportData.ticker : '';
      let date = window.AppState.currentReportData ? window.AppState.currentReportData.date : '';
      if (!sym) {
        const titleEl = document.getElementById('modal-ticker-title');
        if (titleEl) {
          const m = titleEl.innerText.match(/^([A-Z0-9.\-]+)/);
          if (m) sym = m[1];
        }
      }
      if (!sym) sym = 'STOCK';
      if (!date) {
        const titleEl = document.getElementById('modal-ticker-title');
        if (titleEl) {
          const m = titleEl.innerText.match(/\((\d{4}-\d{2}-\d{2})\)/);
          if (m) date = m[1];
        }
      }
      const key = `${sym}_${date}`;

      // Use board context ONLY if user explicitly attached it via the 📌 button
      const boardContext = this._modalAttachedBoard ? this._modalAttachedBoard.context : null;
      const boardName = this._modalAttachedBoard ? this._modalAttachedBoard.name : null;

      let userDisplayHtml = this.escapeHtml(question);
      if (boardName) {
        userDisplayHtml = `<span style="font-size:10px; opacity:0.85; font-weight:700; display:block; margin-bottom:3px;">📌 ${boardName}</span>` + userDisplayHtml;
      }
      if (imageData) {
        userDisplayHtml += `<div style="margin-top:6px;"><img src="${imageData}" style="max-height:85px; max-width:100%; border-radius:6px; border:1px solid rgba(255,255,255,0.35);" /></div>`;
      }

      if (!window.AppState.modalChatHistories) window.AppState.modalChatHistories = {};
      if (!window.AppState.modalChatHistory) window.AppState.modalChatHistory = [];
      if (!this._modalSessionId) this._modalSessionId = `sess_${sym}_${date}_${Date.now()}`;

      // Clean history for payload (only valid string content and roles)
      const cleanHistory = (window.AppState.modalChatHistory || [])
        .filter(m => (m.role === 'user' || m.role === 'assistant') && m.content)
        .map(m => ({ role: m.role, content: typeof m.content === 'string' ? m.content : String(m.content) }));

      window.AppState.modalChatHistory.push({ role: 'user', content: question, displayHtml: userDisplayHtml });
      window.AppState.modalChatHistories[key] = window.AppState.modalChatHistory;
      this.renderModalCopilotChat();

      // Reset image attachment after staging to message
      this._modalAttachedImage = null;
      this.updateAttachmentUI('modal');

      const thread = document.getElementById('modal-chat-thread');
      const btn = document.getElementById('btn-modal-chat-send');
      
      window.AppState.modalChatAbortController = new AbortController();
      window.AppState.isModalChatStreaming = true;

      if (btn) {
        btn.className = 'btn danger';
        btn.innerText = '⏹ Stop';
        btn.disabled = false;
      }

      const assistantBubble = document.createElement('div');
      assistantBubble.style = 'align-self:flex-start; max-width:92%; background:var(--bg-subtle); border:1px solid var(--border); border-radius:10px; padding:10px 14px; font-size:12.5px; line-height:1.5; color:var(--text-main); box-shadow:var(--shadow-card);';
      assistantBubble.innerHTML = `<div style="font-size:10px; font-weight:700; color:var(--blue); margin-bottom:6px; display:flex; justify-content:space-between; align-items:center;"><span>🤖 COPILOT ANALYSIS (${sym})</span><button class="btn danger" onclick="AppChat.stopModalCopilotStream()" style="padding:1px 6px; font-size:9.5px; font-weight:700; cursor:pointer;">⏹ Stop</button></div><div><em>Connecting to local model stream...</em></div>`;
      if (thread) {
        thread.appendChild(assistantBubble);
        thread.scrollTop = thread.scrollHeight;
      }

      let fullAnswer = '';
      try {
        const response = await fetch('/api/copilot/chat/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: window.AppState.modalChatAbortController.signal,
          body: JSON.stringify({
            question,
            ticker: sym,
            date,
            history: cleanHistory,
            session_id: this._modalSessionId,
            board_context: boardContext,
            image_data: imageData
          })
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split('\n');
          buffer = lines.pop();

          for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith('data: ')) {
              const jsonStr = trimmed.slice(6);
              try {
                const data = JSON.parse(jsonStr);
                if (data.session_id) this._modalSessionId = data.session_id;
                if (data.status === 'connecting') {
                  assistantBubble.innerHTML = `<div style="font-size:10px; font-weight:700; color:var(--blue); margin-bottom:6px; display:flex; justify-content:space-between; align-items:center;"><span>🤖 COPILOT ANALYSIS (${sym})</span><button class="btn danger" onclick="AppChat.stopModalCopilotStream()" style="padding:1px 6px; font-size:9.5px; font-weight:700; cursor:pointer;">⏹ Stop</button></div><div><em>Analyzing real-time market data for $${sym}...</em></div>`;
                } else if (data.token) {
                  fullAnswer += data.token;
                  assistantBubble.innerHTML = `<div style="font-size:10px; font-weight:700; color:var(--emerald-light); margin-bottom:6px; display:flex; justify-content:space-between; align-items:center;"><span>🤖 COPILOT ANALYSIS (${sym})</span><button class="btn danger" onclick="AppChat.stopModalCopilotStream()" style="padding:1px 6px; font-size:9.5px; font-weight:700; cursor:pointer;">⏹ Stop</button></div><div>${window.AppUtils.renderMarkdown(fullAnswer)}</div>`;
                  if (thread) thread.scrollTop = thread.scrollHeight;
                } else if (data.error) {
                  fullAnswer += `\n\n❌ **Error:** ${data.error}`;
                  assistantBubble.innerHTML = `<div style="font-size:10px; font-weight:700; color:var(--rose-light); margin-bottom:4px;">❌ ERROR</div><div>${window.AppUtils.renderMarkdown(fullAnswer)}</div>`;
                }
              } catch (parseErr) {}
            }
          }
        }
        
        assistantBubble.innerHTML = `<div style="font-size:10px; font-weight:700; color:var(--emerald-light); margin-bottom:4px;">🤖 COPILOT ANALYSIS (${sym})</div><div>${window.AppUtils.renderMarkdown(fullAnswer)}</div>`;
        if (fullAnswer.trim()) {
          window.AppState.modalChatHistory.push({ role: 'assistant', content: fullAnswer });
          window.AppState.modalChatHistories[key] = window.AppState.modalChatHistory;
        }
      } catch (e) {
        const isAbort = e.name === 'AbortError' || (window.AppState.modalChatAbortController && window.AppState.modalChatAbortController.signal.aborted);
        if (fullAnswer.trim()) {
          const savedText = isAbort ? `${fullAnswer}\n\n*[⏹ Stream stopped by user]*` : `${fullAnswer}\n\n*[⚠️ Connection interrupted: ${e.message}]*`;
          window.AppState.modalChatHistory.push({ role: 'assistant', content: savedText });
          window.AppState.modalChatHistories[key] = window.AppState.modalChatHistory;
          assistantBubble.innerHTML = `<div style="font-size:10px; font-weight:700; color:var(--emerald-light); margin-bottom:4px;">🤖 COPILOT ANALYSIS (${sym})</div><div>${window.AppUtils.renderMarkdown(savedText)}</div>`;
        } else {
          const errText = isAbort ? '*[⏹ Stream cancelled]*' : `❌ Error: ${e.message}`;
          window.AppState.modalChatHistory.push({ role: 'assistant', content: errText });
          window.AppState.modalChatHistories[key] = window.AppState.modalChatHistory;
          assistantBubble.innerHTML = `<div style="font-size:10px; font-weight:700; color:var(--rose-light); margin-bottom:4px;">❌ NOTICE</div><div>${window.AppUtils.renderMarkdown(errText)}</div>`;
        }
      }
    } catch (topErr) {
      console.error("sendModalCopilotMsg error:", topErr);
    } finally {
      this._isSendingModalMsg = false;
      window.AppState.isModalChatStreaming = false;
      window.AppState.modalChatAbortController = null;
      const btn = document.getElementById('btn-modal-chat-send');
      if (btn) {
        btn.className = 'btn';
        btn.innerText = '💬 Send';
        btn.disabled = false;
      }
      const thread = document.getElementById('modal-chat-thread');
      if (thread) thread.scrollTop = thread.scrollHeight;
    }
  }
};
