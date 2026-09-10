"""
Research & Quantitative Backtesting Suite for Hyper Hedge.
Provides 1-Minute Sub-Candle Replay, Monte Carlo Simulation, and Statistical RST.
"""

from .replay_engine import ReplayEngine, BacktestResult, TradeRecord
from .monte_carlo import MonteCarloEngine, MonteCarloResult, RSTResult
from .optimizer import StrategyOptimizer, OptimizationResult, OptimizationTrial
from .test_store import save_test_run, get_test_run, list_test_runs, delete_test_run

__all__ = [
    "ReplayEngine",
    "BacktestResult",
    "TradeRecord",
    "MonteCarloEngine",
    "MonteCarloResult",
    "RSTResult",
    "StrategyOptimizer",
    "OptimizationResult",
    "OptimizationTrial",
    "save_test_run",
    "get_test_run",
    "list_test_runs",
    "delete_test_run",
]

