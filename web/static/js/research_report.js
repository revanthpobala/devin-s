/* research_report.js — Decision tab renderer for the structured {T}_report.json.
   All LLM/grounded text arrives HTML-escaped from the backend; it is safe to inject
   as text content. For attribute contexts (title=...) use atq(): the server escapes
   <>& but leaves quotes literal, so they are entity-escaped here. */
window.ResearchDecision = (() => {

  const esc = (t) => String(t == null ? '' : t); // already entity-escaped server-side
  const atq = (t) => esc(t).replace(/"/g, '&quot;'); // safe inside title="..."
  const money = (v) => (v == null || isNaN(+v)) ? '—' : '$' + (+v).toFixed(2);
  const pct = (v, sign = true) => (v == null || isNaN(+v)) ? '—' : `${sign && +v > 0 ? '+' : ''}${(+v).toFixed(1)}%`;
  const num = (v, nd = 2) => (v == null || isNaN(+v)) ? '—' : (+v).toFixed(nd);
  const KIND_LABEL = { computed: 'computed', base_rate: 'base rate', model_estimate: 'model', scenario: 'scenario', verified: 'verified' };
  const kbadge = (kind) => kind && KIND_LABEL[kind] ? `<sup class="rd-kind rd-kind-${kind}">${KIND_LABEL[kind]}</sup>` : '';

  const DEC_CLASS = {
    BUY_NOW: 'rd-d-buynow', BUY_ON_PULLBACK: 'rd-d-pullback', WAIT: 'rd-d-wait',
    HOLD: 'rd-d-hold', TRIM: 'rd-d-wait', EXIT: 'rd-d-sell', SELL: 'rd-d-sell', AVOID: 'rd-d-avoid',
  };

  // Plan price-line styling: when the contract rejected the levels they are drawn
  // grey + dotted and titled REJECTED — they are context, not an order.
  function planLineStyle(rejected, color, title, style) {
    const LS = window.LightweightCharts ? window.LightweightCharts.LineStyle : { Dashed: 2, SparseDotted: 4 };
    return rejected
      ? { color: '#475569', title: `REJECTED ${title}`, style: LS.SparseDotted }
      : { color, title, style: style == null ? LS.Dashed : style };
  }

  function card(title, inner, span) {
    return `<div class="rd-card${span ? ' rd-span' : ''}"><h3>${title}</h3>${inner}</div>`;
  }

  // ---------- 1. verdict hero ------------------------------------------------
  function freshness(rep) {
    const asof = rep.as_of || {};
    if (!asof.live) return asof.generated_at ? `<span class="rd-pill">delayed · report ${esc(String(asof.generated_at).slice(0, 16).replace('T', ' '))}</span>` : '';
    const gen = String(asof.generated_at || '').slice(0, 10);
    const today = new Date().toISOString().slice(0, 10);
    if (gen === today) return `<span class="rd-pill ok">live · ${esc(String(asof.generated_at).slice(11, 16))}</span>`;
    const days = Math.max(1, Math.round((new Date(today) - new Date(gen)) / 864e5));
    return `<span class="rd-pill bad">stale ${days}d · live as of ${esc(gen)}</span>`;
  }

  function hero(rep) {
    const d = rep.decision || {};
    const rejected = rep.audit && rep.audit.checks_passed === false;
    const cls = DEC_CLASS[d.code] || 'rd-d-avoid';
    const why = (d.why || []).slice(0, 3).map(w => `<li>${esc(w)}</li>`).join('');
    const asof = rep.as_of || {};
    const st = rep.style || {};
    const q = rep.quant || {};
    const rs = (q.rs_spy || {})['1M'];
    const pills = [
      freshness(rep),
      d.code ? `<span class="rd-pill">${esc(d.code)}</span>` : '',
      rep.veto && rep.veto !== 'NONE' ? `<span class="rd-pill bad">veto ${esc(rep.veto)}</span>` : '',
      st.best_fit ? `<span class="rd-pill ok">style ${esc(String(st.best_fit).replace(/_/g, ' '))}</span>` : '',
      rep.position ? `<span class="rd-pill hot">position open</span>` : '',
      asof.bar_forming ? `<span class="rd-pill hot">bar forming</span>` : '',
      `<span class="rd-pill">daily/weekly setups — no intraday edge</span>`,
    ].filter(Boolean).join('');
    return card('Decision', `
      ${rejected ? '<div class="rd-rejected">LEVELS REJECTED — any levels shown are context, not an order.</div>' : ''}
      <div class="rd-hero">
        <div class="rd-verdict ${cls}">
          <div class="rd-label">${esc(d.label || 'NO DATA')}</div>
          <div class="rd-sub">${kbadge('computed')} ${esc(rep.date || '')}</div>
        </div>
        <div class="rd-hero-main">
          ${why ? `<ul class="rd-why">${why}</ul>` : ''}
          ${rep.veto_fact ? `<div class="rd-note" style="margin-top:6px;">${esc(rep.veto_fact)}</div>` : ''}
          ${st.reason ? `<div class="rd-note" style="margin-top:4px;"><em>Style:</em> ${esc(st.reason)}</div>` : ''}
          <div class="rd-price-row">
            <span>close <b>${money(asof.close)}</b> ${esc(asof.close_date || '')}${kbadge('verified')}</span>
            ${asof.live ? `<span>live <b>${money(asof.live)}</b>${kbadge('verified')}</span>` : ''}
            ${rep.rr && rep.rr.at_entry != null ? `<span>R:R entry <b>${num(rep.rr.at_entry)}</b>${kbadge('computed')}</span>` : ''}
            ${rep.rr && rep.rr.at_market != null ? `<span>R:R close <b>${num(rep.rr.at_market)}</b>${kbadge('computed')}</span>` : ''}
            ${rep.rr && rep.rr.at_live != null ? `<span>R:R live <b>${num(rep.rr.at_live)}</b>${kbadge('computed')}</span>` : ''}
            ${rs != null ? `<span>vs SPY 1M <b>${pct(rs)}pp</b>${kbadge('computed')}</span>` : ''}
          </div>
          <div class="rd-pills">${pills}</div>
        </div>
      </div>`);
  }

  // ---------- 2. price chart -------------------------------------------------
  function sma(closes, n) {
    const out = [];
    let s = 0;
    for (let i = 0; i < closes.length; i++) {
      s += closes[i];
      if (i >= n) s -= closes[i - n];
      out.push(i >= n - 1 ? s / n : null);
    }
    return out;
  }

  function drawChart(el, rep, bars) {
    if (!el || !window.LightweightCharts || !bars.length) { if (el) el.innerHTML = '<div class="rd-note" style="padding:18px;">No price history.</div>'; return; }
    el.innerHTML = '';
    const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
    const chart = window.LightweightCharts.createChart(el, {
      width: el.clientWidth || 880, height: 360,
      layout: { background: { type: 'solid', color: isDark ? '#0c1220' : '#fff' },
                textColor: isDark ? '#94a3b8' : '#334155' },
      grid: { vertLines: { color: 'rgba(51,72,102,0.25)' }, horzLines: { color: 'rgba(51,72,102,0.25)' } },
      timeScale: { borderColor: '#334866' }, rightPriceScale: { borderColor: '#334866' },
    });
    const ser = chart.addCandlestickSeries({ upColor: '#10b981', downColor: '#f43f5e',
      borderUpColor: '#10b981', borderDownColor: '#f43f5e', wickUpColor: '#10b981', wickDownColor: '#f43f5e' });
    ser.setData(bars);
    const closes = bars.map(b => b.close);
    [[20, '#22d3ee'], [50, '#fbbf24'], [200, '#a78bfa']].forEach(([n, color]) => {
      const ma = sma(closes, n);
      const line = chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      line.setData(bars.map((b, i) => ma[i] != null ? { time: b.time, value: ma[i] } : null).filter(Boolean));
    });
    const lv = rep.levels || {};
    const rejected = rep.audit && rep.audit.checks_passed === false;
    const mk = (price, color, title, style) => {
      if (!price) return;
      const s = planLineStyle(rejected, color, title, style);
      ser.createPriceLine({ price, color: s.color, lineWidth: 1, lineStyle: s.style,
                            axisLabelVisible: true, title: s.title });
    };
    mk((lv.entry_low || {}).price, '#06b6d4', 'ZONE LOW');
    mk((lv.entry_high || {}).price, '#06b6d4', 'ZONE HIGH');
    mk((lv.trigger || {}).price, '#fbbf24', 'TRIGGER');
    mk((lv.stop || {}).price, '#f43f5e', 'STOP');
    mk((lv.target_1 || {}).price, '#10b981', 'T1');
    mk((lv.target_2 || {}).price, '#34d399', 'T2');
    const ref = ((rep.as_of || {}).live || (rep.as_of || {}).close) || closes[closes.length - 1];
    Object.entries(rep.context_levels || {})
      .filter(([, v]) => v && typeof v.price === 'number')
      .sort((a, b) => Math.abs(a[1].price - ref) - Math.abs(b[1].price - ref))
      .slice(0, 4)
      .forEach(([name, v]) => {
        const c = name.startsWith('KEY_SUPPORT') || name.endsWith('SUPPORT') || name === 'VP_VAL' ? '#64748b' : '#8b5cf6';
        mk(v.price, c, String(v.label || name.replace(/_/g, ' ')).toUpperCase(), window.LightweightCharts.LineStyle.SparseDotted);
      });
    const ed = (rep.earnings || {}).date;
    if (ed) {
      const bar = bars.find(b => b.time >= ed);
      if (bar) {
        ser.setMarkers([{ time: bar.time, position: 'belowBar', color: '#f59e0b', shape: 'circle',
                          text: `EARN ${ed}${rep.earnings.median_move != null ? ` · med move ${rep.earnings.median_move}%` : ''}` }]);
      }
      const note = el.parentElement && el.parentElement.querySelector('.rd-earnings-note');
      if (note && ed > bars[bars.length - 1].time) {
        note.innerHTML = `<span class="rd-pill">earnings ${esc(ed)}${rep.earnings.median_move != null ? ` · med move ${rep.earnings.median_move}%` : ''} (off chart)</span>`;
        note.style.display = '';
      }
    }
    chart.timeScale().fitContent();
    return chart;
  }

  function chartCard(rep, bars) {
    const ranges = [['3M', 63], ['6M', 126], ['1Y', 252], ['2Y', 504], ['ALL', 2000]];
    const btns = ranges.map(([t], i) => `<button class="rd-range${i === 1 ? ' on' : ''}" data-n="${ranges[i][1]}">${t}</button>`).join('');
    const en = (rep.earnings || {});
    const off = en.date ? `<div class="rd-earnings-note" style="display:none;"></div>` : '';
    return `<div class="rd-card rd-span"><h3>Price · MAs · plan & context levels ${kbadge('computed')}</h3>
      <div class="rd-ranges">${btns}</div><div class="rd-chart" id="rd-chart"></div>${off}</div>`;
  }

  // ---------- 3. scenario range bar (one price axis) --------------------------
  function scenarioBar(rep) {
    const bbb = rep.scenarios_bbb || [];
    if (!bbb.length) return '';
    const asof = rep.as_of || {};
    const ref = asof.live || asof.close;
    const pts = bbb.map(s => ({ c: s.case, p: +s.price, s }));
    if (ref) pts.push({ c: 'PRICE', p: +ref });
    const lo = Math.min(...pts.map(x => x.p)), hi = Math.max(...pts.map(x => x.p));
    const pad = (hi - lo) * 0.12 || hi * 0.02 || 1;
    const X = (v) => 60 + ((v - (lo - pad)) / ((hi + pad) - (lo - pad))) * 880;
    const COL = { BULL: '#34d399', BASE: '#94a3b8', BEAR: '#fb7185', PRICE: '#fbbf24' };
    const nodes = pts.sort((a, b) => a.p - b.p).map(x => {
      const odds = x.s && x.s.odds && x.s.odds.pct != null ? `touch ${x.s.odds.pct}% (n=${x.s.odds.n})` : (x.s ? esc(x.s.odds || '—') : '');
      const title = x.s ? `${x.s.case} ${money(x.s.price)} ${pct(x.s.pct_from_ref)} by ${x.s.by || ''} · odds ${x.s.odds && x.s.odds.pct != null ? x.s.odds.pct + '%' : 'n/a'}\nDrivers: ${(x.s.drivers || []).join('; ')}\nAssumptions: ${(x.s.assumptions || []).join('; ')}` : `price ${money(x.p)}`;
      const top = x.c !== 'PRICE';
      return `<g><title>${atq(title)}</title>
        <line x1="${X(x.p)}" y1="${top ? 52 : 70}" x2="${X(x.p)}" y2="${top ? 108 : 118}" stroke="${COL[x.c]}" stroke-width="2"/>
        <circle cx="${X(x.p)}" cy="${top ? 40 : 126}" r="${top ? 7 : 6}" fill="${COL[x.c]}"/>
        <text x="${X(x.p)}" y="${top ? 22 : 152}" text-anchor="middle" fill="${COL[x.c]}" font-size="13" font-weight="800">${x.c}</text>
        <text x="${X(x.p)}" y="${top ? 122 : 140}" text-anchor="middle" fill="#94a3b8" font-size="11" font-family="JetBrains Mono,monospace">${money(x.p)}${x.s ? ` ${pct(x.s.pct_from_ref)}` : ''}</text>
        ${x.s ? `<text x="${X(x.p)}" y="137" text-anchor="middle" fill="#64748b" font-size="10">${atq(odds)}</text>` : ''}
      </g>`;
    }).join('');
    return card('Scenarios — one axis, bear → bull ' + kbadge('base_rate'), `
      <svg viewBox="0 0 1000 162" class="rd-svg">
        <line x1="40" y1="85" x2="960" y2="85" stroke="#334866" stroke-width="1" stroke-dasharray="4 4"/>${nodes}
      </svg>
      <div class="rd-scen-meta">Price axis ${money(lo - pad)} – ${money(hi + pad)} · markers: ${kbadge('computed')} price/% move · ${kbadge('base_rate')} empirical touch odds, each measured alone (they overlap, not a 100% split) · drivers & assumptions on hover are ${kbadge('scenario')} writer narrative</div>`, true);
  }

  // ---------- 4. horizon matrix ----------------------------------------------
  function horizons(rep) {
    const hz = rep.horizons || {};
    const keys = ['1W', '1M', '3M', '6M', '1Y', 'MULTI_YEAR'];
    const cells = keys.map(k => {
      const v = hz[k] || {};
      const b = v.base || {};
      const st = v.stance || 'NOT_ASSESSED';
      let whisk = '<div class="rd-whisk"></div>';
      if (b.p10 != null && b.p90 != null && b.p10 !== b.p90) {
        // Each whisker is scaled to its own p10–p90 range: the range fills the track
        // and p50 sits inside it. Values are labelled beside the track, not clipped.
        const L = 4, W = 92;
        const P = b.p50 != null ? L + W * Math.max(0, Math.min(1, (b.p50 - b.p10) / (b.p90 - b.p10))) : L;
        whisk = `<div class="rd-whisk"><div class="rd-whisk-range" style="left:${L}%;width:${W}%"></div>
          <div class="rd-whisk-p50" style="left:${P}%"></div></div>`;
      }
      return `<div class="rd-hz">
        <div class="rd-hz-k">${k.replace(/_/g, ' ')}</div>
        <div class="rd-stance rd-stance-${esc(st)}">${esc(st)}</div>
        ${whisk}
        <div class="rd-hz-rate">${b.p50 != null ? `${pct(b.p10)} / ${pct(b.p50)} / ${pct(b.p90)} (n=${b.n}) ${kbadge('base_rate')}` : '—'}</div>
        <div class="rd-hz-why">${esc(v.why || '')}</div>
      </div>`;
    }).join('');
    return card('Horizon matrix — empirical base rates', `<div class="rd-hz-grid">${cells}
      <div class="rd-hz rd-hz-na"><div class="rd-hz-k">INTRADAY</div><div class="rd-stance rd-stance-NOT_ASSESSED">OUT OF SCOPE</div>
        <div class="rd-hz-why">daily/weekly setups only — no intraday edge</div></div></div>`, true);
  }

  // ---------- 5. catalyst timeline --------------------------------------------
  function catalystTimeline(rep) {
    const cats = (rep.catalysts || []).slice().sort((a, b) => (a.date < b.date ? -1 : 1));
    const em = rep.earnings || {};
    const today = new Date().toISOString().slice(0, 10);
    const end = new Date(Date.now() + 183 * 864e5).toISOString().slice(0, 10);
    const X = (d) => 60 + Math.max(0, Math.min(1, (new Date(d) - new Date(today)) / (new Date(end) - new Date(today)))) * 880;
    const DAY = 864e5;
    const items = cats.map((c, i) => {
      // Alternate labels above/below the axis when two catalysts land within 10 days.
      const near = i > 0 && (new Date(c.date) - new Date(cats[i - 1].date)) < 10 * DAY;
      const above = near ? !(i % 2) : true;
      const dy = above ? { y1: 48, y2: 62, ty: 26, ey: 80 } : { y1: 62, y2: 76, ty: 100, ey: 30 };
      return `
      <g><title>${atq(`${c.event}\nif good: ${c.if_good || ''}\nif bad: ${c.if_bad || ''}`)}</title>
        <line x1="${X(c.date)}" y1="${above ? 48 : 62}" x2="${X(c.date)}" y2="${above ? 62 : 76}" stroke="#22d3ee" stroke-width="1.6"/>
        <circle cx="${X(c.date)}" cy="55" r="5" fill="#22d3ee"/>
        <text x="${X(c.date)}" y="${dy.ty}" text-anchor="middle" fill="#22d3ee" font-size="11" font-family="JetBrains Mono,monospace">${esc(c.date).slice(5)}</text>
        <text x="${X(c.date)}" y="${dy.ey}" text-anchor="middle" fill="#94a3b8" font-size="10">${atq(String(c.event || '').slice(0, 18))}</text>
      </g>`;
    }).join('');
    const earn = em.date ? `
      <g><title>${atq(`Earnings ${em.date}${em.median_move != null ? ` · median 1d |move| ${em.median_move}%` : ''}`)}</title>
        <rect x="${X(em.date) - 6}" y="49" width="12" height="12" fill="#f59e0b" transform="rotate(45 ${X(em.date)} 55)"/>
        <text x="${X(em.date)}" y="98" text-anchor="middle" fill="#f59e0b" font-size="10" font-weight="800">EARNINGS${em.median_move != null ? ` ±${em.median_move}%` : ''}</text>
      </g>` : '';
    if (!cats.length && !em.date) return '';
    return card('Catalyst timeline — next 6 months', `
      <svg viewBox="0 0 1000 110" class="rd-svg">
        <line x1="50" y1="55" x2="950" y2="55" stroke="#334866" stroke-width="1"/>
        <text x="50" y="70" fill="#64748b" font-size="10">${esc(today).slice(5)}</text>
        <text x="950" y="70" text-anchor="end" fill="#64748b" font-size="10">+6M</text>
        ${items}${earn}
      </svg>`, true);
  }

  // ---------- 6. risk panel ----------------------------------------------------
  function drawdownSvg(bars) {
    if (!bars.length) return '';
    let peak = -Infinity;
    const pts = bars.map(b => { peak = Math.max(peak, b.close); return peak > 0 ? 100 * (b.close / peak - 1) : 0; });
    const min = Math.min(...pts, -5);
    const X = (i) => 14 + (i / Math.max(1, bars.length - 1)) * 472;
    const Y = (v) => 14 + (v / min) * 88;
    const path = pts.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ');
    return `<svg viewBox="0 0 500 116" class="rd-svg">
      <path d="${path} L486,${Y(0)} L14,${Y(0)} Z" fill="rgba(244,63,94,0.18)"/>
      <path d="${path}" fill="none" stroke="#fb7185" stroke-width="1.4"/>
      <text x="14" y="${Y(0) - 4}" fill="#64748b" font-size="9">underwater % from peak · worst ${num(min, 1)}%</text>
    </svg>`;
  }

  function earningsHist(rep) {
    const evs = ((rep.quant || {}).earnings_moves || []);
    if (evs.length < 3) return '';
    const max = Math.max(...evs.map(e => e.pct), 1);
    const w = 450 / evs.length;
    const bars = evs.map((e, i) => `<g><title>${atq(`${e.date}: |${e.pct}%|`)}</title>
      <rect x="${24 + i * w}" y="${80 - (e.pct / max) * 66}" width="${Math.max(3, w - 5)}" height="${(e.pct / max) * 66}" fill="#8b5cf6" opacity="0.8"/></g>`).join('');
    return `<div class="rd-note" style="margin-top:10px;"><b>Earnings 1-day |reaction| history</b> ${kbadge('base_rate')}
      <svg viewBox="0 0 500 88" class="rd-svg">${bars}
      <line x1="20" y1="80" x2="478" y2="80" stroke="#334866" stroke-width="1"/></svg></div>`;
  }

  function riskPanel(rep, bars) {
    const q = rep.quant || {};
    const lv = rep.levels || {};
    // Stop width measured from entry_low — the lowest (worst-case) fill — same as the gate.
    const entryRef = lv.entry_low || lv.entry_high || lv.trigger;
    const stopW = lv.stop && entryRef && q.atr14 ? ((entryRef.price - lv.stop.price) / q.atr14) : null;
    const chips = [
      ['HV20 / HV60', q.hv20 != null ? `${q.hv20}%${q.hv60 != null ? ' / ' + q.hv60 + '%' : ''}` : null, 'annualized'],
      ['Beta / corr', q.beta != null ? `${q.beta} / ${q.corr_spy}` : null, '1y daily vs SPY'],
      ['Stop width', stopW != null ? `${stopW.toFixed(1)} ATR` : null, 'entry low → stop'],
      ['Max DD 1y', q.max_dd_1y != null ? q.max_dd_1y + '%' : null, q.dd_from_52w != null ? `now ${q.dd_from_52w}% off 52w` : ''],
      ['$ vol 20d', q.dollar_vol_20d != null ? '$' + (q.dollar_vol_20d / 1e6).toFixed(0) + 'M' : null, 'avg daily'],
      ['Earnings move', q.earnings_move_median != null ? '±' + q.earnings_move_median + '%' : null, 'median 1d |reaction|'],
    ].filter(c => c[1] != null).map(([k, v, s]) =>
      `<div class="rd-stat"><div class="k">${k}</div><div class="v">${v}${kbadge('computed')}</div><div class="s">${s}</div></div>`).join('');
    const inv = (lv.stop || {}).price;
    return card('Risk panel', `
      ${drawdownSvg(bars)}
      <div class="rd-stats" style="margin-top:10px;">${chips}</div>
      ${earningsHist(rep)}
      ${inv ? `<div class="rd-note" style="margin-top:10px;"><b>Invalidation:</b> daily close below <b>${money(inv)}</b>${kbadge('computed')}</div>` : ''}
      ${rep.wrong_if ? `<div class="rd-note" style="margin-top:6px;"><b>This thesis is wrong if:</b> ${esc(rep.wrong_if)}</div>` : ''}`);
  }

  // ---------- 7-8. fundamentals + market context -------------------------------
  function fundamentals(rep) {
    const items = (rep.fundamentals || []);
    if (!items.length) return '';
    return card('Fundamentals — grounded, cited', `<ul class="rd-list">${items.map(t => `<li>${esc(t)}</li>`).join('')}</ul>`);
  }

  function rsBar(v, label, ref) {
    if (v == null) return '';
    const w = Math.min(48, Math.abs(v) / 2);
    const pos = v >= 0;
    return `<div class="rd-rs-row"><span class="rd-rs-k">${label}</span>
      <div class="rd-rs-track"><div class="rd-rs-bar ${pos ? 'pos' : 'neg'}" style="${pos ? 'left:50%' : `left:${50 - w}%`};width:${Math.max(1.5, w)}%"></div></div>
      <span class="num">${pct(v)}pp${ref ? ` <span class="rd-scen-meta">vs ${ref}</span>` : ''}</span></div>`;
  }

  function marketContext(rep) {
    const q = rep.quant || {};
    const rs = q.rs_spy || {}, rs2 = q.rs_sector || {};
    const reg = q.spy_regime || {};
    const rows = ['1M', '3M', '6M', '12M'].map(k => rsBar(rs[k], `RS ${k}`, 'SPY') + rsBar(rs2[k], '', q.sector_etf)).join('');
    const chips = [
      reg.spy != null ? `<span class="rd-pill ${reg.above_200sma ? 'ok' : 'bad'}">SPY ${reg.above_200sma ? 'above' : 'below'} 200SMA${kbadge('computed')}</span>` : '',
      reg.r21 != null ? `<span class="rd-pill">SPY 21d ${pct(reg.r21)}</span>` : '',
      q.vix != null ? `<span class="rd-pill ${q.vix > 25 ? 'hot' : ''}">VIX ${q.vix}${q.vix_21d_avg ? ` (avg ${q.vix_21d_avg})` : ''}</span>` : '',
    ].filter(Boolean).join('');
    const ctx = (rep.market_context || []).map(t => `<li>${esc(t)}</li>`).join('');
    if (!rows && !chips && !ctx) return '';
    return card(`Market context${q.sector ? ` — ${esc(q.sector)}` : ''}`, `
      ${chips ? `<div class="rd-pills" style="margin:0 0 10px;">${chips}</div>` : ''}${rows}
      ${ctx ? `<ul class="rd-list" style="margin-top:10px;">${ctx}</ul>` : ''}`);
  }

  // ---------- 8b. events & transmission (context only) --------------------------
  function transmissionCard(rep) {
    const chains = rep.transmission || [];
    const tape = rep.market_tape || {};
    const rows = (tape.rows || []).filter(r => r.d1 != null);
    if (!chains.length && !rows.length) return '';
    const BADGE = { CONFIRMED: 'ok', CONTRADICTED: 'bad', UNTESTED: '' };
    const chainHtml = chains.map(ch => {
      const steps = (ch.chain || []).map((s, i) =>
        `<li><span class="rd-scen-meta">${esc(s.kind || '').toLowerCase()}</span> ${esc(s.step)}</li>`).join('');
      const conf = (ch.confirm || []).map(c =>
        `<tr><td>${esc(c.symbol)}</td><td>${esc(String(c.move || '').toLowerCase())}</td>
           <td class="num">${c.actual != null ? pct(c.actual) : '—'}</td></tr>`).join('');
      return `<div class="rd-tier">
        <div class="rd-tier-t">${esc(ch.event)}
          <span class="rd-pill ${BADGE[ch.tape_status] || ''}" style="margin-left:6px;">${esc(ch.tape_status || 'UNTESTED')}</span></div>
        <div class="rd-scen-meta">${esc(String(ch.status || '').toLowerCase())} · ${esc(String(ch.channel || '').toLowerCase().replace(/_/g, ' '))}
          · ${esc(String(ch.direction || '').toLowerCase())} · ${esc(String(ch.horizon || '').replace(/_/g, ' '))}</div>
        ${steps ? `<ul class="rd-list" style="margin-top:6px;">${steps}</ul>` : ''}
        <div class="rd-note" style="margin-top:6px;"><b>Exposure:</b> ${esc(ch.exposure || '')}</div>
        ${conf ? `<table class="rd-table" style="margin-top:6px;"><tr><th>confirm</th><th>expected</th><th>actual (1d)</th></tr>${conf}</table>` : ''}
        ${ch.invalidate ? `<div class="rd-note" style="margin-top:4px;"><b>Invalidates if:</b> ${esc(ch.invalidate)}</div>` : ''}
      </div>`;
    }).join('');
    const ext = tape.extended;
    const tapeGrid = rows.length ? `<div class="rd-stats" style="margin-top:10px;">${rows.map(r =>
      `<div class="rd-stat"><div class="k">${esc(r.symbol)}</div><div class="v">${pct(r.d1)}</div>
       <div class="s">5d ${pct(r.d5)}</div></div>`).join('')}
      ${ext ? `<div class="rd-stat"><div class="k">${esc(ext.symbol)} ext</div><div class="v">${money(ext.price)}</div>
        <div class="s">${ext.pct != null ? pct(ext.pct) + ' ext hours' : 'ext hours'}</div></div>` : ''}</div>
      <div class="rd-scen-meta" style="margin-top:4px;">tape as of ${esc(tape.as_of || 'unknown')}${tape.partial ? ' (partial — bar still forming)' : ''} · ${kbadge('computed')}</div>` : '';
    const dropped = (rep.audit && rep.audit.transmission_withheld) || [];
    const droppedHtml = dropped.length ? `<details style="margin-top:8px;"><summary class="rd-scen-meta">${dropped.length} chain(s) removed by code checks</summary>
      <ul class="rd-list">${dropped.map(w => `<li>${esc(w)}</li>`).join('')}</ul></details>` : '';
    return card('Events & transmission — context only ' + kbadge('scenario'), `
      <div class="rd-note" style="margin-bottom:8px;">Causal chains the writer considered — they inform the read and cannot change the verdict or the levels.</div>
      ${chainHtml}${tapeGrid}${droppedHtml}`, true);
  }

  // ---------- 8c. held position ------------------------------------------------
  function positionCard(rep) {
    const p = rep.position;
    if (!p || typeof p !== 'object') return '';
    const rows = [];
    const qty = +p.quantity || 0;
    const basis = +p.average_price || 0;
    const px = (rep.as_of || {}).live || +p.last_price || +p.current_price || (rep.as_of || {}).close;
    const accts = (p.accounts || []).filter(a => +a.quantity > 0);
    if (qty) rows.push(`<div class="rd-stat"><div class="k">Shares</div><div class="v">${qty} ${esc(p.side || 'LONG')}${kbadge('verified')}</div>
      <div class="s">${accts.length > 1 ? accts.map(a => `${esc(a.account)}: ${a.quantity} @ ${money(a.average_price)}`).join(' · ') : esc(p.account || '')}</div></div>`);
    if (basis) rows.push(`<div class="rd-stat"><div class="k">Cost basis</div><div class="v">${money(basis)}</div>
      <div class="s">${px ? 'now ' + money(px) : ''}</div></div>`);
    if (basis && px && qty) {
      const upl = (px - basis) * qty;
      rows.push(`<div class="rd-stat"><div class="k">Unrealized P/L</div><div class="v" style="color:${upl >= 0 ? '#34d399' : '#fb7185'}">${upl >= 0 ? '+' : ''}$${upl.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',')}</div>
        <div class="s">${pct(100 * (px - basis) / basis)} on basis</div></div>`);
    }
    if (p.day_pnl != null && String(p.source || '').toUpperCase() !== 'PORTFOLIO')
      rows.push(`<div class="rd-stat"><div class="k">Day P/L</div><div class="v">${(+p.day_pnl >= 0 ? '+' : '')}$${(+p.day_pnl).toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',')}</div>
      <div class="s">day move only</div></div>`);
    const legs = (p.options || []).filter(o => +o.quantity);
    const legsHtml = legs.length ? `<div class="rd-note" style="margin-top:8px;"><b>Option legs:</b> ${legs.map(o => `${o.quantity > 0 ? '+' : ''}${o.quantity} ${esc(String(o.symbol || '').trim())}`).join('; ')}</div>` : '';
    return card('Your position ' + kbadge('verified'), `<div class="rd-stats">${rows.join('')}</div>${legsHtml}`);
  }

  // ---------- 9. trade plans ---------------------------------------------------
  function levelsTable(rep) {
    const lv = rep.levels || {};
    const order = ['entry_low', 'entry_high', 'trigger', 'stop', 'target_1', 'target_2'];
    const rows = order.filter(k => lv[k]).map(k => `
      <tr><td>${k.replace(/_/g, ' ')}</td><td class="num">${money(lv[k].price)}${kbadge('computed')}</td>
          <td>${esc(lv[k].name || '')}</td><td>${esc(lv[k].source || '')}</td></tr>`).join('');
    return rows ? `<table class="rd-table"><tr><th>plan field</th><th>price</th><th>level</th><th>source</th></tr>${rows}</table>` : '';
  }

  function planCard(rep) {
    const p = rep.plan || {};
    const rejected = rep.audit && rep.audit.checks_passed === false;
    const parts = [];
    if (rejected) parts.push('<div class="rd-rejected">LEVELS REJECTED — not an order.</div>');
    const lt = levelsTable(rep);
    if (lt) parts.push(lt);
    if (p.recommended_shares) parts.push(`<div class="rd-note" style="margin-top:8px;"><b>Shares:</b> ${esc(p.recommended_shares)} at the risk budget${p.entry_max ? ` · entry max ${money(p.entry_max)}` : ''}</div>`);
    const menu = rep.options_menu || {};
    [['Tactical spread (30-50 DTE)', 'tactical_spread'], ['LEAPS (6-12 months)', 'leaps'], ['Income / CSP (20-50 DTE)', 'income_or_csp'],
     ['Covered call (30-60 DTE)', 'covered_call']]
      .map(([t, k]) => ({ t, m: menu[k] || {} }))
      .filter(x => x.m && x.m.structure && x.m.structure !== 'NONE')
      .forEach(x => parts.push(`<div class="rd-tier"><div class="rd-tier-t">${x.t}</div>
        <div class="rd-note"><b>${esc(x.m.structure)}</b> — ${esc(x.m.summary || '')}</div></div>`));
    if ((menu.existing_legs || []).length) parts.push(`<div class="rd-note" style="margin-top:8px;"><b>Your option legs (untouched):</b> ${esc(menu.existing_legs.join('; '))}</div>`);
    (rep.scenarios || []).forEach(s => parts.push(
      `<div class="rd-tier"><div class="rd-tier-t">${esc(s.kind)} scenario — window to ${esc(s.window_end || '')}</div>
       <div class="rd-note">trigger ${money(s.trigger)} · stop ${money(s.stop)} · target ${money(s.target)}${s.thesis ? ` — ${esc(s.thesis)}` : ''}</div></div>`));
    if (!parts.length) return '';
    return card('Trade plans — shares, options tiers, scenarios', parts.join(''));
  }

  // ---------- evidence / risks / sources / audit --------------------------------
  function evidence(rep) {
    const groups = [
      ['Technicals', rep.technicals], ['News', rep.news],
      ['Concurrence', rep.concurrence], ['Conflicts', rep.conflicts],
    ].filter(([, v]) => v && v.length);
    if (!groups.length) return '';
    const cols = groups.map(([t, v]) =>
      `<div><h3 style="margin-bottom:8px;">${t}</h3><ul class="rd-list">${v.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>`).join('');
    return card('Evidence', `<div class="rd-cols">${cols}</div>`);
  }

  function risks(rep) {
    const items = [];
    if (rep.why_now) items.push(`<li><b>Why now:</b> ${esc(rep.why_now)}</li>`);
    if (rep.edge) items.push(`<li><b>The edge:</b> ${esc(rep.edge)}</li>`);
    (rep.risks || []).forEach(t => items.push(`<li>${esc(t)}</li>`));
    (rep.case_against || []).forEach(t => items.push(`<li>${esc(t)}</li>`));
    if (rep.vs_alternatives) items.push(`<li><b>Vs doing nothing or SPY:</b> ${esc(rep.vs_alternatives)}</li>`);
    if (!items.length) return '';
    return card('Thesis, risks & the case against', `<ul class="rd-list">${items.join('')}</ul>`);
  }

  function sources(rep) {
    const src = rep.sources || [];
    if (!src.length) return '';
    const rows = src.map(c =>
      `<div>${c.n}. <a href="${atq(c.url)}" target="_blank" rel="noopener noreferrer">${esc(c.title)}</a>${c.topic ? ` <span class="topic">— ${esc(c.topic)}</span>` : ''} ${kbadge('verified')}</div>`).join('');
    return `<details class="rd-card rd-span rd-drawer"><summary>Sources (${src.length}) — grounded citations · as of ${esc(rep.date || '')}</summary>
      <div class="rd-sources">${rows}</div></details>`;
  }

  function audit(rep) {
    const a = rep.audit || {};
    const fails = (a.fails || []).map(f => `<div class="fail">✗ ${esc(f)}</div>`).join('');
    const wh = (a.withheld || []).length;
    const traces = (a.traces || []).length;
    return `<div class="rd-audit rd-card rd-span">
      checks ${a.checks_passed ? '<b style="color:#34d399">PASSED</b>' : '<b class="fail">FAILED</b>'}
      · independent review ${esc(String(a.adversary || 'n/a').toLowerCase())}
      · ${traces} traced numbers · ${wh} withheld lines
      ${fails}
      ${wh ? `<details><summary>${wh} withheld lines (numbers in no source)</summary>${(a.withheld || []).map(w => `<div>– ${esc(w)}</div>`).join('')}</details>` : ''}
    </div>`;
  }

  // ---------- render ------------------------------------------------------------
  async function fetchBars(ticker) {
    const r = await fetch(`/api/research/${encodeURIComponent(ticker)}/candles?limit=1000`);
    if (!r.ok) return [];
    const d = await r.json();
    const seen = new Set();
    return (d.candles || [])
      .filter(b => b && b.time && b.close && !seen.has(b.time) && seen.add(b.time))
      .sort((a, b) => (a.time < b.time ? -1 : 1))
      .map(b => ({ time: b.time, open: +b.open, high: +b.high, low: +b.low, close: +b.close }));
  }

  async function render(el, ticker, dateStr) {
    if (!el) return;
    el.innerHTML = `<div class="empty-state-box"><div class="empty-state-icon">🧭</div>
      <div class="empty-state-title">Loading decision report…</div></div>`;
    let rep = null, bars = [];
    try {
      const q = dateStr && /^\d{4}-\d{2}-\d{2}$/.test(dateStr) ? `?date=${dateStr}` : '';
      const [r, b] = await Promise.all([
        fetch(`/api/research/${encodeURIComponent(ticker)}/report-json${q}`), fetchBars(ticker)]);
      if (r.ok) rep = await r.json();
      bars = b;
    } catch (e) { /* fall through to empty state */ }
    if (!rep || rep.kind !== 'computed') {
      el.innerHTML = `<div class="empty-state-box"><div class="empty-state-icon">🧭</div>
        <div class="empty-state-title">No Decision Report</div>
        <div class="empty-state-desc">No structured report exists for ${String(ticker).replace(/[^A-Za-z0-9.\-:]/g, '')}. Run deep research to produce one.</div></div>`;
      return;
    }
    el.innerHTML = `<div class="rd-wrap">
      ${hero(rep)}
      <div class="rd-grid">
        ${chartCard(rep, bars)}
        ${scenarioBar(rep)}
        ${horizons(rep)}
        ${catalystTimeline(rep)}
        ${positionCard(rep)}
        ${riskPanel(rep, bars)}
        ${marketContext(rep)}
        ${transmissionCard(rep)}
        ${fundamentals(rep)}
        ${evidence(rep)}
        ${risks(rep)}
        ${planCard(rep)}
        ${sources(rep)}
        ${audit(rep)}
      </div>
    </div>`;
    const chartEl = el.querySelector('#rd-chart');
    let chart = drawChart(chartEl, rep, bars.slice(-126));
    el.querySelectorAll('.rd-range').forEach(btn => btn.addEventListener('click', () => {
      el.querySelectorAll('.rd-range').forEach(x => x.classList.remove('on'));
      btn.classList.add('on');
      if (chart) { try { chart.remove(); } catch (e) {} }
      chart = drawChart(chartEl, rep, bars.slice(-parseInt(btn.dataset.n, 10)));
    }));
  }

  return { render };
})();
