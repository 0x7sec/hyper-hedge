"""
Comprehensive strategy verification script:
- Run BTC/ETH/SOL backtests with production parameters (15m, EMA9/21, ADX>=15/0/0, SL6%, TP2.5%)
- Run Walk-Forward, Monte Carlo, and Permutation validation for each
- Compare live bot trade structure vs backtest signal logic
"""
import sys
import numpy as np
from decimal import Decimal
from datetime import datetime

# Add project root to path
sys.path.insert(0, '.')

from backtest import (
    fetch_historical_candles,
    run_single_backtest,
    validate_walk_forward,
    validate_monte_carlo,
    validate_permutation,
    print_backtest_report,
)
from rich.console import Console
from rich.rule import Rule
from rich.table import Table

console = Console()

CONFIGS = [
    {
        "symbol": "BTCUSDT",
        "interval": "15",
        "candles": 5000,
        "sl_pct": Decimal("6.0"),
        "tp_pct": Decimal("2.5"),
        "size": Decimal("0.007"),
        "leverage": 4,
        "ema_fast": 9,
        "ema_slow": 21,
        "adx_min": Decimal("15"),
        "adx_period": 14,
        "capital": Decimal("9818"),
        "label": "BTC (EMA9/21 + ADX>15, SL6%, TP2.5%)",
    },
    {
        "symbol": "ETHUSDT",
        "interval": "15",
        "candles": 5000,
        "sl_pct": Decimal("6.0"),
        "tp_pct": Decimal("2.5"),
        "size": Decimal("0.20"),
        "leverage": 4,
        "ema_fast": 9,
        "ema_slow": 21,
        "adx_min": Decimal("0"),
        "adx_period": 14,
        "capital": Decimal("9818"),
        "label": "ETH (EMA9/21, no ADX filter, SL6%, TP2.5%)",
    },
    {
        "symbol": "SOLUSDT",
        "interval": "15",
        "candles": 5000,
        "sl_pct": Decimal("6.0"),
        "tp_pct": Decimal("2.5"),
        "size": Decimal("5"),
        "leverage": 4,
        "ema_fast": 9,
        "ema_slow": 21,
        "adx_min": Decimal("0"),
        "adx_period": 14,
        "capital": Decimal("9818"),
        "label": "SOL (EMA9/21, no ADX filter, SL6%, TP2.5%)",
    },
]

all_results = {}

for cfg in CONFIGS:
    sym = cfg["symbol"]
    console.print()
    console.print(Rule(f"[bold cyan]{cfg['label']}[/bold cyan]"))
    
    candles = fetch_historical_candles(sym, cfg["interval"], cfg["candles"])
    
    results = run_single_backtest(
        candles,
        sl_pct=cfg["sl_pct"],
        tp_pct=cfg["tp_pct"],
        position_size=cfg["size"],
        leverage=cfg["leverage"],
        initial_capital=cfg["capital"],
        include_fees=True,
        indicator="ema_cross",
        ema_fast=cfg["ema_fast"],
        ema_slow=cfg["ema_slow"],
        ema_trend=200,
        adx_min=cfg["adx_min"],
        adx_period=cfg["adx_period"],
        be_lock=True,
        be_buffer_pct=Decimal("0.20"),
    )
    
    if "error" in results:
        console.print(f"[red]No trades for {sym}: {results['error']}[/red]")
        continue
    
    all_results[sym] = results
    
    # Print main report
    print_backtest_report(results, cfg["capital"])
    
    # Statistical validation
    console.print(f"\n[bold yellow]=== STATISTICAL VALIDATION: {sym} ===[/bold yellow]")
    validate_walk_forward(candles, results)
    validate_monte_carlo(results, n_iter=5000)
    validate_permutation(candles, results, n_iter=500)

# Summary comparison table
if all_results:
    console.print()
    console.print(Rule("[bold green]PORTFOLIO SUMMARY[/bold green]"))
    table = Table(title="Strategy Performance vs Live Testnet Results", border_style="green")
    table.add_column("Symbol", style="bold")
    table.add_column("Backtest Trades")
    table.add_column("Backtest Win%")
    table.add_column("Backtest Net PnL")
    table.add_column("Profit Factor")
    table.add_column("Max DD")
    table.add_column("Live Testnet PnL")
    
    live_pnl = {"BTCUSDT": -28.86, "ETHUSDT": -8.40, "SOLUSDT": -1.34}
    
    for sym, r in all_results.items():
        net = float(r["total_net_pnl_usd"])
        col = "green" if net >= 0 else "red"
        lp = live_pnl.get(sym, 0)
        lp_col = "green" if lp >= 0 else "red"
        table.add_row(
            sym,
            str(r["total_cycles"]),
            f"{r['win_rate']:.1f}%",
            f"[{col}]${net:+.2f}[/{col}]",
            f"{float(r['profit_factor']):.2f}",
            f"${float(r['max_drawdown_usd']):.2f}",
            f"[{lp_col}]${lp:+.2f}[/{lp_col}]",
        )
    
    total_live = sum(live_pnl.values())
    table.add_row("[bold]TOTAL[/bold]", "", "", "", "", "", f"[bold]${total_live:+.2f}[/bold]")
    console.print(table)
    
    console.print()
    console.print("[dim]Live PnL is from Bybit testnet actual trades (2026-09-08 to 2026-09-09)[/dim]")
    console.print("[dim]Note: Live SOLUSDT Short TP counter-close was affected by EC_NoImmediateQtyToFill[/dim]")
    console.print("[dim]      (fixed in client.py -- retry + GTC limit fallback now active)[/dim]")
