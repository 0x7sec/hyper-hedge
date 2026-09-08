#!/usr/bin/env python3
"""
Comprehensive Backtesting Engine for Bybit Dual-Leg Gold (XAUUSDT) Hedge Strategy.
Simulates simultaneous Long + Short entry with 3% Trailing SL and 6% TP across historical market data.
"""

import sys
import time
import argparse
import random
import numpy as np
from datetime import datetime
from decimal import Decimal
from typing import List, Dict, Any, Tuple, Optional
from pybit.unified_trading import HTTP
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

TAKER_FEE_RATE = Decimal("0.00055")  # Bybit standard VIP0 taker fee: 0.055%


def calculate_ema(prices: List[Decimal], period: int) -> List[Optional[Decimal]]:
    """Compute Exponential Moving Average (EMA) using pure Decimal arithmetic."""
    if len(prices) < period:
        return [None] * len(prices)
    ema: List[Optional[Decimal]] = [None] * (period - 1)
    initial_sma = sum(prices[:period]) / Decimal(period)
    ema.append(initial_sma)
    multiplier = Decimal("2") / Decimal(period + 1)
    for px in prices[period:]:
        new_val = (px - ema[-1]) * multiplier + ema[-1]
        ema.append(new_val)
    return ema


def calculate_adx(candles: List[Dict[str, Any]], period: int = 14) -> List[Optional[Decimal]]:
    """Compute Average Directional Index (ADX) using Wilder's smoothing."""
    n = len(candles)
    adx_vals: List[Optional[Decimal]] = [None] * n
    if n < 2 * period + 1:
        return adx_vals

    tr_list: List[Decimal] = [Decimal("0")] * n
    plus_dm: List[Decimal] = [Decimal("0")] * n
    minus_dm: List[Decimal] = [Decimal("0")] * n

    for i in range(1, n):
        h = candles[i]["high"]
        l = candles[i]["low"]
        prev_h = candles[i - 1]["high"]
        prev_l = candles[i - 1]["low"]
        prev_c = candles[i - 1]["close"]

        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list[i] = tr

        up_move = h - prev_h
        down_move = prev_l - l

        if up_move > down_move and up_move > 0:
            plus_dm[i] = up_move
        else:
            plus_dm[i] = Decimal("0")

        if down_move > up_move and down_move > 0:
            minus_dm[i] = down_move
        else:
            minus_dm[i] = Decimal("0")

    p_dec = Decimal(str(period))
    smoothed_tr = sum(tr_list[1:period + 1])
    smoothed_plus_dm = sum(plus_dm[1:period + 1])
    smoothed_minus_dm = sum(minus_dm[1:period + 1])

    dx_list: List[Optional[Decimal]] = [None] * n
    p_di = (smoothed_plus_dm / smoothed_tr) * Decimal("100") if smoothed_tr > 0 else Decimal("0")
    m_di = (smoothed_minus_dm / smoothed_tr) * Decimal("100") if smoothed_tr > 0 else Decimal("0")
    di_sum = p_di + m_di
    dx_list[period] = (abs(p_di - m_di) / di_sum) * Decimal("100") if di_sum > 0 else Decimal("0")

    for i in range(period + 1, n):
        smoothed_tr = smoothed_tr - (smoothed_tr / p_dec) + tr_list[i]
        smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / p_dec) + plus_dm[i]
        smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / p_dec) + minus_dm[i]

        p_di = (smoothed_plus_dm / smoothed_tr) * Decimal("100") if smoothed_tr > 0 else Decimal("0")
        m_di = (smoothed_minus_dm / smoothed_tr) * Decimal("100") if smoothed_tr > 0 else Decimal("0")
        di_sum = p_di + m_di
        dx_list[i] = (abs(p_di - m_di) / di_sum) * Decimal("100") if di_sum > 0 else Decimal("0")

    first_dx_window = [dx_list[j] for j in range(period, 2 * period)]
    current_adx = sum(first_dx_window) / p_dec
    adx_vals[2 * period - 1] = current_adx

    for i in range(2 * period, n):
        current_adx = (current_adx * (p_dec - Decimal("1")) + dx_list[i]) / p_dec
        adx_vals[i] = current_adx

    return adx_vals


def compute_indicators(
    candles: List[Dict[str, Any]],
    fast_periods: List[int] = None,
    adx_period: int = 14,
) -> None:
    """Precompute EMAs and ADX and attach them to each candle dictionary."""
    if fast_periods is None:
        fast_periods = [9, 20, 50, 100, 200]

    closes = [c["close"] for c in candles]
    for p in fast_periods:
        ema_vals = calculate_ema(closes, p)
        for idx, c in enumerate(candles):
            c[f"ema_{p}"] = ema_vals[idx]

    adx_vals = calculate_adx(candles, period=adx_period)
    for idx, c in enumerate(candles):
        c["adx"] = adx_vals[idx]


def fetch_historical_candles(symbol: str, interval: str, target_candles: int = 5000) -> List[Dict[str, Any]]:
    """
    Fetch historical candles by paginating Bybit Mainnet API (newest to oldest),
    then reverse to chronological order (oldest to newest).
    """
    session = HTTP(testnet=False)
    all_candles = []
    end_time = None

    console.print(f"[cyan]Fetching {target_candles} historical {interval}-min candles for [bold green]{symbol}[/bold green] from Bybit Mainnet...[/cyan]")

    while len(all_candles) < target_candles:
        limit = min(1000, target_candles - len(all_candles))
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if end_time is not None:
            params["endTime"] = end_time

        res = session.get_kline(**params)
        if res.get("retCode") != 0:
            console.print(f"[red]Error fetching klines: {res.get('retMsg')}[/red]")
            break

        klines = res.get("result", {}).get("list", [])
        if not klines:
            break

        all_candles.extend(klines)
        # Update end_time to just before the oldest candle in this batch
        oldest_ts = int(klines[-1][0])
        end_time = oldest_ts - 1

        print(f"\rFetched {len(all_candles)}/{target_candles} candles...", end="", flush=True)

        if len(klines) < limit:
            break
        time.sleep(0.1)

    print()
    # Reverse to chronological order (oldest -> newest)
    all_candles.reverse()

    parsed = []
    for k in all_candles:
        parsed.append({
            "timestamp": int(k[0]),
            "datetime": datetime.fromtimestamp(int(k[0]) / 1000),
            "open": Decimal(str(k[1])),
            "high": Decimal(str(k[2])),
            "low": Decimal(str(k[3])),
            "close": Decimal(str(k[4])),
            "volume": Decimal(str(k[5])),
        })

    compute_indicators(parsed)
    console.print(f"[green]Successfully loaded {len(parsed)} candles (with precalculated EMAs) from {parsed[0]['datetime']} to {parsed[-1]['datetime']}.[/green]\n")
    return parsed


