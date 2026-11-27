"""Pure deterministic 1-hour short-term quantitative trading strategy for Crypto, KRX, and NASDAQ."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .config import (
    ASSETS,
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TAKE_PROFIT_PCT,
    MAX_POSITION_RATIO,
    MAX_STOP_LOSS_PCT,
    MAX_TAKE_PROFIT_PCT,
    MIN_STOP_LOSS_PCT,
    MIN_TAKE_PROFIT_PCT,
    TIME_STOP_HOURS,
    AssetConfig,
)
from .market_data import MarketSnapshot


@dataclass
class TradingDecision:
    action: str  # "BUY" | "SELL" | "HOLD"
    order_pct: float  # BUY: % of available cash (0..100); SELL: % of held qty (0..100); HOLD: 0
    confidence: int  # 0..100
    stop_loss_pct: float
    take_profit_pct: float
    headline: str
    briefing_summary: str
    key_factors: list[str]
    risk_notes: str
    next_watch: str
    engine: str = "quant_shortterm"
    model_name: str = "quant-shortterm-v2"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def format_price(value: float, asset: AssetConfig) -> str:
    if asset.quote_currency == "USD":
        return f"${value:,.2f}"
    return f"{round(value):,}원"


def decide_trade(
    snapshot: MarketSnapshot,
    indicators: dict[str, Any],
    portfolio_context: dict[str, Any],
    *,
    asset: AssetConfig | None = None,
) -> TradingDecision:
    """Short-term 1h quant decision engine: fast EMA5/13 + MACD(8,17,6) + 12h VWAP + Time-Stop."""
    cfg = asset or ASSETS["sol"]
    spot = snapshot.spot_price
    score = float(indicators["quant_score"])
    rsi14 = float(indicators["rsi14"])
    rsi_prev = float(indicators["rsi14_prev"])
    ema5 = float(indicators["ema5"])
    ema13 = float(indicators["ema13"])
    fast_align = str(indicators["fast_alignment"])
    htf_trend = str(indicators["htf_trend"])
    bb_b = float(indicators["bb_percent_b"])
    atr_pct = float(indicators["atr14_pct"])
    vol_ratio = float(indicators["volume_ratio_24h"])
    macd_hist = float(indicators["macd_hist"])
    macd_delta = float(indicators["macd_hist_delta"])
    vwap12 = float(indicators["vwap_12h"])
    dist_vwap12 = float(indicators["dist_from_vwap12_pct"])
    dc_high = float(indicators["donchian_high_12h"])
    dc_low = float(indicators["donchian_low_12h"])
    regime_kr = str(indicators["regime_kr"])

    exposure_pct = float(portfolio_context["exposure_pct"])
    held_qty = float(portfolio_context["sol_qty"])
    unrealized_pct = float(portfolio_context["unrealized_pnl_pct"])
    holding_hours = float(portfolio_context.get("holding_hours", 0.0))
    circuit_breaker = bool(portfolio_context["daily_circuit_breaker_active"])
    sl_cooldown = bool(portfolio_context.get("sl_cooldown_active", False))
    same_candle_traded = bool(portfolio_context.get("same_candle_buy_blocked", False))

    # Tight ATR-calibrated short-term Stop-Loss & Take-Profit
    sl_pct = round(_clamp(max(DEFAULT_STOP_LOSS_PCT, atr_pct * 1.5), MIN_STOP_LOSS_PCT, MAX_STOP_LOSS_PCT), 2)
    tp_pct = round(_clamp(max(DEFAULT_TAKE_PROFIT_PCT, atr_pct * 2.6), MIN_TAKE_PROFIT_PCT, MAX_TAKE_PROFIT_PCT), 2)

    action = "HOLD"
    order_pct = 0.0
    confidence = int(_clamp(54 + abs(score) * 0.38, 42, 90))
    headline = f"{regime_kr} — 관망 유지"

    can_buy = (
        snapshot.market_open
        and (not circuit_breaker)
        and (not sl_cooldown)
        and (not same_candle_traded)
        and (exposure_pct < (MAX_POSITION_RATIO * 100.0 - 8.0))
    )
    has_position = held_qty > 1e-8 and exposure_pct >= 2.0

    # Short-term Time-Stop: flat stagnation for >= 12h without bullish momentum
    time_stop_triggered = (
        snapshot.market_open
        and has_position
        and holding_hours >= TIME_STOP_HOURS
        and (-0.8 <= unrealized_pct <= 0.8)
        and score < 15.0
    )

    # Oversold bounce sniping condition
    oversold_bounce = (rsi14 <= 35.0 and rsi14 > rsi_prev and bb_b <= 0.22 and macd_delta > 0)

    if not snapshot.market_open:
        action = "HOLD"
        order_pct = 0.0
        headline = f"{snapshot.market_status_label} — 지표 점수 {score:+.1f}점 (개장 대기)"
    elif time_stop_triggered:
        action = "SELL"
        order_pct = 100.0
        confidence = 72
        headline = f"{holding_hours:.0f}시간 횡보 정체 — 단타 시간 손절 전량 청산"
    elif has_position and (
        score <= -22.0
        or (fast_align == "bearish" and macd_delta < 0 and score <= -12.0)
        or (rsi14 >= 70.0 and unrealized_pct >= 1.2)
    ):
        action = "SELL"
        if score <= -38.0 or (fast_align == "bearish" and dist_vwap12 < -0.25):
            order_pct = 100.0
            headline = f"단기 추세 이탈(점수 {score:+.1f}) — 보유 물량 전량 청산"
        elif rsi14 >= 70.0 and unrealized_pct >= 1.2:
            order_pct = 50.0
            headline = f"단기 과열(RSI {rsi14:.1f} · 수익 {unrealized_pct:+.1f}%) — 50% 분할 익절"
        else:
            order_pct = 60.0
            headline = f"단기 모멘텀 둔화(점수 {score:+.1f}) — 보유 비중 60% 축소"
        confidence = int(_clamp(62 + abs(score) * 0.35, 58, 92))
    elif can_buy and ((score >= 24.0 and rsi14 < 68.0 and spot >= ema13 * 0.998) or oversold_bounce):
        action = "BUY"
        if score >= 45.0 and fast_align == "bullish":
            order_pct = 50.0
            headline = f"단기 상승 돌파(점수 {score:+.1f}) — 가용 현금 50% 진입"
        elif oversold_bounce and score < 24.0:
            order_pct = 30.0
            sl_pct = round(_clamp(min(sl_pct, 2.0), MIN_STOP_LOSS_PCT, MAX_STOP_LOSS_PCT), 2)
            headline = f"과매도 반등 포착(RSI {rsi14:.1f}↑) — 30% 단기 스나이핑"
        else:
            order_pct = 35.0
            headline = f"단기 추세 전환(점수 {score:+.1f}) — 가용 현금 35% 분할 매수"
        confidence = int(_clamp(60 + max(0.0, score) * 0.38, 56, 91))
    else:
        action = "HOLD"
        order_pct = 0.0
        if circuit_breaker:
            headline = "일일 손실 서킷브레이커 발동 — 신규 매수 차단"
        elif sl_cooldown:
            headline = "장중 손절 직후 1회차 쿨다운 — 관망 유지"
        elif same_candle_traded:
            headline = "동일 1시간봉 중복 진입 방지 — 기존 포지션 점검"
        elif has_position:
            headline = f"단타 포지션 유지({exposure_pct:.1f}% · {unrealized_pct:+.2f}%) — 추세 추적"
        elif score <= -15.0:
            headline = f"단기 약세 구간(점수 {score:+.1f}) — 현금 관망 유지"
        elif score >= 14.0:
            headline = f"매수 임계치(+24) 근접(점수 {score:+.1f}) — 진입 대기"
        else:
            headline = f"단기 방향성 혼조(점수 {score:+.1f}) — 거래비용 절감 관망"

    fast_kr = {"bullish": "강세(EMA5>13)", "bearish": "약세(EMA5<13)", "mixed": "혼조"}[fast_align]
    htf_kr = {"uptrend": "상승", "downtrend": "하락", "sideways": "횡보"}[htf_trend]
    macd_dir = "개선" if macd_delta >= 0 else "둔화"
    rt_cost_pct = (cfg.buy_fee_rate + cfg.sell_fee_rate + cfg.base_slippage_rate * 2.0) * 100.0

    if not snapshot.market_open:
        action_sentence = (
            f"현재 {snapshot.market_status_label} 상태이므로 주문을 실행하지 않고 최신 종가 기준 지표 점수({score:+.1f}점)만 갱신하며 다음 개장을 대기합니다."
        )
    elif action == "BUY":
        action_sentence = (
            f"단기 퀀트 점수 {score:+.1f}점(EMA5 {format_price(ema5, cfg)} / EMA13 {format_price(ema13, cfg)})으로 "
            f"왕복 거래비용(약 {rt_cost_pct:.2f}%)을 감안한 진입 기준을 충족해 가용 현금의 {order_pct:.0f}%를 매수하며, "
            f"손절 -{sl_pct:.1f}% / 익절 +{tp_pct:.1f}%를 설정합니다."
        )
    elif action == "SELL":
        if time_stop_triggered:
            action_sentence = (
                f"진입 후 {holding_hours:.1f}시간 동안 방향성 없는 정체({unrealized_pct:+.2f}%)가 지속되어 "
                f"단타 시간 손절 규칙에 따라 전량 현금화합니다."
            )
        else:
            action_sentence = (
                f"단기 모멘텀 약화 또는 과열 신호(점수 {score:+.1f}, RSI {rsi14:.1f})가 확인되어 "
                f"보유 {cfg.short_name}의 {order_pct:.0f}%를 매도해 수익 보존 및 손실 제한에 나섭니다."
            )
    elif not has_position and score <= -15.0:
        action_sentence = (
            f"단기 퀀트 점수({score:+.1f}점)가 약세 구간에 머물러 성급한 역추세 진입을 피하고 전액 현금 관망을 유지합니다."
        )
    else:
        action_sentence = (
            f"단기 퀀트 점수({score:+.1f}점)가 매수(+24)·매도(-22) 임계치 사이에 위치해 불필요한 거래비용 소모를 막고 현 포지션을 유지합니다."
        )

    briefing_summary = (
        f"{cfg.name}({cfg.short_name}) 현재가는 {format_price(spot, cfg)}(변동 {snapshot.change_24h_pct:+.2f}%)이며 {regime_kr} 흐름입니다. "
        f"단기 EMA5/13은 {fast_kr}, 12h VWAP 대비 {dist_vwap12:+.2f}%, RSI(14)는 {rsi14:.1f}(직전 {rsi_prev:.1f})입니다. "
        f"{action_sentence}"
    )

    key_factors = [
        f"초단기 추세: EMA5 {format_price(ema5, cfg)} / EMA13 {format_price(ema13, cfg)} ({fast_kr}) · 4h 중기 {htf_kr}",
        f"단기 모멘텀: Fast MACD(8,17,6) 히스토그램 {macd_hist:+.2f}({macd_dir}), RSI(14) {rsi14:.1f}",
        f"장중 기준가: 12h VWAP {format_price(vwap12, cfg)} ({dist_vwap12:+.2f}%), 12h 채널 {format_price(dc_low, cfg)} ~ {format_price(dc_high, cfg)}",
        f"수급·변동성: 거래량 평균 대비 {vol_ratio:.2f}배, ATR(14) {atr_pct:.2f}%, 호가 스프레드 {indicators['spread_bps']:.1f}bp",
    ]

    risk_notes = (
        f"12시간 단기 지지선은 {format_price(dc_low, cfg)}, 저항선은 {format_price(dc_high, cfg)}입니다. "
        f"시간당 변동성(ATR {atr_pct:.2f}%)과 왕복 비용(~{rt_cost_pct:.2f}%)을 감안해 수익 +1.2% 도달 시 본절 트레일링 보호가 자동 가동됩니다."
    )
    next_watch = (
        f"다음 회차에는 EMA13({format_price(ema13, cfg)}) 및 12h VWAP({format_price(vwap12, cfg)}) 지지·돌파 여부와 "
        f"단기 저항 {format_price(dc_high, cfg)} 테스트를 관찰합니다."
    )

    return TradingDecision(
        action=action,
        order_pct=order_pct,
        confidence=confidence,
        stop_loss_pct=sl_pct,
        take_profit_pct=tp_pct,
        headline=headline,
        briefing_summary=briefing_summary,
        key_factors=key_factors,
        risk_notes=risk_notes,
        next_watch=next_watch,
    )
