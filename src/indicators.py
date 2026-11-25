"""Pure-Python technical indicators, higher-timeframe context, and market regime analysis."""

from __future__ import annotations

import math
import statistics
from typing import Any

from .market_data import Candle, MarketSnapshot


def sma(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    window = values[-period:] if len(values) >= period else values
    return sum(window) / len(window)


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    out = [values[0]]
    for val in values[1:]:
        out.append(val * k + out[-1] * (1.0 - k))
    return out


def ema(values: list[float], period: int) -> float:
    series = ema_series(values, period)
    return series[-1] if series else 0.0


def rsi_series(closes: list[float], period: int = 14) -> list[float]:
    """Wilder's smoothed RSI series."""
    if len(closes) < 2:
        return [50.0] * len(closes)

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    if len(deltas) < period:
        avg_g = sum(gains) / len(gains)
        avg_l = sum(losses) / len(losses)
        rs = (avg_g / avg_l) if avg_l > 0 else float("inf")
        val = 100.0 if math.isinf(rs) else (100.0 - 100.0 / (1.0 + rs))
        return [50.0] * (len(closes) - 1) + [val]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    out: list[float] = [50.0] * period
    for i in range(period, len(deltas)):
        if i == period:
            val = 100.0 if avg_loss == 0 else (100.0 - 100.0 / (1.0 + avg_gain / avg_loss))
            out.append(val)
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        val = 100.0 if avg_loss == 0 else (100.0 - 100.0 / (1.0 + avg_gain / avg_loss))
        out.append(val)

    # Ensure output matches closes length
    if len(out) < len(closes):
        val = 100.0 if avg_loss == 0 else (100.0 - 100.0 / (1.0 + avg_gain / avg_loss))
        out.append(val)
    return out[: len(closes)]


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, float]:
    if len(closes) < 2:
        return {"line": 0.0, "signal": 0.0, "hist": 0.0, "prev_hist": 0.0}
    fast_ema = ema_series(closes, fast)
    slow_ema = ema_series(closes, slow)
    macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
    signal_line = ema_series(macd_line, signal)
    hist = [m - s for m, s in zip(macd_line, signal_line)]
    return {
        "line": macd_line[-1],
        "signal": signal_line[-1],
        "hist": hist[-1],
        "prev_hist": hist[-2] if len(hist) >= 2 else hist[-1],
    }


def bollinger_bands(closes: list[float], period: int = 20, std_mult: float = 2.0) -> dict[str, float]:
    if not closes:
        return {"upper": 0.0, "mid": 0.0, "lower": 0.0, "width_pct": 0.0, "percent_b": 0.5}
    window = closes[-period:] if len(closes) >= period else closes
    mid = sum(window) / len(window)
    std = statistics.pstdev(window) if len(window) >= 2 else 0.0
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    width_pct = ((upper - lower) / mid * 100.0) if mid > 0 else 0.0
    denom = upper - lower
    percent_b = ((closes[-1] - lower) / denom) if denom > 0 else 0.5
    return {
        "upper": upper,
        "mid": mid,
        "lower": lower,
        "width_pct": width_pct,
        "percent_b": percent_b,
    }


def atr(candles: list[Candle], period: int = 14) -> tuple[float, float]:
    if len(candles) < 2:
        return 0.0, 0.0
    trs: list[float] = []
    for i in range(1, len(candles)):
        cur = candles[i]
        prev_close = candles[i - 1].close
        tr = max(
            cur.high - cur.low,
            abs(cur.high - prev_close),
            abs(cur.low - prev_close),
        )
        trs.append(tr)

    if len(trs) <= period:
        val = sum(trs) / len(trs)
    else:
        val = sum(trs[:period]) / period
        for tr in trs[period:]:
            val = (val * (period - 1) + tr) / period

    last_close = candles[-1].close
    atr_pct = (val / last_close * 100.0) if last_close > 0 else 0.0
    return val, atr_pct


def vwap_rolling(candles: list[Candle], window: int = 24) -> float:
    subset = candles[-window:] if len(candles) >= window else candles
    total_vol = sum(c.volume for c in subset)
    if total_vol <= 0:
        return subset[-1].close if subset else 0.0
    pv = sum(((c.high + c.low + c.close) / 3.0) * c.volume for c in subset)
    return pv / total_vol


def _pct_return(series: list[float], lookback: int, current_price: float) -> float:
    if not series:
        return 0.0
    idx = max(0, len(series) - lookback)
    base = series[idx]
    return ((current_price / base) - 1.0) * 100.0 if base > 0 else 0.0


