"""Configuration constants and risk guardrails for the SOL/KRW paper trader."""

from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

# Project paths
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DOCS_DIR = ROOT_DIR / "docs"
DASHBOARD_DATA_PATH = DOCS_DIR / "data" / "dashboard.json"
STATE_PATH = DATA_DIR / "state.json"
HISTORY_PATH = DATA_DIR / "history.json"
TRADES_PATH = DATA_DIR / "trades.json"
README_PATH = ROOT_DIR / "README.md"

# Timezone
KST = timezone(timedelta(hours=9), name="KST")

# Market & Paper Trading Settings
MARKET = "KRW-SOL"
ASSET_SYMBOL = "SOL"
QUOTE_CURRENCY = "KRW"
INITIAL_CAPITAL_KRW = 10_000_000.0  # 1,000만 원

# Execution Realism (Upbit KRW Market)
FEE_RATE = 0.0005  # 0.05% per trade
BASE_SLIPPAGE_RATE = 0.0003  # 0.03% (3 bps) base market-order slippage

# Hard Risk Guardrails (enforced regardless of AI output)
MAX_POSITION_RATIO = 0.70  # Max 70% of total equity in SOL (min 30% KRW buffer)
MIN_ORDER_KRW = 10_000.0  # Minimum order size (KRW)
MIN_ORDER_PCT = 5.0  # Ignore tiny orders below 5% to prevent fee churn
DEFAULT_STOP_LOSS_PCT = 2.5  # -2.5%
MIN_STOP_LOSS_PCT = 1.5
MAX_STOP_LOSS_PCT = 5.0
DEFAULT_TAKE_PROFIT_PCT = 4.5  # +4.5%
MIN_TAKE_PROFIT_PCT = 2.0
MAX_TAKE_PROFIT_PCT = 10.0
DAILY_LOSS_LIMIT_PCT = 5.0  # Block new BUYs if KST daily drawdown exceeds -5.0%

# Dashboard Payload Caps (full history stays in data/history.json)
DASHBOARD_SERIES_LIMIT = 720  # 30 days of hourly points
DASHBOARD_BRIEFINGS_LIMIT = 168  # 7 days of hourly briefings
DASHBOARD_TRADES_LIMIT = 200

# Gemini API Configuration
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_PRIMARY_MODEL = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-3.8-flash"
GEMINI_FALLBACK_MODELS: tuple[str, ...] = tuple(
    dict.fromkeys([GEMINI_PRIMARY_MODEL, "gemini-3.8-flash", "gemini-3.5-flash-lite"])
)
HTTP_TIMEOUT_SECONDS = 25
