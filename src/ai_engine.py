"""AI trading decision engine using Gemini REST API with deterministic Quant Fallback."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from .config import (
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TAKE_PROFIT_PCT,
    FEE_RATE,
    GEMINI_API_BASE,
    GEMINI_FALLBACK_MODELS,
    HTTP_TIMEOUT_SECONDS,
    MAX_POSITION_RATIO,
    MAX_STOP_LOSS_PCT,
    MAX_TAKE_PROFIT_PCT,
    MIN_STOP_LOSS_PCT,
    MIN_TAKE_PROFIT_PCT,
)
from .market_data import MarketSnapshot


@dataclass
class TradingDecision:
    action: str  # "BUY" | "SELL" | "HOLD"
    order_pct: float  # BUY: % of available KRW cash (0..100); SELL: % of held SOL (0..100); HOLD: 0
    confidence: int  # 0..100
    stop_loss_pct: float  # 1.5..5.0
    take_profit_pct: float  # 2.0..10.0
    headline: str
    briefing_summary: str
    key_factors: list[str]
    risk_notes: str
    next_watch: str
    engine: str  # "gemini" | "quant_fallback"
    model_name: str
    fallback_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DECISION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["BUY", "SELL", "HOLD"],
            "description": "Trading action for this 1-hour cycle.",
        },
        "order_pct": {
            "type": "number",
            "description": "For BUY: percent of available KRW cash to allocate (0-100). For SELL: percent of held SOL to sell (0-100). For HOLD: 0.",
        },
        "confidence": {
            "type": "integer",
            "description": "Confidence score from 0 to 100.",
        },
        "stop_loss_pct": {
            "type": "number",
            "description": "Stop-loss percentage below entry price (1.5 to 5.0).",
        },
        "take_profit_pct": {
            "type": "number",
            "description": "Take-profit percentage above entry price (2.0 to 10.0).",
        },
        "headline": {
            "type": "string",
            "description": "Concise Korean one-line headline under 45 characters summarizing the decision.",
        },
        "briefing_summary": {
            "type": "string",
            "description": "2-3 sentences Korean hourly market briefing explaining price action, indicators, and decision rationale.",
        },
        "key_factors": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3 to 4 concise Korean bullet strings citing exact numeric indicators.",
        },
        "risk_notes": {
            "type": "string",
            "description": "1-2 sentences Korean risk assessment and thesis invalidation level.",
        },
        "next_watch": {
            "type": "string",
            "description": "1 sentence Korean outlook specifying key price or indicator levels to watch next hour.",
        },
    },
    "required": [
        "action",
        "order_pct",
        "confidence",
        "stop_loss_pct",
        "take_profit_pct",
        "headline",
        "briefing_summary",
        "key_factors",
        "risk_notes",
        "next_watch",
    ],
}


SYSTEM_INSTRUCTION = f"""너는 업비트 KRW-SOL 현물 시장을 1시간 주기로 운용하는 규율 있는 퀀트 트레이더 겸 브리핑 애널리스트다.
이 계좌는 초기 시드 1,000만 원의 모의투자 시뮬레이션 계좌이며, 다음의 엄격한 원칙을 지켜야 한다:

