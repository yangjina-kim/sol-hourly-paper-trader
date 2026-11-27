"""Multi-asset persistence, strategy version change detection, dashboard JSON compiler, and Markdown summaries."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .config import (
    ASSET_ORDER,
    ASSETS,
    DAILY_LOSS_LIMIT_PCT,
    DASHBOARD_BRIEFINGS_LIMIT,
    DASHBOARD_CANDLES_LIMIT,
    DASHBOARD_DATA_PATH,
    DASHBOARD_SERIES_LIMIT,
    DASHBOARD_TRADES_LIMIT,
    DEFAULT_ASSET_ID,
    MAX_POSITION_RATIO,
    MAX_STOP_LOSS_PCT,
    MAX_TAKE_PROFIT_PCT,
    MIN_STOP_LOSS_PCT,
    MIN_TAKE_PROFIT_PCT,
    README_PATH,
    AssetConfig,
)
from .indicators import compute_candle_indicator_series
from .market_data import Candle, MarketSnapshot
from .strategy import format_price


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


def _short_version_label(model_name: str) -> str:
    match = re.search(r"v\d+(?:\.\d+)?", model_name, flags=re.IGNORECASE)
    if match:
        return f"{match.group(0).lower()} 전환"
    return f"{model_name} 전환"


def extract_version_changes(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect every timestamp where decision.model_name changed so charts can mark strategy upgrades."""
    changes: list[dict[str, Any]] = []
    prev_model: str | None = None
    for h in history:
        model = str((h.get("decision") or {}).get("model_name") or "").strip()
        if not model:
            continue
        if prev_model is not None and model != prev_model:
            changes.append(
                {
                    "run_id": h.get("run_id"),
                    "time_kst": h.get("time_kst"),
                    "time_utc": h.get("time_utc"),
                    "from_model": prev_model,
                    "to_model": model,
                    "label": _short_version_label(model),
                }
            )
        prev_model = model
    return changes


def build_asset_payload(
    *,
    asset: AssetConfig,
    snapshot: MarketSnapshot,
    indicators: dict[str, Any],
    summary: dict[str, Any],
    latest_entry: dict[str, Any],
    history: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    accumulated_candles: list[Candle],
) -> dict[str, Any]:
    series = [
        {
            "run_id": h.get("run_id"),
            "time_kst": h["time_kst"],
            "time_utc": h["time_utc"],
            "spot_price": h["spot_price"],
            "equity_krw": h["equity_krw"],
            "benchmark_krw": h["benchmark_krw"],
            "return_pct": h["return_pct"],
            "benchmark_return_pct": h["benchmark_return_pct"],
            "exposure_pct": h["exposure_pct"],
            "ema5": h["indicators"].get("ema5"),
            "ema13": h["indicators"].get("ema13"),
            "ema21": h["indicators"].get("ema21"),
            "ema50": h["indicators"].get("ema50"),
            "rsi14": h["indicators"].get("rsi14"),
            "quant_score": h["indicators"].get("quant_score"),
            "action": h["decision"]["action"],
            "model_name": h["decision"].get("model_name", ""),
            "executed_sides": h.get("executed_sides", []),
        }
        for h in history[-DASHBOARD_SERIES_LIMIT:]
    ]

    briefings = list(reversed(history[-DASHBOARD_BRIEFINGS_LIMIT:]))
    recent_trades = list(reversed(trades[-DASHBOARD_TRADES_LIMIT:]))
    candle_decimals = max(1, asset.price_decimals)
    candles_with_indicators = compute_candle_indicator_series(
        accumulated_candles,
        limit=DASHBOARD_CANDLES_LIMIT,
        decimals=candle_decimals,
    )
    version_changes = extract_version_changes(history[-DASHBOARD_SERIES_LIMIT:])

    return {
        "generated_at_kst": snapshot.fetched_at_kst,
        "generated_at_utc": snapshot.fetched_at_utc,
        "meta": {
            "asset_id": asset.asset_id,
            "name": asset.name,
            "short_name": asset.short_name,
            "market": asset.symbol,
            "market_type": asset.market_type,
            "market_label": asset.market_label,
            "asset": asset.short_name,
            "quote": asset.quote_currency,
            "currency_symbol": asset.currency_symbol,
            "price_decimals": asset.price_decimals,
            "qty_decimals": asset.qty_decimals,
            "allow_fractional": asset.allow_fractional,
            "exchange_source": snapshot.source,
            "market_open": snapshot.market_open,
            "market_status_label": snapshot.market_status_label,
            "initial_capital_krw": asset.initial_capital,
            "fee_rate_pct": round(asset.buy_fee_rate * 100.0, 4),
            "sell_fee_rate_pct": round(asset.sell_fee_rate * 100.0, 4),
            "base_slippage_bps": round(asset.base_slippage_rate * 10_000.0, 1),
            "max_position_ratio_pct": round(MAX_POSITION_RATIO * 100.0, 1),
            "min_order_krw": asset.min_order_value,
            "daily_loss_limit_pct": DAILY_LOSS_LIMIT_PCT,
            "stop_loss_range_pct": [MIN_STOP_LOSS_PCT, MAX_STOP_LOSS_PCT],
            "take_profit_range_pct": [MIN_TAKE_PROFIT_PCT, MAX_TAKE_PROFIT_PCT],
            "candles_accumulated": len(accumulated_candles),
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
            "market_open": snapshot.market_open,
            "market_status_label": snapshot.market_status_label,
        },
        "indicators": indicators,
        "summary": summary,
        "latest_briefing": latest_entry,
        "version_changes": version_changes,
        "series": series,
        "candles_recent": candles_with_indicators,
        "briefings": briefings,
        "trades": recent_trades,
    }


