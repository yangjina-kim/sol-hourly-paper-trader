"""Comprehensive unit and end-to-end simulation tests for the SOL/KRW short-term quant paper trader."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import ASSETS, FEE_RATE, INITIAL_CAPITAL_KRW, KST, MAX_POSITION_RATIO
from src.indicators import compute_candle_indicator_series, compute_indicators, rsi_series
from src.market_data import Candle, MarketSnapshot, is_market_open, merge_candles
from src.portfolio import (
    build_portfolio_context,
    check_intrabar_triggers,
    create_initial_state,
    estimate_execution_slippage,
    execute_decision,
    portfolio_equity,
    refresh_daily_guardrails,
)
from src.report_builder import extract_version_changes
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
    market: str = "KRW-SOL",
    source: str = "upbit",
    market_open: bool = True,
    market_status_label: str = "24시간 운영 중",
    bar_range: float | None = None,
) -> MarketSnapshot:
    now_utc = datetime(2026, 11, 25, 12, 17, 0, tzinfo=timezone.utc) + timedelta(hours=hour_offset)
    now_kst = now_utc.astimezone(KST)
    half_range = bar_range if bar_range is not None else max(base_price * 0.0048, 0.5)

    candles_1h: list[Candle] = []
    for i in range(180):
        bar_dt = now_utc.replace(minute=0, second=0, microsecond=0) - timedelta(hours=180 - i)
        close = base_price + (i - 120) * trend_per_bar + math.sin(i / 4.5) * wave_amp
        open_p = close - trend_per_bar * 0.5
        high = max(open_p, close) + half_range
        low = min(open_p, close) - half_range
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
                open=round(open_p, 2),
                high=round(high, 2),
                low=round(low, 2),
                close=round(close, 2),
                volume=vol,
                quote_volume=close * vol,
            )
        )

    candles_4h = candles_1h[::4][-55:]
    spot = spot_override if spot_override is not None else candles_1h[-1].close
    return MarketSnapshot(
        source=source,
        market=market,
        fetched_at_utc=now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        fetched_at_kst=now_kst.strftime("%Y-%m-%d %H:%M:%S KST"),
        spot_price=spot,
        change_24h_pct=round(((spot / candles_1h[-24].close) - 1.0) * 100.0, 2),
        change_24h_krw=round(spot - candles_1h[-24].close, 2),
        high_24h=max(c.high for c in candles_1h[-24:]),
        low_24h=min(c.low for c in candles_1h[-24:]),
        volume_24h_sol=sum(c.volume for c in candles_1h[-24:]),
        turnover_24h_krw=sum(c.quote_volume for c in candles_1h[-24:]),
        best_ask=spot + (spot * 0.0002),
        best_bid=spot - (spot * 0.0002),
        spread_bps=4.0,
        orderbook_imbalance=0.15,
        candles_1h=candles_1h,
        current_candle_1h=None,
        candles_4h=candles_4h,
        market_open=market_open,
        market_status_label=market_status_label,
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
            self.assertEqual(saved_dash["schema_version"], 2)
            self.assertEqual(saved_dash["accounts"]["sol"]["summary"]["run_count"], 3)
            self.assertEqual(saved_dash["accounts"]["sol"]["latest_briefing"]["decision"]["model_name"], "quant-shortterm-v2")
            self.assertIn("cumulative_slippage_krw", saved_dash["accounts"]["sol"]["summary"])
            self.assertIn("최신 정각 멀티 계좌 브리핑", readme_path.read_text(encoding="utf-8"))

    def test_extract_version_changes_detects_engine_transition(self) -> None:
        history = [
            {
                "run_id": 1,
                "time_kst": "2026-11-25 18:17:00 KST",
                "time_utc": "2026-11-25T09:17:00Z",
                "decision": {"model_name": "quant-multifactor-v1"},
            },
            {
                "run_id": 2,
                "time_kst": "2026-11-25 19:17:00 KST",
                "time_utc": "2026-11-25T10:17:00Z",
                "decision": {"model_name": "quant-multifactor-v1"},
            },
            {
                "run_id": 3,
                "time_kst": "2026-11-25 20:17:00 KST",
                "time_utc": "2026-11-25T11:17:00Z",
                "decision": {"model_name": "quant-shortterm-v2"},
            },
            {
                "run_id": 4,
                "time_kst": "2026-11-25 21:17:00 KST",
                "time_utc": "2026-11-25T12:17:00Z",
                "decision": {"model_name": "quant-shortterm-v2"},
            },
        ]
        changes = extract_version_changes(history)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["run_id"], 3)
        self.assertEqual(changes[0]["from_model"], "quant-multifactor-v1")
        self.assertEqual(changes[0]["to_model"], "quant-shortterm-v2")
        self.assertEqual(changes[0]["label"], "v2 전환")

    def test_merge_candles_and_indicator_overlay_series(self) -> None:
        snap1 = _make_synthetic_snapshot(hour_offset=0)
        snap2 = _make_synthetic_snapshot(hour_offset=2)
        merged = merge_candles(snap1.candles_1h, snap2.candles_1h)
        self.assertEqual(len(merged), len(snap1.candles_1h) + 2)
        self.assertEqual(len({c.timestamp_ms for c in merged}), len(merged))

        overlay = compute_candle_indicator_series(merged, limit=120, decimals=1)
        self.assertEqual(len(overlay), 120)
        last = overlay[-1]
        for key in ("ts", "open", "high", "low", "close", "ema5", "ema13", "vwap12", "bb_upper", "bb_lower", "rsi14", "macd_hist"):
            self.assertIn(key, last)
            self.assertIsNotNone(last[key])

    def test_market_hours_crypto_krx_nasdaq(self) -> None:
        # Crypto is always open (even Sunday)
        sun_utc = datetime(2026, 11, 29, 3, 0, tzinfo=timezone.utc)
        self.assertTrue(is_market_open("crypto", sun_utc)[0])
        self.assertFalse(is_market_open("kr_stock", sun_utc)[0])
        self.assertFalse(is_market_open("us_stock", sun_utc)[0])

        # Wednesday 10:17 KST = 01:17 UTC -> KRX open, NASDAQ closed
        krx_open_utc = datetime(2026, 11, 25, 1, 17, tzinfo=timezone.utc)
        self.assertTrue(is_market_open("kr_stock", krx_open_utc)[0])
        self.assertFalse(is_market_open("us_stock", krx_open_utc)[0])

        # Wednesday 11:17 EST (winter) = 16:17 UTC -> NASDAQ open, KRX closed
        us_open_utc = datetime(2026, 11, 25, 16, 17, tzinfo=timezone.utc)
        self.assertFalse(is_market_open("kr_stock", us_open_utc)[0])
        self.assertTrue(is_market_open("us_stock", us_open_utc)[0])

    def test_hynix_integer_shares_and_krx_sell_tax(self) -> None:
        hynix = ASSETS["hynix"]
        snap = _make_synthetic_snapshot(
            base_price=210_000.0,
            trend_per_bar=0.0,
            wave_amp=0.0,
            spot_override=210_000.0,
            market=hynix.symbol,
            source="yahoo",
            market_open=True,
            market_status_label="KRX 정규장 개장",
        )
        state = create_initial_state(snap, asset=hynix)
        buy_dec = TradingDecision(
            action="BUY",
            order_pct=40.0,
            confidence=82,
            stop_loss_pct=2.0,
            take_profit_pct=4.0,
            headline="하이닉스 매수",
            briefing_summary="테스트",
            key_factors=["테스트"],
            risk_notes="테스트",
            next_watch="테스트",
        )
        t_buy, note = execute_decision(state, snap, buy_dec, asset=hynix)
        self.assertIsNotNone(t_buy)
        self.assertIsNone(note)
        # Must buy whole integer shares only
        self.assertEqual(t_buy["sol_qty"], float(int(t_buy["sol_qty"])))
        self.assertEqual(state["sol_qty"], float(int(state["sol_qty"])))
        self.assertAlmostEqual(t_buy["fee_krw"], round(t_buy["krw_amount"] * hynix.buy_fee_rate), delta=2)

        sell_dec = TradingDecision(
            action="SELL",
            order_pct=100.0,
            confidence=80,
            stop_loss_pct=2.0,
            take_profit_pct=4.0,
            headline="하이닉스 전량 매도",
            briefing_summary="테스트",
            key_factors=["테스트"],
            risk_notes="테스트",
            next_watch="테스트",
        )
        t_sell, _ = execute_decision(state, snap, sell_dec, asset=hynix)
        self.assertIsNotNone(t_sell)
        self.assertEqual(state["sol_qty"], 0.0)
        # KRX sell fee + STT (0.165%)
        self.assertAlmostEqual(t_sell["fee_krw"], round(t_sell["krw_amount"] * hynix.sell_fee_rate), delta=2)

    def test_nvda_usd_fractional_shares(self) -> None:
        nvda = ASSETS["nvda"]
        snap = _make_synthetic_snapshot(
            base_price=140.0,
            trend_per_bar=0.0,
            wave_amp=0.0,
            spot_override=140.0,
            market=nvda.symbol,
            source="yahoo",
            market_open=True,
            market_status_label="NASDAQ 정규장 개장",
            bar_range=0.8,
        )
        state = create_initial_state(snap, asset=nvda)
        self.assertEqual(state["initial_capital_krw"], 10_000.0)
        buy_dec = TradingDecision(
            action="BUY",
            order_pct=35.0,
            confidence=80,
            stop_loss_pct=2.0,
            take_profit_pct=4.0,
            headline="NVDA 매수",
            briefing_summary="테스트",
            key_factors=["테스트"],
            risk_notes="테스트",
            next_watch="테스트",
        )
        t_buy, note = execute_decision(state, snap, buy_dec, asset=nvda)
        self.assertIsNotNone(t_buy)
        self.assertIsNone(note)
        self.assertGreater(state["sol_qty"], 20.0)
        self.assertAlmostEqual(t_buy["fee_krw"], round(t_buy["krw_amount"] * nvda.buy_fee_rate, 2), delta=0.05)

    def test_closed_stock_market_initializes_without_phantom_trades(self) -> None:
        hynix = ASSETS["hynix"]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_dir = tmp_path / "data"
            dash_path = tmp_path / "docs" / "data" / "dashboard.json"
            readme_path = tmp_path / "README.md"
            readme_path.write_text("# Test\n<!-- LIVE_SUMMARY_START -->\nold\n<!-- LIVE_SUMMARY_END -->\n", encoding="utf-8")

            closed_snap = _make_synthetic_snapshot(
                base_price=210_000.0,
                trend_per_bar=500.0,
                wave_amp=600.0,
                market=hynix.symbol,
                source="yahoo",
                market_open=False,
                market_status_label="KRX 장 마감 (평일 09:00~15:30 KST)",
            )
            m1 = run_hourly_cycle(
                snapshots_by_asset={"hynix": closed_snap},
                asset_ids=["hynix"],
                data_dir=data_dir,
                dashboard_path=dash_path,
                readme_path=readme_path,
            )
            acc1 = m1["accounts"]["hynix"]
            self.assertEqual(acc1["summary"]["run_count"], 1)
            self.assertEqual(acc1["latest_briefing"]["decision"]["action"], "HOLD")
            self.assertEqual(acc1["summary"]["total_trades"], 0)
            self.assertFalse(acc1["market"]["market_open"])

            # Second closed run must NOT increment run_count or execute trades
            closed_snap2 = _make_synthetic_snapshot(
                base_price=210_000.0,
                trend_per_bar=500.0,
                wave_amp=600.0,
                hour_offset=1,
                market=hynix.symbol,
                source="yahoo",
                market_open=False,
                market_status_label="KRX 장 마감 (평일 09:00~15:30 KST)",
            )
            m2 = run_hourly_cycle(
                snapshots_by_asset={"hynix": closed_snap2},
                asset_ids=["hynix"],
                data_dir=data_dir,
                dashboard_path=dash_path,
                readme_path=readme_path,
            )
            acc2 = m2["accounts"]["hynix"]
            self.assertEqual(acc2["summary"]["run_count"], 1)
            self.assertEqual(len(acc2["series"]), 1)
            self.assertEqual(acc2["summary"]["total_trades"], 0)

    def test_multi_asset_cycle_and_legacy_sol_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_dir = tmp_path / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            dash_path = tmp_path / "docs" / "data" / "dashboard.json"
            readme_path = tmp_path / "README.md"
            readme_path.write_text("# Test\n<!-- LIVE_SUMMARY_START -->\nold\n<!-- LIVE_SUMMARY_END -->\n", encoding="utf-8")

            # Seed legacy SOL data with v1 model_name to verify seamless migration & version change detection
            legacy_sol_snap = _make_synthetic_snapshot(base_price=100_000.0, trend_per_bar=200.0, wave_amp=500.0, hour_offset=0)
            legacy_state = create_initial_state(legacy_sol_snap, asset=ASSETS["sol"])
            legacy_state["run_count"] = 1
            legacy_history = [
                {
                    "run_id": 1,
                    "time_kst": "2026-11-25 18:17:00 KST",
                    "time_utc": "2026-11-25T09:17:00Z",
                    "spot_price": 100_000.0,
                    "change_24h_pct": 1.0,
                    "equity_krw": 10_000_000.0,
                    "benchmark_krw": 10_000_000.0,
                    "return_pct": 0.0,
                    "benchmark_return_pct": 0.0,
                    "cash_krw": 10_000_000.0,
                    "sol_qty": 0.0,
                    "exposure_pct": 0.0,
                    "indicators": {"quant_score": 12.0, "rsi14": 50.0},
                    "decision": {"action": "HOLD", "model_name": "quant-multifactor-v1", "headline": "v1 관망"},
                    "executed_sides": [],
                }
            ]
            (data_dir / "state.json").write_text(json.dumps(legacy_state), encoding="utf-8")
            (data_dir / "history.json").write_text(json.dumps(legacy_history), encoding="utf-8")
            (data_dir / "trades.json").write_text("[]", encoding="utf-8")

            snaps = {
                "sol": _make_synthetic_snapshot(base_price=102_000.0, trend_per_bar=300.0, wave_amp=500.0, hour_offset=1),
                "hynix": _make_synthetic_snapshot(
                    base_price=215_000.0,
                    trend_per_bar=400.0,
                    wave_amp=800.0,
                    hour_offset=1,
                    market="000660.KS",
                    source="yahoo",
                    market_open=True,
                    market_status_label="KRX 정규장 개장",
                ),
                "nvda": _make_synthetic_snapshot(
                    base_price=142.0,
                    trend_per_bar=0.4,
                    wave_amp=0.6,
                    hour_offset=1,
                    market="NVDA",
                    source="yahoo",
                    market_open=True,
                    market_status_label="NASDAQ 정규장 개장",
                    bar_range=0.8,
                ),
            }
            multi = run_hourly_cycle(
                snapshots_by_asset=snaps,
                data_dir=data_dir,
                dashboard_path=dash_path,
                readme_path=readme_path,
            )
            self.assertEqual(multi["schema_version"], 2)
            self.assertEqual(set(multi["accounts"].keys()), {"sol", "hynix", "nvda"})

            sol_acc = multi["accounts"]["sol"]
            self.assertEqual(sol_acc["summary"]["run_count"], 2)
            self.assertEqual(len(sol_acc["version_changes"]), 1)
            self.assertEqual(sol_acc["version_changes"][0]["label"], "v2 전환")
            self.assertGreater(len(sol_acc["candles_recent"]), 50)
            self.assertTrue((data_dir / "accounts" / "sol" / "candles.json").exists())
            self.assertTrue((data_dir / "accounts" / "hynix" / "candles.json").exists())
            self.assertTrue((data_dir / "accounts" / "nvda" / "candles.json").exists())

            readme_txt = readme_path.read_text(encoding="utf-8")
            self.assertIn("솔라나", readme_txt)
            self.assertIn("SK하이닉스", readme_txt)
            self.assertIn("엔비디아", readme_txt)


if __name__ == "__main__":
    unittest.main()
