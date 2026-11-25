"""Main hourly execution entrypoint for the SOL/KRW AI paper trading simulator."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .ai_engine import decide_trade
from .config import DASHBOARD_DATA_PATH, DATA_DIR, HISTORY_PATH, MARKET, README_PATH, STATE_PATH, TRADES_PATH
from .indicators import compute_indicators
from .market_data import MarketSnapshot, fetch_market_snapshot
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
    build_dashboard_payload,
    load_json_file,
    persist_all,
    render_markdown_briefing,
    update_readme_summary,
    write_github_step_summary,
)


def run_hourly_cycle(
    *,
    snapshot: MarketSnapshot | None = None,
    force_quant: bool = False,
    dry_run: bool = False,
    data_dir: Path = DATA_DIR,
    dashboard_path: Path = DASHBOARD_DATA_PATH,
    readme_path: Path = README_PATH,
) -> dict[str, Any]:
    """Execute one complete hourly data -> indicators -> risk check -> decision -> ledger -> report cycle."""
    if snapshot is None:
        snapshot = fetch_market_snapshot(MARKET)

    indicators = compute_indicators(snapshot)

    state = load_json_file(data_dir / STATE_PATH.name, None)
    history: list[dict[str, Any]] = load_json_file(data_dir / HISTORY_PATH.name, [])
    trades: list[dict[str, Any]] = load_json_file(data_dir / TRADES_PATH.name, [])

    if state is None:
        state = create_initial_state(snapshot)

    executed_this_cycle: list[dict[str, Any]] = []

    # 1. Check intrabar Stop-Loss / Take-Profit triggers from the elapsed hour
    atr14_pct = float(indicators.get("atr14_pct", 1.0))
    intrabar_trade = check_intrabar_triggers(state, snapshot, atr14_pct=atr14_pct)
    sl_cooldown_active = False
    if intrabar_trade is not None:
        executed_this_cycle.append(intrabar_trade)
        trades.append(intrabar_trade)
        if intrabar_trade["trigger"] == "stop_loss":
            sl_cooldown_active = True

    # 2. Refresh KST daily drawdown circuit breaker
    refresh_daily_guardrails(state, snapshot.spot_price, snapshot.fetched_at_kst)

    # 3. Build portfolio context & recent decision memory for the decision engine
    portfolio_ctx = build_portfolio_context(state, snapshot.spot_price, sl_cooldown_active=sl_cooldown_active)
    recent_decisions = [
        {
            "time_kst": h["time_kst"],
            "spot_price": h["spot_price"],
            "action": h["decision"]["action"],
            "confidence": h["decision"]["confidence"],
            "headline": h["decision"]["headline"],
        }
        for h in history[-5:]
    ]

    # 4. Run Gemini AI decision (or automatic Quant Fallback)
    decision = decide_trade(
        snapshot,
        indicators,
        portfolio_ctx,
        recent_decisions,
        force_quant=force_quant,
    )

    # 5. Execute decision with strict paper-trading guardrails
    decision_trade, guardrail_note = execute_decision(
        state,
        snapshot,
        decision,
        sl_cooldown_active=sl_cooldown_active,
        atr14_pct=atr14_pct,
    )
    if decision_trade is not None:
        executed_this_cycle.append(decision_trade)
        trades.append(decision_trade)

    # 6. Finalize cumulative metrics & benchmark comparison
    summary = finalize_cycle_metrics(state, snapshot, history)
    eq_after = portfolio_equity(state, snapshot.spot_price)
    bm_after = benchmark_equity(state, snapshot.spot_price)
    initial = float(state["initial_capital_krw"])

    history_entry = {
        "run_id": summary["run_count"],
        "time_kst": snapshot.fetched_at_kst,
        "time_utc": snapshot.fetched_at_utc,
        "spot_price": snapshot.spot_price,
        "change_24h_pct": snapshot.change_24h_pct,
        "equity_krw": round(eq_after, 0),
        "benchmark_krw": round(bm_after, 0),
        "return_pct": round(((eq_after / initial) - 1.0) * 100.0, 2) if initial > 0 else 0.0,
        "benchmark_return_pct": round(((bm_after / initial) - 1.0) * 100.0, 2) if initial > 0 else 0.0,
        "cash_krw": round(float(state["cash_krw"]), 0),
        "sol_qty": round(float(state["sol_qty"]), 6),
        "exposure_pct": summary["exposure_pct"],
        "indicators": indicators,
        "decision": decision.to_dict(),
        "guardrail_note": guardrail_note,
        "intrabar_event": intrabar_trade,
        "executed_trades": executed_this_cycle,
        "executed_sides": [t["side"] for t in executed_this_cycle],
    }
    history.append(history_entry)

    dashboard_payload = build_dashboard_payload(
        snapshot=snapshot,
        indicators=indicators,
        summary=summary,
        latest_entry=history_entry,
        history=history,
        trades=trades,
    )

    if not dry_run:
        persist_all(
            state=state,
            history=history,
            trades=trades,
            dashboard_payload=dashboard_payload,
            data_dir=data_dir,
            dashboard_path=dashboard_path,
        )
        update_readme_summary(dashboard_payload, readme_path=readme_path)
        write_github_step_summary(dashboard_payload)

    return dashboard_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one hourly KRW-SOL paper trading cycle.")
    parser.add_argument(
        "--force-quant",
        action="store_true",
        help="Skip Gemini API and run the deterministic multi-factor quant strategy.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute decision and print briefing without modifying state or dashboard files.",
    )
    args = parser.parse_args(argv)

    payload = run_hourly_cycle(force_quant=args.force_quant, dry_run=args.dry_run)
    print(render_markdown_briefing(payload))
    if args.dry_run:
        print("\n[dry-run] No files were modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
