"""Market data fetcher for KRW-SOL via Upbit Quotation REST API (with Bithumb fallback)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .config import HTTP_TIMEOUT_SECONDS, KST, MARKET

UPBIT_BASE = "https://api.upbit.com/v1"
BITHUMB_BASE = "https://api.bithumb.com/public"

DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "sol-hourly-paper-trader/1.0 (+https://github.com/yangjina-kim/sol-hourly-paper-trader)",
}


@dataclass(frozen=True)
class Candle:
    timestamp_ms: int
    time_utc: str
    time_kst: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketSnapshot:
    source: str
    market: str
    fetched_at_utc: str
    fetched_at_kst: str
    spot_price: float
    change_24h_pct: float
    change_24h_krw: float
    high_24h: float
    low_24h: float
    volume_24h_sol: float
    turnover_24h_krw: float
    best_ask: float
    best_bid: float
    spread_bps: float
    orderbook_imbalance: float  # -1.0 (ask heavy) to +1.0 (bid heavy)
    candles_1h: list[Candle]  # Completed closed 1h candles, oldest -> newest
    current_candle_1h: Candle | None  # Currently forming 1h candle
    candles_4h: list[Candle]  # 4h candles, oldest -> newest
    orderbook_asks: tuple[tuple[float, float], ...] = ()  # [(ask_price, ask_size_sol), ...] lowest -> highest
    orderbook_bids: tuple[tuple[float, float], ...] = ()  # [(bid_price, bid_size_sol), ...] highest -> lowest


def _http_get_json(url: str, retries: int = 3, backoff: float = 1.5) -> Any:
    last_err: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            last_err = exc
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"HTTP GET failed for {url}: {last_err}")


def _iso_from_upbit_utc(dt_str: str) -> tuple[int, str, str]:
    dt = datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)
    ts_ms = int(dt.timestamp() * 1000)
    return ts_ms, dt.strftime("%Y-%m-%dT%H:%M:%SZ"), dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def _parse_upbit_candles(rows: list[dict[str, Any]]) -> list[Candle]:
    candles: list[Candle] = []
    for row in rows:
        ts_ms, time_utc, time_kst = _iso_from_upbit_utc(row["candle_date_time_utc"])
        candles.append(
            Candle(
                timestamp_ms=ts_ms,
                time_utc=time_utc,
                time_kst=time_kst,
                open=float(row["opening_price"]),
                high=float(row["high_price"]),
                low=float(row["low_price"]),
                close=float(row["trade_price"]),
                volume=float(row["candle_acc_trade_volume"]),
                quote_volume=float(row["candle_acc_trade_price"]),
            )
        )
    # Upbit returns newest-first; sort chronologically oldest -> newest
    candles.sort(key=lambda c: c.timestamp_ms)
    return candles


def _split_forming_candle(candles: list[Candle], now_utc: datetime, interval_minutes: int) -> tuple[list[Candle], Candle | None]:
    """Separate the currently forming bar from completed bars so indicators never use half-baked volume."""
    if not candles:
        return [], None
    last = candles[-1]
    interval_ms = interval_minutes * 60 * 1000
    current_bucket_start_ms = (int(now_utc.timestamp() * 1000) // interval_ms) * interval_ms
    if last.timestamp_ms >= current_bucket_start_ms and len(candles) >= 2:
        return candles[:-1], last
    return candles, None


def fetch_upbit_snapshot(market: str = MARKET) -> MarketSnapshot:
    now_utc = datetime.now(timezone.utc)
    now_kst = now_utc.astimezone(KST)

    qs_1h = urllib.parse.urlencode({"market": market, "count": 200})
    qs_4h = urllib.parse.urlencode({"market": market, "count": 60})
    qs_m = urllib.parse.urlencode({"markets": market})

    raw_1h = _http_get_json(f"{UPBIT_BASE}/candles/minutes/60?{qs_1h}")
    time.sleep(0.15)  # Respect Upbit public rate limit (10 req/sec)
    raw_4h = _http_get_json(f"{UPBIT_BASE}/candles/minutes/240?{qs_4h}")
    time.sleep(0.15)
    raw_ticker = _http_get_json(f"{UPBIT_BASE}/ticker?{qs_m}")
    time.sleep(0.15)
    raw_ob = _http_get_json(f"{UPBIT_BASE}/orderbook?{qs_m}")

    all_1h = _parse_upbit_candles(raw_1h)
    closed_1h, current_1h = _split_forming_candle(all_1h, now_utc, interval_minutes=60)
    all_4h = _parse_upbit_candles(raw_4h)
    closed_4h, _ = _split_forming_candle(all_4h, now_utc, interval_minutes=240)

    ticker = raw_ticker[0]
    spot_price = float(ticker["trade_price"])
    change_24h_pct = float(ticker["signed_change_rate"]) * 100.0
    change_24h_krw = float(ticker["signed_change_price"])
    high_24h = float(ticker["high_price"])
    low_24h = float(ticker["low_price"])
    volume_24h_sol = float(ticker["acc_trade_volume_24h"])
    turnover_24h_krw = float(ticker["acc_trade_price_24h"])

    ob = raw_ob[0]
    units = ob.get("orderbook_units") or []
    best_ask = float(units[0]["ask_price"]) if units else spot_price
    best_bid = float(units[0]["bid_price"]) if units else spot_price
    mid = (best_ask + best_bid) / 2.0 if (best_ask + best_bid) > 0 else spot_price
    spread_bps = ((best_ask - best_bid) / mid * 10_000.0) if mid > 0 else 0.0

    # Top-5 orderbook depth imbalance & full 15-level ladder for depth-walk slippage
    top_units = units[:5]
    bid_sum = sum(float(u["bid_size"]) for u in top_units)
    ask_sum = sum(float(u["ask_size"]) for u in top_units)
    denom = bid_sum + ask_sum
    imbalance = (bid_sum - ask_sum) / denom if denom > 0 else 0.0

    asks = tuple(
        (float(u["ask_price"]), float(u["ask_size"]))
        for u in units
        if float(u.get("ask_price", 0.0)) > 0 and float(u.get("ask_size", 0.0)) > 0
    )
    bids = tuple(
        (float(u["bid_price"]), float(u["bid_size"]))
        for u in units
        if float(u.get("bid_price", 0.0)) > 0 and float(u.get("bid_size", 0.0)) > 0
    )

    return MarketSnapshot(
        source="upbit",
        market=market,
        fetched_at_utc=now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        fetched_at_kst=now_kst.strftime("%Y-%m-%d %H:%M:%S KST"),
        spot_price=spot_price,
        change_24h_pct=round(change_24h_pct, 3),
        change_24h_krw=round(change_24h_krw, 2),
        high_24h=high_24h,
        low_24h=low_24h,
        volume_24h_sol=round(volume_24h_sol, 4),
        turnover_24h_krw=round(turnover_24h_krw, 0),
        best_ask=best_ask,
        best_bid=best_bid,
        spread_bps=round(max(spread_bps, 0.0), 2),
        orderbook_imbalance=round(imbalance, 4),
        candles_1h=closed_1h,
        current_candle_1h=current_1h,
        candles_4h=closed_4h if closed_4h else all_4h,
        orderbook_asks=asks,
        orderbook_bids=bids,
    )


def _parse_bithumb_candles(rows: list[list[Any]]) -> list[Candle]:
    candles: list[Candle] = []
    for row in rows:
        # [timestamp_ms, open, close, high, low, volume]
        ts_ms = int(row[0])
        o, c, h, l, v = float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
        dt_utc = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        candles.append(
            Candle(
                timestamp_ms=ts_ms,
                time_utc=dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                time_kst=dt_utc.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
                open=o,
                high=h,
                low=l,
                close=c,
                volume=v,
                quote_volume=c * v,
            )
        )
    candles.sort(key=lambda x: x.timestamp_ms)
    return candles


def fetch_bithumb_snapshot(market: str = MARKET) -> MarketSnapshot:
    now_utc = datetime.now(timezone.utc)
    now_kst = now_utc.astimezone(KST)

    raw_1h = _http_get_json(f"{BITHUMB_BASE}/candlestick/SOL_KRW/1h")
    raw_4h = _http_get_json(f"{BITHUMB_BASE}/candlestick/SOL_KRW/4h")
    raw_ticker = _http_get_json(f"{BITHUMB_BASE}/ticker/SOL_KRW")

    all_1h = _parse_bithumb_candles(raw_1h["data"][-200:])
    closed_1h, current_1h = _split_forming_candle(all_1h, now_utc, interval_minutes=60)
    all_4h = _parse_bithumb_candles(raw_4h["data"][-60:])
    closed_4h, _ = _split_forming_candle(all_4h, now_utc, interval_minutes=240)

    t = raw_ticker["data"]
    spot_price = float(t["closing_price"])
    prev_close = float(t.get("prev_closing_price") or spot_price)
    change_krw = spot_price - prev_close
    change_pct = float(t.get("fluctate_rate_24H") or (change_krw / prev_close * 100.0 if prev_close else 0.0))

    return MarketSnapshot(
        source="bithumb",
        market=market,
        fetched_at_utc=now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        fetched_at_kst=now_kst.strftime("%Y-%m-%d %H:%M:%S KST"),
        spot_price=spot_price,
        change_24h_pct=round(change_pct, 3),
        change_24h_krw=round(change_krw, 2),
        high_24h=float(t.get("max_price") or spot_price),
        low_24h=float(t.get("min_price") or spot_price),
        volume_24h_sol=round(float(t.get("units_traded_24H") or 0.0), 4),
        turnover_24h_krw=round(float(t.get("acc_trade_value_24H") or 0.0), 0),
        best_ask=spot_price,
        best_bid=spot_price,
        spread_bps=4.0,
        orderbook_imbalance=0.0,
        candles_1h=closed_1h,
        current_candle_1h=current_1h,
        candles_4h=closed_4h if closed_4h else all_4h,
    )


def fetch_market_snapshot(market: str = MARKET) -> MarketSnapshot:
    """Fetch live KRW-SOL market snapshot from Upbit, falling back to Bithumb if needed."""
    try:
        return fetch_upbit_snapshot(market)
    except Exception as upbit_err:
        try:
            return fetch_bithumb_snapshot(market)
        except Exception as bithumb_err:
            raise RuntimeError(
                f"Both Upbit ({upbit_err}) and Bithumb ({bithumb_err}) market data fetches failed."
            ) from bithumb_err
