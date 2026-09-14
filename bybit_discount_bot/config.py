"""
config.py — Configuration and parameters for the Bybit Discount Buy Suite.
Enforces a strict $1,000 USD maximum capital limit per engine.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

# ── Bybit API Credentials ───────────────────────────────────────────────────
BYBIT_API_KEY: str = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET: str = os.getenv("BYBIT_API_SECRET", "")
TESTNET: bool = os.getenv("TESTNET", "false").lower() in ("true", "1", "yes")

# ── Capital Governance ──────────────────────────────────────────────────────
# STRICT $1,000 USD HARD CAP PER ENGINE
MAX_CAPITAL_PER_ENGINE: float = 1000.0
CIRCUIT_BREAKER_MAX_DD_PCT: float = 5.0   # Emergency halt if engine loses > 5%

# ── Default Trading Symbols ─────────────────────────────────────────────────
DEFAULT_SYMBOL_SPOT: str = "BTCUSDT"
DEFAULT_SYMBOL_PERP: str = "BTCUSDT"
DEFAULT_UNDERLYING_OPTION: str = "BTC"    # Bybit options settle in USDC

# ── Cycle Timeframes ────────────────────────────────────────────────────────
DEFAULT_CYCLE_HOURS: int = 24             # 24 hours aligns with Bybit daily options expiry
POLL_INTERVAL_SECONDS: int = 15           # Main loop evaluation frequency

# ── Engine 1: Options Cash-Secured Put Parameters ───────────────────────────
OPTIONS_TARGET_OTM_PCT: float = 0.010     # 1.0% OTM put strike
OPTIONS_MIN_APR_THRESHOLD: float = 0.15   # Only sell if annualized APR >= 15%
OPTIONS_MAX_DTE_HOURS: float = 30.0       # Look for options expiring within 30 hours

# ── Engine 2: Spot Maker Accumulator Parameters ─────────────────────────────
SPOT_DISCOUNT_PCT: float = 0.010          # 1.0% below spot
SPOT_LADDER_ENABLED: bool = True          # 3-tranche laddering:
SPOT_LADDER_TRANCHES = [
    {"pct": 0.35, "discount": 0.008},    # 35% at 0.8% discount
    {"pct": 0.35, "discount": 0.012},    # 35% at 1.2% discount
    {"pct": 0.30, "discount": 0.018},    # 30% at 1.8% discount
]
SPOT_TAKE_PROFIT_PCT: float = 0.012       # +1.2% trailing take profit on filled spot
SPOT_TRAILING_RATCHET_PCT: float = 0.004  # Ratchet trail distance

# ── Engine 3: Delta-Hedged Market-Neutral Parameters ─────────────────────────
NEUTRAL_DISCOUNT_PCT: float = 0.010       # 1.0% below spot
NEUTRAL_TARGET_SPREAD_PCT: float = 0.008  # Close cycle when net profit >= 0.8% or at cycle end

# ── State Persistence ───────────────────────────────────────────────────────
STATE_FILE_PATH: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "discount_state.json")
LOG_DIR: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