1. 수수료({FEE_RATE * 100:.2f}%)와 슬리피지가 매 거래마다 발생한다. 1시간봉 단타에서는 잦은 뇌동매매가 가장 큰 손실 원인이다. 방향성 우위가 불분명하거나 횡보 노이즈 구간이면 과감하게 `HOLD`(관망)를 선택하라.
2. 현물 전용이며 공매도/레버리지는 불가능하다. SOL을 보유하고 있지 않을 때는 `SELL`을 선택하지 말고 `HOLD` 또는 `BUY`를 선택하라.
3. 총자산 대비 최대 SOL 노출 한도는 {int(MAX_POSITION_RATIO * 100)}%다. 분할 진입/분할 익절을 선호하고, 확실한 근거 없이 한 번에 100% 몰빵하지 마라.
4. `daily_circuit_breaker_active`가 true이거나 `sl_cooldown_active`가 true이면 신규 `BUY`를 금지하고 `HOLD` 또는 `SELL`만 검토하라.
5. 모든 텍스트 필드(`headline`, `briefing_summary`, `key_factors`, `risk_notes`, `next_watch`)는 한국어로 명확하고 객관적인 금융 리포트 톤으로 작성하라. 추상적 미사여구 대신 실제 수치(원화 가격, RSI, EMA, MACD, 4시간봉 추세, 거래량 배수 등)를 근거로 인용하라.
6. 반드시 지정된 JSON 스키마만 출력하라."""


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _format_krw(value: float) -> str:
    return f"{round(value):,}원"


def _sanitize_decision(
    raw: dict[str, Any],
    *,
    engine: str,
    model_name: str,
    fallback_reason: str | None = None,
) -> TradingDecision:
    action = str(raw.get("action", "HOLD")).strip().upper()
    if action not in {"BUY", "SELL", "HOLD"}:
        action = "HOLD"

    try:
        order_pct = float(raw.get("order_pct", 0.0))
    except (TypeError, ValueError):
        order_pct = 0.0
    order_pct = 0.0 if action == "HOLD" else round(_clamp(order_pct, 0.0, 100.0), 1)

    try:
        confidence = int(round(float(raw.get("confidence", 50))))
    except (TypeError, ValueError):
        confidence = 50
    confidence = int(_clamp(confidence, 0, 100))

    try:
        sl_pct = float(raw.get("stop_loss_pct", DEFAULT_STOP_LOSS_PCT))
    except (TypeError, ValueError):
        sl_pct = DEFAULT_STOP_LOSS_PCT
    sl_pct = round(_clamp(sl_pct, MIN_STOP_LOSS_PCT, MAX_STOP_LOSS_PCT), 2)

    try:
        tp_pct = float(raw.get("take_profit_pct", DEFAULT_TAKE_PROFIT_PCT))
    except (TypeError, ValueError):
        tp_pct = DEFAULT_TAKE_PROFIT_PCT
    tp_pct = round(_clamp(tp_pct, MIN_TAKE_PROFIT_PCT, MAX_TAKE_PROFIT_PCT), 2)

    factors = raw.get("key_factors") or []
    if not isinstance(factors, list):
        factors = [str(factors)]
    clean_factors = [str(f).strip() for f in factors if str(f).strip()][:5]
    if not clean_factors:
        clean_factors = ["주요 기술적 지표 혼조세로 관망 기준 적용"]

    return TradingDecision(
        action=action,
        order_pct=order_pct,
        confidence=confidence,
        stop_loss_pct=sl_pct,
        take_profit_pct=tp_pct,
        headline=str(raw.get("headline", "SOL/KRW 정각 시황 점검")).strip()[:80],
        briefing_summary=str(raw.get("briefing_summary", "")).strip(),
        key_factors=clean_factors,
        risk_notes=str(raw.get("risk_notes", "")).strip(),
        next_watch=str(raw.get("next_watch", "")).strip(),
        engine=engine,
        model_name=model_name,
        fallback_reason=fallback_reason,
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Model response did not contain a valid JSON object.")


def _call_gemini_model(api_key: str, model_name: str, user_payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{GEMINI_API_BASE}/{model_name}:generateContent"
    prompt_text = (
        "아래 실시간 KRW-SOL 1시간봉/4시간봉 시장 데이터와 모의투자 포트폴리오 상태를 분석하여 "
        "이번 정각의 매매 결정과 한국어 브리핑을 JSON으로 작성하라.\n\n"
        + json.dumps(user_payload, ensure_ascii=False)
    )

    # Note: Gemini 3.6+/3.8 models deprecated temperature/topP/topK and thinkingBudget.
    # Use systemInstruction + thinkingConfig.thinkingLevel + responseJsonSchema.
    body: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": DECISION_JSON_SCHEMA,
            "maxOutputTokens": 2048,
            "thinkingConfig": {"thinkingLevel": "LOW"},
        },
    }

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
        raw_resp = json.loads(resp.read().decode("utf-8"))

    candidates = raw_resp.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {raw_resp}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    # Skip thought parts if any; extract final text part
    text_parts = [p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought") and p.get("text")]
    if not text_parts:
        text_parts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]
    combined = "\n".join(text_parts).strip()
    if not combined:
        raise RuntimeError(f"Gemini candidate contained empty text: {candidates[0]}")
    return _extract_json_object(combined)


def decide_quant_fallback(
    snapshot: MarketSnapshot,
    indicators: dict[str, Any],
    portfolio_context: dict[str, Any],
    *,
    fallback_reason: str | None = None,
) -> TradingDecision:
    """Deterministic multi-factor technical strategy with transparent Korean briefing."""
    spot = snapshot.spot_price
    score = float(indicators["quant_score"])
    rsi14 = float(indicators["rsi14"])
    rsi_prev = float(indicators["rsi14_prev"])
    ema_align = str(indicators["ema_alignment"])
    htf_trend = str(indicators["htf_trend"])
    bb_b = float(indicators["bb_percent_b"])
    atr_pct = float(indicators["atr14_pct"])
    vol_ratio = float(indicators["volume_ratio_24h"])
    macd_hist = float(indicators["macd_hist"])
    macd_delta = float(indicators["macd_hist_delta"])
    support = float(indicators["support_48h"])
    resistance = float(indicators["resistance_48h"])
    regime_kr = str(indicators["regime_kr"])

    exposure_pct = float(portfolio_context["exposure_pct"])
    sol_qty = float(portfolio_context["sol_qty"])
    unrealized_pct = float(portfolio_context["unrealized_pnl_pct"])
    circuit_breaker = bool(portfolio_context["daily_circuit_breaker_active"])
    sl_cooldown = bool(portfolio_context.get("sl_cooldown_active", False))

    # Dynamic ATR-calibrated stop-loss and take-profit
    sl_pct = round(_clamp(max(DEFAULT_STOP_LOSS_PCT, atr_pct * 1.8), MIN_STOP_LOSS_PCT, MAX_STOP_LOSS_PCT), 2)
    tp_pct = round(_clamp(max(DEFAULT_TAKE_PROFIT_PCT, atr_pct * 3.0), MIN_TAKE_PROFIT_PCT, MAX_TAKE_PROFIT_PCT), 2)

    action = "HOLD"
    order_pct = 0.0
    confidence = int(_clamp(52 + abs(score) * 0.4, 40, 88))
    headline = f"{regime_kr} 구간 — 관망 유지"

    can_buy = (not circuit_breaker) and (not sl_cooldown) and (exposure_pct < (MAX_POSITION_RATIO * 100.0 - 8.0))
    has_position = sol_qty > 1e-8 and exposure_pct >= 2.0

    if has_position and (score <= -28.0 or (rsi14 >= 74.0 and unrealized_pct >= 1.2)):
        action = "SELL"
        if score <= -45.0 or (htf_trend == "downtrend" and ema_align == "bearish"):
            order_pct = 100.0
            headline = "추세 약화 신호 확인 — 보유 물량 전량 청산"
        elif rsi14 >= 74.0 and unrealized_pct >= 1.2:
            order_pct = 50.0
            headline = f"단기 과매수(RSI {rsi14:.1f}) 도달 — 50% 분할 익절"
        else:
            order_pct = 60.0
            headline = "하방 압력 확대 — 보유 비중 60% 축소"
        confidence = int(_clamp(60 + abs(score) * 0.35, 55, 90))
    elif can_buy and score >= 30.0 and rsi14 < 68.0:
        action = "BUY"
        if score >= 52.0 and htf_trend == "uptrend":
            order_pct = 55.0
            headline = "1h·4h 추세 동조 상승 — 가용 현금 55% 매수"
        elif rsi14 <= 38.0 and bb_b <= 0.25:
            order_pct = 35.0
            headline = f"단기 과매도 반등(RSI {rsi14:.1f}) — 35% 분할 매수"
        else:
            order_pct = 35.0
            headline = "상승 모멘텀 우위 — 가용 현금 35% 분할 진입"
        confidence = int(_clamp(58 + score * 0.38, 55, 89))
    else:
        action = "HOLD"
        order_pct = 0.0
        if circuit_breaker:
            headline = "일일 손실 한도 도달 — 신규 매수 차단 및 관망"
        elif sl_cooldown:
            headline = "직전 손절 체결 직후 — 과매매 방지 1시간 쿨다운"
        elif has_position:
            headline = f"포지션 유지({exposure_pct:.1f}%) — 추세 지속 여부 점검"
        else:
            headline = f"방향성 모호(점수 {score:+.1f}) — 현금 관망 유지"

    align_kr = {"bullish": "정배열(EMA9>21>50)", "bearish": "역배열(EMA9<21<50)", "mixed": "혼조 배열"}[ema_align]
    htf_kr = {"uptrend": "상승 추세", "downtrend": "하락 추세", "sideways": "횡보"}[htf_trend]
    macd_dir = "개선" if macd_delta >= 0 else "둔화"

    if action == "BUY":
        action_sentence = (
            f"복합 정량 점수가 {score:+.1f}점으로 수수료·슬리피지 허들을 상회해 가용 현금의 {order_pct:.0f}%를 분할 매수하며, "
            f"손절 -{sl_pct:.1f}% / 익절 +{tp_pct:.1f}% 가드레일을 설정합니다."
        )
    elif action == "SELL":
        action_sentence = (
            f"단기 하방 압력 또는 과매수 부담(점수 {score:+.1f}, RSI {rsi14:.1f})이 커져 보유 SOL의 {order_pct:.0f}%를 매도해 리스크를 관리합니다."
        )
    else:
        action_sentence = (
            f"복합 정량 점수({score:+.1f}점)가 진입/청산 임계치에 미달하여 불필요한 수수료 소모를 피하고 현 포지션을 유지합니다."
        )

    briefing_summary = (
        f"현재 업비트 KRW-SOL은 {_format_krw(spot)}(24시간 {snapshot.change_24h_pct:+.2f}%)로 {regime_kr} 흐름을 보이고 있습니다. "
        f"1시간봉은 {align_kr}, 4시간 상위 추세는 {htf_kr}이며 RSI(14)는 {rsi14:.1f}(직전 {rsi_prev:.1f})입니다. "
        f"{action_sentence}"
    )

    key_factors = [
        f"추세 구조: 1시간봉 {align_kr} / 4시간봉 {htf_kr} (EMA21 {_format_krw(indicators['ema21'])})",
        f"모멘텀: RSI(14) {rsi14:.1f}, MACD 히스토그램 {macd_hist:+.1f} (직전 대비 {macd_dir})",
        f"변동성·밴드: 볼린저 %B {bb_b:.2f}, ATR(14) {atr_pct:.2f}%, 24h VWAP 대비 {indicators['dist_from_vwap_pct']:+.2f}%",
        f"수급·체결: 직전 1시간 거래량 평균 대비 {vol_ratio:.2f}배, 호가 불균형 {indicators['orderbook_imbalance']:+.2f}",
    ]

    risk_notes = (
        f"최근 48시간 단기 지지선은 {_format_krw(support)}, 저항선은 {_format_krw(resistance)}입니다. "
        f"시간당 변동성(ATR {atr_pct:.2f}%)을 감안해 지지선 이탈 시 추가 변동성 확대에 유의해야 합니다."
    )
    next_watch = (
        f"다음 1시간 동안 {_format_krw(indicators['ema21'])}(1h EMA21) 안착 여부와 "
        f"{_format_krw(resistance)} 돌파 시 거래량 동반 여부를 중점 관찰합니다."
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
        engine="quant_fallback",
        model_name="quant-multifactor-v1",
        fallback_reason=fallback_reason,
    )


def decide_trade(
    snapshot: MarketSnapshot,
    indicators: dict[str, Any],
    portfolio_context: dict[str, Any],
    recent_decisions: list[dict[str, Any]],
    *,
    force_quant: bool = False,
) -> TradingDecision:
    """Ask Gemini for the hourly decision; automatically fall back to Quant Engine on missing key or error."""
    if force_quant:
        return decide_quant_fallback(
            snapshot,
            indicators,
            portfolio_context,
            fallback_reason="사용자 옵션(force_quant)으로 정량 전략 실행",
        )

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return decide_quant_fallback(
            snapshot,
            indicators,
            portfolio_context,
            fallback_reason="GEMINI_API_KEY 미설정 — 정량 멀티팩터 전략으로 자동 실행",
        )

    recent_candles_summary = [
        {
            "kst": c.time_kst[5:16],
            "o": round(c.open),
            "h": round(c.high),
            "l": round(c.low),
            "c": round(c.close),
            "vol": round(c.volume, 1),
        }
        for c in snapshot.candles_1h[-12:]
    ]

    user_payload = {
        "timestamp_kst": snapshot.fetched_at_kst,
        "market": snapshot.market,
        "spot_price_krw": snapshot.spot_price,
        "change_24h_pct": snapshot.change_24h_pct,
        "high_24h_krw": snapshot.high_24h,
        "low_24h_krw": snapshot.low_24h,
        "indicators": indicators,
        "recent_12h_candles": recent_candles_summary,
        "portfolio": portfolio_context,
        "recent_5h_decisions": recent_decisions[-5:],
    }

    errors: list[str] = []
    for model_name in GEMINI_FALLBACK_MODELS:
        try:
            raw = _call_gemini_model(api_key, model_name, user_payload)
            return _sanitize_decision(raw, engine="gemini", model_name=model_name)
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="ignore")[:180]
            except Exception:
                pass
            errors.append(f"{model_name}: HTTP {exc.code} {err_body}")
        except Exception as exc:
            errors.append(f"{model_name}: {type(exc).__name__} {exc}")

    return decide_quant_fallback(
        snapshot,
        indicators,
        portfolio_context,
        fallback_reason=f"Gemini API 호출 실패로 정량 전략 자동 폴백 ({'; '.join(errors)[:220]})",
    )