def build_multi_dashboard_payload(
    *,
    generated_at_kst: str,
    generated_at_utc: str,
    accounts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    ordered_ids = [aid for aid in ASSET_ORDER if aid in accounts]
    default_id = DEFAULT_ASSET_ID if DEFAULT_ASSET_ID in accounts else (ordered_ids[0] if ordered_ids else "sol")
    return {
        "schema_version": 2,
        "generated_at_kst": generated_at_kst,
        "generated_at_utc": generated_at_utc,
        "default_asset": default_id,
        "asset_order": ordered_ids,
        "accounts": accounts,
    }


def persist_account_files(
    *,
    account_path: Path,
    state: dict[str, Any],
    history: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    candles: list[Candle],
) -> None:
    save_json_atomic(account_path / "state.json", state)
    save_json_atomic(account_path / "history.json", history)
    save_json_atomic(account_path / "trades.json", trades)
    save_json_atomic(account_path / "candles.json", [c.to_dict() for c in candles])


def _format_signed_pct(val: float) -> str:
    return f"{val:+.2f}%"


def render_multi_markdown_briefing(multi_payload: dict[str, Any]) -> str:
    accounts = multi_payload.get("accounts") or {}
    gen_kst = multi_payload.get("generated_at_kst", "-")

    lines = [
        f"### 최신 정각 멀티 계좌 브리핑 ({gen_kst})",
        "",
        "| 종목 (계좌) | 상태 | 현재가 | 퀀트 점수 | 결정 | 총 평가자산 | 누적 수익률 | B&H 대비 |",
        "| --- | --- | ---: | ---: | :---: | ---: | ---: | ---: |",
    ]
    for aid in multi_payload.get("asset_order") or list(accounts.keys()):
        acc = accounts.get(aid)
        if not acc:
            continue
        cfg = ASSETS.get(aid) or ASSETS["sol"]
        m = acc["market"]
        s = acc["summary"]
        ind = acc["indicators"]
        dec = acc["latest_briefing"]["decision"]
        status_txt = "개장" if m.get("market_open", True) else "장마감"
        lines.append(
            f"| **{cfg.name}** (`{cfg.short_name}`) | {status_txt} | {format_price(m['spot_price'], cfg)} ({_format_signed_pct(m['change_24h_pct'])}) "
            f"| `{ind['quant_score']:+.1f}점` | `{dec['action']}` | {format_price(s['total_equity_krw'], cfg)} "
            f"| {_format_signed_pct(s['total_return_pct'])} | `{s['alpha_pp']:+.2f}%p` |"
        )

    lines.append("")
    for aid in multi_payload.get("asset_order") or list(accounts.keys()):
        acc = accounts.get(aid)
        if not acc:
            continue
        cfg = ASSETS.get(aid) or ASSETS["sol"]
        dec = acc["latest_briefing"]["decision"]
        lines.append(f"- **{cfg.name} ({cfg.short_name})**: {dec['headline']} — {dec['briefing_summary']}")

    return "\n".join(lines)


def update_readme_summary(multi_payload: dict[str, Any], readme_path: Path = README_PATH) -> None:
    if not readme_path.exists():
        return
    start_marker = "<!-- LIVE_SUMMARY_START -->"
    end_marker = "<!-- LIVE_SUMMARY_END -->"
    text = readme_path.read_text(encoding="utf-8")
    if start_marker not in text or end_marker not in text:
        return
    summary_md = render_multi_markdown_briefing(multi_payload)
    pattern = re.compile(rf"{re.escape(start_marker)}.*?{re.escape(end_marker)}", flags=re.DOTALL)
    replacement = f"{start_marker}\n{summary_md}\n{end_marker}"
    updated = pattern.sub(replacement, text, count=1)
    readme_path.write_text(updated, encoding="utf-8")


def write_github_step_summary(multi_payload: dict[str, Any]) -> None:
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not summary_file:
        return
    try:
        with open(summary_file, "a", encoding="utf-8") as fh:
            fh.write(render_multi_markdown_briefing(multi_payload) + "\n")
    except OSError:
        pass
