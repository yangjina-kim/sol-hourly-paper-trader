"""Multi-asset configuration and risk guardrails for SOL (Crypto), SK Hynix (KRX), and NVIDIA (NASDAQ)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

# Project paths
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
ACCOUNTS_DIR = DATA_DIR / "accounts"
DOCS_DIR = ROOT_DIR / "docs"
DASHBOARD_DATA_PATH = DOCS_DIR / "data" / "dashboard.json"
README_PATH = ROOT_DIR / "README.md"

# Legacy single-asset paths (auto-migrated into data/accounts/sol/ on first run)
STATE_PATH = DATA_DIR / "state.json"
HISTORY_PATH = DATA_DIR / "history.json"
TRADES_PATH = DATA_DIR / "trades.json"

# Timezone
KST = timezone(timedelta(hours=9), name="KST")


@dataclass(frozen=True)
class AssetConfig:
    asset_id: str
    name: str
    short_name: str
    symbol: str
    yahoo_symbol: str | None
    market_type: str  # "crypto" | "kr_stock" | "us_stock"
    market_label: str
    quote_currency: str  # "KRW" | "USD"
    currency_symbol: str  # "원" | "$"
    price_decimals: int
    qty_decimals: int
    allow_fractional: bool
    initial_capital: float
    min_order_value: float
    buy_fee_rate: float
    sell_fee_rate: float
    base_slippage_rate: float


ASSETS: dict[str, AssetConfig] = {
    "sol": AssetConfig(
        asset_id="sol",
        name="솔라나",
        short_name="SOL",
        symbol="KRW-SOL",
        yahoo_symbol=None,
        market_type="crypto",
        market_label="가상화폐 · Upbit",
        quote_currency="KRW",
        currency_symbol="원",
        price_decimals=0,
        qty_decimals=4,
        allow_fractional=True,
        initial_capital=10_000_000.0,  # 1,000만 원
        min_order_value=10_000.0,
        buy_fee_rate=0.0005,  # 0.05% Upbit KRW market
        sell_fee_rate=0.0005,
        base_slippage_rate=0.0005,  # 5 bps floor + Upbit 15-level OB walk
    ),
    "hynix": AssetConfig(
        asset_id="hynix",
        name="SK하이닉스",
        short_name="000660",
        symbol="000660.KS",
        yahoo_symbol="000660.KS",
        market_type="kr_stock",
        market_label="국내주식 · KRX",
        quote_currency="KRW",
        currency_symbol="원",
        price_decimals=0,
        qty_decimals=0,
        allow_fractional=False,  # KRX 1주 정수 단위
        initial_capital=10_000_000.0,  # 1,000만 원
        min_order_value=100_000.0,
        buy_fee_rate=0.00015,  # 0.015% 국내 온라인 수수료
        sell_fee_rate=0.00165,  # 0.015% 수수료 + 0.15% 증권거래세
        base_slippage_rate=0.0006,  # 6 bps KRX 호가 스프레드
    ),
    "nvda": AssetConfig(
        asset_id="nvda",
        name="엔비디아",
        short_name="NVDA",
        symbol="NVDA",
        yahoo_symbol="NVDA",
        market_type="us_stock",
        market_label="미국주식 · NASDAQ",
        quote_currency="USD",
        currency_symbol="$",
        price_decimals=2,
        qty_decimals=2,
        allow_fractional=True,  # 소수점 0.01주 매매 지원
        initial_capital=10_000.0,  # $10,000 USD
        min_order_value=50.0,
        buy_fee_rate=0.0007,  # 0.07% 해외주식 온라인 수수료
        sell_fee_rate=0.0007,
        base_slippage_rate=0.0004,  # 4 bps NASDAQ 유동성 스프레드
    ),
}

ASSET_ORDER: tuple[str, ...] = ("sol", "hynix", "nvda")
DEFAULT_ASSET_ID = "sol"

# Backward-compatible default constants (SOL)
MARKET = ASSETS["sol"].symbol
ASSET_SYMBOL = ASSETS["sol"].short_name
QUOTE_CURRENCY = ASSETS["sol"].quote_currency
INITIAL_CAPITAL_KRW = ASSETS["sol"].initial_capital
FEE_RATE = ASSETS["sol"].buy_fee_rate
BASE_SLIPPAGE_RATE = ASSETS["sol"].base_slippage_rate

# Dynamic Slippage Calibration
VOL_SLIPPAGE_ATR_THRESHOLD_PCT = 0.8  # Apply extra drift slippage when 1h ATR(14) > 0.8%
VOL_SLIPPAGE_PER_ATR_PCT = 0.0004  # +4 bps per +1.0%p ATR above threshold
MAX_VOL_SLIPPAGE_RATE = 0.0015  # Cap volatility surcharge at 15 bps (0.15%)
STOP_LOSS_EXTRA_SLIPPAGE_RATE = 0.0012  # +12 bps (0.12%) panic-sweep surcharge on intrabar SL
TAKE_PROFIT_SLIPPAGE_RATE = 0.0001  # 1 bp (0.01%) minimal slippage on limit-style intrabar TP
MAX_TOTAL_SLIPPAGE_RATE = 0.0060  # Hard cap at 60 bps (0.60%) per fill

# Short-Term Trading Risk Guardrails
MAX_POSITION_RATIO = 0.70  # Max 70% of total equity in asset (min 30% cash buffer)
MIN_ORDER_KRW = 10_000.0
MIN_ORDER_PCT = 5.0  # Ignore tiny orders below 5% to prevent fee churn
DEFAULT_STOP_LOSS_PCT = 1.8
MIN_STOP_LOSS_PCT = 1.5
MAX_STOP_LOSS_PCT = 3.8
DEFAULT_TAKE_PROFIT_PCT = 3.2
MIN_TAKE_PROFIT_PCT = 2.2
MAX_TAKE_PROFIT_PCT = 6.5
BREAKEVEN_TRIGGER_PCT = 1.2  # Lock breakeven+cost SL once unrealized PnL >= +1.2%
TIME_STOP_HOURS = 12.0  # Exit stagnant short-term positions after 12h in flat range
DAILY_LOSS_LIMIT_PCT = 5.0  # Block new BUYs if daily drawdown exceeds -5.0%

# Cumulative Storage & Dashboard Payload Caps
CANDLE_STORE_LIMIT = 1500  # Keep up to 1,500 accumulated 1h candles per asset
DASHBOARD_CANDLES_LIMIT = 240  # Send up to 240 candles + strategy overlays to dashboard
DASHBOARD_SERIES_LIMIT = 720  # 30 days of hourly points
DASHBOARD_BRIEFINGS_LIMIT = 168  # 7 days of hourly briefings
DASHBOARD_TRADES_LIMIT = 200

HTTP_TIMEOUT_SECONDS = 25


def account_dir(asset_id: str, base_data_dir: Path = DATA_DIR) -> Path:
    return base_data_dir / "accounts" / asset_id
