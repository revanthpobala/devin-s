/**
 * Scratch Pad Controller
 * File-based shared message board. Type, send, everyone sees it.
 */
window.AppMessages = {
  _messages: [],

  escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  },

  async load() {
    try {
      const res = await AppApi.get("/api/pad");
      this._messages = res.messages || [];
      this.render();
    } catch (e) {
      console.error("Failed to load messages:", e);
    }
  },

  async send() {
    const input = document.getElementById("pad-input");
    if (!input) return;
    const content = input.value;
    if (content.length === 0) return;

    const sender = (window.AppState && window.AppState.userName) || "user";

    this._messages.push({
      id: 0,
      sender,
      content,
      created_at: new Date().toISOString(),
    });
    this.render();
    input.value = "";

    try {
      const res = await AppApi.post("/api/pad", { sender, content });
      if (res.id) {
        const last = this._messages[this._messages.length - 1];
        if (last) last.id = res.id;
      }
      await this.load();
    } catch (e) {
      console.error("Failed to send:", e);
    }
  },

  async delete(id) {
    try {
      await AppApi.delete(`/api/pad/${id}`);
      await this.load();
    } catch (e) {
      console.error("Failed to delete:", e);
    }
  },

  async clearAll() {
    if (!confirm("Clear all messages?")) return;
    try {
      await AppApi.post("/api/pad/clear");
      await this.load();
    } catch (e) {
      console.error("Failed to clear:", e);
    }
  },

  render() {
    const container = document.getElementById("pad-thread");
    if (!container) return;

    if (this._messages.length === 0) {
      container.innerHTML = `
        <div style="text-align:center; padding:30px; color:var(--text-muted); font-size:13px;">
          <div style="font-size:28px; margin-bottom:8px;">📝</div>
          No messages yet. Type below.
        </div>`;
      return;
    }

    container.innerHTML = this._messages
      .map((m) => {
        const isMe = m.sender !== "system";
        const time = this.formatTime(m.created_at);

        return `
          <div style="display:flex; flex-direction:column; ${isMe ? "align-items:flex-end;" : "align-items:flex-start;"} margin-bottom:6px;">
            <div style="font-size:10px; font-weight:700; color:${isMe ? "var(--blue)" : "var(--emerald)"}; margin-bottom:2px; padding:0 4px;">
              ${this.escapeHtml(m.sender)}${isMe ? " (you)" : ""}
            </div>
            <div style="max-width:85%; background:${isMe ? "var(--blue)" : "var(--bg-subtle)"}; color:${isMe ? "#ffffff" : "var(--text-main)"}; border:${isMe ? "none" : "1px solid var(--border)"}; border-radius:8px; padding:6px 10px; font-size:13px; line-height:1.4; ${isMe ? "box-shadow:0 1px 3px rgba(0,0,0,0.08);" : "box-shadow:var(--shadow-card);"}>
              ${this.escapeHtml(m.content)}
            </div>
            <div style="font-size:9px; color:var(--text-muted); margin-top:1px; padding:0 4px; display:flex; gap:6px; align-items:center;">
              <span>${time}</span>
              <button onclick="AppMessages.delete(${m.id})" style="background:none; border:none; cursor:pointer; font-size:10px; color:var(--rose-light); padding:0 2px;" title="Delete">✕</button>
            </div>
          </div>`;
      })
      .join("");

    container.scrollTop = container.scrollHeight;
  },

  formatTime(isoStr) {
    if (!isoStr) return "";
    try {
      const d = new Date(isoStr);
      const now = new Date();
      const diffMins = Math.floor((now - d) / 60000);
      if (diffMins < 1) return "Just now";
      if (diffMins < 60) return `${diffMins}m ago`;
      const diffHours = Math.floor(diffMins / 60);
      if (diffHours < 24) return `${diffHours}h ago`;
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (e) {
      return isoStr;
    }
  },

  init() {
    this.load();

    const input = document.getElementById("pad-input");
    if (input) {
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          this.send();
        }
      });
    }
  },
};
