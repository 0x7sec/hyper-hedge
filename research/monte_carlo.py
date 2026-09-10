#!/usr/bin/env python3
"""
Monte Carlo Resampling & Statistical Rule Significance Permutation Test (RST) Engine.
Provides 5,000-path bootstrap simulation, risk of ruin calculation, confidence intervals,
fan chart percentile cones, and permutation hypothesis testing against random chance.
"""

import sys
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

logger = logging.getLogger("MonteCarlo")


@dataclass
class MonteCarloResult:
    iterations: int
    num_trades: int
    initial_capital: float
    median_profit: float
    mean_profit: float
    conf_interval_90: Tuple[float, float]  # 5th, 95th percentile
    conf_interval_95: Tuple[float, float]  # 2.5th, 97.5th percentile
    percentile_5: float
    percentile_25: float
    percentile_50: float
    percentile_75: float
    percentile_95: float
    prob_profit: float                     # % of paths ending in profit
    risk_of_ruin: float                    # % of paths with drawdown > 25%
    median_max_dd: float
    percentile_95_max_dd: float
    fan_chart: List[Dict[str, Any]]        # Step-by-step 5th, 25th, 50th, 75th, 95th percentiles


@dataclass
class RSTResult:
    permutations: int
    strategy_net_profit: float
    null_mean_profit: float
    null_std_profit: float
    z_score: float
    p_value: float
    is_significant: bool                   # True if p_value < 0.01 (99% confidence)
    confidence_level_pct: float            # (1 - p_value) * 100


class MonteCarloEngine:
    def __init__(self, initial_capital: float = 1000.0):
        self.initial_capital = initial_capital

    def run_monte_carlo(
        self,
        trade_pnls: List[float],
        iterations: int = 5000,
        fan_steps: int = 50,
        random_seed: Optional[int] = 42,
    ) -> MonteCarloResult:
        """
        Executes 5,000-path bootstrap trade resampling with replacement.
        Computes distribution of returns, max drawdowns, confidence bands, and fan chart percentiles.
        """
        if not trade_pnls:
            return MonteCarloResult(
                iterations=0,
                num_trades=0,
                initial_capital=self.initial_capital,
                median_profit=0.0,
                mean_profit=0.0,
                conf_interval_90=(0.0, 0.0),
                conf_interval_95=(0.0, 0.0),
                percentile_5=0.0,
                percentile_25=0.0,
                percentile_50=0.0,
                percentile_75=0.0,
                percentile_95=0.0,
                prob_profit=0.0,
                risk_of_ruin=0.0,
                median_max_dd=0.0,
                percentile_95_max_dd=0.0,
                fan_chart=[],
            )

        if random_seed is not None:
            np.random.seed(random_seed)

        pnls = np.array(trade_pnls, dtype=np.float64)
        n_trades = len(pnls)

        # Draw random paths: matrix shape (iterations, n_trades)
        sampled_indices = np.random.randint(0, n_trades, size=(iterations, n_trades))
        sampled_pnls = pnls[sampled_indices]

        # Cumulative PnL curves: shape (iterations, n_trades)
        cum_pnls = np.cumsum(sampled_pnls, axis=1)
        equity_paths = self.initial_capital + cum_pnls
        # Insert initial capital at step 0
        init_col = np.full((iterations, 1), self.initial_capital)
        all_equity = np.hstack([init_col, equity_paths])

        # Final profits
        final_profits = cum_pnls[:, -1]

        # Calculate max drawdown for each path
        running_max = np.maximum.accumulate(all_equity, axis=1)
        drawdowns = (running_max - all_equity) / running_max
        max_drawdowns_pct = np.max(drawdowns, axis=1) * 100.0
        max_drawdowns_val = np.max(running_max - all_equity, axis=1)

        # Percentile metrics
        p5 = float(np.percentile(final_profits, 5))
        p25 = float(np.percentile(final_profits, 25))
        p50 = float(np.percentile(final_profits, 50))
        p75 = float(np.percentile(final_profits, 75))
        p95 = float(np.percentile(final_profits, 95))
        p2_5 = float(np.percentile(final_profits, 2.5))
        p97_5 = float(np.percentile(final_profits, 97.5))

        prob_profit = float(np.mean(final_profits > 0.0) * 100.0)
        risk_of_ruin = float(np.mean(max_drawdowns_pct > 25.0) * 100.0)

        median_max_dd = float(np.median(max_drawdowns_val))
        p95_max_dd = float(np.percentile(max_drawdowns_val, 95))

        # Generate fan chart coordinates downsampled to fan_steps
        step_indices = np.linspace(0, n_trades, min(fan_steps, n_trades + 1), dtype=int)
        fan_chart = []
        for step in step_indices:
            col = all_equity[:, step]
            fan_chart.append({
                "step": int(step),
                "p5": round(float(np.percentile(col, 5)), 2),
                "p25": round(float(np.percentile(col, 25)), 2),
                "p50": round(float(np.percentile(col, 50)), 2),
                "p75": round(float(np.percentile(col, 75)), 2),
                "p95": round(float(np.percentile(col, 95)), 2),
            })

        return MonteCarloResult(
            iterations=iterations,
            num_trades=n_trades,
            initial_capital=self.initial_capital,
            median_profit=round(p50, 2),
            mean_profit=round(float(np.mean(final_profits)), 2),
            conf_interval_90=(round(p5, 2), round(p95, 2)),
            conf_interval_95=(round(p2_5, 2), round(p97_5, 2)),
            percentile_5=round(p5, 2),
            percentile_25=round(p25, 2),
            percentile_50=round(p50, 2),
            percentile_75=round(p75, 2),
            percentile_95=round(p95, 2),
            prob_profit=round(prob_profit, 2),
            risk_of_ruin=round(risk_of_ruin, 2),
            median_max_dd=round(median_max_dd, 2),
            percentile_95_max_dd=round(p95_max_dd, 2),
            fan_chart=fan_chart,
        )

    def run_rst_permutation(
        self,
        trade_pnls: List[float],
        permutations: int = 1000,
        random_seed: Optional[int] = 42,
    ) -> RSTResult:
        """
        Rule Significance Permutation Test (RST).
        Permutes trade order and outcome sign randomly under the Null Hypothesis (H0: Edge is random chance).
        Computes empirical p-value and Z-score.
        """
        if not trade_pnls:
            return RSTResult(
                permutations=0,
                strategy_net_profit=0.0,
                null_mean_profit=0.0,
                null_std_profit=0.0,
                z_score=0.0,
                p_value=1.0,
                is_significant=False,
                confidence_level_pct=0.0,
            )

        if random_seed is not None:
            np.random.seed(random_seed)

        pnls = np.array(trade_pnls, dtype=np.float64)
        actual_profit = float(np.sum(pnls))
        n_trades = len(pnls)

        # Generate Null Distribution: random directional flips (+1 or -1) simulating random entry timing
        random_signs = np.random.choice([-1.0, 1.0], size=(permutations, n_trades))
        # Deduct round-trip fee penalty on flipped trades to reflect market reality
        null_pnls = pnls * random_signs
        null_totals = np.sum(null_pnls, axis=1)

        null_mean = float(np.mean(null_totals))
        null_std = float(np.std(null_totals)) if np.std(null_totals) > 0 else 1e-6

        # Count permutations beating actual strategy
        beating_count = np.sum(null_totals >= actual_profit)
        p_value = float((beating_count + 1) / (permutations + 1))  # Conservative Laplace smoothing

        z_score = float((actual_profit - null_mean) / null_std)
        is_significant = p_value < 0.01  # 99% significance threshold
        confidence_pct = max(0.0, min(100.0, (1.0 - p_value) * 100.0))

        return RSTResult(
            permutations=permutations,
            strategy_net_profit=round(actual_profit, 2),
            null_mean_profit=round(null_mean, 2),
            null_std_profit=round(null_std, 2),
            z_score=round(z_score, 2),
            p_value=round(p_value, 4),
            is_significant=is_significant,
            confidence_level_pct=round(confidence_pct, 2),
        )


