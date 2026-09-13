#!/usr/bin/env python3
"""
Main CLI Daemon Entry Point for Bybit Macro-Filtered AMD + FVG Bot.
Completely isolated from the single-leg trend hedging daemon.
Usage:
  python run_amd_bot.py --testnet
  python run_amd_bot.py --testnet --dry-run
  python run_amd_bot.py --symbols BTCUSDT,DOGEUSDT --testnet
"""

import os
import sys
import time
import signal
import argparse
import logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from amd_bot.config import LOG_FILE, ACTIVE_SYMBOLS, TESTNET
from amd_bot.client import AMDBitService
from amd_bot.engine import AMDEngine


def setup_logging(verbose: bool = False):
    """Configure isolated logging to stdout and amd_bot.log."""
    level = logging.DEBUG if verbose else logging.INFO
    log_format = "%(asctime)s [%(levelname)s] (%(name)s) %(message)s"

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ]

    logging.basicConfig(level=level, format=log_format, handlers=handlers)
    # Suppress verbose pybit websocket heartbeat logs
    logging.getLogger("pybit").setLevel(logging.WARNING)


def main():
    parser = argparse.ArgumentParser(description="Bybit Macro-Filtered AMD + FVG Bot Daemon (Isolated)")
    parser.add_argument("--testnet", action="store_true", default=True, help="Run on Bybit Testnet (Default: True)")
    parser.add_argument("--mainnet", action="store_true", help="Run on Bybit Mainnet")
    parser.add_argument("--dry-run", action="store_true", help="Run in simulation mode (live data, no real fills)")
    parser.add_argument("--symbols", type=str, default=",".join(ACTIVE_SYMBOLS), help="Comma-separated symbols")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger("AMDMain")

    testnet = not args.mainnet
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    mode_str = "DRY-RUN (SIMULATION)" if args.dry_run else ("TESTNET (LIVE ORDERS)" if testnet else "MAINNET (REAL MONEY)")

    logger.info("=" * 80)
    logger.info("BYBIT MACRO-FILTERED AMD + FVG TRADING BOT (ISOLATED ENGINE)")
    logger.info(f"Target Symbols: {', '.join(symbols)}")
    logger.info(f"Mode:           {mode_str}")
    logger.info(f"Log File:       {LOG_FILE}")
    logger.info("=" * 80)

    client = AMDBitService(is_dry_run=args.dry_run, testnet=testnet)
    engine = AMDEngine(client=client, symbols=symbols)

    def handle_shutdown(signum, frame):
        logger.info("\nShutdown signal received. Stopping AMD Bot cleanly...")
        engine.is_running = False
        if engine.ws:
            try:
                engine.ws.exit()
            except Exception:
                pass
        logger.info("AMD Bot stopped.")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Initialize market specs and seed historical klines
    engine.initialize()

    # Start live WebSocket listener
    engine.start()

    logger.info("AMD Bot active. Listening for 15m/4H klines and market sweeps...")

    scan_count = 0
    while engine.is_running:
        try:
            time.sleep(5.0)
            scan_count += 1
            # Every 60 seconds (12 x 5s), emit a high-signal heartbeat log
            if scan_count % 12 == 0:
                parts = []
                for s in symbols:
                    st = engine.pairs.get(s)
                    if st:
                        px_fmt = f"{st.mark_price:,.5f}" if st.mark_price < 1.0 else f"{st.mark_price:,.2f}"
                        age = f"{int(time.time() - st.last_tick_time)}s ago" if st.last_tick_time > 0 else "init"
                        parts.append(f"{s}: ${px_fmt} [{st.phase}, {age}]")
                if parts:
                    logger.info(f"[HEARTBEAT] Scanning | " + " | ".join(parts))
        except KeyboardInterrupt:
            handle_shutdown(None, None)


if __name__ == "__main__":
    main()
