"""Comprehensive unit and end-to-end simulation tests for the SOL/KRW paper trader."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from src.ai_engine import TradingDecision, _extract_json_object, _sanitize_decision, decide_quant_fallback, decide_trade
from src.config import FEE_RATE, INITIAL_CAPITAL_KRW, KST, MAX_POSITION_RATIO
from src.indicators import compute_indicators, rsi_series
from src.market_data import Candle, MarketSnapshot
from src.portfolio import (
    check_intrabar_triggers,
    create_initial_state,
    execute_decision,
    portfolio_equity,
    refresh_daily_guardrails,
)
from src.run_cycle import run_hourly_cycle


def _make_synthetic_snapshot(
    base_price: float = 250_000.0,
    trend_per_bar: float = 400.0,
    wave_amp: float = 2_500.0,
    hour_offset: int = 0,
    override_last_low: float | None = None,
    override_last_high: float | None = None,
    spot_override: float | None = None,
) -> MarketSnapshot:
    now_utc = datetime(2026, 11, 25, 12, 5, 0, tzinfo=timezone.utc) + timedelta(hours=hour_offset)
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
        self.assertEqual(up_ind["ema_alignment"], "bullish")
        self.assertEqual(up_ind["htf_trend"], "uptrend")
        self.assertGreater(up_ind["quant_score"], 20.0)
        self.assertTrue(0.0 <= up_ind["rsi14"] <= 100.0)

        down_snap = _make_synthetic_snapshot(base_price=320_000.0, trend_per_bar=-700.0, wave_amp=500.0)
        down_ind = compute_indicators(down_snap)
        self.assertEqual(down_ind["ema_alignment"], "bearish")
        self.assertEqual(down_ind["htf_trend"], "downtrend")
        self.assertLess(down_ind["quant_score"], -20.0)

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
            stop_loss_pct=2.5,
            take_profit_pct=5.0,
            headline="테스트 전액 매수 요청",
            briefing_summary="테스트",
            key_factors=["테스트"],
            risk_notes="테스트",
            next_watch="테스트",
            engine="gemini",
            model_name="gemini-3.8-flash",
        )
        trade, note = execute_decision(state, snap, buy_all)
        self.assertIsNotNone(trade)
        self.assertIsNone(note)
        eq = portfolio_equity(state, snap.spot_price)
        exposure = (state["sol_qty"] * snap.spot_price) / eq
        # Must respect MAX_POSITION_RATIO (70%) even when AI asked for 100%
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
            engine="quant_fallback",
            model_name="quant-multifactor-v1",
        )
        t_buy, _ = execute_decision(state, snap1, buy_dec)
        self.assertIsNotNone(t_buy)
        state["last_run_ts_ms"] = int(datetime(2026, 11, 25, 12, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)
        sl_price = state["stop_loss_price"]
        tp_price = state["take_profit_price"]
        self.assertIsNotNone(sl_price)
        self.assertIsNotNone(tp_price)

        # Next hour touches BOTH SL and TP -> conservative engine MUST fill SL first
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
        # Simulate -6% drop from daily start equity
        state["cash_krw"] = INITIAL_CAPITAL_KRW * 0.94
        refresh_daily_guardrails(state, snap.spot_price, snap.fetched_at_kst)
        self.assertTrue(state["daily_circuit_breaker_active"])

        buy_dec = TradingDecision(
            action="BUY",
            order_pct=40.0,
            confidence=90,
            stop_loss_pct=2.5,
            take_profit_pct=5.0,
            headline="매수 시도",
            briefing_summary="테스트",
            key_factors=["테스트"],
            risk_notes="테스트",
            next_watch="테스트",
            engine="gemini",
            model_name="gemini-3.8-flash",
        )
        trade, note = execute_decision(state, snap, buy_dec)
        self.assertIsNone(trade)
        self.assertIsNotNone(note)
        self.assertIn("차단", note)

    def test_json_extractor_and_gemini_fallback(self) -> None:
        fenced = '```json\n{"action": "buy", "order_pct": 150, "confidence": 120, "stop_loss_pct": 0.5, "take_profit_pct": 25, "headline": "테스트", "briefing_summary": "요약", "key_factors": ["팩터1"], "risk_notes": "리스크", "next_watch": "관전"}\n```'
        parsed = _extract_json_object(fenced)
        sanitized = _sanitize_decision(parsed, engine="gemini", model_name="gemini-3.8-flash")
        self.assertEqual(sanitized.action, "BUY")
        self.assertEqual(sanitized.order_pct, 100.0)
        self.assertEqual(sanitized.confidence, 100)
        self.assertEqual(sanitized.stop_loss_pct, 1.5)
        self.assertEqual(sanitized.take_profit_pct, 10.0)

        # Without GEMINI_API_KEY, decide_trade cleanly falls back to quant engine
        snap = _make_synthetic_snapshot()
        ind = compute_indicators(snap)
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": ""}, clear=False):
            dec = decide_trade(
                snap,
                ind,
                {
                    "exposure_pct": 0.0,
                    "sol_qty": 0.0,
                    "unrealized_pnl_pct": 0.0,
                    "daily_circuit_breaker_active": False,
                },
                [],
            )
        self.assertEqual(dec.engine, "quant_fallback")
        self.assertTrue(len(dec.briefing_summary) > 10)

    def test_end_to_end_multi_hour_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_dir = tmp_path / "data"
            dash_path = tmp_path / "docs" / "data" / "dashboard.json"
            readme_path = tmp_path / "README.md"
            readme_path.write_text("# Test\n<!-- LIVE_SUMMARY_START -->\nold\n<!-- LIVE_SUMMARY_END -->\n", encoding="utf-8")

            # Run 3 consecutive synthetic hours
            for h in range(3):
                snap = _make_synthetic_snapshot(
                    base_price=240_000.0 + h * 3_500.0,
                    trend_per_bar=600.0,
                    wave_amp=800.0,
                    hour_offset=h,
                )
                payload = run_hourly_cycle(
                    snapshot=snap,
                    force_quant=True,
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
            self.assertIn("최신 정각 브리핑", readme_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