def run_single_backtest(
    candles: List[Dict[str, Any]],
    sl_pct: Decimal = Decimal("3.0"),
    tp_pct: Decimal = Decimal("6.0"),
    position_size: Decimal = Decimal("1.0"),  # base size
    leverage: int = 1,
    initial_capital: Decimal = Decimal("10000"),
    include_fees: bool = True,
    indicator: str = "none",
    ema_fast: int = 20,
    ema_slow: int = 50,
    ema_trend: int = 200,
    min_spread_pct: Decimal = Decimal("0.10"),
    adx_min: Decimal = Decimal("0"),
    adx_period: int = 14,
    asymmetric_hedge: bool = False,
    counter_hedge_ratio: Decimal = Decimal("0.50"),
    be_lock: bool = False,
    be_buffer_pct: Decimal = Decimal("0.20"),
) -> Dict[str, Any]:
    """
    Run backtest simulation over candles.
    Simulates double-entry cycles, optionally gated by technical indicators (e.g. EMA cross, spread, trend, ADX),
    with support for asymmetric sizing (100% trend leg / 50% counter-trend leg) and Break-Even Lock.
    """
    sl_ratio = sl_pct / Decimal("100")
    tp_ratio = tp_pct / Decimal("100")
    spread_ratio = min_spread_pct / Decimal("100")
    be_buffer_ratio = be_buffer_pct / Decimal("100")
    eff_size = position_size * Decimal(str(leverage))

    # Ensure required EMAs and ADX are computed
    needed = [p for p in [ema_fast, ema_slow, ema_trend] if candles and f"ema_{p}" not in candles[0]]
    if needed or (candles and "adx" not in candles[0]):
        compute_indicators(candles, needed, adx_period=adx_period)

    cycles = []
    start_idx = max(
        ema_trend if indicator == "ema_trend" else 0,
        ema_slow if indicator in ["ema_cross", "ema_spread"] else 0,
        ema_fast if indicator in ["ema_cross", "ema_spread"] else 0,
        2 * adx_period + 2 if adx_min > Decimal("0") else 0,
    ) if (indicator != "none" or adx_min > Decimal("0")) else 0
    i = start_idx
    total_candles = len(candles)

    while i < total_candles - 1:
        direction = "neutral"
        # Check indicator filter condition before entering
        if indicator == "ema_cross":
            prev_fast = candles[i - 1].get(f"ema_{ema_fast}")
            prev_slow = candles[i - 1].get(f"ema_{ema_slow}")
            curr_fast = candles[i].get(f"ema_{ema_fast}")
            curr_slow = candles[i].get(f"ema_{ema_slow}")
            if prev_fast is None or prev_slow is None or curr_fast is None or curr_slow is None:
                i += 1
                continue
            crossed_up = (prev_fast <= prev_slow) and (curr_fast > curr_slow)
            crossed_down = (prev_fast >= prev_slow) and (curr_fast < curr_slow)
            if not (crossed_up or crossed_down):
                i += 1
                continue
            direction = "bullish" if crossed_up else "bearish"

        elif indicator == "ema_trend":
            ema_t = candles[i].get(f"ema_{ema_trend}")
            if ema_t is None:
                i += 1
                continue
            px = candles[i]["close"]
            dist = abs(px - ema_t) / px
            if dist < Decimal("0.003"):  # Inside 0.3% is consolidation chop
                i += 1
                continue
            direction = "bullish" if px > ema_t else "bearish"

        elif indicator == "ema_spread":
            fast = candles[i].get(f"ema_{ema_fast}")
            slow = candles[i].get(f"ema_{ema_slow}")
            px = candles[i]["close"]
            if fast is None or slow is None:
                i += 1
                continue
            spread = abs(fast - slow) / px
            if spread < spread_ratio:
                i += 1
                continue
            direction = "bullish" if fast > slow else "bearish"

        # Check ADX filter condition if enabled (adx_min > 0)
        if adx_min > Decimal("0"):
            curr_adx = candles[i].get("adx")
            if curr_adx is None or curr_adx <= adx_min:
                i += 1
                continue

        # Sizing with optional asymmetric hedge
        if asymmetric_hedge:
            if direction == "bullish":
                long_size = eff_size
                short_size = eff_size * counter_hedge_ratio
            elif direction == "bearish":
                long_size = eff_size * counter_hedge_ratio
                short_size = eff_size
            else:
                long_size = eff_size
                short_size = eff_size
        else:
            long_size = eff_size
            short_size = eff_size

        entry_candle = candles[i]
        entry_price = entry_candle["close"] if indicator != "none" else entry_candle["open"]
        entry_time = entry_candle["datetime"]

        # Long state
        long_active = True
        long_peak = entry_price
        long_sl = entry_price * (Decimal("1") - sl_ratio)
        long_tp = entry_price * (Decimal("1") + tp_ratio)
        long_exit_px = None
        long_exit_reason = None
        long_exit_time = None

        # Short state
        short_active = True
        short_trough = entry_price
        short_sl = entry_price * (Decimal("1") + sl_ratio)
        short_tp = entry_price * (Decimal("1") - tp_ratio)
        short_exit_px = None
        short_exit_reason = None
        short_exit_time = None

        # Step through subsequent candles until both legs close.
        # Bug fix #1: start at i+1 so the signal candle is never used as an exit
        # candle — entry is at close[i], first possible exit is the next bar.
        j = i + 1
        while (long_active or short_active) and j < total_candles:
            c = candles[j]
            high = c["high"]
            low = c["low"]

            # 1. Update Long
            if long_active:
                # Bug fix #2: ratchet the trailing SL FIRST (using this candle's
                # high) before checking exit conditions.  This ensures a candle
                # that spikes to a new peak and then crashes will exit at the
                # correctly-raised stop level, not the pre-spike level.
                if high > long_peak:
                    long_peak = high
                    new_sl = high * (Decimal("1") - sl_ratio)
                    if new_sl > long_sl:
                        long_sl = new_sl
                # Check TP
                if high >= long_tp:
                    long_active = False
                    long_exit_px = long_tp
                    long_exit_reason = "TP"
                    long_exit_time = c["datetime"]
                # Check Trailing SL (uses ratcheted level)
                elif low <= long_sl:
                    long_active = False
                    long_exit_px = long_sl
                    long_exit_reason = "SL"
                    long_exit_time = c["datetime"]

            # 2. Update Short
            if short_active:
                # Ratchet trailing SL downwards first (symmetric to Long fix above)
                if low < short_trough:
                    short_trough = low
                    new_sl = low * (Decimal("1") + sl_ratio)
                    if new_sl < short_sl:
                        short_sl = new_sl
                # Check TP
                if low <= short_tp:
                    short_active = False
                    short_exit_px = short_tp
                    short_exit_reason = "TP"
                    short_exit_time = c["datetime"]
                # Check Trailing SL (uses ratcheted level)
                elif high >= short_sl:
                    short_active = False
                    short_exit_px = short_sl
                    short_exit_reason = "SL"
                    short_exit_time = c["datetime"]

            # Break-even lock: when one leg stops out at SL, lock surviving leg SL to at least entry +/- buffer
            if be_lock:
                if not short_active and long_active:
                    be_level = entry_price * (Decimal("1") + be_buffer_ratio)
                    if long_sl < be_level:
                        long_sl = be_level
                if not long_active and short_active:
                    be_level = entry_price * (Decimal("1") - be_buffer_ratio)
                    if short_sl > be_level:
                        short_sl = be_level

            j += 1

        # If backtest data ended while positions were open, mark them closed at final price
        if long_active:
            long_exit_px = candles[-1]["close"]
            long_exit_reason = "END_OF_DATA"
            long_exit_time = candles[-1]["datetime"]
        if short_active:
            short_exit_px = candles[-1]["close"]
            short_exit_reason = "END_OF_DATA"
            short_exit_time = candles[-1]["datetime"]

        # Calculate PnL for Long
        long_diff = long_exit_px - entry_price
        long_pnl_usd = long_diff * long_size
        long_pnl_pct = (long_diff / entry_price) * Decimal("100")

        # Calculate PnL for Short
        short_diff = entry_price - short_exit_px
        short_pnl_usd = short_diff * short_size
        short_pnl_pct = (short_diff / entry_price) * Decimal("100")

        # Calculate Taker Fees (4 orders: Long Entry, Short Entry, Long Exit, Short Exit)
        turnover = (entry_price + long_exit_px) * long_size + (entry_price + short_exit_px) * short_size
        fee_usd = turnover * TAKER_FEE_RATE if include_fees else Decimal("0")
        fee_pct = (fee_usd / (entry_price * eff_size)) * Decimal("100")

        net_pnl_usd = (long_pnl_usd + short_pnl_usd) - fee_usd
        net_pnl_pct = (long_pnl_pct + short_pnl_pct) - fee_pct

        # Determine cycle outcome category
        if long_exit_reason == "TP" and short_exit_reason == "SL":
            category = "TREND_LONG"
        elif short_exit_reason == "TP" and long_exit_reason == "SL":
            category = "TREND_SHORT"
        elif long_exit_reason == "SL" and short_exit_reason == "SL":
            category = "DOUBLE_STOP_CHOP"
        elif long_exit_reason == "TP" and short_exit_reason == "TP":
            category = "DOUBLE_TP_REVERSAL"
        else:
            category = "MIXED"

        cycles.append({
            "cycle_idx": len(cycles) + 1,
            "entry_time": entry_time,
            "exit_time": max(long_exit_time, short_exit_time),
            "entry_price": entry_price,
            "direction": direction,
            "long_size": long_size,
            "short_size": short_size,
            "long_exit_px": long_exit_px,
            "long_exit_reason": long_exit_reason,
            "long_peak": long_peak,
            "long_pnl_pct": long_pnl_pct,
            "long_pnl_usd": long_pnl_usd,
            "short_exit_px": short_exit_px,
            "short_exit_reason": short_exit_reason,
            "short_trough": short_trough,
            "short_pnl_pct": short_pnl_pct,
            "short_pnl_usd": short_pnl_usd,
            "fee_usd": fee_usd,
            "net_pnl_pct": net_pnl_pct,
            "net_pnl_usd": net_pnl_usd,
            "category": category,
            "duration_candles": j - i,
        })

        # Advance to next cycle
        i = j

    # Compute Aggregate Metrics
    total_cycles = len(cycles)
    if total_cycles == 0:
        return {"error": "No completed cycles in range."}

    winning_cycles = [c for c in cycles if c["net_pnl_usd"] > 0]
    losing_cycles = [c for c in cycles if c["net_pnl_usd"] < 0]
    breakeven_cycles = [c for c in cycles if c["net_pnl_usd"] == 0]

    win_rate = (len(winning_cycles) / total_cycles) * 100
    total_net_pnl_usd = sum(c["net_pnl_usd"] for c in cycles)
    total_net_pnl_pct = sum(c["net_pnl_pct"] for c in cycles)
    total_fees_usd = sum(c["fee_usd"] for c in cycles)

    gross_profit = sum(c["net_pnl_usd"] for c in winning_cycles)
    gross_loss = abs(sum(c["net_pnl_usd"] for c in losing_cycles))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else Decimal("999.0")

    # Max Drawdown calculation
    peak_equity = Decimal("0")
    current_equity = Decimal("0")
    max_drawdown_usd = Decimal("0")

    for c in cycles:
        current_equity += c["net_pnl_usd"]
        if current_equity > peak_equity:
            peak_equity = current_equity
        dd = peak_equity - current_equity
        if dd > max_drawdown_usd:
            max_drawdown_usd = dd

    # Categorization counts
    double_sl_count = sum(1 for c in cycles if c["category"] == "DOUBLE_STOP_CHOP")
    trend_count = sum(1 for c in cycles if c["category"] in ("TREND_LONG", "TREND_SHORT"))

    return {
        "sl_pct": sl_pct,
        "tp_pct": tp_pct,
        "leverage": leverage,
        "initial_capital": initial_capital,
        "base_size": position_size,
        "eff_size": eff_size,
        "indicator": indicator,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "ema_trend": ema_trend,
        "min_spread_pct": min_spread_pct,
        "adx_min": adx_min,
        "adx_period": adx_period,
        "asymmetric_hedge": asymmetric_hedge,
        "counter_hedge_ratio": counter_hedge_ratio,
        "total_cycles": total_cycles,
        "winning_cycles": len(winning_cycles),
        "losing_cycles": len(losing_cycles),
        "win_rate": win_rate,
        "total_net_pnl_usd": total_net_pnl_usd,
        "total_net_pnl_pct": total_net_pnl_pct,
        "total_fees_usd": total_fees_usd,
        "profit_factor": profit_factor,
        "max_drawdown_usd": max_drawdown_usd,
        "double_sl_count": double_sl_count,
        "trend_count": trend_count,
        "cycles": cycles,
    }


