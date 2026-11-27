"""Multi-asset paper trading portfolio ledger, 3-layer slippage engine, intrabar SL/TP, and risk guardrails."""

from __future__ import annotations

import math
import statistics
from datetime import datetime
from typing import Any

from .config import (
    ASSETS,
    BASE_SLIPPAGE_RATE,
    BREAKEVEN_TRIGGER_PCT,
    DAILY_LOSS_LIMIT_PCT,
    KST,
    MAX_POSITION_RATIO,
    MAX_TOTAL_SLIPPAGE_RATE,
    MAX_VOL_SLIPPAGE_RATE,
    MIN_ORDER_PCT,
    STOP_LOSS_EXTRA_SLIPPAGE_RATE,
    TAKE_PROFIT_SLIPPAGE_RATE,
    VOL_SLIPPAGE_ATR_THRESHOLD_PCT,
    VOL_SLIPPAGE_PER_ATR_PCT,
    AssetConfig,
)
from .market_data import Candle, MarketSnapshot
from .strategy import TradingDecision, format_price


def create_initial_state(snapshot: MarketSnapshot, asset: AssetConfig | None = None) -> dict[str, Any]:
    cfg = asset or ASSETS["sol"]
    now_kst_date = snapshot.fetched_at_kst[:10]
    spot = snapshot.spot_price
    initial = cfg.initial_capital
    bm_fee = initial * cfg.buy_fee_rate
    bm_qty = (initial - bm_fee) / spot if spot > 0 else 0.0

    return {
        "version": 2,
        "asset_id": cfg.asset_id,
        "symbol": cfg.symbol,
        "quote_currency": cfg.quote_currency,
        "started_at_kst": snapshot.fetched_at_kst,
        "started_at_utc": snapshot.fetched_at_utc,
        "last_run_kst": None,
        "last_run_utc": None,
        "last_run_ts_ms": 0,
        "run_count": 0,
        "initial_capital_krw": initial,
        "cash_krw": initial,
        "sol_qty": 0.0,
        "avg_entry_price": 0.0,
        "entry_time_kst": None,
        "stop_loss_price": None,
        "take_profit_price": None,
        "highest_price_since_entry": None,
        "cumulative_fees_krw": 0.0,
        "cumulative_slippage_krw": 0.0,
        "realized_pnl_krw": 0.0,
        "peak_equity_krw": initial,
        "max_drawdown_pct": 0.0,
        "daily_ref_date_kst": now_kst_date,
        "daily_start_equity_krw": initial,
        "daily_circuit_breaker_active": False,
        "benchmark": {
            "initial_price_krw": spot,
            "sol_qty": bm_qty,
            "initial_fee_krw": bm_fee,
        },
        "stats": {
            "total_trades": 0,
            "buy_trades": 0,
            "sell_trades": 0,
            "closed_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "gross_profit_krw": 0.0,
            "gross_loss_krw": 0.0,
        },
    }


def _walk_orderbook_buy_vwap(
    asks: tuple[tuple[float, float], ...],
    quote_notional: float,
) -> float | None:
    if not asks or quote_notional <= 0:
        return None
    remaining = quote_notional
    total_qty = 0.0
    for price, size in asks:
        if price <= 0 or size <= 0:
            continue
        level_val = price * size
        if level_val >= remaining:
            total_qty += remaining / price
            remaining = 0.0
            break
        total_qty += size
        remaining -= level_val

    if remaining > 0:
        tail_price = asks[-1][0] * 1.0010
        total_qty += remaining / tail_price

    return (quote_notional / total_qty) if total_qty > 0 else None


def _walk_orderbook_sell_vwap(
    bids: tuple[tuple[float, float], ...],
    qty: float,
) -> float | None:
    if not bids or qty <= 0:
        return None
    remaining_qty = qty
    total_val = 0.0
    for price, size in bids:
        if price <= 0 or size <= 0:
            continue
        if size >= remaining_qty:
            total_val += remaining_qty * price
            remaining_qty = 0.0
            break
        total_val += size * price
        remaining_qty -= size

    if remaining_qty > 0:
        tail_price = bids[-1][0] * 0.9990
        total_val += remaining_qty * tail_price

    return (total_val / qty) if qty > 0 else None


