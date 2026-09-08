import unittest
from decimal import Decimal
from bybit_bot.leg import PositionLeg


class TestBybitStrategy(unittest.TestCase):
    def setUp(self):
        self.entry = Decimal("5000.00")
        self.size = Decimal("0.01")
        self.sl_ratio = Decimal("0.03")  # 3%
        self.tp_ratio = Decimal("0.06")  # 6%
        self.ratchet_step = Decimal("0.005")  # 0.5%

    def test_long_trailing_stop_loss_ratchet(self):
        leg = PositionLeg.new_long(self.size, self.entry, self.sl_ratio, self.tp_ratio)

        # Invariant 1: Initial SL must be exactly 3% below entry (4850)
        self.assertEqual(leg.trailing_sl, Decimal("4850.00"))
        # Invariant 2: Initial TP must be exactly 6% above entry (5300)
        self.assertEqual(leg.tp_target, Decimal("5300.00"))

        # Price rises to 5100 (+2%) -> SL should ratchet to 5100 * 0.97 = 4947
        action = leg.on_price_tick(Decimal("5100.00"), self.sl_ratio, self.ratchet_step)
        self.assertEqual(action, "RATCHET_TRIGGER")
        self.assertEqual(leg.extreme_price, Decimal("5100.00"))
        self.assertEqual(leg.trailing_sl, Decimal("4947.00"))

        # Invariant 3: Price pulls back to 5000 -> SL must NOT decrease!
        action = leg.on_price_tick(Decimal("5000.00"), self.sl_ratio, self.ratchet_step)
        self.assertEqual(action, "HOLD")
        self.assertEqual(leg.trailing_sl, Decimal("4947.00"))

        # Price drops to 4947 -> Triggers Stop Loss!
        action = leg.on_price_tick(Decimal("4947.00"), self.sl_ratio, self.ratchet_step)
        self.assertEqual(action, "TRIGGER_SL")
        self.assertEqual(leg.status, "CLOSED_SL")

    def test_long_take_profit(self):
        leg = PositionLeg.new_long(self.size, self.entry, self.sl_ratio, self.tp_ratio)

        # Price jumps to 5310 (> 5300 TP)
        action = leg.on_price_tick(Decimal("5310.00"), self.sl_ratio, self.ratchet_step)
        self.assertEqual(action, "TRIGGER_TP")
        self.assertEqual(leg.status, "CLOSED_TP")
        pnl_usd, pnl_pct = leg.pnl(Decimal("5310.00"))
        self.assertGreaterEqual(pnl_pct, Decimal("6.0"))

    def test_short_trailing_stop_loss_ratchet(self):
        leg = PositionLeg.new_short(self.size, self.entry, self.sl_ratio, self.tp_ratio)

        # Invariant 1: Initial SL must be exactly 3% above entry (5150)
        self.assertEqual(leg.trailing_sl, Decimal("5150.00"))
        # Invariant 2: Initial TP must be exactly 6% below entry (4700)
        self.assertEqual(leg.tp_target, Decimal("4700.00"))

        # Price drops to 4900 (-2%) -> SL should ratchet to 4900 * 1.03 = 5047
        action = leg.on_price_tick(Decimal("4900.00"), self.sl_ratio, self.ratchet_step)
        self.assertEqual(action, "RATCHET_TRIGGER")
        self.assertEqual(leg.extreme_price, Decimal("4900.00"))
        self.assertEqual(leg.trailing_sl, Decimal("5047.00"))

        # Invariant 3: Price bounces to 4950 -> SL must NOT increase!
        action = leg.on_price_tick(Decimal("4950.00"), self.sl_ratio, self.ratchet_step)
        self.assertEqual(action, "HOLD")
        self.assertEqual(leg.trailing_sl, Decimal("5047.00"))

        # Price rises to 5050 -> Triggers Stop Loss!
        action = leg.on_price_tick(Decimal("5050.00"), self.sl_ratio, self.ratchet_step)
        self.assertEqual(action, "TRIGGER_SL")
        self.assertEqual(leg.status, "CLOSED_SL")

    def test_short_take_profit(self):
        leg = PositionLeg.new_short(self.size, self.entry, self.sl_ratio, self.tp_ratio)

        # Price drops to 4690 (< 4700 TP)
        action = leg.on_price_tick(Decimal("4690.00"), self.sl_ratio, self.ratchet_step)
        self.assertEqual(action, "TRIGGER_TP")
        self.assertEqual(leg.status, "CLOSED_TP")
        pnl_usd, pnl_pct = leg.pnl(Decimal("4690.00"))
        self.assertGreaterEqual(pnl_pct, Decimal("6.0"))


if __name__ == "__main__":
    unittest.main()