def print_backtest_report(results: Dict[str, Any], initial_capital: Optional[Decimal] = None) -> None:
    """Print beautifully formatted analytics table."""
    sl_pct = results["sl_pct"]
    tp_pct = results["tp_pct"]
    lev = results.get("leverage", 1)
    cap = initial_capital if initial_capital is not None else results.get("initial_capital", Decimal("10000"))
    total = results["total_cycles"]
    wins = results["winning_cycles"]
    losses = results["losing_cycles"]
    win_rate = results["win_rate"]
    net_pnl = results["total_net_pnl_usd"]
    fees = results["total_fees_usd"]
    pf = results["profit_factor"]
    max_dd = results["max_drawdown_usd"]
    double_sl = results["double_sl_count"]
    trends = results["trend_count"]

    ind = results.get("indicator", "none")
    if ind == "ema_cross":
        ind_desc = f"EMA Cross ({results.get('ema_fast')}/{results.get('ema_slow')})"
    elif ind == "ema_spread":
        ind_desc = f"EMA Spread Filter ({results.get('ema_fast')}/{results.get('ema_slow')} >= {results.get('min_spread_pct')}%)"
    elif ind == "ema_trend":
        ind_desc = f"EMA({results.get('ema_trend')}) Trend Filter"
    else:
        ind_desc = "None (Continuous Double-Entry)"

    adx_thresh = results.get("adx_min", Decimal("0"))
    if adx_thresh > Decimal("0"):
        if ind == "none":
            ind_desc = f"ADX({results.get('adx_period', 14)}) > {adx_thresh}"
        else:
            ind_desc += f" + ADX({results.get('adx_period', 14)}) > {adx_thresh}"

    header_color = "green" if net_pnl >= 0 else "red"

    panel = Panel.fit(
        f"[bold {header_color}]BACKTEST REPORT: DUAL-LEG HEDGE STRATEGY[/bold {header_color}]\n"
        f"Symbol: [bold yellow]XAUUSDT / PAXG (Gold)[/bold yellow] | Config: [red]SL {sl_pct}% (Trailing)[/red] / [green]TP {tp_pct}%[/green] | Leverage: [bold]{lev}x[/bold]\n"
        f"Capital: [bold green]${cap:,.2f} USDT[/bold green] | Base Size: {results.get('base_size')} XAU | Eff Size: {results.get('eff_size')} XAU\n"
        f"Indicator Filter: [bold magenta]{ind_desc}[/bold magenta]",
        border_style=header_color,
    )
    console.print(panel)

    table = Table(title="Performance Metrics", border_style="cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", style="cyan")

    table.add_row("Initial Account Capital", f"${cap:,.2f} USDT")
    table.add_row("Account Leverage", f"{lev}x")
    table.add_row("Base Size / Eff Size", f"{results.get('base_size')} XAU / {results.get('eff_size')} XAU")
    if results.get("asymmetric_hedge"):
        ratio_pct = int(results.get("counter_hedge_ratio", Decimal("0.50")) * 100)
        table.add_row("Hedge Structure", f"[bold yellow]Asymmetric (100% Trend Leg / {ratio_pct}% Counter Leg)[/bold yellow]")
    table.add_row("Indicator Filter", ind_desc)
    table.add_row("Total Trade Cycles", f"{total}")
    table.add_row("Profitable Cycles", f"[green]{wins}[/green] ({win_rate:.1f}%)")
    table.add_row("Unprofitable Cycles", f"[red]{losses}[/red] ({100 - win_rate:.1f}%)")
    table.add_row("Trending Cycles (1 TP + 1 SL)", f"{trends} ({(trends / total * 100):.1f}%)" if total else "0")
    table.add_row("Choppy Cycles (Double SL Hit)", f"[yellow]{double_sl}[/yellow] ({(double_sl / total * 100):.1f}%)" if total else "0")
    table.add_row("Total Taker Fees Paid", f"${fees:.2f}")

    pnl_style = "green" if net_pnl >= 0 else "red"
    table.add_row("Net Profit / Loss ($)", f"[{pnl_style}]${net_pnl:+.2f}[/{pnl_style}]")
    pnl_on_capital = (net_pnl / cap) * 100
    table.add_row(f"Return on Capital (${cap:,.0f})", f"[{pnl_style}]{pnl_on_capital:+.2f}%[/{pnl_style}]")
    table.add_row("Profit Factor", f"{pf:.2f}")
    table.add_row("Max Drawdown ($)", f"[red]${max_dd:.2f}[/red]")
    max_dd_pct = (max_dd / cap) * 100
    table.add_row("Max Drawdown on Capital (%)", f"[red]{max_dd_pct:.2f}%[/red]")

    console.print(table)