def estimate_execution_slippage(
    *,
    side: str,
    ref_price: float,
    order_krw: float,
    spread_bps: float,
    atr14_pct: float = 1.0,
    trigger: str = "quant_shortterm",
    orderbook_asks: tuple[tuple[float, float], ...] = (),
    orderbook_bids: tuple[tuple[float, float], ...] = (),
    base_slippage_rate: float = BASE_SLIPPAGE_RATE,
    quote_currency: str = "KRW",
) -> tuple[float, dict[str, float]]:
    """Compute realistic execution slippage rate combining orderbook depth, ATR volatility, and trigger type."""
    if ref_price <= 0:
        return base_slippage_rate, {"book_bps": round(base_slippage_rate * 10_000.0, 2), "vol_bps": 0.0, "event_bps": 0.0}

    if trigger == "take_profit":
        rate = TAKE_PROFIT_SLIPPAGE_RATE
        return rate, {"book_bps": 0.0, "vol_bps": 0.0, "event_bps": round(rate * 10_000.0, 2)}

    half_spread = max(0.0, spread_bps) / 2.0 / 10_000.0
    floor_book_rate = max(base_slippage_rate, half_spread)

    vol_excess = max(0.0, atr14_pct - VOL_SLIPPAGE_ATR_THRESHOLD_PCT)
    vol_rate = min(MAX_VOL_SLIPPAGE_RATE, vol_excess * VOL_SLIPPAGE_PER_ATR_PCT)

    if trigger == "stop_loss":
        event_rate = STOP_LOSS_EXTRA_SLIPPAGE_RATE
        total_rate = min(MAX_TOTAL_SLIPPAGE_RATE, floor_book_rate + (vol_rate * 1.5) + event_rate)
        return total_rate, {
            "book_bps": round(floor_book_rate * 10_000.0, 2),
            "vol_bps": round(vol_rate * 1.5 * 10_000.0, 2),
            "event_bps": round(event_rate * 10_000.0, 2),
        }

    book_rate = floor_book_rate
    if side == "BUY" and orderbook_asks:
        vwap = _walk_orderbook_buy_vwap(orderbook_asks, order_krw)
        if vwap is not None and vwap > ref_price:
            book_rate = max(floor_book_rate, (vwap - ref_price) / ref_price)
    elif side == "SELL" and orderbook_bids:
        est_qty = order_krw / ref_price
        vwap = _walk_orderbook_sell_vwap(orderbook_bids, est_qty)
        if vwap is not None and vwap < ref_price:
            book_rate = max(floor_book_rate, (ref_price - vwap) / ref_price)
    else:
        threshold = 1_000_000.0 if quote_currency == "KRW" else 1_000.0
        if order_krw > threshold:
            size_impact = min(0.0010, ((order_krw - threshold) / threshold) * 0.00005)
            book_rate = floor_book_rate + size_impact

    total_rate = min(MAX_TOTAL_SLIPPAGE_RATE, book_rate + vol_rate)
    return total_rate, {
        "book_bps": round(book_rate * 10_000.0, 2),
        "vol_bps": round(vol_rate * 10_000.0, 2),
        "event_bps": 0.0,
    }


def effective_slippage_rate(spread_bps: float) -> float:
    half_spread = max(0.0, spread_bps) / 2.0 / 10_000.0
    return max(BASE_SLIPPAGE_RATE, half_spread)


def portfolio_equity(state: dict[str, Any], spot_price: float) -> float:
    return float(state["cash_krw"]) + float(state["sol_qty"]) * spot_price


def benchmark_equity(state: dict[str, Any], spot_price: float) -> float:
    bm = state["benchmark"]
    return float(bm["sol_qty"]) * spot_price


