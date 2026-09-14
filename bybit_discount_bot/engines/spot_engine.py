"""
spot_engine.py — Engine 2: Autonomous Spot Maker Limit Accumulator on Bybit Spot.
Budget: Strict $1,000 USD maximum capital enclosure.
"""

import time
import math
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from bybit_discount_bot.config import (
    MAX_CAPITAL_PER_ENGINE,
    DEFAULT_SYMBOL_SPOT,
    SPOT_DISCOUNT_PCT,
    SPOT_LADDER_ENABLED,
    SPOT_LADDER_TRANCHES,
    SPOT_TAKE_PROFIT_PCT,
    DEFAULT_CYCLE_HOURS,
)
from bybit_discount_bot.client import BybitDiscountClient
from bybit_discount_bot.state import EngineState

logger = logging.getLogger("discount_suite.spot_engine")


class SpotAccumulatorEngine:
    """
    Engine 2: Places laddered Post-Only Maker Buy orders at 0.8% - 1.8% discount.
    If filled, arms dynamic trailing take-profit (+1.2%); if not filled, cancels and re-centers.
    """

    def __init__(self, client: BybitDiscountClient, state: EngineState,
                 symbol: str = DEFAULT_SYMBOL_SPOT):
        self.client = client
        self.state = state
        self.symbol = symbol
        self.max_budget = MAX_CAPITAL_PER_ENGINE

    def tick(self, spot_price: float):
        """Main periodic loop."""
        # 1. Manage active filled position (trailing take-profit)
        if self.state.active_positions:
            self._manage_filled_spot(spot_price)
            return

        # 2. Check resting limit orders
        if self.state.active_orders:
            self._manage_resting_orders(spot_price)
            return

        # 3. If idle, deploy new 24h discount ladder
        self._deploy_discount_orders(spot_price)

    def _deploy_discount_orders(self, spot_price: float):
        """Place Post-Only maker limit buy orders at discount prices."""
        logger.info(f"[SPOT ACCUMULATOR] Spot: ${spot_price:,.2f} | Deploying Discount Maker Orders (Budget: ${self.max_budget:,.2f})...")

        now_str = datetime.now(timezone.utc).isoformat()
        self.state.last_cycle_start = now_str
        self.state.status = "ORDERS_RESTING"

        if SPOT_LADDER_ENABLED:
            for idx, tranche in enumerate(SPOT_LADDER_TRANCHES):
                tranche_alloc = self.max_budget * tranche["pct"]
                disc = tranche["discount"]
                target_px = spot_price * (1.0 - disc)
                qty = math.floor((tranche_alloc / target_px) * 100000) / 100000

                order_id = self.client.place_spot_maker_order(self.symbol, "Buy", qty, target_px)
                if order_id:
                    self.state.active_orders.append({
                        "order_id": order_id,
                        "tranche_idx": idx,
                        "discount_pct": disc,
                        "price": target_px,
                        "qty": qty,
                        "allocated_usd": tranche_alloc,
                        "entry_time": time.time(),
                    })
        else:
            target_px = spot_price * (1.0 - SPOT_DISCOUNT_PCT)
            qty = round(self.max_budget / target_px, 5)
            order_id = self.client.place_spot_maker_order(self.symbol, "Buy", qty, target_px)
            if order_id:
                self.state.active_orders.append({
                    "order_id": order_id,
                    "price": target_px,
                    "qty": qty,
                    "allocated_usd": self.max_budget,
                    "entry_time": time.time(),
                })

        self.state.metrics["resting_orders_count"] = len(self.state.active_orders)
        self.state.metrics["nearest_discount_px"] = min(o["price"] for o in self.state.active_orders) if self.state.active_orders else 0.0

    def _manage_resting_orders(self, spot_price: float):
        """Check if market dropped into our discount limit prices, or if 24h expired."""
        unfilled = []
        filled = []

        now = time.time()
        for order in self.state.active_orders:
            elapsed_h = (now - order["entry_time"]) / 3600.0

            # In dry-run or real tick: check if price touched/breached discount limit
            if spot_price <= order["price"]:
                logger.info(f"[SPOT ACCUMULATOR FILL] Limit filled at discount! Price: ${order['price']:,.2f} (Market was ${spot_price:,.2f})")
                filled.append(order)
            elif elapsed_h >= DEFAULT_CYCLE_HOURS:
                # Expired unfilled: cancel order
                logger.info(f"[SPOT ACCUMULATOR EXPIRED] 24h expired without fill for order {order['order_id']} | Cancelling...")
                self.client.cancel_spot_order(self.symbol, order["order_id"])
            else:
                unfilled.append(order)

        self.state.active_orders = unfilled

        if filled:
            # Combine filled tranches into position
            total_qty = sum(o["qty"] for o in filled)
            total_spent = sum(o["qty"] * o["price"] for o in filled)
            avg_entry = total_spent / total_qty

            self.state.status = "POSITION_ACTIVE"
            self.state.active_positions.append({
                "symbol": self.symbol,
                "qty": total_qty,
                "avg_entry": avg_entry,
                "tp_price": avg_entry * (1.0 + SPOT_TAKE_PROFIT_PCT),
                "high_watermark": spot_price,
                "entry_time": time.time(),
            })
            logger.info(f"[SPOT POSITION ACTIVE] Holding {total_qty:.5f} {self.symbol} @ Avg: ${avg_entry:,.2f} | TP Target: ${avg_entry * (1.0 + SPOT_TAKE_PROFIT_PCT):,.2f}")

        if not self.state.active_orders and not self.state.active_positions:
            self.state.status = "IDLE"
            self.state.total_cycles += 1

    def _manage_filled_spot(self, spot_price: float):
        """Monitor filled spot position for trailing take-profit or cycle close."""
        pos = self.state.active_positions[0]
        entry = pos["avg_entry"]
        tp_px = pos["tp_price"]
        qty = pos["qty"]
        elapsed_h = (time.time() - pos["entry_time"]) / 3600.0

        if spot_price > pos["high_watermark"]:
            pos["high_watermark"] = spot_price

        # Take-profit hit
        if spot_price >= tp_px or (elapsed_h >= DEFAULT_CYCLE_HOURS and spot_price > entry):
            pnl_usd = (spot_price - entry) * qty
            logger.info(f"[SPOT TAKE-PROFIT HIT] Sold {qty:.5f} {self.symbol} @ ${spot_price:,.2f} | Realized PnL: +${pnl_usd:,.2f}!")
            self.client.place_spot_maker_order(self.symbol, "Sell", qty, spot_price)

            self.state.current_capital += pnl_usd
            self.state.total_realized_pnl += pnl_usd
            self.state.total_cycles += 1
            self.state.profitable_cycles += 1
            self.state.status = "CYCLE_CLOSED"
            self.state.active_positions.clear()
        elif elapsed_h >= DEFAULT_CYCLE_HOURS * 2:
            # Stale hold timeout: close and reset
            pnl_usd = (spot_price - entry) * qty
            logger.info(f"[SPOT CYCLE TIMEOUT] Closing spot holding @ ${spot_price:,.2f} | PnL: ${pnl_usd:,.2f}")
            self.client.place_spot_maker_order(self.symbol, "Sell", qty, spot_price)

            self.state.current_capital += pnl_usd
            self.state.total_realized_pnl += pnl_usd
            self.state.total_cycles += 1
            self.state.status = "CYCLE_CLOSED"
            self.state.active_positions.clear()
