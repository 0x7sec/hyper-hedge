"""
test_discount_suite.py — Unit and integration tests for the Bybit Discount Buy Suite.
Verifies strict $1,000 capital limits, order generation, delta-neutral hedging, and circuit breaker.
"""

import unittest
import time
from bybit_discount_bot.config import MAX_CAPITAL_PER_ENGINE
from bybit_discount_bot.client import BybitDiscountClient
from bybit_discount_bot.state import DiscountStateManager, EngineState
from bybit_discount_bot.engines.options_engine import OptionsPutEngine
from bybit_discount_bot.engines.spot_engine import SpotAccumulatorEngine
from bybit_discount_bot.engines.neutral_engine import DeltaNeutralEngine
from bybit_discount_bot.coordinator import SuiteCoordinator


class TestDiscountSuite(unittest.TestCase):

    def setUp(self):
        self.client = BybitDiscountClient(dry_run=True)
        self.state_mgr = DiscountStateManager(file_path="test_discount_state.json", dry_run=True)

    def tearDown(self):
        import os
        for p in ["test_discount_state.json", "test_discount_state.json.tmp"]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    def test_strict_capital_limits(self):
        """Ensure all 3 engines enforce strict $1,000 max budget."""
        opt_engine = OptionsPutEngine(self.client, self.state_mgr.state.options_engine)
        spot_engine = SpotAccumulatorEngine(self.client, self.state_mgr.state.spot_engine)
        neutral_engine = DeltaNeutralEngine(self.client, self.state_mgr.state.neutral_engine)

        self.assertEqual(opt_engine.max_budget, 1000.0)
        self.assertEqual(spot_engine.max_budget, 1000.0)
        self.assertEqual(neutral_engine.max_budget, 1000.0)

    def test_engine1_options_put_underwriting(self):
        """Test Options Engine sells put within $1,000 collateral and tracks expiry."""
        opt_engine = OptionsPutEngine(self.client, self.state_mgr.state.options_engine)
        spot_px = 60000.0

        # Initial tick should sell put
        opt_engine.tick(spot_px)
        self.assertEqual(opt_engine.state.status, "CONTRACT_ACTIVE")
        self.assertEqual(len(opt_engine.state.active_positions), 1)

        pos = opt_engine.state.active_positions[0]
        self.assertLessEqual(pos["qty"] * pos["strike"], 1000.0)

        # Simulate 24h passing with price above strike -> Worthless expiry WIN
        pos["entry_time"] -= 25 * 3600  # fast-forward 25 hours
        opt_engine.tick(spot_px)

        self.assertEqual(opt_engine.state.status, "CYCLE_CLOSED")
        self.assertEqual(opt_engine.state.profitable_cycles, 1)
        self.assertGreater(opt_engine.state.current_capital, 1000.0)

    def test_engine2_spot_accumulator_ladder(self):
        """Test Spot Engine places 3 tranches within $1,000 budget and takes profit."""
        spot_engine = SpotAccumulatorEngine(self.client, self.state_mgr.state.spot_engine)
        spot_px = 60000.0

        spot_engine.tick(spot_px)
        self.assertEqual(spot_engine.state.status, "ORDERS_RESTING")
        self.assertEqual(len(spot_engine.state.active_orders), 3)

        total_alloc = sum(o["allocated_usd"] for o in spot_engine.state.active_orders)
        self.assertAlmostEqual(total_alloc, 1000.0, places=2)

        # Simulate price drop to fill all 3 tranches
        lowest_px = min(o["price"] for o in spot_engine.state.active_orders)
        spot_engine.tick(lowest_px)

        self.assertEqual(spot_engine.state.status, "POSITION_ACTIVE")
        self.assertEqual(len(spot_engine.state.active_positions), 1)

        # Simulate price bounce hitting take-profit (+1.2%)
        pos = spot_engine.state.active_positions[0]
        tp_px = pos["tp_price"] + 10.0
        spot_engine.tick(tp_px)

        self.assertEqual(spot_engine.state.status, "CYCLE_CLOSED")
        self.assertEqual(spot_engine.state.profitable_cycles, 1)
        self.assertGreater(spot_engine.state.current_capital, 1000.0)

    def test_engine3_delta_neutral_atomic_hedge(self):
        """Test Delta-Neutral Engine pairs spot discount buy with perp short hedge (net delta = 0)."""
        neutral_engine = DeltaNeutralEngine(self.client, self.state_mgr.state.neutral_engine)
        spot_px = 60000.0
        self.client.simulated_price = spot_px

        neutral_engine.tick(spot_px)
        self.assertEqual(neutral_engine.state.status, "RESTING_DISCOUNT_LIMIT")
        self.assertEqual(len(neutral_engine.state.active_orders), 1)

        order = neutral_engine.state.active_orders[0]
        self.assertLessEqual(order["discount_price"] * order["qty"], 1000.0)

        # Simulate price drop triggering spot fill -> Should lock delta-neutral pair
        self.client.simulated_price = order["discount_price"]
        neutral_engine.tick(order["discount_price"])

        self.assertEqual(neutral_engine.state.status, "DELTA_HEDGED_ACTIVE")
        self.assertEqual(len(neutral_engine.state.active_positions), 1)

        hedged_pos = neutral_engine.state.active_positions[0]
        # Verify delta hedge: spot qty == perp qty
        self.assertEqual(hedged_pos["spot_qty"], hedged_pos["perp_qty"])
        self.assertGreater(hedged_pos["locked_spread_usd"], 0.0)

        # Fast-forward 24h to close cycle and bank locked spread
        hedged_pos["entry_time"] -= 25 * 3600
        self.client.simulated_price = spot_px
        neutral_engine.tick(spot_px)

        self.assertEqual(neutral_engine.state.status, "CYCLE_CLOSED")
        self.assertEqual(neutral_engine.state.profitable_cycles, 1)
        self.assertGreater(neutral_engine.state.current_capital, 1000.0)

    def test_circuit_breaker_halts_engine(self):
        """Verify circuit breaker trips if capital drops > 5%."""
        coordinator = SuiteCoordinator(self.client, self.state_mgr)
        engine_state = self.state_mgr.state.spot_engine
        engine_state.current_capital = 940.0  # -6.0% drawdown

        allowed = coordinator._check_circuit_breaker(engine_state)
        self.assertFalse(allowed)
        self.assertEqual(engine_state.status, "CIRCUIT_BREAKER_HALTED")


if __name__ == "__main__":
    unittest.main()
