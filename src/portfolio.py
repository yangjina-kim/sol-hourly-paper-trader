"""Paper trading portfolio ledger, intrabar SL/TP triggers, risk guardrails, and analytics."""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Any

from .ai_engine import TradingDecision
from .config import (
    BASE_SLIPPAGE_RATE,
    DAILY_LOSS_LIMIT_PCT,
    FEE_RATE,
    INITIAL_CAPITAL_KRW,
    KST,
    MAX_POSITION_RATIO,
    MAX_TOTAL_SLIPPAGE_RATE,
    MAX_VOL_SLIPPAGE_RATE,
    MIN_ORDER_KRW,
    MIN_ORDER_PCT,
    STOP_LOSS_EXTRA_SLIPPAGE_RATE,
    TAKE_PROFIT_SLIPPAGE_RATE,
    VOL_SLIPPAGE_ATR_THRESHOLD_PCT,
    VOL_SLIPPAGE_PER_ATR_PCT,
)
from .market_data import Candle, MarketSnapshot


def create_initial_state(snapshot: MarketSnapshot) -> dict[str, Any]:
    now_kst_date = snapshot.fetched_at_kst[:10]
    spot = snapshot.spot_price
    bm_fee = INITIAL_CAPITAL_KRW * FEE_RATE
    bm_sol_qty = (INITIAL_CAPITAL_KRW - bm_fee) / spot if spot > 0 else 0.0

    return {
        "version": 1,
        "started_at_kst": snapshot.fetched_at_kst,
        "started_at_utc": snapshot.fetched_at_utc,
        "last_run_kst": None,
        "last_run_utc": None,
        "last_run_ts_ms": 0,
        "run_count": 0,
        "initial_capital_krw": INITIAL_CAPITAL_KRW,
        "cash_krw": INITIAL_CAPITAL_KRW,
        "sol_qty": 0.0,
        "avg_entry_price": 0.0,
        "entry_time_kst": None,
        "stop_loss_price": None,
        "take_profit_price": None,
        "highest_price_since_entry": None,
        "cumulative_fees_krw": 0.0,
        "cumulative_slippage_krw": 0.0,
        "realized_pnl_krw": 0.0,
        "peak_equity_krw": INITIAL_CAPITAL_KRW,
        "max_drawdown_pct": 0.0,
        "daily_ref_date_kst": now_kst_date,
        "daily_start_equity_krw": INITIAL_CAPITAL_KRW,
        "daily_circuit_breaker_active": False,
        "benchmark": {
            "initial_price_krw": spot,
            "sol_qty": bm_sol_qty,
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
    krw_notional: float,
) -> float | None:
    """Walk live Upbit ask levels to estimate VWAP execution price for a KRW market buy."""
    if not asks or krw_notional <= 0:
        return None
    remaining_krw = krw_notional
    total_qty = 0.0
    for price, size_sol in asks:
        if price <= 0 or size_sol <= 0:
            continue
        level_krw = price * size_sol
        if level_krw >= remaining_krw:
            total_qty += remaining_krw / price
            remaining_krw = 0.0
            break
        total_qty += size_sol
        remaining_krw -= level_krw

    if remaining_krw > 0:
        # Order exceeds top-15 depth; fill remainder at worst ask + 10 bps penalty
        tail_price = asks[-1][0] * 1.0010
        total_qty += remaining_krw / tail_price

    return (krw_notional / total_qty) if total_qty > 0 else None


def _walk_orderbook_sell_vwap(
    bids: tuple[tuple[float, float], ...],
    sol_qty: float,
) -> float | None:
    """Walk live Upbit bid levels to estimate VWAP execution price for a SOL market sell."""
    if not bids or sol_qty <= 0:
        return None
    remaining_qty = sol_qty
    total_krw = 0.0
    for price, size_sol in bids:
        if price <= 0 or size_sol <= 0:
            continue
        if size_sol >= remaining_qty:
            total_krw += remaining_qty * price
            remaining_qty = 0.0
            break
        total_krw += size_sol * price
        remaining_qty -= size_sol

    if remaining_qty > 0:
        # Order exceeds top-15 depth; fill remainder at worst bid - 10 bps penalty
        tail_price = bids[-1][0] * 0.9990
        total_krw += remaining_qty * tail_price

    return (total_krw / sol_qty) if sol_qty > 0 else None


def estimate_execution_slippage(
    *,
    side: str,
    ref_price: float,
    order_krw: float,
    spread_bps: float,
    atr14_pct: float = 1.0,
    trigger: str = "gemini",
    orderbook_asks: tuple[tuple[float, float], ...] = (),
    orderbook_bids: tuple[tuple[float, float], ...] = (),
) -> tuple[float, dict[str, float]]:
    """Compute realistic execution slippage rate combining orderbook depth, ATR volatility, and trigger type."""
    if ref_price <= 0:
        return BASE_SLIPPAGE_RATE, {"book_bps": round(BASE_SLIPPAGE_RATE * 10_000.0, 2), "vol_bps": 0.0, "event_bps": 0.0}

    # Limit-style intrabar take-profit rests on the book with minimal slippage
    if trigger == "take_profit":
        rate = TAKE_PROFIT_SLIPPAGE_RATE
        return rate, {"book_bps": 0.0, "vol_bps": 0.0, "event_bps": round(rate * 10_000.0, 2)}

    half_spread = max(0.0, spread_bps) / 2.0 / 10_000.0
    floor_book_rate = max(BASE_SLIPPAGE_RATE, half_spread)

    # Volatility / latency drift surcharge based on 1h ATR(14)%
    vol_excess = max(0.0, atr14_pct - VOL_SLIPPAGE_ATR_THRESHOLD_PCT)
    vol_rate = min(MAX_VOL_SLIPPAGE_RATE, vol_excess * VOL_SLIPPAGE_PER_ATR_PCT)

    # Intrabar stop-loss executes during sharp downward sweeps -> extra panic-sweep slippage
    if trigger == "stop_loss":
        event_rate = STOP_LOSS_EXTRA_SLIPPAGE_RATE
        total_rate = min(MAX_TOTAL_SLIPPAGE_RATE, floor_book_rate + (vol_rate * 1.5) + event_rate)
        return total_rate, {
            "book_bps": round(floor_book_rate * 10_000.0, 2),
            "vol_bps": round(vol_rate * 1.5 * 10_000.0, 2),
            "event_bps": round(event_rate * 10_000.0, 2),
        }

    # Scheduled market order: walk live orderbook ladder when available
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
    elif order_krw > 1_000_000.0:
        # Fallback size-impact estimate when live orderbook ladder is unavailable (+0.5 bps per 100만 원 above 100만 원)
        size_impact = min(0.0010, ((order_krw - 1_000_000.0) / 1_000_000.0) * 0.00005)
        book_rate = floor_book_rate + size_impact

    total_rate = min(MAX_TOTAL_SLIPPAGE_RATE, book_rate + vol_rate)
    return total_rate, {
        "book_bps": round(book_rate * 10_000.0, 2),
        "vol_bps": round(vol_rate * 10_000.0, 2),
        "event_bps": 0.0,
    }


def effective_slippage_rate(spread_bps: float) -> float:
    """Backward-compatible helper returning base half-spread slippage rate."""
    half_spread = max(0.0, spread_bps) / 2.0 / 10_000.0
    return max(BASE_SLIPPAGE_RATE, half_spread)


def portfolio_equity(state: dict[str, Any], spot_price: float) -> float:
    return float(state["cash_krw"]) + float(state["sol_qty"]) * spot_price


def benchmark_equity(state: dict[str, Any], spot_price: float) -> float:
    bm = state["benchmark"]
    return float(bm["sol_qty"]) * spot_price


def refresh_daily_guardrails(state: dict[str, Any], spot_price: float, now_kst_str: str) -> None:
    """Roll over KST daily reference equity at midnight and check -5% daily circuit breaker."""
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


def _record_sell_execution(
    state: dict[str, Any],
    *,
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
    qty = min(held_qty, max(0.0, sell_qty))
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
    )
    exec_price = ref_price * (1.0 - slip_rate)
    gross_krw = qty * exec_price
    if gross_krw < MIN_ORDER_KRW and qty < held_qty * 0.99:
        return None

    slippage_krw = qty * max(0.0, ref_price - exec_price)
    fee_krw = gross_krw * FEE_RATE
    net_proceeds = gross_krw - fee_krw
    avg_entry = float(state["avg_entry_price"])
    cost_basis = qty * avg_entry
    realized_pnl = net_proceeds - cost_basis
    realized_pnl_pct = ((realized_pnl / cost_basis) * 100.0) if cost_basis > 0 else 0.0

    state["cash_krw"] = float(state["cash_krw"]) + net_proceeds
    remaining_qty = held_qty - qty
    if remaining_qty * ref_price < 1_000.0 or remaining_qty <= 1e-8:
        # Dust cleanup: close out completely
        remaining_qty = 0.0
        state["avg_entry_price"] = 0.0
        state["entry_time_kst"] = None
        state["stop_loss_price"] = None
        state["take_profit_price"] = None
        state["highest_price_since_entry"] = None
    state["sol_qty"] = remaining_qty

    state["cumulative_fees_krw"] = float(state["cumulative_fees_krw"]) + fee_krw
    state["cumulative_slippage_krw"] = float(state.get("cumulative_slippage_krw", 0.0)) + slippage_krw
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

    return {
        "id": f"T{stats['total_trades']:04d}",
        "time_kst": timestamp_kst,
        "time_utc": timestamp_utc,
        "side": "SELL",
        "trigger": trigger,
        "engine": engine,
        "spot_price": round(ref_price, 2),
        "exec_price": round(exec_price, 2),
        "slippage_bps": round(slip_rate * 10_000.0, 2),
        "slippage_krw": round(slippage_krw, 0),
        "slippage_breakdown": slip_breakdown,
        "sol_qty": round(qty, 6),
        "krw_amount": round(gross_krw, 0),
        "fee_krw": round(fee_krw, 0),
        "realized_pnl_krw": round(realized_pnl, 0),
        "realized_pnl_pct": round(realized_pnl_pct, 2),
        "reason": reason,
    }


def _record_buy_execution(
    state: dict[str, Any],
    *,
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
    current_sol_val = float(state["sol_qty"]) * spot_price
    max_sol_val = eq * MAX_POSITION_RATIO
    room_krw = max(0.0, max_sol_val - current_sol_val)

    spend_total = min(cash, krw_budget, room_krw)
    if spend_total < MIN_ORDER_KRW:
        return None

    slip_rate, slip_breakdown = estimate_execution_slippage(
        side="BUY",
        ref_price=spot_price,
        order_krw=spend_total,
        spread_bps=spread_bps,
        atr14_pct=atr14_pct,
        trigger=trigger,
        orderbook_asks=orderbook_asks,
    )
    exec_price = spot_price * (1.0 + slip_rate)
    fee_krw = spend_total * FEE_RATE
    net_krw = spend_total - fee_krw
    bought_qty = net_krw / exec_price
    slippage_krw = bought_qty * max(0.0, exec_price - spot_price)

    prev_qty = float(state["sol_qty"])
    prev_avg = float(state["avg_entry_price"])
    new_qty = prev_qty + bought_qty
    # Include entry fee in effective cost basis for honest realized PnL
    effective_cost_per_sol = spend_total / bought_qty
    new_avg = ((prev_qty * prev_avg) + (bought_qty * effective_cost_per_sol)) / new_qty if new_qty > 0 else exec_price

    state["cash_krw"] = cash - spend_total
    state["sol_qty"] = new_qty
    state["avg_entry_price"] = new_avg
    if prev_qty <= 1e-9:
        state["entry_time_kst"] = timestamp_kst
        state["highest_price_since_entry"] = spot_price
    else:
        prev_high = float(state.get("highest_price_since_entry") or spot_price)
        state["highest_price_since_entry"] = max(prev_high, spot_price)

    state["stop_loss_price"] = round(new_avg * (1.0 - stop_loss_pct / 100.0), 1)
    state["take_profit_price"] = round(new_avg * (1.0 + take_profit_pct / 100.0), 1)
    state["cumulative_fees_krw"] = float(state["cumulative_fees_krw"]) + fee_krw
    state["cumulative_slippage_krw"] = float(state.get("cumulative_slippage_krw", 0.0)) + slippage_krw

    stats = state["stats"]
    stats["total_trades"] += 1
    stats["buy_trades"] += 1

    return {
        "id": f"T{stats['total_trades']:04d}",
        "time_kst": timestamp_kst,
        "time_utc": timestamp_utc,
        "side": "BUY",
        "trigger": trigger,
        "engine": engine,
        "spot_price": round(spot_price, 2),
        "exec_price": round(exec_price, 2),
        "slippage_bps": round(slip_rate * 10_000.0, 2),
        "slippage_krw": round(slippage_krw, 0),
        "slippage_breakdown": slip_breakdown,
        "sol_qty": round(bought_qty, 6),
        "krw_amount": round(spend_total, 0),
        "fee_krw": round(fee_krw, 0),
        "realized_pnl_krw": None,
        "realized_pnl_pct": None,
        "reason": reason,
    }


def check_intrabar_triggers(
    state: dict[str, Any],
    snapshot: MarketSnapshot,
    *,
    atr14_pct: float = 1.0,
) -> dict[str, Any] | None:
    """Check if Stop-Loss or Take-Profit was touched between the last run and now."""
    if float(state["sol_qty"]) <= 1e-8:
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

    # Conservative assumption: if both SL and TP were hit inside the same bar, assume SL hit first
    if sl is not None and low_seen <= float(sl):
        return _record_sell_execution(
            state,
            sell_qty=float(state["sol_qty"]),
            ref_price=float(sl),
            spread_bps=snapshot.spread_bps,
            atr14_pct=atr14_pct,
            orderbook_bids=snapshot.orderbook_bids,
            timestamp_kst=snapshot.fetched_at_kst,
            timestamp_utc=snapshot.fetched_at_utc,
            trigger="stop_loss",
            engine="risk_guardrail",
            reason=f"장중 손절가({round(float(sl)):,}원) 터치(저가 {round(low_seen):,}원)로 자동 전량 청산",
        )

    if tp is not None and high_seen >= float(tp):
        return _record_sell_execution(
            state,
            sell_qty=float(state["sol_qty"]),
            ref_price=float(tp),
            spread_bps=snapshot.spread_bps,
            atr14_pct=atr14_pct,
            orderbook_bids=snapshot.orderbook_bids,
            timestamp_kst=snapshot.fetched_at_kst,
            timestamp_utc=snapshot.fetched_at_utc,
            trigger="take_profit",
            engine="risk_guardrail",
            reason=f"장중 익절가({round(float(tp)):,}원) 도달(고가 {round(high_seen):,}원)로 자동 전량 익절",
        )

    return None


def build_portfolio_context(
    state: dict[str, Any],
    spot_price: float,
    *,
    sl_cooldown_active: bool = False,
) -> dict[str, Any]:
    eq = portfolio_equity(state, spot_price)
    sol_qty = float(state["sol_qty"])
    sol_val = sol_qty * spot_price
    exposure_pct = (sol_val / eq * 100.0) if eq > 0 else 0.0
    avg_entry = float(state["avg_entry_price"])
    unrealized_krw = (spot_price - avg_entry) * sol_qty if sol_qty > 1e-8 and avg_entry > 0 else 0.0
    unrealized_pct = ((spot_price / avg_entry) - 1.0) * 100.0 if sol_qty > 1e-8 and avg_entry > 0 else 0.0

    return {
        "total_equity_krw": round(eq, 0),
        "cash_krw": round(float(state["cash_krw"]), 0),
        "sol_qty": round(sol_qty, 6),
        "sol_value_krw": round(sol_val, 0),
        "exposure_pct": round(exposure_pct, 2),
        "max_allowed_exposure_pct": round(MAX_POSITION_RATIO * 100.0, 1),
        "avg_entry_price_krw": round(avg_entry, 1),
        "unrealized_pnl_krw": round(unrealized_krw, 0),
        "unrealized_pnl_pct": round(unrealized_pct, 2),
        "stop_loss_price_krw": state.get("stop_loss_price"),
        "take_profit_price_krw": state.get("take_profit_price"),
        "daily_circuit_breaker_active": bool(state.get("daily_circuit_breaker_active", False)),
        "sl_cooldown_active": sl_cooldown_active,
    }


def execute_decision(
    state: dict[str, Any],
    snapshot: MarketSnapshot,
    decision: TradingDecision,
    *,
    sl_cooldown_active: bool = False,
    atr14_pct: float = 1.0,
) -> tuple[dict[str, Any] | None, str | None]:
    """Apply hard guardrails and execute paper BUY/SELL/HOLD. Returns (trade_record, guardrail_note)."""
    spot = snapshot.spot_price

    if decision.action == "BUY":
        if state.get("daily_circuit_breaker_active"):
            return None, f"일일 최대 손실 한도(-{DAILY_LOSS_LIMIT_PCT:.1f}%) 발동으로 신규 매수 차단"
        if sl_cooldown_active:
            return None, "직전 장중 손절 체결 직후 1시간 재진입 쿨다운 적용"
        if decision.order_pct < MIN_ORDER_PCT:
            return None, f"주문 비중({decision.order_pct:.1f}%)이 최소 임계치({MIN_ORDER_PCT:.0f}%) 미만으로 스킵"

        budget = float(state["cash_krw"]) * (decision.order_pct / 100.0)
        trade = _record_buy_execution(
            state,
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
            return None, f"최대 포지션 한도({int(MAX_POSITION_RATIO * 100)}%) 또는 최소 주문 금액({int(MIN_ORDER_KRW):,}원) 제약으로 매수 미체결"
        return trade, None

    if decision.action == "SELL":
        if float(state["sol_qty"]) <= 1e-8:
            return None, "보유 SOL 수량이 없어 매도 스킵"
        if decision.order_pct < MIN_ORDER_PCT:
            return None, f"매도 비중({decision.order_pct:.1f}%)이 최소 임계치 미만으로 스킵"

        sell_qty = float(state["sol_qty"]) * (decision.order_pct / 100.0)
        trade = _record_sell_execution(
            state,
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
            return None, f"최소 주문 금액({int(MIN_ORDER_KRW):,}원) 미만으로 매도 미체결"
        return trade, None

    # HOLD: Optionally tighten stop-loss upwards (trailing protection) if position is profitable
    if float(state["sol_qty"]) > 1e-8 and state.get("stop_loss_price") is not None:
        proposed_sl = round(spot * (1.0 - decision.stop_loss_pct / 100.0), 1)
        if proposed_sl > float(state["stop_loss_price"]) and spot > float(state["avg_entry_price"]) * 1.015:
            state["stop_loss_price"] = proposed_sl
    return None, None


def finalize_cycle_metrics(
    state: dict[str, Any],
    snapshot: MarketSnapshot,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Update peak equity / MDD and compute summary performance analytics."""
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
    state["run_count"] = int(state.get("run_count", 0)) + 1

    total_return_pct = ((eq / initial) - 1.0) * 100.0 if initial > 0 else 0.0
    bm_return_pct = ((bm_eq / initial) - 1.0) * 100.0 if initial > 0 else 0.0
    alpha_pp = total_return_pct - bm_return_pct

    sol_qty = float(state["sol_qty"])
    sol_val = sol_qty * spot
    avg_entry = float(state["avg_entry_price"])
    unrealized_krw = (spot - avg_entry) * sol_qty if sol_qty > 1e-8 and avg_entry > 0 else 0.0
    unrealized_pct = ((spot / avg_entry) - 1.0) * 100.0 if sol_qty > 1e-8 and avg_entry > 0 else 0.0

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

    # Annualized Sharpe Ratio from hourly equity series (once >= 12 hourly returns exist)
    equities = [float(h["equity_krw"]) for h in history[-720:]] + [eq]
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
                sharpe_ratio = round((mean_r / std) * math.sqrt(24.0 * 365.0), 2)

    daily_ref = float(state.get("daily_start_equity_krw") or eq)
    daily_return_pct = ((eq / daily_ref) - 1.0) * 100.0 if daily_ref > 0 else 0.0
    cum_fees = float(state.get("cumulative_fees_krw", 0.0))
    cum_slip = float(state.get("cumulative_slippage_krw", 0.0))

    return {
        "total_equity_krw": round(eq, 0),
        "total_pnl_krw": round(eq - initial, 0),
        "total_return_pct": round(total_return_pct, 2),
        "daily_return_pct": round(daily_return_pct, 2),
        "benchmark_equity_krw": round(bm_eq, 0),
        "benchmark_return_pct": round(bm_return_pct, 2),
        "alpha_pp": round(alpha_pp, 2),
        "cash_krw": round(float(state["cash_krw"]), 0),
        "sol_qty": round(sol_qty, 6),
        "sol_value_krw": round(sol_val, 0),
        "exposure_pct": round((sol_val / eq * 100.0) if eq > 0 else 0.0, 2),
        "avg_entry_price_krw": round(avg_entry, 1),
        "unrealized_pnl_krw": round(unrealized_krw, 0),
        "unrealized_pnl_pct": round(unrealized_pct, 2),
        "realized_pnl_krw": round(float(state["realized_pnl_krw"]), 0),
        "stop_loss_price_krw": state.get("stop_loss_price"),
        "take_profit_price_krw": state.get("take_profit_price"),
        "entry_time_kst": state.get("entry_time_kst"),
        "cumulative_fees_krw": round(cum_fees, 0),
        "cumulative_slippage_krw": round(cum_slip, 0),
        "total_trading_cost_krw": round(cum_fees + cum_slip, 0),
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
