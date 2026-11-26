/* Responsive 1:1 Pixel SVG Dashboard Client — SOL/KRW Hourly Quant Paper Trader */

(function () {
  "use strict";

  const state = {
    data: null,
    chartMode: "equity", // "equity" | "price" | "signal"
    range: "all", // "24" | "168" | "720" | "all"
    briefingFilter: "all", // "all" | "traded" | "hold"
    expandedRunIds: new Set(),
    resizeTimer: null,
  };

  const $ = (id) => document.getElementById(id);

  function fmtKrw(val, signed = false) {
    if (val === null || val === undefined || Number.isNaN(Number(val))) return "-";
    const n = Math.round(Number(val));
    const prefix = signed && n > 0 ? "+" : "";
    return `${prefix}${n.toLocaleString("ko-KR")}원`;
  }

  function fmtShortKrw(val) {
    if (val === null || val === undefined || Number.isNaN(Number(val))) return "-";
    const n = Number(val);
    if (Math.abs(n) >= 1_000_000) return `${(n / 10_000).toFixed(0)}만`;
    return Math.round(n).toLocaleString("ko-KR");
  }

  function fmtPct(val, signed = true, suffix = "%") {
    if (val === null || val === undefined || Number.isNaN(Number(val))) return "-";
    const n = Number(val);
    const prefix = signed && n > 0 ? "+" : "";
    return `${prefix}${n.toFixed(2)}${suffix}`;
  }

  function signClass(val) {
    if (val === null || val === undefined) return "is-neutral";
    const n = Number(val);
    if (n > 0.0001) return "is-pos";
    if (n < -0.0001) return "is-neg";
    return "is-neutral";
  }

  function shortKst(kstStr) {
    if (!kstStr) return "-";
    return kstStr.slice(5, 16);
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function triggerLabel(trigger) {
    const map = {
      quant_shortterm: "퀀트 단타 v2",
      quant_fallback: "퀀트 v1",
      stop_loss: "장중 손절",
      take_profit: "장중 익절",
    };
    return map[trigger] || trigger;
  }

  function alignLabel(align) {
    const map = { bullish: "강세", bearish: "약세", mixed: "혼조" };
    return map[align] || align;
  }

  function htfLabel(trend) {
    const map = { uptrend: "상승", downtrend: "하락", sideways: "횡보" };
    return map[trend] || trend;
  }

  function applyPalette(choice) {
    document.documentElement.setAttribute("data-palette", choice);
    try {
      localStorage.setItem("sol_trader_palette", choice);
    } catch (_) {}
    document.querySelectorAll("[data-palette-choice]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.paletteChoice === choice);
    });
    if (state.data) renderChart();
  }

  function renderMasthead(data) {
    const m = data.market;
    const dec = data.latest_briefing.decision;

    $("spot-price").textContent = fmtKrw(m.spot_price);
    const chEl = $("spot-change-24h");
    chEl.textContent = fmtPct(m.change_24h_pct);
    chEl.className = `num-display ticker-delta-pill ${signClass(m.change_24h_pct)}`;
    chEl.title = `24시간 변동 ${fmtKrw(m.change_24h_krw, true)}`;

    $("spot-hl-24h").textContent = `${fmtKrw(m.high_24h)} / ${fmtKrw(m.low_24h)}`;

    const engineEl = $("engine-badge");
    engineEl.textContent = dec.model_name === "quant-shortterm-v2" ? "퀀트 단타 v2" : `퀀트 (${dec.model_name})`;

    $("last-updated").textContent = shortKst(data.generated_at_kst);
    $("footer-meta").textContent = `데이터 출처: ${(data.meta.exchange_source || "upbit").toUpperCase()} · 생성 ${data.generated_at_kst}`;

    const banner = $("status-banner");
    const messages = [];
    if (data.summary.daily_circuit_breaker_active) {
      messages.push(
        `<strong>일일 손실 서킷브레이커 발동 중:</strong> 금일(KST) 낙폭이 -${data.meta.daily_loss_limit_pct}%를 초과해 신규 매수가 자동 차단되었습니다.`
      );
    }
    if (data.latest_briefing.guardrail_note) {
      messages.push(`<strong>가드레일 메모:</strong> ${escapeHtml(data.latest_briefing.guardrail_note)}`);
    }
    if (messages.length > 0) {
      banner.innerHTML = messages.join(" &nbsp;|&nbsp; ");
      banner.classList.remove("hidden");
    } else {
      banner.classList.add("hidden");
    }
  }

  function renderKpis(data) {
    const s = data.summary;

    $("kpi-run-count").textContent = `${s.run_count}회 누적`;
    $("kpi-equity").textContent = fmtKrw(s.total_equity_krw);

    const retEl = $("kpi-return");
    retEl.textContent = fmtPct(s.total_return_pct);
    retEl.className = `num-display kpi-return-pill ${signClass(s.total_return_pct)}`;
    $("kpi-pnl").textContent = `누적 손익 ${fmtKrw(s.total_pnl_krw, true)}`;
    $("kpi-daily-ret").textContent = `금일 ${fmtPct(s.daily_return_pct)}`;

    // Alpha + Comparison Bars
    const alphaEl = $("kpi-alpha");
    alphaEl.textContent = fmtPct(s.alpha_pp, true, "%p");
    alphaEl.className = `num-display kpi-value ${signClass(s.alpha_pp)}`;

    const stratRet = Number(s.total_return_pct || 0);
    const bmRet = Number(s.benchmark_return_pct || 0);
    const maxAbs = Math.max(2.0, Math.abs(stratRet), Math.abs(bmRet));
    const stratW = Math.max(8, Math.min(100, ((stratRet + maxAbs) / (2 * maxAbs)) * 100));
    const bmW = Math.max(8, Math.min(100, ((bmRet + maxAbs) / (2 * maxAbs)) * 100));
    $("cmp-bar-strat").style.width = `${stratW.toFixed(1)}%`;
    $("cmp-bar-bm").style.width = `${bmW.toFixed(1)}%`;
    const cmpStrat = $("cmp-val-strat");
    cmpStrat.textContent = fmtPct(stratRet);
    cmpStrat.className = `num-display compare-val ${signClass(stratRet)}`;
    const cmpBm = $("cmp-val-bm");
    cmpBm.textContent = fmtPct(bmRet);
    cmpBm.className = `num-display compare-val ${signClass(bmRet)}`;

    // Position Card
    $("kpi-exposure-pct").textContent = `비중 ${Number(s.exposure_pct).toFixed(1)}%`;
    $("kpi-sol-value").textContent = fmtKrw(s.sol_value_krw);
    $("kpi-exposure-bar").style.width = `${Math.min(100, Math.max(0, Number(s.exposure_pct)))}%`;
    $("kpi-sol-qty").textContent = `${Number(s.sol_qty).toFixed(4)} SOL`;
    $("kpi-cash-mini").textContent = `현금 ${fmtShortKrw(s.cash_krw)}원`;

    const unrealEl = $("kpi-unrealized");
    if (Number(s.sol_qty) > 1e-8) {
      unrealEl.textContent = `미실현 ${fmtPct(s.unrealized_pnl_pct)} (${fmtKrw(s.unrealized_pnl_krw, true)})`;
      unrealEl.className = `num-display kpi-secondary ${signClass(s.unrealized_pnl_krw)}`;
    } else {
      unrealEl.textContent = "전액 원화 관망 중";
      unrealEl.className = "num-display kpi-secondary is-neutral";
    }

    // Risk & Cost Scorecard
    $("kpi-trade-count").textContent = `매수 ${s.buy_trades} · 매도 ${s.sell_trades}`;
    const mddEl = $("kpi-mdd");
    mddEl.textContent = fmtPct(s.max_drawdown_pct, false);
    mddEl.className = `num-display mini-val ${Number(s.max_drawdown_pct) < 0 ? "is-neg" : ""}`;
    $("kpi-winrate").textContent =
      s.win_rate_pct !== null ? `${s.win_rate_pct.toFixed(1)}% (${s.winning_trades}/${s.closed_trades})` : "-";

    const feesVal = Number(s.cumulative_fees_krw || 0);
    const slipVal = Number(s.cumulative_slippage_krw || 0);
    const totalCost = s.total_trading_cost_krw !== undefined ? Number(s.total_trading_cost_krw) : feesVal + slipVal;
    $("kpi-fees").textContent = fmtKrw(totalCost);
    $("kpi-cost-split").textContent = `수수료 ${Math.round(feesVal).toLocaleString("ko-KR")} · 슬리피지 ${Math.round(slipVal).toLocaleString("ko-KR")}`;

    const realEl = $("kpi-realized");
    realEl.textContent = fmtKrw(s.realized_pnl_krw, true);
    realEl.className = `num-display mini-val ${signClass(s.realized_pnl_krw)}`;

    // Avoid misleading annualized Sharpe when sample size is < 48 hours
    if (Number(s.run_count) >= 48 && s.sharpe_ratio !== null) {
      $("kpi-sharpe").textContent = `연환산 샤프 ${Number(s.sharpe_ratio).toFixed(2)}`;
    } else {
      $("kpi-sharpe").textContent = `샤프 ${s.run_count}/48h 축적 중`;
    }
  }

  function renderIndicators(data) {
    const ind = data.indicators;

    // 1. Quant Score + Bidirectional Gauge
    const qScore = Number(ind.quant_score || 0);
    const qEl = $("ind-qscore");
    qEl.textContent = `${qScore > 0 ? "+" : ""}${qScore.toFixed(1)}점`;
    qEl.className = `num-display ind-val ${signClass(qScore)}`;
    const qFill = $("gauge-qscore");
    const qPct = Math.min(50, (Math.abs(qScore) / 100) * 50);
    if (qScore >= 0) {
      qFill.style.left = "50%";
      qFill.style.width = `${qPct}%`;
      qFill.style.background = "var(--pos)";
    } else {
      qFill.style.left = `${50 - qPct}%`;
      qFill.style.width = `${qPct}%`;
      qFill.style.background = "var(--neg)";
    }
    $("ind-ob").textContent = `호가 불균형 ${ind.orderbook_imbalance >= 0 ? "+" : ""}${Number(ind.orderbook_imbalance).toFixed(2)}`;

    // 2. RSI + Pin Gauge
    const rsi = Number(ind.rsi14 || 50);
    $("ind-rsi").textContent = rsi.toFixed(1);
    $("gauge-rsi").style.left = `${Math.max(2, Math.min(98, rsi))}%`;
    let rsiNote = "중립 구간";
    if (rsi >= 70) rsiNote = "단기 과열 경계";
    else if (rsi <= 35) rsiNote = "단기 과매도 구간";
    else if (rsi > ind.rsi14_prev) rsiNote = `상승 전환 (직전 ${ind.rsi14_prev})`;
    else rsiNote = `완만 둔화 (직전 ${ind.rsi14_prev})`;
    $("ind-rsi-note").textContent = rsiNote;

    // 3. Fast Trend
    const fastAlign = ind.fast_alignment || ind.ema_alignment;
    $("ind-trend").textContent = `${alignLabel(fastAlign)} / 4h ${htfLabel(ind.htf_trend)}`;
    $("ind-ema13").textContent = ind.ema13 ? `EMA13 ${fmtKrw(ind.ema13)}` : `EMA21 ${fmtKrw(ind.ema21)}`;
    $("ind-ema5").textContent = ind.ema5 ? `EMA5 ${fmtKrw(ind.ema5)}` : `EMA9 ${fmtKrw(ind.ema9)}`;

    // 4. Fast MACD
    const macdEl = $("ind-macd");
    macdEl.textContent = `${ind.macd_hist >= 0 ? "+" : ""}${Number(ind.macd_hist).toFixed(1)}`;
    macdEl.className = `num-display ind-val ${signClass(ind.macd_hist)}`;
    $("ind-macd-note").textContent = ind.macd_hist_delta >= 0 ? "히스토그램 개선" : "히스토그램 둔화";
    $("ind-macd-line").textContent = `Line ${Number(ind.macd_line).toFixed(0)} / Sig ${Number(ind.macd_signal).toFixed(0)}`;

    // 5. 12h VWAP
    const vwapDist = ind.dist_from_vwap12_pct !== undefined ? ind.dist_from_vwap12_pct : ind.dist_from_vwap_pct;
    const vwapPrice = ind.vwap_12h !== undefined ? ind.vwap_12h : ind.vwap_24h;
    const vwapEl = $("ind-vwap");
    vwapEl.textContent = fmtPct(vwapDist);
    vwapEl.className = `num-display ind-val ${signClass(vwapDist)}`;
    $("ind-vwap-price").textContent = `기준가 ${fmtKrw(vwapPrice)}`;
    $("ind-vol").textContent = `거래량 평균 대비 ${Number(ind.volume_ratio_24h).toFixed(2)}x`;

    // 6. BB & ATR & Spread
    $("ind-bb").textContent = `%B ${Number(ind.bb_percent_b).toFixed(2)}`;
    $("ind-atr").textContent = `1h ATR ${Number(ind.atr14_pct).toFixed(2)}%`;
    $("ind-spread").textContent = `호가 스프레드 ${Number(ind.spread_bps || data.market.spread_bps || 0).toFixed(1)} bp`;
  }

  function renderLatestBriefing(data) {
    const b = data.latest_briefing;
    const dec = b.decision;
    const s = data.summary;
    const spot = Number(data.market.spot_price);

    $("briefing-time").textContent = b.time_kst;
    $("briefing-regime").textContent = b.indicators.regime_kr || "-";

    const stamp = $("decision-action");
    const actionLower = dec.action.toLowerCase();
    const actionKorean = { BUY: "BUY · 매수", SELL: "SELL · 매도", HOLD: "HOLD · 관망" }[dec.action] || dec.action;
    stamp.textContent = actionKorean;
    stamp.className = `decision-stamp ${actionLower}`;

    $("decision-order-pct").textContent =
      dec.action === "HOLD" ? "포지션 유지" : `주문 비중 ${Number(dec.order_pct).toFixed(0)}%`;

    $("decision-confidence").textContent = `${dec.confidence}%`;
    $("decision-confidence-bar").style.width = `${Math.min(100, Math.max(0, dec.confidence))}%`;

    $("briefing-headline").textContent = dec.headline;
    $("briefing-summary").textContent = dec.briefing_summary;

    $("briefing-factors").innerHTML = (dec.key_factors || [])
      .map((f) => `<li>${escapeHtml(f)}</li>`)
      .join("");

    $("briefing-risk").textContent = dec.risk_notes || "-";
    $("briefing-next").textContent = dec.next_watch || "-";

    // Position Guardrails + Visual SL/Entry/TP Bracket
    const hasPos = Number(s.sol_qty) > 1e-8 && s.stop_loss_price_krw && s.take_profit_price_krw;
    $("pos-avg-entry").textContent = Number(s.avg_entry_price_krw) > 0 ? fmtKrw(s.avg_entry_price_krw) : "-";
    $("pos-sl").textContent = s.stop_loss_price_krw ? fmtKrw(s.stop_loss_price_krw) : "-";
    $("pos-tp").textContent = s.take_profit_price_krw ? fmtKrw(s.take_profit_price_krw) : "-";
    $("pos-cash").textContent = fmtKrw(s.cash_krw);

    const barWrap = $("bracket-bar-wrap");
    if (hasPos) {
      const sl = Number(s.stop_loss_price_krw);
      const tp = Number(s.take_profit_price_krw);
      const entry = Number(s.avg_entry_price_krw);
      const span = Math.max(1, tp - sl);
      const entryPct = Math.max(4, Math.min(96, ((entry - sl) / span) * 100));
      const spotPct = Math.max(2, Math.min(98, ((spot - sl) / span) * 100));
      $("bracket-entry-mark").style.left = `${entryPct.toFixed(1)}%`;
      $("bracket-spot-dot").style.left = `${spotPct.toFixed(1)}%`;
      $("bracket-sl-lbl").textContent = `SL ${Math.round(sl).toLocaleString("ko-KR")}`;
      $("bracket-entry-lbl").textContent = `평단 ${Math.round(entry).toLocaleString("ko-KR")}`;
      $("bracket-tp-lbl").textContent = `TP ${Math.round(tp).toLocaleString("ko-KR")}`;
      const toTpPct = ((tp / spot) - 1) * 100;
      const toSlPct = ((sl / spot) - 1) * 100;
      $("bracket-status").textContent = `손절 ${toSlPct.toFixed(1)}% · 익절 +${toTpPct.toFixed(1)}%`;
      barWrap.classList.remove("hidden");
    } else {
      $("bracket-status").textContent = "보유 포지션 없음";
      barWrap.classList.add("hidden");
    }
  }

  function getFilteredSeries() {
    const raw = state.data?.series || [];
    if (state.range === "all") return raw;
    const n = Number(state.range);
    return raw.length > n ? raw.slice(-n) : raw;
  }

  function buildMarkerSvg(cx, cy, side, colorPos, colorNeg) {
    // Crisp geometric equilateral triangle markers (1:1 aspect ratio, zero distortion)
    if (side === "BUY") {
      const pts = `${cx.toFixed(1)},${(cy - 7).toFixed(1)} ${(cx - 6).toFixed(1)},${(cy + 4).toFixed(1)} ${(cx + 6).toFixed(1)},${(cy + 4).toFixed(1)}`;
      return `<polygon points="${pts}" fill="${colorPos}" stroke="#ffffff" stroke-width="1.5" stroke-linejoin="round" />`;
    }
    const pts = `${cx.toFixed(1)},${(cy + 7).toFixed(1)} ${(cx - 6).toFixed(1)},${(cy - 4).toFixed(1)} ${(cx + 6).toFixed(1)},${(cy - 4).toFixed(1)}`;
    return `<polygon points="${pts}" fill="${colorNeg}" stroke="#ffffff" stroke-width="1.5" stroke-linejoin="round" />`;
  }

  function buildAxisPill(x, y, text, bgColor, svgW, minY, maxY, textColor = "#ffffff") {
    const w = Math.max(54, text.length * 6.6 + 12);
    const px = Math.min(svgW - w - 2, x);
    const py = Math.max(minY, Math.min(maxY, y));
    return `
      <g>
        <rect x="${px.toFixed(1)}" y="${(py - 10).toFixed(1)}" width="${w.toFixed(1)}" height="20" rx="5" fill="${bgColor}" />
        <text x="${(px + w / 2).toFixed(1)}" y="${(py + 3.5).toFixed(1)}" text-anchor="middle" fill="${textColor}" font-family="Paperlogy, sans-serif" font-weight="700" font-size="10.5">${escapeHtml(text)}</text>
      </g>
    `;
  }

  function renderChart() {
    const svg = $("main-chart-svg");
    const stage = $("chart-stage");
    const legendEl = $("chart-legend");
    const tooltip = $("chart-tooltip");
    tooltip.classList.add("hidden");

    // Measure REAL CSS pixel dimensions for 1:1 undistorted typography & circles
    const rect = stage.getBoundingClientRect();
    const W = Math.max(300, Math.round(rect.width || 760));
    const H = Math.max(240, Math.round(rect.height || 350));
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.removeAttribute("preserveAspectRatio");

    const isNarrow = W < 520;
    const pad = { top: 22, right: isNarrow ? 78 : 88, bottom: 32, left: 16 };
    const plotW = Math.max(100, W - pad.left - pad.right);
    const plotH = Math.max(100, H - pad.top - pad.bottom);
    const pillMinY = pad.top + 10;
    const pillMaxY = pad.top + plotH - 10;

    const styles = getComputedStyle(document.documentElement);
    const colorStrategy = styles.getPropertyValue("--accent-strategy").trim();
    const colorMint = styles.getPropertyValue("--accent-mint").trim();
    const colorBenchmark = styles.getPropertyValue("--accent-benchmark").trim();
    const colorPos = styles.getPropertyValue("--pos").trim();
    const colorNeg = styles.getPropertyValue("--neg").trim();
    const colorGrid = styles.getPropertyValue("--border-hairline").trim();
    const colorMuted = styles.getPropertyValue("--ink-muted").trim();
    const colorInk = styles.getPropertyValue("--ink-primary").trim();

    const fontStack = "Paperlogy, -apple-system, sans-serif";
    const xTickCount = isNarrow ? 3 : 5;

    // -------------------------------------------------------------------------
    // MODE 1: Equity Curve vs Buy & Hold
    // -------------------------------------------------------------------------
    if (state.chartMode === "equity") {
      legendEl.innerHTML = `
        <span class="legend-item"><span class="legend-swatch" style="background:${colorStrategy}"></span>퀀트 단타 평가자산</span>
        <span class="legend-item"><span class="legend-swatch dashed"></span>SOL 단순 보유 (B&amp;H)</span>
        <span class="legend-item"><span class="legend-tri up"></span>매수 체결</span>
        <span class="legend-item"><span class="legend-tri down"></span>매도 체결</span>
      `;

      const pts = getFilteredSeries();
      if (!pts.length) {
        svg.innerHTML = `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" fill="${colorMuted}" font-family="${fontStack}" font-size="13">누적 데이터가 아직 없습니다.</text>`;
        return;
      }

      const initCap = Number(state.data.meta.initial_capital_krw || 10_000_000);
      const chartPts =
        pts.length === 1
          ? [
              {
                time_kst: "시뮬레이션 시작",
                equity_krw: initCap,
                benchmark_krw: initCap,
                return_pct: 0,
                benchmark_return_pct: 0,
                spot_price: pts[0].spot_price,
                executed_sides: [],
              },
              pts[0],
            ]
          : pts;

      const vals = chartPts.flatMap((p) => [Number(p.equity_krw), Number(p.benchmark_krw), initCap]);
      let minV = Math.min(...vals);
      let maxV = Math.max(...vals);
      const rawSpan = maxV - minV;
      const margin = rawSpan < 50_000 ? 100_000 : rawSpan * 0.16;
      minV -= margin;
      maxV += margin;

      const xFor = (i) => pad.left + (i / Math.max(1, chartPts.length - 1)) * plotW;
      const yFor = (v) => pad.top + (1 - (v - minV) / (maxV - minV)) * plotH;

      const eqLine = chartPts.map((p, i) => `${i === 0 ? "M" : "L"}${xFor(i).toFixed(1)},${yFor(p.equity_krw).toFixed(1)}`).join(" ");
      const bmLine = chartPts.map((p, i) => `${i === 0 ? "M" : "L"}${xFor(i).toFixed(1)},${yFor(p.benchmark_krw).toFixed(1)}`).join(" ");
      const areaPath = `${eqLine} L${xFor(chartPts.length - 1).toFixed(1)},${(pad.top + plotH).toFixed(1)} L${pad.left},${(pad.top + plotH).toFixed(1)} Z`;

      let gridSvg = "";
      for (let t = 0; t <= 4; t++) {
        const v = minV + ((maxV - minV) * t) / 4;
        const y = yFor(v);
        gridSvg += `<line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${W - pad.right}" y2="${y.toFixed(1)}" stroke="${colorGrid}" stroke-width="1" />`;
        gridSvg += `<text x="${(W - pad.right + 8).toFixed(1)}" y="${(y + 3.5).toFixed(1)}" text-anchor="start" fill="${colorMuted}" font-family="${fontStack}" font-weight="500" font-size="11">${(v / 10_000).toFixed(0)}만</text>`;
      }

      // Initial 1,000만 baseline
      const yInit = yFor(initCap);
      gridSvg += `<line x1="${pad.left}" y1="${yInit.toFixed(1)}" x2="${W - pad.right}" y2="${yInit.toFixed(1)}" stroke="${colorMuted}" stroke-opacity="0.45" stroke-width="1" stroke-dasharray="2 3" />`;

      let xAxisSvg = "";
      const ticks = Math.min(xTickCount, chartPts.length);
      for (let k = 0; k < ticks; k++) {
        const idx = Math.round((k * (chartPts.length - 1)) / Math.max(1, ticks - 1));
        const label = chartPts[idx].time_kst.includes("시작") ? "시작" : shortKst(chartPts[idx].time_kst);
        const anchor = k === 0 ? "start" : k === ticks - 1 ? "end" : "middle";
        xAxisSvg += `<text x="${xFor(idx).toFixed(1)}" y="${H - 10}" text-anchor="${anchor}" fill="${colorMuted}" font-family="${fontStack}" font-weight="500" font-size="11">${escapeHtml(label)}</text>`;
      }

      let markersSvg = "";
      chartPts.forEach((p, i) => {
        const sides = p.executed_sides || [];
        if (sides.includes("BUY")) {
          markersSvg += buildMarkerSvg(xFor(i), yFor(p.equity_krw), "BUY", colorPos, colorNeg);
        }
        if (sides.includes("SELL")) {
          markersSvg += buildMarkerSvg(xFor(i), yFor(p.equity_krw), "SELL", colorPos, colorNeg);
        }
      });

      const lastPt = chartPts[chartPts.length - 1];
      const endPill = buildAxisPill(
        W - pad.right + 4,
        yFor(lastPt.equity_krw),
        `${(lastPt.equity_krw / 10_000).toFixed(0)}만`,
        colorStrategy,
        W,
        pillMinY,
        pillMaxY
      );

      svg.innerHTML = `
        <defs>
          <linearGradient id="eqFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="${colorStrategy}" stop-opacity="0.18" />
            <stop offset="100%" stop-color="${colorStrategy}" stop-opacity="0.01" />
          </linearGradient>
        </defs>
        ${gridSvg}
        ${xAxisSvg}
        <path d="${areaPath}" fill="url(#eqFill)" />
        <path d="${bmLine}" fill="none" stroke="${colorBenchmark}" stroke-width="2" stroke-dasharray="5 4" stroke-linejoin="round" />
        <path d="${eqLine}" fill="none" stroke="${colorStrategy}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />
        ${markersSvg}
        ${endPill}
        <line id="hover-vline" x1="0" y1="${pad.top}" x2="0" y2="${pad.top + plotH}" stroke="${colorInk}" stroke-opacity="0.28" stroke-width="1" stroke-dasharray="3 3" visibility="hidden" />
        <line id="hover-hline" x1="${pad.left}" y1="0" x2="${W - pad.right}" y2="0" stroke="${colorInk}" stroke-opacity="0.18" stroke-width="1" stroke-dasharray="3 3" visibility="hidden" />
        <circle id="hover-dot" cx="0" cy="0" r="5" fill="${colorStrategy}" stroke="#ffffff" stroke-width="2.2" visibility="hidden" />
        <rect id="hover-capture" x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}" fill="transparent" />
      `;

      bindChartHover(W, chartPts, xFor, (p) => yFor(p.equity_krw), (p) => `
        <div class="tt-time">${escapeHtml(p.time_kst)}</div>
        <div class="tt-row"><span>전략 자산</span><strong class="num-display">${fmtKrw(p.equity_krw)} (${fmtPct(p.return_pct)})</strong></div>
        <div class="tt-row"><span>단순 보유</span><span class="num-display">${fmtKrw(p.benchmark_krw)} (${fmtPct(p.benchmark_return_pct)})</span></div>
        <div class="tt-row"><span>SOL 시세</span><span class="num-display">${fmtKrw(p.spot_price)}</span></div>
      `);
      return;
    }

    // -------------------------------------------------------------------------
    // MODE 2: SOL/KRW Price, EMA & Trade Entry/Exit Markers + SL/TP Guides
    // -------------------------------------------------------------------------
    if (state.chartMode === "price") {
      legendEl.innerHTML = `
        <span class="legend-item"><span class="legend-swatch" style="background:${colorStrategy}"></span>SOL/KRW 가격</span>
        <span class="legend-item"><span class="legend-swatch" style="background:${colorBenchmark}"></span>EMA 추세선</span>
        <span class="legend-item"><span class="legend-tri up"></span>매수 타점</span>
        <span class="legend-item"><span class="legend-tri down"></span>매도 타점</span>
      `;

      const seriesPts = getFilteredSeries();
      const useCandles = seriesPts.length < 6 && (state.data.candles_recent || []).length >= 2;
      const sidesByHour = new Map();
      seriesPts.forEach((p) => {
        if ((p.executed_sides || []).length) {
          const key = String(p.time_kst || "").slice(0, 13);
          const existing = sidesByHour.get(key) || [];
          sidesByHour.set(key, Array.from(new Set([...existing, ...p.executed_sides])));
        }
      });

      const pricePts = useCandles
        ? state.data.candles_recent.map((c) => ({
            time_kst: c.time_kst,
            price: c.close,
            ema: null,
            executed_sides: sidesByHour.get(String(c.time_kst || "").slice(0, 13)) || [],
          }))
        : seriesPts.map((p) => ({
            time_kst: p.time_kst,
            price: p.spot_price,
            ema: p.ema21,
            executed_sides: p.executed_sides || [],
          }));

      if (useCandles && seriesPts.length > 0) {
        const latest = seriesPts[seriesPts.length - 1];
        pricePts.push({
          time_kst: latest.time_kst,
          price: latest.spot_price,
          ema: latest.ema21,
          executed_sides: latest.executed_sides || [],
        });
      }

      const s = state.data.summary;
      const vals = pricePts.flatMap((p) => [p.price, p.ema].filter((v) => v !== null && v !== undefined));
      if (s.stop_loss_price_krw) vals.push(Number(s.stop_loss_price_krw));
      if (s.take_profit_price_krw) vals.push(Number(s.take_profit_price_krw));

      let minV = Math.min(...vals);
      let maxV = Math.max(...vals);
      const padSpan = Math.max((maxV - minV) * 0.14, minV * 0.008);
      minV -= padSpan;
      maxV += padSpan;

      const xFor = (i) => pad.left + (i / Math.max(1, pricePts.length - 1)) * plotW;
      const yFor = (v) => pad.top + (1 - (v - minV) / (maxV - minV)) * plotH;

      let gridSvg = "";
      for (let t = 0; t <= 4; t++) {
        const v = minV + ((maxV - minV) * t) / 4;
        const y = yFor(v);
        gridSvg += `<line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${W - pad.right}" y2="${y.toFixed(1)}" stroke="${colorGrid}" stroke-width="1" />`;
        gridSvg += `<text x="${(W - pad.right + 8).toFixed(1)}" y="${(y + 3.5).toFixed(1)}" text-anchor="start" fill="${colorMuted}" font-family="${fontStack}" font-weight="500" font-size="11">${Math.round(v).toLocaleString("ko-KR")}</text>`;
      }

      let xAxisSvg = "";
      const ticks = Math.min(xTickCount, pricePts.length);
      for (let k = 0; k < ticks; k++) {
        const idx = Math.round((k * (pricePts.length - 1)) / Math.max(1, ticks - 1));
        const anchor = k === 0 ? "start" : k === ticks - 1 ? "end" : "middle";
        xAxisSvg += `<text x="${xFor(idx).toFixed(1)}" y="${H - 10}" text-anchor="${anchor}" fill="${colorMuted}" font-family="${fontStack}" font-weight="500" font-size="11">${shortKst(pricePts[idx].time_kst)}</text>`;
      }

      const pricePath = pricePts.map((p, i) => `${i === 0 ? "M" : "L"}${xFor(i).toFixed(1)},${yFor(p.price).toFixed(1)}`).join(" ");
      const priceAreaPath = `${pricePath} L${xFor(pricePts.length - 1).toFixed(1)},${(pad.top + plotH).toFixed(1)} L${pad.left},${(pad.top + plotH).toFixed(1)} Z`;
      const emaPts = pricePts.map((p, i) => (p.ema ? `${xFor(i).toFixed(1)},${yFor(p.ema).toFixed(1)}` : null)).filter(Boolean);
      const emaPath = emaPts.length >= 2 ? `M${emaPts.join(" L")}` : "";

      let guidesSvg = "";
      const axisPills = [];
      if (s.take_profit_price_krw) {
        const yTp = yFor(Number(s.take_profit_price_krw));
        guidesSvg += `<line x1="${pad.left}" y1="${yTp.toFixed(1)}" x2="${W - pad.right}" y2="${yTp.toFixed(1)}" stroke="${colorPos}" stroke-width="1.4" stroke-dasharray="4 3" />`;
        axisPills.push({ y: yTp, text: `TP ${Math.round(s.take_profit_price_krw).toLocaleString("ko-KR")}`, color: colorPos });
      }
      const lastP = pricePts[pricePts.length - 1];
      axisPills.push({ y: yFor(lastP.price), text: Math.round(lastP.price).toLocaleString("ko-KR"), color: colorStrategy });
      if (s.stop_loss_price_krw) {
        const ySl = yFor(Number(s.stop_loss_price_krw));
        guidesSvg += `<line x1="${pad.left}" y1="${ySl.toFixed(1)}" x2="${W - pad.right}" y2="${ySl.toFixed(1)}" stroke="${colorNeg}" stroke-width="1.4" stroke-dasharray="4 3" />`;
        axisPills.push({ y: ySl, text: `SL ${Math.round(s.stop_loss_price_krw).toLocaleString("ko-KR")}`, color: colorNeg });
      }
      // Sort top-to-bottom and enforce minimum 22px spacing so right-axis pills never collide
      axisPills.sort((a, b) => a.y - b.y);
      for (let i = 1; i < axisPills.length; i++) {
        if (axisPills[i].y - axisPills[i - 1].y < 22) {
          axisPills[i].y = axisPills[i - 1].y + 22;
        }
      }
      const pillsSvg = axisPills
        .map((p) => buildAxisPill(W - pad.right + 4, p.y, p.text, p.color, W, pillMinY, pillMaxY))
        .join("");

      let markersSvg = "";
      pricePts.forEach((p, i) => {
        const sides = p.executed_sides || [];
        if (sides.includes("BUY")) markersSvg += buildMarkerSvg(xFor(i), yFor(p.price), "BUY", colorPos, colorNeg);
        if (sides.includes("SELL")) markersSvg += buildMarkerSvg(xFor(i), yFor(p.price), "SELL", colorPos, colorNeg);
      });

      svg.innerHTML = `
        <defs>
          <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="${colorStrategy}" stop-opacity="0.16" />
            <stop offset="100%" stop-color="${colorStrategy}" stop-opacity="0.01" />
          </linearGradient>
        </defs>
        ${gridSvg}
        ${xAxisSvg}
        <path d="${priceAreaPath}" fill="url(#priceFill)" />
        ${emaPath ? `<path d="${emaPath}" fill="none" stroke="${colorBenchmark}" stroke-width="1.8" stroke-linejoin="round" />` : ""}
        <path d="${pricePath}" fill="none" stroke="${colorStrategy}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round" />
        ${guidesSvg}
        ${markersSvg}
        ${pillsSvg}
        <line id="hover-vline" x1="0" y1="${pad.top}" x2="0" y2="${pad.top + plotH}" stroke="${colorInk}" stroke-opacity="0.28" stroke-width="1" stroke-dasharray="3 3" visibility="hidden" />
        <line id="hover-hline" x1="${pad.left}" y1="0" x2="${W - pad.right}" y2="0" stroke="${colorInk}" stroke-opacity="0.18" stroke-width="1" stroke-dasharray="3 3" visibility="hidden" />
        <circle id="hover-dot" cx="0" cy="0" r="5" fill="${colorStrategy}" stroke="#ffffff" stroke-width="2.2" visibility="hidden" />
        <rect id="hover-capture" x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}" fill="transparent" />
      `;

      bindChartHover(W, pricePts, xFor, (p) => yFor(p.price), (p) => `
        <div class="tt-time">${escapeHtml(p.time_kst)}</div>
        <div class="tt-row"><span>SOL 시세</span><strong class="num-display">${fmtKrw(p.price)}</strong></div>
        ${p.ema ? `<div class="tt-row"><span>EMA 추세</span><span class="num-display">${fmtKrw(p.ema)}</span></div>` : ""}
        ${p.executed_sides.length ? `<div class="tt-row"><span>체결 내역</span><strong class="num-display">${p.executed_sides.join(", ")}</strong></div>` : ""}
      `);
      return;
    }

    // -------------------------------------------------------------------------
    // MODE 3: Quant Score (-100..+100) & Buy/Sell Threshold Bands + RSI(14)
    // -------------------------------------------------------------------------
    legendEl.innerHTML = `
      <span class="legend-item"><span class="legend-swatch" style="background:${colorStrategy}"></span>단기 퀀트 점수 (-100 ~ +100)</span>
      <span class="legend-item"><span class="legend-swatch" style="background:${colorMint}"></span>RSI (14)</span>
      <span class="legend-item"><span class="legend-tri up"></span>매수 임계 (+24)</span>
      <span class="legend-item"><span class="legend-tri down"></span>매도 임계 (-22)</span>
    `;

    // Build chronological signal series from briefings (which carry exact quant_score + rsi14)
    const rawBriefings = [...(state.data.briefings || [])].reverse();
    const nRange = state.range === "all" ? rawBriefings.length : Number(state.range);
    const sigPts = rawBriefings.slice(-nRange).map((b) => ({
      time_kst: b.time_kst,
      score: Number(b.indicators?.quant_score ?? 0),
      rsi: Number(b.indicators?.rsi14 ?? 50),
      action: b.decision?.action || "HOLD",
      executed_sides: b.executed_sides || [],
    }));

    if (sigPts.length < 2) {
      svg.innerHTML = `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" fill="${colorMuted}" font-family="${fontStack}" font-size="13">시그널 추이는 2회 이상 누적 시 표시됩니다.</text>`;
      return;
    }

    const xFor = (i) => pad.left + (i / Math.max(1, sigPts.length - 1)) * plotW;
    const yScore = (v) => pad.top + (1 - (v + 100) / 200) * plotH;
    // Map RSI 0..100 onto -100..+100 visual plane (50 -> 0 center line)
    const yRsi = (r) => yScore((r - 50) * 2);

    const buyZoneY = yScore(24);
    const sellZoneY = yScore(-22);
    const zeroY = yScore(0);

    let gridSvg = `
      <rect x="${pad.left}" y="${pad.top}" width="${plotW}" height="${Math.max(0, buyZoneY - pad.top).toFixed(1)}" fill="${colorPos}" fill-opacity="0.06" />
      <rect x="${pad.left}" y="${sellZoneY.toFixed(1)}" width="${plotW}" height="${Math.max(0, pad.top + plotH - sellZoneY).toFixed(1)}" fill="${colorNeg}" fill-opacity="0.06" />
    `;
    [-100, -50, 0, 50, 100].forEach((val) => {
      const y = yScore(val);
      gridSvg += `<line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${W - pad.right}" y2="${y.toFixed(1)}" stroke="${colorGrid}" stroke-width="${val === 0 ? 1.2 : 1}" />`;
      gridSvg += `<text x="${(W - pad.right + 8).toFixed(1)}" y="${(y + 3.5).toFixed(1)}" text-anchor="start" fill="${colorMuted}" font-family="${fontStack}" font-weight="500" font-size="11">${val > 0 ? `+${val}` : val}</text>`;
    });

    gridSvg += `<line x1="${pad.left}" y1="${buyZoneY.toFixed(1)}" x2="${W - pad.right}" y2="${buyZoneY.toFixed(1)}" stroke="${colorPos}" stroke-opacity="0.55" stroke-width="1.2" stroke-dasharray="4 3" />`;
    gridSvg += `<line x1="${pad.left}" y1="${sellZoneY.toFixed(1)}" x2="${W - pad.right}" y2="${sellZoneY.toFixed(1)}" stroke="${colorNeg}" stroke-opacity="0.55" stroke-width="1.2" stroke-dasharray="4 3" />`;

    let xAxisSvg = "";
    const ticks = Math.min(xTickCount, sigPts.length);
    for (let k = 0; k < ticks; k++) {
      const idx = Math.round((k * (sigPts.length - 1)) / Math.max(1, ticks - 1));
      const anchor = k === 0 ? "start" : k === ticks - 1 ? "end" : "middle";
      xAxisSvg += `<text x="${xFor(idx).toFixed(1)}" y="${H - 10}" text-anchor="${anchor}" fill="${colorMuted}" font-family="${fontStack}" font-weight="500" font-size="11">${shortKst(sigPts[idx].time_kst)}</text>`;
    }

    const scorePath = sigPts.map((p, i) => `${i === 0 ? "M" : "L"}${xFor(i).toFixed(1)},${yScore(p.score).toFixed(1)}`).join(" ");
    const rsiPath = sigPts.map((p, i) => `${i === 0 ? "M" : "L"}${xFor(i).toFixed(1)},${yRsi(p.rsi).toFixed(1)}`).join(" ");

    let markersSvg = "";
    sigPts.forEach((p, i) => {
      if (p.executed_sides.includes("BUY")) markersSvg += buildMarkerSvg(xFor(i), yScore(p.score), "BUY", colorPos, colorNeg);
      if (p.executed_sides.includes("SELL")) markersSvg += buildMarkerSvg(xFor(i), yScore(p.score), "SELL", colorPos, colorNeg);
    });

    const lastS = sigPts[sigPts.length - 1];
    const scorePill = buildAxisPill(
      W - pad.right + 4,
      yScore(lastS.score),
      `${lastS.score > 0 ? "+" : ""}${lastS.score.toFixed(0)}점`,
      colorStrategy,
      W,
      pillMinY,
      pillMaxY
    );

    svg.innerHTML = `
      ${gridSvg}
      ${xAxisSvg}
      <line x1="${pad.left}" y1="${zeroY.toFixed(1)}" x2="${W - pad.right}" y2="${zeroY.toFixed(1)}" stroke="${colorMuted}" stroke-opacity="0.45" stroke-width="1" />
      <path d="${rsiPath}" fill="none" stroke="${colorMint}" stroke-width="1.8" stroke-dasharray="4 3" stroke-linejoin="round" />
      <path d="${scorePath}" fill="none" stroke="${colorStrategy}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />
      ${markersSvg}
      ${scorePill}
      <line id="hover-vline" x1="0" y1="${pad.top}" x2="0" y2="${pad.top + plotH}" stroke="${colorInk}" stroke-opacity="0.28" stroke-width="1" stroke-dasharray="3 3" visibility="hidden" />
      <line id="hover-hline" x1="${pad.left}" y1="0" x2="${W - pad.right}" y2="0" stroke="${colorInk}" stroke-opacity="0.18" stroke-width="1" stroke-dasharray="3 3" visibility="hidden" />
      <circle id="hover-dot" cx="0" cy="0" r="5" fill="${colorStrategy}" stroke="#ffffff" stroke-width="2.2" visibility="hidden" />
      <rect id="hover-capture" x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}" fill="transparent" />
    `;

    bindChartHover(W, sigPts, xFor, (p) => yScore(p.score), (p) => `
      <div class="tt-time">${escapeHtml(p.time_kst)}</div>
      <div class="tt-row"><span>퀀트 점수</span><strong class="num-display">${p.score > 0 ? "+" : ""}${p.score.toFixed(1)}점</strong></div>
      <div class="tt-row"><span>RSI (14)</span><span class="num-display">${p.rsi.toFixed(1)}</span></div>
      <div class="tt-row"><span>결정</span><strong class="num-display">${p.action}${p.executed_sides.length ? ` (${p.executed_sides.join("/")})` : ""}</strong></div>
    `);
  }

  function bindChartHover(svgW, points, xFor, yForPoint, tooltipHtmlFn) {
    const capture = $("hover-capture");
    const vline = $("hover-vline");
    const hline = $("hover-hline");
    const dot = $("hover-dot");
    const tooltip = $("chart-tooltip");
    const stage = $("chart-stage");
    if (!capture || !points.length) return;

    const svg = $("main-chart-svg");
    const onMove = (evt) => {
      const rect = svg.getBoundingClientRect();
      const relX = ((evt.clientX - rect.left) / rect.width) * svgW;
      let bestIdx = 0;
      let bestDist = Infinity;
      for (let i = 0; i < points.length; i++) {
        const d = Math.abs(xFor(i) - relX);
        if (d < bestDist) {
          bestDist = d;
          bestIdx = i;
        }
      }
      const p = points[bestIdx];
      const cx = xFor(bestIdx);
      const cy = yForPoint(p);

      vline.setAttribute("x1", cx.toFixed(1));
      vline.setAttribute("x2", cx.toFixed(1));
      vline.setAttribute("visibility", "visible");
      if (hline) {
        hline.setAttribute("y1", cy.toFixed(1));
        hline.setAttribute("y2", cy.toFixed(1));
        hline.setAttribute("visibility", "visible");
      }
      dot.setAttribute("cx", cx.toFixed(1));
      dot.setAttribute("cy", cy.toFixed(1));
      dot.setAttribute("visibility", "visible");

      tooltip.innerHTML = tooltipHtmlFn(p);
      tooltip.classList.remove("hidden");

      const stageRect = stage.getBoundingClientRect();
      const px = (cx / svgW) * stageRect.width;
      const tipW = tooltip.offsetWidth || 205;
      const leftPos = px + 16 + tipW > stageRect.width - 70 ? Math.max(10, px - tipW - 16) : px + 16;
      tooltip.style.left = `${leftPos}px`;
      tooltip.style.top = "12px";
    };

    const onLeave = () => {
      vline.setAttribute("visibility", "hidden");
      if (hline) hline.setAttribute("visibility", "hidden");
      dot.setAttribute("visibility", "hidden");
      tooltip.classList.add("hidden");
    };

    capture.addEventListener("pointermove", onMove);
    capture.addEventListener("pointerleave", onLeave);
  }

  function renderBriefingFeed(data) {
    const container = $("briefing-feed");
    const all = data.briefings || [];
    const filtered = all.filter((item) => {
      if (state.briefingFilter === "traded") return (item.executed_sides || []).length > 0;
      if (state.briefingFilter === "hold") return (item.executed_sides || []).length === 0;
      return true;
    });

    if (!filtered.length) {
      container.innerHTML = `<div class="empty-state">조건에 해당하는 브리핑 이력이 아직 없습니다.</div>`;
      return;
    }

    container.innerHTML = filtered
      .map((item) => {
        const dec = item.decision;
        const actLower = dec.action.toLowerCase();
        const isOpen = state.expandedRunIds.has(item.run_id);
        const factorsHtml = (dec.key_factors || []).map((f) => `<li>${escapeHtml(f)}</li>`).join("");
        const qScore = Number(item.indicators?.quant_score ?? 0);
        const tradeSummary = (item.executed_trades || [])
          .map((t) => `${t.side} ${Number(t.sol_qty).toFixed(4)} SOL @ ${fmtKrw(t.exec_price)} (${triggerLabel(t.trigger)})`)
          .join(" / ");

        return `
          <article class="feed-item">
            <button type="button" class="feed-summary" data-toggle-run="${item.run_id}" aria-expanded="${isOpen}">
              <span class="num-display feed-time">${escapeHtml(shortKst(item.time_kst))}</span>
              <span class="badge-action ${actLower}">${dec.action}</span>
              <span class="feed-headline">${escapeHtml(dec.headline)}</span>
              <span class="num-display feed-score ${signClass(qScore)}">${qScore > 0 ? "+" : ""}${qScore.toFixed(1)}점</span>
              <span class="num-display feed-price">${fmtKrw(item.spot_price)}</span>
              <span class="num-display feed-ret ${signClass(item.return_pct)}">${fmtPct(item.return_pct)}</span>
            </button>
            <div class="feed-details ${isOpen ? "" : "hidden"}">
              <p>${escapeHtml(dec.briefing_summary)}</p>
              <ul class="factor-list">${factorsHtml}</ul>
              ${tradeSummary ? `<p style="margin-top:8px"><strong>체결 기록:</strong> <span class="num-display">${escapeHtml(tradeSummary)}</span></p>` : ""}
              ${item.guardrail_note ? `<p><strong>가드레일:</strong> ${escapeHtml(item.guardrail_note)}</p>` : ""}
              <div class="feed-detail-grid num-display">
                <span>엔진: ${escapeHtml(dec.model_name)}</span>
                <span>신뢰도: ${dec.confidence}%</span>
                <span>RSI: ${Number(item.indicators.rsi14).toFixed(1)}</span>
                <span>평가자산: ${fmtKrw(item.equity_krw)}</span>
                <span>비중: ${Number(item.exposure_pct).toFixed(1)}%</span>
              </div>
            </div>
          </article>
        `;
      })
      .join("");

    container.querySelectorAll("[data-toggle-run]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = Number(btn.dataset.toggleRun);
        if (state.expandedRunIds.has(id)) state.expandedRunIds.delete(id);
        else state.expandedRunIds.add(id);
        renderBriefingFeed(state.data);
      });
    });
  }

  function renderTradesTable(data) {
    const tbody = $("trades-tbody");
    const trades = data.trades || [];
    $("tab-trades-count").textContent = String(trades.length);

    if (!trades.length) {
      tbody.innerHTML = `<tr><td colspan="11" class="empty-state">아직 체결된 모의 거래가 없습니다. 유리한 진입 신호가 포착되면 자동으로 기록됩니다.</td></tr>`;
      return;
    }

    tbody.innerHTML = trades
      .map((t) => {
        const sideLower = t.side.toLowerCase();
        const pnlText =
          t.realized_pnl_krw !== null && t.realized_pnl_krw !== undefined
            ? `${fmtKrw(t.realized_pnl_krw, true)} (${fmtPct(t.realized_pnl_pct)})`
            : "-";
        const slipBps = t.slippage_bps !== undefined && t.slippage_bps !== null ? `${Number(t.slippage_bps).toFixed(1)}bp` : "-";
        const slipKrw = t.slippage_krw !== undefined && t.slippage_krw !== null ? ` (${fmtKrw(t.slippage_krw)})` : "";
        const bd = t.slippage_breakdown;
        const slipTitle = bd ? `호가 ${bd.book_bps}bp + 변동성 ${bd.vol_bps}bp + 이벤트 ${bd.event_bps}bp` : "";

        return `
          <tr>
            <td class="mono">${escapeHtml(shortKst(t.time_kst))}</td>
            <td><span class="badge-action ${sideLower}">${t.side}</span></td>
            <td>${escapeHtml(triggerLabel(t.trigger))}</td>
            <td class="num">${fmtKrw(t.spot_price)}</td>
            <td class="num">${fmtKrw(t.exec_price)}</td>
            <td class="num">${Number(t.sol_qty).toFixed(4)}</td>
            <td class="num">${fmtKrw(t.krw_amount)}</td>
            <td class="num">${fmtKrw(t.fee_krw)}</td>
            <td class="num" title="${escapeHtml(slipTitle)}">${slipBps}${slipKrw}</td>
            <td class="num ${t.realized_pnl_krw !== null ? signClass(t.realized_pnl_krw) : ""}">${pnlText}</td>
            <td class="reason-cell">${escapeHtml(t.reason)}</td>
          </tr>
        `;
      })
      .join("");
  }

  function renderAll(data) {
    state.data = data;
    renderMasthead(data);
    renderKpis(data);
    renderIndicators(data);
    renderLatestBriefing(data);
    renderChart();
    renderBriefingFeed(data);
    renderTradesTable(data);
  }

  async function loadDashboardData() {
    const refreshBtn = $("refresh-btn");
    const refreshLabel = $("refresh-label");
    refreshBtn.disabled = true;
    refreshLabel.textContent = "갱신 중...";
    try {
      const resp = await fetch(`data/dashboard.json?t=${Date.now()}`, { cache: "no-store" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const payload = await resp.json();
      renderAll(payload);
    } catch (err) {
      const banner = $("status-banner");
      banner.innerHTML = `<strong>데이터 로드 대기 중:</strong> 시뮬레이션 데이터를 불러오지 못했습니다 (${escapeHtml(err.message)}). 잠시 후 새로고침해 주세요.`;
      banner.classList.remove("hidden");
    } finally {
      refreshBtn.disabled = false;
      refreshLabel.textContent = "새로고침";
    }
  }

  function wireEvents() {
    const savedPalette = (() => {
      try {
        return localStorage.getItem("sol_trader_palette") || "kr";
      } catch (_) {
        return "kr";
      }
    })();
    applyPalette(savedPalette);

    document.querySelectorAll("[data-palette-choice]").forEach((btn) => {
      btn.addEventListener("click", () => applyPalette(btn.dataset.paletteChoice));
    });

    $("refresh-btn").addEventListener("click", loadDashboardData);

    // Chart Mode Tabs
    document.querySelectorAll("[data-chart-mode]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.chartMode = btn.dataset.chartMode;
        document.querySelectorAll("[data-chart-mode]").forEach((b) => {
          const active = b === btn;
          b.classList.toggle("active", active);
          b.setAttribute("aria-selected", String(active));
        });
        if (state.data) renderChart();
      });
    });

    // Range Selector
    document.querySelectorAll("[data-range]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.range = btn.dataset.range;
        document.querySelectorAll("[data-range]").forEach((b) => b.classList.toggle("active", b === btn));
        if (state.data) renderChart();
      });
    });

    // Ledger Tabs
    document.querySelectorAll("[data-ledger-tab]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = btn.dataset.ledgerTab;
        document.querySelectorAll("[data-ledger-tab]").forEach((b) => {
          const active = b === btn;
          b.classList.toggle("active", active);
          b.setAttribute("aria-selected", String(active));
        });
        ["briefings", "trades", "rules"].forEach((name) => {
          $(`panel-${name}`).classList.toggle("hidden", name !== target);
        });
        $("briefing-filters").classList.toggle("hidden", target !== "briefings");
      });
    });

    // Briefing Filters
    document.querySelectorAll("[data-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.briefingFilter = btn.dataset.filter;
        document.querySelectorAll("[data-filter]").forEach((b) => b.classList.toggle("active", b === btn));
        if (state.data) renderBriefingFeed(state.data);
      });
    });

    // 1:1 Pixel SVG ResizeObserver — guarantees zero text/shape stretching on any screen width
    const stage = $("chart-stage");
    if (typeof ResizeObserver !== "undefined" && stage) {
      const ro = new ResizeObserver(() => {
        if (!state.data) return;
        clearTimeout(state.resizeTimer);
        state.resizeTimer = setTimeout(() => renderChart(), 50);
      });
      ro.observe(stage);
    } else {
      window.addEventListener("resize", () => {
        if (state.data) renderChart();
      });
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    wireEvents();
    loadDashboardData();
  });
})();
