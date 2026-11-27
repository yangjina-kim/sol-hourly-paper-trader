"""Multi-market data fetcher: Upbit/Bithumb (KRW-SOL) & Yahoo Finance v8 Chart API (KRX 000660.KS, NASDAQ NVDA)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .config import ASSETS, CANDLE_STORE_LIMIT, HTTP_TIMEOUT_SECONDS, KST, MARKET, AssetConfig

UPBIT_BASE = "https://api.upbit.com/v1"
BITHUMB_BASE = "https://api.bithumb.com/public"
YAHOO_HOSTS = (
    "https://query1.finance.yahoo.com/v8/finance/chart",
    "https://query2.finance.yahoo.com/v8/finance/chart",
)

KST_ZONE = ZoneInfo("Asia/Seoul")
ET_ZONE = ZoneInfo("America/New_York")

DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "sol-hourly-paper-trader/2.0 (+https://github.com/yangjina-kim/sol-hourly-paper-trader)",
}

YAHOO_HEADERS = {
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
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

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Candle":
        return cls(
            timestamp_ms=int(raw["timestamp_ms"]),
            time_utc=str(raw["time_utc"]),
            time_kst=str(raw["time_kst"]),
            open=float(raw["open"]),
            high=float(raw["high"]),
            low=float(raw["low"]),
            close=float(raw["close"]),
            volume=float(raw["volume"]),
            quote_volume=float(raw.get("quote_volume", float(raw["close"]) * float(raw["volume"]))),
        )


@dataclass(frozen=True)
class MarketSnapshot:
    source: str
    market: str
    fetched_at_utc: str
    fetched_at_kst: str
    spot_price: float
    change_24h_pct: float
    change_24h_krw: float  # Absolute change in quote currency (KRW or USD)
    high_24h: float
    low_24h: float
    volume_24h_sol: float  # 24h / recent session volume in asset units
    turnover_24h_krw: float  # 24h / recent session turnover in quote currency
    best_ask: float
    best_bid: float
    spread_bps: float
    orderbook_imbalance: float  # -1.0 (ask heavy) to +1.0 (bid heavy)
    candles_1h: list[Candle]  # Completed closed 1h candles, oldest -> newest
    current_candle_1h: Candle | None  # Currently forming 1h candle
    candles_4h: list[Candle]  # 4h candles, oldest -> newest
    orderbook_asks: tuple[tuple[float, float], ...] = ()
    orderbook_bids: tuple[tuple[float, float], ...] = ()
    market_open: bool = True
    market_status_label: str = "24시간 운영 중"


def is_market_open(market_type: str, now_utc: datetime | None = None) -> tuple[bool, str]:
    """Check whether regular trading session is currently open for crypto, KRX, or NASDAQ."""
    now = now_utc or datetime.now(timezone.utc)
    if market_type == "crypto":
        return True, "24시간 운영 중"

    if market_type == "kr_stock":
        now_kr = now.astimezone(KST_ZONE)
        if now_kr.weekday() >= 5:
            return False, "KRX 주말 휴장"
        hm = now_kr.hour * 60 + now_kr.minute
        # KRX regular session: 09:00 ~ 15:30 KST (allow small 5m grace after close)
        if 9 * 60 <= hm <= 15 * 60 + 35:
            return True, "KRX 정규장 개장"
        return False, "KRX 장 마감 (평일 09:00~15:30 KST)"

    if market_type == "us_stock":
        now_et = now.astimezone(ET_ZONE)
        if now_et.weekday() >= 5:
            return False, "NASDAQ 주말 휴장"
        hm = now_et.hour * 60 + now_et.minute
        # NASDAQ regular session: 09:30 ~ 16:00 ET (automatic DST via ZoneInfo)
        if 9 * 60 + 30 <= hm <= 16 * 60 + 5:
            return True, "NASDAQ 정규장 개장"
        return False, "NASDAQ 장 마감 (평일 09:30~16:00 ET)"

    return True, "운영 중"


def merge_candles(existing: list[Candle], incoming: list[Candle], max_len: int = CANDLE_STORE_LIMIT) -> list[Candle]:
    """Merge persisted 1h candles with freshly fetched candles, deduplicating by timestamp_ms."""
    by_ts: dict[int, Candle] = {c.timestamp_ms: c for c in existing}
    for c in incoming:
        by_ts[c.timestamp_ms] = c
    merged = sorted(by_ts.values(), key=lambda c: c.timestamp_ms)
    return merged[-max_len:] if len(merged) > max_len else merged


def aggregate_candles(candles_1h: list[Candle], group_size: int = 4) -> list[Candle]:
    """Aggregate 1h candles into higher-timeframe bars (used for stocks where Yahoo has no 4h interval)."""
    if not candles_1h:
        return []
    out: list[Candle] = []
    for i in range(0, len(candles_1h), group_size):
        chunk = candles_1h[i : i + group_size]
        if not chunk:
            continue
        vol = sum(c.volume for c in chunk)
        qvol = sum(c.quote_volume for c in chunk)
        out.append(
            Candle(
                timestamp_ms=chunk[0].timestamp_ms,
                time_utc=chunk[0].time_utc,
                time_kst=chunk[0].time_kst,
                open=chunk[0].open,
                high=max(c.high for c in chunk),
                low=min(c.low for c in chunk),
                close=chunk[-1].close,
                volume=vol,
                quote_volume=qvol,
            )
        )
    return out


def _http_get_json(
    url: str,
    *,
    headers: dict[str, str] = DEFAULT_HEADERS,
    retries: int = 3,
    backoff: float = 1.5,
) -> Any:
    last_err: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=headers, method="GET")
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
    time.sleep(0.15)
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
        market_open=True,
        market_status_label="24시간 운영 중",
    )


def _parse_bithumb_candles(rows: list[list[Any]]) -> list[Candle]:
    candles: list[Candle] = []
    for row in rows:
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
        spread_bps=6.0,
        orderbook_imbalance=0.0,
        candles_1h=closed_1h,
        current_candle_1h=current_1h,
        candles_4h=closed_4h if closed_4h else all_4h,
        market_open=True,
        market_status_label="24시간 운영 중",
    )


def _build_stock_snapshot_from_candles(
    candles: list[Candle],
    asset: AssetConfig,
    now_utc: datetime,
    *,
    meta: dict[str, Any] | None = None,
    source: str = "yahoo",
) -> MarketSnapshot:
    meta = meta or {}
    candles = sorted(candles, key=lambda c: c.timestamp_ms)
    if not candles:
        raise RuntimeError(f"Stock provider parsed zero valid candles for {asset.symbol}")

    market_open, market_status_label = is_market_open(asset.market_type, now_utc)
    if market_open:
        closed_1h, current_1h = _split_forming_candle(candles, now_utc, interval_minutes=60)
    else:
        closed_1h, current_1h = candles, None

    if not closed_1h:
        closed_1h = candles

    spot_price = float(meta.get("regularMarketPrice") or candles[-1].close)
    tz_local = KST_ZONE if asset.market_type == "kr_stock" else ET_ZONE
    prev_close_raw = meta.get("previousClose") or meta.get("regularMarketPreviousClose")
    if prev_close_raw is not None and float(prev_close_raw) > 0:
        prev_close = float(prev_close_raw)
    else:
        latest_local_date = datetime.fromtimestamp(candles[-1].timestamp_ms / 1000.0, tz=timezone.utc).astimezone(tz_local).date()
        prev_day_candles = [
            c
            for c in candles
            if datetime.fromtimestamp(c.timestamp_ms / 1000.0, tz=timezone.utc).astimezone(tz_local).date() < latest_local_date
        ]
        prev_close = prev_day_candles[-1].close if prev_day_candles else candles[0].open

    change_abs = spot_price - prev_close
    change_pct = ((spot_price / prev_close) - 1.0) * 100.0 if prev_close > 0 else 0.0

    recent_day = candles[-8:]
    high_day = float(meta.get("regularMarketDayHigh") or max(c.high for c in recent_day))
    low_day = float(meta.get("regularMarketDayLow") or min(c.low for c in recent_day))
    vol_day = float(meta.get("regularMarketVolume") or sum(c.volume for c in recent_day))
    turnover_day = vol_day * spot_price

    spread_bps = round(asset.base_slippage_rate * 2.0 * 10_000.0, 2)
    candles_4h = aggregate_candles(closed_1h, group_size=4)
    now_kst = now_utc.astimezone(KST)

    return MarketSnapshot(
        source=source,
        market=asset.symbol,
        fetched_at_utc=now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        fetched_at_kst=now_kst.strftime("%Y-%m-%d %H:%M:%S KST"),
        spot_price=round(spot_price, asset.price_decimals + 2),
        change_24h_pct=round(change_pct, 3),
        change_24h_krw=round(change_abs, asset.price_decimals + 2),
        high_24h=round(high_day, asset.price_decimals + 2),
        low_24h=round(low_day, asset.price_decimals + 2),
        volume_24h_sol=round(vol_day, 2),
        turnover_24h_krw=round(turnover_day, 2),
        best_ask=spot_price,
        best_bid=spot_price,
        spread_bps=spread_bps,
        orderbook_imbalance=0.0,
        candles_1h=closed_1h,
        current_candle_1h=current_1h,
        candles_4h=candles_4h,
        market_open=market_open,
        market_status_label=market_status_label,
    )


def _parse_yahoo_chart(raw: dict[str, Any], asset: AssetConfig, now_utc: datetime) -> MarketSnapshot:
    chart = (raw.get("chart") or {})
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"Yahoo Finance returned empty result for {asset.symbol}: {chart.get('error')}")

    res = results[0]
    meta = res.get("meta") or {}
    timestamps = res.get("timestamp") or []
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]

    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    vols = quote.get("volume") or []

    candles: list[Candle] = []
    for i, ts in enumerate(timestamps):
        if ts is None:
            continue
        o = opens[i] if i < len(opens) else None
        h = highs[i] if i < len(highs) else None
        l = lows[i] if i < len(lows) else None
        c = closes[i] if i < len(closes) else None
        v = vols[i] if i < len(vols) else 0
        if c is None:
            continue
        o_f = float(o) if o is not None else float(c)
        h_f = float(h) if h is not None else max(o_f, float(c))
        l_f = float(l) if l is not None else min(o_f, float(c))
        c_f = float(c)
        v_f = float(v) if v is not None else 0.0
        dt_utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        candles.append(
            Candle(
                timestamp_ms=int(ts) * 1000,
                time_utc=dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                time_kst=dt_utc.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
                open=round(o_f, asset.price_decimals + 2),
                high=round(h_f, asset.price_decimals + 2),
                low=round(l_f, asset.price_decimals + 2),
                close=round(c_f, asset.price_decimals + 2),
                volume=v_f,
                quote_volume=c_f * v_f,
            )
        )

    return _build_stock_snapshot_from_candles(candles, asset, now_utc, meta=meta, source="yahoo")


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return None if (f != f) else f  # NaN check
    except (TypeError, ValueError):
        return None


def _fetch_yfinance_snapshot(asset: AssetConfig, now_utc: datetime) -> MarketSnapshot:
    import yfinance as yf  # Optional dependency installed in CI for reliable stock sessions

    symbol = asset.yahoo_symbol or asset.symbol
    ticker = yf.Ticker(symbol)
    df = ticker.history(period="1mo", interval="60m", auto_adjust=False, prepost=False)
    if df is None or df.empty:
        raise RuntimeError(f"yfinance returned empty history for {symbol}")

    candles: list[Candle] = []
    for idx, row in df.iterrows():
        c_f = _safe_float(row.get("Close"))
        if c_f is None or c_f <= 0:
            continue
        o_f = _safe_float(row.get("Open")) or c_f
        h_f = _safe_float(row.get("High")) or max(o_f, c_f)
        l_f = _safe_float(row.get("Low")) or min(o_f, c_f)
        v_f = _safe_float(row.get("Volume")) or 0.0

        dt = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        if dt.tzinfo is None:
            dt_utc = dt.replace(tzinfo=timezone.utc)
        else:
            dt_utc = dt.astimezone(timezone.utc)
        ts_ms = int(dt_utc.timestamp() * 1000)

        candles.append(
            Candle(
                timestamp_ms=ts_ms,
                time_utc=dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                time_kst=dt_utc.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
                open=round(o_f, asset.price_decimals + 2),
                high=round(h_f, asset.price_decimals + 2),
                low=round(l_f, asset.price_decimals + 2),
                close=round(c_f, asset.price_decimals + 2),
                volume=v_f,
                quote_volume=c_f * v_f,
            )
        )

    meta: dict[str, Any] = {}
    try:
        fi = ticker.fast_info
        for src_key, dst_key in (
            ("last_price", "regularMarketPrice"),
            ("regular_market_previous_close", "regularMarketPreviousClose"),
            ("previous_close", "previousClose"),
            ("day_high", "regularMarketDayHigh"),
            ("day_low", "regularMarketDayLow"),
            ("last_volume", "regularMarketVolume"),
        ):
            try:
                v = _safe_float(fi.get(src_key) if hasattr(fi, "get") else getattr(fi, src_key, None))
                if v is not None and v > 0:
                    meta[dst_key] = v
            except Exception:
                continue
    except Exception:
        pass

    return _build_stock_snapshot_from_candles(candles, asset, now_utc, meta=meta, source="yfinance")


def fetch_yahoo_snapshot(asset: AssetConfig) -> MarketSnapshot:
    now_utc = datetime.now(timezone.utc)
    symbol = asset.yahoo_symbol or asset.symbol
    errors: list[str] = []

    try:
        return _fetch_yfinance_snapshot(asset, now_utc)
    except ImportError:
        errors.append("yfinance: not installed")
    except Exception as exc:
        errors.append(f"yfinance: {exc}")

    qs = urllib.parse.urlencode({"interval": "60m", "range": "1mo", "includePrePost": "false"})
    for host in YAHOO_HOSTS:
        url = f"{host}/{urllib.parse.quote(symbol)}?{qs}"
        try:
            raw = _http_get_json(url, headers=YAHOO_HEADERS, retries=2, backoff=1.2)
            return _parse_yahoo_chart(raw, asset, now_utc)
        except Exception as exc:
            errors.append(f"{host}: {exc}")
            time.sleep(0.5)
    raise RuntimeError(f"Yahoo Finance fetch failed for {symbol}: {'; '.join(errors)}")


def fetch_market_snapshot(asset: AssetConfig | str = MARKET) -> MarketSnapshot:
    """Fetch live market snapshot for either an AssetConfig or legacy market string ('KRW-SOL')."""
    if isinstance(asset, str):
        cfg = ASSETS["sol"] if asset == MARKET else next((a for a in ASSETS.values() if a.symbol == asset or a.asset_id == asset), ASSETS["sol"])
    else:
        cfg = asset

    if cfg.market_type == "crypto":
        try:
            return fetch_upbit_snapshot(cfg.symbol)
        except Exception as upbit_err:
            try:
                return fetch_bithumb_snapshot(cfg.symbol)
            except Exception as bithumb_err:
                raise RuntimeError(
                    f"Both Upbit ({upbit_err}) and Bithumb ({bithumb_err}) market data fetches failed."
                ) from bithumb_err

    return fetch_yahoo_snapshot(cfg)