def print_trade_details(results: Dict[str, Any], initial_capital: Decimal) -> None:
    """Print an exhaustive trade-by-trade ledger of every executed hedge cycle."""
    cycles = results.get("cycles", [])
    if not cycles:
        console.print("[yellow]No trade cycles to display.[/yellow]")
        return

    table = Table(
        title=f"Trade-by-Trade Ledger ({len(cycles)} Cycles | Starting Capital: ${initial_capital:,.2f})",
        border_style="magenta",
        show_lines=True,
    )
    table.add_column("#", style="bold", justify="right")
    table.add_column("Entry Time\n& Price", style="cyan")
    table.add_column("Exit Time", style="cyan")
    table.add_column("Type / Category", style="bold")
    table.add_column("Long Leg Exit\n(Px / Reason / PnL)", style="green")
    table.add_column("Short Leg Exit\n(Px / Reason / PnL)", style="red")
    table.add_column("Fees", style="dim")
    table.add_column("Cycle Net PnL", justify="right")
    table.add_column("Account Balance\n& Cumulative %", justify="right", style="bold")

    cum_balance = initial_capital
    for c in cycles:
        net = c["net_pnl_usd"]
        cum_balance += net
        cum_pct = ((cum_balance - initial_capital) / initial_capital) * Decimal("100")
        pnl_color = "green" if net >= 0 else "red"
        cum_color = "green" if cum_balance >= initial_capital else "red"

        cat = c["category"].replace("_", " ").title()
        if "Double Stop" in cat:
            cat_styled = f"[red]{cat}[/red]"
        elif "Trend" in cat:
            cat_styled = f"[green]{cat}[/green]"
        else:
            cat_styled = f"[yellow]{cat}[/yellow]"

        l_color = "green" if c["long_pnl_usd"] >= 0 else "red"
        s_color = "green" if c["short_pnl_usd"] >= 0 else "red"

        l_str = f"${c['long_exit_px']:,.2f} ({c['long_exit_reason']})\n[{l_color}]${c['long_pnl_usd']:+,.2f}[/{l_color}]"
        s_str = f"${c['short_exit_px']:,.2f} ({c['short_exit_reason']})\n[{s_color}]${c['short_pnl_usd']:+,.2f}[/{s_color}]"

        table.add_row(
            str(c["cycle_idx"]),
            f"{c['entry_time']}\n@ ${c['entry_price']:,.2f}",
            str(c["exit_time"]),
            cat_styled,
            l_str,
            s_str,
            f"${c['fee_usd']:.2f}",
            f"[{pnl_color}]${net:+,.2f}[/{pnl_color}]",
            f"[{cum_color}]${cum_balance:,.2f}\n({cum_pct:+.1f}%)[/{cum_color}]",
        )

    console.print(table)


