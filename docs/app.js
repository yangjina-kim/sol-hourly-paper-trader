/* Multi-Asset Responsive 1:1 Pixel SVG Dashboard Client — SOL · SK Hynix · NVIDIA */

(function () {
  "use strict";

  const state = {
    rawPayload: null,
    accounts: {},
    assetOrder: [],
    selectedAsset: "sol",
    activeView: "overview", // "overview" | "indicators" | "history" | "rules"
    briefSubTab: "factors", // "factors" | "notes"
    chartMode: "price", // "price" | "equity" | "signal"
    range: "all", // "24" | "72" | "168" | "all"
    overlays: { ema: true, vwap: true, bb: true, sltp: true },
    briefingFilter: "all",
    briefingVisibleCount: 10,
    expandedRunIds: new Set(),
    resizeTimer: null,
  };

  const $ = (id) => document.getElementById(id);

  function currentAccount() {
    return state.accounts[state.selectedAsset] || Object.values(state.accounts)[0] || null;
  }

  function isUsd(acc) {
    return (acc?.meta?.quote || "KRW") === "USD";
  }

  function fmtMoney(val, acc, signed = false) {
    if (val === null || val === undefined || Number.isNaN(Number(val))) return "-";
    const n = Number(val);
    if (isUsd(acc)) {
      const sign = n < 0 ? "-" : signed && n > 0 ? "+" : "";
      return `${sign}$${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }
    const r = Math.round(n);
    const prefix = signed && r > 0 ? "+" : "";
    return `${prefix}${r.toLocaleString("ko-KR")}원`;
  }

  function fmtAxisMoney(val, acc, compact = false) {
    if (val === null || val === undefined || Number.isNaN(Number(val))) return "-";
    const n = Number(val);
    if (isUsd(acc)) {
      if (compact && Math.abs(n) >= 1000) return `$${(n / 1000).toFixed(2)}k`;
      return `$${n.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}`;
    }
    if (compact && Math.abs(n) >= 1_000_000) return `${(n / 10_000).toFixed(0)}만`;
    return Math.round(n).toLocaleString("ko-KR");
  }

  function fmtQty(val, acc) {
    if (val === null || val === undefined || Number.isNaN(Number(val))) return "-";
    const meta = acc?.meta || {};
    const dec = meta.qty_decimals !== undefined ? Number(meta.qty_decimals) : 4;
    const n = Number(val);
    const unit = meta.market_type === "crypto" ? (meta.short_name || "SOL") : "주";
    return `${n.toLocaleString("ko-KR", { minimumFractionDigits: dec, maximumFractionDigits: dec })} ${unit}`;
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
    if (currentAccount()) {
      renderScoreboard();
      renderChart();
    }
  }

  // Normalize both schema v2 (multi-account) and legacy v1 (single SOL) payloads cleanly
  function normalizePayload(raw) {
    if (raw && raw.schema_version === 2 && raw.accounts) {
      return {
        generated_at_kst: raw.generated_at_kst,
        assetOrder: raw.asset_order || Object.keys(raw.accounts),
        defaultAsset: raw.default_asset || "sol",
        accounts: raw.accounts,
      };
    }
    // Legacy v1 single-SOL payload fallback
    const solMeta = {
      ...(raw.meta || {}),
      asset_id: "sol",
      name: "솔라나",
      short_name: "SOL",
      market_type: "crypto",
      market_label: "가상화폐 · Upbit",
      quote: "KRW",
      currency_symbol: "원",
      price_decimals: 0,
      qty_decimals: 4,
      market_open: true,
      market_status_label: "24시간 운영 중",
    };
    return {
      generated_at_kst: raw.generated_at_kst,
      assetOrder: ["sol"],
      defaultAsset: "sol",
      accounts: {
        sol: { ...raw, meta: solMeta },
      },
    };
  }

  // =========================================================================
  // 1. TOP HERO SCOREBOARD: Prominent Scores + Unified Account Selector
  // =========================================================================
  function selectAsset(target, scrollCard = false) {
    if (!target || target === state.selectedAsset) return;
    state.selectedAsset = target;
    state.expandedRunIds.clear();
    state.briefingVisibleCount = 10;
    try {
      localStorage.setItem("quant_trader_selected_asset", target);
    } catch (_) {}
    renderSelectedAccount();
    if (scrollCard) {
      const card = document.querySelector(`.score-card[data-select-asset="${target}"]`);
      if (card && window.innerWidth <= 760) {
        card.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
      }
    }
  }

  function renderScoreboard() {
    const grid = $("asset-scoreboard");
    const mobilePills = $("mobile-asset-pills");
    if (!grid) return;

    if (mobilePills) {
      mobilePills.innerHTML = state.assetOrder
        .map((aid) => {
          const acc = state.accounts[aid];
          if (!acc) return "";
          const meta = acc.meta || {};
          const score = Number(acc.indicators?.quant_score ?? 0);
          const isActive = aid === state.selectedAsset;
          return `
            <button type="button" role="tab" aria-selected="${isActive}" class="m-asset-pill ${isActive ? "active" : ""}" data-select-asset="${aid}" data-scroll-card="1">
              <span class="m-pill-name">${escapeHtml(meta.name || aid)}</span>
              <span class="m-pill-score num-display ${isActive ? "" : signClass(score)}" style="${isActive ? (score > 0 ? "color:var(--hero-pos)" : score < 0 ? "color:var(--hero-neg)" : "color:#fff") : ""}">${score > 0 ? "+" : ""}${score.toFixed(1)}</span>
            </button>
          `;
        })
        .join("");
    }

    grid.innerHTML = state.assetOrder
      .map((aid) => {
        const acc = state.accounts[aid];
        if (!acc) return "";
        const meta = acc.meta || {};
        const m = acc.market || {};
        const s = acc.summary || {};
        const ind = acc.indicators || {};
        const dec = acc.latest_briefing?.decision || { action: "HOLD", confidence: 50 };

        const score = Number(ind.quant_score ?? 0);
        const isActive = aid === state.selectedAsset;
        const isOpen = m.market_open !== false;
        const openTxt = meta.market_type === "crypto" ? "24H" : isOpen ? "개장" : "장마감";
        const typeShort = meta.market_type === "crypto" ? "코인" : meta.market_type === "kr_stock" ? "국내주식" : "미국주식";

        const actionLower = String(dec.action || "HOLD").toLowerCase();
        const actionTxt = { BUY: "BUY · 매수", SELL: "SELL · 매도", HOLD: "HOLD · 관망" }[dec.action] || dec.action;

        const barPct = Math.min(50, (Math.abs(score) / 100) * 50);
        const barLeft = score >= 0 ? 50 : 50 - barPct;
        const barColor = score >= 24 ? "var(--pos)" : score <= -22 ? "var(--neg)" : "var(--accent-strategy)";

        return `
          <button type="button" role="tab" aria-selected="${isActive}" class="score-card ${isActive ? "active" : ""}" data-select-asset="${aid}">
            <div class="score-card-top">
              <div class="score-asset-name">
                <strong>${escapeHtml(meta.name || aid)}</strong>
                <span class="score-type-chip">${typeShort}</span>
              </div>
              <span class="market-pill ${isOpen ? "is-open" : ""}">${openTxt}</span>
            </div>

            <div class="score-big-row">
              <div class="num-display score-big-value ${signClass(score)}">
                ${score > 0 ? "+" : ""}${score.toFixed(1)}<span class="score-big-unit">점</span>
              </div>
              <div class="score-action-col">
                <span class="score-decision-stamp ${actionLower}">${actionTxt}</span>
                <span class="score-conf-txt num-display">신뢰도 ${dec.confidence ?? 50}% · RSI ${Number(ind.rsi14 ?? 50).toFixed(1)}</span>
              </div>
            </div>

            <div class="score-meter" aria-hidden="true">
              <div class="score-meter-tick sell" title="매도 임계 -22"></div>
              <div class="score-meter-center"></div>
              <div class="score-meter-tick buy" title="매수 임계 +24"></div>
              <div class="score-meter-fill" style="left:${barLeft.toFixed(1)}%; width:${barPct.toFixed(1)}%; background:${barColor}"></div>
            </div>

            <div class="score-card-footer">
              <div class="score-price-box num-display">
                <strong>${fmtMoney(m.spot_price, acc)}</strong>
                <span class="${signClass(m.change_24h_pct)}">${fmtPct(m.change_24h_pct)}</span>
              </div>
              <div class="score-acct-ret num-display ${signClass(s.total_return_pct)}">
                계좌 ${fmtPct(s.total_return_pct)}
              </div>
            </div>
          </button>
        `;
      })
      .join("");

    document.querySelectorAll("[data-select-asset]").forEach((btn) => {
      btn.addEventListener("click", () => {
        selectAsset(btn.dataset.selectAsset, btn.dataset.scrollCard === "1");
      });
    });
  }

  function renderSelectedHeader(acc) {
    const meta = acc.meta || {};
    const m = acc.market || {};
    const dec = acc.latest_briefing?.decision || {};

    $("engine-badge").textContent = dec.model_name === "quant-shortterm-v2" ? "퀀트 단타 v2" : `퀀트 (${dec.model_name || "v2"})`;
    $("last-updated").textContent = shortKst(state.rawPayload?.generated_at_kst || acc.generated_at_kst);

    $("spot-hl-24h").textContent = `고/저 ${fmtMoney(m.high_24h, acc)} / ${fmtMoney(m.low_24h, acc)}`;

    const pill = $("market-status-pill");
    const isOpen = m.market_open !== false;
    pill.textContent = m.market_status_label || meta.market_status_label || (isOpen ? "운영 중" : "장 마감");
    pill.className = `market-pill ${isOpen ? "is-open" : ""}`;

    $("footer-meta").textContent = `${(meta.exchange_source || "upbit").toUpperCase()} (${meta.market}) · ${shortKst(acc.generated_at_kst)}`;

    const banner = $("status-banner");
    const messages = [];
    if (acc.summary?.daily_circuit_breaker_active) {
      messages.push(
        `<strong>일일 손실 서킷브레이커 발동 중:</strong> 금일 낙폭이 -${meta.daily_loss_limit_pct || 5}%를 초과해 신규 매수가 자동 차단되었습니다.`
      );
    }
    if (acc.latest_briefing?.guardrail_note) {
      messages.push(`<strong>가드레일 메모:</strong> ${escapeHtml(acc.latest_briefing.guardrail_note)}`);
    }
    if (messages.length > 0) {
      banner.innerHTML = messages.join(" &nbsp;|&nbsp; ");
      banner.classList.remove("hidden");
    } else {
      banner.classList.add("hidden");
    }
  }

  function renderKpis(acc) {
    const s = acc.summary;
    const meta = acc.meta || {};

    $("kpi-equity-label").textContent = `${meta.name} 계좌 평가자산`;
    $("kpi-run-count").textContent = `${s.run_count}회 누적`;
    $("kpi-equity").textContent = fmtMoney(s.total_equity_krw, acc);

    const retEl = $("kpi-return");
    retEl.textContent = fmtPct(s.total_return_pct);
    retEl.className = `num-display kpi-return-pill ${signClass(s.total_return_pct)}`;
    $("kpi-pnl").textContent = `누적 손익 ${fmtMoney(s.total_pnl_krw, acc, true)}`;
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
    $("kpi-pos-label").textContent = `${meta.short_name} 보유 포지션`;
    $("kpi-exposure-pct").textContent = `비중 ${Number(s.exposure_pct).toFixed(1)}%`;
    $("kpi-sol-value").textContent = fmtMoney(s.sol_value_krw, acc);
    $("kpi-exposure-bar").style.width = `${Math.min(100, Math.max(0, Number(s.exposure_pct)))}%`;
    $("kpi-sol-qty").textContent = fmtQty(s.sol_qty, acc);
    $("kpi-cash-mini").textContent = `현금 ${fmtAxisMoney(s.cash_krw, acc, true)}${isUsd(acc) ? "" : "원"}`;

    const unrealEl = $("kpi-unrealized");
    if (Number(s.sol_qty) > 1e-8) {
      unrealEl.textContent = `미실현 ${fmtPct(s.unrealized_pnl_pct)} (${fmtMoney(s.unrealized_pnl_krw, acc, true)})`;
      unrealEl.className = `num-display kpi-secondary ${signClass(s.unrealized_pnl_krw)}`;
    } else {
      unrealEl.textContent = "전액 현금 관망 중";
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
    $("kpi-fees").textContent = fmtMoney(totalCost, acc);
    $("kpi-cost-split").textContent = `수수료 ${fmtMoney(feesVal, acc)} · 슬리피지 ${fmtMoney(slipVal, acc)}`;

    const realEl = $("kpi-realized");
    realEl.textContent = fmtMoney(s.realized_pnl_krw, acc, true);
    realEl.className = `num-display mini-val ${signClass(s.realized_pnl_krw)}`;

    if (Number(s.run_count) >= 48 && s.sharpe_ratio !== null) {
      $("kpi-sharpe").textContent = `연환산 샤프 ${Number(s.sharpe_ratio).toFixed(2)}`;
    } else {
      $("kpi-sharpe").textContent = `샤프 ${s.run_count}/48회 축적 중`;
    }
  }

  function renderIndicators(acc) {
    const ind = acc.indicators;
    const meta = acc.meta || {};

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
    $("ind-ob").textContent = `스프레드 ${Number(ind.spread_bps || acc.market.spread_bps || 0).toFixed(1)} bp`;

    const rsi = Number(ind.rsi14 || 50);
    $("ind-rsi").textContent = rsi.toFixed(1);
    $("gauge-rsi").style.left = `${Math.max(2, Math.min(98, rsi))}%`;
    let rsiNote = "중립 구간";
    if (rsi >= 70) rsiNote = "단기 과열 경계";
    else if (rsi <= 35) rsiNote = "단기 과매도 구간";
    else if (rsi > ind.rsi14_prev) rsiNote = `상승 전환 (직전 ${ind.rsi14_prev})`;
    else rsiNote = `완만 둔화 (직전 ${ind.rsi14_prev})`;
    $("ind-rsi-note").textContent = rsiNote;

    const fastAlign = ind.fast_alignment || ind.ema_alignment;
    $("ind-trend").textContent = `${alignLabel(fastAlign)} / 4h ${htfLabel(ind.htf_trend)}`;
    $("ind-ema13").textContent = ind.ema13 ? `EMA13 ${fmtMoney(ind.ema13, acc)}` : `EMA21 ${fmtMoney(ind.ema21, acc)}`;
    $("ind-ema5").textContent = ind.ema5 ? `EMA5 ${fmtMoney(ind.ema5, acc)}` : `EMA9 ${fmtMoney(ind.ema9, acc)}`;

    const macdEl = $("ind-macd");
    macdEl.textContent = `${ind.macd_hist >= 0 ? "+" : ""}${Number(ind.macd_hist).toFixed(2)}`;
    macdEl.className = `num-display ind-val ${signClass(ind.macd_hist)}`;
    $("ind-macd-note").textContent = ind.macd_hist_delta >= 0 ? "히스토그램 개선" : "히스토그램 둔화";
    $("ind-macd-line").textContent = `Line ${Number(ind.macd_line).toFixed(1)} / Sig ${Number(ind.macd_signal).toFixed(1)}`;

    const vwapDist = ind.dist_from_vwap12_pct !== undefined ? ind.dist_from_vwap12_pct : ind.dist_from_vwap_pct;
    const vwapPrice = ind.vwap_12h !== undefined ? ind.vwap_12h : ind.vwap_24h;
    const vwapEl = $("ind-vwap");
    vwapEl.textContent = fmtPct(vwapDist);
    vwapEl.className = `num-display ind-val ${signClass(vwapDist)}`;
    $("ind-vwap-price").textContent = `기준가 ${fmtMoney(vwapPrice, acc)}`;
    $("ind-vol").textContent = `거래량 평균 대비 ${Number(ind.volume_ratio_24h).toFixed(2)}x`;

    $("ind-bb").textContent = `%B ${Number(ind.bb_percent_b).toFixed(2)}`;
    $("ind-atr").textContent = `1h ATR ${Number(ind.atr14_pct).toFixed(2)}%`;
    $("ind-candles-count").textContent = `누적 1h 봉 ${(meta.candles_accumulated || acc.candles_recent?.length || 0).toLocaleString("ko-KR")}개`;
    const costSplitSub = $("ind-cost-split-sub");
    if (costSplitSub) {
      costSplitSub.textContent = `거래량 ${Number(ind.volume_ratio_24h).toFixed(2)}x`;
    }

    const quickBar = $("quick-ind-items");
    if (quickBar) {
      quickBar.innerHTML = `
        <span>RSI <strong>${rsi.toFixed(1)}</strong></span>
        <span>추세 <strong>${alignLabel(fastAlign)}</strong></span>
        <span>VWAP <strong class="${signClass(vwapDist)}">${fmtPct(vwapDist)}</strong></span>
        <span>ATR <strong>${Number(ind.atr14_pct).toFixed(2)}%</strong></span>
      `;
    }
  }

  function renderLatestBriefing(acc) {
    const b = acc.latest_briefing;
    const dec = b.decision;
    const s = acc.summary;
    const meta = acc.meta || {};
    const spot = Number(acc.market.spot_price);

    $("briefing-panel-title").textContent = `${meta.name} 최신 브리핑`;
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

    const hasPos = Number(s.sol_qty) > 1e-8 && s.stop_loss_price_krw && s.take_profit_price_krw;
    $("pos-avg-entry").textContent = Number(s.avg_entry_price_krw) > 0 ? fmtMoney(s.avg_entry_price_krw, acc) : "-";
    $("pos-sl").textContent = s.stop_loss_price_krw ? fmtMoney(s.stop_loss_price_krw, acc) : "-";
    $("pos-tp").textContent = s.take_profit_price_krw ? fmtMoney(s.take_profit_price_krw, acc) : "-";
    $("pos-cash").textContent = fmtMoney(s.cash_krw, acc);

    const peekEl = $("briefing-pos-peek-txt");
    const barWrap = $("bracket-bar-wrap");
    if (hasPos) {
      const sl = Number(s.stop_loss_price_krw);
      const tp = Number(s.take_profit_price_krw);
      const entry = Number(s.avg_entry_price_krw);
      const span = Math.max(0.0001, tp - sl);
      const entryPct = Math.max(4, Math.min(96, ((entry - sl) / span) * 100));
      const spotPct = Math.max(2, Math.min(98, ((spot - sl) / span) * 100));
      $("bracket-entry-mark").style.left = `${entryPct.toFixed(1)}%`;
      $("bracket-spot-dot").style.left = `${spotPct.toFixed(1)}%`;
      $("bracket-sl-lbl").textContent = `SL ${fmtAxisMoney(sl, acc)}`;
      $("bracket-entry-lbl").textContent = `평단 ${fmtAxisMoney(entry, acc)}`;
      $("bracket-tp-lbl").textContent = `TP ${fmtAxisMoney(tp, acc)}`;
      const toTpPct = ((tp / spot) - 1) * 100;
      const toSlPct = ((sl / spot) - 1) * 100;
      $("bracket-status").textContent = `손절 ${toSlPct.toFixed(1)}% · 익절 +${toTpPct.toFixed(1)}%`;
      if (peekEl) {
        peekEl.textContent = `평단 ${fmtAxisMoney(entry, acc)} · SL ${fmtAxisMoney(sl, acc)} · TP ${fmtAxisMoney(tp, acc)}`;
      }
      barWrap.classList.remove("hidden");
    } else {
      $("bracket-status").textContent = "보유 포지션 없음";
      if (peekEl) {
        peekEl.textContent = `전액 현금 관망 중 (${fmtMoney(s.cash_krw, acc)})`;
      }
      barWrap.classList.add("hidden");
    }
  }

  // =========================================================================
  // 2. STRATEGY VERSION CHANGE DETECTION & 1:1 PIXEL SVG CHART ENGINE
  // =========================================================================
  function getVersionChanges(acc) {
    if (Array.isArray(acc.version_changes) && acc.version_changes.length) {
      return acc.version_changes;
    }
    // Client-side fallback detection for legacy dashboard payloads
    const raw = [...(acc.briefings || [])].reverse();
    const out = [];
    let prev = null;
    raw.forEach((h) => {
      const m = h.decision?.model_name;
      if (m && prev && m !== prev) {
        const match = m.match(/v\d+(?:\.\d+)?/i);
        out.push({
          run_id: h.run_id,
          time_kst: h.time_kst,
          from_model: prev,
          to_model: m,
          label: match ? `${match[0].toLowerCase()} 전환` : `${m} 전환`,
        });
      }
      if (m) prev = m;
    });
    return out;
  }

  function buildVersionMarkersSvg(points, versionChanges, xFor, topY, bottomY, colorVersion, fontStack) {
    if (!versionChanges.length || !points.length) return "";
    let svg = "";
    const placedXs = [];

    versionChanges.forEach((vc) => {
      const targetHour = String(vc.time_kst || "").slice(0, 13);
      let idx = points.findIndex((p) => p.time_kst === vc.time_kst);
      if (idx < 0) {
        idx = points.findIndex((p) => String(p.time_kst || "").slice(0, 13) === targetHour);
      }
      if (idx < 0) {
        // Find nearest point at or after the transition timestamp
        idx = points.findIndex((p) => String(p.time_kst || "") >= String(vc.time_kst || ""));
      }
      if (idx < 0) return;

      const cx = xFor(idx);
      if (placedXs.some((px) => Math.abs(px - cx) < 28)) return;
      placedXs.push(cx);

      const label = vc.label || "전략 전환";
      const badgeW = Math.max(54, label.length * 7.6 + 14);
      const bx = Math.max(8, cx - badgeW / 2);

      svg += `
        <g class="version-marker">
          <line x1="${cx.toFixed(1)}" y1="${(topY + 18).toFixed(1)}" x2="${cx.toFixed(1)}" y2="${bottomY.toFixed(1)}" stroke="${colorVersion}" stroke-width="1.5" stroke-dasharray="4 3" stroke-opacity="0.78" />
          <rect x="${bx.toFixed(1)}" y="${(topY - 2).toFixed(1)}" width="${badgeW.toFixed(1)}" height="19" rx="5" fill="${colorVersion}" />
          <text x="${(bx + badgeW / 2).toFixed(1)}" y="${(topY + 10.5).toFixed(1)}" text-anchor="middle" fill="#ffffff" font-family="${fontStack}" font-weight="700" font-size="10">${escapeHtml(label)}</text>
        </g>
      `;
    });
    return svg;
  }

  function buildMarkerSvg(cx, cy, side, colorPos, colorNeg) {
    if (side === "BUY") {
      const pts = `${cx.toFixed(1)},${(cy - 7).toFixed(1)} ${(cx - 6).toFixed(1)},${(cy + 4).toFixed(1)} ${(cx + 6).toFixed(1)},${(cy + 4).toFixed(1)}`;
      return `<polygon points="${pts}" fill="${colorPos}" stroke="#ffffff" stroke-width="1.5" stroke-linejoin="round" />`;
    }
    const pts = `${cx.toFixed(1)},${(cy + 7).toFixed(1)} ${(cx - 6).toFixed(1)},${(cy - 4).toFixed(1)} ${(cx + 6).toFixed(1)},${(cy - 4).toFixed(1)}`;
    return `<polygon points="${pts}" fill="${colorNeg}" stroke="#ffffff" stroke-width="1.5" stroke-linejoin="round" />`;
  }

  function buildAxisPill(x, y, text, bgColor, svgW, minY, maxY, textColor = "#ffffff") {
    const w = Math.max(56, text.length * 6.6 + 12);
    const px = Math.min(svgW - w - 2, x);
    const py = Math.max(minY, Math.min(maxY, y));
    return `
      <g>
        <rect x="${px.toFixed(1)}" y="${(py - 10).toFixed(1)}" width="${w.toFixed(1)}" height="20" rx="5" fill="${bgColor}" />
        <text x="${(px + w / 2).toFixed(1)}" y="${(py + 3.5).toFixed(1)}" text-anchor="middle" fill="${textColor}" font-family="Paperlogy, sans-serif" font-weight="700" font-size="10.5">${escapeHtml(text)}</text>
      </g>
    `;
  }

  function sliceByRange(arr) {
    if (state.range === "all") return arr;
    const n = Number(state.range);
    return arr.length > n ? arr.slice(-n) : arr;
  }

  function renderChart() {
    const acc = currentAccount();
    if (!acc) return;

    const svg = $("main-chart-svg");
    const stage = $("chart-stage");
    const legendEl = $("chart-legend");
    const tooltip = $("chart-tooltip");
    const overlayBar = $("overlay-toggles");
    if (!stage || stage.clientWidth < 40) return;
    tooltip.classList.add("hidden");

    overlayBar.classList.toggle("hidden", state.chartMode !== "price");

    const rect = stage.getBoundingClientRect();
    const W = Math.max(280, Math.round(rect.width || 760));
    const H = Math.max(260, Math.round(rect.height || 355));
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.removeAttribute("preserveAspectRatio");

    const isNarrow = W < 520;
    const pad = { top: 26, right: isNarrow ? 78 : 92, bottom: 28, left: 16 };
    const plotW = Math.max(100, W - pad.left - pad.right);

    const styles = getComputedStyle(document.documentElement);
    const colorStrategy = styles.getPropertyValue("--accent-strategy").trim();
    const colorMint = styles.getPropertyValue("--accent-mint").trim();
    const colorBenchmark = styles.getPropertyValue("--accent-benchmark").trim();
    const colorVwap = styles.getPropertyValue("--accent-vwap").trim();
    const colorVersion = styles.getPropertyValue("--accent-version").trim();
    const colorPos = styles.getPropertyValue("--pos").trim();
    const colorNeg = styles.getPropertyValue("--neg").trim();
    const colorGrid = styles.getPropertyValue("--border-hairline").trim();
    const colorMuted = styles.getPropertyValue("--ink-muted").trim();
    const colorInk = styles.getPropertyValue("--ink-primary").trim();

    const fontStack = "Paperlogy, -apple-system, sans-serif";
    const xTickCount = isNarrow ? 3 : 5;
    const versionChanges = getVersionChanges(acc);

    // -------------------------------------------------------------------------
    // MODE 1 (DEFAULT): Accumulated OHLC Candlesticks + Strategy Indicator Overlays + RSI/MACD Sub-Chart
    // -------------------------------------------------------------------------
    if (state.chartMode === "price") {
      legendEl.innerHTML = `
        <span class="legend-item"><span class="legend-swatch" style="background:${colorStrategy}"></span>EMA 5</span>
        <span class="legend-item"><span class="legend-swatch" style="background:${colorBenchmark}"></span>EMA 13</span>
        <span class="legend-item"><span class="legend-swatch" style="background:${colorVwap}"></span>12h VWAP</span>
        <span class="legend-item"><span class="legend-tri up"></span>매수</span>
        <span class="legend-item"><span class="legend-tri down"></span>매도</span>
        ${versionChanges.length ? `<span class="legend-item"><span class="legend-swatch version"></span>전략 버전 전환</span>` : ""}
      `;

      const candlesAll = acc.candles_recent || [];
      const candles = sliceByRange(candlesAll);
      if (candles.length < 2) {
        svg.innerHTML = `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" fill="${colorMuted}" font-family="${fontStack}" font-size="13">누적 시세 캔들을 수집하는 중입니다.</text>`;
        return;
      }

      // Map executed trades from series onto candle hour keys
      const sidesByHour = new Map();
      (acc.series || []).forEach((p) => {
        if ((p.executed_sides || []).length) {
          const key = String(p.time_kst || "").slice(0, 13);
          const cur = sidesByHour.get(key) || [];
          sidesByHour.set(key, Array.from(new Set([...cur, ...p.executed_sides])));
        }
      });

      const pts = candles.map((c) => {
        const key = String(c.time_kst || "").slice(0, 13);
        return {
          ...c,
          open: Number(c.open),
          high: Number(c.high),
          low: Number(c.low),
          close: Number(c.close),
          executed_sides: sidesByHour.get(key) || [],
        };
      });

      // Split vertical space: Top 71% Price+Overlays, Gap 14px, Bottom 24% RSI(14)+MACD Histogram
      const totalH = H - pad.top - pad.bottom;
      const subGap = 16;
      const subH = Math.max(66, Math.round(totalH * 0.25));
      const mainH = Math.max(130, totalH - subH - subGap);
      const mainBottom = pad.top + mainH;
      const subTop = mainBottom + subGap;
      const subBottom = subTop + subH;

      const s = acc.summary || {};
      const vals = [];
      pts.forEach((p) => {
        vals.push(p.high, p.low);
        if (state.overlays.ema && p.ema5) vals.push(Number(p.ema5));
        if (state.overlays.ema && p.ema13) vals.push(Number(p.ema13));
        if (state.overlays.vwap && p.vwap12) vals.push(Number(p.vwap12));
        if (state.overlays.bb && p.bb_upper) vals.push(Number(p.bb_upper));
        if (state.overlays.bb && p.bb_lower) vals.push(Number(p.bb_lower));
      });
      if (state.overlays.sltp && s.stop_loss_price_krw) vals.push(Number(s.stop_loss_price_krw));
      if (state.overlays.sltp && s.take_profit_price_krw) vals.push(Number(s.take_profit_price_krw));

      let minV = Math.min(...vals);
      let maxV = Math.max(...vals);
      const span = Math.max((maxV - minV) * 0.10, minV * 0.006);
      minV -= span;
      maxV += span;

      const step = plotW / Math.max(1, pts.length);
      const xFor = (i) => pad.left + (i + 0.5) * step;
      const yFor = (v) => pad.top + (1 - (v - minV) / (maxV - minV)) * mainH;
      const candleW = Math.max(2, Math.min(10, step * 0.62));

      // Main grid
      let gridSvg = "";
      for (let t = 0; t <= 4; t++) {
        const v = minV + ((maxV - minV) * t) / 4;
        const y = yFor(v);
        gridSvg += `<line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${W - pad.right}" y2="${y.toFixed(1)}" stroke="${colorGrid}" stroke-width="1" />`;
        gridSvg += `<text x="${(W - pad.right + 8).toFixed(1)}" y="${(y + 3.5).toFixed(1)}" text-anchor="start" fill="${colorMuted}" font-family="${fontStack}" font-weight="500" font-size="10.5">${fmtAxisMoney(v, acc)}</text>`;
      }

      // Bollinger Band shaded ribbon
      let bbSvg = "";
      if (state.overlays.bb && pts.some((p) => p.bb_upper && p.bb_lower)) {
        const valid = pts.map((p, i) => (p.bb_upper && p.bb_lower ? { i, u: Number(p.bb_upper), l: Number(p.bb_lower) } : null)).filter(Boolean);
        if (valid.length >= 2) {
          const upperPath = valid.map((v, k) => `${k === 0 ? "M" : "L"}${xFor(v.i).toFixed(1)},${yFor(v.u).toFixed(1)}`).join(" ");
          const lowerRev = [...valid].reverse().map((v) => `L${xFor(v.i).toFixed(1)},${yFor(v.l).toFixed(1)}`).join(" ");
          const lowerLine = valid.map((v, k) => `${k === 0 ? "M" : "L"}${xFor(v.i).toFixed(1)},${yFor(v.l).toFixed(1)}`).join(" ");
          bbSvg = `
            <path d="${upperPath} ${lowerRev} Z" fill="${colorStrategy}" fill-opacity="0.06" />
            <path d="${upperPath}" fill="none" stroke="${colorStrategy}" stroke-opacity="0.28" stroke-width="1" stroke-dasharray="3 3" />
            <path d="${lowerLine}" fill="none" stroke="${colorStrategy}" stroke-opacity="0.28" stroke-width="1" stroke-dasharray="3 3" />
          `;
        }
      }

      // Candlesticks
      let candlesSvg = "";
      pts.forEach((p, i) => {
        const cx = xFor(i);
        const isUp = p.close >= p.open;
        const cColor = isUp ? colorPos : colorNeg;
        const yHigh = yFor(p.high);
        const yLow = yFor(p.low);
        const yOpen = yFor(p.open);
        const yClose = yFor(p.close);
        const bodyTop = Math.min(yOpen, yClose);
        const bodyH = Math.max(1.5, Math.abs(yClose - yOpen));
        candlesSvg += `<line x1="${cx.toFixed(1)}" y1="${yHigh.toFixed(1)}" x2="${cx.toFixed(1)}" y2="${yLow.toFixed(1)}" stroke="${cColor}" stroke-width="1.2" />`;
        candlesSvg += `<rect x="${(cx - candleW / 2).toFixed(1)}" y="${bodyTop.toFixed(1)}" width="${candleW.toFixed(1)}" height="${bodyH.toFixed(1)}" rx="1" fill="${cColor}" fill-opacity="${isUp ? "0.9" : "0.85"}" />`;
      });

      // EMA 5 / 13 & 12h VWAP lines
      let overlaysSvg = "";
      if (state.overlays.ema) {
        const e5 = pts.map((p, i) => (p.ema5 ? `${xFor(i).toFixed(1)},${yFor(Number(p.ema5)).toFixed(1)}` : null)).filter(Boolean);
        const e13 = pts.map((p, i) => (p.ema13 ? `${xFor(i).toFixed(1)},${yFor(Number(p.ema13)).toFixed(1)}` : null)).filter(Boolean);
        if (e13.length >= 2) overlaysSvg += `<path d="M${e13.join(" L")}" fill="none" stroke="${colorBenchmark}" stroke-width="1.7" stroke-linejoin="round" />`;
        if (e5.length >= 2) overlaysSvg += `<path d="M${e5.join(" L")}" fill="none" stroke="${colorStrategy}" stroke-width="1.6" stroke-linejoin="round" />`;
      }
      if (state.overlays.vwap) {
        const vw = pts.map((p, i) => (p.vwap12 ? `${xFor(i).toFixed(1)},${yFor(Number(p.vwap12)).toFixed(1)}` : null)).filter(Boolean);
        if (vw.length >= 2) overlaysSvg += `<path d="M${vw.join(" L")}" fill="none" stroke="${colorVwap}" stroke-width="1.5" stroke-dasharray="4 2.5" />`;
      }

      // SL/TP & Live Spot Right-Axis Pills with anti-overlap spacing
      let guidesSvg = "";
      const axisPills = [];
      if (state.overlays.sltp && s.take_profit_price_krw) {
        const yTp = yFor(Number(s.take_profit_price_krw));
        guidesSvg += `<line x1="${pad.left}" y1="${yTp.toFixed(1)}" x2="${W - pad.right}" y2="${yTp.toFixed(1)}" stroke="${colorPos}" stroke-width="1.3" stroke-dasharray="4 3" />`;
        axisPills.push({ y: yTp, text: `TP ${fmtAxisMoney(s.take_profit_price_krw, acc)}`, color: colorPos });
      }
      const spotVal = Number(acc.market.spot_price || pts[pts.length - 1].close);
      axisPills.push({ y: yFor(spotVal), text: fmtAxisMoney(spotVal, acc), color: colorStrategy });
      if (state.overlays.sltp && s.stop_loss_price_krw) {
        const ySl = yFor(Number(s.stop_loss_price_krw));
        guidesSvg += `<line x1="${pad.left}" y1="${ySl.toFixed(1)}" x2="${W - pad.right}" y2="${ySl.toFixed(1)}" stroke="${colorNeg}" stroke-width="1.3" stroke-dasharray="4 3" />`;
        axisPills.push({ y: ySl, text: `SL ${fmtAxisMoney(s.stop_loss_price_krw, acc)}`, color: colorNeg });
      }
      axisPills.sort((a, b) => a.y - b.y);
      for (let i = 1; i < axisPills.length; i++) {
        if (axisPills[i].y - axisPills[i - 1].y < 21) axisPills[i].y = axisPills[i - 1].y + 21;
      }
      const pillsSvg = axisPills
        .map((p) => buildAxisPill(W - pad.right + 4, p.y, p.text, p.color, W, pad.top + 10, mainBottom - 10))
        .join("");

      // Trade markers on candles
      let markersSvg = "";
      pts.forEach((p, i) => {
        if (p.executed_sides.includes("BUY")) markersSvg += buildMarkerSvg(xFor(i), yFor(p.low) + 9, "BUY", colorPos, colorNeg);
        if (p.executed_sides.includes("SELL")) markersSvg += buildMarkerSvg(xFor(i), yFor(p.high) - 9, "SELL", colorPos, colorNeg);
      });

      // Sub-chart: RSI(14) line + Fast MACD Histogram bars
      let subSvg = `<line x1="${pad.left}" y1="${subTop.toFixed(1)}" x2="${W - pad.right}" y2="${subTop.toFixed(1)}" stroke="${colorGrid}" stroke-width="1" />`;
      subSvg += `<text x="${pad.left + 4}" y="${(subTop + 11).toFixed(1)}" fill="${colorMuted}" font-family="${fontStack}" font-weight="700" font-size="9.5">RSI(14) · Fast MACD</text>`;
      const yRsi = (r) => subTop + (1 - Math.max(0, Math.min(100, r)) / 100) * subH;
      const y70 = yRsi(70);
      const y30 = yRsi(30);
      subSvg += `<rect x="${pad.left}" y="${y70.toFixed(1)}" width="${plotW}" height="${(y30 - y70).toFixed(1)}" fill="${colorStrategy}" fill-opacity="0.045" />`;
      subSvg += `<line x1="${pad.left}" y1="${y70.toFixed(1)}" x2="${W - pad.right}" y2="${y70.toFixed(1)}" stroke="${colorGrid}" stroke-dasharray="2 3" />`;
      subSvg += `<line x1="${pad.left}" y1="${y30.toFixed(1)}" x2="${W - pad.right}" y2="${y30.toFixed(1)}" stroke="${colorGrid}" stroke-dasharray="2 3" />`;
      subSvg += `<text x="${(W - pad.right + 8).toFixed(1)}" y="${(y70 + 3).toFixed(1)}" fill="${colorMuted}" font-family="${fontStack}" font-size="9.5">70</text>`;
      subSvg += `<text x="${(W - pad.right + 8).toFixed(1)}" y="${(y30 + 3).toFixed(1)}" fill="${colorMuted}" font-family="${fontStack}" font-size="9.5">30</text>`;

      const macdVals = pts.map((p) => Number(p.macd_hist || 0));
      const maxMacd = Math.max(1e-6, ...macdVals.map((v) => Math.abs(v)));
      const subMidY = subTop + subH / 2;
      pts.forEach((p, i) => {
        if (p.macd_hist === undefined) return;
        const m = Number(p.macd_hist);
        const hBar = (Math.abs(m) / maxMacd) * (subH * 0.42);
        const yBar = m >= 0 ? subMidY - hBar : subMidY;
        const barColor = m >= 0 ? colorPos : colorNeg;
        subSvg += `<rect x="${(xFor(i) - candleW / 2).toFixed(1)}" y="${yBar.toFixed(1)}" width="${candleW.toFixed(1)}" height="${Math.max(1, hBar).toFixed(1)}" fill="${barColor}" fill-opacity="0.32" />`;
      });

      const rsiPts = pts.map((p, i) => (p.rsi14 !== undefined ? `${xFor(i).toFixed(1)},${yRsi(Number(p.rsi14)).toFixed(1)}` : null)).filter(Boolean);
      if (rsiPts.length >= 2) {
        subSvg += `<path d="M${rsiPts.join(" L")}" fill="none" stroke="${colorMint}" stroke-width="1.6" stroke-linejoin="round" />`;
      }

      // Strategy Version Transition Vertical Lines (spanning main + subchart)
      const versionSvg = buildVersionMarkersSvg(pts, versionChanges, xFor, pad.top, subBottom, colorVersion, fontStack);

      // X-axis labels at very bottom
      let xAxisSvg = "";
      const ticks = Math.min(xTickCount, pts.length);
      for (let k = 0; k < ticks; k++) {
        const idx = Math.round((k * (pts.length - 1)) / Math.max(1, ticks - 1));
        const anchor = k === 0 ? "start" : k === ticks - 1 ? "end" : "middle";
        xAxisSvg += `<text x="${xFor(idx).toFixed(1)}" y="${H - 8}" text-anchor="${anchor}" fill="${colorMuted}" font-family="${fontStack}" font-weight="500" font-size="10.5">${shortKst(pts[idx].time_kst)}</text>`;
      }

      svg.innerHTML = `
        ${gridSvg}
        ${bbSvg}
        ${candlesSvg}
        ${overlaysSvg}
        ${guidesSvg}
        ${subSvg}
        ${versionSvg}
        ${markersSvg}
        ${pillsSvg}
        ${xAxisSvg}
        <line id="hover-vline" x1="0" y1="${pad.top}" x2="0" y2="${subBottom}" stroke="${colorInk}" stroke-opacity="0.28" stroke-width="1" stroke-dasharray="3 3" visibility="hidden" />
        <line id="hover-hline" x1="${pad.left}" y1="0" x2="${W - pad.right}" y2="0" stroke="${colorInk}" stroke-opacity="0.18" stroke-width="1" stroke-dasharray="3 3" visibility="hidden" />
        <circle id="hover-dot" cx="0" cy="0" r="4.5" fill="${colorStrategy}" stroke="#ffffff" stroke-width="2" visibility="hidden" />
        <rect id="hover-capture" x="${pad.left}" y="${pad.top}" width="${plotW}" height="${subBottom - pad.top}" fill="transparent" />
      `;

      bindChartHover(W, pts, xFor, (p) => yFor(p.close), (p) => `
        <div class="tt-time">${escapeHtml(p.time_kst)}</div>
        <div class="tt-row"><span>시 / 고 / 저</span><span class="num-display">${fmtAxisMoney(p.open, acc)} / ${fmtAxisMoney(p.high, acc)} / ${fmtAxisMoney(p.low, acc)}</span></div>
        <div class="tt-row"><span>종가</span><strong class="num-display">${fmtMoney(p.close, acc)}</strong></div>
        ${p.ema5 ? `<div class="tt-row"><span>EMA 5 / 13</span><span class="num-display">${fmtAxisMoney(p.ema5, acc)} / ${fmtAxisMoney(p.ema13, acc)}</span></div>` : ""}
        ${p.vwap12 ? `<div class="tt-row"><span>12h VWAP</span><span class="num-display">${fmtMoney(p.vwap12, acc)}</span></div>` : ""}
        ${p.rsi14 !== undefined ? `<div class="tt-row"><span>RSI / MACD</span><span class="num-display">${Number(p.rsi14).toFixed(1)} / ${Number(p.macd_hist || 0).toFixed(1)}</span></div>` : ""}
        ${p.executed_sides.length ? `<div class="tt-row"><span>체결</span><strong class="num-display">${p.executed_sides.join(", ")}</strong></div>` : ""}
      `);
      return;
    }

    const plotH = Math.max(120, H - pad.top - pad.bottom);
    const pillMinY = pad.top + 10;
    const pillMaxY = pad.top + plotH - 10;

    // -------------------------------------------------------------------------
    // MODE 2: Equity Curve vs Buy & Hold (+ Strategy Version Transition Lines)
    // -------------------------------------------------------------------------
    if (state.chartMode === "equity") {
      legendEl.innerHTML = `
        <span class="legend-item"><span class="legend-swatch" style="background:${colorStrategy}"></span>퀀트 단타 평가자산</span>
        <span class="legend-item"><span class="legend-swatch dashed"></span>단순 보유 (B&amp;H)</span>
        <span class="legend-item"><span class="legend-tri up"></span>매수</span>
        <span class="legend-item"><span class="legend-tri down"></span>매도</span>
        ${versionChanges.length ? `<span class="legend-item"><span class="legend-swatch version"></span>전략 버전 전환</span>` : ""}
      `;

      const pts = sliceByRange(acc.series || []);
      if (!pts.length) {
        svg.innerHTML = `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" fill="${colorMuted}" font-family="${fontStack}" font-size="13">누적 데이터가 아직 없습니다.</text>`;
        return;
      }

      const initCap = Number(acc.meta?.initial_capital_krw || 10_000_000);
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
                model_name: pts[0].model_name,
                executed_sides: [],
              },
              pts[0],
            ]
          : pts;

      const vals = chartPts.flatMap((p) => [Number(p.equity_krw), Number(p.benchmark_krw), initCap]);
      let minV = Math.min(...vals);
      let maxV = Math.max(...vals);
      const rawSpan = maxV - minV;
      const minPad = isUsd(acc) ? 100 : 100_000;
      const margin = rawSpan < minPad / 2 ? minPad : rawSpan * 0.18;
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
        gridSvg += `<text x="${(W - pad.right + 8).toFixed(1)}" y="${(y + 3.5).toFixed(1)}" text-anchor="start" fill="${colorMuted}" font-family="${fontStack}" font-weight="500" font-size="11">${fmtAxisMoney(v, acc, true)}</text>`;
      }

      const yInit = yFor(initCap);
      gridSvg += `<line x1="${pad.left}" y1="${yInit.toFixed(1)}" x2="${W - pad.right}" y2="${yInit.toFixed(1)}" stroke="${colorMuted}" stroke-opacity="0.45" stroke-width="1" stroke-dasharray="2 3" />`;

      let xAxisSvg = "";
      const ticks = Math.min(xTickCount, chartPts.length);
      for (let k = 0; k < ticks; k++) {
        const idx = Math.round((k * (chartPts.length - 1)) / Math.max(1, ticks - 1));
        const label = chartPts[idx].time_kst.includes("시작") ? "시작" : shortKst(chartPts[idx].time_kst);
        const anchor = k === 0 ? "start" : k === ticks - 1 ? "end" : "middle";
        xAxisSvg += `<text x="${xFor(idx).toFixed(1)}" y="${H - 8}" text-anchor="${anchor}" fill="${colorMuted}" font-family="${fontStack}" font-weight="500" font-size="11">${escapeHtml(label)}</text>`;
      }

      const versionSvg = buildVersionMarkersSvg(chartPts, versionChanges, xFor, pad.top, pad.top + plotH, colorVersion, fontStack);

      let markersSvg = "";
      chartPts.forEach((p, i) => {
        const sides = p.executed_sides || [];
        if (sides.includes("BUY")) markersSvg += buildMarkerSvg(xFor(i), yFor(p.equity_krw), "BUY", colorPos, colorNeg);
        if (sides.includes("SELL")) markersSvg += buildMarkerSvg(xFor(i), yFor(p.equity_krw), "SELL", colorPos, colorNeg);
      });

      const lastPt = chartPts[chartPts.length - 1];
      const endPill = buildAxisPill(
        W - pad.right + 4,
        yFor(lastPt.equity_krw),
        fmtAxisMoney(lastPt.equity_krw, acc, true),
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
        ${versionSvg}
        ${markersSvg}
        ${endPill}
        <line id="hover-vline" x1="0" y1="${pad.top}" x2="0" y2="${pad.top + plotH}" stroke="${colorInk}" stroke-opacity="0.28" stroke-width="1" stroke-dasharray="3 3" visibility="hidden" />
        <line id="hover-hline" x1="${pad.left}" y1="0" x2="${W - pad.right}" y2="0" stroke="${colorInk}" stroke-opacity="0.18" stroke-width="1" stroke-dasharray="3 3" visibility="hidden" />
        <circle id="hover-dot" cx="0" cy="0" r="5" fill="${colorStrategy}" stroke="#ffffff" stroke-width="2.2" visibility="hidden" />
        <rect id="hover-capture" x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}" fill="transparent" />
      `;

      bindChartHover(W, chartPts, xFor, (p) => yFor(p.equity_krw), (p) => `
        <div class="tt-time">${escapeHtml(p.time_kst)}</div>
        <div class="tt-row"><span>전략 자산</span><strong class="num-display">${fmtMoney(p.equity_krw, acc)} (${fmtPct(p.return_pct)})</strong></div>
        <div class="tt-row"><span>단순 보유</span><span class="num-display">${fmtMoney(p.benchmark_krw, acc)} (${fmtPct(p.benchmark_return_pct)})</span></div>
        <div class="tt-row"><span>기준 시세</span><span class="num-display">${fmtMoney(p.spot_price, acc)}</span></div>
        ${p.model_name ? `<div class="tt-row"><span>전략 엔진</span><span class="num-display">${escapeHtml(p.model_name)}</span></div>` : ""}
      `);
      return;
    }

    // -------------------------------------------------------------------------
    // MODE 3: Quant Score (-100..+100) + RSI(14) + Strategy Version Markers
    // -------------------------------------------------------------------------
    legendEl.innerHTML = `
      <span class="legend-item"><span class="legend-swatch" style="background:${colorStrategy}"></span>단기 퀀트 점수</span>
      <span class="legend-item"><span class="legend-swatch" style="background:${colorMint}"></span>RSI (14)</span>
      <span class="legend-item"><span class="legend-tri up"></span>매수 (+24)</span>
      <span class="legend-item"><span class="legend-tri down"></span>매도 (-22)</span>
      ${versionChanges.length ? `<span class="legend-item"><span class="legend-swatch version"></span>전략 버전 전환</span>` : ""}
    `;

    const rawBriefings = [...(acc.briefings || [])].reverse();
    const sigPts = sliceByRange(rawBriefings).map((b) => ({
      time_kst: b.time_kst,
      score: Number(b.indicators?.quant_score ?? 0),
      rsi: Number(b.indicators?.rsi14 ?? 50),
      action: b.decision?.action || "HOLD",
      model_name: b.decision?.model_name || "",
      executed_sides: b.executed_sides || [],
    }));

    if (sigPts.length < 2) {
      svg.innerHTML = `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" fill="${colorMuted}" font-family="${fontStack}" font-size="13">시그널 추이는 2회 이상 누적 시 표시됩니다.</text>`;
      return;
    }

    const xFor = (i) => pad.left + (i / Math.max(1, sigPts.length - 1)) * plotW;
    const yScore = (v) => pad.top + (1 - (v + 100) / 200) * plotH;
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
      xAxisSvg += `<text x="${xFor(idx).toFixed(1)}" y="${H - 8}" text-anchor="${anchor}" fill="${colorMuted}" font-family="${fontStack}" font-weight="500" font-size="11">${shortKst(sigPts[idx].time_kst)}</text>`;
    }

    const scorePath = sigPts.map((p, i) => `${i === 0 ? "M" : "L"}${xFor(i).toFixed(1)},${yScore(p.score).toFixed(1)}`).join(" ");
    const rsiPath = sigPts.map((p, i) => `${i === 0 ? "M" : "L"}${xFor(i).toFixed(1)},${yRsi(p.rsi).toFixed(1)}`).join(" ");
    const versionSvg = buildVersionMarkersSvg(sigPts, versionChanges, xFor, pad.top, pad.top + plotH, colorVersion, fontStack);

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
      ${versionSvg}
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
      ${p.model_name ? `<div class="tt-row"><span>엔진</span><span class="num-display">${escapeHtml(p.model_name)}</span></div>` : ""}
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
      const tipW = tooltip.offsetWidth || 215;
      const leftPos = px + 16 + tipW > stageRect.width - 80 ? Math.max(10, px - tipW - 16) : px + 16;
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

  function renderBriefingFeed(acc) {
    const container = $("briefing-feed");
    const moreWrap = $("feed-more-wrap");
    const moreBtn = $("btn-load-more");
    const all = acc.briefings || [];
    const filtered = all.filter((item) => {
      if (state.briefingFilter === "traded") return (item.executed_sides || []).length > 0;
      if (state.briefingFilter === "hold") return (item.executed_sides || []).length === 0;
      return true;
    });

    if (!filtered.length) {
      container.innerHTML = `<div class="empty-state">조건에 해당하는 브리핑 이력이 아직 없습니다.</div>`;
      if (moreWrap) moreWrap.classList.add("hidden");
      return;
    }

    const visible = filtered.slice(0, state.briefingVisibleCount);
    const remaining = filtered.length - visible.length;

    container.innerHTML = visible
      .map((item) => {
        const dec = item.decision;
        const actLower = dec.action.toLowerCase();
        const isOpen = state.expandedRunIds.has(item.run_id);
        const factorsHtml = (dec.key_factors || []).map((f) => `<li>${escapeHtml(f)}</li>`).join("");
        const qScore = Number(item.indicators?.quant_score ?? 0);
        const tradeSummary = (item.executed_trades || [])
          .map((t) => `${t.side} ${fmtQty(t.sol_qty, acc)} @ ${fmtMoney(t.exec_price, acc)} (${triggerLabel(t.trigger)})`)
          .join(" / ");

        return `
          <article class="feed-item">
            <button type="button" class="feed-summary" data-toggle-run="${item.run_id}" aria-expanded="${isOpen}">
              <span class="num-display feed-time">${escapeHtml(shortKst(item.time_kst))}</span>
              <span class="badge-action ${actLower}">${dec.action}</span>
              <span class="feed-headline">${escapeHtml(dec.headline)}</span>
              <span class="num-display feed-score ${signClass(qScore)}">${qScore > 0 ? "+" : ""}${qScore.toFixed(1)}점</span>
              <span class="num-display feed-price">${fmtMoney(item.spot_price, acc)}</span>
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
                <span>평가자산: ${fmtMoney(item.equity_krw, acc)}</span>
                <span>비중: ${Number(item.exposure_pct).toFixed(1)}%</span>
              </div>
            </div>
          </article>
        `;
      })
      .join("");

    if (moreWrap && moreBtn) {
      if (remaining > 0) {
        moreBtn.textContent = `이전 브리핑 더 보기 (남은 ${remaining}건)`;
        moreWrap.classList.remove("hidden");
      } else {
        moreWrap.classList.add("hidden");
      }
    }

    container.querySelectorAll("[data-toggle-run]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = Number(btn.dataset.toggleRun);
        if (state.expandedRunIds.has(id)) state.expandedRunIds.delete(id);
        else state.expandedRunIds.add(id);
        renderBriefingFeed(currentAccount());
      });
    });
  }

  function renderTradesTable(acc) {
    const tbody = $("trades-tbody");
    const cardsEl = $("trades-cards");
    const trades = acc.trades || [];
    const countStr = String(trades.length);
    $("tab-trades-count").textContent = countStr;
    if ($("nav-trades-count")) $("nav-trades-count").textContent = countStr;

    if (!trades.length) {
      const emptyMsg = "아직 체결된 모의 거래가 없습니다. 개장 중 진입 조건이 충족되면 자동으로 기록됩니다.";
      tbody.innerHTML = `<tr><td colspan="11" class="empty-state">${emptyMsg}</td></tr>`;
      if (cardsEl) cardsEl.innerHTML = `<div class="empty-state">${emptyMsg}</div>`;
      return;
    }

    tbody.innerHTML = trades
      .map((t) => {
        const sideLower = t.side.toLowerCase();
        const pnlText =
          t.realized_pnl_krw !== null && t.realized_pnl_krw !== undefined
            ? `${fmtMoney(t.realized_pnl_krw, acc, true)} (${fmtPct(t.realized_pnl_pct)})`
            : "-";
        const slipBps = t.slippage_bps !== undefined && t.slippage_bps !== null ? `${Number(t.slippage_bps).toFixed(1)}bp` : "-";
        const slipCost = t.slippage_krw !== undefined && t.slippage_krw !== null ? ` (${fmtMoney(t.slippage_krw, acc)})` : "";
        const bd = t.slippage_breakdown;
        const slipTitle = bd ? `호가 ${bd.book_bps}bp + 변동성 ${bd.vol_bps}bp + 이벤트 ${bd.event_bps}bp` : "";

        return `
          <tr>
            <td class="mono">${escapeHtml(shortKst(t.time_kst))}</td>
            <td><span class="badge-action ${sideLower}">${t.side}</span></td>
            <td>${escapeHtml(triggerLabel(t.trigger))}</td>
            <td class="num">${fmtMoney(t.spot_price, acc)}</td>
            <td class="num">${fmtMoney(t.exec_price, acc)}</td>
            <td class="num">${fmtQty(t.sol_qty, acc)}</td>
            <td class="num">${fmtMoney(t.krw_amount, acc)}</td>
            <td class="num">${fmtMoney(t.fee_krw, acc)}</td>
            <td class="num" title="${escapeHtml(slipTitle)}">${slipBps}${slipCost}</td>
            <td class="num ${t.realized_pnl_krw !== null ? signClass(t.realized_pnl_krw) : ""}">${pnlText}</td>
            <td class="reason-cell">${escapeHtml(t.reason)}</td>
          </tr>
        `;
      })
      .join("");

    if (cardsEl) {
      cardsEl.innerHTML = trades
        .map((t) => {
          const sideLower = t.side.toLowerCase();
          const hasPnl = t.realized_pnl_krw !== null && t.realized_pnl_krw !== undefined;
          const pnlText = hasPnl ? `${fmtMoney(t.realized_pnl_krw, acc, true)} (${fmtPct(t.realized_pnl_pct)})` : "-";
          const slipBps = t.slippage_bps !== undefined && t.slippage_bps !== null ? `${Number(t.slippage_bps).toFixed(1)}bp` : "-";
          return `
            <article class="m-trade-card">
              <div class="m-trade-top">
                <div class="m-trade-left">
                  <span class="badge-action ${sideLower}">${t.side}</span>
                  <strong class="num-display" style="font-size:0.84rem">${fmtMoney(t.exec_price, acc)}</strong>
                  <span style="font-size:0.72rem;color:var(--ink-muted)">${escapeHtml(triggerLabel(t.trigger))}</span>
                </div>
                <span class="mono m-trade-time">${escapeHtml(shortKst(t.time_kst))}</span>
              </div>
              <div class="m-trade-grid num-display">
                <div class="m-trade-kv"><span>수량</span><strong>${fmtQty(t.sol_qty, acc)}</strong></div>
                <div class="m-trade-kv"><span>거래금</span><strong>${fmtMoney(t.krw_amount, acc)}</strong></div>
                <div class="m-trade-kv"><span>수수료/슬리피지</span><strong>${fmtMoney(t.fee_krw, acc)} · ${slipBps}</strong></div>
                <div class="m-trade-kv"><span>실현손익</span><strong class="${hasPnl ? signClass(t.realized_pnl_krw) : ""}">${pnlText}</strong></div>
              </div>
              <div class="m-trade-reason">${escapeHtml(t.reason)}</div>
            </article>
          `;
        })
        .join("");
    }
  }

  function switchView(target) {
    if (!target) return;
    state.activeView = target;
    document.querySelectorAll("[data-view-tab]").forEach((btn) => {
      const active = btn.dataset.viewTab === target;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", String(active));
    });
    ["overview", "indicators", "history", "rules"].forEach((name) => {
      const el = $(`view-${name}`);
      if (el) el.classList.toggle("hidden", name !== target);
    });
    if (target === "overview" && currentAccount()) {
      requestAnimationFrame(() => renderChart());
    }
  }

  function switchBriefSubTab(target) {
    state.briefSubTab = target;
    document.querySelectorAll("[data-brief-tab]").forEach((btn) => {
      const active = btn.dataset.briefTab === target;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", String(active));
    });
    ["factors", "notes"].forEach((name) => {
      const el = $(`brief-panel-${name}`);
      if (el) el.classList.toggle("hidden", name !== target);
    });
  }

  function renderSelectedAccount() {
    const acc = currentAccount();
    if (!acc) return;
    renderScoreboard();
    renderSelectedHeader(acc);
    renderKpis(acc);
    renderIndicators(acc);
    renderLatestBriefing(acc);
    renderChart();
    renderBriefingFeed(acc);
    renderTradesTable(acc);
  }

  async function loadDashboardData() {
    const refreshBtn = $("refresh-btn");
    const refreshLabel = $("refresh-label");
    refreshBtn.disabled = true;
    if (refreshLabel) refreshLabel.textContent = "갱신 중";
    try {
      const resp = await fetch(`data/dashboard.json?t=${Date.now()}`, { cache: "no-store" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const raw = await resp.json();
      const norm = normalizePayload(raw);
      state.rawPayload = raw;
      state.accounts = norm.accounts;
      state.assetOrder = norm.assetOrder;

      const savedAsset = (() => {
        try {
          return localStorage.getItem("quant_trader_selected_asset");
        } catch (_) {
          return null;
        }
      })();
      if (savedAsset && state.accounts[savedAsset]) {
        state.selectedAsset = savedAsset;
      } else if (!state.accounts[state.selectedAsset]) {
        state.selectedAsset = norm.defaultAsset;
      }

      renderSelectedAccount();
    } catch (err) {
      const banner = $("status-banner");
      banner.innerHTML = `<strong>데이터 로드 대기 중:</strong> 시뮬레이션 데이터를 불러오지 못했습니다 (${escapeHtml(err.message)}). 잠시 후 새로고침해 주세요.`;
      banner.classList.remove("hidden");
    } finally {
      refreshBtn.disabled = false;
      if (refreshLabel) refreshLabel.textContent = "새로고침";
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

    // Primary Section View Tabs
    document.querySelectorAll("[data-view-tab]").forEach((btn) => {
      btn.addEventListener("click", () => switchView(btn.dataset.viewTab));
    });

    // Quick-Jump Links ("상세 지표 ->", "포지션 상세 ->")
    document.querySelectorAll("[data-jump-view]").forEach((btn) => {
      btn.addEventListener("click", () => switchView(btn.dataset.jumpView));
    });

    // Briefing Card Mini Sub-Tabs ("핵심 근거" | "리스크 · 관전")
    document.querySelectorAll("[data-brief-tab]").forEach((btn) => {
      btn.addEventListener("click", () => switchBriefSubTab(btn.dataset.briefTab));
    });

    // Chart Mode Tabs
    document.querySelectorAll("[data-chart-mode]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.chartMode = btn.dataset.chartMode;
        document.querySelectorAll("[data-chart-mode]").forEach((b) => {
          const active = b === btn;
          b.classList.toggle("active", active);
          b.setAttribute("aria-selected", String(active));
        });
        if (currentAccount()) renderChart();
      });
    });

    // Range Selector
    document.querySelectorAll("[data-range]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.range = btn.dataset.range;
        document.querySelectorAll("[data-range]").forEach((b) => b.classList.toggle("active", b === btn));
        if (currentAccount()) renderChart();
      });
    });

    // Strategy Indicator Overlay Chips
    document.querySelectorAll("[data-overlay]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.dataset.overlay;
        state.overlays[key] = !state.overlays[key];
        btn.classList.toggle("active", state.overlays[key]);
        if (currentAccount()) renderChart();
      });
    });

    // History Sub-Tabs ("시간별 브리핑" | "체결 원장")
    document.querySelectorAll("[data-ledger-tab]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = btn.dataset.ledgerTab;
        document.querySelectorAll("[data-ledger-tab]").forEach((b) => {
          const active = b === btn;
          b.classList.toggle("active", active);
          b.setAttribute("aria-selected", String(active));
        });
        ["briefings", "trades"].forEach((name) => {
          $(`panel-${name}`).classList.toggle("hidden", name !== target);
        });
        $("briefing-filters").classList.toggle("hidden", target !== "briefings");
      });
    });

    // Briefing Filters
    document.querySelectorAll("[data-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.briefingFilter = btn.dataset.filter;
        state.briefingVisibleCount = 10;
        document.querySelectorAll("[data-filter]").forEach((b) => b.classList.toggle("active", b === btn));
        if (currentAccount()) renderBriefingFeed(currentAccount());
      });
    });

    // Briefing Load More Pagination
    const loadMoreBtn = $("btn-load-more");
    if (loadMoreBtn) {
      loadMoreBtn.addEventListener("click", () => {
        state.briefingVisibleCount += 15;
        if (currentAccount()) renderBriefingFeed(currentAccount());
      });
    }

    // 1:1 Pixel SVG ResizeObserver
    const stage = $("chart-stage");
    if (typeof ResizeObserver !== "undefined" && stage) {
      const ro = new ResizeObserver(() => {
        if (!currentAccount() || state.activeView !== "overview") return;
        clearTimeout(state.resizeTimer);
        state.resizeTimer = setTimeout(() => renderChart(), 50);
      });
      ro.observe(stage);
    } else {
      window.addEventListener("resize", () => {
        if (currentAccount() && state.activeView === "overview") renderChart();
      });
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    wireEvents();
    loadDashboardData();
  });
})();
