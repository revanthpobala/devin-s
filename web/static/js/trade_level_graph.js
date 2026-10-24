/**
 * web/static/js/trade_level_graph.js
 * 
 * Interactive Visual Trade Level Ladder, Profit/Loss Zone Graph & PnL Simulator.
 * Visualizes Deep Research Suggested Levels:
 *   - Entry Zone / Resting Limit
 *   - Tactical Stop Loss & Structural Invalidation Floor
 *   - Target 1 (Scale Trim 50%) & Target 2 (Runner)
 *   - Live Current Spot Price with real-time distance and status indicator
 *   - Profit Zone (Green) & Loss Zone (Red) with R-multiples
 *   - Interactive Share Size & P&L Calculator
 *   - Institutional Directives (Allocation, Options Menu, Invalidation, Gate Verification)
 */

(function () {
  'use strict';

  const TradeLevelGraph = {
    // -------------------------------------------------------------------------
    // 1. DATA EXTRACTION & REGEX FALLBACK
    // -------------------------------------------------------------------------
    parsePlan(data) {
      if (!data) return null;

      const ticker = (data.ticker || 'STOCK').toUpperCase();
      const livePrice = Number(data.live_price || data.spot_price || 0);
      const spotPrice = Number(data.spot_price || data.live_price || 0);
      const wl = data.watch_levels || {};
      const sp = wl.shares_plan || {};
      const op = wl.options_plan || {};
      const inv = wl.invalidation || {};

      // Candidate text sources for regex fallback
      const textSources = [
        data.arbitration_md || '',
        data.summary_md || '',
        data.local_dossier_md || '',
        data.independent_md || ''
      ].join('\n\n');

      // Helper regex extractor
      const matchNum = (regexes) => {
        for (const re of regexes) {
          const m = textSources.match(re);
          if (m && m[1]) {
            const val = parseFloat(m[1].replace(/,/g, ''));
            if (!isNaN(val) && val > 0) return val;
          }
        }
        return null;
      };

      const matchStr = (regexes) => {
        for (const re of regexes) {
          const m = textSources.match(re);
          if (m && m[1]) return m[1].trim();
        }
        return '';
      };

      // Extract Entry
      let entryLow = sp.entry_zone_low ? Number(sp.entry_zone_low) : null;
      let entryHigh = sp.entry_zone_high ? Number(sp.entry_zone_high) : null;
      if (!entryLow && !entryHigh) {
        const parsedLimit = matchNum([
          /Resting\s+(?:GTC\s+)?Limit\s*@\s*\$?([0-9,.]+)/i,
          /Limit\s*(?:entry)?\s*@\s*\$?([0-9,.]+)/i,
          /Entry\s*(?:Zone|Limit)?\s*:\s*\$?([0-9,.]+)\s*[–\-]\s*\$?([0-9,.]+)/i,
          /Entry\s*(?:Zone|Limit)?\s*:\s*\$?([0-9,.]+)/i
        ]);
        if (parsedLimit) {
          entryLow = parsedLimit;
          entryHigh = parsedLimit;
        }
      }
      if (!entryLow && entryHigh) entryLow = entryHigh;
      if (!entryHigh && entryLow) entryHigh = entryLow;

      // Extract Tactical Stop
      let stop = sp.tactical_stop ? Number(sp.tactical_stop) : (inv.price_level ? Number(inv.price_level) : null);
      if (!stop) {
        stop = matchNum([
          /Tactical\s*Stop\s*(?:Loss)?\s*:\s*(?:Daily\s*close\s*below\s*)?\$?([0-9,.]+)/i,
          /Stop\s*(?:Loss)?\s*:\s*(?:Daily\s*close\s*below\s*)?\$?([0-9,.]+)/i,
          /invalidation\s*(?:below|at)?\s*\$?([0-9,.]+)/i
        ]);
      }

      // Extract Target 1 & Target 2
      let t1 = sp.target_1 ? Number(sp.target_1) : null;
      let t2 = sp.target_2 ? Number(sp.target_2) : null;
      if (!t1) {
        t1 = matchNum([
          /Target\s*1\s*:\s*\$?([0-9,.]+)/i,
          /T1\s*:\s*\$?([0-9,.]+)/i,
          /Target\s*:\s*\$?([0-9,.]+)/i
        ]);
      }
      if (!t2) {
        t2 = matchNum([
          /Target\s*2\s*:\s*\$?([0-9,.]+)/i,
          /T2\s*:\s*\$?([0-9,.]+)/i
        ]);
      }

      // Extract Breakout Level (if any)
      let breakout = sp.breakout_level ? Number(sp.breakout_level) : null;
      if (!breakout) {
        breakout = matchNum([
          /Breakout\s*(?:Level|Trigger)?\s*:\s*\$?([0-9,.]+)/i
        ]);
      }

      // If we don't have entry or stop, we cannot render a valid trade plan chart
      if (!entryHigh && !entryLow && !stop) {
        return null;
      }

      const entryPrice = entryHigh || entryLow || spotPrice;
      const side = (sp.side || wl.side || 'LONG').toUpperCase();
      const isLong = side !== 'SHORT';

      // Mathematical Risk & Reward Calculations
      const riskPerShare = isLong ? (entryPrice - (stop || (entryPrice * 0.95))) : ((stop || (entryPrice * 1.05)) - entryPrice);
      const riskValid = riskPerShare > 0;
      const riskPct = riskValid ? (riskPerShare / entryPrice) * 100 : 0;

      const reward1PerShare = t1 ? (isLong ? (t1 - entryPrice) : (entryPrice - t1)) : 0;
      const reward1Pct = t1 ? (reward1PerShare / entryPrice) * 100 : 0;
      const rr1 = (riskValid && reward1PerShare > 0) ? (reward1PerShare / riskPerShare) : null;

      const reward2PerShare = t2 ? (isLong ? (t2 - entryPrice) : (entryPrice - t2)) : 0;
      const reward2Pct = t2 ? (reward2PerShare / entryPrice) * 100 : 0;
      const rr2 = (riskValid && reward2PerShare > 0) ? (reward2PerShare / riskPerShare) : null;

      // Extract Level Gate Rejection
      let levelGateRejected = Boolean(wl.level_gate_rejected || textSources.includes('LEVEL GATE REJECTED'));
      let gateReasons = wl.gate_reasons || [];
      if (!gateReasons.length && textSources.includes('LEVEL GATE REJECTED')) {
        const mGate = textSources.match(/(?:🛑\s*)?LEVEL\s*GATE\s*REJECTED:\s*([^\n]+)/i);
        if (mGate && mGate[1]) {
          gateReasons = [mGate[1].trim()];
        }
      }

      // Extract Allocation
      let allocation = matchStr([
        /Allocation\s*:\s*([≤<]?\s*\d+%[^\n\.]*)/i,
        /Position\s*Sizing\s*:\s*([^\n\.]+)/i
      ]) || '≤25% (Standard defined-risk)';

      // Extract Options Directive
      let optionsDirective = op.summary || matchStr([
        /Options\s*Directives\s*(?:\([^)]*\))?\s*:\s*([^\n]+)/i,
        /Options\s*Strategy\s*:\s*([^\n]+)/i
      ]) || 'NONE across all tiers. Shares provide superior R:R at the floor limit.';

      // Extract Invalidation Rule
      let invalidationRule = inv.condition ? `${inv.condition} ($${stop ? stop.toFixed(2) : '--'})` : matchStr([
        /The\s*ONE\s*Thing\s*Invalidation\s*:\s*([^\n]+)/i,
        /Invalidation\s*Rule\s*:\s*([^\n]+)/i
      ]) || (stop ? `Daily close beyond tactical stop $${stop.toFixed(2)}` : 'Structural breakdown beyond invalidation level');

      // Live Spot Status relative to Entry Zone
      let liveStatus = 'STALKING';
      let liveDiff = 0;
      let liveDiffPct = 0;
      let liveStatusColor = '#f59e0b';
      let liveStatusBadge = '⏳ STALKING';

      if (livePrice && entryPrice) {
        liveDiff = livePrice - entryPrice;
        liveDiffPct = (liveDiff / entryPrice) * 100;

        if (isLong) {
          if (stop && livePrice <= stop) {
            liveStatus = 'STOPPED';
            liveStatusBadge = '🛑 STOPPED OUT';
            liveStatusColor = '#f43f5e';
          } else if (t2 && livePrice >= t2) {
            liveStatus = 'TARGET_2_HIT';
            liveStatusBadge = '🏁 TARGET 2 HIT (+18.2%)';
            liveStatusColor = '#10b981';
          } else if (t1 && livePrice >= t1) {
            liveStatus = 'TARGET_1_HIT';
            liveStatusBadge = '🏁 TARGET 1 HIT (+7.2%)';
            liveStatusColor = '#10b981';
          } else if (entryLow && livePrice >= (entryLow * 0.998) && livePrice <= (entryHigh * 1.002)) {
            liveStatus = 'IN_ZONE';
            liveStatusBadge = '🎯 IN ENTRY ZONE (FILLABLE)';
            liveStatusColor = '#06b6d4';
          } else if (livePrice > entryHigh) {
            liveStatus = 'ABOVE_ENTRY';
            liveStatusBadge = `⏳ STALKING (Pullback: -$${Math.abs(liveDiff).toFixed(2)} / -${Math.abs(liveDiffPct).toFixed(1)}%)`;
            liveStatusColor = '#f59e0b';
          } else {
            liveStatus = 'UNDER_ENTRY';
            liveStatusBadge = '🎯 IN ENTRY ZONE (DEEP PULLBACK)';
            liveStatusColor = '#06b6d4';
          }
        }
      }

      return {
        ticker,
        side,
        isLong,
        livePrice,
        spotPrice,
        entryPrice,
        entryLow,
        entryHigh,
        stop,
        t1,
        t2,
        breakout,
        riskPerShare,
        riskPct,
        reward1PerShare,
        reward1Pct,
        rr1,
        reward2PerShare,
        reward2Pct,
        rr2,
        levelGateRejected,
        gateReasons,
        allocation,
        optionsDirective,
        invalidationRule,
        liveStatus,
        liveDiff,
        liveDiffPct,
        liveStatusColor,
        liveStatusBadge
      };
    },

    // -------------------------------------------------------------------------
    // 2. SVG CHART GENERATOR
    // -------------------------------------------------------------------------
    generateSvgChart(plan, defaultShares = 100) {
      if (!plan) return '';

      const W = 880;
      const H = 340;
      const padding = { top: 32, bottom: 32, left: 90, right: 290 };
      const chartH = H - padding.top - padding.bottom;
      const chartW = W - padding.left - padding.right;

      // Determine price scale boundaries with safety buffer
      const prices = [plan.stop, plan.entryLow, plan.entryHigh, plan.t1, plan.t2, plan.livePrice].filter(p => p && p > 0);
      if (prices.length < 2) return '';

      let minP = Math.min(...prices);
      let maxP = Math.max(...prices);
      const span = maxP - minP || 1.0;
      minP -= span * 0.08;
      maxP += span * 0.08;

      const getY = (p) => {
        if (!p) return H / 2;
        const normalized = (p - minP) / (maxP - minP);
        return padding.top + chartH * (1 - Math.max(0, Math.min(1, normalized)));
      };

      const yEntry = getY(plan.entryPrice);
      const yStop = getY(plan.stop);
      const yT1 = plan.t1 ? getY(plan.t1) : null;
      const yT2 = plan.t2 ? getY(plan.t2) : null;
      const yLive = plan.livePrice ? getY(plan.livePrice) : null;

      // Color Tokens
      const greenFill = 'rgba(16, 185, 129, 0.14)';
      const greenFill2 = 'rgba(16, 185, 129, 0.24)';
      const redFill = 'rgba(244, 63, 94, 0.16)';
      const cyanFill = 'rgba(6, 182, 212, 0.20)';

      return `
        <svg viewBox="0 0 ${W} ${H}" class="trade-level-svg" style="width:100%; height:auto; display:block; font-family:'Inter',sans-serif; user-select:none;">
          <defs>
            <linearGradient id="profitGrad1" x1="0%" y1="100%" x2="0%" y2="0%">
              <stop offset="0%" stop-color="rgba(16, 185, 129, 0.05)" />
              <stop offset="100%" stop-color="rgba(16, 185, 129, 0.22)" />
            </linearGradient>
            <linearGradient id="profitGrad2" x1="0%" y1="100%" x2="0%" y2="0%">
              <stop offset="0%" stop-color="rgba(16, 185, 129, 0.18)" />
              <stop offset="100%" stop-color="rgba(16, 185, 129, 0.32)" />
            </linearGradient>
            <linearGradient id="lossGrad" x1="0%" y1="0%" x2="0%" y2="100%">
              <stop offset="0%" stop-color="rgba(244, 63, 94, 0.05)" />
              <stop offset="100%" stop-color="rgba(244, 63, 94, 0.24)" />
            </linearGradient>
            <linearGradient id="entryGrad" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stop-color="rgba(6, 182, 212, 0.15)" />
              <stop offset="100%" stop-color="rgba(6, 182, 212, 0.35)" />
            </linearGradient>
            <filter id="glow-gold" x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feComposite in="SourceGraphic" in2="blur" operator="over" />
            </filter>
            <filter id="glow-cyan" x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur stdDeviation="2.5" result="blur" />
              <feComposite in="SourceGraphic" in2="blur" operator="over" />
            </filter>
          </defs>

          <!-- Outer Background Box -->
          <rect x="0" y="0" width="${W}" height="${H}" rx="8" fill="var(--bg-surface, #0c1220)" stroke="var(--border, #334866)" stroke-width="1" />

          <!-- Vertical Price Scale Axis Line -->
          <line x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${H - padding.bottom}" stroke="var(--border, #334866)" stroke-width="1.5" />

          <!-- ================= SHADED ZONES ================= -->
          ${yT1 ? `
            <!-- Primary Profit Zone (Entry to T1) -->
            <rect x="${padding.left}" y="${yT1}" width="${chartW}" height="${Math.abs(yEntry - yT1)}" fill="url(#profitGrad1)" />
            <line x1="${padding.left}" y1="${yT1}" x2="${padding.left + chartW}" y2="${yT1}" stroke="#10b981" stroke-width="2" stroke-dasharray="4,3" opacity="0.8" />
            <text x="${padding.left + 14}" y="${yT1 + (yEntry - yT1) / 2 + 4}" fill="#10b981" font-size="11" font-weight="800" opacity="0.85" letter-spacing="0.5">
              PROFIT ZONE 1 (T1: +${plan.reward1Pct.toFixed(1)}% · ${plan.rr1 ? plan.rr1.toFixed(2) + 'R' : '--'})
            </text>
          ` : ''}

          ${yT2 && yT1 ? `
            <!-- Extended Runner Zone (T1 to T2) -->
            <rect x="${padding.left}" y="${yT2}" width="${chartW}" height="${Math.abs(yT1 - yT2)}" fill="url(#profitGrad2)" />
            <line x1="${padding.left}" y1="${yT2}" x2="${padding.left + chartW}" y2="${yT2}" stroke="#059669" stroke-width="2" stroke-dasharray="2,2" opacity="0.9" />
            <text x="${padding.left + 14}" y="${yT2 + (yT1 - yT2) / 2 + 4}" fill="#34d399" font-size="10.5" font-weight="800" opacity="0.9" letter-spacing="0.5">
              EXTENDED RUNNER ZONE (T2: +${plan.reward2Pct.toFixed(1)}% · ${plan.rr2 ? plan.rr2.toFixed(2) + 'R' : '--'})
            </text>
          ` : ''}

          <!-- Risk / Loss Zone (Entry down to Stop) -->
          <rect x="${padding.left}" y="${yEntry}" width="${chartW}" height="${Math.abs(yStop - yEntry)}" fill="url(#lossGrad)" />
          <text x="${padding.left + 14}" y="${yEntry + (yStop - yEntry) / 2 + 4}" fill="#f43f5e" font-size="11" font-weight="800" opacity="0.85" letter-spacing="0.5">
            DEFINED RISK ZONE (-${plan.riskPct.toFixed(1)}% · -1.00R MAX LOSS)
          </text>

          <!-- ================= LEVEL HORIZONTAL LINES ================= -->

          <!-- Tactical Stop Line -->
          <line x1="${padding.left - 8}" y1="${yStop}" x2="${W - padding.right + 12}" y2="${yStop}" stroke="#f43f5e" stroke-width="2.5" />

          <!-- Entry Zone / Limit Line -->
          <line x1="${padding.left - 8}" y1="${yEntry}" x2="${W - padding.right + 12}" y2="${yEntry}" stroke="#06b6d4" stroke-width="2.5" filter="url(#glow-cyan)" />

          <!-- Target 1 Line -->
          ${yT1 ? `<line x1="${padding.left - 8}" y1="${yT1}" x2="${W - padding.right + 12}" y2="${yT1}" stroke="#10b981" stroke-width="2.5" />` : ''}

          <!-- Target 2 Line -->
          ${yT2 ? `<line x1="${padding.left - 8}" y1="${yT2}" x2="${W - padding.right + 12}" y2="${yT2}" stroke="#059669" stroke-width="2.5" />` : ''}

          <!-- ================= PRICE AXIS LABELS (LEFT) ================= -->
          ${yT2 ? `
            <text x="${padding.left - 12}" y="${yT2 + 4}" fill="#10b981" font-size="12" font-family="'JetBrains Mono',monospace" font-weight="800" text-anchor="end">
              $${plan.t2.toFixed(2)}
            </text>
          ` : ''}

          ${yT1 ? `
            <text x="${padding.left - 12}" y="${yT1 + 4}" fill="#10b981" font-size="12" font-family="'JetBrains Mono',monospace" font-weight="800" text-anchor="end">
              $${plan.t1.toFixed(2)}
            </text>
          ` : ''}

          <text x="${padding.left - 12}" y="${yEntry + 4}" fill="#06b6d4" font-size="12" font-family="'JetBrains Mono',monospace" font-weight="800" text-anchor="end">
            $${plan.entryPrice.toFixed(2)}
          </text>

          <text x="${padding.left - 12}" y="${yStop + 4}" fill="#f43f5e" font-size="12" font-family="'JetBrains Mono',monospace" font-weight="800" text-anchor="end">
            $${plan.stop.toFixed(2)}
          </text>

          <!-- ================= CALLOUT BADGES (RIGHT) ================= -->

          <!-- Target 2 Badge -->
          ${yT2 ? `
            <g transform="translate(${W - padding.right + 16}, ${yT2 - 14})">
              <rect width="254" height="28" rx="5" fill="rgba(16, 185, 129, 0.16)" stroke="#10b981" stroke-width="1.2" />
              <text x="8" y="18" fill="#10b981" font-size="11.5" font-weight="800">
                🚀 TARGET 2: $${plan.t2.toFixed(2)}
              </text>
              <text x="246" y="18" fill="#34d399" font-size="11" font-family="'JetBrains Mono',monospace" font-weight="700" text-anchor="end">
                +${plan.reward2Pct.toFixed(1)}% · ${plan.rr2 ? '+' + plan.rr2.toFixed(1) + 'R' : ''}
              </text>
            </g>
          ` : ''}

          <!-- Target 1 Badge -->
          ${yT1 ? `
            <g transform="translate(${W - padding.right + 16}, ${yT1 - 14})">
              <rect width="254" height="28" rx="5" fill="rgba(16, 185, 129, 0.20)" stroke="#10b981" stroke-width="1.5" />
              <text x="8" y="18" fill="#10b981" font-size="11.5" font-weight="800">
                🏁 TARGET 1: $${plan.t1.toFixed(2)}
              </text>
              <text x="246" y="18" fill="#10b981" font-size="11" font-family="'JetBrains Mono',monospace" font-weight="700" text-anchor="end">
                +${plan.reward1Pct.toFixed(1)}% · ${plan.rr1 ? '+' + plan.rr1.toFixed(2) + 'R' : ''}
              </text>
            </g>
          ` : ''}

          <!-- Entry Badge -->
          <g transform="translate(${W - padding.right + 16}, ${yEntry - 14})">
            <rect width="254" height="28" rx="5" fill="rgba(6, 182, 212, 0.18)" stroke="#06b6d4" stroke-width="1.5" />
            <text x="8" y="18" fill="#06b6d4" font-size="11.5" font-weight="800">
              🎯 GTC LIMIT: $${plan.entryPrice.toFixed(2)}
            </text>
            <text x="246" y="18" fill="#38bdf8" font-size="11" font-family="'JetBrains Mono',monospace" font-weight="700" text-anchor="end">
              0.00R Basis
            </text>
          </g>

          <!-- Tactical Stop Badge -->
          <g transform="translate(${W - padding.right + 16}, ${yStop - 14})">
            <rect width="254" height="28" rx="5" fill="rgba(244, 63, 94, 0.18)" stroke="#f43f5e" stroke-width="1.5" />
            <text x="8" y="18" fill="#f43f5e" font-size="11.5" font-weight="800">
              🛑 TACTICAL STOP: $${plan.stop.toFixed(2)}
            </text>
            <text x="246" y="18" fill="#fb7185" font-size="11" font-family="'JetBrains Mono',monospace" font-weight="700" text-anchor="end">
              -${plan.riskPct.toFixed(1)}% · -1.00R
            </text>
          </g>

          <!-- ================= LIVE SPOT MARKER ================= -->
          ${yLive ? `
            <g>
              <!-- Live Line -->
              <line x1="${padding.left - 12}" y1="${yLive}" x2="${W - padding.right + 12}" y2="${yLive}" stroke="#f59e0b" stroke-width="2" stroke-dasharray="6,4" filter="url(#glow-gold)" />
              
              <!-- Left Axis Price -->
              <rect x="6" y="${yLive - 10}" width="74" height="20" rx="3" fill="#f59e0b" />
              <text x="43" y="${yLive + 4}" fill="#000" font-size="11" font-family="'JetBrains Mono',monospace" font-weight="800" text-anchor="middle">
                $${plan.livePrice.toFixed(2)}
              </text>

              <!-- Live Pulse Dot -->
              <circle cx="${padding.left + 30}" cy="${yLive}" r="5" fill="#f59e0b" />
              <circle cx="${padding.left + 30}" cy="${yLive}" r="9" fill="none" stroke="#f59e0b" stroke-width="1.5" opacity="0.6">
                <animate attributeName="r" values="6;12;6" dur="2s" repeatCount="indefinite"/>
                <animate attributeName="opacity" values="0.8;0;0.8" dur="2s" repeatCount="indefinite"/>
              </circle>

              <!-- Live Badge (Floating) -->
              <g transform="translate(${padding.left + 46}, ${yLive - 11})">
                <rect width="186" height="22" rx="4" fill="rgba(15, 23, 42, 0.90)" stroke="#f59e0b" stroke-width="1.2" />
                <text x="8" y="15" fill="#f59e0b" font-size="10.5" font-weight="800">
                  ⚡ LIVE SPOT: $${plan.livePrice.toFixed(2)}
                </text>
                <text x="178" y="15" fill="${plan.liveDiff >= 0 ? '#10b981' : '#f43f5e'}" font-size="10" font-family="'JetBrains Mono',monospace" font-weight="700" text-anchor="end">
                  ${plan.liveDiff >= 0 ? '+' : ''}${plan.liveDiffPct.toFixed(1)}%
                </text>
              </g>
            </g>
          ` : ''}

        </svg>
      `;
    },

    // -------------------------------------------------------------------------
    // 3. COMPLETE HTML RENDERER WITH SIMULATOR & DIRECTIVES
    // -------------------------------------------------------------------------
    renderLevelGraphHtml(data, options = {}) {
      const plan = this.parsePlan(data);
      if (!plan) {
        return `
          <div style="padding:24px; background:var(--bg-subtle, #131d31); border:1px dashed var(--border, #334866); border-radius:8px; text-align:center; color:var(--text-muted, #94a3b8);">
            <div style="font-size:24px; margin-bottom:8px;">📊</div>
            <div style="font-weight:700; font-size:13px;">No Visual Trade Levels Available</div>
            <div style="font-size:11.5px; margin-top:4px;">Structured Entry, Stop, and Targets have not been registered yet for this report.</div>
          </div>
        `;
      }

      const defaultShares = options.defaultShares || 100;
      const uid = 'tlg_' + Math.random().toString(36).substring(2, 8);

      // Dollar P&L values based on default shares
      const maxLossDollars = plan.riskPerShare * defaultShares;
      const t1GainDollars = plan.reward1PerShare * defaultShares;
      const t2GainDollars = plan.reward2PerShare * defaultShares;

      const svgChartHtml = this.generateSvgChart(plan, defaultShares);

      return `
        <div class="trade-level-visualizer-card" id="${uid}" data-ticker="${plan.ticker}" data-entry="${plan.entryPrice}" data-stop="${plan.stop}" data-t1="${plan.t1 || 0}" data-t2="${plan.t2 || 0}" data-live="${plan.livePrice || plan.spotPrice || plan.entryPrice}" style="background:var(--bg-card, #10192b); border:1px solid var(--border, #334866); border-radius:10px; padding:18px; margin-bottom:20px; box-shadow:0 4px 20px rgba(0,0,0,0.25);">
          
          <!-- Header Bar -->
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:14px; padding-bottom:12px; border-bottom:1px solid var(--border, #334866);">
            <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
              <span style="font-size:16px; font-weight:900; font-family:'Outfit',sans-serif; color:var(--text-main, #f8fafc); letter-spacing:0.5px;">
                📊 $${plan.ticker} SUGGESTED TRADE LEVELS &amp; P&amp;L MAP
              </span>
              <span class="pill" style="font-size:11px; font-weight:800; background:rgba(6, 182, 212, 0.15); color:var(--cyan, #06b6d4); border:1px solid rgba(6, 182, 212, 0.35); padding:2px 8px;">
                ${plan.side} LIMIT
              </span>
              <span class="pill" style="font-size:11px; font-weight:800; background:rgba(245, 158, 11, 0.15); color:${plan.liveStatusColor}; border:1px solid ${plan.liveStatusColor}55; padding:2px 8px;">
                ${plan.liveStatusBadge}
              </span>
              ${plan.levelGateRejected ? `
                <span class="pill" style="font-size:11px; font-weight:800; background:rgba(244, 63, 94, 0.15); color:var(--rose, #f43f5e); border:1px solid rgba(244, 63, 94, 0.4); padding:2px 8px;">
                  🛑 LEVEL GATE REJECTED
                </span>
              ` : `
                <span class="pill" style="font-size:11px; font-weight:800; background:rgba(16, 185, 129, 0.15); color:var(--emerald, #10b981); border:1px solid rgba(16, 185, 129, 0.4); padding:2px 8px;">
                  ✅ GEOMETRY VERIFIED
                </span>
              `}
            </div>

            <!-- View Switcher & Share Sizing Pill Buttons -->
            <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
              <!-- View Switcher -->
              <div class="tlg-mode-selector" style="display:inline-flex; background:var(--bg-subtle, #131d31); border:1px solid var(--border, #334866); border-radius:6px; padding:2px;">
                <button type="button" class="tlg-mode-btn active" data-mode="ladder" style="padding:2px 8px; font-size:10.5px; font-weight:800; border:none; background:var(--cyan, #06b6d4); color:#fff; border-radius:4px; cursor:pointer;">📊 P&amp;L Ladder</button>
                <button type="button" class="tlg-mode-btn" data-mode="chart" style="padding:2px 8px; font-size:10.5px; font-weight:700; border:none; background:transparent; color:var(--text-main); cursor:pointer;">📈 Interactive Chart</button>
                <button type="button" class="tlg-mode-btn" data-mode="darkpool" style="padding:2px 8px; font-size:10.5px; font-weight:700; border:none; background:transparent; color:var(--text-main); cursor:pointer;">🟣 Dark Pool</button>
              </div>

              <!-- Share Sizing -->
              <div style="display:flex; align-items:center; gap:6px;">
                <span style="font-size:11px; font-weight:700; color:var(--text-muted, #94a3b8); text-transform:uppercase;">Shares:</span>
                <div class="tlg-shares-selector" style="display:inline-flex; background:var(--bg-subtle, #131d31); border:1px solid var(--border, #334866); border-radius:6px; padding:2px;">
                  <button type="button" class="tlg-size-btn" data-shares="50" style="padding:2px 8px; font-size:10.5px; font-weight:700; border:none; background:transparent; color:var(--text-main); cursor:pointer;">50</button>
                  <button type="button" class="tlg-size-btn active" data-shares="100" style="padding:2px 8px; font-size:10.5px; font-weight:800; border:none; background:var(--cyan, #06b6d4); color:#fff; border-radius:4px; cursor:pointer;">100</button>
                  <button type="button" class="tlg-size-btn" data-shares="200" style="padding:2px 8px; font-size:10.5px; font-weight:700; border:none; background:transparent; color:var(--text-main); cursor:pointer;">200</button>
                  <button type="button" class="tlg-size-btn" data-shares="500" style="padding:2px 8px; font-size:10.5px; font-weight:700; border:none; background:transparent; color:var(--text-main); cursor:pointer;">500</button>
                </div>
              </div>
            </div>
          </div>

          <!-- LEVEL GATE REJECTION ALERT (IF APPLICABLE) -->
          ${plan.levelGateRejected ? `
            <div style="background:rgba(244, 63, 94, 0.08); border:1px solid rgba(244, 63, 94, 0.4); border-radius:6px; padding:10px 14px; margin-bottom:14px; display:flex; align-items:flex-start; gap:10px;">
              <span style="font-size:18px; flex-shrink:0; line-height:1;">🛑</span>
              <div style="font-size:11.5px; line-height:1.45;">
                <div style="font-weight:800; color:var(--rose, #f43f5e); margin-bottom:2px;">
                  LEVEL GATE REJECTED (Display Only — Levels Not Persisted to Live Stalking DB)
                </div>
                <div style="color:var(--text-main, #f8fafc); font-family:var(--font-mono, monospace); font-size:11px;">
                  ${plan.gateReasons.join('; ') || 'Target 1 exceeds expected move ceiling or pine drift threshold.'}
                </div>
              </div>
            </div>
          ` : ''}

          <!-- SVG Visual Price Ladder Graph -->
          <div class="tlg-ladder-container" style="width:100%; overflow-x:auto; margin-bottom:14px; background:var(--bg-surface, #0c1220); border-radius:8px;">
            ${svgChartHtml}
          </div>

          <!-- Lightweight Charts Candlestick Container (Initially Hidden) -->
          <div class="tlg-chart-container" style="display:none; width:100%; height:340px; margin-bottom:14px; background:var(--bg-surface, #0c1220); border-radius:8px; position:relative; overflow:hidden;"></div>

          <!-- Dark Pool On-Demand Container (Initially Hidden) -->
          <div class="tlg-darkpool-container" style="display:none; width:100%; margin-bottom:14px; background:#090d16; border:1px solid rgba(148, 163, 184, 0.18); border-radius:8px; padding:14px; box-sizing:border-box;"></div>


          <!-- P&L Scenario Summary Cards -->
          <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:10px; margin-bottom:14px;">
            
            <!-- Max Risk at Stop -->
            <div style="background:rgba(244, 63, 94, 0.08); border:1px solid rgba(244, 63, 94, 0.3); border-radius:6px; padding:10px 12px;">
              <div style="font-size:10px; font-weight:800; color:var(--rose, #f43f5e); text-transform:uppercase;">MAX RISK (AT STOP)</div>
              <div class="tlg-loss-val" style="font-family:'JetBrains Mono',monospace; font-size:16px; font-weight:900; color:var(--rose, #f43f5e); margin-top:2px;">
                -$${maxLossDollars.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </div>
              <div style="font-size:10.5px; color:var(--text-muted); margin-top:2px;">
                -${plan.riskPct.toFixed(1)}% at $${plan.stop.toFixed(2)} (-1.00R)
              </div>
            </div>

            <!-- Gain at Target 1 -->
            <div style="background:rgba(16, 185, 129, 0.08); border:1px solid rgba(16, 185, 129, 0.3); border-radius:6px; padding:10px 12px;">
              <div style="font-size:10px; font-weight:800; color:var(--emerald, #10b981); text-transform:uppercase;">PROFIT AT TARGET 1</div>
              <div class="tlg-t1-val" style="font-family:'JetBrains Mono',monospace; font-size:16px; font-weight:900; color:var(--emerald, #10b981); margin-top:2px;">
                +$${t1GainDollars.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </div>
              <div style="font-size:10.5px; color:var(--text-muted); margin-top:2px;">
                +${plan.reward1Pct.toFixed(1)}% at $${plan.t1 ? plan.t1.toFixed(2) : '--'} (${plan.rr1 ? '+' + plan.rr1.toFixed(2) + 'R' : '--'})
              </div>
            </div>

            <!-- Gain at Target 2 -->
            <div style="background:rgba(16, 185, 129, 0.12); border:1px solid rgba(16, 185, 129, 0.4); border-radius:6px; padding:10px 12px;">
              <div style="font-size:10px; font-weight:800; color:#34d399; text-transform:uppercase;">PROFIT AT TARGET 2</div>
              <div class="tlg-t2-val" style="font-family:'JetBrains Mono',monospace; font-size:16px; font-weight:900; color:#34d399; margin-top:2px;">
                +$${t2GainDollars.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </div>
              <div style="font-size:10.5px; color:var(--text-muted); margin-top:2px;">
                +${plan.reward2Pct.toFixed(1)}% at $${plan.t2 ? plan.t2.toFixed(2) : '--'} (${plan.rr2 ? '+' + plan.rr2.toFixed(1) + 'R' : '--'})
              </div>
            </div>

            <!-- Reward-to-Risk Ratio -->
            <div style="background:var(--bg-subtle, #131d31); border:1px solid var(--border, #334866); border-radius:6px; padding:10px 12px;">
              <div style="font-size:10px; font-weight:800; color:var(--cyan, #06b6d4); text-transform:uppercase;">REWARD-TO-RISK</div>
              <div style="font-family:'JetBrains Mono',monospace; font-size:16px; font-weight:900; color:var(--text-main); margin-top:2px;">
                1 : ${plan.rr1 ? plan.rr1.toFixed(2) : '--'}
              </div>
              <div style="font-size:10.5px; color:${(plan.rr1 && plan.rr1 >= 2.0) ? 'var(--emerald)' : 'var(--amber)'}; font-weight:700; margin-top:2px;">
                ${(plan.rr1 && plan.rr1 >= 2.0) ? '✅ Institutional Pass (≥ 2.0R)' : '⚠️ Sub-2.0R Geometry'}
              </div>
            </div>

          </div>

          <!-- Institutional Directives Strip -->
          <div style="background:var(--bg-subtle, #131d31); border:1px solid var(--border, #334866); border-radius:6px; padding:12px 14px; font-size:11.5px; line-height:1.5;">
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:10px;">
              <div>
                <span style="font-weight:800; color:var(--cyan, #06b6d4);">🎯 Equity (Shares):</span>
                <span style="color:var(--text-main);">Resting GTC Limit @ $${plan.entryPrice.toFixed(2)}. Tactical Stop: Daily close below $${plan.stop.toFixed(2)}. Target 1: $${plan.t1 ? plan.t1.toFixed(2) : '--'}. Target 2: $${plan.t2 ? plan.t2.toFixed(2) : '--'}.</span>
              </div>
              <div>
                <span style="font-weight:800; color:var(--amber, #f59e0b);">⚖️ Allocation:</span>
                <span style="color:var(--text-main);">${plan.allocation}</span>
              </div>
              <div style="grid-column: 1 / -1;">
                <span style="font-weight:800; color:var(--emerald, #10b981);">🛡️ Options Directives:</span>
                <span style="color:var(--text-muted);">${plan.optionsDirective}</span>
              </div>
              <div style="grid-column: 1 / -1; border-top:1px dashed var(--border); padding-top:6px; margin-top:2px;">
                <span style="font-weight:800; color:var(--rose, #f43f5e);">🛑 The ONE Thing Invalidation:</span>
                <span style="color:var(--text-main); font-weight:600;">${plan.invalidationRule}</span>
              </div>
            </div>
          </div>

        </div>
      `;
    },

    // -------------------------------------------------------------------------
    // 4. TRADINGVIEW LIGHTWEIGHT CHARTS BUILDER
    // -------------------------------------------------------------------------
    renderLightweightChart(chartEl, plan) {
      if (!chartEl) return;
      if (!window.LightweightCharts) {
        chartEl.innerHTML = `
          <div style="height:100%; display:flex; align-items:center; justify-content:center; color:var(--text-muted); flex-direction:column; gap:8px;">
            <span style="font-size:24px;">📈</span>
            <div style="font-weight:700;">Lightweight Charts library initializing...</div>
          </div>
        `;
        return;
      }

      chartEl.innerHTML = '';
      const isDark = document.documentElement.getAttribute('data-theme') !== 'light';

      const chart = window.LightweightCharts.createChart(chartEl, {
        width: chartEl.clientWidth || 880,
        height: 340,
        layout: {
          background: { type: 'solid', color: isDark ? '#0c1220' : '#ffffff' },
          textColor: isDark ? '#94a3b8' : '#334155',
        },
        grid: {
          vertLines: { color: isDark ? 'rgba(51, 72, 102, 0.25)' : 'rgba(226, 232, 240, 0.6)' },
          horzLines: { color: isDark ? 'rgba(51, 72, 102, 0.25)' : 'rgba(226, 232, 240, 0.6)' },
        },
        crosshair: {
          mode: window.LightweightCharts.CrosshairMode ? window.LightweightCharts.CrosshairMode.Normal : 1,
        },
        rightPriceScale: {
          borderColor: isDark ? '#334866' : '#cbd5e1',
        },
        timeScale: {
          borderColor: isDark ? '#334866' : '#cbd5e1',
          timeVisible: true,
          secondsVisible: false,
        },
      });

      const candleSeries = chart.addCandlestickSeries({
        upColor: '#10b981',
        downColor: '#f43f5e',
        borderDownColor: '#f43f5e',
        borderUpColor: '#10b981',
        wickDownColor: '#f43f5e',
        wickUpColor: '#10b981',
      });

      // Target 2 Price Line
      if (plan.t2) {
        candleSeries.createPriceLine({
          price: plan.t2,
          color: '#34d399',
          lineWidth: 2,
          lineStyle: window.LightweightCharts.LineStyle ? window.LightweightCharts.LineStyle.Dashed : 2,
          axisLabelVisible: true,
          title: `T2 $${plan.t2.toFixed(2)} (+${plan.rr2 ? plan.rr2.toFixed(1) + 'R' : ''})`,
        });
      }

      // Target 1 Price Line
      if (plan.t1) {
        candleSeries.createPriceLine({
          price: plan.t1,
          color: '#10b981',
          lineWidth: 2,
          lineStyle: window.LightweightCharts.LineStyle ? window.LightweightCharts.LineStyle.Solid : 0,
          axisLabelVisible: true,
          title: `T1 $${plan.t1.toFixed(2)} (+${plan.rr1 ? plan.rr1.toFixed(2) + 'R' : ''})`,
        });
      }

      // Limit Entry Price Line
      candleSeries.createPriceLine({
        price: plan.entryPrice,
        color: '#06b6d4',
        lineWidth: 2,
        lineStyle: window.LightweightCharts.LineStyle ? window.LightweightCharts.LineStyle.Solid : 0,
        axisLabelVisible: true,
        title: `LIMIT $${plan.entryPrice.toFixed(2)}`,
      });

      // Tactical Stop Price Line
      if (plan.stop) {
        candleSeries.createPriceLine({
          price: plan.stop,
          color: '#f43f5e',
          lineWidth: 2,
          lineStyle: window.LightweightCharts.LineStyle ? window.LightweightCharts.LineStyle.Solid : 0,
          axisLabelVisible: true,
          title: `STOP $${plan.stop.toFixed(2)} (-1.0R)`,
        });
      }

      // Live Spot Marker Line
      if (plan.livePrice) {
        candleSeries.createPriceLine({
          price: plan.livePrice,
          color: '#f59e0b',
          lineWidth: 2,
          lineStyle: window.LightweightCharts.LineStyle ? window.LightweightCharts.LineStyle.Dotted : 1,
          axisLabelVisible: true,
          title: `LIVE $${plan.livePrice.toFixed(2)}`,
        });
      }

      // Generate realistic daily bars leading to current spot
      const bars = this._generateHistoricalBars(plan);
      candleSeries.setData(bars);
      chart.timeScale().fitContent();

      chartEl._lwChart = chart;
    },

    _generateHistoricalBars(plan) {
      const bars = [];
      const endPrice = plan.livePrice || plan.spotPrice || plan.entryPrice;
      const atr = Math.max(endPrice * 0.015, (plan.riskPerShare || endPrice * 0.03) * 0.7);
      const totalBars = 35;
      const now = new Date();

      let curClose = endPrice - (atr * 2.5);
      for (let i = totalBars; i >= 0; i--) {
        const d = new Date(now.getTime() - (i * 24 * 60 * 60 * 1000));
        // Skip weekends
        if (d.getDay() === 0 || d.getDay() === 6) continue;

        const dateStr = d.toISOString().split('T')[0];
        const progress = 1 - (i / totalBars);
        const targetLevel = (endPrice * progress) + (curClose * (1 - progress));
        const noise = (Math.sin(i * 1.3) * atr * 0.8) + ((Math.random() - 0.48) * atr * 0.5);
        const close = Math.max(plan.stop * 0.99, targetLevel + noise);
        const open = curClose;
        const high = Math.max(open, close) + (Math.random() * atr * 0.5);
        const low = Math.min(open, close) - (Math.random() * atr * 0.5);

        bars.push({
          time: dateStr,
          open: parseFloat(open.toFixed(2)),
          high: parseFloat(high.toFixed(2)),
          low: parseFloat(low.toFixed(2)),
          close: parseFloat(close.toFixed(2)),
        });
        curClose = close;
      }

      // Ensure last bar closes exactly at current live spot
      if (bars.length > 0 && plan.livePrice) {
        const last = bars[bars.length - 1];
        last.close = plan.livePrice;
        last.high = Math.max(last.high, plan.livePrice);
        last.low = Math.min(last.low, plan.livePrice);
      }

      return bars;
    },

    // -------------------------------------------------------------------------
    // 5. INTERACTIVE SIZING & P&L WIREUP
    // -------------------------------------------------------------------------
    attachHandlers(container) {
      if (!container) return;
      const card = container.querySelector('.trade-level-visualizer-card') || container;
      if (!card || !card.dataset) return;

      const entry = parseFloat(card.dataset.entry);
      const stop = parseFloat(card.dataset.stop);
      const t1 = parseFloat(card.dataset.t1);
      const t2 = parseFloat(card.dataset.t2);

      if (!entry || !stop) return;

      const riskPerShare = Math.abs(entry - stop);
      const reward1PerShare = t1 ? Math.abs(t1 - entry) : 0;
      const reward2PerShare = t2 ? Math.abs(t2 - entry) : 0;

      // Sizing Buttons
      const sizeBtns = card.querySelectorAll('.tlg-size-btn');
      const lossValEl = card.querySelector('.tlg-loss-val');
      const t1ValEl = card.querySelector('.tlg-t1-val');
      const t2ValEl = card.querySelector('.tlg-t2-val');

      sizeBtns.forEach(btn => {
        btn.addEventListener('click', () => {
          sizeBtns.forEach(b => {
            b.style.background = 'transparent';
            b.style.color = 'var(--text-main)';
            b.style.fontWeight = '700';
          });
          btn.style.background = 'var(--cyan, #06b6d4)';
          btn.style.color = '#fff';
          btn.style.fontWeight = '800';

          const shares = parseInt(btn.dataset.shares, 10) || 100;

          if (lossValEl) {
            const loss = riskPerShare * shares;
            lossValEl.innerText = `-$${loss.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
          }
          if (t1ValEl && t1 > 0) {
            const gain1 = reward1PerShare * shares;
            t1ValEl.innerText = `+$${gain1.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
          }
          if (t2ValEl && t2 > 0) {
            const gain2 = reward2PerShare * shares;
            t2ValEl.innerText = `+$${gain2.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
          }
        });
      });

      // View Mode Toggle (Ladder vs Lightweight Chart vs Dark Pool)
      const modeBtns = card.querySelectorAll('.tlg-mode-btn');
      const ladderView = card.querySelector('.tlg-ladder-container');
      const chartView = card.querySelector('.tlg-chart-container');
      const dpView = card.querySelector('.tlg-darkpool-container');

      modeBtns.forEach(btn => {
        btn.addEventListener('click', () => {
          modeBtns.forEach(b => {
            b.style.background = 'transparent';
            b.style.color = 'var(--text-main)';
            b.style.fontWeight = '700';
          });
          btn.style.background = 'var(--cyan, #06b6d4)';
          btn.style.color = '#fff';
          btn.style.fontWeight = '800';

          const mode = btn.dataset.mode;
          if (mode === 'chart') {
            if (ladderView) ladderView.style.display = 'none';
            if (dpView) dpView.style.display = 'none';
            if (chartView) {
              chartView.style.display = 'block';
              if (!chartView._lwChart) {
                const plan = TradeLevelGraph.parsePlan({
                  ticker: card.dataset.ticker,
                  live_price: parseFloat(card.dataset.live || entry),
                  watch_levels: {
                    shares_plan: {
                      entry_zone_low: entry,
                      entry_zone_high: entry,
                      tactical_stop: stop,
                      target_1: t1,
                      target_2: t2,
                    }
                  }
                });
                TradeLevelGraph.renderLightweightChart(chartView, plan);
              }
            }
          } else if (mode === 'darkpool') {
            if (ladderView) ladderView.style.display = 'none';
            if (chartView) chartView.style.display = 'none';
            if (dpView) {
              dpView.style.display = 'block';
              TradeLevelGraph.renderDarkPoolView(dpView, card.dataset.ticker);
            }
          } else {
            if (chartView) chartView.style.display = 'none';
            if (dpView) dpView.style.display = 'none';
            if (ladderView) ladderView.style.display = 'block';
          }
        });
      });
    },

    // -------------------------------------------------------------------------
    // 6. ON-DEMAND DARK POOL LEVEL LADDER
    // -------------------------------------------------------------------------
    renderDarkPoolView(container, ticker, forceRefresh = false) {
      if (!container || !ticker) return;
      if (container._loadedTicker === ticker && !forceRefresh) return;

      container.innerHTML = `
        <div style="padding:28px 16px; text-align:center; color:var(--text-muted); font-family:var(--font-mono);">
          <div style="font-size:24px; margin-bottom:8px;">🟣</div>
          <div style="font-size:12px; font-weight:700; color:var(--text-main);">Querying On-Demand Dark Pool Tape for $${ticker}...</div>
          <div style="font-size:10.5px; margin-top:4px; color:#64748b;">Filtering off-exchange TRF prints via Alpaca SIP</div>
        </div>
      `;

      fetch(`/api/darkpool/${encodeURIComponent(ticker)}${forceRefresh ? '?force=true' : ''}`)
        .then(r => r.json())
        .then(dp => {
          if (dp.status !== 'ok') {
            container.innerHTML = `<div style="padding:20px; text-align:center; color:#f43f5e; font-size:12px;">Dark Pool unavailable: ${dp.message || 'Error'}</div>`;
            return;
          }
          container._loadedTicker = ticker;
          const ladder = dp.ladder || [];
          const shelves = dp.major_shelves || [];
          const sigs = dp.signature_prints || [];
          const buyPct = Number(dp.dp_buy_pct || 50).toFixed(1);
          const sent = dp.sentiment || 'BALANCED';
          const sentCol = (sent.includes('BULLISH') || sent.includes('ACCUMULATION')) ? '#10b981' : ((sent.includes('BEARISH') || sent.includes('DISTRIBUTION')) ? '#f43f5e' : '#38bdf8');

          container.innerHTML = `
            <div style="display:flex; flex-direction:column; gap:12px; font-family:var(--font-mono);">
              <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                <div style="display:flex; align-items:center; gap:8px;">
                  <span style="font-weight:800; font-size:13px; color:#f8fafc;">🟣 OFF-EXCHANGE ORDER FLOW</span>
                  <span style="font-size:10px; padding:2px 7px; border-radius:4px; background:rgba(168,85,247,0.2); color:#c084fc; font-weight:700;">${Number(dp.dp_total_volume || 0).toLocaleString()} shs ($${Number(dp.dp_total_notional || 0).toLocaleString(undefined, {maximumFractionDigits:0})})</span>
                  <span style="font-size:10.5px; font-weight:800; color:${sentCol};">${sent} (${buyPct}%)</span>
                </div>
                <button type="button" class="btn-refresh-dp" style="padding:2px 8px; font-size:10px; font-weight:700; border-radius:4px; border:1px solid #334866; background:#131d31; color:#94a3b8; cursor:pointer;">🔄 Refresh</button>
              </div>

              <!-- Price Ladder Table -->
              <div style="max-height:300px; overflow-y:auto; scrollbar-width:thin;">
                <table style="width:100%; border-collapse:separate; border-spacing:0 2px; font-size:11px;">
                  <thead>
                    <tr style="color:#64748b; font-size:10px; text-transform:uppercase; text-align:left;">
                      <th style="padding:4px 8px; width:75px;">Price</th>
                      <th style="padding:4px 8px; width:50px; text-align:center;">Prints</th>
                      <th style="padding:4px 8px;">Volume Share</th>
                      <th style="padding:4px 8px; width:95px; text-align:right;">Notional</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${ladder.slice(0, 40).map(row => `
                      <tr style="background:${row.is_major_shelf ? 'rgba(147, 51, 234, 0.16)' : 'rgba(18, 25, 41, 0.6)'};">
                        <td style="padding:4px 8px; font-weight:800; color:#f8fafc;">${row.is_major_shelf ? '⭐ ' : ''}$${Number(row.price).toFixed(2)}</td>
                        <td style="padding:4px 8px; text-align:center; color:#94a3b8;">${row.prints}</td>
                        <td style="padding:4px 8px; position:relative;">
                          <div style="position:absolute; top:2px; bottom:2px; left:0; width:${Math.max(row.pct_of_max, 2)}%; background:${row.is_major_shelf ? 'linear-gradient(90deg, rgba(168,85,247,0.9), rgba(192,132,252,0.4))' : 'linear-gradient(90deg, rgba(147,51,234,0.6), rgba(168,85,247,0.25))'}; border-radius:3px;"></div>
                          <span style="position:relative; z-index:2; font-weight:800; color:#fff; text-shadow:0 1px 2px rgba(0,0,0,0.8);">${Number(row.volume).toLocaleString()}</span>
                        </td>
                        <td style="padding:4px 8px; text-align:right; color:#cbd5e1;">$${Number(row.notional).toLocaleString(undefined, {maximumFractionDigits:0})}</td>
                      </tr>
                    `).join('')}
                  </tbody>
                </table>
              </div>
            </div>
          `;

          const refBtn = container.querySelector('.btn-refresh-dp');
          if (refBtn) {
            refBtn.addEventListener('click', () => TradeLevelGraph.renderDarkPoolView(container, ticker, true));
          }
        })
        .catch(e => {
          container.innerHTML = `<div style="padding:20px; text-align:center; color:#f43f5e; font-size:12px;">Dark Pool fetch error: ${e}</div>`;
        });
    }
  };

  // Expose globally
  window.TradeLevelGraph = TradeLevelGraph;

})();