def run_indicator_comparison(
    candles: List[Dict[str, Any]],
    sl_pct: Decimal = Decimal("3.0"),
    tp_pct: Decimal = Decimal("6.0"),
    leverage: int = 1,
) -> None:
    """Compare performance across different indicator filters on the exact same market data."""
    console.print(f"\n[bold yellow]=== INDICATOR COMPARISON TABLE (Baseline vs EMA Filters | Leverage: {leverage}x) ===[/bold yellow]")

    configs = [
        ("None (Continuous)", "none", {}),
        ("EMA(20/50) Crossover", "ema_cross", {"ema_fast": 20, "ema_slow": 50}),
        ("EMA(9/21) Fast Cross", "ema_cross", {"ema_fast": 9, "ema_slow": 21}),
        ("EMA Spread (>= 0.10%)", "ema_spread", {"ema_fast": 20, "ema_slow": 50, "min_spread_pct": Decimal("0.10")}),
        ("EMA Spread (>= 0.20%)", "ema_spread", {"ema_fast": 20, "ema_slow": 50, "min_spread_pct": Decimal("0.20")}),
        ("EMA(200) Trend Filter", "ema_trend", {"ema_trend": 200}),
    ]

    table = Table(title=f"Strategy Performance by Indicator Filter ({leverage}x Leverage, 0.055% Fees)", border_style="cyan")
    table.add_column("Indicator Filter Setup", style="bold")
    table.add_column("Cycles", style="cyan")
    table.add_column("Win Rate")
    table.add_column("Chop (Double SL)", style="yellow")
    table.add_column("Net PnL ($)", style="bold")
    table.add_column("Return %")
    table.add_column("Profit Factor")
    table.add_column("Max DD ($)")

    for label, ind, kwargs in configs:
        res = run_single_backtest(candles, sl_pct=sl_pct, tp_pct=tp_pct, leverage=leverage, indicator=ind, **kwargs)
        if "error" in res or res.get("total_cycles", 0) == 0:
            continue
        pnl = res["total_net_pnl_usd"]
        pnl_style = "green" if pnl >= 0 else "red"
        chop_pct = (res["double_sl_count"] / res["total_cycles"]) * 100 if res["total_cycles"] else 0
        ret_pct = (pnl / Decimal("10000")) * Decimal("100")

        table.add_row(
            label,
            f"{res['total_cycles']}",
            f"{res['win_rate']:.1f}%",
            f"{res['double_sl_count']} ({chop_pct:.1f}%)",
            f"[{pnl_style}]${pnl:+.2f}[/{pnl_style}]",
            f"[{pnl_style}]{ret_pct:+.2f}%[/{pnl_style}]",
            f"{res['profit_factor']:.2f}",
            f"${res['max_drawdown_usd']:.2f}",
        )

    console.print(table)


def run_parameter_sensitivity(
    candles: List[Dict[str, Any]],
    indicator: str = "none",
    **kwargs,
) -> None:
    """Compare multiple SL and TP parameter combinations across the same market data."""
    fast = kwargs.get("ema_fast", 20)
    slow = kwargs.get("ema_slow", 50)
    lev = kwargs.get("leverage", 1)
    ind_label = f"{indicator.upper()} ({fast}/{slow})" if "cross" in indicator else indicator.upper()
    console.print(f"\n[bold yellow]=== PARAMETER SENSITIVITY GRID (SL/TP Optimization | Indicator: {ind_label} | Leverage: {lev}x) ===[/bold yellow]")

    grid = [
        (Decimal("1.5"), Decimal("3.0")),
        (Decimal("2.0"), Decimal("4.0")),
        (Decimal("2.0"), Decimal("6.0")),
        (Decimal("3.0"), Decimal("6.0")),  # User's configuration
        (Decimal("3.0"), Decimal("9.0")),
        (Decimal("4.0"), Decimal("8.0")),
    ]

    table = Table(title=f"Comparison of SL / TP Configurations ({lev}x Leverage, Taker Fees Included)", border_style="magenta")
    table.add_column("SL % (Trailing)", style="red")
    table.add_column("TP %", style="green")
    table.add_column("Cycles", style="bold")
    table.add_column("Win Rate", style="cyan")
    table.add_column("Double SL % (Chop)", style="yellow")
    table.add_column("Net PnL ($)", style="bold")
    table.add_column("Profit Factor")
    table.add_column("Max DD ($)")

    for sl, tp in grid:
        res = run_single_backtest(candles, sl_pct=sl, tp_pct=tp, indicator=indicator, include_fees=True, **kwargs)
        if "error" in res or res.get("total_cycles", 0) == 0:
            continue
        pnl = res["total_net_pnl_usd"]
        pnl_style = "green" if pnl >= 0 else "red"
        chop_pct = (res["double_sl_count"] / res["total_cycles"]) * 100 if res["total_cycles"] else 0

        table.add_row(
            f"{sl}%",
            f"{tp}%",
            f"{res['total_cycles']}",
            f"{res['win_rate']:.1f}%",
            f"{chop_pct:.1f}%",
            f"[{pnl_style}]${pnl:+.2f}[/{pnl_style}]",
            f"{res['profit_factor']:.2f}",
            f"${res['max_drawdown_usd']:.2f}",
        )

    console.print(table)