def refresh_daily_guardrails(state: dict[str, Any], spot_price: float, now_kst_str: str) -> None:
    today_kst = now_kst_str[:10]
    eq = portfolio_equity(state, spot_price)

    if state.get("daily_ref_date_kst") != today_kst:
        state["daily_ref_date_kst"] = today_kst
        state["daily_start_equity_krw"] = eq
        state["daily_circuit_breaker_active"] = False
        return

    ref_eq = float(state.get("daily_start_equity_krw") or eq)
    if ref_eq > 0:
        daily_ret_pct = ((eq / ref_eq) - 1.0) * 100.0
        if daily_ret_pct <= -DAILY_LOSS_LIMIT_PCT:
            state["daily_circuit_breaker_active"] = True


def _quantize_qty(raw_qty: float, cfg: AssetConfig) -> float:
    if not cfg.allow_fractional:
        return float(math.floor(max(0.0, raw_qty)))
    factor = 10 ** max(cfg.qty_decimals, 2)
    return math.floor(max(0.0, raw_qty) * factor) / factor


def _record_sell_execution(
    state: dict[str, Any],
    *,
    asset: AssetConfig,
    sell_qty: float,
    ref_price: float,
    spread_bps: float,
    atr14_pct: float,
    orderbook_bids: tuple[tuple[float, float], ...],
    timestamp_kst: str,
    timestamp_utc: str,
    trigger: str,
    engine: str,
    reason: str,
) -> dict[str, Any] | None:
    held_qty = float(state["sol_qty"])
    is_full_exit = sell_qty >= held_qty * 0.99
    qty = held_qty if is_full_exit else _quantize_qty(min(held_qty, max(0.0, sell_qty)), asset)
    if not asset.allow_fractional and qty < 1.0 and held_qty >= 1.0:
        qty = 1.0 if not is_full_exit else held_qty
    if qty <= 1e-9:
        return None

    slip_rate, slip_breakdown = estimate_execution_slippage(
        side="SELL",
        ref_price=ref_price,
        order_krw=qty * ref_price,
        spread_bps=spread_bps,
        atr14_pct=atr14_pct,
        trigger=trigger,
        orderbook_bids=orderbook_bids,
        base_slippage_rate=asset.base_slippage_rate,
        quote_currency=asset.quote_currency,
    )
    exec_price = ref_price * (1.0 - slip_rate)
    gross_val = qty * exec_price
    if gross_val < asset.min_order_value and not is_full_exit:
        return None

    slippage_cost = qty * max(0.0, ref_price - exec_price)
    fee_cost = gross_val * asset.sell_fee_rate
    net_proceeds = gross_val - fee_cost
    avg_entry = float(state["avg_entry_price"])
    cost_basis = qty * avg_entry
    realized_pnl = net_proceeds - cost_basis
    realized_pnl_pct = ((realized_pnl / cost_basis) * 100.0) if cost_basis > 0 else 0.0

    state["cash_krw"] = float(state["cash_krw"]) + net_proceeds
    remaining_qty = held_qty - qty
    dust_threshold = 1_000.0 if asset.quote_currency == "KRW" else 5.0
    if remaining_qty * ref_price < dust_threshold or remaining_qty <= 1e-8 or (not asset.allow_fractional and remaining_qty < 1.0):
        remaining_qty = 0.0
        state["avg_entry_price"] = 0.0
        state["entry_time_kst"] = None
        state["stop_loss_price"] = None
        state["take_profit_price"] = None
        state["highest_price_since_entry"] = None
    state["sol_qty"] = remaining_qty

    state["cumulative_fees_krw"] = float(state["cumulative_fees_krw"]) + fee_cost
    state["cumulative_slippage_krw"] = float(state.get("cumulative_slippage_krw", 0.0)) + slippage_cost
    state["realized_pnl_krw"] = float(state["realized_pnl_krw"]) + realized_pnl

    stats = state["stats"]
    stats["total_trades"] += 1
    stats["sell_trades"] += 1
    stats["closed_trades"] += 1
    if realized_pnl > 0:
        stats["winning_trades"] += 1
        stats["gross_profit_krw"] += realized_pnl
    else:
        stats["losing_trades"] += 1
        stats["gross_loss_krw"] += abs(realized_pnl)

    val_dec = asset.price_decimals
    return {
        "id": f"T{stats['total_trades']:04d}",
        "time_kst": timestamp_kst,
        "time_utc": timestamp_utc,
        "side": "SELL",
        "trigger": trigger,
        "engine": engine,
        "spot_price": round(ref_price, max(2, val_dec)),
        "exec_price": round(exec_price, max(2, val_dec)),
        "slippage_bps": round(slip_rate * 10_000.0, 2),
        "slippage_krw": round(slippage_cost, val_dec),
        "slippage_breakdown": slip_breakdown,
        "sol_qty": round(qty, 6 if asset.allow_fractional else 0),
        "krw_amount": round(gross_val, val_dec),
        "fee_krw": round(fee_cost, val_dec),
        "realized_pnl_krw": round(realized_pnl, val_dec),
        "realized_pnl_pct": round(realized_pnl_pct, 2),
        "reason": reason,
    }


