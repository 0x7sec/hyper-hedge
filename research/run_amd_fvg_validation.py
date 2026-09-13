#!/usr/bin/env python3
"""
Statistical Validation for Macro-Filtered AMD + FVG Engine:
Runs:
1. 5,000-Path Monte Carlo Bootstrap Resampling
2. 1,000-Permutation Rule Significance Test (RST)
Across:
- Full 7-Asset Portfolio (2,364 trades)
- Top Champion Pair: BTCUSDT (370 trades)
- Top High-Beta Pair: DOGEUSDT (272 trades)
- Curated Top 4 Basket: BTC + DOGE + SOL + XMR (1,356 trades)
"""

import os
import sys
import pickle
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from research.monte_carlo import MonteCarloEngine


def run_full_validation():
    pkl_file = os.path.join(BASE_DIR, "scratch/macro_amd_fvg_results.pkl")
    with open(pkl_file, "rb") as f:
        data = pickle.load(f)

    trades = data["trades"]
    breakdown = data["breakdown"]
    mc_engine = MonteCarloEngine(initial_capital=1000.0)

    # Compute PnLs under MEXC MX Token (0.00% Maker / 0.032% Taker)
    def extract_pnls(t_list):
        pnls = []
        for t in t_list:
            e_fee = 0.00000 * 1000.0
            x_fee = (0.00000 if t.reason == "TP" else 0.00032) * (t.xp / t.ep * 1000.0) if t.ep > 0 else 0.0
            pnls.append(t.gross_pnl - (e_fee + x_fee))
        return pnls

    suites = {
        "Full 7-Asset Portfolio": trades,
        "BTCUSDT Alone": breakdown["BTCUSDT"],
        "DOGEUSDT Alone": breakdown["DOGEUSDT"],
        "Curated Top 4 (BTC + DOGE + SOL + XMR)": breakdown["BTCUSDT"] + breakdown["DOGEUSDT"] + breakdown["SOLUSDT"] + breakdown["XMRUSDT"],
    }

    print("=" * 105)
    print("MONTE CARLO (5,000 RUNS) & RULE SIGNIFICANCE TEST (1,000 RUNS): MACRO AMD + FVG")
    print("=" * 105)

    report = {}

    for name, t_list in suites.items():
        pnls = extract_pnls(t_list)
        n_trades = len(pnls)

        print(f"\n" + "#" * 105)
        print(f"SUITE: {name} ({n_trades} Closed Trades)")
        print("#" * 105)

        mc = mc_engine.run_monte_carlo(pnls, iterations=5000, random_seed=42)
        rst = mc_engine.run_rst_permutation(pnls, permutations=1000, random_seed=42)

        report[name] = {"mc": mc, "rst": rst, "n_trades": n_trades, "net": sum(pnls)}

        print(f"\n  [MONTE CARLO BOOTSTRAP (5,000 Paths)]")
        print(f"    * Actual Strategy Profit:   ${sum(pnls):+,.2f}")
        print(f"    * Median Net Profit:        ${mc.median_profit:+,.2f}")
        print(f"    * Mean Net Profit:          ${mc.mean_profit:+,.2f}")
        print(f"    * 90% Confidence Interval:  [${mc.conf_interval_90[0]:+,.2f}  to  ${mc.conf_interval_90[1]:+,.2f}]")
        print(f"    * 95% Confidence Interval:  [${mc.conf_interval_95[0]:+,.2f}  to  ${mc.conf_interval_95[1]:+,.2f}]")
        print(f"    * Probability of Profit:    {mc.prob_profit:.1f}%")
        print(f"    * Risk of Ruin (>20% DD):   {mc.risk_of_ruin:.1f}%")
        print(f"    * Median Max Drawdown:      ${mc.median_max_dd:,.2f}")

        print(f"\n  [RULE SIGNIFICANCE TEST (RST - 1,000 Permutations)]")
        print(f"    * Strategy Net Profit:      ${rst.strategy_net_profit:+,.2f}")
        print(f"    * Null Distribution Mean:   ${rst.null_mean_profit:+,.2f} (StdDev: ${rst.null_std_profit:,.2f})")
        print(f"    * Z-Score vs. Random Luck:  {rst.z_score:+.2f}")
        print(f"    * Empirical p-value:        {rst.p_value:.4f}")
        print(f"    * Statistical Confidence:   {rst.confidence_level_pct:.2f}%")
        status = "PROVEN AUTHENTIC EDGE (p < 0.05)" if rst.is_significant or rst.p_value < 0.05 else "FAILED SIGNIFICANCE"
        print(f"    * Edge Verification:        {status}")

    val_out = os.path.join(BASE_DIR, "scratch/macro_amd_fvg_validation_report.pkl")
    with open(val_out, "wb") as f:
        pickle.dump(report, f)
    print(f"\n[INFO] Saved complete validation report to {val_out}")


if __name__ == "__main__":
    run_full_validation()