def compute_indicators(snapshot: MarketSnapshot) -> dict[str, Any]:
    """Compute a comprehensive 1h + 4h technical indicator pack and composite quant score."""
    candles_1h = snapshot.candles_1h
    candles_4h = snapshot.candles_4h
    if not candles_1h:
        raise ValueError("Cannot compute indicators without 1h candles.")

    closes_1h = [c.close for c in candles_1h]
    spot = snapshot.spot_price
    last_closed = candles_1h[-1]

    # Moving averages on 1h closed bars
    ema9 = ema(closes_1h, 9)
    ema21 = ema(closes_1h, 21)
    ema50 = ema(closes_1h, 50)
    ema200 = ema(closes_1h, 200) if len(closes_1h) >= 50 else ema50

    if ema9 > ema21 > ema50:
        ema_alignment = "bullish"
    elif ema9 < ema21 < ema50:
        ema_alignment = "bearish"
    else:
        ema_alignment = "mixed"

    # Momentum & volatility
    rsi_vals = rsi_series(closes_1h, 14)
    rsi14 = rsi_vals[-1]
    rsi14_prev = rsi_vals[-2] if len(rsi_vals) >= 2 else rsi14
    macd_data = macd(closes_1h)
    bb_data = bollinger_bands(closes_1h, 20, 2.0)
    atr14, atr14_pct = atr(candles_1h, 14)

    # VWAP & Volume flow
    vwap24 = vwap_rolling(candles_1h, 24)
    dist_vwap_pct = ((spot / vwap24) - 1.0) * 100.0 if vwap24 > 0 else 0.0

    vol_window = [c.volume for c in candles_1h[-25:-1]] if len(candles_1h) > 25 else [c.volume for c in candles_1h[:-1]]
    avg_vol = (sum(vol_window) / len(vol_window)) if vol_window else last_closed.volume
    vol_ratio = (last_closed.volume / avg_vol) if avg_vol > 0 else 1.0
    vol_std = statistics.pstdev(vol_window) if len(vol_window) >= 2 else 0.0
    vol_zscore = ((last_closed.volume - avg_vol) / vol_std) if vol_std > 0 else 0.0

    # Support / resistance over last 48 closed 1h bars
    sr_slice = candles_1h[-48:] if len(candles_1h) >= 48 else candles_1h
    support_48h = min(c.low for c in sr_slice)
    resistance_48h = max(c.high for c in sr_slice)

    # Higher-timeframe (4h) trend context
    closes_4h = [c.close for c in candles_4h] if candles_4h else closes_1h
    htf_ema21 = ema(closes_4h, 21)
    htf_ema50 = ema(closes_4h, 50)
    htf_rsi14 = rsi_series(closes_4h, 14)[-1]
    htf_spread_pct = ((htf_ema21 / htf_ema50) - 1.0) * 100.0 if htf_ema50 > 0 else 0.0
    if htf_spread_pct > 0.35 and spot > htf_ema21:
        htf_trend = "uptrend"
    elif htf_spread_pct < -0.35 and spot < htf_ema21:
        htf_trend = "downtrend"
    else:
        htf_trend = "sideways"

    # Returns across horizons
    ret_1h = _pct_return(closes_1h, 1, spot)
    ret_4h = _pct_return(closes_1h, 4, spot)
    ret_24h = snapshot.change_24h_pct
    ret_72h = _pct_return(closes_1h, 72, spot)

    # Market Regime Classification
    if atr14_pct >= 2.2 or bb_data["width_pct"] >= 9.0:
        regime = "high_volatility"
        regime_kr = "고변동성 장세"
    elif ema_alignment == "bullish" and htf_trend == "uptrend":
        regime = "uptrend"
        regime_kr = "상승 추세"
    elif ema_alignment == "bearish" and htf_trend == "downtrend":
        regime = "downtrend"
        regime_kr = "하락 추세"
    elif ema_alignment == "bullish":
        regime = "rebound"
        regime_kr = "단기 반등"
    elif ema_alignment == "bearish":
        regime = "pullback"
        regime_kr = "단기 조정"
    else:
        regime = "range"
        regime_kr = "박스권 횡보"

    # Composite Multi-Factor Quant Score (-100 to +100)
    score = 0.0
    # 1) 1h EMA Trend (max +/- 24)
    if ema_alignment == "bullish":
        score += 18.0
    elif ema_alignment == "bearish":
        score -= 18.0
    if spot > ema21:
        score += 6.0
    else:
        score -= 6.0

    # 2) 4h Higher-Timeframe Alignment (max +/- 20)
    if htf_trend == "uptrend":
        score += 20.0
    elif htf_trend == "downtrend":
        score -= 20.0

    # 3) MACD Momentum & Turning Point (max +/- 18)
    hist_delta = macd_data["hist"] - macd_data["prev_hist"]
    if macd_data["hist"] > 0 and hist_delta > 0:
        score += 18.0
    elif macd_data["hist"] > 0:
        score += 8.0
    elif macd_data["hist"] < 0 and hist_delta < 0:
        score -= 18.0
    elif macd_data["hist"] < 0 and hist_delta > 0:
        score += 5.0  # Bearish momentum waning

    # 4) RSI Mean Reversion / Exhaustion (regime-aware to avoid catching falling knives, max +/- 18)
    if rsi14 <= 32.0:
        if htf_trend == "downtrend" and ema_alignment == "bearish":
            # In confirmed downtrends, oversold can stay oversold; only slight credit if turning up
            score += 4.0 if rsi14 > rsi14_prev else -4.0
        else:
            score += 18.0 if rsi14 >= rsi14_prev else 10.0
    elif rsi14 <= 42.0 and rsi14 > rsi14_prev and htf_trend != "downtrend":
        score += 10.0
    elif rsi14 >= 74.0:
        if htf_trend == "uptrend" and ema_alignment == "bullish":
            score -= 6.0 if rsi14 < rsi14_prev else 2.0
        else:
            score -= 18.0
    elif rsi14 >= 66.0 and rsi14 < rsi14_prev and htf_trend != "uptrend":
        score -= 10.0

    # 5) Bollinger Band Position & VWAP (max +/- 12)
    if bb_data["percent_b"] < 0.15 and htf_trend != "downtrend":
        score += 8.0
    elif bb_data["percent_b"] > 0.90 and htf_trend != "uptrend":
        score -= 8.0
    if dist_vwap_pct > 0.25:
        score += 4.0
    elif dist_vwap_pct < -0.25:
        score -= 4.0

    # 6) Volume & Orderbook Flow Confirmation (max +/- 8)
    if vol_ratio >= 1.35:
        score += 5.0 if last_closed.close >= last_closed.open else -5.0
    score += max(-3.0, min(3.0, snapshot.orderbook_imbalance * 6.0))

    quant_score = round(max(-100.0, min(100.0, score)), 1)

    return {
        "spot_price": round(spot, 2),
        "last_closed_candle_kst": last_closed.time_kst,
        "last_closed_close": round(last_closed.close, 2),
        "ret_1h_pct": round(ret_1h, 2),
        "ret_4h_pct": round(ret_4h, 2),
        "ret_24h_pct": round(ret_24h, 2),
        "ret_72h_pct": round(ret_72h, 2),
        "ema9": round(ema9, 1),
        "ema21": round(ema21, 1),
        "ema50": round(ema50, 1),
        "ema200": round(ema200, 1),
        "ema_alignment": ema_alignment,
        "rsi14": round(rsi14, 2),
        "rsi14_prev": round(rsi14_prev, 2),
        "macd_line": round(macd_data["line"], 2),
        "macd_signal": round(macd_data["signal"], 2),
        "macd_hist": round(macd_data["hist"], 2),
        "macd_hist_delta": round(hist_delta, 2),
        "bb_upper": round(bb_data["upper"], 1),
        "bb_mid": round(bb_data["mid"], 1),
        "bb_lower": round(bb_data["lower"], 1),
        "bb_width_pct": round(bb_data["width_pct"], 2),
        "bb_percent_b": round(bb_data["percent_b"], 3),
        "atr14": round(atr14, 1),
        "atr14_pct": round(atr14_pct, 2),
        "vwap_24h": round(vwap24, 1),
        "dist_from_vwap_pct": round(dist_vwap_pct, 2),
        "volume_ratio_24h": round(vol_ratio, 2),
        "volume_zscore_24h": round(vol_zscore, 2),
        "spread_bps": round(snapshot.spread_bps, 2),
        "orderbook_imbalance": round(snapshot.orderbook_imbalance, 3),
        "support_48h": round(support_48h, 1),
        "resistance_48h": round(resistance_48h, 1),
        "htf_ema21": round(htf_ema21, 1),
        "htf_ema50": round(htf_ema50, 1),
        "htf_rsi14": round(htf_rsi14, 2),
        "htf_trend": htf_trend,
        "regime": regime,
        "regime_kr": regime_kr,
        "quant_score": quant_score,
    }
