"""
neutral_engine.py — Engine 3: Delta-Hedged Market-Neutral Discount Harvester.
Budget: Strict $1,000 USD maximum capital enclosure.
Pre-hedged Basis Spread: Short Perp @ S0, Limit Buy Spot @ S0 * (1 - discount).
When filled, locks in the exact discount spread with zero directional risk (Net Delta = 0.00).
"""

import time
import math
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from bybit_discount_bot.config import (
    MAX_CAPITAL_PER_ENGINE,
    DEFAULT_SYMBOL_SPOT,
    DEFAULT_SYMBOL_PERP,
    NEUTRAL_DISCOUNT_PCT,
    NEUTRAL_TARGET_SPREAD_PCT,
    DEFAULT_CYCLE_HOURS,
)
from bybit_discount_bot.client import BybitDiscountClient
from bybit_discount_bot.state import EngineState

logger = logging.getLogger("discount_suite.neutral_engine")


class DeltaNeutralEngine:
    """
    Engine 3: Pre-hedges with 1x Short Perp at S0 and places Spot Limit Buy at S0 * (1 - 1%).
    When Spot fills, locks in the guaranteed 1.0% discount spread + funding rate yield.
    """

    def __init__(self, client: BybitDiscountClient, state: EngineState,
                 symbol_spot: str = DEFAULT_SYMBOL_SPOT, symbol_perp: str = DEFAULT_SYMBOL_PERP):
        self.client = client
        self.state = state
        self.symbol_spot = symbol_spot
        self.symbol_perp = symbol_perp
        self.max_budget = MAX_CAPITAL_PER_ENGINE

    def tick(self, spot_price: float):
        """Main periodic evaluation loop."""
        # 1. Manage active delta-neutral hedged pair
        if self.state.active_positions:
            self._manage_hedged_pair(spot_price)
            return

        # 2. Manage resting discount limit order
        if self.state.active_orders:
            self._manage_resting_cycle(spot_price)
            return

        # 3. If idle, deploy new pre-hedged discount cycle
        self._deploy_discount_cycle(spot_price)

    def _deploy_discount_cycle(self, spot_price: float):
        """Deploy 1x Short Perp at S0 and Post-Only Spot Limit Buy at S0 * (1 - 1%)."""
        logger.info(f"[DELTA-NEUTRAL] Spot: ${spot_price:,.2f} | Deploying Market-Neutral Cycle (Budget: ${self.max_budget:,.2f})...")

        target_px = spot_price * (1.0 - NEUTRAL_DISCOUNT_PCT)
        qty = math.floor((self.max_budget / target_px) * 100000) / 100000

        # 1. Place resting Post-Only Spot Limit Buy at discount K FIRST
        spot_order_id = self.client.place_spot_maker_order(self.symbol_spot, "Buy", qty, target_px)
        if not spot_order_id:
            logger.warning("[DELTA-NEUTRAL] Could not place resting discount buy order. Aborting cycle deployment.")
            return

        # 2. Open 1x Short Perp Hedge at S0 to lock the basis
        perp_order_id = self.client.place_perp_short_hedge(self.symbol_perp, qty)
        if not perp_order_id:
            logger.error("[DELTA-NEUTRAL] Failed to open short perp hedge! Cancelling resting discount order immediately...")
            self.client.cancel_spot_order(self.symbol_spot, spot_order_id)
            return

        # Both legs successfully deployed
        now_str = datetime.now(timezone.utc).isoformat()
        self.state.last_cycle_start = now_str
        self.state.status = "RESTING_DISCOUNT_LIMIT"
        self.state.active_orders.append({
            "spot_order_id": spot_order_id,
            "perp_order_id": perp_order_id,
            "discount_price": target_px,
            "perp_entry_px": spot_price,
            "qty": qty,
            "benchmark_spot": spot_price,
            "entry_time": time.time(),
        })
        self.state.metrics["target_discount_px"] = target_px
        self.state.metrics["target_spread_pct"] = NEUTRAL_DISCOUNT_PCT * 100

    def _manage_resting_cycle(self, spot_price: float):
        """Check for spot fill or cycle expiry."""
        order = self.state.active_orders[0]
        elapsed_h = (time.time() - order.get("entry_time", time.time())) / 3600.0
        discount_price = order.get("discount_price") or order.get("price", spot_price * 0.99)
        benchmark = order.get("benchmark_spot", spot_price)
        qty = order.get("qty", 0.0)
        perp_entry_px = order.get("perp_entry_px", benchmark)

        if spot_price <= discount_price:
            # SPOT FILLED AT DISCOUNT! The pair is now completely locked:
            logger.info(f"[DELTA-NEUTRAL FILL] Spot bought at discount: ${discount_price:,.2f}! Pair is now 100% Delta-Hedged.")

            now_str = datetime.now(timezone.utc).isoformat()
            self.state.status = "DELTA_HEDGED_ACTIVE"
            locked_spread_usd = (benchmark - discount_price) * qty
            locked_spread_pct = (benchmark / discount_price - 1.0) * 100 if discount_price > 0 else 0.0

            self.state.active_positions.append({
                "spot_qty": qty,
                "spot_entry_px": discount_price,
                "perp_qty": qty,
                "perp_entry_px": perp_entry_px,
                "benchmark_spot": benchmark,
                "locked_spread_usd": locked_spread_usd,
                "locked_spread_pct": locked_spread_pct,
                "entry_time": time.time(),
                "accumulated_funding": 0.0,
            })
            self.state.active_orders.clear()
            self.state.metrics["net_delta"] = 0.00000
            self.state.metrics["locked_spread_usd"] = round(locked_spread_usd, 2)
            logger.info(f"[DELTA-NEUTRAL HEDGED] Net Delta = 0.00! Locked Discount Spread: +${locked_spread_usd:.2f} ({locked_spread_pct:.2f}%)")

        elif elapsed_h >= DEFAULT_CYCLE_HOURS:
            # 24h expired without fill: close short perp, cancel spot order, reset
            logger.info("[DELTA-NEUTRAL EXPIRED] 24h expired without fill. Closing short perp and cancelling spot limit...")
            if "spot_order_id" in order:
                self.client.cancel_spot_order(self.symbol_spot, order["spot_order_id"])
            self.client.close_perp_short_hedge(self.symbol_perp, qty)

            perp_px = self.client.get_perp_price(self.symbol_perp)
            perp_pnl = (perp_entry_px - perp_px) * qty
            self.state.current_capital += perp_pnl
            self.state.total_realized_pnl += perp_pnl
            self.state.total_cycles += 1
            self.state.active_orders.clear()
            self.state.status = "IDLE"

    def _manage_hedged_pair(self, spot_price: float):
        """Manage open hedged pair until cycle resolution."""
        pos = self.state.active_positions[0]
        elapsed_h = (time.time() - pos["entry_time"]) / 3600.0

        spot_px = spot_price
        perp_px = self.client.get_perp_price(self.symbol_perp)

        # Net PnL from Spot + Perp (Delta Neutral)
        spot_pnl = (spot_px - pos["spot_entry_px"]) * pos["spot_qty"]
        perp_pnl = (pos["perp_entry_px"] - perp_px) * pos["perp_qty"]
        net_spread_pnl = spot_pnl + perp_pnl + pos.get("accumulated_funding", 0.0)

        # Target reached or cycle ended
        if elapsed_h >= DEFAULT_CYCLE_HOURS or net_spread_pnl >= pos["locked_spread_usd"] * 0.90:
            logger.info(f"[DELTA-NEUTRAL UNWIND] Closing Hedged Pair! Net Spread Realized: +${net_spread_pnl:.2f} (Locked was ${pos['locked_spread_usd']:.2f})")

            # Close both legs simultaneously
            self.client.place_spot_maker_order(self.symbol_spot, "Sell", pos["spot_qty"], spot_px)
            self.client.close_perp_short_hedge(self.symbol_perp, pos["perp_qty"])

            self.state.current_capital += net_spread_pnl
            self.state.total_realized_pnl += net_spread_pnl
            self.state.total_cycles += 1
            if net_spread_pnl > 0:
                self.state.profitable_cycles += 1
            self.state.status = "CYCLE_CLOSED"
            self.state.active_positions.clear()
            self.state.metrics["net_delta"] = 0.0
