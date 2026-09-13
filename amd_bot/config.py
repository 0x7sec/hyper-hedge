#!/usr/bin/env python3
"""
Configuration & Champion Pair Profiles for Bybit AMD + FVG Bot.
Isolated from the single-leg trend hedge bot.
"""

import os
import sys
from decimal import Decimal
from dataclasses import dataclass, field
from typing import Dict, Any, List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Load environment variables from .env if present
env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))

# API Credentials & Environment
API_KEY = os.environ.get("BYBIT_API_KEY", "")
API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TESTNET = os.environ.get("TESTNET", "true").lower() in ["1", "true", "yes"]

# Isolated File Paths
STATE_FILE = os.path.join(BASE_DIR, "amd_bot_state.json")
TRADES_FILE = os.path.join(BASE_DIR, "amd_trades.csv")
LOG_FILE = os.path.join(BASE_DIR, "amd_bot.log")

# Portfolio Risk Settings
ACCOUNT_CAPITAL = float(os.environ.get("ACCOUNT_CAPITAL", "1000.0"))
MAX_CONCURRENT_PAIRS = int(os.environ.get("MAX_CONCURRENT_PAIRS", "2"))
MARGIN_PER_TRADE = float(os.environ.get("MARGIN_PER_TRADE", "250.0"))
DEFAULT_LEVERAGE = int(os.environ.get("LEVERAGE", "4"))

# Bybit API Endpoints
REST_URL = "https://api-testnet.bybit.com" if TESTNET else "https://api.bybit.com"
WS_LINEAR_URL = "wss://stream-testnet.bybit.com/v5/public/linear" if TESTNET else "wss://stream.bybit.com/v5/public/linear"
WS_PRIVATE_URL = "wss://stream-testnet.bybit.com/v5/private" if TESTNET else "wss://stream.bybit.com/v5/private"


@dataclass
class AMDPairProfile:
    symbol: str
    execution_tf: str = "15"             # 15-Minute Micro Timeframe
    macro_tf: str = "240"                # 4-Hour Macro Trend Timeframe
    macro_ema_period: int = 200          # 4H 200 EMA for Directional Bias
    range_lookback: int = 16             # 16 bars (4 hours of accumulation)
    max_range_pct: float = 0.030         # Maximum accumulation range width (3.0%)
    disp_body_min: float = 0.55          # 55% displacement candle body ratio
    sl_atr_buffer: float = 0.15          # SL buffer beyond sweep wick (0.15 ATR)
    rr_ratio: float = 2.5                # Minimum Risk/Reward target
    order_timeout_minutes: int = 60      # Cancel FVG limit if unfilled after 60 min
    base_size: float = 0.015             # Default contract size
    leverage: int = 4                    # Default leverage
    price_precision: int = 2
    qty_precision: int = 3
    min_order_qty: float = 0.001


# Champion Profiles for Supported Pairs
AMD_PROFILES: Dict[str, AMDPairProfile] = {
    "BTCUSDT": AMDPairProfile(
        symbol="BTCUSDT",
        execution_tf="15",
        macro_tf="240",
        macro_ema_period=200,
        range_lookback=16,
        max_range_pct=0.030,
        disp_body_min=0.55,
        sl_atr_buffer=0.15,
        rr_ratio=2.5,
        order_timeout_minutes=60,
        base_size=0.015,  # ~$900 - $1,000 notional
        leverage=4,
        price_precision=2,
        qty_precision=3,
        min_order_qty=0.001,
    ),
    "DOGEUSDT": AMDPairProfile(
        symbol="DOGEUSDT",
        execution_tf="15",
        macro_tf="240",
        macro_ema_period=200,
        range_lookback=16,
        max_range_pct=0.040,  # Slightly wider for DOGE volatility
        disp_body_min=0.55,
        sl_atr_buffer=0.15,
        rr_ratio=2.8,  # Higher RR for DOGE runners
        order_timeout_minutes=60,
        base_size=12000.0,  # ~$1,000 notional
        leverage=4,
        price_precision=5,
        qty_precision=0,
        min_order_qty=1.0,
    ),
    "SOLUSDT": AMDPairProfile(
        symbol="SOLUSDT",
        execution_tf="15",
        macro_tf="240",
        macro_ema_period=200,
        range_lookback=16,
        max_range_pct=0.035,
        disp_body_min=0.55,
        sl_atr_buffer=0.15,
        rr_ratio=2.5,
        order_timeout_minutes=60,
        base_size=7.0,  # ~$1,000 notional
        leverage=4,
        price_precision=3,
        qty_precision=2,
        min_order_qty=0.1,
    ),
    "ETHUSDT": AMDPairProfile(
        symbol="ETHUSDT",
        execution_tf="15",
        macro_tf="240",
        macro_ema_period=200,
        range_lookback=16,
        max_range_pct=0.030,
        disp_body_min=0.55,
        sl_atr_buffer=0.15,
        rr_ratio=2.5,
        order_timeout_minutes=60,
        base_size=0.35,  # ~$900 - $1,000 notional
        leverage=4,
        price_precision=2,
        qty_precision=3,
        min_order_qty=0.01,
    ),
}

ACTIVE_SYMBOLS: List[str] = list(AMD_PROFILES.keys())
