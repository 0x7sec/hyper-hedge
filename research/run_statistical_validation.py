#!/usr/bin/env python3
"""
Statistical Validation & Robustness Engine (MC & RST)
Applies 5,000-path Monte Carlo bootstrap resampling and 1,000-permutation
Rule Significance Testing (RST) on the multi-strategy 1-minute replay results.
"""

import os
import sys
import pickle
import numpy as np
from typing import Dict, Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from research.monte_carlo import MonteCarloEngine


def run_full_statistical_analysis(results_pkl_path: str = "scratch/multi_strategy_research_results.pkl"):
    pkl_full = os.path.join(BASE_DIR, results_pkl_path)
    if not os.path.exists(pkl_full):
        print(f"[ERROR] Results file not found: {pkl_full}")
        sys.exit(1)

    with open(pkl_full, "rb") as f:
        data = pickle.load(f)

    mc_engine = MonteCarloEngine(initial_capital=1000.0)

    print("\n" + "=" * 95)
    print("MONTE CARLO (5,000 PATHS) & RULE SIGNIFICANCE PERMUTATION TESTS (1,000 RUNS)")
    print("=" * 95)

    stat_report = {}

    for arch_name, arch_data in data.items():
        trades = arch_data["trades"]
        pnls = [t.net_pnl for t in trades]
        n_trades = len(pnls)

        print(f"\n===============================================================================================")
        print(f"STRATEGY: {arch_name}")
        print(f"===============================================================================================")
        print(f"  Total Closed Trades: {n_trades}")

        if n_trades < 5:
            print("  [SKIP] Insufficient trades for statistical significance.")
            continue

        # 1. Monte Carlo
        mc = mc_engine.run_monte_carlo(pnls, iterations=5000, random_seed=42)
        # 2. RST Permutation
        rst = mc_engine.run_rst_permutation(pnls, permutations=1000, random_seed=42)

        stat_report[arch_name] = {
            "mc": mc,
            "rst": rst,
            "raw_metrics": arch_data["metrics"],
            "assets": arch_data["assets"]
        }

        print(f"\n  [MONTE CARLO BOOTSTRAP (5,000 Resampled Paths)]")
        print(f"    * Median Net Profit:    ${mc.median_profit:+,.2f}")
        print(f"    * Mean Net Profit:      ${mc.mean_profit:+,.2f}")
        print(f"    * 90% Confidence Band:  [${mc.conf_interval_90[0]:+,.2f}  to  ${mc.conf_interval_90[1]:+,.2f}]")
        print(f"    * 95% Confidence Band:  [${mc.conf_interval_95[0]:+,.2f}  to  ${mc.conf_interval_95[1]:+,.2f}]")
        print(f"    * Probability of Profit: {mc.prob_profit:.1f}%")
        print(f"    * Risk of Ruin (>25% DD):{mc.risk_of_ruin:.2f}%")
        print(f"    * Median Max Drawdown:  ${mc.median_max_dd:,.2f}")
        print(f"    * 95th Pct Max Drawdown:${mc.percentile_95_max_dd:,.2f}")

        print(f"\n  [RULE SIGNIFICANCE PERMUTATION TEST (RST - 1,000 Runs)]")
        print(f"    * Actual Strategy Profit: ${rst.strategy_net_profit:+,.2f}")
        print(f"    * Null Distribution Mean: ${rst.null_mean_profit:+,.2f} (StdDev: ${rst.null_std_profit:,.2f})")
        print(f"    * Z-Score vs. Random:     {rst.z_score:+.2f}")
        print(f"    * Empirical p-value:      {rst.p_value:.4f}")
        print(f"    * Statistical Confidence: {rst.confidence_level_pct:.2f}%")
        status_str = "PROVEN REAL QUANTITATIVE EDGE (p < 0.01)" if rst.is_significant else "FAILED SIGNIFICANCE (Likely Noise/Curve-Fit)"
        print(f"    * Conclusion:             {status_str}")

    # Save statistical report
    stat_pkl = os.path.join(BASE_DIR, "scratch/statistical_validation_report.pkl")
    with open(stat_pkl, "wb") as f:
        pickle.dump(stat_report, f)
    print(f"\n[INFO] Saved complete statistical validation report to {stat_pkl}")

    return stat_report


if __name__ == "__main__":
    run_full_statistical_analysis()
