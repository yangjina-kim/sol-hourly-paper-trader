"""Persistence layer, dashboard JSON compiler, and README / GitHub Actions summary updater."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .config import (
    ASSET_SYMBOL,
    BASE_SLIPPAGE_RATE,
    DAILY_LOSS_LIMIT_PCT,
    DASHBOARD_BRIEFINGS_LIMIT,
    DASHBOARD_DATA_PATH,
    DASHBOARD_SERIES_LIMIT,
    DASHBOARD_TRADES_LIMIT,
    DATA_DIR,
    FEE_RATE,
    HISTORY_PATH,
    INITIAL_CAPITAL_KRW,
    MARKET,
    MAX_POSITION_RATIO,
    MAX_STOP_LOSS_PCT,
    MAX_TAKE_PROFIT_PCT,
    MIN_ORDER_KRW,
    MIN_STOP_LOSS_PCT,
    MIN_TAKE_PROFIT_PCT,
    QUOTE_CURRENCY,
    README_PATH,
    STATE_PATH,
    TRADES_PATH,
)
from .market_data import MarketSnapshot


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def save_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def build_dashboard_payload(
    *,
    snapshot: MarketSnapshot,
    indicators: dict[str, Any],
    summary: dict[str, Any],
    latest_entry: dict[str, Any],
    history: list[dict[str, Any]],
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    series = [
        {
            "time_kst": h["time_kst"],
            "time_utc": h["time_utc"],
            "spot_price": h["spot_price"],
            "equity_krw": h["equity_krw"],
            "benchmark_krw": h["benchmark_krw"],
            "return_pct": h["return_pct"],
            "benchmark_return_pct": h["benchmark_return_pct"],
            "exposure_pct": h["exposure_pct"],
            "ema21": h["indicators"].get("ema21"),
            "ema50": h["indicators"].get("ema50"),
            "rsi14": h["indicators"].get("rsi14"),
            "action": h["decision"]["action"],
            "executed_sides": h.get("executed_sides", []),
        }
        for h in history[-DASHBOARD_SERIES_LIMIT:]
    ]

    briefings = list(reversed(history[-DASHBOARD_BRIEFINGS_LIMIT:]))
    recent_trades = list(reversed(trades[-DASHBOARD_TRADES_LIMIT:]))
    recent_candles = [
        {
            "time_kst": c.time_kst,
            "open": round(c.open, 1),
            "high": round(c.high, 1),
            "low": round(c.low, 1),
            "close": round(c.close, 1),
            "volume": round(c.volume, 2),
        }
        for c in snapshot.candles_1h[-72:]
    ]

    return {
        "generated_at_kst": snapshot.fetched_at_kst,
        "generated_at_utc": snapshot.fetched_at_utc,
        "meta": {
            "market": MARKET,
            "asset": ASSET_SYMBOL,
            "quote": QUOTE_CURRENCY,
            "exchange_source": snapshot.source,
            "initial_capital_krw": INITIAL_CAPITAL_KRW,
            "fee_rate_pct": round(FEE_RATE * 100.0, 3),
            "base_slippage_bps": round(BASE_SLIPPAGE_RATE * 10_000.0, 1),
            "max_position_ratio_pct": round(MAX_POSITION_RATIO * 100.0, 1),
            "min_order_krw": MIN_ORDER_KRW,
            "daily_loss_limit_pct": DAILY_LOSS_LIMIT_PCT,
            "stop_loss_range_pct": [MIN_STOP_LOSS_PCT, MAX_STOP_LOSS_PCT],
            "take_profit_range_pct": [MIN_TAKE_PROFIT_PCT, MAX_TAKE_PROFIT_PCT],
        },
        "market": {
            "spot_price": snapshot.spot_price,
            "change_24h_pct": snapshot.change_24h_pct,
            "change_24h_krw": snapshot.change_24h_krw,
            "high_24h": snapshot.high_24h,
            "low_24h": snapshot.low_24h,
            "volume_24h_sol": snapshot.volume_24h_sol,
            "turnover_24h_krw": snapshot.turnover_24h_krw,
            "spread_bps": snapshot.spread_bps,
            "orderbook_imbalance": snapshot.orderbook_imbalance,
        },
        "indicators": indicators,
        "summary": summary,
        "latest_briefing": latest_entry,
        "series": series,
        "candles_recent": recent_candles,
        "briefings": briefings,
        "trades": recent_trades,
    }


def persist_all(
    *,
    state: dict[str, Any],
    history: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    dashboard_payload: dict[str, Any],
    data_dir: Path = DATA_DIR,
    dashboard_path: Path = DASHBOARD_DATA_PATH,
) -> None:
    save_json_atomic(data_dir / STATE_PATH.name, state)
    save_json_atomic(data_dir / HISTORY_PATH.name, history)
    save_json_atomic(data_dir / TRADES_PATH.name, trades)
    save_json_atomic(dashboard_path, dashboard_payload)


def _format_signed_pct(val: float) -> str:
    return f"{val:+.2f}%"


def render_markdown_briefing(dashboard_payload: dict[str, Any]) -> str:
    m = dashboard_payload["market"]
    s = dashboard_payload["summary"]
    b = dashboard_payload["latest_briefing"]
    dec = b["decision"]

    action_label = {"BUY": "매수 (BUY)", "SELL": "매도 (SELL)", "HOLD": "관망 (HOLD)"}.get(
        dec["action"], dec["action"]
    )
    engine_label = f"정량 단타 전략 (`{dec['model_name']}`)"

    lines = [
        f"### 최신 정각 브리핑 ({b['time_kst']})",
        "",
        f"- **결정**: `{action_label}` (신뢰도 {dec['confidence']}%) — {dec['headline']}",
        f"- **전략 엔진**: {engine_label}",
        f"- **SOL 현재가**: {round(m['spot_price']):,}원 (24h {_format_signed_pct(m['change_24h_pct'])})",
        f"- **총 평가자산**: {round(s['total_equity_krw']):,}원 (누적 {_format_signed_pct(s['total_return_pct'])} / 손익 {round(s['total_pnl_krw']):+,}원)",
        f"- **단순 보유 대비**: B&H {_format_signed_pct(s['benchmark_return_pct'])} / 초과수익 `{s['alpha_pp']:+.2f}%p`",
        f"- **포지션**: 현금 {round(s['cash_krw']):,}원 | SOL {s['sol_qty']:.4f}개 (비중 {s['exposure_pct']:.1f}%)",
        f"- **누적 통계**: 실행 {s['run_count']}회 | 거래 {s['total_trades']}회 | 최대낙폭(MDD) {s['max_drawdown_pct']:.2f}% | 누적 수수료 {round(s['cumulative_fees_krw']):,}원 | 누적 슬리피지 {round(s.get('cumulative_slippage_krw', 0.0)):,}원",
        "",
        f"> {dec['briefing_summary']}",
    ]
    return "\n".join(lines)


def update_readme_summary(dashboard_payload: dict[str, Any], readme_path: Path = README_PATH) -> None:
    if not readme_path.exists():
        return
    start_marker = "<!-- LIVE_SUMMARY_START -->"
    end_marker = "<!-- LIVE_SUMMARY_END -->"
    text = readme_path.read_text(encoding="utf-8")
    if start_marker not in text or end_marker not in text:
        return
    summary_md = render_markdown_briefing(dashboard_payload)
    pattern = re.compile(rf"{re.escape(start_marker)}.*?{re.escape(end_marker)}", flags=re.DOTALL)
    replacement = f"{start_marker}\n{summary_md}\n{end_marker}"
    updated = pattern.sub(replacement, text, count=1)
    readme_path.write_text(updated, encoding="utf-8")


def write_github_step_summary(dashboard_payload: dict[str, Any]) -> None:
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not summary_file:
        return
    try:
        with open(summary_file, "a", encoding="utf-8") as fh:
            fh.write(render_markdown_briefing(dashboard_payload) + "\n")
    except OSError:
        pass
