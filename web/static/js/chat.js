/**
 * REV CHAT Sidebar & Modal Dossier Copilot Controller with SQLite 15-Day History Persistence
 */
window.AppChat = {
  // The sidebar thread lives in SQLite; the session id (and portfolio mode) are kept in
  // localStorage so every page load resumes the same conversation instead of starting fresh.
  _revSid: (() => { try { return localStorage.getItem('rev_chat_sid') || null; } catch (e) { return null; } })(),
  _revPortfolio: (() => { try { return localStorage.getItem('rev_chat_portfolio') === '1'; } catch (e) { return false; } })(),
  get _revSessionId() { return this._revSid; },
  set _revSessionId(v) {
    this._revSid = v || null;
    try { v ? localStorage.setItem('rev_chat_sid', v) : localStorage.removeItem('rev_chat_sid'); } catch (e) { /* storage unavailable */ }
  },
  get _portfolioMode() { return this._revPortfolio; },
  set _portfolioMode(v) {
    this._revPortfolio = !!v;
    try { localStorage.setItem('rev_chat_portfolio', v ? '1' : '0'); } catch (e) { /* storage unavailable */ }
  },
  _modalSessionId: null,
  _portfolioSessionId: null,
  _forcePortfolioChat: false,
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
  // ---- Desk-scoped chat context -----------------------------------------------------------------
  // The chat follows the page you are actually on. No stale ticker, no generic "Market" placeholder:
  // each desk contributes the real data it is displaying right now, so "which of these is worth a
  // trade?" can be answered from what the user is looking at.
  deskLabel() {
    const desk = (window.AppState && window.AppState.currentDesk) || 'swing';
    const map = {
      today: 'Today', swing: 'Swing Desk', watchlist: 'Watchlist', portfolio: 'Portfolio',
      intraday: 'Intraday', alerts: 'Alerts', trades: 'Trades', screener: 'Screener',
      logs: 'Logs', journal: 'Journal', record: 'Record',
    };
    return map[desk] || 'Desk';
  },

  buildDeskBoardContext() {
    const desk = (window.AppState && window.AppState.currentDesk) || 'swing';

    if (desk === 'portfolio') return this.buildPortfolioBoardContext();
    if (desk === 'today') return this.buildTodayDeskContext();
    if (desk === 'watchlist') return this.buildWatchlistDeskContext();
    if (desk === 'intraday') return this.buildIntradayDeskContext();

    return { name: `${this.deskLabel()} Desk`, context: `**ACTIVE DESK:** User is viewing the ${this.deskLabel()} desk. They have not focused a specific ticker, so treat their question as being about this desk's data (not a single stock).` };
  },

  buildTodayDeskContext() {
    const desk = window.AppDesk;
    const data = (desk && desk._lastTodayData) || {};
    const pulse = data.market_pulse || {};
    const regime = data.market_regime || {};
    const opps = data.top_opportunities || [];
    const alerts = data.actionable_alerts || [];
    if (!opps.length && !Object.keys(pulse).length) return null;

    const lines = [];
    lines.push(`**LIVE TODAY DESK — the exact setups, alerts, and market pulse on screen (Date: ${data.date || 'today'}):**`);

    const statusBits = [];
    if (pulse.spy) statusBits.push(`SPY $${parseFloat(pulse.spy).toFixed(2)}`);
    if (pulse.qqq) statusBits.push(`QQQ $${parseFloat(pulse.qqq).toFixed(2)}`);
    if (pulse.vix) statusBits.push(`VIX ${parseFloat(pulse.vix).toFixed(2)}`);
    if (pulse.market_status_text) statusBits.push(pulse.market_status_text);
    if (statusBits.length) lines.push(`• MARKET PULSE: ${statusBits.join(' | ')}`);

      // Regime gauges: Fear & Greed, VIX band, SPY tide, breadth, dramatic movers
      if (regime && regime.available) {
      const rgBits = [];
      if (regime.fear_greed && regime.fear_greed.score !== undefined) rgBits.push(`Fear & Greed ${regime.fear_greed.score.toFixed(0)}/100 (${regime.fear_greed.rating || 'n/a'})`);
      if (regime.vix && regime.vix.price) rgBits.push(`VIX ${parseFloat(regime.vix.price).toFixed(2)}`);
      if (regime.tide) rgBits.push(`Tide: ${regime.tide.trend} (SPY 20EMA $${regime.tide.ema20} vs 50SMA $${regime.tide.sma50}, 20d ${regime.tide.spy_20d_return >= 0 ? '+' : ''}${regime.tide.spy_20d_return}%)`);
      if (regime.breadth && regime.breadth.universe) rgBits.push(`Breadth ${regime.breadth.advancing}▲ / ${regime.breadth.declining}▼ / ${regime.breadth.flat}▬ of ${regime.breadth.universe}`);
      if (rgBits.length) lines.push(`• MARKET REGIME${regime.as_of_et ? ` (${regime.as_of_et})` : ''}: ${rgBits.join(' | ')}`);

      const mv = regime.movers || {};
      const drops = mv.drops || [];
      const spikes = mv.spikes || [];
      if (drops.length || spikes.length) {
        const fmt = (r) => `${r.ticker} ${r.chg_pct >= 0 ? '+' : ''}${r.chg_pct}% ($${r.price}${r.held ? ', HELD' : ''}${r.drastic ? ', DRASTIC' : ''})`;
        lines.push(`• DRAMATIC MOVERS (|Δ|≥3% across ${mv.universe_size || 0} tracked names):`);
        if (drops.length) lines.push(`   ▼ Drops: ${drops.map(fmt).join(' | ')}`);
        if (spikes.length) lines.push(`   ▲ Spikes: ${spikes.map(fmt).join(' | ')}`);
      }
    }


    const tab = (desk && desk._todaySubTab) || 'opps';
    const filter = (desk && desk._oppsFilter) || 'ALL';
    lines.push(`• ACTIVE VIEW: sub-tab "${tab}" | filter "${filter}" | ${opps.length} actionable opportunities | ${alerts.length} actionable alerts`);

    if (data.funnel) {
      const f = data.funnel;
      lines.push(`• PIPELINE FUNNEL: alerts ${f.alerts || 0} → local PASS ${f.local_pass || 0} → measured ${f.measured || 0} → deep done ${f.deep_done || 0} → actionable ${f.actionable || 0}`);
    }

    const visible = opps.slice(0, 12);
    if (visible.length) {
      lines.push('\n**ACTIONABLE OPPORTUNITY CARDS ON SCREEN (ticker, side, spot, distance to entry, R:R, conviction, levels):**');
      visible.forEach(o => {
        const sym = (o.ticker || '').toUpperCase();
        const spot = o.spot_price ? `$${parseFloat(o.spot_price).toFixed(2)}` : 'n/a';
        const dist = o.in_zone ? '🎯 IN ZONE' : `${(parseFloat(o.dist_pct) || 0) >= 0 ? '+' : ''}${(parseFloat(o.dist_pct) || 0).toFixed(2)}%`;
        const rr = o.live_rr > 0 ? `R:R ${parseFloat(o.live_rr).toFixed(2)}:1` : 'R:R n/a';
        const entry = o.entry_zone_low && o.entry_zone_high ? `$${parseFloat(o.entry_zone_low).toFixed(2)}-$${parseFloat(o.entry_zone_high).toFixed(2)}` : 'n/a';
        const stop = o.stop_loss ? `$${parseFloat(o.stop_loss).toFixed(2)}` : 'n/a';
        const t1 = o.target_1 ? `$${parseFloat(o.target_1).toFixed(2)}` : 'n/a';
        const conv = o.conviction !== undefined && o.conviction !== null ? `${o.conviction}/10` : 'n/a';
        lines.push(`• **${sym}** ${o.side || ''} [${o.in_zone ? 'IN ZONE' : `${dist} from entry`}] | Spot ${spot} | ${rr} | Conviction ${conv} | Vehicle ${o.vehicle_type || 'shares'} | Entry ${entry} | Stop ${stop} | T1 ${t1}${o.score ? ` | Score ${o.score}` : ''}`);
      });
    }

    const inZone = opps.filter(o => o.in_zone).map(o => (o.ticker || '').toUpperCase());
    const coiling = opps.filter(o => !o.in_zone && o.priority_tier <= 2).map(o => (o.ticker || '').toUpperCase());
    if (inZone.length) lines.push(`\n• IN ENTRY ZONE NOW: ${inZone.join(', ')}`);
    if (coiling.length) lines.push(`• COILING TRIGGERS (tier ≤2, not yet in zone): ${coiling.join(', ')}`);

    if (alerts.length) {
      const cleanAlerts = alerts.filter(a => a && typeof a === 'object');
      if (cleanAlerts.length) {
        lines.push(`\n**ACTIONABLE ALERTS:** ${cleanAlerts.slice(0, 6).map(a => `${(a.ticker || '?').toUpperCase()} (${a.verdict || a.side || 'alert'})`).join(' | ')}`);
      }
    }

    // Deterministic action layer: bat signal, per-setup risk flags, event radar, calibration
    const ap = data.action_panel || {};
    if (ap.bat_signal && ap.bat_signal.action) {
      const b = ap.bat_signal;
      lines.push(`\n**🦇 BAT SIGNAL (deterministic): ${b.action}** — ${b.why || ''} Suggested sizing: ${b.size || 'n/a'}. (Live counts: ${b.headline || 'n/a'})`);
    }
    if (ap.risk_flags && Object.keys(ap.risk_flags).length) {
      lines.push('\n**⚠️ RISK FLAGS (deterministic cross-check — these change takeability):**');
      Object.entries(ap.risk_flags).slice(0, 12).forEach(([sym, v]) => {
        const flags = (v && Array.isArray(v.flags)) ? v.flags.filter(f => f && typeof f === 'object') : [];
        const labels = flags.map(f => `${f.tone === 'rose' ? '⛔' : (f.tone === 'cyan' ? '👤' : '⚠️')} ${f.label || 'flag'}`).join(' , ');
        lines.push(`   • **${sym || '?'}**: ${labels || 'see desk card'}`);
      });
    }
    if (ap.event_radar && ap.event_radar.length) {
      lines.push('\n**📡 EVENT RADAR (drastic movers with cause and book impact):**');
      ap.event_radar.filter(m => m && typeof m === 'object').slice(0, 6).forEach(m => {
        const chg = Number(m.chg_pct) || 0;
        lines.push(`   • **${m.ticker || '?'}** ${chg >= 0 ? '+' : ''}${chg}% ($${m.price || 0})${m.held ? ' — YOU HOLD THIS' : ''} | Cause: ${m.cause || 'unknown'} | Stance: ${m.stance || 'n/a'}`);
      });
    }
    const cal = ap.calibration || {};
    if (cal.real && (cal.real.scored || 0) > 0) {
      const r = cal.real, m = cal.model || {}, cov = cal.coverage || {};
      lines.push(`\n**🎓 CALIBRATION — SPLIT BY DATA POOL (never blended):**`);
      lines.push(`   • PAPER (100 shares at the thesis spot, resolved on real bars): ${(ap.calibration.paper.aggregate || {}).resolved || 0} resolved | ${(ap.calibration.paper.aggregate || {}).thesis_correct_rate_pct}% of theses reached their target | avg ${(ap.calibration.paper.aggregate || {}).avg_r}R | PF ${(ap.calibration.paper.aggregate || {}).profit_factor} | total $${(ap.calibration.paper.aggregate || {}).total_pnl_dollars} | MFE/MAE ${(ap.calibration.paper.aggregate || {}).avg_mfe_pct}% / ${(ap.calibration.paper.aggregate || {}).avg_mae_pct}% — this is the screen's own hit rate, independent of the user's execution.`);
      lines.push(`   • REAL executed trades (${r.scored} scored, ${r.unmeasured} unmeasured): win rate ${r.win_rate_pct}% vs a ${r.breakeven_win_rate_pct}% breakeven implied by the payoff ratio | profit factor ${r.profit_factor} | expectancy ${r.expectancy}R | total ${r.total_realized_r}R`);
      if ((m.scored || 0) > 0) {
        lines.push(`   • MODEL simulated (${m.scored} scored): win rate ${m.win_rate_pct}% | profit factor ${m.profit_factor} | expectancy ${m.expectancy}R — this is a backtest with perfect fills, NOT evidence about the user's execution; do not size from it.`);
      }
      if (cov.available && cov.closed_total) {
        lines.push(`   • DATA QUALITY: only ${cov.with_exit_price}/${cov.closed_total} (${cov.exit_coverage_pct}%) closed trades recorded an exit price and ${cov.stop_coverage_pct}% a stop level, so R exists for a subset. Treat the real-trade win rate as a small-sample estimate and say so.`);
      }
      (cal.reasoning || []).forEach(x => lines.push(`   • ${x}`));
    }

    const text = lines.join('\n');
    return { name: 'Today Desk', context: text };
  },

  buildWatchlistDeskContext() {
    const wl = window.AppWatchlist;
    const targets = (wl && wl._allTargets) || [];
    if (!targets.length) return null;

    const lines = ['**LIVE WATCHLIST — every research target on screen (spot, distance to entry, stop, targets, status):**'];
    targets.slice(0, 25).forEach(t => {
      const sym = (t.ticker || '').toUpperCase();
      const spot = t.last_price ? `$${parseFloat(t.last_price).toFixed(2)}` : 'n/a';
      const ez = t.entry_zone_low && t.entry_zone_high ? `$${parseFloat(t.entry_zone_low).toFixed(2)}-$${parseFloat(t.entry_zone_high).toFixed(2)}` : 'n/a';
      lines.push(`• **${sym}** [${t.status || 'n/a'}] ${t.verdict || ''} | Spot ${spot} | Entry ${ez} | Stop ${t.tactical_stop ? '$' + parseFloat(t.tactical_stop).toFixed(2) : 'n/a'} | T1 ${t.target_1 ? '$' + parseFloat(t.target_1).toFixed(2) : 'n/a'}`);
    });
    const byStatus = {};
    targets.forEach(t => { const s = (t.status || 'UNKNOWN').toUpperCase(); byStatus[s] = (byStatus[s] || 0) + 1; });
    lines.push(`\n• STATUS MIX: ${Object.entries(byStatus).map(([k, v]) => `${k} ${v}`).join(' | ')}`);
    return { name: 'Watchlist Desk', context: lines.join('\n') };
  },

  buildIntradayDeskContext() {
    const id = window.AppIntraday;
    const positions = (id && id._rawPositions) || [];
    if (!positions.length) {
      return { name: 'Intraday Desk', context: '**ACTIVE DESK:** Intraday desk is open and there are currently **no open intraday positions**. Answer questions about the live monitor, stops, and EOD flatten rules for positions if they exist.' };
    }
    const lines = ['**LIVE INTRADAY DESK — open monitor positions on screen:**'];
    positions.slice(0, 12).forEach(p => {
      const sym = (p.ticker || '').toUpperCase();
      const entry = p.entry_price ? `$${parseFloat(p.entry_price).toFixed(2)}` : 'n/a';
      const spot = p.current_price ? `$${parseFloat(p.current_price).toFixed(2)}` : 'n/a';
      const pnl = p.pnl_pct !== undefined && p.pnl_pct !== null ? `${parseFloat(p.pnl_pct) >= 0 ? '+' : ''}${parseFloat(p.pnl_pct).toFixed(2)}%` : 'n/a';
      lines.push(`• **${sym}** ${(p.side || '').toUpperCase()} | Entry ${entry} | Live ${spot} | P&L ${pnl} | Stop ${p.stop ? '$' + parseFloat(p.stop).toFixed(2) : 'n/a'} | T1 ${p.target ? '$' + parseFloat(p.target).toFixed(2) : 'n/a'} | Strategy ${p.strategy || 'n/a'}`);
    });
    return { name: 'Intraday Desk', context: lines.join('\n') };
  },

  buildPortfolioBoardContext() {
    const pf = window.AppPortfolio;
    if (!pf) return null;

    const summary = pf.summaryData || {};
    const analysis = pf.analysisData || {};
    const perf = analysis.performance || {};
    const alloc = analysis.allocation || {};
    const briefing = analysis.briefing || {};
    const positions = (pf.positionsData || []).slice(0, 75);
    if (!positions.length && !summary.total_liquidation_value) return null;

    const money = (v, d = 2) => {
      const n = Number(v) || 0;
      return `${n < 0 ? '-' : '+'}$${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })}`;
    };
    const plain = (v, d = 2) => `$${(Number(v) || 0).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })}`;

    const lines = [];
    lines.push('**LIVE SCHWAB PORTFOLIO DESK — THE EXACT TICKERS, CONTRACTS & TRADES THE USER IS VIEWING:**');

    const filters = [];
    if (pf.activeAccountFilter && pf.activeAccountFilter !== 'ALL') filters.push(`Account=${pf.activeAccountFilter}`);
    if (pf.activeAssetFilter && pf.activeAssetFilter !== 'ALL') filters.push(`Asset=${pf.activeAssetFilter}`);
    if (pf.searchQuery) filters.push(`Search="${pf.searchQuery}"`);
    if (pf.sortBy) filters.push(`Sort=${pf.sortBy}`);
    if (filters.length) lines.push(`• ACTIVE VIEW FILTERS: ${filters.join(' | ')}`);

    lines.push(
      `• TOTAL LIQUIDATION VALUE: ${plain(summary.total_liquidation_value)} | ` +
      `TODAY'S DAY P/L: ${money(summary.total_day_pnl)} (${summary.total_day_pnl_pct >= 0 ? '+' : ''}${(Number(summary.total_day_pnl_pct) || 0).toFixed(2)}%) | ` +
      `TOTAL UNREALIZED P/L: ${money(summary.total_unrealized_pnl)} (${summary.total_unrealized_pnl_pct >= 0 ? '+' : ''}${(Number(summary.total_unrealized_pnl_pct) || 0).toFixed(2)}%) | ` +
      `CASH: ${plain(summary.total_cash_balance)} | POSITIONS: ${summary.position_count || positions.length}`
    );
    if (alloc.equity_pct !== undefined) {
      lines.push(
        `• ALLOCATION: Equities ${alloc.equity_pct}% (${plain(alloc.equity_val, 0)}) | Mutual Funds ${alloc.mutual_fund_pct}% (${plain(alloc.mutual_fund_val, 0)}) | ` +
        `Options ${alloc.option_pct}% (${plain(alloc.option_val, 0)}) | Cash ${alloc.cash_pct}% (${plain(alloc.cash_val, 0)}) | GOOGL+AMZN concentration: ${alloc.top2_concentration}%`
      );
    }
    if (summary.last_synced) lines.push(`• LAST SYNCED FROM SCHWAB API: ${summary.last_synced}`);

    lines.push('\n**EVERY POSITION ROW CURRENTLY ON SCREEN (Symbol, Contract, Qty, Cost, Live Px, Value, P/L, Account):**');
    positions.forEach(p => {
      const isOpt = p.asset_type === 'OPTION';
      const sym = p.underlying_symbol || p.symbol;
      const acct = `${p.account_type === 'MARGIN' ? 'MARGIN' : 'CASH'} ${p.account_number_masked || ''}`.trim();
      const qty = Number(p.quantity) || 0;
      const px = (v) => (v !== null && v !== undefined && !isNaN(parseFloat(v)) ? parseFloat(v) : 0);
      let head = `• ${sym} [${p.asset_type}] — ${qty} ${isOpt ? 'ct' : 'sh'}`;
      if (isOpt) {
        const strike = p.option_strike !== null && p.option_strike !== undefined ? `$${parseFloat(p.option_strike).toFixed(2)}` : 'strike n/a';
        head += ` ${p.option_expiration || 'exp n/a'} ${strike} ${p.option_type || ''}`.replace(/\s+/g, ' ');
      }
      if (p.description) head += ` — ${p.description}`;
      const detail =
        `Avg ${plain(p.average_price)} | Live ${plain(p.current_price)} | MV ${plain(p.market_value)} | ` +
        `Day ${money(p.day_profit_loss)} | Unreal ${money(p.unrealized_profit_loss)} (${px(p.unrealized_profit_loss_pct) >= 0 ? '+' : ''}${px(p.unrealized_profit_loss_pct).toFixed(1)}%) | ${acct}`;
      lines.push(`${head}\n   ${detail}`);
    });

    const trades = (analysis.recent_trades || []).slice(0, 12);
    if (trades.length) {
      lines.push('\n**RECENT FILLED ORDERS (Schwab API verified):**');
      trades.forEach(t => {
        lines.push(`• ${t.date} | ${t.category} | ${t.order_type} | ${t.legs} | ${t.price ? '$' + t.price : 'MKT'}`);
      });
    }

    if (briefing.what_happened) lines.push(`\n**EXECUTIVE BRIEFING (WHAT HAPPENED):** ${briefing.what_happened}`);
    if (briefing.what_is_happening) lines.push(`**RISK POSTURE (WHAT IS HAPPENING NOW):** ${briefing.what_is_happening}`);

    const draggers = (perf.top_draggers || []).slice(0, 5);
    const gainers = (perf.top_gainers || []).slice(0, 5);
    if (draggers.length) lines.push(`**TOP DAGGERS TODAY:** ${draggers.map(d => `${d.symbol} ${money(d.day_pnl)}`).join(' | ')}`);
    if (gainers.length) lines.push(`**TOP GAINERS TODAY:** ${gainers.map(g => `${g.symbol} ${money(g.day_pnl)}`).join(' | ')}`);

    let text = lines.join('\n');
    if (text.length > 11000) text = text.slice(0, 11000) + '\n...[roster truncated for context window]';
    return { name: 'Schwab Portfolio Desk', context: text };
  },

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
      const sym = (window.AppState.activeChatTicker || (window.AppState.currentReportData ? window.AppState.currentReportData.ticker : '') || 'STOCK').toUpperCase();
      const currentTab = window.AppState.activeDossierTab || 'plan';
      const data = window.AppState.currentReportData || {};

      if (currentTab === 'optflow' || currentTab === 'flow') {
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
          name: `Options Flow (${sym})`,
          context: `**CURRENTLY VISIBLE OPTIONS FLOW TABLE FOR ${sym}:**\n${tableText || 'Options Flow view active.'}`
        };
      } else if (currentTab === 'dp') {
        const text = document.getElementById('modal-body') ? document.getElementById('modal-body').innerText.slice(0, 4000) : '';
        return {
          name: `Dark Pool Tape (${sym})`,
          context: `**CURRENTLY VISIBLE DARK POOL / OFF-EXCHANGE ORDER FLOW FOR ${sym}:**\n${text || 'Dark Pool view active.'}`
        };
      } else if (currentTab === 'arb') {
        const text = data.arbitration_md || (document.getElementById('modal-body') ? document.getElementById('modal-body').innerText : '');
        return {
          name: `PM Arbitration (${sym})`,
          context: `**PM ARBITRATION RULING FOR ${sym}:**\n${text ? text.slice(0, 4500) : 'Arbitration view active.'}`
        };
      } else if (currentTab === 'sum' || currentTab === 'modela') {
        const text = data.summary_md || (document.getElementById('modal-body') ? document.getElementById('modal-body').innerText : '');
        return {
          name: `Model A Synthesis (${sym})`,
          context: `**MODEL A SYNTHESIS DOSSIER FOR ${sym}:**\n${text ? text.slice(0, 4500) : 'Synthesis view active.'}`
        };
      } else if (currentTab === 'ind' || currentTab === 'modelb') {
        const text = data.independent_md || (document.getElementById('modal-body') ? document.getElementById('modal-body').innerText : '');
        return {
          name: `Model B Independent (${sym})`,
          context: `**MODEL B INDEPENDENT REPORT FOR ${sym}:**\n${text ? text.slice(0, 4500) : 'Independent Report view active.'}`
        };
      } else {
        const text = (document.getElementById('pane-tab-suggested') ? document.getElementById('pane-tab-suggested').innerText : (document.getElementById('modal-body') ? document.getElementById('modal-body').innerText : ''));
        return {
          name: `Suggested Position (${sym})`,
          context: `**CURRENTLY VISIBLE SUGGESTED POSITION SETUP FOR ${sym}:**\n${text ? text.slice(0, 4500) : 'Position Setup view active.'}`
        };
      }
    } else {
      // Main dashboard view: follow the active desk (Today / Watchlist / Portfolio / Intraday / ...)
      const currentDesk = (window.AppState && window.AppState.currentDesk) || 'swing';
      if (currentDesk !== 'swing') {
        const deskCtx = this.buildDeskBoardContext();
        if (deskCtx) return deskCtx;
      }

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

  ensureChatOpen() {
    const container = document.querySelector('.container');
    if (container && container.classList.contains('chat-collapsed')) this.toggleCollapse();
  },

  toggleCollapse() {
    const container = document.querySelector('.container');
    if (!container) return;
    const isCollapsed = container.classList.toggle('chat-collapsed');
    try {
      localStorage.setItem('rev_chat_collapsed', isCollapsed ? '1' : '0');
    } catch (e) {}
    this.updateCollapseUi(isCollapsed);
  },

  updateCollapseUi(isCollapsed) {
    // Header chat toggle button
    const headerBtn = document.getElementById('btn-header-chat');
    const headerTxt = document.getElementById('txt-header-chat');
    if (headerBtn) {
      if (isCollapsed) {
        headerBtn.classList.add('chat-collapsed-state');
        headerBtn.title = "Open Rev Chat Sidebar";
        if (headerTxt) headerTxt.innerText = "Show Chat";
      } else {
        headerBtn.classList.remove('chat-collapsed-state');
        headerBtn.title = "Collapse Rev Chat (Full Screen Mode)";
        if (headerTxt) headerTxt.innerText = "Hide Chat";
      }
    }

    // Floating edge trigger on right screen margin
    const floatTab = document.getElementById('floating-chat-trigger');
    if (floatTab) {
      floatTab.style.display = isCollapsed ? 'flex' : 'none';
    }

    // Modal / legacy toggle button if present
    const toggleBtn = document.getElementById('btn-toggle-chat');
    const toggleTxt = document.getElementById('txt-toggle-chat');
    if (toggleBtn) {
      if (isCollapsed) {
        toggleBtn.classList.add('active');
        toggleBtn.title = "Expand Rev Chat Sidebar";
        if (toggleTxt) toggleTxt.innerText = "Show Chat";
      } else {
        toggleBtn.classList.remove('active');
        toggleBtn.title = "Collapse Rev Chat (Full Screen Mode)";
        if (toggleTxt) toggleTxt.innerText = "Hide Chat";
      }
    }
  },

  initChatCollapse() {
    try {
      const saved = localStorage.getItem('rev_chat_collapsed');
      if (saved === '1') {
        const container = document.querySelector('.container');
        if (container) container.classList.add('chat-collapsed');
        this.updateCollapseUi(true);
      } else {
        this.updateCollapseUi(false);
      }
    } catch (e) {}
  },

  initChatResize() {
    this.initPasteListeners();
    this.initChatInputVerticalResize();
    this.initChatCollapse();
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
    this.initChatInputVerticalResize();
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

  // ---- Vertical Resizing for Chat Input Area (Draggable to Top) ----
  applyChatInputHeight(px, source = 'sidebar') {
    const isSidebar = source === 'sidebar';
    const containerId = isSidebar ? 'rev-chat-input-container' : 'modal-chat-input-container';
    const container = document.getElementById(containerId);
    if (!container) return px;

    const parent = container.parentElement;
    const parentH = parent ? parent.clientHeight : 700;
    // Allow dragging up to 82% of the container/sidebar
    const maxH = Math.max(160, Math.floor(parentH - (isSidebar ? 120 : 160)));
    const clamped = Math.max(78, Math.min(px, maxH));

    container.style.height = `${clamped}px`;
    container.style.flex = 'none';

    // Update expand button icon & tooltip
    const expandBtnId = isSidebar ? 'btn-rev-chat-expand' : 'btn-modal-chat-expand';
    const expandBtn = document.getElementById(expandBtnId);
    if (expandBtn) {
      if (clamped > 150) {
        expandBtn.innerText = '⤡';
        expandBtn.title = 'Collapse input area to compact (Double-click bar above)';
      } else {
        expandBtn.innerText = '⤢';
        expandBtn.title = 'Expand input area to top (Drag bar above or click)';
      }
    }

    try {
      localStorage.setItem(isSidebar ? 'rev_chat_input_h' : 'modal_chat_input_h', clamped);
    } catch (e) {}

    return clamped;
  },

  toggleRevChatInputExpand() {
    const container = document.getElementById('rev-chat-input-container');
    if (!container) return;
    const currentH = container.getBoundingClientRect().height;
    if (currentH > 150) {
      this.applyChatInputHeight(108, 'sidebar');
    } else {
      const parent = container.parentElement;
      const targetH = parent ? Math.min(450, Math.round(parent.clientHeight * 0.58)) : 320;
      this.applyChatInputHeight(targetH, 'sidebar');
    }
  },

  toggleModalChatInputExpand() {
    const container = document.getElementById('modal-chat-input-container');
    if (!container) return;
    const currentH = container.getBoundingClientRect().height;
    if (currentH > 150) {
      this.applyChatInputHeight(108, 'modal');
    } else {
      const parent = container.parentElement;
      const targetH = parent ? Math.min(450, Math.round(parent.clientHeight * 0.58)) : 320;
      this.applyChatInputHeight(targetH, 'modal');
    }
  },

  initChatInputVerticalResize() {
    const setupVResize = (resizerId, containerId, source) => {
      const resizer = document.getElementById(resizerId);
      const container = document.getElementById(containerId);
      if (!resizer || !container || resizer._hasVResizeListener) return;
      resizer._hasVResizeListener = true;

      // Restore saved height
      try {
        const savedH = parseFloat(localStorage.getItem(source === 'sidebar' ? 'rev_chat_input_h' : 'modal_chat_input_h'));
        if (savedH >= 80) {
          this.applyChatInputHeight(savedH, source);
        } else {
          this.applyChatInputHeight(108, source);
        }
      } catch (e) {
        this.applyChatInputHeight(108, source);
      }

      let dragging = false;
      let startY = 0;
      let startH = 0;

      const onMouseDown = (e) => {
        e.preventDefault();
        dragging = true;
        startY = e.clientY !== undefined ? e.clientY : (e.touches && e.touches[0] ? e.touches[0].clientY : 0);
        startH = container.getBoundingClientRect().height;
        resizer.classList.add('active');
        document.body.classList.add('chat-v-resizing');
      };

      const onMouseMove = (e) => {
        if (!dragging) return;
        const currentY = e.clientY !== undefined ? e.clientY : (e.touches && e.touches[0] ? e.touches[0].clientY : startY);
        // Dragging UP means currentY < startY, so delta > 0 (expanding height upwards)
        const delta = startY - currentY;
        this.applyChatInputHeight(startH + delta, source);
      };

      const onMouseUp = () => {
        if (!dragging) return;
        dragging = false;
        resizer.classList.remove('active');
        document.body.classList.remove('chat-v-resizing');
      };

      resizer.addEventListener('mousedown', onMouseDown);
      resizer.addEventListener('touchstart', onMouseDown, { passive: false });

      // Double-click resizer to toggle expand/collapse
      resizer.addEventListener('dblclick', (e) => {
        e.preventDefault();
        if (source === 'sidebar') {
          this.toggleRevChatInputExpand();
        } else {
          this.toggleModalChatInputExpand();
        }
      });

      window.addEventListener('mousemove', onMouseMove);
      window.addEventListener('touchmove', onMouseMove, { passive: false });
      window.addEventListener('mouseup', onMouseUp);
      window.addEventListener('touchend', onMouseUp);
    };

    setupVResize('rev-chat-input-resizer', 'rev-chat-input-container', 'sidebar');
    setupVResize('modal-chat-input-resizer', 'modal-chat-input-container', 'modal');
  },

  // ---- Collapsible Suggestions Toggle (Default Collapsed) ----
  toggleSuggestions(source = 'sidebar') {
    let bodyId = 'rev-chat-suggestions-body';
    let labelId = 'rev-chat-suggestions-label';
    let iconId = 'rev-chat-suggestions-icon';

    if (source === 'modal') {
      bodyId = 'modal-chat-suggestions-body';
      labelId = 'modal-chat-suggestions-label';
      iconId = 'modal-chat-suggestions-icon';
    } else if (source === 'alert') {
      bodyId = 'alert-chat-suggestions-body';
      labelId = 'alert-chat-suggestions-label';
      iconId = 'alert-chat-suggestions-icon';
    }

    const bodyEl = document.getElementById(bodyId);
    const labelEl = document.getElementById(labelId);
    const iconEl = document.getElementById(iconId);
    if (!bodyEl) return;

    const isHidden = bodyEl.style.display === 'none' || getComputedStyle(bodyEl).display === 'none';
    if (isHidden) {
      bodyEl.style.display = 'flex';
      if (labelEl) labelEl.innerText = 'Hide';
      if (iconEl) iconEl.classList.add('open');
    } else {
      bodyEl.style.display = 'none';
      if (labelEl) labelEl.innerText = 'Show';
      if (iconEl) iconEl.classList.remove('open');
    }
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
      const activeBorder = isCurrent ? 'border:1px solid var(--cyan-glow); background:rgba(6,182,212,0.14);' : 'border:1px solid var(--border); background:var(--bg-subtle);';
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
        if (String(sessionId).startsWith('sess_PORTFOLIO_')) {
          this._portfolioMode = true;
          this._portfolioSessionId = sessionId;
        } else {
          this._portfolioMode = false;
          this._portfolioSessionId = null;
        }
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

  async restoreRevChat() {
    const sid = this._revSessionId;
    if (!sid || (window.AppState.revChatHistory || []).length) return;
    const map = (messages) => messages.map(m => ({ role: m.role, content: m.content, date: m.date, created_at: m.created_at }));
    try {
      const res = await fetch(`/api/copilot/chats/history?session_id=${encodeURIComponent(sid)}`);
      const data = await res.json();
      const messages = (data && data.messages) || [];
      if (!messages.length || (window.AppState.revChatHistory || []).length) return;
      if (String(sid).startsWith('sess_PORTFOLIO_')) {
        this._portfolioSessionId = sid;
        this.enterPortfolioMode();
      }
      window.AppState.revChatHistory = map(messages);
      // The server keeps generating after you leave the page; if the last turn is still your
      // question, show a placeholder and pick the answer up from the database when it lands.
      if (messages[messages.length - 1].role === 'user') {
        window.AppState.revChatHistory.push({ role: 'assistant', pending: true, content: '⏳ Still generating. The answer will appear here when it finishes.' });
        this._pollRevAnswer(sid, map);
      }
      this.renderRevChat();
    } catch (e) {
      console.debug('Could not restore sidebar chat:', e);
    }
  },

  _pollRevAnswer(sid, map, tries = 0) {
    if (tries > 120) return;
    setTimeout(async () => {
      if (this._revSessionId !== sid) return;
      try {
        const res = await fetch(`/api/copilot/chats/history?session_id=${encodeURIComponent(sid)}`);
        const data = await res.json();
        const messages = (data && data.messages) || [];
        const hist = window.AppState.revChatHistory || [];
        if (!hist.some(m => m.pending)) return;
        if (messages.length && messages[messages.length - 1].role === 'assistant') {
          window.AppState.revChatHistory = map(messages);
          this.renderRevChat();
          return;
        }
      } catch (e) { /* retry */ }
      this._pollRevAnswer(sid, map, tries + 1);
    }, 3000);
  },

  async recoverActiveSession() {
    const sid = this._revSessionId;
    if (!sid) return;
    try {
      const res = await fetch(`/api/copilot/chats/history?session_id=${encodeURIComponent(sid)}`);
      const data = await res.json();
      const messages = (data && data.messages) || [];
      if (messages.length && messages[messages.length - 1].role === 'assistant') {
        const map = (msgs) => msgs.map(m => ({ role: m.role, content: m.content, date: m.date, created_at: m.created_at }));
        window.AppState.revChatHistory = map(messages);
        this.renderRevChat();
      }
    } catch (e) {
      console.debug('Failed to manually recover session:', e);
    }
  },

  _autoRecoverSession(sid, bubbleEl, headerTitle, tries = 0) {
    if (tries > 60 || !sid || this._revSessionId !== sid) return;
    setTimeout(async () => {
      if (this._revSessionId !== sid) return;
      try {
        const res = await fetch(`/api/copilot/chats/history?session_id=${encodeURIComponent(sid)}`);
        const data = await res.json();
        const messages = (data && data.messages) || [];
        if (messages.length) {
          const lastMsg = messages[messages.length - 1];
          if (lastMsg.role === 'assistant' && lastMsg.content && !lastMsg.content.includes('(interrupted: you left the page')) {
            const cleanContent = lastMsg.content;
            if (bubbleEl) {
              this._flushRcStreamRender(bubbleEl, cleanContent, headerTitle, false);
            }
            if (window.AppState.revChatHistory && window.AppState.revChatHistory.length) {
              const lastHist = window.AppState.revChatHistory[window.AppState.revChatHistory.length - 1];
              if (lastHist && lastHist.role === 'assistant') {
                lastHist.content = cleanContent;
              } else {
                window.AppState.revChatHistory.push({ role: 'assistant', content: cleanContent });
              }
            }
            return;
          }
        }
      } catch (err) {}
      this._autoRecoverSession(sid, bubbleEl, headerTitle, tries + 1);
    }, 2500);
  },

  startNewChat(target = 'modal') {
    const isModal = target === 'modal';
    if (isModal) {
      const sym = (window.AppState.activeChatTicker || (window.AppState.currentReportData && window.AppState.currentReportData.ticker) || 'STOCK').toUpperCase().trim();
      const date = (window.AppState.currentReportData && window.AppState.currentReportData.date) || '';
      this.stopModalCopilotStream(sym);
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
      this._portfolioSessionId = null;
      this._portfolioMode = false;
      window.AppState.revChatHistory = [];
      this.renderRevChat();
      const panel = document.getElementById('sidebar-prior-chats-panel');
      if (panel) panel.style.display = 'none';
    }
  },

  startFreshModalSession() {
    this.startNewChat('modal');
    const sym = (window.AppState.activeChatTicker || (window.AppState.currentReportData && window.AppState.currentReportData.ticker) || 'STOCK').toUpperCase().trim();
    if (window.AppUtils && typeof window.AppUtils.showToast === 'function') {
      window.AppUtils.showToast(`Started fresh isolated session for $${sym}`);
    }
  },

  // ---- REV CHAT Sidebar Logic ----
  clearRevChat() {
    window.AppState.revChatHistory = [];
    this._revSessionId = `sess_REV_${Date.now()}`;
    this.exitPortfolioMode();
    this.renderRevChat();
  },

  renderRevChat() {
    const thread = document.getElementById('rev-chat-thread');
    if (!thread) return;

    this.updatePortfolioModeBadge();
    thread.classList.toggle('rc-thread-portfolio', !!this._portfolioMode);

    const history = window.AppState.revChatHistory || [];
    if (history.length === 0) {
      thread.classList.toggle('rc-thread-portfolio', !!this._portfolioMode);
      if (this._portfolioMode) {
        thread.innerHTML = `
        <div class="rc-welcome rc-welcome-portfolio">
          <div class="rc-welcome-head"><span class="rc-welcome-emoji">💼</span>Portfolio Copilot Ready</div>
          <div class="rc-welcome-body">
            A dedicated assistant for your <strong>live Schwab book</strong> — every holding, contract, strike, expiration, and fill. It answers about <strong>your actual positions only</strong> — never hypotheticals, and never a stock you do not own.
            <div class="rc-quick-grid">
              <button class="rc-quick-btn" onclick="AppChat.askPortfolioCopilot('Give me a full executive summary of my portfolio: total liquidation value, today P&amp;L attribution, my biggest holdings with portfolio weights, every option leg with strike expiration and DTE, concentration risk, and a ranked action list for each material position.')"><span class="rc-quick-btn-ico">📊</span><span>Full portfolio summary &amp; ranked action list</span></button>
              <button class="rc-quick-btn" onclick="AppChat.askPortfolioCopilot('How concentrated am I, and what is my dry powder position right now?')"><span class="rc-quick-btn-ico">⚖️</span><span>Concentration &amp; dry powder check</span></button>
              <button class="rc-quick-btn" onclick="AppChat.askPortfolioCopilot('Review every option contract I am carrying: strike, expiration, DTE, mark vs basis, and theta/assignment risk for each leg.')"><span class="rc-quick-btn-ico">🧾</span><span>All option legs &amp; expiry risk</span></button>
              <button class="rc-quick-btn" onclick="AppChat.askPortfolioCopilot('What are my best covered call / PMCC candidates among my holdings right now, with exact strikes, expirations, and premium per contract?')"><span class="rc-quick-btn-ico">💰</span><span>Income engine: covered call / PMCC candidates</span></button>
              <button class="rc-quick-btn" onclick="AppChat.askPortfolioCopilot('Review my recent filled trades and tell me what worked, what did not, and what to repeat or stop.')"><span class="rc-quick-btn-ico">🧠</span><span>Post-mortem of my recent fills</span></button>
            </div>
          </div>
        </div>
      `;
        return;
      }
      thread.classList.remove('rc-thread-portfolio');
      thread.innerHTML = `
        <div class="rc-welcome">
          <div class="rc-welcome-head"><span class="rc-welcome-emoji">⚡</span>Rev Chat Ready</div>
          <div class="rc-welcome-body">
            Ask anything about the page you are on, tactical levels, or stock setups (<span class="tv-symbol-hover" data-ticker="WMT" style="cursor:pointer; color:var(--cyan-glow, #38bdf8); font-weight:700;">$WMT</span>, <span class="tv-symbol-hover" data-ticker="MSFT" style="cursor:pointer; color:var(--cyan-glow, #38bdf8); font-weight:700;">$MSFT</span>, <span class="tv-symbol-hover" data-ticker="AMZN" style="cursor:pointer; color:var(--cyan-glow, #38bdf8); font-weight:700;">$AMZN</span>). Live quotes &amp; options available on demand.
            <div class="rc-quick-grid">
              <button class="rc-quick-btn" onclick="AppChat.askRevChat('What is the tape doing right now? Give me the Fear and Greed read, the VIX regime, the market tide, breadth, and any drastic movers, then what it means for my setups.')"><span class="rc-quick-btn-ico">📡</span><span>Regime check: Fear &amp; Greed, VIX, tide, breadth</span></button>
              <button class="rc-quick-btn" onclick="AppChat.askRevChat('Which stocks dropped or spiked hard today, why, and does any of it hit something I hold?')"><span class="rc-quick-btn-ico">🌊</span><span>Biggest movers &amp; what caused them</span></button>
            </div>
            <div style="margin-top:8px; font-family:var(--font-mono, monospace); font-size:10.5px; color:var(--text-dim);">TIP · Click <strong>💼 Portfolio</strong> in the header for a dedicated assistant on your real Schwab holdings.</div>
          </div>
        </div>
      `;
      return;
    }

    thread.innerHTML = history.map(m => {
      if (m.role === 'user') {
        return `<div class="rc-msg rc-msg-user">${m.content}</div>`;
      } else {
        return `<div class="rc-msg rc-msg-assistant">
          <div class="rc-msg-head"><span class="rc-msg-who">⚡ REV CHAT</span></div>
          <div class="rc-msg-body">${window.AppUtils.renderMarkdown(m.content)}</div>
        </div>`;
      }
    }).join('');
    thread.scrollTop = thread.scrollHeight;
  },

  // --- REV CHAT focus -------------------------------------------------------------------
  // Single source of truth: window.AppState.revChatFocusTicker.
  //
  // The badge used to re-derive its value from currentReportData and #ticker-input whenever it
  // was handed an empty string, so "clear" was inexpressible: clearing set revChatFocusTicker to
  // '' and the badge immediately re-derived the ticker from whatever report was open. Sending the
  // next message did the same, which is why the focus appeared stuck once you had clicked around
  // a few stocks. Nothing here re-derives any more; only setRevChatFocus/clearRevChatFocus write.

  _focusTicker() {
    return (window.AppState.revChatFocusTicker || '').trim().toUpperCase();
  },

  setRevChatFocus(ticker) {
    const t = (ticker || '').trim().toUpperCase();
    if (!t || ['GENERAL', 'AUTO', 'NONE', 'ALL', 'STOCK', 'MARKET'].includes(t)) {
      return this.clearRevChatFocus();
    }
    window.AppState.revChatFocusTicker = t;
    window.AppState.activeChatTicker = t;
    window.AppState.revChatFocusCleared = false;
    this.updateSidebarFocusBadge();
    return t;
  },

  clearRevChatFocus() {
    window.AppState.revChatFocusTicker = '';
    window.AppState.activeChatTicker = '';
    window.AppState.revChatFocusCleared = true;
    const input = document.getElementById('ticker-input');
    if (input) input.value = '';
    this.updateSidebarFocusBadge();
    this._syncSidebarFocusInput();
  },

  // Back-compat alias: the badge's onclick in index.html calls clearSidebarFocus().
  clearSidebarFocus() {
    this.clearRevChatFocus();
  },

  _syncSidebarFocusInput() {
    const box = document.getElementById('rev-chat-focus-input');
    if (box) box.value = this._focusTicker();
  },

  updateSidebarFocusBadge(ticker) {
    const badge = document.getElementById('rev-chat-focus-badge');
    if (!badge) return;
    // An explicit argument wins; otherwise read the single source of truth. Never fall back to
    // currentReportData or #ticker-input -- that fallback is what made clearing impossible.
    const t = (ticker !== undefined && ticker !== null)
      ? String(ticker).trim().toUpperCase()
      : this._focusTicker();
    if (t) {
      badge.innerText = `🎯 Focus: $${t}`;
      badge.className = 'pill blue rev-chat-focus-badge active';
      badge.title = `Chat is focused on $${t}. Click to clear, or type a ticker to switch.`;
    } else {
      // No ticker focused: the chat is scoped to the desk/page the user is actually on.
      const deskName = this.deskLabel();
      badge.innerText = `🎯 Focus: ${deskName}`;
      badge.className = 'pill rev-chat-focus-badge';
      badge.title = `Chat is scoped to the ${deskName} desk (everything visible on this page). Click to type a ticker and focus on one stock instead.`;
    }
    this._syncSidebarFocusInput();
  },

  // Enter in the sidebar focus box -> focus on that ticker (or clear if blank).
  applySidebarFocusInput() {
    const box = document.getElementById('rev-chat-focus-input');
    const raw = box ? box.value : '';
    const t = this.setRevChatFocus(raw);
    if (box) box.value = window.AppState.revChatFocusTicker || '';
    return t;
  },

  askActiveChat(prompt) {
    const alertModal = document.getElementById('tv-alert-detail-modal');
    const isAlertOpen = alertModal && alertModal.style.display !== 'none';
    if (isAlertOpen && window.AppAlerts && typeof window.AppAlerts.sendModalCopilotMsg === 'function') {
      window.AppAlerts.sendModalCopilotMsg(prompt);
      return;
    }

    const reportModal = document.getElementById('report-modal');
    const isModalOpen = reportModal && reportModal.style.display !== 'none';
    if (isModalOpen) {
      if (reportModal.classList.contains('collapsed')) {
        if (window.AppSwing && typeof window.AppSwing.toggleMinimizeReportModal === 'function') {
          window.AppSwing.toggleMinimizeReportModal();
        }
      }
      this.askModalCopilot(prompt);
    } else {
      this.askRevChat(prompt);
    }
  },

  async handleSetAlertAction(queryString) {
    try {
      const params = new URLSearchParams(queryString || '');
      const ticker = (
        params.get('ticker') ||
        params.get('symbol') ||
        window.AppState?.activeChatTicker ||
        (window.AppState?.currentReportData && window.AppState.currentReportData.ticker) ||
        (document.getElementById('ticker-input') && document.getElementById('ticker-input').value) ||
        ''
      ).toUpperCase().replace(/^\$/, '').trim();
      const price = parseFloat(params.get('price') || params.get('threshold') || 0);
      const op = params.get('op') || '<=';
      if (!ticker || !price) {
        if (window.AppUtils && window.AppUtils.showToast) {
          window.AppUtils.showToast('Invalid alert parameters', 'error');
        }
        return;
      }
      if (window.AppUtils && window.AppUtils.showToast) {
        window.AppUtils.showToast(`⏳ Registering Tastytrade cloud alert for ${ticker} at ${op} $${price.toFixed(2)}...`, 'info');
      }
      const resp = await fetch('/api/tastytrade-alerts/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: ticker,
          threshold: price,
          operator: op.includes('<') ? '<' : '>',
        })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        if (window.AppUtils && window.AppUtils.showToast) {
          window.AppUtils.showToast(`🔔 Cloud Quote Alert Set for ${ticker} at ${op} $${price.toFixed(2)}! (Tastytrade push active)`, 'success');
        }
      } else {
        const errMsg = data.detail || data.error || 'Failed to set alert';
        if (window.AppUtils && window.AppUtils.showToast) {
          window.AppUtils.showToast(`Alert Note: ${errMsg}`, 'warning');
        }
      }
    } catch (e) {
      console.error('Error setting alert:', e);
      if (window.AppUtils && window.AppUtils.showToast) {
        window.AppUtils.showToast(`Error setting alert: ${e.message}`, 'error');
      }
    }
  },

  async handleSyncTastytradeAlertsAction(queryString) {
    try {
      const params = new URLSearchParams(queryString || '');
      const ticker = (
        params.get('ticker') ||
        params.get('symbol') ||
        window.AppState?.activeChatTicker ||
        (window.AppState?.currentReportData && window.AppState.currentReportData.ticker) ||
        (document.getElementById('ticker-input') && document.getElementById('ticker-input').value) ||
        ''
      ).toUpperCase().replace(/^\$/, '').trim();
      const date = params.get('date') || window.AppState?.activeDate || '';
      if (!ticker) {
        if (window.AppUtils && window.AppUtils.showToast) {
          window.AppUtils.showToast('Please specify a ticker to sync alerts', 'error');
        }
        return;
      }
      if (window.AppUtils && window.AppUtils.showToast) {
        window.AppUtils.showToast(`⏳ Registering Tastytrade cloud quote alerts for ${ticker}...`, 'info');
      }
      const resp = await fetch('/api/tastytrade-alerts/sync-ticker', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker: ticker, date: date || null })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        const count = data.tt_alerts_count || data.indexed_count || 0;
        if (window.AppUtils && window.AppUtils.showToast) {
          window.AppUtils.showToast(`📲 Registered ${count} Tastytrade cloud quote alert(s) for ${ticker}! (Mobile push active 24/7)`, 'success');
        }
      } else {
        const msg = data.detail || data.error || data.message || 'Failed to sync Tastytrade alerts';
        if (window.AppUtils && window.AppUtils.showToast) {
          window.AppUtils.showToast(`Tastytrade Alert: ${msg}`, 'warning');
        }
      }
    } catch (e) {
      console.error('Error syncing TT alerts:', e);
      if (window.AppUtils && window.AppUtils.showToast) {
        window.AppUtils.showToast(`Error syncing TT alerts: ${e.message}`, 'error');
      }
    }
  },

  async handleAddWatchAction(queryString) {
    try {
      const params = new URLSearchParams(queryString);
      const ticker = (params.get('ticker') || params.get('symbol') || '').toUpperCase().replace(/^\$/, '');
      const entry = parseFloat(params.get('entry') || params.get('entry_zone_high') || 0);
      const stop = parseFloat(params.get('stop') || params.get('tactical_stop') || 0);
      const target = parseFloat(params.get('target') || params.get('target_1') || 0);
      if (!ticker) return;

      if (window.AppUtils && window.AppUtils.showToast) {
        window.AppUtils.showToast(`⏳ Adding ${ticker} to Active Watchlist Radar...`, 'info');
      }
      const resp = await fetch('/api/watch-targets/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker: ticker,
          entry_zone_high: entry || null,
          entry_zone_low: entry ? parseFloat((entry * 0.99).toFixed(2)) : null,
          tactical_stop: stop || null,
          target_1: target || null,
          verdict: 'PASS',
          side: 'LONG',
          create_cloud_alert: true
        })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        const extra = data.cloud_alert_created ? ' + Cloud Push Alert' : '';
        if (window.AppUtils && window.AppUtils.showToast) {
          window.AppUtils.showToast(`🎯 Added ${ticker} to Active Watchlist Radar${extra}!`, 'success');
        }
        if (window.AppWatchlist && typeof window.AppWatchlist.refreshWatchTargets === 'function') {
          window.AppWatchlist.refreshWatchTargets();
        }
      } else {
        const errMsg = data.detail || data.error || 'Failed to add watch target';
        if (window.AppUtils && window.AppUtils.showToast) {
          window.AppUtils.showToast(`Watch Target Note: ${errMsg}`, 'warning');
        }
      }
    } catch (e) {
      console.error('Error adding watch target:', e);
      if (window.AppUtils && window.AppUtils.showToast) {
        window.AppUtils.showToast(`Error adding watch target: ${e.message}`, 'error');
      }
    }
  },

  openChatWithPrompt(prompt, sym = null) {
    this.ensureChatOpen();
    if (sym) {
      this.setRevChatFocus(sym);
      const tickerInput = document.getElementById('ticker-input');
      if (tickerInput && !tickerInput.value) tickerInput.value = sym;
    }

    // Ensure sidebar chat width is at least 420px
    try {
      const curW = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--chat-w')) || 0;
      if (curW < 320) {
        this.applyChatWidth(460);
      }
    } catch (e) {}

    // Scroll sidebar into view if on narrow/mobile view
    const sidebar = document.querySelector('.sidebar-rev-chat');
    if (sidebar && typeof sidebar.scrollIntoView === 'function') {
      sidebar.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    // Trigger message stream in Rev Chat
    this.sendRevChatMsg(prompt);
  },

  sendCopilotMsg(prompt = null) {
    return this.sendRevChatMsg(prompt);
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

  // Portfolio-desk entry point: drops any single-ticker focus so the copilot answers about the
  // whole carried book (every holding, contract, and recent fill) instead of one symbol.
  // Starts a clean, isolated chat session and clears prior conversation history.
  askPortfolioCopilot(prompt) {
    if (window.AppState.isRevChatStreaming) {
      this.stopRevChatStream();
    }
    this._forcePortfolioChat = true;
    this._portfolioSessionId = `sess_PORTFOLIO_${Date.now()}`;
    this._revSessionId = this._portfolioSessionId;
    window.AppState.revChatHistory = [];
    window.AppState.revChatCrossTurns = [];
    this.clearRevChatFocus();
    this.enterPortfolioMode();
    this.renderRevChat();
    const panel = document.getElementById('sidebar-prior-chats-panel');
    if (panel) panel.style.display = 'none';
    this.openChatWithPrompt(prompt);
  },

  // Dedicated PORTFOLIO COPILOT thread: opened from the header button or the Portfolio desk.
  // Keeps the chat in portfolio mode for every subsequent message until the user hits New/Clear,
  // so a leftover ticker focus can never recapture the conversation.
  openPortfolioCopilot() {
    if (window.AppState.isRevChatStreaming) {
      this.stopRevChatStream();
    }
    this._forcePortfolioChat = true;
    this._portfolioSessionId = `sess_PORTFOLIO_${Date.now()}`;
    this._revSessionId = this._portfolioSessionId;
    window.AppState.revChatHistory = [];
    window.AppState.revChatCrossTurns = [];
    this.clearRevChatFocus();
    this.enterPortfolioMode();
    this.renderRevChat();
    const panel = document.getElementById('sidebar-prior-chats-panel');
    if (panel) panel.style.display = 'none';
  },

  enterPortfolioMode() {
    this._portfolioMode = true;
    this._revSessionId = this._portfolioSessionId || `sess_PORTFOLIO_${Date.now()}`;
    this._portfolioSessionId = this._revSessionId;
    window.AppState.revChatFocusTicker = '';
    window.AppState.activeChatTicker = '';
    window.AppState.revChatFocusCleared = true;
    if (typeof this.updateSidebarFocusBadge === 'function') this.updateSidebarFocusBadge();
    if (typeof this._syncSidebarFocusInput === 'function') this._syncSidebarFocusInput();
    const title = document.getElementById('rev-chat-mode-badge');
    if (title) {
      title.innerText = '💼 PORTFOLIO COPILOT';
      title.style.display = 'inline-flex';
    }
  },

  exitPortfolioMode() {
    this._portfolioMode = false;
    this._portfolioSessionId = null;
    const title = document.getElementById('rev-chat-mode-badge');
    if (title) title.style.display = 'none';
  },

  updatePortfolioModeBadge() {
    const title = document.getElementById('rev-chat-mode-badge');
    if (title) {
      if (this._portfolioMode) {
        title.innerText = '💼 Portfolio';
        title.style.display = 'inline-flex';
      } else {
        title.style.display = 'none';
      }
    }
    const header = document.getElementById('rev-chat-header');
    if (header) header.classList.toggle('portfolio-mode', !!this._portfolioMode);
  },

  askModalCopilot(prompt, ticker = null, date = null) {
    const input = document.getElementById('modal-chat-input');
    if (prompt && prompt.endsWith('$')) {
      if (input) {
        input.value = prompt;
        input.focus();
        input.setSelectionRange(prompt.length, prompt.length);
      }
    } else {
      if (input) input.value = '';
      this.sendModalCopilotMsg(prompt, ticker, date);
    }
  },

  handleRevChatBtnClick() {
    const btn = document.getElementById('btn-rev-chat-send');
    // NOTE: read textContent (raw label), NOT innerText. The send button is CSS-uppercased, and
    // Chromium's innerText returns the *rendered* text ("⏹ STOP"), which broke the Stop toggle.
    const label = String((btn && (btn.textContent || btn.innerText)) || '').toUpperCase();
    const isStop = label.includes('STOP');

    if (window.AppState.isRevChatStreaming && isStop) {
      this.stopRevChatStream();
    } else if (!window.AppState.isRevChatStreaming) {
      this.sendRevChatMsg();
    } else {
      this.stopRevChatStream();
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
    this._flushRcStreamRender(null, '', '', false, true);
    const sendBtn = document.getElementById('btn-rev-chat-send');
    if (sendBtn) {
      sendBtn.className = 'btn';
      sendBtn.innerText = '💬 Send';
      sendBtn.disabled = false;
    }
  },

  // ---- Live tool-call timeline ------------------------------------------------------------
  // The model's data-collection pass used to sit behind one static "Executing live tool(s)..."
  // line with no progress. Now each tool gets a row with a live spinner, then a resolved
  // duration when it lands, so long research passes visibly progress.
  _rcToolLabel(name) {
    const n = String(name || '').replace(/^get_realtime_quote$/, 'get_realtime_quote');
    const map = {
      fetch_earnings_calendar: '📅 Earnings calendar',
      search_web: '🔎 Web search',
      fetch_finnhub_news: '📰 Finnhub news',
      fetch_alpaca_news: '📰 Alpaca news',
      get_realtime_quote: '💹 Live quote',
      fetch_options_chain: '🧮 Options chain',
      scrape_tradingview_options_finder: '📈 TV strategy finder',
      fetch_historical_zone_and_regime_analytics: '🕰️ Zone & regime analytics',
      fetch_tastytrade_volatility_and_options: '🌊 Tastytrade vol metrics',
      run_quantitative_plugin: '🧪 Quantitative plugin',
      execute_python_code: '🐍 Python sandbox',
      fetch_prior_research: '📚 Prior research',
      detect_candlestick_patterns: '🕯️ Candlestick patterns',
    };
    return map[n] || `⚙️ ${n || 'tool'}`;
  },

  _rcToolStart(bubble, tools) {
    if (!bubble || !tools.length) return;
    let box = bubble.querySelector('.rc-tool-timeline');
    if (!box) {
      box = document.createElement('div');
      box.className = 'rc-tool-timeline';
      const head = bubble.querySelector('.rc-msg-head');
      if (head && head.nextSibling) bubble.insertBefore(box, head.nextSibling);
      else bubble.appendChild(box);
    }
    tools.forEach((name) => {
      if (box.querySelector(`[data-tool="${CSS.escape(String(name))}"]`)) return;
      const row = document.createElement('div');
      row.className = 'rc-tool-row is-running';
      row.dataset.tool = String(name);
      row.innerHTML = `<span class="rc-tool-spinner"></span><span class="rc-tool-name">${this._rcToolLabel(name)}</span><span class="rc-tool-time">running…</span>`;
      box.appendChild(row);
    });
    box.style.display = 'block';
  },

  _rcToolDone(bubble, tools, totalMs) {
    if (!bubble) return;
    const box = bubble.querySelector('.rc-tool-timeline');
    if (!box) return;
    tools.forEach((t) => {
      const name = typeof t === 'string' ? t : (t && t.name);
      if (!name) return;
      const row = box.querySelector(`[data-tool="${CSS.escape(String(name))}"]`);
      if (!row) return;
      const ok = typeof t === 'object' && t ? t.ok !== false : true;
      const ms = typeof t === 'object' && t && t.ms ? t.ms : 0;
      row.classList.remove('is-running');
      row.classList.add(ok ? 'is-done' : 'is-error');
      row.innerHTML = `<span class="rc-tool-spinner">${ok ? '✓' : '✕'}</span><span class="rc-tool-name">${this._rcToolLabel(name)}</span><span class="rc-tool-time">${ms >= 1000 ? (ms / 1000).toFixed(1) + 's' : ms + 'ms'}</span>`;
    });
    const foot = document.createElement('div');
    foot.className = 'rc-tool-foot';
    foot.textContent = `data pass complete · ${totalMs >= 1000 ? (totalMs / 1000).toFixed(1) + 's' : totalMs + 'ms'}`;
    const existing = box.querySelector('.rc-tool-foot');
    if (existing) existing.remove();
    box.appendChild(foot);
  },

  // ---- Streaming renderer: throttled markdown + live telemetry -----------------------------  // Rendering full markdown on every token is what made long answers feel static and janky.
  // We coalesce renders to ~12fps, keep the stop control mounted, show a live caret plus
  // elapsed/token telemetry in the bubble head, and only auto-scroll when the user is at the bottom.
  _rcStreamState: null,

  _scheduleRcStreamRender(bubble, fullText, title) {
    if (!bubble) return;
    const st = this._rcStreamState || {};
    st.bubble = bubble;
    st.fullText = fullText;
    st.title = title;
    st.dirty = true;
    if (!st.t0) st.t0 = Date.now();
    this._rcStreamState = st;
    const thread = document.getElementById('rev-chat-thread');
    const nearBottom = thread
      ? (thread.scrollHeight - thread.scrollTop - thread.clientHeight) < 80
      : true;
    this._rcStreamState.autoScroll = nearBottom;

    if (this._rcStreamRaf) return;
    this._rcStreamRaf = setTimeout(() => {
      this._rcStreamRaf = null;
      const st = this._rcStreamState;
      if (!st || !st.bubble) return;
      this._paintRcStream(st);
    }, 80);
  },

  _paintRcStream(st) {
    const text = typeof st.fullText === 'string' ? st.fullText : '';
    const elapsed = st.t0 ? `${((Date.now() - st.t0) / 1000).toFixed(1)}s` : '';
    const tokens = text.length;
    const meta = [elapsed, `${tokens} chars`].filter(Boolean).join(' · ');

    let head = st.bubble.querySelector('.rc-msg-head');
    let metaEl = st.bubble.querySelector('.rc-msg-meta');
    let whoEl = st.bubble.querySelector('.rc-msg-who');
    let body = st.bubble.querySelector('.rc-msg-body');

    if (!head || !body) {
      const timeline = st.bubble.querySelector('.rc-tool-timeline');
      st.bubble.innerHTML =
        `<div class="rc-msg-head"><span class="rc-msg-who">${st.title}</span>` +
        `<span class="rc-msg-meta">${meta}</span>` +
        `<button class="btn danger rc-stop-btn" onclick="AppChat.stopRevChatStream()">⏹ Stop</button></div>`;
      if (timeline) st.bubble.appendChild(timeline);
      st.bubble.insertAdjacentHTML('beforeend',
        `<div class="rc-msg-body rc-streaming">${window.AppUtils.renderMarkdown(text)}<span class="rc-caret"></span></div>`);
    } else {
      if (whoEl && st.title && whoEl.textContent !== st.title) whoEl.textContent = st.title;
      if (metaEl) metaEl.textContent = meta;
      body.innerHTML = `${window.AppUtils.renderMarkdown(text)}<span class="rc-caret"></span>`;
    }

    const thread = document.getElementById('rev-chat-thread');
    if (thread && st.autoScroll) {
      thread.scrollTop = thread.scrollHeight;
    }
    st.dirty = false;
  },

  _flushRcStreamRender(bubble, fullText, title, isError = false, cancelled = false) {
    if (this._rcStreamRaf) {
      clearTimeout(this._rcStreamRaf);
      this._rcStreamRaf = null;
    }
    const st = this._rcStreamState;
    const target = bubble || (st && st.bubble);
    const text = fullText || (st && st.fullText) || '';
    const headTitle = title || (st && st.title) || '';
    const err = isError || cancelled;
    if (target && text) {
      const timeline = target.querySelector('.rc-tool-timeline');
      const meta = st && st.t0 ? `${((Date.now() - st.t0) / 1000).toFixed(1)}s · ${text.length} chars` : `${text.length} chars`;
      target.innerHTML =
        `<div class="rc-msg-head${err ? ' rc-msg-head-err' : ''}"><span class="rc-msg-who">${err ? '⏹ ' : ''}${headTitle}</span>` +
        `<span class="rc-msg-meta">${meta}</span></div>`;
      if (timeline) target.appendChild(timeline);
      target.insertAdjacentHTML('beforeend', `<div class="rc-msg-body">${window.AppUtils.renderMarkdown(text)}</div>`);
    }
    this._rcStreamState = null;
    const thread = document.getElementById('rev-chat-thread');
    if (thread) thread.scrollTop = thread.scrollHeight;
  },

  async sendRevChatMsg(customPrompt = null) {
    if (this._isSendingRevMsg) return;
    this.ensureChatOpen();
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
      // Continuity: when the user switches between ticker and portfolio threads, top the context up
      // from the last few turns of the other thread so follow-ups ("and its covered call?") keep
      // their subject. These turns are context only - they are not rendered into this thread.
      let cleanHistory = (window.AppState.revChatHistory || [])
        .filter(m => (m.role === 'user' || m.role === 'assistant') && m.content && !m.pending)
        .map(m => ({ role: m.role, content: typeof m.content === 'string' ? m.content : String(m.content) }));

      if (cleanHistory.length < 2 && Array.isArray(window.AppState.revChatCrossTurns) && window.AppState.revChatCrossTurns.length) {
        const bridge = window.AppState.revChatCrossTurns.slice(-4);
        cleanHistory = bridge.concat(cleanHistory);
      }

      window.AppState.revChatHistory = window.AppState.revChatHistory.filter(m => !m.pending);
      window.AppState.revChatHistory.push({ role: 'user', content: question });

      // Remember recent turns across threads (capped) for cross-mode continuity
      if (!Array.isArray(window.AppState.revChatCrossTurns)) window.AppState.revChatCrossTurns = [];
      window.AppState.revChatCrossTurns.push({ role: 'user', content: question });
      if (window.AppState.revChatCrossTurns.length > 8) window.AppState.revChatCrossTurns.shift();

      let boardContext = this._sidebarAttachedBoard ? this._sidebarAttachedBoard.context : null;
      let boardName = this._sidebarAttachedBoard ? this._sidebarAttachedBoard.name : null;

      // Follow the active desk: every message carries what that page is actually showing (Today's
      // setups, watchlist targets, live Schwab holdings, open intraday positions...) so the chat is
      // scoped to the page the user is on instead of guessing at a generic market answer.
      if (!boardContext) {
        const deskCtx = this.buildDeskBoardContext();
        if (deskCtx) {
          boardContext = deskCtx.context;
          boardName = deskCtx.name;
        }
      }

      if (thread) {
        const userBubble = document.createElement('div');
        userBubble.className = 'rc-msg rc-msg-user';
        let bubbleContent = `<div class="rc-msg-body" style="white-space:pre-wrap;">${this.escapeHtml(question)}</div>`;
        if (boardName) {
          bubbleContent = `<div class="rc-msg-head" style="opacity:0.9;"><span class="rc-msg-who">📌 ${boardName}</span></div>` + bubbleContent;
        }
        if (imageData) {
          bubbleContent += `<div style="margin-top:6px;"><img src="${imageData}" style="max-height:85px; max-width:100%; border-radius:6px; border:1px solid rgba(37,99,235,0.35);" /></div>`;
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

      // Resolve the ticker for the main sidebar chat.
      //
      // The old chain fell back through activeChatTicker / currentReportData / #ticker-input, so
      // a message you sent after clearing the focus re-established it from whatever report
      // happened to be open. An explicit clear now sticks until focus is set again deliberately.
      let activeTicker = this._focusTicker();
      if (!activeTicker && !window.AppState.revChatFocusCleared) {
        activeTicker = (
          (window.AppState.activeChatTicker) ||
          (window.AppState.currentReportData ? window.AppState.currentReportData.ticker : '') ||
          (document.getElementById('ticker-input') ? document.getElementById('ticker-input').value : '') ||
          ''
        ).trim().toUpperCase();
        if (['GENERAL', 'AUTO', 'NONE', 'ALL', 'STOCK', 'MARKET'].includes(activeTicker)) activeTicker = '';
      }

      // Portfolio Chat detection: this chat is a SEPARATE persona from the generic REV CHAT desk.
      // It fires when the question is about the book itself, or when the user is on the Schwab
      // Portfolio desk and is not asking about a specific stock. It must never be captured by a
      // leftover sidebar ticker focus.
      const currentDeskName = (window.AppState && window.AppState.currentDesk) || '';
      const pfQuestion = /(portfolio|holdings?|positions?|my\s+(trades?|account|book|legs?|spreads?|leaps|shares|options?|contracts?|schwab|covered\s+calls?)|covered\s+calls?|allocation|concentrat\w*|net\s+liquidation|account\s+balance|buying\s+power|net\s+worth|dry\s+powder|cash\s+balance|what\s+do\s+i\s+own|what\s+am\s+i\s+holding|do\s+i\s+own|how\s+much\s+\w+\s+do\s+i\s+(own|have)|ndxp|brokerage)/i;
      // Unmistakable book-ownership phrasing switches the chat to the dedicated portfolio persona
      // from any desk; the broad regex only does so while the Portfolio desk is the active desk.
      const pfStrong = /(my\s+(portfolio|book|account|holdings|positions|trades|schwab|brokerage|shares|options?|contracts?|legs?|spreads?|leaps|covered\s+calls?|covered\s+call)|portfolio\s+(summary|update|status|review|health|thoughts|overview|tactics)|thoughts\s+on\s+the\s+portfolio|summariz\w*\s+.{0,25}portfolio|net\s+liquidation|what\s+do\s+i\s+own|how\s+is\s+my\s+book|over-?concentrat\w*|dry\s+powder)/i;
      const isPortfolioChat = this._portfolioMode || this._forcePortfolioChat || pfStrong.test(question) || (
        currentDeskName === 'portfolio' && (pfQuestion.test(question) || !activeTicker)
      );
      this._forcePortfolioChat = false;
      if (isPortfolioChat) this._portfolioMode = true;

      if (isPortfolioChat) {
        // Drop any inherited ticker framing so the dedicated PORTFOLIO COPILOT persona owns this turn.
        activeTicker = '';
        window.AppState.revChatFocusTicker = '';
        window.AppState.activeChatTicker = '';
        window.AppState.revChatFocusCleared = true;
        if (!this._portfolioSessionId) this._portfolioSessionId = `sess_PORTFOLIO_${Date.now()}`;
        this._revSessionId = this._portfolioSessionId;
      } else if (activeTicker) {
        this.setRevChatFocus(activeTicker);
        if (!this._revSessionId) {
          this._revSessionId = `sess_${activeTicker}_${Date.now()}`;
        }
      }

      const chatHeaderTitle = isPortfolioChat
        ? '💼 PORTFOLIO COPILOT'
        : (activeTicker ? `⚡ REV CHAT ($${activeTicker})` : '⚡ REV CHAT');

      const assistantBubble = document.createElement('div');
      assistantBubble.className = 'rc-msg rc-msg-assistant';
      this._flushRcStreamRender(assistantBubble, 'Connecting to AI Copilot...', chatHeaderTitle, false);
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
                  this._flushRcStreamRender(assistantBubble, 'Connecting to AI Copilot...', chatHeaderTitle, false);
                } else if (data.status === 'progress') {
                  this._flushRcStreamRender(assistantBubble, `> ⚙️ ${data.step || 'Compiling data...'}\n\n_Formulating quantitative intelligence..._`, chatHeaderTitle, false);
                } else if (data.status === 'provider') {
                  const provTag = data.provider === 'local' ? '⚡ GPU' : `🌐 ${data.model || 'Cloud'}`;
                  chatHeaderTitle = `${chatHeaderTitle} · ${provTag}`;
                  if (this._rcStreamState) this._rcStreamState.title = chatHeaderTitle;
                  const who = assistantBubble.querySelector('.rc-msg-who');
                  if (who) who.textContent = chatHeaderTitle;
                } else if (data.status === 'slot_notice' || data.notice) {
                  const noticeMsg = data.notice || '';
                  this._flushRcStreamRender(assistantBubble, `> ${noticeMsg}\n\n_Streaming response..._`, chatHeaderTitle, false);
                } else if (data.status === 'thinking' || data.thought) {
                  if (!fullAnswer) {
                    if (!this._rcThinkingBuf) this._rcThinkingBuf = '';
                    this._rcThinkingBuf += (data.thought || '');
                    const snippet = this._rcThinkingBuf.replace(/\s+/g, ' ').slice(-140);
                    this._flushRcStreamRender(assistantBubble, `> 💭 *Reasoning:* ...${snippet}\n\n_Formulating institutional response..._`, chatHeaderTitle, false);
                  }
                } else if (data.status === 'tool_start') {
                  this._rcToolStart(assistantBubble, data.tools || []);
                } else if (data.status === 'tool_done') {
                  this._rcToolDone(assistantBubble, data.tools || [], data.total_ms || 0);
                } else if (data.token) {
                  fullAnswer += data.token;
                  if (!this._rcStreamT0) this._rcStreamT0 = Date.now();
                  this._scheduleRcStreamRender(assistantBubble, fullAnswer, chatHeaderTitle);
                } else if (data.error) {
                  fullAnswer += `\n\n❌ **Error:** ${data.error}`;
                  this._flushRcStreamRender(assistantBubble, fullAnswer, chatHeaderTitle, true);
                }
              } catch (parseErr) {}
            }
          }
        }

        if (fullAnswer.trim()) {
          this._flushRcStreamRender(assistantBubble, fullAnswer, chatHeaderTitle, false);
          window.AppState.revChatHistory.push({ role: 'assistant', content: fullAnswer });
          window.AppState.revChatCrossTurns.push({ role: 'assistant', content: fullAnswer });
          if (window.AppState.revChatCrossTurns.length > 8) window.AppState.revChatCrossTurns.shift();
        } else {
          // Stream ended with no content - surface the failure visibly
          const emptyMsg = '⚠️ **No response received from LLM.** The server may have closed the connection early or timed out. If using local inference, verify port 8000 is running; if using OpenRouter, verify your API key and connection.';
          this._flushRcStreamRender(assistantBubble, emptyMsg, chatHeaderTitle, true);
          window.AppState.revChatHistory.push({ role: 'assistant', content: emptyMsg });
        }

      } catch (e) {
        const isAbort = e.name === 'AbortError' || (window.AppState.revChatAbortController && window.AppState.revChatAbortController.signal.aborted);
        if (isAbort) {
          const savedText = fullAnswer.trim() ? `${fullAnswer}\n\n*[⏹ Stream stopped by user]*` : '*[⏹ Stream cancelled]*';
          window.AppState.revChatHistory.push({ role: 'assistant', content: savedText });
          this._flushRcStreamRender(assistantBubble, savedText, chatHeaderTitle, !fullAnswer.trim());
        } else {
          // Connection interrupted: detached server generator continues in background.
          // Show recovery status and auto-poll SQLite to seamlessly recover the completed answer!
          const currentSid = this._revSessionId;
          const pendingNotice = (fullAnswer.trim() ? `${fullAnswer}\n\n` : '') +
            `> ⏳ *Connection interrupted (${e.message || 'network blip'}). The server continues generation in background — recovering answer...* <button class="btn" style="padding:2px 8px; font-size:10px; margin-left:6px;" onclick="AppChat.recoverActiveSession()">🔄 Check Now</button>`;
          this._flushRcStreamRender(assistantBubble, pendingNotice, chatHeaderTitle, false);
          this._autoRecoverSession(currentSid, assistantBubble, chatHeaderTitle);
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
    }
  },

  _autoRecoverSession(sessionId, bubble, title, attempts = 0) {
    if (!sessionId || attempts >= 10) return;
    setTimeout(async () => {
      try {
        const res = await fetch(`/api/copilot/chats/history?session_id=${encodeURIComponent(sessionId)}`);
        if (!res.ok) return;
        const data = await res.json();
        const msgs = (data && data.messages) || [];
        const lastAsst = msgs.filter(m => m.role === 'assistant').pop();
        if (lastAsst && lastAsst.content && !lastAsst.content.startsWith('❌ Context compilation error')) {
          this._flushRcStreamRender(bubble, lastAsst.content, title, false);
          if (window.AppState.revChatHistory) {
            window.AppState.revChatHistory.push({ role: 'assistant', content: lastAsst.content });
          }
          return;
        }
        this._autoRecoverSession(sessionId, bubble, title, attempts + 1);
      } catch (err) {
        console.debug('Session recovery polling:', err);
      }
    }, 1200);
  },

  async recoverActiveSession() {
    const sid = this._revSessionId;
    if (!sid) return;
    try {
      const res = await fetch(`/api/copilot/chats/history?session_id=${encodeURIComponent(sid)}`);
      if (!res.ok) return;
      const data = await res.json();
      const msgs = (data && data.messages) || [];
      const lastAsst = msgs.filter(m => m.role === 'assistant').pop();
      if (lastAsst && lastAsst.content) {
        const thread = document.getElementById('rev-chat-thread');
        if (thread) {
          const lastMsg = thread.querySelector('.rc-msg-assistant:last-child');
          if (lastMsg) {
            this._flushRcStreamRender(lastMsg, lastAsst.content, '⚡ REV CHAT', false);
            if (window.AppState.revChatHistory) {
              window.AppState.revChatHistory.push({ role: 'assistant', content: lastAsst.content });
            }
          }
        }
      }
    } catch (e) {
      console.warn('Manual recovery failed:', e);
    }
  },

  // ---- Modal Dossier Copilot Chat ----
  clearModalChat() {
    const sym = (window.AppState.activeChatTicker || (window.AppState.currentReportData && window.AppState.currentReportData.ticker) || 'STOCK').toUpperCase().trim();
    const date = (window.AppState.currentReportData && window.AppState.currentReportData.date) || '';
    const key = `${sym}_${date}`;
    this.stopModalCopilotStream(sym);
    if (!window.AppState.modalChatHistories) window.AppState.modalChatHistories = {};
    window.AppState.modalChatHistories[key] = [];
    window.AppState.modalChatHistory = [];
    this._modalSessionId = `sess_${sym}_${date}_${Date.now()}`;
    this.renderModalCopilotChat();
  },

  async initModalChatForTicker(ticker, date) {
    if (!ticker) return;
    const sym = ticker.toUpperCase().trim();
    const key = `${sym}_${date || ''}`;
    if (!window.AppState.modalChatHistories) window.AppState.modalChatHistories = {};

    const stream = window.AppState.modalStreams && window.AppState.modalStreams[key];
    // Instant switch if history is already in memory or if active stream is in progress for this stock
    if ((window.AppState.modalChatHistories[key] && window.AppState.modalChatHistories[key].length > 0) || (stream && stream.isStreaming)) {
      window.AppState.modalChatHistory = window.AppState.modalChatHistories[key] || [];
      this.renderModalCopilotChat();
      this.initModalChatResize();
      return;
    }

    try {
      const res = await fetch(`/api/copilot/chats/history?ticker=${encodeURIComponent(sym)}&date=${encodeURIComponent(date || '')}`);
      const data = await res.json();
      const fetched = (data && data.messages && data.messages.length > 0)
        ? data.messages.map(m => ({ role: m.role, content: m.content, date: m.date, created_at: m.created_at }))
        : [];

      // Check if local messages or stream were added during the network round-trip
      const currentLocal = window.AppState.modalChatHistories[key] || [];
      const currentStream = window.AppState.modalStreams && window.AppState.modalStreams[key];
      if (currentLocal.length > 0 || (currentStream && currentStream.isStreaming)) {
        window.AppState.modalChatHistory = currentLocal;
      } else {
        window.AppState.modalChatHistory = fetched;
        window.AppState.modalChatHistories[key] = fetched;
      }
      this._modalSessionId = (data && data.messages && data.messages[0] && data.messages[0].session_id) || `sess_${sym}_${date}_${Date.now()}`;
    } catch (e) {
      console.debug('Could not load ticker chat history from DB:', e);
    }
    this.renderModalCopilotChat();
    this.initModalChatResize();
  },

  renderModalCopilotChat() {
    const sym = (window.AppState.activeChatTicker || (window.AppState.currentReportData && window.AppState.currentReportData.ticker) || 'STOCK').toUpperCase().trim();
    const date = (window.AppState.currentReportData && window.AppState.currentReportData.ticker === sym)
      ? (window.AppState.currentReportData.date || '')
      : '';
    const thread = document.getElementById('modal-chat-thread');
    if (!thread) return;

    const key = `${sym}_${date}`;
    if (!window.AppState.modalChatHistories) window.AppState.modalChatHistories = {};
    const history = window.AppState.modalChatHistories[key] || [];
    window.AppState.modalChatHistory = history;

    const todayStr = new Date().toISOString().slice(0, 10);
    const isPastDossier = Boolean(date && date < todayStr);
    const hasHistory = history && history.length > 0;

    let historyHtml = history.map(m => {
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

    const stream = window.AppState.modalStreams && window.AppState.modalStreams[key];
    if (!historyHtml && !stream) {
      historyHtml = `
        <div style="background:var(--bg-subtle); border:1px solid var(--border); border-radius:8px; padding:12px 14px;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
            <span style="font-weight:700; color:var(--blue); font-size:11px;">🤖 ACTIVE DOSSIER: ${sym} (${date || 'Active'})</span>
            <span class="pill cyan" style="font-size:9.5px; padding:1px 6px;">FRESH CONTEXT</span>
          </div>
          <div style="font-size:11.5px; color:var(--text-muted); line-height:1.45; margin-bottom:10px;">
            Two-Pass Protocol active: evaluates current state, checks binary risks & tactical levels, and asks in case of ambiguity before locking execution.
          </div>
          <div style="display:flex; flex-direction:column; gap:5px;">
            <button class="btn" style="background:linear-gradient(135deg, #0284c7, #2563eb); color:#fff; font-size:11px; padding:5px 10px; text-align:left; border-radius:6px; cursor:pointer;" onclick="AppChat.sendModalCopilotMsg('What is going on with ${sym} right now? Run Pass 1: assess current state, check live price vs entry zone, binary risk/earnings, and volatility. In case of ANY ambiguity, ask me to clarify with options before proceeding.')">
              ⚡ <strong>Pass 1</strong>: What is going on? (State & Ambiguity Check)
            </button>
            <button class="btn secondary" style="font-size:11px; padding:5px 10px; text-align:left; border-radius:6px; cursor:pointer;" onclick="AppChat.sendModalCopilotMsg('Explain the exact tactical execution plan for ${sym} (limit entry, stop, targets, R:R). If ambiguous, ask first.')">
              🎯 <strong>Pass 2</strong>: Tactical Execution Levels
            </button>
            <button class="btn secondary" style="font-size:11px; padding:5px 10px; text-align:left; border-radius:6px; cursor:pointer;" onclick="AppChat.startFreshModalSession()">
              🔄 <strong>Reset</strong>: Start Fresh Isolated Session
            </button>
          </div>
        </div>
      `;
    }

    // IF THIS TICKER HAS AN ACTIVE STREAM IN PROGRESS, EMBED ITS LIVE STREAM BUBBLE!
    if (stream && stream.isStreaming) {
      const streamContent = stream.fullAnswer
        ? window.AppUtils.renderMarkdown(stream.fullAnswer)
        : `<em>${stream.statusText || 'Connecting to local model stream...'}</em>`;
      const titleColor = stream.fullAnswer ? 'var(--emerald-light)' : 'var(--blue)';

      historyHtml += `
        <div id="modal-assistant-stream-bubble" style="align-self:flex-start; max-width:92%; background:var(--bg-subtle); border:1px solid var(--border); border-radius:10px; padding:10px 14px; font-size:12.5px; line-height:1.5; color:var(--text-main); box-shadow:var(--shadow-card);">
          <div style="font-size:10px; font-weight:700; color:${titleColor}; margin-bottom:6px; display:flex; justify-content:space-between; align-items:center;">
            <span>🤖 COPILOT ANALYSIS (${sym})</span>
            <button class="btn danger" onclick="AppChat.stopModalCopilotStream('${sym}')" style="padding:1px 6px; font-size:9.5px; font-weight:700; cursor:pointer;">⏹ Stop</button>
          </div>
          <div id="modal-stream-content">${streamContent}</div>
        </div>
      `;
    }

    thread.innerHTML = historyHtml;
    thread.scrollTop = thread.scrollHeight;
    this.updateModalSendButtonState();
  },

  updateModalSendButtonState() {
    const sym = (window.AppState.activeChatTicker || (window.AppState.currentReportData && window.AppState.currentReportData.ticker) || '').toUpperCase().trim();
    const date = (window.AppState.currentReportData && window.AppState.currentReportData.ticker === sym)
      ? (window.AppState.currentReportData.date || '')
      : '';
    const key = `${sym}_${date}`;
    const stream = window.AppState.modalStreams && window.AppState.modalStreams[key];
    const isStreamingThis = Boolean(stream && stream.isStreaming);

    const btn = document.getElementById('btn-modal-chat-send');
    if (btn) {
      if (isStreamingThis) {
        btn.className = 'btn danger';
        btn.innerText = '⏹ Stop';
        btn.disabled = false;
      } else {
        btn.className = 'btn';
        btn.innerText = '💬 Send';
        btn.disabled = false;
      }
    }
    window.AppState.isModalChatStreaming = isStreamingThis;
  },

  handleModalCopilotBtnClick() {
    const sym = (window.AppState.activeChatTicker || (window.AppState.currentReportData && window.AppState.currentReportData.ticker) || '').toUpperCase().trim();
    const date = (window.AppState.currentReportData && window.AppState.currentReportData.ticker === sym)
      ? (window.AppState.currentReportData.date || '')
      : '';
    const key = `${sym}_${date}`;
    const isStreamingThis = Boolean(window.AppState.modalStreams && window.AppState.modalStreams[key] && window.AppState.modalStreams[key].isStreaming);

    if (isStreamingThis) {
      this.stopModalCopilotStream(sym);
    } else {
      this.sendModalCopilotMsg();
    }
  },

  stopModalCopilotStream(targetTicker = null) {
    const sym = (targetTicker || window.AppState.activeChatTicker || (window.AppState.currentReportData && window.AppState.currentReportData.ticker) || '').toUpperCase().trim();
    if (!sym) return;
    const date = (window.AppState.currentReportData && window.AppState.currentReportData.ticker === sym)
      ? (window.AppState.currentReportData.date || '')
      : '';
    const key = `${sym}_${date}`;
    if (window.AppState.modalStreams && window.AppState.modalStreams[key]) {
      const stream = window.AppState.modalStreams[key];
      if (stream.abortController) {
        try { stream.abortController.abort(); } catch (e) {}
      }
      stream.isStreaming = false;
      delete window.AppState.modalStreams[key];
    }
    this.updateModalSendButtonState();
  },

  async sendModalCopilotMsg(customPrompt = null, explicitTicker = null, explicitDate = null) {
    let sym = (
      explicitTicker ||
      window.AppState.activeChatTicker ||
      (window.AppState.currentReportData ? window.AppState.currentReportData.ticker : '') ||
      ''
    ).toUpperCase().trim();
    if (!sym) {
      const titleEl = document.getElementById('modal-ticker-title');
      if (titleEl) {
        const m = titleEl.innerText.match(/^([A-Z0-9.\-]+)/);
        if (m) sym = m[1].toUpperCase().trim();
      }
    }
    if (!sym) sym = 'STOCK';

    let date = explicitDate || '';
    if (!date && window.AppState.currentReportData && window.AppState.currentReportData.ticker === sym) {
      date = window.AppState.currentReportData.date || '';
    }
    if (!date && window.AppState.activeChats) {
      const chat = window.AppState.activeChats.find(c => (c.ticker || '').toUpperCase().trim() === sym);
      if (chat) date = chat.date || '';
    }
    if (!date) {
      const titleEl = document.getElementById('modal-ticker-title');
      if (titleEl) {
        const m = titleEl.innerText.match(/\((\d{4}-\d{2}-\d{2})\)/);
        if (m) date = m[1];
      }
    }
    const key = `${sym}_${date}`;

    // Ensure active ticker matches so chat UI displays this message & stream tokens
    this.setRevChatFocus(sym);
    const tickerInput = document.getElementById('ticker-input');
    if (tickerInput) tickerInput.value = sym;

    // Check if THIS specific ticker is currently streaming
    if (window.AppState.modalStreams && window.AppState.modalStreams[key] && window.AppState.modalStreams[key].isStreaming) {
      if (customPrompt) {
        this.stopModalCopilotStream(sym);
        await new Promise(r => setTimeout(r, 60));
      } else {
        return; // Already streaming on this ticker
      }
    }

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

      if (input) input.value = '';

      // Auto-extract board context from the active tab if not manually pinned
      let boardContext = this._modalAttachedBoard ? this._modalAttachedBoard.context : null;
      let boardName = this._modalAttachedBoard ? this._modalAttachedBoard.name : null;
      if (!boardContext) {
        const autoBoard = this.getActiveBoardContext('modal');
        if (autoBoard) {
          boardContext = autoBoard.context;
          boardName = autoBoard.name;
        }
      }

      let userDisplayHtml = this.escapeHtml(question);
      if (boardName) {
        userDisplayHtml = `<span style="font-size:10px; opacity:0.85; font-weight:700; display:block; margin-bottom:3px;">📌 ${boardName}</span>` + userDisplayHtml;
      }
      if (imageData) {
        userDisplayHtml += `<div style="margin-top:6px;"><img src="${imageData}" style="max-height:85px; max-width:100%; border-radius:6px; border:1px solid rgba(255,255,255,0.35);" /></div>`;
      }

      if (!window.AppState.modalChatHistories) window.AppState.modalChatHistories = {};
      if (!window.AppState.modalChatHistories[key]) window.AppState.modalChatHistories[key] = [];
      const targetHistory = window.AppState.modalChatHistories[key];
      if (!this._modalSessionId || !this._modalSessionId.includes(`_${sym}_`)) {
        this._modalSessionId = `sess_${sym}_${date || 'live'}_${Date.now()}`;
      }

      // Clean history for payload (only valid string content and roles)
      const cleanHistory = targetHistory
        .filter(m => (m.role === 'user' || m.role === 'assistant') && m.content)
        .map(m => ({ role: m.role, content: typeof m.content === 'string' ? m.content : String(m.content) }));

      targetHistory.push({ role: 'user', content: question, displayHtml: userDisplayHtml });
      window.AppState.modalChatHistories[key] = targetHistory;
      window.AppState.modalChatHistory = targetHistory;

      // Reset image attachment after staging to message
      this._modalAttachedImage = null;
      this.updateAttachmentUI('modal');

      // Create isolated stream tracking for THIS ticker
      const abortController = new AbortController();
      const streamObj = {
        ticker: sym,
        date: date,
        abortController: abortController,
        isStreaming: true,
        fullAnswer: '',
        statusText: `Connecting to AI Copilot...`,
        sessionId: this._modalSessionId
      };
      if (!window.AppState.modalStreams) window.AppState.modalStreams = {};
      window.AppState.modalStreams[key] = streamObj;

      if (window.AppState.activeChatTicker === sym) {
        this.renderModalCopilotChat();
      }
      this.updateModalSendButtonState();

      try {
        const response = await fetch('/api/copilot/chat/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: abortController.signal,
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
                  streamObj.statusText = `Analyzing real-time market data for $${sym}...`;
                  if (window.AppState.activeChatTicker === sym) {
                    const contentEl = document.getElementById('modal-stream-content');
                    if (contentEl && !streamObj.fullAnswer) {
                      contentEl.innerHTML = `<em>${streamObj.statusText}</em>`;
                    }
                  }
                } else if (data.status === 'progress') {
                  streamObj.statusText = data.step || `Compiling quantitative context for $${sym}...`;
                  if (window.AppState.activeChatTicker === sym) {
                    const contentEl = document.getElementById('modal-stream-content');
                    if (contentEl && !streamObj.fullAnswer) {
                      contentEl.innerHTML = `<em>⚙️ ${this.escapeHtml(streamObj.statusText)}</em>`;
                    }
                  }
                } else if (data.status === 'provider') {
                  const provTag = data.provider === 'local' ? '⚡ GPU' : `🌐 ${data.model || 'Cloud'}`;
                  const titleEl = document.getElementById('modal-copilot-title');
                  if (titleEl) {
                    titleEl.innerText = `🤖 Copilot Intelligence · ${provTag}`;
                  }
                } else if (data.status === 'slot_notice' || data.notice) {
                  streamObj.statusText = data.notice || '';
                  if (window.AppState.activeChatTicker === sym) {
                    const contentEl = document.getElementById('modal-stream-content');
                    if (contentEl && !streamObj.fullAnswer) {
                      contentEl.innerHTML = `<div style="font-size:11px; color:var(--amber-light); margin-bottom:6px; background:rgba(245,158,11,0.08); border:1px solid rgba(245,158,11,0.25); border-radius:6px; padding:6px 8px;">${this.escapeHtml(streamObj.statusText)}</div><em>Streaming response...</em>`;
                    }
                  }
                } else if (data.status === 'thinking' || data.thought) {
                  if (!streamObj.fullAnswer) {
                    if (!streamObj.thinkingBuf) streamObj.thinkingBuf = '';
                    streamObj.thinkingBuf += (data.thought || '');
                    const snippet = streamObj.thinkingBuf.replace(/\s+/g, ' ').slice(-140);
                    streamObj.statusText = `💭 Reasoning: ...${snippet}`;
                    if (window.AppState.activeChatTicker === sym) {
                      const contentEl = document.getElementById('modal-stream-content');
                      if (contentEl) {
                        contentEl.innerHTML = `<div style="font-size:11px; color:var(--text-muted); font-style:italic; margin-bottom:6px; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); border-radius:6px; padding:6px 8px;">${this.escapeHtml(streamObj.statusText)}</div><em>Formulating institutional response...</em>`;
                      }
                    }
                  }
                } else if (data.token) {
                  streamObj.fullAnswer += data.token;
                  if (window.AppState.activeChatTicker === sym) {
                    const contentEl = document.getElementById('modal-stream-content');
                    const thread = document.getElementById('modal-chat-thread');
                    if (contentEl) {
                      contentEl.innerHTML = window.AppUtils.renderMarkdown(streamObj.fullAnswer);
                      if (thread) thread.scrollTop = thread.scrollHeight;
                    } else {
                      this.renderModalCopilotChat();
                    }
                  }
                } else if (data.error) {
                  streamObj.fullAnswer += `\n\n❌ **Error:** ${data.error}`;
                  if (window.AppState.activeChatTicker === sym) {
                    const contentEl = document.getElementById('modal-stream-content');
                    if (contentEl) {
                      contentEl.innerHTML = window.AppUtils.renderMarkdown(streamObj.fullAnswer);
                    }
                  }
                }
              } catch (parseErr) {}
            }
          }
        }

        if (streamObj.fullAnswer.trim()) {
          targetHistory.push({ role: 'assistant', content: streamObj.fullAnswer });
          window.AppState.modalChatHistories[key] = targetHistory;
        }
      } catch (e) {
        const isAbort = e.name === 'AbortError' || abortController.signal.aborted;
        if (streamObj.fullAnswer.trim()) {
          const savedText = isAbort ? `${streamObj.fullAnswer}\n\n*[⏹ Stream stopped by user]*` : `${streamObj.fullAnswer}\n\n*[⚠️ Connection interrupted: ${e.message}]*`;
          targetHistory.push({ role: 'assistant', content: savedText });
        } else {
          const errText = isAbort ? '*[⏹ Stream cancelled]*' : `❌ Error: ${e.message}`;
          targetHistory.push({ role: 'assistant', content: errText });
        }
        window.AppState.modalChatHistories[key] = targetHistory;
      }
    } catch (topErr) {
      console.error("sendModalCopilotMsg error:", topErr);
    } finally {
      if (window.AppState.modalStreams) {
        delete window.AppState.modalStreams[key];
      }
      if (window.AppState.activeChatTicker === sym) {
        this.renderModalCopilotChat();
      }
      this.updateModalSendButtonState();
    }
  }
};

document.addEventListener('DOMContentLoaded', () => { if (window.AppChat) window.AppChat.restoreRevChat(); });
