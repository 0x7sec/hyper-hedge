#!/usr/bin/env python3
"""
Comparative Strategy Benchmark: Original Bar-Level Engine vs. 1-Minute Sub-Candle Replay Engine.
Runs identical historical data through both engines across champion pairs (BTCUSDT, ETHUSDT, SOLUSDT),
applies 5,000-iteration Monte Carlo & 1,000-run RST validation, saves each test with a unique Permlink ID,
and generates a rigorous quantitative comparison report.
"""

import os
import sys
from decimal import Decimal
import time
from typing import Dict, Any, List

# Ensure project root is on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backtest import run_single_backtest, compute_indicators
from research.replay_engine import ReplayEngine, CHAMPION_PROFILES
from research.monte_carlo import MonteCarloEngine
from research.test_store import save_test_run, list_test_runs


def convert_candles_for_backtest_py(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Converts replay engine candle dicts to Decimal types expected by backtest.py."""
    out = []
    for c in candles:
        out.append({
            "timestamp": int(c["timestamp"]),
            "datetime": c.get("datetime"),
            "open": Decimal(str(c["open"])),
            "high": Decimal(str(c["high"])),
            "low": Decimal(str(c["low"])),
            "close": Decimal(str(c["close"])),
            "volume": Decimal(str(c.get("volume", 1.0))),
        })
    return out


def run_comparative_benchmark(symbols: List[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"], bars: int = 2000):
    print("=" * 90)
    print("HYPER HEDGE RESEARCH: QUANTITATIVE STRATEGY ENGINE COMPARISON BENCHMARK")
    print("Original Coarse Bar Engine (backtest.py) vs. 1-Min Sub-Candle Replay (replay_engine.py)")
    print("=" * 90)

    benchmark_results = {}

    for symbol in symbols:
        print(f"\n[BENCHMARK] Evaluating {symbol} across {bars} 60m macro bars...")

        # 1. Load identical historical candles
        candles = ReplayEngine.load_candles(symbol, limit=bars)
        candles_backtest_py = convert_candles_for_backtest_py(candles)
        prof = CHAMPION_PROFILES.get(symbol, CHAMPION_PROFILES["BTCUSDT"])

        # ---------------------------------------------------------------------
        # ENGINE A: Original Coarse Bar Engine (backtest.py)
        # ---------------------------------------------------------------------
        t0 = time.time()
        res_orig = run_single_backtest(
            candles=candles_backtest_py,
            sl_pct=Decimal(str(round(prof["d_pct"] * prof["confirm_mult"], 2))),
            tp_pct=Decimal(str(round(prof["d_pct"] * prof["b1_tp_mult"], 2))),
            position_size=Decimal("1.0"),
            leverage=int(prof.get("leverage", 10)),
            initial_capital=Decimal("1000.0"),
            include_fees=True,
            indicator="ema_cross",
            ema_fast=9,
            ema_slow=21,
            adx_min=Decimal(str(prof.get("adx_min", 0.0))),
            asymmetric_hedge=True,
            counter_hedge_ratio=Decimal(str(prof.get("hedge_ratio", 0.30))),
            be_lock=True,
            be_buffer_pct=Decimal("0.10"),
        )
        t_orig = (time.time() - t0) * 1000.0

        orig_trades = res_orig.get("cycles", [])
        orig_wins = res_orig.get("winning_cycles", 0)
        orig_total = res_orig.get("total_cycles", len(orig_trades))
        orig_wr = (orig_wins / orig_total * 100.0) if orig_total > 0 else 0.0
        orig_pnl = float(res_orig.get("total_net_pnl", Decimal("0.0")))
        orig_pf = float(res_orig.get("profit_factor", Decimal("0.0")))
        orig_max_dd = float(res_orig.get("max_drawdown", Decimal("0.0")))
        orig_max_dd_pct = (orig_max_dd / 1000.0 * 100.0)

        # ---------------------------------------------------------------------
        # ENGINE B: 1-Minute Sub-Candle Replay Engine (research/replay_engine.py)
        # ---------------------------------------------------------------------
        t0 = time.time()
        replay_engine = ReplayEngine(initial_capital=1000.0)
        res_replay = replay_engine.run_backtest(symbol, candles)
        t_replay = (time.time() - t0) * 1000.0

        # Save Replay Run to Test Store with Unique Permlink
        replay_summary = {
            "symbol": symbol,
            "engine": "1-Min Sub-Candle Replay",
            "total_trades": res_replay.total_trades,
            "win_rate": res_replay.win_rate,
            "shield_rate": res_replay.shield_rate,
            "net_profit": res_replay.net_profit,
            "profit_factor": res_replay.profit_factor,
            "max_drawdown_pct": res_replay.max_drawdown_pct,
            "expectancy_usd": res_replay.expectancy_usd,
            "cagr_pct": res_replay.cagr_pct,
            "sortino_ratio": res_replay.sortino_ratio,
            "calmar_ratio": res_replay.calmar_ratio,
        }
        saved_replay = save_test_run(
            test_type="backtest",
            symbol=symbol,
            title=f"{symbol} 1-Min Replay Strategy Benchmark",
            summary=replay_summary,
            data={
                "symbol": res_replay.symbol,
                "total_trades": res_replay.total_trades,
                "win_rate": res_replay.win_rate,
                "shield_rate": res_replay.shield_rate,
                "net_profit": res_replay.net_profit,
                "profit_factor": res_replay.profit_factor,
                "max_drawdown": res_replay.max_drawdown,
                "max_drawdown_pct": res_replay.max_drawdown_pct,
                "expectancy_usd": res_replay.expectancy_usd,
                "cagr_pct": res_replay.cagr_pct,
                "sortino_ratio": res_replay.sortino_ratio,
                "calmar_ratio": res_replay.calmar_ratio,
                "trades": [
                    {
                        "cycle_id": t.cycle_id,
                        "direction": t.direction,
                        "scenario": t.scenario,
                        "entry_time": t.entry_time,
                        "exit_time": t.exit_time,
                        "entry_price": t.entry_price,
                        "exit_price": t.exit_price,
                        "net_pnl": t.net_pnl,
                        "return_pct": t.return_pct,
                        "bars_held": t.bars_held,
                        "exhaustion_guard_triggered": t.exhaustion_guard_triggered,
                    }
                    for t in res_replay.trades
                ],
                "equity_curve": res_replay.equity_curve,
                "drawdown_curve": res_replay.drawdown_curve,
                "scenario_counts": res_replay.scenario_counts,
            },
            params={"symbol": symbol, "bars": bars, "engine": "ReplayEngine_1m"},
        )

        # ---------------------------------------------------------------------
        # ENGINE C: Monte Carlo 5,000 Paths & RST 1,000 Permutations
        # ---------------------------------------------------------------------
        trade_pnls = [t.net_pnl for t in res_replay.trades]
        mc_engine = MonteCarloEngine(initial_capital=1000.0)
        mc_res = mc_engine.run_monte_carlo(trade_pnls, iterations=5000)
        rst_res = mc_engine.run_rst_permutation(trade_pnls, permutations=1000)

        saved_mc = save_test_run(
            test_type="monte_carlo",
            symbol=symbol,
            title=f"{symbol} Monte Carlo 5,000 & RST Permutations",
            summary={
                "symbol": symbol,
                "median_profit": mc_res.median_profit,
                "prob_profit": mc_res.prob_profit,
                "risk_of_ruin": mc_res.risk_of_ruin,
                "p_value": rst_res.p_value,
                "z_score": rst_res.z_score,
                "is_significant": rst_res.is_significant,
            },
            data={
                "monte_carlo": {
                    "iterations": mc_res.iterations,
                    "median_profit": mc_res.median_profit,
                    "prob_profit": mc_res.prob_profit,
                    "risk_of_ruin": mc_res.risk_of_ruin,
                    "median_max_dd": mc_res.median_max_dd,
                    "percentile_5": mc_res.percentile_5,
                    "percentile_95": mc_res.percentile_95,
                    "fan_chart": mc_res.fan_chart,
                },
                "rst": {
                    "permutations": rst_res.permutations,
                    "strategy_net_profit": rst_res.strategy_net_profit,
                    "z_score": rst_res.z_score,
                    "p_value": rst_res.p_value,
                    "is_significant": rst_res.is_significant,
                }
            },
            params={"symbol": symbol, "iterations": 5000, "permutations": 1000},
        )

        # Count Flash-Crash Exhaustion Guard activations
        exhaustion_triggers = sum(1 for t in res_replay.trades if t.exhaustion_guard_triggered)

        benchmark_results[symbol] = {
            "symbol": symbol,
            "bars": len(candles),
            # Original Engine Metrics
            "orig_total_cycles": orig_total,
            "orig_win_rate": orig_wr,
            "orig_net_profit": orig_pnl,
            "orig_profit_factor": orig_pf,
            "orig_max_dd_pct": orig_max_dd_pct,
            "orig_exec_time_ms": t_orig,
            # Replay Engine Metrics
            "replay_total_cycles": res_replay.total_trades,
            "replay_win_rate": res_replay.win_rate,
            "replay_shield_rate": res_replay.shield_rate,
            "replay_net_profit": res_replay.net_profit,
            "replay_profit_factor": res_replay.profit_factor,
            "replay_max_dd_pct": res_replay.max_drawdown_pct,
            "replay_expectancy": res_replay.expectancy_usd,
            "replay_cagr": res_replay.cagr_pct,
            "replay_sortino": res_replay.sortino_ratio,
            "replay_calmar": res_replay.calmar_ratio,
            "replay_exhaustion_triggers": exhaustion_triggers,
            "replay_exec_time_ms": t_replay,
            "replay_test_id": saved_replay["test_id"],
            "replay_permlink": saved_replay["test_url"],
            # Monte Carlo & RST Metrics
            "mc_median_profit": mc_res.median_profit,
            "mc_prob_profit": mc_res.prob_profit,
            "mc_risk_of_ruin": mc_res.risk_of_ruin,
            "rst_p_value": rst_res.p_value,
            "rst_z_score": rst_res.z_score,
            "rst_is_significant": rst_res.is_significant,
            "mc_test_id": saved_mc["test_id"],
            "mc_permlink": saved_mc["test_url"],
        }

    # -------------------------------------------------------------------------
    # PRINT SYSTEM COMPARISON REPORT
    # -------------------------------------------------------------------------
    print("\n" + "=" * 95)
    print("SIDE-BY-SIDE QUANTITATIVE ENGINE COMPARISON")
    print("=" * 95)
    print(f"{'Metric':<30} | {'Original (backtest.py)':<28} | {'1-Min Replay (research/replay)':<30}")
    print("-" * 95)

    for sym, d in benchmark_results.items():
        print(f"\n>>> ASSET: {sym} ({d['bars']} 60m bars)")
        print(f"{'  Total Cycles / Trades':<30} | {d['orig_total_cycles']:<28} | {d['replay_total_cycles']:<30}")
        print(f"{'  Win Rate %':<30} | {d['orig_win_rate']:<27.1f}% | {d['replay_win_rate']:<29.1f}%")
        print(f"{'  Shield / Breakeven Rate %':<30} | {'N/A (Coarse Bar)':<28} | {d['replay_shield_rate']:<29.1f}%")
        print(f"{'  Net Profit ($)':<30} | ${d['orig_net_profit']:<27.2f} | ${d['replay_net_profit']:<29.2f}")
        print(f"{'  Profit Factor':<30} | {d['orig_profit_factor']:<28.2f} | {d['replay_profit_factor']:<30.2f}")
        print(f"{'  Max Drawdown %':<30} | {d['orig_max_dd_pct']:<27.2f}% | {d['replay_max_dd_pct']:<29.2f}%")
        print(f"{'  Trade Expectancy ($)':<30} | {'N/A':<28} | ${d['replay_expectancy']:<29.2f}")
        print(f"{'  CAGR % / Sortino':<30} | {'N/A':<28} | {d['replay_cagr']:.1f}% / {d['replay_sortino']:.2f}")
        print(f"{'  Exhaustion Guard Triggers':<30} | {'N/A (Blind Spot)':<28} | {d['replay_exhaustion_triggers']:<30} activations")
        print(f"{'  Monte Carlo Median (5k)':<30} | {'N/A':<28} | ${d['mc_median_profit']:<29.2f} ({d['mc_prob_profit']:.1f}% win)")
        print(f"{'  RST p-value / Significance':<30} | {'N/A':<28} | p = {d['rst_p_value']:.4f} ({'Sig' if d['rst_is_significant'] else 'Null'})")
        print(f"{'  Execution Time':<30} | {d['orig_exec_time_ms']:<26.1f}ms | {d['replay_exec_time_ms']:<28.1f}ms")
        print(f"{'  Active Backtest Permlink':<30} | {'N/A':<28} | {d['replay_permlink']}")
        print(f"{'  Active Monte Carlo Permlink':<30} | {'N/A':<28} | {d['mc_permlink']}")

    print("\n" + "=" * 95)
    print("SAVED RESEARCH TEST RUNS IN REPOSITORY:")
    all_runs = list_test_runs(limit=10)
    for r in all_runs:
        print(f" - [{r['test_type'].upper()}] {r['test_id']} ({r['symbol']}) -> {r['test_url']}")
    print("=" * 95)

    return benchmark_results


if __name__ == "__main__":
    run_comparative_benchmark(["BTCUSDT", "ETHUSDT", "SOLUSDT"], bars=2000)