def _record_buy_execution(
    state: dict[str, Any],
    *,
    asset: AssetConfig,
    krw_budget: float,
    spot_price: float,
    spread_bps: float,
    atr14_pct: float,
    orderbook_asks: tuple[tuple[float, float], ...],
    stop_loss_pct: float,
    take_profit_pct: float,
    timestamp_kst: str,
    timestamp_utc: str,
    trigger: str,
    engine: str,
    reason: str,
) -> dict[str, Any] | None:
    cash = float(state["cash_krw"])
    eq = portfolio_equity(state, spot_price)
    current_val = float(state["sol_qty"]) * spot_price
    max_val = eq * MAX_POSITION_RATIO
    room = max(0.0, max_val - current_val)

    spend_budget = min(cash, krw_budget, room)
    if spend_budget < asset.min_order_value:
        return None

    slip_rate, slip_breakdown = estimate_execution_slippage(
        side="BUY",
        ref_price=spot_price,
        order_krw=spend_budget,
        spread_bps=spread_bps,
        atr14_pct=atr14_pct,
        trigger=trigger,
        orderbook_asks=orderbook_asks,
        base_slippage_rate=asset.base_slippage_rate,
        quote_currency=asset.quote_currency,
    )
    exec_price = spot_price * (1.0 + slip_rate)

    if asset.allow_fractional:
        spend_total = spend_budget
        fee_cost = spend_total * asset.buy_fee_rate
        net_spend = spend_total - fee_cost
        bought_qty = net_spend / exec_price
        if asset.qty_decimals <= 2:
            bought_qty = _quantize_qty(bought_qty, asset)
            if bought_qty <= 0:
                return None
            net_spend = bought_qty * exec_price
            fee_cost = net_spend * asset.buy_fee_rate
            spend_total = net_spend + fee_cost
    else:
        # Integer share market (e.g., KRX SK Hynix): buy whole shares within budget
        max_shares = math.floor(spend_budget / (exec_price * (1.0 + asset.buy_fee_rate)))
        if max_shares < 1:
            return None
        bought_qty = float(max_shares)
        net_spend = bought_qty * exec_price
        fee_cost = net_spend * asset.buy_fee_rate
        spend_total = net_spend + fee_cost
        if spend_total > cash:
            return None

    slippage_cost = bought_qty * max(0.0, exec_price - spot_price)

    prev_qty = float(state["sol_qty"])
    prev_avg = float(state["avg_entry_price"])
    new_qty = prev_qty + bought_qty
    effective_cost_per_unit = spend_total / bought_qty
    new_avg = ((prev_qty * prev_avg) + (bought_qty * effective_cost_per_unit)) / new_qty if new_qty > 0 else exec_price

    state["cash_krw"] = cash - spend_total
    state["sol_qty"] = new_qty
    state["avg_entry_price"] = new_avg
    if prev_qty <= 1e-9:
        state["entry_time_kst"] = timestamp_kst
        state["highest_price_since_entry"] = spot_price
    else:
        prev_high = float(state.get("highest_price_since_entry") or spot_price)
        state["highest_price_since_entry"] = max(prev_high, spot_price)

    price_dec = max(1, asset.price_decimals)
    state["stop_loss_price"] = round(new_avg * (1.0 - stop_loss_pct / 100.0), price_dec)
    state["take_profit_price"] = round(new_avg * (1.0 + take_profit_pct / 100.0), price_dec)
    state["cumulative_fees_krw"] = float(state["cumulative_fees_krw"]) + fee_cost
    state["cumulative_slippage_krw"] = float(state.get("cumulative_slippage_krw", 0.0)) + slippage_cost

    stats = state["stats"]
    stats["total_trades"] += 1
    stats["buy_trades"] += 1

    val_dec = asset.price_decimals
    return {
        "id": f"T{stats['total_trades']:04d}",
        "time_kst": timestamp_kst,
        "time_utc": timestamp_utc,
        "side": "BUY",
        "trigger": trigger,
        "engine": engine,
        "spot_price": round(spot_price, max(2, val_dec)),
        "exec_price": round(exec_price, max(2, val_dec)),
        "slippage_bps": round(slip_rate * 10_000.0, 2),
        "slippage_krw": round(slippage_cost, val_dec),
        "slippage_breakdown": slip_breakdown,
        "sol_qty": round(bought_qty, 6 if asset.allow_fractional else 0),
        "krw_amount": round(spend_total, val_dec),
        "fee_krw": round(fee_cost, val_dec),
        "realized_pnl_krw": None,
        "realized_pnl_pct": None,
        "reason": reason,
    }


