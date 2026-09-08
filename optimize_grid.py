#!/usr/bin/env python3
"""
Exhaustive SL/TP Grid Optimizer for Bybit Gold (XAUUSDT) Dual-Leg Hedge Strategy.
Runs across 3 months of 15m candles with EMA(20/50) crossover at 3x leverage.
"""

import sys
from decimal import Decimal
from typing import List, Dict, Any
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from backtest import fetch_historical_candles, run_single_backtest

console = Console()

def run_grid_optimization():
    candles = fetch_historical_candles("XAUUSDT", "15", target_candles=9000)
    if not candles:
        console.print("[red]Failed to load candles.[/red]")
        return

    leverage = 3
    fast = 20
    slow = 50
    initial_capital = Decimal("10000")

    # Define candidate SL and TP ranges
    sl_candidates = [
        Decimal("1.0"), Decimal("1.5"), Decimal("2.0"), Decimal("2.5"),
        Decimal("3.0"), Decimal("3.5"), Decimal("4.0"), Decimal("4.5"), Decimal("5.0")
    ]
    tp_candidates = [
        Decimal("2.0"), Decimal("2.5"), Decimal("3.0"), Decimal("3.5"),
        Decimal("4.0"), Decimal("4.5"), Decimal("5.0"), Decimal("5.5"),
        Decimal("6.0"), Decimal("6.5"), Decimal("7.0"), Decimal("7.5"),
        Decimal("8.0"), Decimal("9.0"), Decimal("10.0")
    ]

    results = []
    total_combinations = sum(1 for sl in sl_candidates for tp in tp_candidates if tp >= sl)
    console.print(f"[cyan]Evaluating [bold yellow]{total_combinations}[/bold yellow] parameter combinations (SL from 1.0% to 5.0%, TP from 2.0% to 10.0%)...[/cyan]")

    for sl in sl_candidates:
        for tp in tp_candidates:
            if tp < sl:
                continue

            res = run_single_backtest(
                candles,
                sl_pct=sl,
                tp_pct=tp,
                leverage=leverage,
                indicator="ema_cross",
                ema_fast=fast,
                ema_slow=slow,
                include_fees=True,
            )

            if "error" in res or res.get("total_cycles", 0) == 0:
                continue

            pnl = res["total_net_pnl_usd"]
            cycles = res["total_cycles"]
            chop_count = res["double_sl_count"]
            chop_pct = (chop_count / cycles) * 100 if cycles else 0
            ret_pct = (pnl / initial_capital) * 100
            pf = res["profit_factor"]
            max_dd = res["max_drawdown_usd"]
            ret_dd_ratio = (pnl / max_dd) if max_dd > 0 else Decimal("999")

            results.append({
                "sl": sl,
                "tp": tp,
                "rr": f"{tp / sl:.1f}:1",
                "cycles": cycles,
                "win_rate": res["win_rate"],
                "chop_pct": chop_pct,
                "net_pnl": pnl,
                "ret_pct": ret_pct,
                "pf": pf,
                "max_dd": max_dd,
                "ret_dd_ratio": ret_dd_ratio,
            })

    # Sort by Net PnL descending
    results_by_pnl = sorted(results, key=lambda x: x["net_pnl"], reverse=True)

    # Print Top 15 Profitable Setups Table
    table = Table(
        title=f"TOP 15 MOST PROFITABLE SL / TP CONFIGURATIONS (XAUUSDT | 3 Months | EMA 20/50 | Leverage: {leverage}x)",
        border_style="cyan"
    )
    table.add_column("Rank", style="bold yellow")
    table.add_column("SL % (Trailing)", style="red")
    table.add_column("TP %", style="green")
    table.add_column("R:R Ratio", style="magenta")
    table.add_column("Cycles", style="cyan")
    table.add_column("Win Rate")
    table.add_column("Double SL (Chop)", style="yellow")
    table.add_column("Net Profit ($)", style="bold green")
    table.add_column("Return %", style="bold green")
    table.add_column("Profit Factor")
    table.add_column("Max DD ($)", style="red")
    table.add_column("Return/DD", style="bold cyan")

    for rank, r in enumerate(results_by_pnl[:15], start=1):
        table.add_row(
            f"#{rank}",
            f"{r['sl']}%",
            f"{r['tp']}%",
            r["rr"],
            f"{r['cycles']}",
            f"{r['win_rate']:.1f}%",
            f"{r['chop_pct']:.1f}%",
            f"${r['net_pnl']:+.2f}",
            f"{r['ret_pct']:+.2f}%",
            f"{r['pf']:.2f}",
            f"${r['max_dd']:.2f}",
            f"{r['ret_dd_ratio']:.2f}x",
        )

    console.print("\n")
    console.print(table)

    # Print full untruncated Markdown table
    print("\n" + "="*110)
    print("TOP 15 MOST PROFITABLE SL / TP CONFIGURATIONS (XAUUSDT | 3 Months | EMA 20/50 | Leverage: 3x)")
    print("="*110)
    header = f"{'Rank':<5} | {'SL %':<6} | {'TP %':<6} | {'R:R':<6} | {'Cycles':<6} | {'Win %':<7} | {'Chop %':<7} | {'Net PnL ($)':<12} | {'Return %':<10} | {'Profit Factor':<13} | {'Max DD ($)':<10} | {'Return/DD':<9}"
    print(header)
    print("-" * len(header))
    for rank, r in enumerate(results_by_pnl[:15], start=1):
        line = (
            f"#{rank:<4} | {r['sl']}%{'':<3} | {r['tp']}%{'':<3} | {r['rr']:<6} | {r['cycles']:<6} | "
            f"{r['win_rate']:<6.1f}% | {r['chop_pct']:<6.1f}% | ${r['net_pnl']:<11.2f} | {r['ret_pct']:<9.2f}% | "
            f"{r['pf']:<13.2f} | ${r['max_dd']:<9.2f} | {r['ret_dd_ratio']:<8.2f}x"
        )
        print(line)
    print("="*110)

    # Best Risk-Adjusted (Return-to-Drawdown) Setup
    best_risk_adj = sorted(
        [r for r in results if r["net_pnl"] > 0 and r["cycles"] >= 5],
        key=lambda x: x["ret_dd_ratio"],
        reverse=True
    )
    if best_risk_adj:
        top_ra = best_risk_adj[0]
        print(f"\n[BEST RISK-ADJUSTED SETUP]:")
        print(f"Config: SL {top_ra['sl']}% (Trailing) / TP {top_ra['tp']}% (R:R {top_ra['rr']})")
        print(f"Net Profit: ${top_ra['net_pnl']:+.2f} ({top_ra['ret_pct']:+.2f}%) | Win Rate: {top_ra['win_rate']:.1f}% | Double SL Chop: {top_ra['chop_pct']:.1f}%")
        print(f"Profit Factor: {top_ra['pf']:.2f} | Max Drawdown: ${top_ra['max_dd']:.2f} | Return/Drawdown Ratio: {top_ra['ret_dd_ratio']:.2f}x\n")

if __name__ == "__main__":
    run_grid_optimization()
