"""
test_options_harvester.py — Unit tests for Bybit UTA Delta-Neutral Options Harvester.
Verifies $1,000 capital enclosure, Greeks aggregation, 70% profit target, 2x SL, DDH, and circuit breaker.
"""

import os
import unittest
import time

from options_harvester.config import (
    ALLOCATED_CAPITAL,
    CIRCUIT_BREAKER_MAX_DD_PCT,
    PREMIUM_STOP_LOSS_MULT,
    DDH_TOLERANCE_BAND,
)
from options_harvester.client import BybitOptionsClient
from options_harvester.engine import OptionsHarvesterEngine
from options_harvester.greeks import calculate_bsm_greeks, GreeksAggregator


class TestOptionsHarvester(unittest.TestCase):

    def setUp(self):
        self.client = BybitOptionsClient(dry_run=True)
        self.test_state = "test_opt_state.json"
        self.test_trades = "test_opt_trades.csv"
        self.engine = OptionsHarvesterEngine(
            client=self.client,
            state_file=self.test_state,
            trades_file=self.test_trades,
        )

    def tearDown(self):
        for f in [self.test_state, f"{self.test_state}.tmp", self.test_trades]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

    def test_strict_1k_capital_enclosure(self):
        """Ensure engine strictly allocates $1,000 USD."""
        self.assertEqual(self.engine.allocated_capital, 1000.0)
        self.assertEqual(self.engine.current_capital, 1000.0)
        self.assertEqual(ALLOCATED_CAPITAL, 1000.0)

    def test_bsm_greeks_math(self):
        """Verify Black-Scholes analytical Greeks values."""
        spot = 75000.0
        strike_call = 79000.0
        strike_put = 71000.0
        dte_years = 1.0 / 365.0  # 24 hours
        iv = 0.50  # 50%

        call_g = calculate_bsm_greeks(spot, strike_call, dte_years, iv, is_call=True)
        put_g = calculate_bsm_greeks(spot, strike_put, dte_years, iv, is_call=False)

        # OTM Call delta should be positive and < 0.50
        self.assertGreater(call_g["delta"], 0.0)
        self.assertLess(call_g["delta"], 0.50)

        # OTM Put delta should be negative and > -0.50
        self.assertLess(put_g["delta"], 0.0)
        self.assertGreater(put_g["delta"], -0.50)

        # Gammas should be positive
        self.assertGreater(call_g["gamma"], 0.0)
        self.assertGreater(put_g["gamma"], 0.0)

    def test_portfolio_greeks_aggregation(self):
        """Verify short strangle net delta neutrality and positive theta."""
        put_leg = {
            "qty": 0.01,
            "delta": -0.15,
            "gamma": 0.00002,
            "theta": -5.0,  # Decay from buyer perspective
            "vega": 10.0,
            "active": True,
        }
        call_leg = {
            "qty": 0.01,
            "delta": 0.15,
            "gamma": 0.00002,
            "theta": -5.0,
            "vega": 10.0,
            "active": True,
        }

        # Strangle: Short Put delta (+0.0015) + Short Call delta (-0.0015) = 0.0000
        greeks = GreeksAggregator.calculate_portfolio_greeks(put_leg, call_leg, perp_qty=0.0)
        self.assertAlmostEqual(greeks["net_delta_btc"], 0.0, places=4)
        self.assertAlmostEqual(greeks["normalized_net_delta"], 0.0, places=4)
        # Net theta must be strictly positive income for the seller!
        self.assertGreater(greeks["net_theta_usd_per_day"], 0.0)

    def test_70_percent_profit_take(self):
        """Verify strangle automatically closes when 70% of premium decays."""
        spot = 75000.0
        # Initialize manual strangle
        self.engine.active_put = {
            "symbol": "BTC-TEST-73000-P-USDT",
            "strike": 73000.0,
            "entry_price": 200.0,
            "mark_price": 200.0,
            "delta": -0.15,
            "gamma": 0.00001,
            "theta": -5.0,
            "qty": 0.01,
            "active": True,
        }
        self.engine.active_call = {
            "symbol": "BTC-TEST-79000-C-USDT",
            "strike": 79000.0,
            "entry_price": 200.0,
            "mark_price": 200.0,
            "delta": 0.15,
            "gamma": 0.00001,
            "theta": -5.0,
            "qty": 0.01,
            "active": True,
        }
        self.engine.initial_net_premium = (200.0 + 200.0) * 0.01  # $4.00 total
        self.engine.state = "STATE_2_HARVESTING"

        # Premium decays by 75% (from $400 combined mark down to $100 combined mark)
        self.engine.active_put["mark_price"] = 50.0
        self.engine.active_call["mark_price"] = 50.0

        telemetry = self.engine.tick(spot)

        self.assertEqual(telemetry["state"], "STATE_0_SCANNING")
        self.assertEqual(self.engine.profitable_cycles, 1)
        self.assertGreater(self.engine.current_capital, 1000.0)
        self.assertGreater(self.engine.total_realized_pnl, 0.0)

    def test_2x_premium_stop_loss(self):
        """Verify 2.0x premium hard stop triggers rapid liquidation."""
        spot = 75000.0
        self.engine.active_put = {
            "symbol": "BTC-TEST-73000-P-USDT",
            "strike": 73000.0,
            "entry_price": 100.0,
            "mark_price": 100.0,
            "delta": -0.15,
            "gamma": 0.00001,
            "theta": -5.0,
            "qty": 0.01,
            "active": True,
        }
        self.engine.active_call = {
            "symbol": "BTC-TEST-79000-C-USDT",
            "strike": 79000.0,
            "entry_price": 100.0,
            "mark_price": 100.0,
            "delta": 0.15,
            "gamma": 0.00001,
            "theta": -5.0,
            "qty": 0.01,
            "active": True,
        }
        self.engine.initial_net_premium = (100.0 + 100.0) * 0.01
        self.engine.state = "STATE_2_HARVESTING"

        # Price explodes against the call: call mark surges to 2.1x ($210)
        self.engine.active_call["mark_price"] = 210.0

        telemetry = self.engine.tick(spot)

        self.assertEqual(telemetry["state"], "STATE_0_SCANNING")
        self.assertEqual(self.engine.loss_cycles, 1)
        self.assertLess(self.engine.current_capital, 1000.0)

    def test_ddh_rebalance_trigger(self):
        """Verify DDH engine triggers micro-perp rebalance when |Net Delta| > 0.10."""
        spot = 75000.0
        self.engine.active_put = {
            "symbol": "BTC-TEST-73000-P-USDT",
            "strike": 73000.0,
            "entry_price": 100.0,
            "mark_price": 90.0,
            "delta": -0.05,  # Put delta decayed towards 0
            "gamma": 0.00001,
            "theta": -5.0,
            "qty": 0.01,
            "active": True,
        }
        self.engine.active_call = {
            "symbol": "BTC-TEST-79000-C-USDT",
            "strike": 79000.0,
            "entry_price": 100.0,
            "mark_price": 120.0,
            "delta": 0.35,  # Call delta surged to +0.35
            "gamma": 0.00002,
            "theta": -8.0,
            "qty": 0.01,
            "active": True,
        }
        self.engine.initial_net_premium = 2.0
        self.engine.state = "STATE_2_HARVESTING"

        # Normalized net delta = -0.35 - (-0.05) = -0.30 (Breaches ±0.10 band!)
        telemetry = self.engine.tick(spot)

        self.assertGreater(self.engine.ddh.total_rebalance_count, 0)
        # Since portfolio was short delta (-0.30), DDH must buy perp to neutralize
        self.assertGreater(self.engine.ddh.current_perp_position, 0.0)

    def test_5_pct_circuit_breaker_drawdown(self):
        """Verify 5% max drawdown circuit breaker halts bot."""
        spot = 75000.0
        self.engine.peak_capital = 1000.0
        # Drawdown > 5% ($50 on $1000)
        self.engine.current_capital = 945.0

        telemetry = self.engine.tick(spot)

        self.assertTrue(self.engine.circuit_breaker_active)
        self.assertEqual(self.engine.state, "STATE_6_CIRCUIT_BREAKER")

    def test_client_order_cancellation_and_position_guards(self):
        """Verify client order cancellation and protection against zero-size buybacks."""
        # Cancel order test
        self.assertTrue(self.client.cancel_option_orders("BTC-TEST-73000-P-USDT"))
        self.assertTrue(self.client.cancel_option_orders())

        # Close leg in dry run should succeed
        self.assertTrue(self.client.close_option_leg("BTC-TEST-73000-P-USDT", 0.01))

        # Test sell option leg with market and limit order types
        order_mkt = self.client.sell_option_leg("BTC-TEST-73000-P-USDT", 0.01, 100.0, order_type="Market")
        self.assertIsNotNone(order_mkt)
        order_lmt = self.client.sell_option_leg("BTC-TEST-79000-C-USDT", 0.01, 100.0, order_type="Limit")
        self.assertIsNotNone(order_lmt)


if __name__ == "__main__":
    unittest.main()