def check_intrabar_triggers(
    state: dict[str, Any],
    snapshot: MarketSnapshot,
    *,
    atr14_pct: float = 1.0,
    asset: AssetConfig | None = None,
) -> dict[str, Any] | None:
    cfg = asset or ASSETS["sol"]
    if not snapshot.market_open or float(state["sol_qty"]) <= 1e-8:
        return None

    sl = state.get("stop_loss_price")
    tp = state.get("take_profit_price")
    if sl is None and tp is None:
        return None

    last_ts = int(state.get("last_run_ts_ms") or 0)
    relevant_candles: list[Candle] = [
        c for c in snapshot.candles_1h[-4:] if c.timestamp_ms + 3_600_000 > last_ts
    ]
    if snapshot.current_candle_1h is not None:
        relevant_candles.append(snapshot.current_candle_1h)

    if not relevant_candles:
        low_seen = snapshot.spot_price
        high_seen = snapshot.spot_price
    else:
        low_seen = min(min(c.low for c in relevant_candles), snapshot.spot_price)
        high_seen = max(max(c.high for c in relevant_candles), snapshot.spot_price)

    prev_high = float(state.get("highest_price_since_entry") or snapshot.spot_price)
    state["highest_price_since_entry"] = max(prev_high, high_seen)

    if sl is not None and low_seen <= float(sl):
        return _record_sell_execution(
            state,
            asset=cfg,
            sell_qty=float(state["sol_qty"]),
            ref_price=float(sl),
            spread_bps=snapshot.spread_bps,
            atr14_pct=atr14_pct,
            orderbook_bids=snapshot.orderbook_bids,
            timestamp_kst=snapshot.fetched_at_kst,
            timestamp_utc=snapshot.fetched_at_utc,
            trigger="stop_loss",
            engine="risk_guardrail",
            reason=f"장중 손절가({format_price(float(sl), cfg)}) 터치(저가 {format_price(low_seen, cfg)})로 자동 전량 청산",
        )

    if tp is not None and high_seen >= float(tp):
        return _record_sell_execution(
            state,
            asset=cfg,
            sell_qty=float(state["sol_qty"]),
            ref_price=float(tp),
            spread_bps=snapshot.spread_bps,
            atr14_pct=atr14_pct,
            orderbook_bids=snapshot.orderbook_bids,
            timestamp_kst=snapshot.fetched_at_kst,
            timestamp_utc=snapshot.fetched_at_utc,
            trigger="take_profit",
            engine="risk_guardrail",
            reason=f"장중 익절가({format_price(float(tp), cfg)}) 도달(고가 {format_price(high_seen, cfg)})로 자동 전량 익절",
        )

    return None