# =============================================================================
# STATISTICAL VALIDATION SUITE
# Three methods: Walk-Forward, Monte Carlo, Permutation/Rule Significance
# Run via --validate flag in CLI, or call each function directly.
# =============================================================================

def _hedge_sim_float(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    entry_indices: np.ndarray,
    sl_r: float,
    tp_r: float,
    eff_size: float,
    fee_rate: float = 0.00055,
) -> float:
    """
    Lightweight float-arithmetic hedge simulation used by the permutation test.
    Mirrors the bug-fixed logic in run_single_backtest (ratchet-first, j=i+1).
    Uses native Python floats so 1,000 permutation runs complete in ~60 seconds.
    """
    total     = len(closes)
    net_pnl   = 0.0
    used_until = 0

    for ei in entry_indices:
        if int(ei) < used_until or int(ei) >= total - 1:
            continue
        ei = int(ei)

        ep      = closes[ei]
        l_sl    = ep * (1.0 - sl_r);   l_tp = ep * (1.0 + tp_r);   l_peak    = ep
        s_sl    = ep * (1.0 + sl_r);   s_tp = ep * (1.0 - tp_r);   s_trough  = ep
        l_exit  = None;  s_exit = None
        l_on    = True;  s_on   = True

        j = ei + 1
        while (l_on or s_on) and j < total:
            h = highs[j]; lo = lows[j]

            if l_on:
                if h > l_peak:
                    l_peak = h
                    ns = h * (1.0 - sl_r)
                    if ns > l_sl:
                        l_sl = ns
                if h >= l_tp:
                    l_exit = l_tp; l_on = False
                elif lo <= l_sl:
                    l_exit = l_sl; l_on = False

            if s_on:
                if lo < s_trough:
                    s_trough = lo
                    ns = lo * (1.0 + sl_r)
                    if ns < s_sl:
                        s_sl = ns
                if lo <= s_tp:
                    s_exit = s_tp; s_on = False
                elif h >= s_sl:
                    s_exit = s_sl; s_on = False

            j += 1

        if l_exit is None: l_exit = closes[-1]
        if s_exit is None: s_exit = closes[-1]

        long_pnl  = (l_exit - ep) * eff_size
        short_pnl = (ep - s_exit) * eff_size
        turnover  = (ep + l_exit) * eff_size + (ep + s_exit) * eff_size
        fee       = turnover * fee_rate
        net_pnl  += long_pnl + short_pnl - fee
        used_until = j

    return net_pnl


def validate_walk_forward(
    candles: List[Dict[str, Any]],
    results_full: Dict[str, Any],
    split: float = 0.70,
) -> None:
    """
    Walk-Forward Test: trains on the first `split` fraction of candles
    and verifies the same fixed parameters on the remaining out-of-sample data.
    Reuses every parameter already stored in results_full.
    """
    console.print("\n[bold cyan]TEST 1 -- WALK-FORWARD (70% IS / 30% OOS)[/bold cyan]")

    cut  = int(len(candles) * split)
    is_c = candles[:cut]
    oos_c = candles[cut:]

    console.print(f"  In-Sample    : {len(is_c):,} candles  "
                  f"{is_c[0]['datetime'].strftime('%Y-%m-%d')} -> {is_c[-1]['datetime'].strftime('%Y-%m-%d')}")
    console.print(f"  Out-of-Sample: {len(oos_c):,} candles  "
                  f"{oos_c[0]['datetime'].strftime('%Y-%m-%d')} -> {oos_c[-1]['datetime'].strftime('%Y-%m-%d')}")

    def _run(label, subset):
        r = run_single_backtest(
            subset,
            sl_pct=results_full["sl_pct"],
            tp_pct=results_full["tp_pct"],
            position_size=results_full["base_size"],
            leverage=results_full["leverage"],
            initial_capital=results_full["initial_capital"],
            indicator=results_full["indicator"],
            ema_fast=results_full["ema_fast"],
            ema_slow=results_full["ema_slow"],
            ema_trend=results_full["ema_trend"],
            min_spread_pct=results_full["min_spread_pct"],
            adx_min=results_full["adx_min"],
            adx_period=results_full["adx_period"],
        )
        if "error" in r:
            console.print(f"  [red]{label}: No trades found.[/red]")
            return None
        col = "green" if float(r["total_net_pnl_usd"]) >= 0 else "red"
        console.print(f"\n  [bold]{label}[/bold]")
        console.print(f"    Trades        : {r['total_cycles']}")
        console.print(f"    Win Rate      : {float(r['win_rate']):.1f}%")
        console.print(f"    Net Profit    : [{col}]${float(r['total_net_pnl_usd']):+.2f}[/{col}]")
        console.print(f"    Profit Factor : {float(r['profit_factor']):.2f}")
        console.print(f"    Max Drawdown  : ${float(r['max_drawdown_usd']):.2f}")
        return r

    is_r  = _run("IN-SAMPLE (70%)",      is_c)
    oos_r = _run("OUT-OF-SAMPLE (30%)",  oos_c)

    if is_r and oos_r:
        oos_wr  = float(oos_r["win_rate"])
        oos_net = float(oos_r["total_net_pnl_usd"])
        decay   = float(is_r["win_rate"]) - oos_wr
        console.print(f"\n  Win Rate Decay (IS->OOS): {decay:+.1f}%")
        if oos_wr >= 65.0 and oos_net > 0:
            verdict = "[green]PASS -- Strategy holds out-of-sample[/green]"
        elif oos_net > 0:
            verdict = "[yellow]MARGINAL -- Some decay but still profitable[/yellow]"
        else:
            verdict = "[red]FAIL -- Strategy collapses OOS (likely overfit)[/red]"
        console.print(f"  Verdict: {verdict}")


