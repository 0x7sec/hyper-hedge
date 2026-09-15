"""
config.py — Configuration and hyperparameters for the Bybit UTA Delta-Neutral Options Harvester.
Strict $1,000 USD capital enclosure with automated Dynamic Delta Hedging (DDH).
"""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ==============================================================================
# BYBIT CREDENTIALS & ACCOUNT MODE
# ==============================================================================
BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TESTNET = os.environ.get("TESTNET", "False").lower() in ("true", "1", "yes")

# ==============================================================================
# CAPITAL ENCLOSURE & RISK GOVERNANCE
# ==============================================================================
ALLOCATED_CAPITAL = 1000.0          # Strict $1,000 USD isolated capital enclosure
CIRCUIT_BREAKER_MAX_DD_PCT = 0.05   # 5.0% maximum aggregate drawdown limit ($50 USD)
PREMIUM_STOP_LOSS_MULT = 2.0        # 2.0x collected premium hard stop on either leg
BASE_ORDER_QTY = 0.01               # Minimum BTC options lot size on Bybit

# ==============================================================================
# STRANGLE STRIKE SELECTION & GREEKS TARGETS
# ==============================================================================
TARGET_UNDERLYING = "BTC"           # Primary asset: BTC
BASE_PERP_SYMBOL = "BTCUSDT"        # Linear perpetual used for DDH micro-hedging

# Target option delta boundaries (Symmetrical Short Strangle)
TARGET_PUT_DELTA_MIN = -0.22
TARGET_PUT_DELTA_MAX = -0.10
IDEAL_PUT_DELTA = -0.15

TARGET_CALL_DELTA_MIN = +0.10
TARGET_CALL_DELTA_MAX = +0.22
IDEAL_CALL_DELTA = +0.15

# Allowed days to expiration (DTE) window
MIN_DTE_HOURS = 18.0                # Minimum 18 hours to avoid extreme immediate gamma
MAX_DTE_HOURS = 72.0                # Maximum 72 hours for high daily theta yield
TARGET_DTE_HOURS = 24.0             # Preferred daily settlement cycle (08:00 UTC)

# IV Rank Entry Filter
MIN_IV_RANK = 20.0                  # Minimum 30-day IV Rank (skip if IV is too suppressed)

# ==============================================================================
# DYNAMIC DELTA HEDGING (DDH) PARAMETERS
# ==============================================================================
DDH_TOLERANCE_BAND = 0.10           # Rebalance when |Net Delta| > 0.10
DDH_MIN_REBALANCE_QTY = 0.001       # Minimum micro-perp order size on Bybit
DDH_COOLDOWN_SECONDS = 15.0         # Minimum time between perpetual rebalance orders
ENABLE_PERP_DDH = True              # Enable micro-perp futures rebalancing
USE_DEFENSIVE_ROLL = True           # Enable rolling winning leg when deeply decayed

# ==============================================================================
# PROFIT HARVESTING & GAMMA PIN AVOIDANCE
# ==============================================================================
PROFIT_TARGET_DECAY_PCT = 0.70      # Close strangle early when 70% of premium has decayed
DEFENSIVE_ROLL_DECAY_PCT = 0.85     # Roll winning leg when 85% decayed to re-center delta
GAMMA_PIN_CUTOFF_MINUTES = 120      # Flatten all open legs 120m before 08:00 UTC expiry

# ==============================================================================
# SYSTEM & TELEMETRY PATHS
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "options_bot_state.json")
TRADES_FILE = os.path.join(BASE_DIR, "options_trades.csv")
LOG_FILE = os.path.join(BASE_DIR, "options_bot.log")

TELEMETRY_PORT = 8083
TELEMETRY_PASSWORD = os.environ.get("TELEMETRY_PASSWORD", "hedge_bot_sec_2026")
