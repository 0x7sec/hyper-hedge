"""
run_backtest.py — CLI entry point for KuCoin Earn Bot Backtester.

Usage:
  python run_backtest.py                          # BTC-USDT, 2 years
  python run_backtest.py --symbol ETH-USDT        # ETH-USDT
  python run_backtest.py --symbol BTC-USDT --years 3
  python run_backtest.py --both                   # run BTC + ETH
"""

import argparse
import sys
import math
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console

console = Console(force_terminal=True, legacy_windows=False)


def main():
    parser = argparse.ArgumentParser(
        description="KuCoin Earn Bot Backtester — Dual Investment, Discount Buy, Range Bound"
    )
    parser.add_argument("--symbol", default="BTC-USDT", help="Symbol (default: BTC-USDT)")
    parser.add_argument("--years",  type=int, default=2,  help="Years of history (default: 2)")
    parser.add_argument("--both",   action="store_true",  help="Run both BTC-USDT and ETH-USDT")
    parser.add_argument("--mc-only",action="store_true",  help="Run Monte Carlo only (no backtest)")
    parser.add_argument("--no-save",action="store_true",  help="Don't save results to JSON")
    args = parser.parse_args()

    from kucoin_earn_bot.backtester import run_full_backtest, print_report, save_results

    symbols = ["BTC-USDT", "ETH-USDT"] if args.both else [args.symbol]

    for sym in symbols:
        try:
            t0      = time.time()
            results = run_full_backtest(sym, args.years)
            print_report(results, sym)
            if not args.no_save:
                save_results(results, sym)
            elapsed = time.time() - t0
            console.print(f"\n[dim]Completed in {elapsed:.1f}s[/dim]")
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted by user.[/yellow]")
            sys.exit(0)
        except Exception as e:
            console.print_exception()
            console.print(f"\n[red]Error running backtest for {sym}: {e}[/red]")
            sys.exit(1)


if __name__ == "__main__":
    main()
