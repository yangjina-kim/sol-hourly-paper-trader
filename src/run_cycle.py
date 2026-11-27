"""Multi-asset hourly execution entrypoint for SOL (Crypto), SK Hynix (KRX), and NVIDIA (NASDAQ)."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import (
    ASSET_ORDER,
    ASSETS,
    DASHBOARD_DATA_PATH,
    DATA_DIR,
    HISTORY_PATH,
    KST,
    README_PATH,
    STATE_PATH,
    TRADES_PATH,
    AssetConfig,
    account_dir,
)
from .indicators import compute_indicators
from .market_data import Candle, MarketSnapshot, fetch_market_snapshot, merge_candles
from .portfolio import (
    benchmark_equity,
    build_portfolio_context,
    check_intrabar_triggers,
    create_initial_state,
    execute_decision,
    finalize_cycle_metrics,
    portfolio_equity,
    refresh_daily_guardrails,
)
from .report_builder import (
    build_asset_payload,
    build_multi_dashboard_payload,
    load_json_file,
    persist_account_files,
    render_multi_markdown_briefing,
    save_json_atomic,
    update_readme_summary,
    write_github_step_summary,
)
from .strategy import decide_trade


def _is_same_candle_already_bought(history: list[dict[str, Any]], current_closed_candle_kst: str) -> bool:
    for entry in reversed(history[-3:]):
        prev_candle = (entry.get("indicators") or {}).get("last_closed_candle_kst")
        if prev_candle != current_closed_candle_kst:
            break
        if "BUY" in (entry.get("executed_sides") or []):
            return True
    return False


def _load_account_records(
    asset: AssetConfig,
    base_data_dir: Path,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]], list[Candle]]:
    acc_path = account_dir(asset.asset_id, base_data_dir)
    state = load_json_file(acc_path / "state.json", None)
    history: list[dict[str, Any]] = load_json_file(acc_path / "history.json", [])
    trades: list[dict[str, Any]] = load_json_file(acc_path / "trades.json", [])
    raw_candles: list[dict[str, Any]] = load_json_file(acc_path / "candles.json", [])

    # Seamless one-time migration for legacy single-asset SOL files in data/*.json
    if state is None and asset.asset_id == "sol":
        legacy_state = load_json_file(base_data_dir / STATE_PATH.name, None)
        if legacy_state is not None:
            state = legacy_state
            history = load_json_file(base_data_dir / HISTORY_PATH.name, [])
            trades = load_json_file(base_data_dir / TRADES_PATH.name, [])

    candles = [Candle.from_dict(c) for c in raw_candles if isinstance(c, dict) and "timestamp_ms" in c]
    return state, history, trades, candles


def run_asset_cycle(
    asset: AssetConfig,
    *,
    snapshot: MarketSnapshot | None = None,
    dry_run: bool = False,
    data_dir: Path = DATA_DIR,
) -> dict[str, Any]:
    """Execute one hourly cycle for a single separate account (crypto or stock)."""
    if snapshot is None:
        snapshot = fetch_market_snapshot(asset)

    state, history, trades, persisted_candles = _load_account_records(asset, data_dir)

    # Accumulate 1h OHLCV candles persistently across runs
    incoming_candles = list(snapshot.candles_1h)
    if snapshot.current_candle_1h is not None:
        incoming_candles.append(snapshot.current_candle_1h)
    accumulated_candles = merge_candles(persisted_candles, incoming_candles)

    indicators = compute_indicators(snapshot)

    is_initial_boot = state is None
    if state is None:
        state = create_initial_state(snapshot, asset=asset)

    executed_this_cycle: list[dict[str, Any]] = []
    guardrail_note: str | None = None
    intrabar_trade: dict[str, Any] | None = None

    should_record_new_bar = snapshot.market_open or is_initial_boot or (len(history) == 0)

    if should_record_new_bar:
        atr14_pct = float(indicators.get("atr14_pct", 1.0))
        if snapshot.market_open:
            intrabar_trade = check_intrabar_triggers(state, snapshot, atr14_pct=atr14_pct, asset=asset)
        sl_cooldown_active = False
        if intrabar_trade is not None:
            executed_this_cycle.append(intrabar_trade)
            trades.append(intrabar_trade)
            if intrabar_trade["trigger"] == "stop_loss":
                sl_cooldown_active = True

        refresh_daily_guardrails(state, snapshot.spot_price, snapshot.fetched_at_kst)

        same_candle_buy_blocked = _is_same_candle_already_bought(
            history,
            str(indicators.get("last_closed_candle_kst", "")),
        )
        portfolio_ctx = build_portfolio_context(
            state,
            snapshot.spot_price,
            sl_cooldown_active=sl_cooldown_active,
            now_kst_str=snapshot.fetched_at_kst,
            same_candle_buy_blocked=same_candle_buy_blocked,
        )

        decision = decide_trade(snapshot, indicators, portfolio_ctx, asset=asset)

        if snapshot.market_open:
            decision_trade, guardrail_note = execute_decision(
                state,
                snapshot,
                decision,
                sl_cooldown_active=sl_cooldown_active,
                atr14_pct=atr14_pct,
                same_candle_buy_blocked=same_candle_buy_blocked,
                asset=asset,
            )
            if decision_trade is not None:
                executed_this_cycle.append(decision_trade)
                trades.append(decision_trade)

        summary = finalize_cycle_metrics(state, snapshot, history, asset=asset, increment_run=True)
        eq_after = portfolio_equity(state, snapshot.spot_price)
        bm_after = benchmark_equity(state, snapshot.spot_price)
        initial = float(state["initial_capital_krw"])
        val_dec = asset.price_decimals

        latest_entry = {
            "run_id": summary["run_count"],
            "time_kst": snapshot.fetched_at_kst,
            "time_utc": snapshot.fetched_at_utc,
            "spot_price": snapshot.spot_price,
            "change_24h_pct": snapshot.change_24h_pct,
            "equity_krw": round(eq_after, val_dec),
            "benchmark_krw": round(bm_after, val_dec),
            "return_pct": round(((eq_after / initial) - 1.0) * 100.0, 2) if initial > 0 else 0.0,
            "benchmark_return_pct": round(((bm_after / initial) - 1.0) * 100.0, 2) if initial > 0 else 0.0,
            "cash_krw": round(float(state["cash_krw"]), val_dec),
            "sol_qty": round(float(state["sol_qty"]), 6 if asset.allow_fractional else 0),
            "exposure_pct": summary["exposure_pct"],
            "indicators": indicators,
            "decision": decision.to_dict(),
            "guardrail_note": guardrail_note,
            "intrabar_event": intrabar_trade,
            "executed_trades": executed_this_cycle,
            "executed_sides": [t["side"] for t in executed_this_cycle],
        }
        history.append(latest_entry)
    else:
        # Stock market closed after initial boot: refresh candles, indicators & mark-to-market without duplicating history
        portfolio_ctx = build_portfolio_context(state, snapshot.spot_price, now_kst_str=snapshot.fetched_at_kst)
        decision = decide_trade(snapshot, indicators, portfolio_ctx, asset=asset)
        summary = finalize_cycle_metrics(state, snapshot, history, asset=asset, increment_run=False)
        eq_now = portfolio_equity(state, snapshot.spot_price)
        bm_now = benchmark_equity(state, snapshot.spot_price)
        initial = float(state["initial_capital_krw"])
        val_dec = asset.price_decimals
        latest_entry = {
            "run_id": summary["run_count"],
            "time_kst": snapshot.fetched_at_kst,
            "time_utc": snapshot.fetched_at_utc,
            "spot_price": snapshot.spot_price,
            "change_24h_pct": snapshot.change_24h_pct,
            "equity_krw": round(eq_now, val_dec),
            "benchmark_krw": round(bm_now, val_dec),
            "return_pct": round(((eq_now / initial) - 1.0) * 100.0, 2) if initial > 0 else 0.0,
            "benchmark_return_pct": round(((bm_now / initial) - 1.0) * 100.0, 2) if initial > 0 else 0.0,
            "cash_krw": round(float(state["cash_krw"]), val_dec),
            "sol_qty": round(float(state["sol_qty"]), 6 if asset.allow_fractional else 0),
            "exposure_pct": summary["exposure_pct"],
            "indicators": indicators,
            "decision": decision.to_dict(),
            "guardrail_note": None,
            "intrabar_event": None,
            "executed_trades": [],
            "executed_sides": [],
        }

    asset_payload = build_asset_payload(
        asset=asset,
        snapshot=snapshot,
        indicators=indicators,
        summary=summary,
        latest_entry=latest_entry,
        history=history,
        trades=trades,
        accumulated_candles=accumulated_candles,
    )

    if not dry_run:
        acc_path = account_dir(asset.asset_id, data_dir)
        persist_account_files(
            account_path=acc_path,
            state=state,
            history=history,
            trades=trades,
            candles=accumulated_candles,
        )

    return asset_payload


def run_hourly_cycle(
    *,
    snapshot: MarketSnapshot | None = None,
    snapshots_by_asset: dict[str, MarketSnapshot] | None = None,
    asset_ids: list[str] | None = None,
    dry_run: bool = False,
    data_dir: Path = DATA_DIR,
    dashboard_path: Path = DASHBOARD_DATA_PATH,
    readme_path: Path = README_PATH,
) -> dict[str, Any]:
    """Run hourly trading cycles across all configured separate accounts (SOL, SK Hynix, NVIDIA)."""
    # Backward compatibility: single snapshot kwarg runs SOL only and returns SOL asset payload
    if snapshot is not None and snapshots_by_asset is None and asset_ids is None:
        sol_payload = run_asset_cycle(ASSETS["sol"], snapshot=snapshot, dry_run=dry_run, data_dir=data_dir)
        if not dry_run:
            multi = build_multi_dashboard_payload(
                generated_at_kst=sol_payload["generated_at_kst"],
                generated_at_utc=sol_payload["generated_at_utc"],
                accounts={"sol": sol_payload},
            )
            save_json_atomic(dashboard_path, multi)
            update_readme_summary(multi, readme_path=readme_path)
        return sol_payload

    target_ids = asset_ids or list(ASSET_ORDER)
    existing_dashboard = load_json_file(dashboard_path, {})
    existing_accounts = existing_dashboard.get("accounts") if isinstance(existing_dashboard, dict) else {}
    if not isinstance(existing_accounts, dict):
        existing_accounts = {}

    accounts: dict[str, dict[str, Any]] = dict(existing_accounts)
    errors: list[str] = []

    for aid in target_ids:
        cfg = ASSETS[aid]
        override_snap = (snapshots_by_asset or {}).get(aid)
        try:
            accounts[aid] = run_asset_cycle(
                cfg,
                snapshot=override_snap,
                dry_run=dry_run,
                data_dir=data_dir,
            )
        except Exception as exc:
            errors.append(f"{cfg.name}({aid}): {exc}")
            print(f"[WARN] Failed cycle for {aid}: {exc}", file=sys.stderr)

    if not accounts:
        raise RuntimeError(f"All asset cycles failed: {'; '.join(errors)}")

    now_utc = datetime.now(timezone.utc)
    gen_utc = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    gen_kst = now_utc.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")

    multi_payload = build_multi_dashboard_payload(
        generated_at_kst=gen_kst,
        generated_at_utc=gen_utc,
        accounts=accounts,
    )

    if not dry_run:
        save_json_atomic(dashboard_path, multi_payload)
        update_readme_summary(multi_payload, readme_path=readme_path)
        write_github_step_summary(multi_payload)

    return multi_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run hourly multi-asset quant paper trading cycle (SOL, SK Hynix, NVDA).")
    parser.add_argument(
        "--assets",
        type=str,
        default="",
        help="Comma-separated asset IDs to run (default: sol,hynix,nvda).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute decisions and print briefing without modifying files.",
    )
    args = parser.parse_args(argv)

    asset_ids = [a.strip() for a in args.assets.split(",") if a.strip()] or None
    payload = run_hourly_cycle(asset_ids=asset_ids, dry_run=args.dry_run)
    print(render_multi_markdown_briefing(payload))
    if args.dry_run:
        print("\n[dry-run] No files were modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
