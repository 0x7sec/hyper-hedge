"""
Research & Quantitative Backtesting Suite for Hyper Hedge.
Provides 1-Minute Sub-Candle Replay, Monte Carlo Simulation, and Statistical RST.
"""

from .replay_engine import ReplayEngine, BacktestResult, TradeRecord
from .monte_carlo import MonteCarloEngine, MonteCarloResult, RSTResult
from .optimizer import StrategyOptimizer, OptimizationResult, OptimizationTrial

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
]
