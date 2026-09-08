"""
SOL re-verification after adding ADX>15 filter.
Also sweeps ADX thresholds 10/15/20/25 to confirm 15 is optimal.
"""
import sys
import numpy as np
from decimal import Decimal
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

console.print()
console.print(Rule("[bold cyan]SOLUSDT — ADX Threshold Sweep (EMA9/21, SL6%, TP2.5%, 15m)[/bold cyan]"))
console.print("[dim]Fetching 5,000 candles once, then sweeping ADX thresholds...[/dim]")

candles = fetch_historical_candles("SOLUSDT", "15", 5000)

# ---- SWEEP TABLE ----
sweep_configs = [
    ("No ADX (original)", Decimal("0")),
    ("ADX > 10",          Decimal("10")),
    ("ADX > 15 (new)",    Decimal("15")),
    ("ADX > 20",          Decimal("20")),
    ("ADX > 25",          Decimal("25")),
]

table = Table(title="SOLUSDT ADX Threshold Sweep — Walk-Forward + Full Sample", border_style="magenta")
table.add_column("Config",         style="bold")
table.add_column("Trades",         justify="right")
table.add_column("Win%",           justify="right", style="cyan")
table.add_column("Net PnL",        justify="right")
table.add_column("PF",             justify="right")
table.add_column("Max DD",         justify="right")
table.add_column("OOS Win%",       justify="right")
table.add_column("OOS PnL",        justify="right")
table.add_column("OOS Verdict",    justify="left")

cut = int(len(candles) * 0.70)
is_c  = candles[:cut]
oos_c = candles[cut:]

for label, adx_min in sweep_configs:
    r = run_single_backtest(
        candles,
        sl_pct=Decimal("6.0"),
        tp_pct=Decimal("2.5"),
        position_size=Decimal("5"),
        leverage=4,
        initial_capital=Decimal("9818"),
        include_fees=True,
        indicator="ema_cross",
        ema_fast=9,
        ema_slow=21,
        ema_trend=200,
        adx_min=adx_min,
        adx_period=14,
        be_lock=True,
        be_buffer_pct=Decimal("0.20"),
    )
    oos_r = run_single_backtest(
        oos_c,
        sl_pct=Decimal("6.0"),
        tp_pct=Decimal("2.5"),
        position_size=Decimal("5"),
        leverage=4,
        initial_capital=Decimal("9818"),
        include_fees=True,
        indicator="ema_cross",
        ema_fast=9,
        ema_slow=21,
        ema_trend=200,
        adx_min=adx_min,
        adx_period=14,
        be_lock=True,
        be_buffer_pct=Decimal("0.20"),
    )

    if "error" in r:
        table.add_row(label, "0", "–", "–", "–", "–", "–", "–", "[red]No trades[/red]")
        continue

    net     = float(r["total_net_pnl_usd"])
    pf      = float(r["profit_factor"])
    dd      = float(r["max_drawdown_usd"])
    wr      = float(r["win_rate"])
    tc      = r["total_cycles"]
    net_col = "green" if net >= 0 else "red"

    if "error" in oos_r or oos_r.get("total_cycles", 0) == 0:
        oos_wr_s, oos_net_s, oos_v = "–", "–", "[yellow]No OOS trades[/yellow]"
    else:
        oos_wr  = float(oos_r["win_rate"])
        oos_net = float(oos_r["total_net_pnl_usd"])
        oos_col = "green" if oos_net >= 0 else "red"
        oos_wr_s  = f"{oos_wr:.1f}%"
        oos_net_s = f"[{oos_col}]${oos_net:+.2f}[/{oos_col}]"
        if oos_wr >= 65 and oos_net > 0:
            oos_v = "[green]PASS[/green]"
        elif oos_net > 0:
            oos_v = "[yellow]MARGINAL[/yellow]"
        else:
            oos_v = "[red]FAIL[/red]"

    marker = " << APPLIED" if "new" in label else ""
    table.add_row(
        f"[bold]{label}{marker}[/bold]" if marker else label,
        str(tc),
        f"{wr:.1f}%",
        f"[{net_col}]${net:+.2f}[/{net_col}]",
        f"{pf:.2f}",
        f"${dd:.2f}",
        oos_wr_s,
        oos_net_s,
        oos_v,
    )

console.print(table)

# ---- FULL VALIDATION for ADX > 15 ----
console.print()
console.print(Rule("[bold green]SOLUSDT Full Validation: ADX > 15 (New Config)[/bold green]"))

sol_results = run_single_backtest(
    candles,
    sl_pct=Decimal("6.0"),
    tp_pct=Decimal("2.5"),
    position_size=Decimal("5"),
    leverage=4,
    initial_capital=Decimal("9818"),
    include_fees=True,
    indicator="ema_cross",
    ema_fast=9,
    ema_slow=21,
    ema_trend=200,
    adx_min=Decimal("15"),
    adx_period=14,
    be_lock=True,
    be_buffer_pct=Decimal("0.20"),
)

print_backtest_report(sol_results, Decimal("9818"))

console.print("\n[bold yellow]=== STATISTICAL VALIDATION: SOLUSDT ADX>15 ===[/bold yellow]")
validate_walk_forward(candles, sol_results)
validate_monte_carlo(sol_results, n_iter=10_000)
validate_permutation(candles, sol_results, n_iter=1000)

# ---- Final comparison vs old config ----
console.print()
console.print(Rule("[bold white]Before vs After: SOLUSDT ADX 0 -> 15[/bold white]"))
before_after = Table(border_style="cyan")
before_after.add_column("Metric")
before_after.add_column("Old (ADX=0)", justify="right")
before_after.add_column("New (ADX>15)", justify="right", style="bold green")

r_old = run_single_backtest(
    candles,
    sl_pct=Decimal("6.0"), tp_pct=Decimal("2.5"),
    position_size=Decimal("5"), leverage=4,
    initial_capital=Decimal("9818"), include_fees=True,
    indicator="ema_cross", ema_fast=9, ema_slow=21, ema_trend=200,
    adx_min=Decimal("0"), adx_period=14, be_lock=True, be_buffer_pct=Decimal("0.20"),
)
r_new = sol_results

for metric, old_val, new_val in [
    ("Total Cycles",    str(r_old["total_cycles"]),                   str(r_new["total_cycles"])),
    ("Win Rate",        f"{float(r_old['win_rate']):.1f}%",           f"{float(r_new['win_rate']):.1f}%"),
    ("Net PnL",         f"${float(r_old['total_net_pnl_usd']):+.2f}", f"${float(r_new['total_net_pnl_usd']):+.2f}"),
    ("Profit Factor",   f"{float(r_old['profit_factor']):.2f}",       f"{float(r_new['profit_factor']):.2f}"),
    ("Max Drawdown",    f"${float(r_old['max_drawdown_usd']):.2f}",   f"${float(r_new['max_drawdown_usd']):.2f}"),
    ("Chop Cycles",     str(r_old["double_sl_count"]),                str(r_new["double_sl_count"])),
]:
    before_after.add_row(metric, old_val, new_val)

console.print(before_after)
console.print()
console.print("[green]config.py updated: SOLUSDT adx_min 0 → 15[/green]")
console.print("[green]Bot will apply new filter on next restart.[/green]")
