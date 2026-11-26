"""Comprehensive unit and end-to-end simulation tests for the SOL/KRW short-term quant paper trader."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import FEE_RATE, INITIAL_CAPITAL_KRW, KST, MAX_POSITION_RATIO
from src.indicators import compute_indicators, rsi_series
from src.market_data import Candle, MarketSnapshot
from src.portfolio import (
    build_portfolio_context,
    check_intrabar_triggers,
    create_initial_state,
    estimate_execution_slippage,
    execute_decision,
    portfolio_equity,
    refresh_daily_guardrails,
)
from src.run_cycle import run_hourly_cycle
from src.strategy import TradingDecision, decide_trade


def _make_synthetic_snapshot(
    base_price: float = 250_000.0,
    trend_per_bar: float = 400.0,
    wave_amp: float = 2_500.0,
    hour_offset: int = 0,
    override_last_low: float | None = None,
    override_last_high: float | None = None,
    spot_override: float | None = None,
) -> MarketSnapshot:
    now_utc = datetime(2026, 11, 25, 12, 17, 0, tzinfo=timezone.utc) + timedelta(hours=hour_offset)
    now_kst = now_utc.astimezone(KST)

    candles_1h: list[Candle] = []
    for i in range(180):
        bar_dt = now_utc.replace(minute=0, second=0, microsecond=0) - timedelta(hours=180 - i)
        close = base_price + (i - 120) * trend_per_bar + math.sin(i / 4.5) * wave_amp
        open_p = close - trend_per_bar * 0.5
        high = max(open_p, close) + 1_200.0
        low = min(open_p, close) - 1_200.0
        if i == 179:
            if override_last_low is not None:
                low = override_last_low
            if override_last_high is not None:
                high = override_last_high
        vol = 1200.0 + (i % 7) * 180.0
        candles_1h.append(
            Candle(
                timestamp_ms=int(bar_dt.timestamp() * 1000),
                time_utc=bar_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                time_kst=bar_dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
                open=round(open_p, 1),
                high=round(high, 1),
                low=round(low, 1),
                close=round(close, 1),
                volume=vol,
                quote_volume=close * vol,
            )
        )

    candles_4h = candles_1h[::4][-55:]
    spot = spot_override if spot_override is not None else candles_1h[-1].close
    return MarketSnapshot(
        source="upbit",
        market="KRW-SOL",
        fetched_at_utc=now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        fetched_at_kst=now_kst.strftime("%Y-%m-%d %H:%M:%S KST"),
        spot_price=spot,
        change_24h_pct=round(((spot / candles_1h[-24].close) - 1.0) * 100.0, 2),
        change_24h_krw=round(spot - candles_1h[-24].close, 1),
        high_24h=max(c.high for c in candles_1h[-24:]),
        low_24h=min(c.low for c in candles_1h[-24:]),
        volume_24h_sol=sum(c.volume for c in candles_1h[-24:]),
        turnover_24h_krw=sum(c.quote_volume for c in candles_1h[-24:]),
        best_ask=spot + 50.0,
        best_bid=spot - 50.0,
        spread_bps=4.0,
        orderbook_imbalance=0.15,
        candles_1h=candles_1h,
        current_candle_1h=None,
        candles_4h=candles_4h,
    )


class TestSolPaperTrader(unittest.TestCase):
    def test_indicators_uptrend_vs_downtrend(self) -> None:
        up_snap = _make_synthetic_snapshot(trend_per_bar=700.0, wave_amp=500.0)
        up_ind = compute_indicators(up_snap)
        self.assertEqual(up_ind["fast_alignment"], "bullish")
        self.assertEqual(up_ind["htf_trend"], "uptrend")
        self.assertGreater(up_ind["quant_score"], 24.0)
        self.assertTrue(0.0 <= up_ind["rsi14"] <= 100.0)

        down_snap = _make_synthetic_snapshot(base_price=320_000.0, trend_per_bar=-700.0, wave_amp=500.0)
        down_ind = compute_indicators(down_snap)
        self.assertEqual(down_ind["fast_alignment"], "bearish")
        self.assertEqual(down_ind["htf_trend"], "downtrend")
        self.assertLess(down_ind["quant_score"], -24.0)

    def test_rsi_bounds(self) -> None:
        flat = [100.0] * 30
        vals = rsi_series(flat, 14)
        self.assertEqual(len(vals), 30)
        rising = [100.0 + i for i in range(30)]
        self.assertGreater(rsi_series(rising, 14)[-1], 95.0)

    def test_max_position_exposure_cap_and_fees(self) -> None:
        snap = _make_synthetic_snapshot(base_price=250_000.0, trend_per_bar=0.0, wave_amp=0.0, spot_override=250_000.0)
        state = create_initial_state(snap)
        buy_all = TradingDecision(
            action="BUY",
            order_pct=100.0,
            confidence=85,
            stop_loss_pct=2.0,
            take_profit_pct=4.0,
            headline="테스트 전액 매수 요청",
            briefing_summary="테스트",
            key_factors=["테스트"],
            risk_notes="테스트",
            next_watch="테스트",
        )
        trade, note = execute_decision(state, snap, buy_all)
        self.assertIsNotNone(trade)
        self.assertIsNone(note)
        eq = portfolio_equity(state, snap.spot_price)
        exposure = (state["sol_qty"] * snap.spot_price) / eq
        self.assertLessEqual(exposure, MAX_POSITION_RATIO + 0.001)
        self.assertAlmostEqual(trade["fee_krw"], round(trade["krw_amount"] * FEE_RATE), delta=2)

    def test_intrabar_stop_loss_triggers_conservatively(self) -> None:
        snap1 = _make_synthetic_snapshot(base_price=250_000.0, trend_per_bar=0.0, wave_amp=0.0, spot_override=250_000.0)
        state = create_initial_state(snap1)
        buy_dec = TradingDecision(
            action="BUY",
            order_pct=50.0,
            confidence=80,
            stop_loss_pct=2.0,
            take_profit_pct=4.0,
            headline="분할 매수",
            briefing_summary="테스트",
            key_factors=["테스트"],
            risk_notes="테스트",
            next_watch="테스트",
        )
        t_buy, _ = execute_decision(state, snap1, buy_dec)
        self.assertIsNotNone(t_buy)
        state["last_run_ts_ms"] = int(datetime(2026, 11, 25, 12, 17, 0, tzinfo=timezone.utc).timestamp() * 1000)
        sl_price = state["stop_loss_price"]
        tp_price = state["take_profit_price"]
        self.assertIsNotNone(sl_price)
        self.assertIsNotNone(tp_price)

        snap2 = _make_synthetic_snapshot(
            base_price=250_000.0,
            trend_per_bar=0.0,
            wave_amp=0.0,
            hour_offset=1,
            override_last_low=sl_price - 500.0,
            override_last_high=tp_price + 500.0,
            spot_override=250_000.0,
        )
        t_exit = check_intrabar_triggers(state, snap2)
        self.assertIsNotNone(t_exit)
        self.assertEqual(t_exit["trigger"], "stop_loss")
        self.assertEqual(state["sol_qty"], 0.0)
        self.assertLess(t_exit["realized_pnl_krw"], 0.0)

    def test_daily_circuit_breaker_blocks_new_buys(self) -> None:
        snap = _make_synthetic_snapshot(base_price=250_000.0, trend_per_bar=0.0, wave_amp=0.0, spot_override=250_000.0)
        state = create_initial_state(snap)
        state["cash_krw"] = INITIAL_CAPITAL_KRW * 0.94
        refresh_daily_guardrails(state, snap.spot_price, snap.fetched_at_kst)
        self.assertTrue(state["daily_circuit_breaker_active"])

        buy_dec = TradingDecision(
            action="BUY",
            order_pct=40.0,
            confidence=90,
            stop_loss_pct=2.0,
            take_profit_pct=4.0,
            headline="매수 시도",
            briefing_summary="테스트",
            key_factors=["테스트"],
            risk_notes="테스트",
            next_watch="테스트",
        )
        trade, note = execute_decision(state, snap, buy_dec)
        self.assertIsNone(trade)
        self.assertIsNotNone(note)
        self.assertIn("차단", note)

    def test_shortterm_time_stop_and_breakeven_trailing_stop(self) -> None:
        snap = _make_synthetic_snapshot(base_price=100_000.0, trend_per_bar=0.0, wave_amp=200.0, spot_override=100_000.0)
        ind = compute_indicators(snap)
        ind["quant_score"] = 5.0  # Stagnant neutral momentum

        # 1) Holding 13 hours with +0.2% flat PnL -> Time-Stop must exit 100%
        ctx = {
            "exposure_pct": 35.0,
            "sol_qty": 35.0,
            "unrealized_pnl_pct": 0.2,
            "holding_hours": 13.0,
            "daily_circuit_breaker_active": False,
            "sl_cooldown_active": False,
            "same_candle_buy_blocked": False,
        }
        dec = decide_trade(snap, ind, ctx)
        self.assertEqual(dec.action, "SELL")
        self.assertEqual(dec.order_pct, 100.0)
        self.assertIn("시간 손절", dec.headline)

        # 2) Breakeven + trailing stop locks SL >= avg_entry * 1.003 once unrealized >= +1.2%
        state = create_initial_state(snap)
        buy_dec = TradingDecision(
            action="BUY",
            order_pct=40.0,
            confidence=80,
            stop_loss_pct=2.0,
            take_profit_pct=4.0,
            headline="매수",
            briefing_summary="테스트",
            key_factors=["테스트"],
            risk_notes="테스트",
            next_watch="테스트",
        )
        execute_decision(state, snap, buy_dec)
        avg_entry = state["avg_entry_price"]
        up_snap = _make_synthetic_snapshot(
            base_price=100_000.0,
            trend_per_bar=0.0,
            wave_amp=0.0,
            hour_offset=2,
            spot_override=round(avg_entry * 1.015, 1),
        )
        hold_dec = TradingDecision(
            action="HOLD",
            order_pct=0.0,
            confidence=65,
            stop_loss_pct=2.0,
            take_profit_pct=4.0,
            headline="유지",
            briefing_summary="테스트",
            key_factors=["테스트"],
            risk_notes="테스트",
            next_watch="테스트",
        )
        execute_decision(state, up_snap, hold_dec)
        self.assertGreaterEqual(state["stop_loss_price"], round(avg_entry * 1.003, 1))

    def test_duplicate_candle_buy_guard_prevents_double_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_dir = tmp_path / "data"
            dash_path = tmp_path / "docs" / "data" / "dashboard.json"
            readme_path = tmp_path / "README.md"
            readme_path.write_text("# Test\n<!-- LIVE_SUMMARY_START -->\nold\n<!-- LIVE_SUMMARY_END -->\n", encoding="utf-8")

            # Textbook pullback-then-rebound (healthy RSI 52.9, EMA5 > EMA13, score +65.9)
            snap1 = _make_synthetic_snapshot(base_price=240_000.0, trend_per_bar=-180.0, wave_amp=500.0, hour_offset=0)
            patched = list(snap1.candles_1h)
            base_c = patched[-7].close
            rebound_offsets = [600.0, 1_200.0, 700.0, 1_400.0, 1_100.0, 1_900.0]
            for idx, off in zip(range(len(patched) - 6, len(patched)), rebound_offsets):
                c = patched[idx]
                new_close = base_c + off
                new_open = new_close - 250.0
                patched[idx] = Candle(
                    timestamp_ms=c.timestamp_ms,
                    time_utc=c.time_utc,
                    time_kst=c.time_kst,
                    open=new_open,
                    high=new_close + 400.0,
                    low=new_open - 300.0,
                    close=new_close,
                    volume=c.volume * 1.5,
                    quote_volume=new_close * c.volume * 1.5,
                )
            snap_breakout = MarketSnapshot(
                **{
                    **snap1.__dict__,
                    "spot_price": patched[-1].close,
                    "candles_1h": patched,
                }
            )
            p1 = run_hourly_cycle(snapshot=snap_breakout, data_dir=data_dir, dashboard_path=dash_path, readme_path=readme_path)
            self.assertIn("BUY", p1["latest_briefing"]["executed_sides"])

            # Second run landing in the EXACT same closed candle must NOT buy again
            p2 = run_hourly_cycle(snapshot=snap_breakout, data_dir=data_dir, dashboard_path=dash_path, readme_path=readme_path)
            self.assertEqual(p2["latest_briefing"]["decision"]["action"], "HOLD")
            self.assertEqual(p2["latest_briefing"]["executed_sides"], [])

    def test_realistic_slippage_model_orderbook_vol_and_triggers(self) -> None:
        asks = (
            (100_050.0, 10.0),
            (100_150.0, 15.0),
            (100_300.0, 30.0),
        )
        rate_buy, breakdown_buy = estimate_execution_slippage(
            side="BUY",
            ref_price=100_000.0,
            order_krw=5_000_000.0,
            spread_bps=6.0,
            atr14_pct=1.8,
            trigger="quant_shortterm",
            orderbook_asks=asks,
        )
        self.assertGreater(breakdown_buy["book_bps"], 15.0)
        self.assertAlmostEqual(breakdown_buy["vol_bps"], 4.0, places=2)
        self.assertAlmostEqual(rate_buy * 10_000.0, breakdown_buy["book_bps"] + breakdown_buy["vol_bps"], places=1)

        rate_sl, breakdown_sl = estimate_execution_slippage(
            side="SELL",
            ref_price=100_000.0,
            order_krw=5_000_000.0,
            spread_bps=8.0,
            atr14_pct=2.0,
            trigger="stop_loss",
        )
        self.assertAlmostEqual(breakdown_sl["event_bps"], 12.0, places=2)
        self.assertGreater(rate_sl, 0.0020)

        rate_tp, breakdown_tp = estimate_execution_slippage(
            side="SELL",
            ref_price=100_000.0,
            order_krw=5_000_000.0,
            spread_bps=12.0,
            atr14_pct=3.0,
            trigger="take_profit",
        )
        self.assertAlmostEqual(rate_tp, 0.0001, places=6)
        self.assertAlmostEqual(breakdown_tp["event_bps"], 1.0, places=2)

    def test_end_to_end_multi_hour_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_dir = tmp_path / "data"
            dash_path = tmp_path / "docs" / "data" / "dashboard.json"
            readme_path = tmp_path / "README.md"
            readme_path.write_text("# Test\n<!-- LIVE_SUMMARY_START -->\nold\n<!-- LIVE_SUMMARY_END -->\n", encoding="utf-8")

            for h in range(3):
                snap = _make_synthetic_snapshot(
                    base_price=240_000.0 + h * 3_500.0,
                    trend_per_bar=600.0,
                    wave_amp=800.0,
                    hour_offset=h,
                )
                payload = run_hourly_cycle(
                    snapshot=snap,
                    dry_run=False,
                    data_dir=data_dir,
                    dashboard_path=dash_path,
                    readme_path=readme_path,
                )

            self.assertEqual(payload["summary"]["run_count"], 3)
            self.assertEqual(len(payload["series"]), 3)
            self.assertTrue(dash_path.exists())
            saved_dash = json.loads(dash_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_dash["summary"]["run_count"], 3)
            self.assertEqual(saved_dash["latest_briefing"]["decision"]["model_name"], "quant-shortterm-v2")
            self.assertIn("cumulative_slippage_krw", saved_dash["summary"])
            self.assertIn("최신 정각 브리핑", readme_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
