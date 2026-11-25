/* Interactive Dashboard Client for SOL/KRW Hourly AI Paper Trader */

(function () {
  "use strict";

  const state = {
    data: null,
    chartMode: "equity", // "equity" | "price"
    range: "all", // "24" | "168" | "720" | "all"
    briefingFilter: "all", // "all" | "traded" | "hold"
    expandedRunIds: new Set(),
  };

  const $ = (id) => document.getElementById(id);

  function fmtKrw(val, signed = false) {
    if (val === null || val === undefined || Number.isNaN(Number(val))) return "-";
    const n = Math.round(Number(val));
    const prefix = signed && n > 0 ? "+" : "";
    return `${prefix}${n.toLocaleString("ko-KR")}원`;
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
    // "2026-11-25 14:05:12 KST" -> "11-25 14:05"
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
      gemini: "Gemini AI",
      quant_fallback: "정량 폴백",
      stop_loss: "장중 손절",
      take_profit: "장중 익절",
    };
    return map[trigger] || trigger;
  }

  function alignLabel(align) {
    const map = { bullish: "정배열", bearish: "역배열", mixed: "혼조" };
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
    chEl.textContent = `${fmtPct(m.change_24h_pct)} (${fmtKrw(m.change_24h_krw, true)})`;
    chEl.className = `num-display ticker-delta ${signClass(m.change_24h_pct)}`;

    const engineEl = $("engine-badge");
    if (dec.engine === "gemini") {
      engineEl.textContent = `Gemini (${dec.model_name})`;
      engineEl.classList.remove("is-fallback");
    } else {
      engineEl.textContent = "정량 멀티팩터 폴백";
      engineEl.classList.add("is-fallback");
    }

    $("last-updated").textContent = shortKst(data.generated_at_kst);
    $("footer-meta").textContent = `데이터 출처: ${(data.meta.exchange_source || "upbit").toUpperCase()} · 생성 ${data.generated_at_kst}`;

    // Status Banner
    const banner = $("status-banner");
    const messages = [];
    if (data.summary.daily_circuit_breaker_active) {
      messages.push(
        `<strong>일일 손실 서킷브레이커 발동 중:</strong> 금일(KST) 낙폭이 -${data.meta.daily_loss_limit_pct}%를 초과해 신규 매수가 자동 차단되었습니다.`
      );
    }
    if (dec.fallback_reason) {
      messages.push(`<strong>엔진 안내:</strong> ${escapeHtml(dec.fallback_reason)}`);
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
    retEl.className = `num-display kpi-delta ${signClass(s.total_return_pct)}`;
    $("kpi-pnl").textContent = `누적 손익 ${fmtKrw(s.total_pnl_krw, true)}`;

    const alphaEl = $("kpi-alpha");
    alphaEl.textContent = fmtPct(s.alpha_pp, true, "%p");
    alphaEl.className = `num-display kpi-value ${signClass(s.alpha_pp)}`;
    $("kpi-bm-return").textContent = `B&H ${fmtPct(s.benchmark_return_pct)}`;
    $("kpi-bm-equity").textContent = fmtKrw(s.benchmark_equity_krw);

    $("kpi-exposure-pct").textContent = `비중 ${Number(s.exposure_pct).toFixed(1)}%`;
    $("kpi-sol-value").textContent = fmtKrw(s.sol_value_krw);
    $("kpi-exposure-bar").style.width = `${Math.min(100, Math.max(0, Number(s.exposure_pct)))}%`;
    $("kpi-sol-qty").textContent = `${Number(s.sol_qty).toFixed(4)} SOL`;

    const unrealEl = $("kpi-unrealized");
    if (Number(s.sol_qty) > 1e-8) {
      unrealEl.textContent = `평가 ${fmtPct(s.unrealized_pnl_pct)} (${fmtKrw(s.unrealized_pnl_krw, true)})`;
      unrealEl.className = `num-display kpi-secondary ${signClass(s.unrealized_pnl_krw)}`;
    } else {
      unrealEl.textContent = "전액 원화 관망 중";
      unrealEl.className = "num-display kpi-secondary is-neutral";
    }

    $("kpi-trade-count").textContent = `${s.total_trades}건 체결`;
    const mddEl = $("kpi-mdd");
    mddEl.textContent = fmtPct(s.max_drawdown_pct, false);
    mddEl.className = `num-display mini-val ${Number(s.max_drawdown_pct) < 0 ? "is-neg" : ""}`;
    $("kpi-winrate").textContent = s.win_rate_pct !== null ? `${s.win_rate_pct.toFixed(1)}% (${s.winning_trades}/${s.closed_trades})` : "-";
    $("kpi-fees").textContent = fmtKrw(s.cumulative_fees_krw);
    $("kpi-sharpe").textContent = s.sharpe_ratio !== null ? Number(s.sharpe_ratio).toFixed(2) : "데이터 축적 중";
  }

  function renderIndicators(data) {
    const ind = data.indicators;

    const rsiEl = $("ind-rsi");
    rsiEl.textContent = Number(ind.rsi14).toFixed(1);
    let rsiNote = "중립 구간";
    if (ind.rsi14 >= 70) rsiNote = "단기 과매수 경계";
    else if (ind.rsi14 <= 30) rsiNote = "단기 과매도 구간";
    else if (ind.rsi14 > ind.rsi14_prev) rsiNote = `상승 전환 (직전 ${ind.rsi14_prev})`;
    else rsiNote = `완만 둔화 (직전 ${ind.rsi14_prev})`;
    $("ind-rsi-note").textContent = rsiNote;

    $("ind-trend").textContent = `${alignLabel(ind.ema_alignment)} / 4h ${htfLabel(ind.htf_trend)}`;
    $("ind-ema21").textContent = `EMA21 ${fmtKrw(ind.ema21)}`;

    const macdEl = $("ind-macd");
    macdEl.textContent = `${ind.macd_hist >= 0 ? "+" : ""}${Number(ind.macd_hist).toFixed(1)}`;
    macdEl.className = `num-display ind-val ${signClass(ind.macd_hist)}`;
    $("ind-macd-note").textContent = ind.macd_hist_delta >= 0 ? "모멘텀 개선" : "모멘텀 둔화";

    $("ind-bb").textContent = `%B ${Number(ind.bb_percent_b).toFixed(2)}`;
    $("ind-atr").textContent = `ATR ${Number(ind.atr14_pct).toFixed(2)}%`;

    const vwapEl = $("ind-vwap");
    vwapEl.textContent = fmtPct(ind.dist_from_vwap_pct);
    vwapEl.className = `num-display ind-val ${signClass(ind.dist_from_vwap_pct)}`;
    $("ind-vol").textContent = `거래량 ${Number(ind.volume_ratio_24h).toFixed(2)}x`;

    const qEl = $("ind-qscore");
    qEl.textContent = `${ind.quant_score > 0 ? "+" : ""}${Number(ind.quant_score).toFixed(1)}점`;
    qEl.className = `num-display ind-val ${signClass(ind.quant_score)}`;
    $("ind-ob").textContent = `호가 ${ind.orderbook_imbalance >= 0 ? "+" : ""}${Number(ind.orderbook_imbalance).toFixed(2)}`;
  }

  function renderLatestBriefing(data) {
    const b = data.latest_briefing;
    const dec = b.decision;
    const s = data.summary;

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

    const ul = $("briefing-factors");
    ul.innerHTML = (dec.key_factors || [])
      .map((f) => `<li>${escapeHtml(f)}</li>`)
      .join("");

    $("briefing-risk").textContent = dec.risk_notes || "-";
    $("briefing-next").textContent = dec.next_watch || "-";

    $("pos-avg-entry").textContent = Number(s.avg_entry_price_krw) > 0 ? fmtKrw(s.avg_entry_price_krw) : "-";
    $("pos-sl").textContent = s.stop_loss_price_krw ? fmtKrw(s.stop_loss_price_krw) : "-";
    $("pos-tp").textContent = s.take_profit_price_krw ? fmtKrw(s.take_profit_price_krw) : "-";
    $("pos-cash").textContent = fmtKrw(s.cash_krw);
  }

  function getFilteredSeries() {
    const raw = state.data?.series || [];
    if (state.range === "all") return raw;
    const n = Number(state.range);
    return raw.length > n ? raw.slice(-n) : raw;
  }

  function renderChart() {
    const svg = $("main-chart-svg");
    const legendEl = $("chart-legend");
    const tooltip = $("chart-tooltip");
    tooltip.classList.add("hidden");

    const styles = getComputedStyle(document.documentElement);
    const colorStrategy = styles.getPropertyValue("--accent-strategy").trim();
    const colorBenchmark = styles.getPropertyValue("--accent-benchmark").trim();
    const colorPos = styles.getPropertyValue("--pos").trim();
    const colorNeg = styles.getPropertyValue("--neg").trim();
    const colorGrid = styles.getPropertyValue("--border-hairline").trim();
    const colorMuted = styles.getPropertyValue("--ink-muted").trim();
    const colorInk = styles.getPropertyValue("--ink-primary").trim();

    const W = 780;
    const H = 330;
    const pad = { top: 20, right: 18, bottom: 34, left: 76 };
    const plotW = W - pad.left - pad.right;
    const plotH = H - pad.top - pad.bottom;

    if (state.chartMode === "equity") {
      legendEl.innerHTML = `
        <span class="legend-item"><span class="legend-swatch" style="background:${colorStrategy}"></span>AI 단타 전략 평가자산</span>
        <span class="legend-item"><span class="legend-swatch dashed"></span>SOL 단순 보유 (Buy &amp; Hold)</span>
        <span class="legend-item"><span class="legend-dot" style="background:${colorPos}"></span>매수 체결</span>
        <span class="legend-item"><span class="legend-dot" style="background:${colorNeg}"></span>매도 체결</span>
      `;

      const pts = getFilteredSeries();
      if (!pts.length) {
        svg.innerHTML = `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" fill="${colorMuted}" font-size="13">아직 누적된 시뮬레이션 데이터가 없습니다.</text>`;
        return;
      }

      // If only 1 point exists, prepend initial capital anchor so user sees a clean baseline line
      const chartPts =
        pts.length === 1
          ? [
              {
                time_kst: "시뮬레이션 시작",
                equity_krw: state.data.meta.initial_capital_krw,
                benchmark_krw: state.data.meta.initial_capital_krw,
                return_pct: 0,
                benchmark_return_pct: 0,
                spot_price: pts[0].spot_price,
                executed_sides: [],
              },
              pts[0],
            ]
          : pts;

      const vals = chartPts.flatMap((p) => [Number(p.equity_krw), Number(p.benchmark_krw)]);
      let minV = Math.min(...vals);
      let maxV = Math.max(...vals);
      if (Math.abs(maxV - minV) < 10_000) {
        minV -= 100_000;
        maxV += 100_000;
      } else {
        const margin = (maxV - minV) * 0.14;
        minV -= margin;
        maxV += margin;
      }

      const xFor = (i) => pad.left + (i / Math.max(1, chartPts.length - 1)) * plotW;
      const yFor = (v) => pad.top + (1 - (v - minV) / (maxV - minV)) * plotH;

      const eqLine = chartPts.map((p, i) => `${i === 0 ? "M" : "L"}${xFor(i).toFixed(1)},${yFor(p.equity_krw).toFixed(1)}`).join(" ");
      const bmLine = chartPts.map((p, i) => `${i === 0 ? "M" : "L"}${xFor(i).toFixed(1)},${yFor(p.benchmark_krw).toFixed(1)}`).join(" ");
      const areaPath = `${eqLine} L${xFor(chartPts.length - 1).toFixed(1)},${(pad.top + plotH).toFixed(1)} L${pad.left},${(pad.top + plotH).toFixed(1)} Z`;

      // Grid lines (4 horizontal ticks)
      let gridSvg = "";
      for (let t = 0; t <= 4; t++) {
        const v = minV + ((maxV - minV) * t) / 4;
        const y = yFor(v);
        gridSvg += `<line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${W - pad.right}" y2="${y.toFixed(1)}" stroke="${colorGrid}" stroke-width="1" />`;
        gridSvg += `<text x="${pad.left - 8}" y="${(y + 4).toFixed(1)}" text-anchor="end" fill="${colorMuted}" font-family="Paperlogy, IBM Plex Mono, sans-serif" font-weight="500" font-size="10.5">${(v / 10000).toFixed(0)}만</text>`;
      }

      // X-axis labels
      let xAxisSvg = "";
      const xTicks = Math.min(5, chartPts.length);
      for (let k = 0; k < xTicks; k++) {
        const idx = Math.round((k * (chartPts.length - 1)) / Math.max(1, xTicks - 1));
        const label = chartPts[idx].time_kst.includes("시작") ? "시작" : shortKst(chartPts[idx].time_kst);
        xAxisSvg += `<text x="${xFor(idx).toFixed(1)}" y="${H - 10}" text-anchor="middle" fill="${colorMuted}" font-family="Paperlogy, IBM Plex Mono, sans-serif" font-weight="500" font-size="10.5">${escapeHtml(label)}</text>`;
      }

      // Trade Markers
      let markersSvg = "";
      chartPts.forEach((p, i) => {
        const sides = p.executed_sides || [];
        if (sides.includes("BUY")) {
          markersSvg += `<circle cx="${xFor(i).toFixed(1)}" cy="${yFor(p.equity_krw).toFixed(1)}" r="4.5" fill="${colorPos}" stroke="#fff" stroke-width="1.5" />`;
        }
        if (sides.includes("SELL")) {
          markersSvg += `<circle cx="${xFor(i).toFixed(1)}" cy="${yFor(p.equity_krw).toFixed(1)}" r="4.5" fill="${colorNeg}" stroke="#fff" stroke-width="1.5" />`;
        }
      });

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
        <path d="${bmLine}" fill="none" stroke="${colorBenchmark}" stroke-width="2" stroke-dasharray="5 4" />
        <path d="${eqLine}" fill="none" stroke="${colorStrategy}" stroke-width="2.6" stroke-linejoin="round" />
        ${markersSvg}
        <line id="hover-vline" x1="0" y1="${pad.top}" x2="0" y2="${pad.top + plotH}" stroke="${colorInk}" stroke-opacity="0.25" stroke-width="1" stroke-dasharray="3 3" visibility="hidden" />
        <circle id="hover-dot" cx="0" cy="0" r="5" fill="${colorStrategy}" stroke="#fff" stroke-width="2" visibility="hidden" />
        <rect id="hover-capture" x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}" fill="transparent" />
      `;

      bindChartHover(chartPts, xFor, (p) => yFor(p.equity_krw), (p) => `
        <div class="tt-time">${escapeHtml(p.time_kst)}</div>
        <div class="tt-row"><span>전략 자산</span><strong class="num-display">${fmtKrw(p.equity_krw)} (${fmtPct(p.return_pct)})</strong></div>
        <div class="tt-row"><span>단순 보유</span><span class="num-display">${fmtKrw(p.benchmark_krw)} (${fmtPct(p.benchmark_return_pct)})</span></div>
        <div class="tt-row"><span>SOL 시세</span><span class="num-display">${fmtKrw(p.spot_price)}</span></div>
      `);
      return;
    }

    // Price & Signals Mode: Uses recent 72h Upbit 1h candles if series is short, otherwise series prices
    legendEl.innerHTML = `
      <span class="legend-item"><span class="legend-swatch" style="background:${colorStrategy}"></span>SOL/KRW 종가</span>
      <span class="legend-item"><span class="legend-swatch" style="background:${colorBenchmark}"></span>EMA 21 (단기 추세)</span>
      <span class="legend-item"><span class="legend-dot" style="background:${colorPos}"></span>매수 타점</span>
      <span class="legend-item"><span class="legend-dot" style="background:${colorNeg}"></span>매도 타점</span>
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
      ? state.data.candles_recent.map((c) => {
          const key = String(c.time_kst || "").slice(0, 13);
          return {
            time_kst: c.time_kst,
            price: c.close,
            high: c.high,
            low: c.low,
            ema21: null,
            executed_sides: sidesByHour.get(key) || [],
          };
        })
      : seriesPts.map((p) => ({
          time_kst: p.time_kst,
          price: p.spot_price,
          high: p.spot_price,
          low: p.spot_price,
          ema21: p.ema21,
          executed_sides: p.executed_sides || [],
        }));

    if (useCandles && seriesPts.length > 0) {
      const latest = seriesPts[seriesPts.length - 1];
      pricePts.push({
        time_kst: latest.time_kst,
        price: latest.spot_price,
        high: latest.spot_price,
        low: latest.spot_price,
        ema21: latest.ema21,
        executed_sides: latest.executed_sides || [],
      });
    }

    const s = state.data.summary;
    const vals = pricePts.flatMap((p) => [p.price, p.ema21].filter((v) => v !== null && v !== undefined));
    if (s.stop_loss_price_krw) vals.push(Number(s.stop_loss_price_krw));
    if (s.take_profit_price_krw) vals.push(Number(s.take_profit_price_krw));

    let minV = Math.min(...vals);
    let maxV = Math.max(...vals);
    const padSpan = Math.max((maxV - minV) * 0.12, minV * 0.008);
    minV -= padSpan;
    maxV += padSpan;

    const xFor = (i) => pad.left + (i / Math.max(1, pricePts.length - 1)) * plotW;
    const yFor = (v) => pad.top + (1 - (v - minV) / (maxV - minV)) * plotH;

    let gridSvg = "";
    for (let t = 0; t <= 4; t++) {
      const v = minV + ((maxV - minV) * t) / 4;
      const y = yFor(v);
      gridSvg += `<line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${W - pad.right}" y2="${y.toFixed(1)}" stroke="${colorGrid}" stroke-width="1" />`;
      gridSvg += `<text x="${pad.left - 8}" y="${(y + 4).toFixed(1)}" text-anchor="end" fill="${colorMuted}" font-family="Paperlogy, IBM Plex Mono, sans-serif" font-weight="500" font-size="10.5">${Math.round(v).toLocaleString("ko-KR")}</text>`;
    }

    let xAxisSvg = "";
    const xTicks = Math.min(5, pricePts.length);
    for (let k = 0; k < xTicks; k++) {
      const idx = Math.round((k * (pricePts.length - 1)) / Math.max(1, xTicks - 1));
      xAxisSvg += `<text x="${xFor(idx).toFixed(1)}" y="${H - 10}" text-anchor="middle" fill="${colorMuted}" font-family="Paperlogy, IBM Plex Mono, sans-serif" font-weight="500" font-size="10.5">${shortKst(pricePts[idx].time_kst)}</text>`;
    }

    const pricePath = pricePts.map((p, i) => `${i === 0 ? "M" : "L"}${xFor(i).toFixed(1)},${yFor(p.price).toFixed(1)}`).join(" ");
    const priceAreaPath = `${pricePath} L${xFor(pricePts.length - 1).toFixed(1)},${(pad.top + plotH).toFixed(1)} L${pad.left},${(pad.top + plotH).toFixed(1)} Z`;
    const emaPts = pricePts.map((p, i) => (p.ema21 ? `${xFor(i).toFixed(1)},${yFor(p.ema21).toFixed(1)}` : null)).filter(Boolean);
    const emaPath = emaPts.length >= 2 ? `M${emaPts.join(" L")}` : "";

    let guidesSvg = "";
    if (s.stop_loss_price_krw) {
      const ySl = yFor(Number(s.stop_loss_price_krw));
      guidesSvg += `<line x1="${pad.left}" y1="${ySl.toFixed(1)}" x2="${W - pad.right}" y2="${ySl.toFixed(1)}" stroke="${colorNeg}" stroke-width="1.3" stroke-dasharray="4 3" />`;
      guidesSvg += `<text x="${W - pad.right - 4}" y="${(ySl - 4).toFixed(1)}" text-anchor="end" fill="${colorNeg}" font-family="Paperlogy, IBM Plex Mono, sans-serif" font-weight="600" font-size="10.5">SL ${Math.round(s.stop_loss_price_krw).toLocaleString("ko-KR")}</text>`;
    }
    if (s.take_profit_price_krw) {
      const yTp = yFor(Number(s.take_profit_price_krw));
      guidesSvg += `<line x1="${pad.left}" y1="${yTp.toFixed(1)}" x2="${W - pad.right}" y2="${yTp.toFixed(1)}" stroke="${colorPos}" stroke-width="1.3" stroke-dasharray="4 3" />`;
      guidesSvg += `<text x="${W - pad.right - 4}" y="${(yTp - 4).toFixed(1)}" text-anchor="end" fill="${colorPos}" font-family="Paperlogy, IBM Plex Mono, sans-serif" font-weight="600" font-size="10.5">TP ${Math.round(s.take_profit_price_krw).toLocaleString("ko-KR")}</text>`;
    }

    let markersSvg = "";
    pricePts.forEach((p, i) => {
      const sides = p.executed_sides || [];
      if (sides.includes("BUY")) {
        markersSvg += `<circle cx="${xFor(i).toFixed(1)}" cy="${yFor(p.price).toFixed(1)}" r="5" fill="${colorPos}" stroke="#fff" stroke-width="1.5" />`;
      }
      if (sides.includes("SELL")) {
        markersSvg += `<circle cx="${xFor(i).toFixed(1)}" cy="${yFor(p.price).toFixed(1)}" r="5" fill="${colorNeg}" stroke="#fff" stroke-width="1.5" />`;
      }
    });

    svg.innerHTML = `
      <defs>
        <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${colorStrategy}" stop-opacity="0.14" />
          <stop offset="100%" stop-color="${colorStrategy}" stop-opacity="0.01" />
        </linearGradient>
      </defs>
      ${gridSvg}
      ${xAxisSvg}
      ${guidesSvg}
      <path d="${priceAreaPath}" fill="url(#priceFill)" />
      ${emaPath ? `<path d="${emaPath}" fill="none" stroke="${colorBenchmark}" stroke-width="1.8" />` : ""}
      <path d="${pricePath}" fill="none" stroke="${colorStrategy}" stroke-width="2.4" stroke-linejoin="round" />
      ${markersSvg}
      <line id="hover-vline" x1="0" y1="${pad.top}" x2="0" y2="${pad.top + plotH}" stroke="${colorInk}" stroke-opacity="0.25" stroke-width="1" stroke-dasharray="3 3" visibility="hidden" />
      <circle id="hover-dot" cx="0" cy="0" r="5" fill="${colorStrategy}" stroke="#fff" stroke-width="2" visibility="hidden" />
      <rect id="hover-capture" x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}" fill="transparent" />
    `;

    bindChartHover(pricePts, xFor, (p) => yFor(p.price), (p) => `
      <div class="tt-time">${escapeHtml(p.time_kst)}</div>
      <div class="tt-row"><span>SOL 가격</span><strong class="num-display">${fmtKrw(p.price)}</strong></div>
      ${p.ema21 ? `<div class="tt-row"><span>EMA 21</span><span class="num-display">${fmtKrw(p.ema21)}</span></div>` : ""}
      ${p.executed_sides.length ? `<div class="tt-row"><span>체결</span><strong class="num-display">${p.executed_sides.join(", ")}</strong></div>` : ""}
    `);
  }

  function bindChartHover(points, xFor, yForPoint, tooltipHtmlFn) {
    const capture = $("hover-capture");
    const vline = $("hover-vline");
    const dot = $("hover-dot");
    const tooltip = $("chart-tooltip");
    const stage = $("chart-stage");
    if (!capture || !points.length) return;

    const svg = $("main-chart-svg");
    const onMove = (evt) => {
      const rect = svg.getBoundingClientRect();
      const relX = ((evt.clientX - rect.left) / rect.width) * 780;
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
      dot.setAttribute("cx", cx.toFixed(1));
      dot.setAttribute("cy", cy.toFixed(1));
      dot.setAttribute("visibility", "visible");

      tooltip.innerHTML = tooltipHtmlFn(p);
      tooltip.classList.remove("hidden");

      const stageRect = stage.getBoundingClientRect();
      const px = (cx / 780) * stageRect.width;
      const tipW = tooltip.offsetWidth || 200;
      const leftPos = px + 14 + tipW > stageRect.width ? Math.max(8, px - tipW - 14) : px + 14;
      tooltip.style.left = `${leftPos}px`;
      tooltip.style.top = "12px";
    };

    const onLeave = () => {
      vline.setAttribute("visibility", "hidden");
      dot.setAttribute("visibility", "hidden");
      tooltip.classList.add("hidden");
    };

    capture.addEventListener("mousemove", onMove);
    capture.addEventListener("mouseleave", onLeave);
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
        const tradeSummary = (item.executed_trades || [])
          .map((t) => `${t.side} ${Number(t.sol_qty).toFixed(4)} SOL @ ${fmtKrw(t.exec_price)} (${triggerLabel(t.trigger)})`)
          .join(" / ");

        return `
          <article class="feed-item">
            <button type="button" class="feed-summary" data-toggle-run="${item.run_id}" aria-expanded="${isOpen}">
              <span class="num-display feed-time">${escapeHtml(shortKst(item.time_kst))}</span>
              <span class="badge-action ${actLower}">${dec.action}</span>
              <span class="feed-headline">${escapeHtml(dec.headline)}</span>
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
                <span>정량점수: ${item.indicators.quant_score > 0 ? "+" : ""}${Number(item.indicators.quant_score).toFixed(1)}</span>
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
    if (!trades.length) {
      tbody.innerHTML = `<tr><td colspan="10" class="empty-state">아직 체결된 모의 거래가 없습니다. 유리한 진입 신호가 포착되면 자동으로 기록됩니다.</td></tr>`;
      return;
    }

    tbody.innerHTML = trades
      .map((t) => {
        const sideLower = t.side.toLowerCase();
        const pnlText =
          t.realized_pnl_krw !== null && t.realized_pnl_krw !== undefined
            ? `${fmtKrw(t.realized_pnl_krw, true)} (${fmtPct(t.realized_pnl_pct)})`
            : "-";
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
    refreshBtn.disabled = true;
    refreshBtn.textContent = "갱신 중...";
    try {
      const resp = await fetch(`data/dashboard.json?t=${Date.now()}`, { cache: "no-store" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const payload = await resp.json();
      renderAll(payload);
    } catch (err) {
      const banner = $("status-banner");
      banner.innerHTML = `<strong>데이터 로드 대기 중:</strong> 아직 첫 시간별 실행 데이터가 없거나 불러오기에 실패했습니다 (${escapeHtml(err.message)}). GitHub Actions 워크플로우 실행 후 새로고침해 주세요.`;
      banner.classList.remove("hidden");
    } finally {
      refreshBtn.disabled = false;
      refreshBtn.textContent = "새로고침";
    }
  }

  function wireEvents() {
    // Palette Toggle
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

    // Chart Tabs
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

    // Briefing Feed Filters
    document.querySelectorAll("[data-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.briefingFilter = btn.dataset.filter;
        document.querySelectorAll("[data-filter]").forEach((b) => b.classList.toggle("active", b === btn));
        if (state.data) renderBriefingFeed(state.data);
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    wireEvents();
    loadDashboardData();
  });
})();
