#!/usr/bin/env python3
"""
run_discount_suite.py — CLI Entrypoint for the Bybit UTA Discount Buy Suite.
Runs 3 independent engines with strict $1,000 capital enclosure each:
  1. Options Cash-Secured Put Underwriting
  2. Autonomous Spot Maker Limit Accumulator
  3. Delta-Hedged Market-Neutral Harvester

Usage:
  python run_discount_suite.py                         # Runs all 3 engines in live/dry-run mode
  python run_discount_suite.py --engine spot           # Runs only Spot Accumulator
  python run_discount_suite.py --engine neutral        # Runs only Delta-Neutral Harvester
  python run_discount_suite.py --engine options        # Runs only Options Engine
  python run_discount_suite.py --dry-run               # Enforces simulated execution
  python run_discount_suite.py --once                  # Executes a single step and exits
"""

import os
import sys
import argparse
import logging
from rich.console import Console
from rich.panel import Panel

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure immediate line-buffered flushing
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

from bybit_discount_bot.config import (
    BYBIT_API_KEY, BYBIT_API_SECRET, TESTNET,
    DEFAULT_SYMBOL_SPOT, MAX_CAPITAL_PER_ENGINE,
)
from bybit_discount_bot.client import BybitDiscountClient
from bybit_discount_bot.state import DiscountStateManager
from bybit_discount_bot.coordinator import SuiteCoordinator

console = Console(force_terminal=True, legacy_windows=False)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "discount_bot.log")

handlers = [
    logging.StreamHandler(sys.stdout),
    logging.FileHandler(LOG_FILE, encoding="utf-8"),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=handlers,
    force=True,
)
logger = logging.getLogger("discount_suite.main")


def main():
    parser = argparse.ArgumentParser(description="Bybit UTA Autonomous Discount Buy Suite ($1,000 Budget Each)")
    parser.add_argument("--engine", choices=["all", "options", "spot", "neutral"], default="all",
                        help="Which engine to run: all, options, spot, or neutral (default: all)")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL_SPOT, help=f"Spot/Perp symbol (default: {DEFAULT_SYMBOL_SPOT})")
    parser.add_argument("--dry-run", action="store_true", help="Force simulated dry-run mode (no real orders)")
    parser.add_argument("--live", action="store_true", help="Enforce real order execution on Bybit")
    parser.add_argument("--once", action="store_true", help="Run a single evaluation step and exit")
    args = parser.parse_args()

    # Determine execution mode: live requires API keys and --live flag, otherwise safe dry-run
    has_keys = bool(BYBIT_API_KEY and BYBIT_API_SECRET)
    dry_run = True if (args.dry_run or not has_keys or not args.live) else False

    mode_label = "[yellow]SIMULATED (DRY-RUN)[/yellow]" if dry_run else "[bold green]LIVE BYBIT UTA[/bold green]"
    engines = ["options", "spot", "neutral"] if args.engine == "all" else [args.engine]

    console.print(Panel(
        f"[bold]Bybit Autonomous Discount Buy Suite[/bold]\n"
        f"Mode: {mode_label}\n"
        f"Active Engines: [cyan]{', '.join(engines).upper()}[/cyan]\n"
        f"Capital Budget: [green]$1,000.00 USD strict per engine[/green] ($3,000 total)\n"
        f"Symbol: [white]{args.symbol}[/white] | Network: [magenta]{'Testnet' if TESTNET else 'Mainnet'}[/magenta]",
        title="System Initialization", border_style="cyan"
    ))

    client = BybitDiscountClient(dry_run=dry_run)
    state_mgr = DiscountStateManager(dry_run=dry_run)
    coordinator = SuiteCoordinator(client, state_mgr, enabled_engines=engines, symbol=args.symbol)

    if args.once:
        console.print("[yellow]Executing single step...[/yellow]")
        coordinator.step()
        console.print("[green][OK] Single step completed.[/green]")
        summary = state_mgr.get_summary_dict()
        console.print(f"Options Status: {summary['options_engine']['status']} | Spot Status: {summary['spot_engine']['status']} | Neutral Status: {summary['neutral_engine']['status']}")
    else:
        coordinator.start()


if __name__ == "__main__":
    main()