def _parse_kst_str(kst_str: str | None) -> datetime | None:
    if not kst_str:
        return None
    try:
        clean = kst_str.replace(" KST", "").strip()
        return datetime.strptime(clean, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    except ValueError:
        return None


def build_portfolio_context(
    state: dict[str, Any],
    spot_price: float,
    *,
    sl_cooldown_active: bool = False,
    now_kst_str: str | None = None,
    same_candle_buy_blocked: bool = False,
) -> dict[str, Any]:
    eq = portfolio_equity(state, spot_price)
    held_qty = float(state["sol_qty"])
    held_val = held_qty * spot_price
    exposure_pct = (held_val / eq * 100.0) if eq > 0 else 0.0
    avg_entry = float(state["avg_entry_price"])
    unrealized_val = (spot_price - avg_entry) * held_qty if held_qty > 1e-8 and avg_entry > 0 else 0.0
    unrealized_pct = ((spot_price / avg_entry) - 1.0) * 100.0 if held_qty > 1e-8 and avg_entry > 0 else 0.0

    holding_hours = 0.0
    if held_qty > 1e-8 and state.get("entry_time_kst") and now_kst_str:
        entry_dt = _parse_kst_str(state.get("entry_time_kst"))
        now_dt = _parse_kst_str(now_kst_str)
        if entry_dt is not None and now_dt is not None and now_dt > entry_dt:
            holding_hours = round((now_dt - entry_dt).total_seconds() / 3600.0, 2)

    return {
        "total_equity_krw": round(eq, 2),
        "cash_krw": round(float(state["cash_krw"]), 2),
        "sol_qty": round(held_qty, 6),
        "sol_value_krw": round(held_val, 2),
        "exposure_pct": round(exposure_pct, 2),
        "max_allowed_exposure_pct": round(MAX_POSITION_RATIO * 100.0, 1),
        "avg_entry_price_krw": round(avg_entry, 2),
        "unrealized_pnl_krw": round(unrealized_val, 2),
        "unrealized_pnl_pct": round(unrealized_pct, 2),
        "holding_hours": holding_hours,
        "stop_loss_price_krw": state.get("stop_loss_price"),
        "take_profit_price_krw": state.get("take_profit_price"),
        "daily_circuit_breaker_active": bool(state.get("daily_circuit_breaker_active", False)),
        "sl_cooldown_active": sl_cooldown_active,
        "same_candle_buy_blocked": same_candle_buy_blocked,
    }


def execute_decision(
    state: dict[str, Any],
    snapshot: MarketSnapshot,
    decision: TradingDecision,
    *,
    sl_cooldown_active: bool = False,
    atr14_pct: float = 1.0,
    same_candle_buy_blocked: bool = False,
    asset: AssetConfig | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    cfg = asset or ASSETS["sol"]
    spot = snapshot.spot_price

    if not snapshot.market_open:
        return None, None

    if decision.action == "BUY":
        if state.get("daily_circuit_breaker_active"):
            return None, f"일일 최대 손실 한도(-{DAILY_LOSS_LIMIT_PCT:.1f}%) 발동으로 신규 매수 차단"
        if sl_cooldown_active:
            return None, "직전 장중 손절 체결 직후 1시간 재진입 쿨다운 적용"
        if same_candle_buy_blocked:
            return None, "크론 지연으로 동일 1시간봉 내 재실행되어 중복 매수 차단"
        if decision.order_pct < MIN_ORDER_PCT:
            return None, f"주문 비중({decision.order_pct:.1f}%)이 최소 임계치({MIN_ORDER_PCT:.0f}%) 미만으로 스킵"

        budget = float(state["cash_krw"]) * (decision.order_pct / 100.0)
        trade = _record_buy_execution(
            state,
            asset=cfg,
            krw_budget=budget,
            spot_price=spot,
            spread_bps=snapshot.spread_bps,
            atr14_pct=atr14_pct,
            orderbook_asks=snapshot.orderbook_asks,
            stop_loss_pct=decision.stop_loss_pct,
            take_profit_pct=decision.take_profit_pct,
            timestamp_kst=snapshot.fetched_at_kst,
            timestamp_utc=snapshot.fetched_at_utc,
            trigger=decision.engine,
            engine=decision.model_name,
            reason=decision.headline,
        )
        if trade is None:
            return None, f"최대 포지션 한도({int(MAX_POSITION_RATIO * 100)}%) 또는 최소 주문 단위 제약으로 매수 미체결"
        return trade, None

    if decision.action == "SELL":
        if float(state["sol_qty"]) <= 1e-8:
            return None, f"보유 {cfg.short_name} 수량이 없어 매도 스킵"
        if decision.order_pct < MIN_ORDER_PCT:
            return None, f"매도 비중({decision.order_pct:.1f}%)이 최소 임계치 미만으로 스킵"

        sell_qty = float(state["sol_qty"]) * (decision.order_pct / 100.0)
        trade = _record_sell_execution(
            state,
            asset=cfg,
            sell_qty=sell_qty,
            ref_price=spot,
            spread_bps=snapshot.spread_bps,
            atr14_pct=atr14_pct,
            orderbook_bids=snapshot.orderbook_bids,
            timestamp_kst=snapshot.fetched_at_kst,
            timestamp_utc=snapshot.fetched_at_utc,
            trigger=decision.engine,
            engine=decision.model_name,
            reason=decision.headline,
        )
        if trade is None:
            return None, "최소 주문 단위 미만으로 매도 미체결"
        return trade, None

    # HOLD: Short-term Breakeven + Trailing Stop-Loss Protection
    if float(state["sol_qty"]) > 1e-8 and state.get("stop_loss_price") is not None:
        avg_entry = float(state["avg_entry_price"])
        if avg_entry > 0:
            unrealized_pct = ((spot / avg_entry) - 1.0) * 100.0
            if unrealized_pct >= BREAKEVEN_TRIGGER_PCT:
                price_dec = max(1, cfg.price_decimals)
                breakeven_floor = round(avg_entry * 1.003, price_dec)
                trailing_sl = round(spot * (1.0 - (decision.stop_loss_pct * 0.7) / 100.0), price_dec)
                proposed_sl = max(breakeven_floor, trailing_sl)
                if proposed_sl > float(state["stop_loss_price"]) and proposed_sl < spot:
                    state["stop_loss_price"] = proposed_sl
    return None, None


def finalize_cycle_metrics(
    state: dict[str, Any],
    snapshot: MarketSnapshot,
    history: list[dict[str, Any]],
    *,
    asset: AssetConfig | None = None,
    increment_run: bool = True,
) -> dict[str, Any]:
    cfg = asset or ASSETS["sol"]
    val_dec = cfg.price_decimals
    spot = snapshot.spot_price
    eq = portfolio_equity(state, spot)
    bm_eq = benchmark_equity(state, spot)
    initial = float(state["initial_capital_krw"])

    if eq > float(state["peak_equity_krw"]):
        state["peak_equity_krw"] = eq
    peak = float(state["peak_equity_krw"])
    cur_dd = ((eq / peak) - 1.0) * 100.0 if peak > 0 else 0.0
    if cur_dd < float(state["max_drawdown_pct"]):
        state["max_drawdown_pct"] = cur_dd

    now_dt = datetime.fromisoformat(snapshot.fetched_at_utc.replace("Z", "+00:00"))
    state["last_run_kst"] = snapshot.fetched_at_kst
    state["last_run_utc"] = snapshot.fetched_at_utc
    state["last_run_ts_ms"] = int(now_dt.timestamp() * 1000)
    if increment_run:
        state["run_count"] = int(state.get("run_count", 0)) + 1

    total_return_pct = ((eq / initial) - 1.0) * 100.0 if initial > 0 else 0.0
    bm_return_pct = ((bm_eq / initial) - 1.0) * 100.0 if initial > 0 else 0.0
    alpha_pp = total_return_pct - bm_return_pct

    held_qty = float(state["sol_qty"])
    held_val = held_qty * spot
    avg_entry = float(state["avg_entry_price"])
    unrealized_val = (spot - avg_entry) * held_qty if held_qty > 1e-8 and avg_entry > 0 else 0.0
    unrealized_pct = ((spot / avg_entry) - 1.0) * 100.0 if held_qty > 1e-8 and avg_entry > 0 else 0.0

    stats = state["stats"]
    closed = int(stats["closed_trades"])
    wins = int(stats["winning_trades"])
    win_rate = (wins / closed * 100.0) if closed > 0 else None
    gross_profit = float(stats["gross_profit_krw"])
    gross_loss = float(stats["gross_loss_krw"])
    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 2)
    elif gross_profit > 0:
        profit_factor = 99.9
    else:
        profit_factor = None

    equities = [float(h["equity_krw"]) for h in history[-720:]] + ([eq] if increment_run else [])
    sharpe_ratio: float | None = None
    if len(equities) >= 13:
        rets = [
            (equities[i] / equities[i - 1]) - 1.0
            for i in range(1, len(equities))
            if equities[i - 1] > 0
        ]
        if len(rets) >= 12:
            std = statistics.pstdev(rets)
            if std > 1e-12:
                mean_r = sum(rets) / len(rets)
                ann_factor = math.sqrt(24.0 * 365.0) if cfg.market_type == "crypto" else math.sqrt(7.0 * 252.0)
                sharpe_ratio = round((mean_r / std) * ann_factor, 2)

    daily_ref = float(state.get("daily_start_equity_krw") or eq)
    daily_return_pct = ((eq / daily_ref) - 1.0) * 100.0 if daily_ref > 0 else 0.0
    cum_fees = float(state.get("cumulative_fees_krw", 0.0))
    cum_slip = float(state.get("cumulative_slippage_krw", 0.0))

    return {
        "total_equity_krw": round(eq, val_dec),
        "total_pnl_krw": round(eq - initial, val_dec),
        "total_return_pct": round(total_return_pct, 2),
        "daily_return_pct": round(daily_return_pct, 2),
        "benchmark_equity_krw": round(bm_eq, val_dec),
        "benchmark_return_pct": round(bm_return_pct, 2),
        "alpha_pp": round(alpha_pp, 2),
        "cash_krw": round(float(state["cash_krw"]), val_dec),
        "sol_qty": round(held_qty, 6 if cfg.allow_fractional else 0),
        "sol_value_krw": round(held_val, val_dec),
        "exposure_pct": round((held_val / eq * 100.0) if eq > 0 else 0.0, 2),
        "avg_entry_price_krw": round(avg_entry, max(1, val_dec)),
        "unrealized_pnl_krw": round(unrealized_val, val_dec),
        "unrealized_pnl_pct": round(unrealized_pct, 2),
        "realized_pnl_krw": round(float(state["realized_pnl_krw"]), val_dec),
        "stop_loss_price_krw": state.get("stop_loss_price"),
        "take_profit_price_krw": state.get("take_profit_price"),
        "entry_time_kst": state.get("entry_time_kst"),
        "cumulative_fees_krw": round(cum_fees, val_dec),
        "cumulative_slippage_krw": round(cum_slip, val_dec),
        "total_trading_cost_krw": round(cum_fees + cum_slip, val_dec),
        "max_drawdown_pct": round(float(state["max_drawdown_pct"]), 2),
        "current_drawdown_pct": round(cur_dd, 2),
        "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
        "profit_factor": profit_factor,
        "sharpe_ratio": sharpe_ratio,
        "total_trades": int(stats["total_trades"]),
        "buy_trades": int(stats["buy_trades"]),
        "sell_trades": int(stats["sell_trades"]),
        "closed_trades": closed,
        "winning_trades": wins,
        "losing_trades": int(stats["losing_trades"]),
        "daily_circuit_breaker_active": bool(state.get("daily_circuit_breaker_active", False)),
        "run_count": int(state["run_count"]),
        "started_at_kst": state["started_at_kst"],
        "last_run_kst": state["last_run_kst"],
    }