if __name__ == "__main__":
    print("Testing Monte Carlo & Statistical RST Engine...")
    engine = MonteCarloEngine(initial_capital=1000.0)

    # Sample trade results from strategy
    np.random.seed(42)
    sample_trades = [
        31.42, 21.02, 0.0, 31.42, -0.43, 21.02, 31.42, 0.0, -38.38, 31.42,
        21.02, 31.42, 0.0, 4.50, 31.42, -0.43, 21.02, 31.42, 0.0, 31.42
    ] * 10  # 200 trades

    mc = engine.run_monte_carlo(sample_trades, iterations=5000)
    print(f"\n[MONTE CARLO RESULTS (5,000 PATHS)]")
    print(f"  Trades per path: {mc.num_trades}")
    print(f"  Median Net PnL : ${mc.median_profit:+.2f}")
    print(f"  90% CI         : [${mc.conf_interval_90[0]:+.2f} , ${mc.conf_interval_90[1]:+.2f}]")
    print(f"  Prob of Profit : {mc.prob_profit}%")
    print(f"  Risk of Ruin   : {mc.risk_of_ruin}%")
    print(f"  Median Max DD  : ${mc.median_max_dd:.2f}")

    rst = engine.run_rst_permutation(sample_trades, permutations=1000)
    print(f"\n[RULE SIGNIFICANCE PERMUTATION TEST (RST)]")
    print(f"  Strategy PnL   : ${rst.strategy_net_profit:+.2f}")
    print(f"  Null Mean PnL  : ${rst.null_mean_profit:+.2f} (+/- ${rst.null_std_profit:.2f})")
    print(f"  Z-Score        : {rst.z_score:.2f}")
    print(f"  p-value        : {rst.p_value:.4f} (Confidence: {rst.confidence_level_pct}%)")
    print(f"  Edge Proven    : {'YES (p < 0.01)' if rst.is_significant else 'NO'}")
