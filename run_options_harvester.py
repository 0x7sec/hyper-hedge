#!/usr/bin/env python3
"""
run_options_harvester.py — Autonomous CLI Daemon for the Bybit UTA Delta-Neutral Options Harvester.
Monetizes the Volatility Risk Premium (VRP) using ~15-delta Short Strangles with DDH rebalancing.
Strict $1,000 USD isolated capital enclosure.

Usage:
  python run_options_harvester.py --dry-run                 # Simulated execution with live market tickers
  python run_options_harvester.py --live                    # Live Bybit UTA execution
  python run_options_harvester.py --once                    # Single tick evaluation
"""

import os
import sys
import time
import signal
import argparse
import logging
from typing import Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from options_harvester.config import (
    BYBIT_API_KEY,
    BYBIT_API_SECRET,
    TESTNET,
    ALLOCATED_CAPITAL,
    LOG_FILE,
    STATE_FILE,
    TRADES_FILE,
    TARGET_UNDERLYING,
    DDH_TOLERANCE_BAND,
)
from options_harvester.client import BybitOptionsClient
from options_harvester.engine import OptionsHarvesterEngine

console = Console(force_terminal=True, legacy_windows=False)

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
logger = logging.getLogger("options_harvester.daemon")


def print_status_dashboard(telemetry: dict):
    """Print an aesthetic status update to the console."""
    state = telemetry.get("state", "UNKNOWN")
    cycle_id = telemetry.get("cycle_id", 1)
    spot = telemetry.get("spot_price", 0.0)
    current_cap = telemetry.get("current_capital", 1000.0)
    pnl = telemetry.get("total_realized_pnl", 0.0)
    theta = telemetry.get("total_theta_harvested", 0.0)
    win_rate = telemetry.get("win_rate_pct", 0.0)
    greeks = telemetry.get("greeks", {})
    norm_delta = greeks.get("normalized_net_delta", 0.0)

    color = "green" if state == "STATE_2_HARVESTING" else ("yellow" if state == "STATE_0_SCANNING" else "magenta")

    t = Table(show_header=True, header_style="bold cyan", border_style="dim")
    t.add_column("Cycle", style="bold white", width=8)
    t.add_column("State", style=color, width=22)
    t.add_column("BTC Spot", justify="right", style="bold yellow", width=12)
    t.add_column("Net Delta", justify="right", style="bold cyan", width=12)
    t.add_column("Total PnL", justify="right", style="bold green" if pnl >= 0 else "bold red", width=12)
    t.add_column("Capital", justify="right", style="bold white", width=12)
    t.add_column("Win Rate", justify="right", style="bold magenta", width=10)

    t.add_row(
        f"#{cycle_id}",
        state,
        f"${spot:,.1f}",
        f"{norm_delta:+.4f}",
        f"${pnl:+.2f}",
        f"${current_cap:,.2f}",
        f"{win_rate:.1f}%",
    )
    console.print(t)

    # If active legs present, print strike cushion
    be = telemetry.get("breakevens", {})
    p = telemetry.get("active_put")
    c = telemetry.get("active_call")
    if p and c and be:
        console.print(
            f"  [dim]Active Strangle:[/dim] Put [bold]{p['symbol']}[/bold] (Strike: ${p['strike']:,.0f}) | "
            f"Call [bold]{c['symbol']}[/bold] (Strike: ${c['strike']:,.0f}) | "
            f"Breakevens: [green]${be['lower_breakeven']:,.0f}[/green] ↔ [green]${be['upper_breakeven']:,.0f}[/green] "
            f"([bold cyan]{be.get('range_width_pct', 0.0):.2f}% range cushion[/bold cyan])"
        )
    console.print(f"  [dim]Status:[/dim] {telemetry.get('last_status_message', '')}\n")


def main():
    parser = argparse.ArgumentParser(description="Bybit UTA Autonomous Delta-Neutral Options Harvester ($1,000 Budget)")
    parser.add_argument("--dry-run", action="store_true", help="Force simulated execution (no real orders)")
    parser.add_argument("--live", action="store_true", help="Enforce real order execution on Bybit UTA")
    parser.add_argument("--symbol", default="BTC", help="Base coin (default: BTC)")
    parser.add_argument("--once", action="store_true", help="Run a single evaluation step and exit")
    parser.add_argument("--interval", type=int, default=5, help="Loop interval in seconds (default: 5)")
    args = parser.parse_args()

    has_keys = bool(BYBIT_API_KEY and BYBIT_API_SECRET)
    dry_run = True if (args.dry_run or not has_keys or not args.live) else False
    mode_str = "[yellow]SIMULATION (DRY-RUN)[/yellow]" if dry_run else "[bold green]LIVE BYBIT UTA[/bold green]"

    console.print(Panel(
        f"[bold]Bybit UTA Delta-Neutral Options Harvester & DDH Engine[/bold]\n"
        f"Mode: {mode_str}\n"
        f"Underlying Asset: [bold white]{args.symbol}[/bold white] | Network: [magenta]{'Testnet' if TESTNET else 'Mainnet'}[/magenta]\n"
        f"Capital Enclosure: [bold green]${ALLOCATED_CAPITAL:,.2f} USD strict[/bold green]\n"
        f"DDH Tolerance Band: [cyan]±{DDH_TOLERANCE_BAND:.2f} Delta[/cyan] | Harvest Target: [green]70% Decay[/green]\n"
        f"Stop Loss: [red]2.0x collected premium[/red] | Expiry Cutoff: [yellow]T-120m Gamma Pin Guard[/yellow]\n"
        f"Telemetry Server: [bold white]http://localhost:8083[/bold white]",
        title="Options Harvester Initialization",
        border_style="cyan"
    ))

    client = BybitOptionsClient(dry_run=dry_run)
    engine = OptionsHarvesterEngine(client=client)

    running = True

    def signal_handler(sig, frame):
        nonlocal running
        console.print("\n[yellow]Received termination signal. Shutting down gracefully...[/yellow]")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if args.once:
        console.print("[cyan]Executing single tick...[/cyan]")
        telemetry = engine.tick()
        print_status_dashboard(telemetry)
        return

    console.print(f"[green]Daemon started. Running evaluation loop every {args.interval}s. Press Ctrl+C to stop.[/green]\n")

    tick_count = 0
    while running:
        try:
            telemetry = engine.tick()
            tick_count += 1
            if tick_count % 3 == 0 or engine.state != "STATE_2_HARVESTING":
                print_status_dashboard(telemetry)
        except Exception as e:
            logger.error(f"Unhandled exception in harvester loop: {e}", exc_info=True)

        time.sleep(args.interval)

    console.print("[bold green]Options Harvester shut down cleanly.[/bold green]")


if __name__ == "__main__":
    main()
