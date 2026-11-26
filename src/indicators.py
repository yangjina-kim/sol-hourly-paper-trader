"""Pure-Python short-term technical indicators and intraday multi-factor scoring."""

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

    if len(out) < len(closes):
        val = 100.0 if avg_loss == 0 else (100.0 - 100.0 / (1.0 + avg_gain / avg_loss))
        out.append(val)
    return out[: len(closes)]


def macd(closes: list[float], fast: int = 8, slow: int = 17, signal: int = 6) -> dict[str, float]:
    """Fast intraday MACD(8, 17, 6) tuned for 1-hour short-term momentum turns."""
    if len(closes) < 2:
        return {"line": 0.0, "signal": 0.0, "hist": 0.0, "prev_hist": 0.0, "prev2_hist": 0.0}
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
        "prev2_hist": hist[-3] if len(hist) >= 3 else (hist[-2] if len(hist) >= 2 else hist[-1]),
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


def vwap_rolling(candles: list[Candle], window: int = 12) -> float:
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
    """Compute 1h short-term trading indicators and fast intraday quant score (-100..+100)."""
    candles_1h = snapshot.candles_1h
    candles_4h = snapshot.candles_4h
    if not candles_1h:
        raise ValueError("Cannot compute indicators without 1h candles.")

    closes_1h = [c.close for c in candles_1h]
    spot = snapshot.spot_price
    last_closed = candles_1h[-1]

    # Fast intraday moving averages (EMA5/EMA13) + reference EMAs (9/21/50/200)
    ema5_list = ema_series(closes_1h, 5)
    ema13_list = ema_series(closes_1h, 13)
    ema5 = ema5_list[-1]
    ema5_prev = ema5_list[-2] if len(ema5_list) >= 2 else ema5
    ema13 = ema13_list[-1]

    ema9 = ema(closes_1h, 9)
    ema21 = ema(closes_1h, 21)
    ema50 = ema(closes_1h, 50)
    ema200 = ema(closes_1h, 200) if len(closes_1h) >= 50 else ema50

    if ema5 > ema13 and spot > ema13:
        fast_alignment = "bullish"
    elif ema5 < ema13 and spot < ema13:
        fast_alignment = "bearish"
    else:
        fast_alignment = "mixed"

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
    macd_data = macd(closes_1h, fast=8, slow=17, signal=6)
    bb_data = bollinger_bands(closes_1h, 20, 2.0)
    atr14, atr14_pct = atr(candles_1h, 14)

    # Intraday 12h VWAP & 24h VWAP
    vwap12 = vwap_rolling(candles_1h, 12)
    dist_vwap12_pct = ((spot / vwap12) - 1.0) * 100.0 if vwap12 > 0 else 0.0
    vwap24 = vwap_rolling(candles_1h, 24)
    dist_vwap24_pct = ((spot / vwap24) - 1.0) * 100.0 if vwap24 > 0 else 0.0

    # 12h Donchian channel (intraday breakout / breakdown reference)
    dc_slice = candles_1h[-13:-1] if len(candles_1h) >= 13 else candles_1h[:-1] or candles_1h
    donchian_high_12h = max(c.high for c in dc_slice)
    donchian_low_12h = min(c.low for c in dc_slice)

    # Volume flow
    vol_window = [c.volume for c in candles_1h[-25:-1]] if len(candles_1h) > 25 else [c.volume for c in candles_1h[:-1]]
    avg_vol = (sum(vol_window) / len(vol_window)) if vol_window else last_closed.volume
    vol_ratio = (last_closed.volume / avg_vol) if avg_vol > 0 else 1.0
    vol_std = statistics.pstdev(vol_window) if len(vol_window) >= 2 else 0.0
    vol_zscore = ((last_closed.volume - avg_vol) / vol_std) if vol_std > 0 else 0.0

    # Support / resistance over last 48 closed 1h bars
    sr_slice = candles_1h[-48:] if len(candles_1h) >= 48 else candles_1h
    support_48h = min(c.low for c in sr_slice)
    resistance_48h = max(c.high for c in sr_slice)

    # Higher-timeframe (4h) context (lightweight directional filter)
    closes_4h = [c.close for c in candles_4h] if candles_4h else closes_1h
    htf_ema21 = ema(closes_4h, 21)
    htf_ema50 = ema(closes_4h, 50)
    htf_rsi14 = rsi_series(closes_4h, 14)[-1]
    htf_spread_pct = ((htf_ema21 / htf_ema50) - 1.0) * 100.0 if htf_ema50 > 0 else 0.0
    if htf_spread_pct > 0.30 and spot > htf_ema21:
        htf_trend = "uptrend"
    elif htf_spread_pct < -0.30 and spot < htf_ema21:
        htf_trend = "downtrend"
    else:
        htf_trend = "sideways"

    # Returns across horizons
    ret_1h = _pct_return(closes_1h, 1, spot)
    ret_4h = _pct_return(closes_1h, 4, spot)
    ret_24h = snapshot.change_24h_pct
    ret_72h = _pct_return(closes_1h, 72, spot)

    # Short-Term Market Regime Classification
    if atr14_pct >= 2.2 or bb_data["width_pct"] >= 9.0:
        regime = "high_volatility"
        regime_kr = "고변동성 급변장"
    elif fast_alignment == "bullish" and htf_trend == "uptrend":
        regime = "uptrend"
        regime_kr = "단기·중기 동반 상승"
    elif fast_alignment == "bearish" and htf_trend == "downtrend":
        regime = "downtrend"
        regime_kr = "단기·중기 동반 하락"
    elif fast_alignment == "bullish":
        regime = "rebound"
        regime_kr = "단기 반등 전환"
    elif fast_alignment == "bearish":
        regime = "pullback"
        regime_kr = "단기 눌림·약세"
    else:
        regime = "range"
        regime_kr = "단기 박스권 횡보"

    # =========================================================================
    # Short-Term Intraday Quant Score (-100 to +100) — Tuned for 1h 단타
    # =========================================================================
    score = 0.0

    # 1) Fast Intraday Trend: EMA5 / EMA13 + Spot Location + Slope (max +/- 28)
    if ema5 > ema13:
        score += 14.0
    elif ema5 < ema13:
        score -= 14.0

    if spot > ema5 and spot > ema13:
        score += 10.0
    elif spot < ema5 and spot < ema13:
        score -= 10.0
    elif spot > ema13:
        score += 4.0
    else:
        score -= 4.0

    if ema5 > ema5_prev * 1.0005:
        score += 4.0
    elif ema5 < ema5_prev * 0.9995:
        score -= 4.0

    # 2) Fast MACD(8,17,6) Momentum & Inflection (max +/- 22)
    hist = macd_data["hist"]
    hist_delta = hist - macd_data["prev_hist"]
    prev_delta = macd_data["prev_hist"] - macd_data["prev2_hist"]
    if hist > 0 and hist_delta > 0:
        score += 22.0
    elif hist > 0:
        score += 8.0
    elif hist < 0 and hist_delta < 0:
        score -= 22.0
    elif hist < 0 and hist_delta > 0:
        # Bearish momentum fading; stronger credit on 2 consecutive improving bars
        score += 10.0 if prev_delta > 0 else 5.0

    # 3) Intraday 12h VWAP Fair Value & Donchian Breakout (max +/- 18)
    if dist_vwap12_pct >= 0.15:
        score += 10.0
    elif dist_vwap12_pct <= -0.15:
        score -= 10.0

    if spot >= donchian_high_12h * 0.997 and vol_ratio >= 1.15:
        score += 8.0
    elif spot <= donchian_low_12h * 1.003:
        score -= 8.0

    # 4) RSI(14) Short-Term Reversal & Overbought Exhaustion (max +/- 16)
    if rsi14 <= 34.0:
        score += 16.0 if rsi14 > rsi14_prev else 6.0
    elif rsi14 <= 50.0 and rsi14 > rsi14_prev + 1.0 and spot > ema5:
        # Early bounce momentum confirmation
        score += 10.0
    elif rsi14 >= 72.0:
        score -= 16.0
    elif rsi14 >= 64.0 and rsi14 < rsi14_prev - 0.8:
        score -= 10.0

    # 5) Lightweight Higher-Timeframe (4h) Context Filter (max +/- 8)
    # Intentionally modest weight so 200h EMA lag does not block intraday reversals
    if htf_trend == "uptrend":
        score += 8.0
    elif htf_trend == "downtrend":
        score -= 8.0

    # 6) Volume Surge & Top-5 Orderbook Imbalance Confirmation (max +/- 8)
    if vol_ratio >= 1.30:
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
        "ema5": round(ema5, 1),
        "ema13": round(ema13, 1),
        "fast_alignment": fast_alignment,
        "ema9": round(ema9, 1),
        "ema21": round(ema21, 1),
        "ema50": round(ema50, 1),
        "ema200": round(ema200, 1),
        "ema_alignment": ema_alignment,
        "rsi14": round(rsi14, 2),
        "rsi14_prev": round(rsi14_prev, 2),
        "macd_line": round(macd_data["line"], 2),
        "macd_signal": round(macd_data["signal"], 2),
        "macd_hist": round(hist, 2),
        "macd_hist_delta": round(hist_delta, 2),
        "bb_upper": round(bb_data["upper"], 1),
        "bb_mid": round(bb_data["mid"], 1),
        "bb_lower": round(bb_data["lower"], 1),
        "bb_width_pct": round(bb_data["width_pct"], 2),
        "bb_percent_b": round(bb_data["percent_b"], 3),
        "atr14": round(atr14, 1),
        "atr14_pct": round(atr14_pct, 2),
        "vwap_12h": round(vwap12, 1),
        "dist_from_vwap12_pct": round(dist_vwap12_pct, 2),
        "vwap_24h": round(vwap24, 1),
        "dist_from_vwap_pct": round(dist_vwap24_pct, 2),
        "donchian_high_12h": round(donchian_high_12h, 1),
        "donchian_low_12h": round(donchian_low_12h, 1),
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