def validate_monte_carlo(
    results: Dict[str, Any],
    n_iter: int = 10_000,
    ruin_fraction: float = 0.90,
) -> None:
    """
    Monte Carlo: resamples the actual trade PnL list with replacement n_iter times,
    builds equity curves, and reports the distribution of final equity and max drawdown.
    """
    console.print(f"\n[bold cyan]TEST 2 -- MONTE CARLO ({n_iter:,} Simulations)[/bold cyan]")

    if "error" in results or not results.get("cycles"):
        console.print("  [red]No trade data available.[/red]")
        return

    trade_pnls = np.array([float(c["net_pnl_usd"]) for c in results["cycles"]])
    n          = len(trade_pnls)
    capital    = float(results["initial_capital"])
    ruin_thr   = -capital * ruin_fraction

    console.print(f"  Source trades  : {n}")
    console.print(f"  Real net profit: ${trade_pnls.sum():+.2f}")
    console.print(f"  Ruin threshold : ${ruin_thr:.0f}  (cumulative equity below this)")

    rng     = np.random.default_rng(42)
    samples = rng.choice(trade_pnls, size=(n_iter, n), replace=True)
    curves  = np.cumsum(samples, axis=1)
    fe      = curves[:, -1]
    peaks   = np.maximum.accumulate(curves, axis=1)
    dd      = (peaks - curves).max(axis=1)
    ruin_pct = float((curves.min(axis=1) <= ruin_thr).mean() * 100)

    console.print(f"\n  Final Equity after {n} trades:")
    console.print(f"    Median            : ${float(np.median(fe)):+.2f}")
    console.print(f"    95th pct (best)   : ${float(np.percentile(fe, 95)):+.2f}")
    console.print(f"    5th  pct (unlucky): ${float(np.percentile(fe, 5)):+.2f}   <- 1-in-20 bad run")
    console.print(f"    1st  pct (worst)  : ${float(np.percentile(fe, 1)):+.2f}   <- 1-in-100 bad run")
    console.print(f"    %% Profitable sims : {float((fe > 0).mean() * 100):.1f}%%")

    console.print(f"\n  Max Drawdown Distribution:")
    console.print(f"    Median Max DD    : ${float(np.median(dd)):.2f}")
    console.print(f"    95th pct Max DD  : ${float(np.percentile(dd, 95)):.2f}   <- 1-in-20 bad run")
    console.print(f"    99th pct Max DD  : ${float(np.percentile(dd, 99)):.2f}   <- 1-in-100 bad run")

    col = "green" if ruin_pct < 1.0 else ("yellow" if ruin_pct < 5.0 else "red")
    console.print(f"\n  Ruin probability : [{col}]{ruin_pct:.2f}%%[/{col}]")
    if ruin_pct < 1.0:
        verdict = "[green]PASS -- Low ruin risk (<1%), sizing manageable[/green]"
    elif ruin_pct < 5.0:
        verdict = "[yellow]CAUTION -- Moderate ruin risk (1-5%), consider reducing size[/yellow]"
    else:
        verdict = "[red]FAIL -- High ruin risk (>5%): reduce position size[/red]"
    console.print(f"  Verdict: {verdict}")


def validate_permutation(
    candles: List[Dict[str, Any]],
    results: Dict[str, Any],
    n_iter: int = 1_000,
) -> None:
    """
    Permutation / Rule Significance Test.
    Replaces the EMA+ADX entry signal with random entry candles and runs the
    full hedge simulation n_iter times.  p-value = fraction of random runs that
    beat the real strategy's net profit.
    """
    console.print(f"\n[bold cyan]TEST 3 -- PERMUTATION TEST ({n_iter:,} Random-Entry Baselines)[/bold cyan]")

    if "error" in results or not results.get("cycles"):
        console.print("  [red]No trade data available.[/red]")
        return

    sl_r     = float(results["sl_pct"]) / 100.0
    tp_r     = float(results["tp_pct"]) / 100.0
    eff_size = float(results["eff_size"])
    real_net = float(results["total_net_pnl_usd"])
    real_tc  = results["total_cycles"]

    closes = np.array([float(c["close"]) for c in candles])
    highs  = np.array([float(c["high"])  for c in candles])
    lows   = np.array([float(c["low"])   for c in candles])

    warmup     = max(2 * 14 + int(results["ema_slow"]) + 5, 60)
    valid_pool = np.arange(warmup, len(candles) - 1)
    n_cand     = min(real_tc * 4, len(valid_pool))

    console.print(f"  Real strategy      : {real_tc} trades | WR {float(results['win_rate']):.1f}% | Net ${real_net:+.2f}")
    console.print(f"  Valid candle pool  : {len(valid_pool):,} candles")
    console.print(f"  Candidates/run     : {n_cand}  (x4 to account for chaining)")
    print(f"  Running {n_iter:,} simulations...", end="", flush=True)

    rng = np.random.default_rng(99)
    rand_profits = []
    for i in range(n_iter):
        candidates = np.sort(rng.choice(valid_pool, size=n_cand, replace=False))
        rand_profits.append(_hedge_sim_float(closes, highs, lows, candidates,
                                             sl_r, tp_r, eff_size))
    console.print(" done.")

    rp         = np.array(rand_profits)
    beat_count = int((rp >= real_net).sum())
    p_value    = beat_count / n_iter

    console.print(f"\n  Random Baseline ({n_iter:,} runs):")
    console.print(f"    Median profit    : ${float(np.median(rp)):+.2f}")
    console.print(f"    95th pct profit  : ${float(np.percentile(rp, 95)):+.2f}")
    console.print(f"    99th pct profit  : ${float(np.percentile(rp, 99)):+.2f}")
    console.print(f"    Real strategy    : ${real_net:+.2f}")
    console.print(f"    Sims beating real: {beat_count} / {n_iter}")
    console.print(f"    p-value          : {p_value:.4f}")

    if p_value < 0.01:
        verdict = "[green]HIGHLY SIGNIFICANT (p<0.01) -- EMA+ADX rule has genuine edge[/green]"
    elif p_value < 0.05:
        verdict = "[green]SIGNIFICANT (p<0.05) -- Rule adds real edge over random entry[/green]"
    elif p_value < 0.10:
        verdict = "[yellow]MARGINAL (p<0.10) -- Weak edge, caution advised[/yellow]"
    else:
        verdict = "[red]NOT SIGNIFICANT (p>=0.10) -- Random entry performs equally well[/red]"
    console.print(f"  Verdict: {verdict}")


