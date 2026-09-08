#!/usr/bin/env python3
"""
Bybit Dual-Leg Gold (XAUUSDT) Trading Bot with pybit
Places simultaneous Long and Short orders with 3% Trailing SL and 6% TP.
"""

import sys
import os

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import logging
from bybit_bot.config import Config
from bybit_bot.client import BybitService
from bybit_bot.engine import BybitTradingEngine

# Setup basic logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    try:
        config = Config.from_args_and_env()
        service = BybitService(config)
        engine = BybitTradingEngine(service, config)
        engine.start()
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
    except Exception as e:
        logging.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
