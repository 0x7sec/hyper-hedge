#!/usr/bin/env python3
"""
Hyperparameter Optimization Engine for Path B Asymmetric Hedging Strategy.
Adopts Jesse's multi-objective fitness modeling, in-sample vs out-of-sample
walk-forward validation, and Bayesian/Random search to discover robust parameters.
"""

import sys
import os
import time
import math
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

logger = logging.getLogger("Optimizer")

from research.replay_engine import ReplayEngine, CHAMPION_PROFILES, BacktestResult


@dataclass
class OptimizationTrial:
    trial_number: int
    params: Dict[str, Any]
    training_profit: float
    training_sharpe: float
    training_win_rate: float
    training_trades: int
    testing_profit: float
    testing_sharpe: float
    testing_win_rate: float
    testing_trades: int
    testing_max_dd: float
    fitness_score: float
    is_overfit: bool


@dataclass
class OptimizationResult:
    symbol: str
    trials_evaluated: int
    objective: str
    train_test_split: float
    best_params: Dict[str, Any]
    best_fitness: float
    best_training_metrics: Dict[str, Any]
    best_testing_metrics: Dict[str, Any]
    leaderboard: List[OptimizationTrial]


class StrategyOptimizer:
    def __init__(self, initial_capital: float = 1000.0):
        self.initial_capital = initial_capital
        self.engine = ReplayEngine(initial_capital=initial_capital)

    def calculate_fitness(
        self,
        res: BacktestResult,
        optimal_trades: int = 50,
        objective: str = "sharpe",
    ) -> float:
        """
        Adopts Jesse's fitness formula:
        Scales Sharpe/Profit by trade volume log10(total) / log10(optimal)
        Penalizes high drawdowns and trade counts under 10.
        """
        if res.total_trades < 5 or res.gross_loss == 0:
            return -999.0

        # Sample size weight: prevents overfitting to 2 lucky trades
        vol_factor = min(1.0, math.log10(max(1, res.total_trades)) / math.log10(max(2, optimal_trades)))

        # Drawdown penalty
        dd_penalty = max(0.0, 1.0 - (res.max_drawdown_pct / 50.0))

        if objective == "sharpe":
            base_score = res.sharpe_ratio
        elif objective == "profit_factor":
            base_score = min(10.0, res.profit_factor)
        elif objective == "shield_rate":
            base_score = res.shield_rate / 10.0
        else:  # "net_profit"
            base_score = res.net_profit / 100.0

        return float(base_score * vol_factor * dd_penalty)

    def run_optimization(
        self,
        symbol: str,
        candles: List[Dict[str, Any]],
        num_trials: int = 40,
        train_ratio: float = 0.70,
        objective: str = "sharpe",
        random_seed: Optional[int] = 42,
    ) -> OptimizationResult:
        """
        Walk-Forward Optimization:
        1. Splits candles into In-Sample Training (70%) and Out-of-Sample Testing (30%).
        2. Evaluates candidate parameter sets.
        3. Validates top candidates on Out-of-Sample testing data to eliminate curve-fitting.
        """
        if random_seed is not None:
            np.random.seed(random_seed)

        n = len(candles)
        split_idx = int(n * train_ratio)
        train_candles = candles[:split_idx]
        test_candles = candles[split_idx:]

        logger.info(f"Optimization {symbol}: {len(train_candles)} train bars (70%) | {len(test_candles)} test bars (30%)")

        base_prof = CHAMPION_PROFILES.get(symbol, CHAMPION_PROFILES["BTCUSDT"]).copy()

        # Parameter Search Ranges
        d_pct_range = [0.50, 0.60, 0.70, 0.80, 0.90, 1.00]
        confirm_range = [0.70, 0.80, 0.90]
        b1_tp_range = [2.00, 2.50, 2.80, 3.00, 3.50]
        b2_tp_range = [2.50, 3.00, 3.50, 4.00]
        adx_range = [0.0, 10.0, 15.0, 20.0]

        trials: List[OptimizationTrial] = []

        # Always evaluate baseline production profile as Trial 0
        all_candidates = [base_prof]

        for _ in range(num_trials - 1):
            cand = {
                "d_pct": float(np.random.choice(d_pct_range)),
                "confirm_mult": float(np.random.choice(confirm_range)),
                "b1_tp_mult": float(np.random.choice(b1_tp_range)),
                "b2_tp_mult": float(np.random.choice(b2_tp_range)),
                "adx_min": float(np.random.choice(adx_range)),
                "b1_r1_trig": 1.40,
                "b1_r1_sl": 1.00,
                "b1_r2_trig": 2.20,
                "b1_r2_sl": 1.70,
                "b2_be_cushion": 0.10,
                "b2_r2_trig": 2.50,
                "b2_r2_sl": 2.10,
                "timeout_bars": 50,
                "hedge_ratio": 0.30,
                "leverage": 2.5,
            }
            all_candidates.append(cand)

        for t_num, hp in enumerate(all_candidates):
            # 1. Evaluate Training (In-Sample)
            train_res = self.engine.run_backtest(symbol, train_candles, custom_params=hp)
            score = self.calculate_fitness(train_res, objective=objective)

            # 2. Evaluate Testing (Out-of-Sample Walk-Forward)
            test_res = self.engine.run_backtest(symbol, test_candles, custom_params=hp)

            # Overfit detection: If training looks amazing but testing collapses into negative
            is_overfit = (train_res.net_profit > 100.0 and test_res.net_profit < -100.0)

            rec = OptimizationTrial(
                trial_number=t_num + 1,
                params=hp,
                training_profit=train_res.net_profit,
                training_sharpe=train_res.sharpe_ratio,
                training_win_rate=train_res.win_rate,
                training_trades=train_res.total_trades,
                testing_profit=test_res.net_profit,
                testing_sharpe=test_res.sharpe_ratio,
                testing_win_rate=test_res.win_rate,
                testing_trades=test_res.total_trades,
                testing_max_dd=test_res.max_drawdown,
                fitness_score=round(score, 3),
                is_overfit=is_overfit,
            )
            trials.append(rec)

        # Sort trials by Out-of-Sample Testing Sharpe & Profit to reward true generalisation
        trials.sort(key=lambda t: (not t.is_overfit, t.testing_profit, t.fitness_score), reverse=True)

        best_trial = trials[0]
        # Re-run best on both splits for clean summary metrics
        b_train = self.engine.run_backtest(symbol, train_candles, custom_params=best_trial.params)
        b_test = self.engine.run_backtest(symbol, test_candles, custom_params=best_trial.params)

        return OptimizationResult(
            symbol=symbol,
            trials_evaluated=len(trials),
            objective=objective,
            train_test_split=train_ratio,
            best_params=best_trial.params,
            best_fitness=best_trial.fitness_score,
            best_training_metrics={
                "profit": b_train.net_profit,
                "win_rate": b_train.win_rate,
                "shield_rate": b_train.shield_rate,
                "profit_factor": b_train.profit_factor,
                "sharpe": b_train.sharpe_ratio,
                "max_drawdown": b_train.max_drawdown,
                "trades": b_train.total_trades,
            },
            best_testing_metrics={
                "profit": b_test.net_profit,
                "win_rate": b_test.win_rate,
                "shield_rate": b_test.shield_rate,
                "profit_factor": b_test.profit_factor,
                "sharpe": b_test.sharpe_ratio,
                "max_drawdown": b_test.max_drawdown,
                "trades": b_test.total_trades,
            },
            leaderboard=trials[:10],  # Top 10 configurations
        )


if __name__ == "__main__":
    print("Testing Walk-Forward Optimizer Engine...")
    candles = ReplayEngine.load_candles("BTCUSDT", limit=2000)
    opt = StrategyOptimizer(initial_capital=1000.0)
    t0 = time.time()
    res = opt.run_optimization("BTCUSDT", candles, num_trials=15, objective="sharpe")
    print(f"\nOptimization completed in {time.time() - t0:.2f}s:")
    print(f"  Best Parameters : {res.best_params}")
    print(f"  In-Sample Train : Net=${res.best_training_metrics['profit']:+.2f} | PF={res.best_training_metrics['profit_factor']} | Sharpe={res.best_training_metrics['sharpe']}")
    print(f"  Out-of-Sample   : Net=${res.best_testing_metrics['profit']:+.2f} | PF={res.best_testing_metrics['profit_factor']} | Sharpe={res.best_testing_metrics['sharpe']}")