def main():
    parser = argparse.ArgumentParser(description="Backtest Bybit Gold (XAUUSDT) Dual-Leg Hedge Strategy")
    parser.add_argument("--symbol", default="XAUUSDT", help="Symbol (default: XAUUSDT)")
    parser.add_argument("--interval", default="15", help="Candle interval in minutes: 1, 5, 15, 60 (default: 15)")
    parser.add_argument("--candles", type=int, default=5000, help="Number of historical candles to fetch (default: 5000)")
    parser.add_argument("--sl-pct", type=str, default="3.0", help="Trailing Stop Loss %% (default: 3.0)")
    parser.add_argument("--tp-pct", type=str, default="6.0", help="Take Profit %% (default: 6.0)")
    parser.add_argument("--capital", type=str, default="10000", help="Account initial capital in USDT (default: 10000)")
    parser.add_argument("--size", type=str, default="", help="Base order size in XAU (default: proportional to capital)")
    parser.add_argument("--leverage", type=int, default=1, help="Leverage multiplier (default: 1)")
    parser.add_argument(
        "--indicator",
        choices=["none", "ema-cross", "ema-spread", "ema-trend"],
        default="none",
        help="Indicator gating: none (continuous), ema-cross, ema-spread, ema-trend (default: none)",
    )
    parser.add_argument("--ema-fast", type=int, default=20, help="Fast EMA period (default: 20)")
    parser.add_argument("--ema-slow", type=int, default=50, help="Slow EMA period (default: 50)")
    parser.add_argument("--ema-trend", type=int, default=200, help="Macro Trend EMA period (default: 200)")
    parser.add_argument("--min-spread-pct", type=str, default="0.10", help="Min EMA spread %% for ema-spread filter (default: 0.10)")
    parser.add_argument("--adx-min", type=str, default="0", help="Min ADX threshold (e.g. 22) (default: 0 = disabled)")
    parser.add_argument("--adx-period", type=int, default=14, help="ADX period (default: 14)")
    parser.add_argument("--compare-indicators", action="store_true", help="Compare all indicator filters side-by-side")
    parser.add_argument("--grid", action="store_true", help="Run multi-parameter sensitivity grid")
    parser.add_argument("--show-trades", action="store_true", help="Print detailed trade-by-trade ledger of all executed cycles")
    parser.add_argument("--asymmetric", action="store_true", help="Enable asymmetric hedge (100% trend leg / 50% counter leg)")
    parser.add_argument("--hedge-ratio", type=str, default="0.50", help="Counter-trend leg size ratio for asymmetric hedge (default: 0.50)")
    parser.add_argument("--be-lock", action="store_true", help="Lock surviving leg to Break-Even + buffer when first leg stops out")
    parser.add_argument("--be-buffer-pct", type=str, default="0.20", help="Break-Even buffer %% above entry (default: 0.20)")
    parser.add_argument("--validate", action="store_true",
                        help="Run full Statistical Validation Suite: Walk-Forward + Monte Carlo + Permutation")
    parser.add_argument("--mc-iter", type=int, default=10000,
                        help="Monte Carlo iteration count (default: 10000)")
    parser.add_argument("--perm-iter", type=int, default=1000,
                        help="Permutation test iteration count (default: 1000)")

    args = parser.parse_args()

    candles = fetch_historical_candles(args.symbol, args.interval, target_candles=args.candles)
    if not candles:
        console.print("[red]No candles retrieved. Aborting.[/red]")
        sys.exit(1)

    sl = Decimal(str(args.sl_pct))
    tp = Decimal(str(args.tp_pct))
    capital = Decimal(str(args.capital))
    base_size = Decimal(str(args.size)) if args.size else (capital / Decimal("10000")) * Decimal("1.0")
    min_spread = Decimal(str(args.min_spread_pct))
    adx_min = Decimal(str(args.adx_min))
    counter_ratio = Decimal(str(args.hedge_ratio))
    be_buf = Decimal(str(args.be_buffer_pct))

    # Canonicalize indicator name
    ind_mode = args.indicator.replace("-", "_")

    # Run primary backtest
    results = run_single_backtest(
        candles,
        sl_pct=sl,
        tp_pct=tp,
        position_size=base_size,
        leverage=args.leverage,
        initial_capital=capital,
        indicator=ind_mode,
        ema_fast=args.ema_fast,
        ema_slow=args.ema_slow,
        ema_trend=args.ema_trend,
        min_spread_pct=min_spread,
        adx_min=adx_min,
        adx_period=args.adx_period,
        asymmetric_hedge=args.asymmetric,
        counter_hedge_ratio=counter_ratio,
        be_lock=args.be_lock,
        be_buffer_pct=be_buf,
    )
    print_backtest_report(results, initial_capital=capital)

    # If requested, show detailed trades
    if args.show_trades:
        print_trade_details(results, initial_capital=capital)

    # If requested, run indicator comparison
    if args.compare_indicators:
        run_indicator_comparison(candles, sl_pct=sl, tp_pct=tp, leverage=args.leverage)

    # If requested, run sensitivity grid
    if args.grid:
        run_parameter_sensitivity(
            candles,
            indicator=ind_mode,
            leverage=args.leverage,
            ema_fast=args.ema_fast,
            ema_slow=args.ema_slow,
            ema_trend=args.ema_trend,
            min_spread_pct=min_spread,
        )

    # Statistical Validation Suite (--validate)
    if args.validate:
        console.print("\n[bold magenta]== STATISTICAL VALIDATION SUITE ==[/bold magenta]")
        validate_walk_forward(candles, results)
        validate_monte_carlo(results, n_iter=args.mc_iter)
        validate_permutation(candles, results, n_iter=args.perm_iter)
        console.print("\n[bold magenta]== VALIDATION COMPLETE ==[/bold magenta]")


if __name__ == "__main__":
    main()
