"""
coordinator.py — Master lifecycle manager for the Bybit Discount Buy Suite.
Coordinates Engine 1 (Options), Engine 2 (Spot), and Engine 3 (Delta-Neutral),
enforcing strict $1,000 capital enclosure per engine and circuit breaker safety.
"""

import time
import logging
from typing import List, Optional

from bybit_discount_bot.config import (
    POLL_INTERVAL_SECONDS,
    DEFAULT_SYMBOL_SPOT,
    MAX_CAPITAL_PER_ENGINE,
    CIRCUIT_BREAKER_MAX_DD_PCT,
)
from bybit_discount_bot.client import BybitDiscountClient
from bybit_discount_bot.state import DiscountStateManager
from bybit_discount_bot.engines.options_engine import OptionsPutEngine
from bybit_discount_bot.engines.spot_engine import SpotAccumulatorEngine
from bybit_discount_bot.engines.neutral_engine import DeltaNeutralEngine

logger = logging.getLogger("discount_suite.coordinator")


class SuiteCoordinator:
    """Orchestrates the execution of all active Discount Buy engines."""

    def __init__(self, client: BybitDiscountClient, state_mgr: DiscountStateManager,
                 enabled_engines: Optional[List[str]] = None, symbol: str = DEFAULT_SYMBOL_SPOT):
        self.client = client
        self.state_mgr = state_mgr
        self.symbol = symbol
        self.running = False

        # Set symbol in state
        self.state_mgr.state.symbol = symbol
        self.state_mgr.state.active_pairs = [symbol]

        if enabled_engines is None:
            enabled_engines = ["options", "spot", "neutral"]
        self.enabled_engines = [e.lower() for e in enabled_engines]

        # Instantiate sub-engines with isolated state references
        self.options_engine = OptionsPutEngine(client, state_mgr.state.options_engine) if "options" in self.enabled_engines else None
        self.spot_engine = SpotAccumulatorEngine(client, state_mgr.state.spot_engine, symbol) if "spot" in self.enabled_engines else None
        self.neutral_engine = DeltaNeutralEngine(client, state_mgr.state.neutral_engine, symbol, symbol) if "neutral" in self.enabled_engines else None

    def start(self):
        """Start the continuous evaluation loop."""
        self.running = True
        logger.info(f"=== Starting Bybit Discount Buy Suite (Engines: {self.enabled_engines} | Budget: $1,000 each) ===")

        while self.running:
            try:
                self.step()
            except KeyboardInterrupt:
                logger.info("Suite interrupted by user. Shutting down gracefully...")
                break
            except Exception as e:
                logger.error(f"Unexpected error in suite coordinator step: {e}", exc_info=True)

            time.sleep(POLL_INTERVAL_SECONDS)

    def step(self):
        """Single tick across all enabled engines."""
        spot_price = self.client.get_spot_price(self.symbol)
        if spot_price <= 0:
            logger.warning(f"Could not retrieve spot price for {self.symbol}. Skipping tick.")
            return

        opt_st = self.state_mgr.state.options_engine.status
        spot_st = self.state_mgr.state.spot_engine.status
        neut_st = self.state_mgr.state.neutral_engine.status
        logger.info(f"[{self.symbol} ${spot_price:,.1f}] Engines: Opt={opt_st} | Spot={spot_st} | Neut={neut_st}")

        # 1. Options Engine Tick
        if self.options_engine:
            if self._check_circuit_breaker(self.state_mgr.state.options_engine):
                self.options_engine.tick(spot_price)

        # 2. Spot Accumulator Engine Tick
        if self.spot_engine:
            if self._check_circuit_breaker(self.state_mgr.state.spot_engine):
                self.spot_engine.tick(spot_price)

        # 3. Delta-Neutral Engine Tick
        if self.neutral_engine:
            if self._check_circuit_breaker(self.state_mgr.state.neutral_engine):
                self.neutral_engine.tick(spot_price)

        # Persist atomic state after every step
        self.state_mgr.save()

    def _check_circuit_breaker(self, engine_state) -> bool:
        """Verify engine hasn't breached the 5.0% maximum drawdown circuit breaker."""
        dd_pct = (engine_state.allocated_capital - engine_state.current_capital) / engine_state.allocated_capital * 100
        if dd_pct >= CIRCUIT_BREAKER_MAX_DD_PCT:
            if engine_state.status != "CIRCUIT_BREAKER_HALTED":
                logger.critical(f"[CIRCUIT BREAKER] {engine_state.name} exceeded {CIRCUIT_BREAKER_MAX_DD_PCT}% drawdown! HALTING ENGINE.")
                engine_state.status = "CIRCUIT_BREAKER_HALTED"
            return False
        return True
