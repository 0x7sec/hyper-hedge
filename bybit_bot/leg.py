from decimal import Decimal
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class PositionLeg:
    side: str  # "Buy" for Long, "Sell" for Short
    position_idx: int  # 1 for Long, 2 for Short in Bybit Hedge Mode
    size: Decimal
    entry_price: Decimal
    extreme_price: Decimal  # Peak for Long, Trough for Short
    trailing_sl: Decimal
    tp_target: Decimal
    last_ratchet_extreme: Decimal
    symbol: str = ""  # Market symbol (e.g. BTCUSDT, ETHUSDT, SOLUSDT)
    status: str = "ACTIVE"  # "ACTIVE", "CLOSED_TP", "CLOSED_SL", "CLOSED_MANUAL"
    exit_price: Optional[Decimal] = None
    realized_pnl: Optional[Decimal] = None

    @classmethod
    def new_long(
        cls,
        size: Decimal,
        entry_price: Decimal,
        sl_ratio: Decimal,
        tp_ratio: Decimal,
        symbol: str = "",
    ) -> "PositionLeg":
        initial_sl = entry_price * (Decimal("1") - sl_ratio)
        tp_target = entry_price * (Decimal("1") + tp_ratio)
        return cls(
            side="Buy",
            position_idx=1,
            size=size,
            entry_price=entry_price,
            extreme_price=entry_price,
            trailing_sl=initial_sl,
            tp_target=tp_target,
            last_ratchet_extreme=entry_price,
            symbol=symbol,
        )

    @classmethod
    def new_short(
        cls,
        size: Decimal,
        entry_price: Decimal,
        sl_ratio: Decimal,
        tp_ratio: Decimal,
        symbol: str = "",
    ) -> "PositionLeg":
        initial_sl = entry_price * (Decimal("1") + sl_ratio)
        tp_target = entry_price * (Decimal("1") - tp_ratio)
        return cls(
            side="Sell",
            position_idx=2,
            size=size,
            entry_price=entry_price,
            extreme_price=entry_price,
            trailing_sl=initial_sl,
            tp_target=tp_target,
            last_ratchet_extreme=entry_price,
            symbol=symbol,
        )

    def pnl(self, current_price: Decimal) -> Tuple[Decimal, Decimal]:
        """Returns (pnl_usd, pnl_pct). Uses exact exchange realized_pnl if closed."""
        if self.realized_pnl is not None:
            notional = self.entry_price * self.size
            pct = (self.realized_pnl / notional) * Decimal("100") if notional > Decimal("0") else Decimal("0")
            return self.realized_pnl, pct

        price = self.exit_price if self.exit_price is not None else current_price
        if self.entry_price == Decimal("0"):
            return Decimal("0"), Decimal("0")

        if self.side == "Buy":
            diff = price - self.entry_price
        else:
            diff = self.entry_price - price

        pnl_usd = diff * self.size
        pnl_pct = (diff / self.entry_price) * Decimal("100")
        return pnl_usd, pnl_pct

    def on_price_tick(self, current_price: Decimal, sl_ratio: Decimal, ratchet_step_ratio: Decimal) -> str:
        """
        Process a new price tick.
        Returns one of: 'HOLD', 'RATCHET_TRIGGER', 'TRIGGER_TP', 'TRIGGER_SL'.
        """
        if self.status != "ACTIVE":
            return "HOLD"

        if self.side == "Buy":
            return self._handle_long_tick(current_price, sl_ratio, ratchet_step_ratio)
        else:
            return self._handle_short_tick(current_price, sl_ratio, ratchet_step_ratio)

    def _handle_long_tick(self, current_price: Decimal, sl_ratio: Decimal, ratchet_step_ratio: Decimal) -> str:
        # Check Take Profit
        if current_price >= self.tp_target:
            self.status = "CLOSED_TP"
            self.exit_price = current_price
            return "TRIGGER_TP"

        # Check Trailing Stop Loss
        if current_price <= self.trailing_sl:
            self.status = "CLOSED_SL"
            self.exit_price = current_price
            return "TRIGGER_SL"

        # Update peak if higher
        if current_price > self.extreme_price:
            self.extreme_price = current_price
            new_sl = current_price * (Decimal("1") - sl_ratio)
            # Long SL must only ratchet UP
            if new_sl > self.trailing_sl:
                self.trailing_sl = new_sl

                # Check if advance exceeds ratchet threshold to update exchange order
                step = (self.extreme_price - self.last_ratchet_extreme) / self.last_ratchet_extreme
                if step >= ratchet_step_ratio:
                    self.last_ratchet_extreme = self.extreme_price
                    return "RATCHET_TRIGGER"

        return "HOLD"

    def _handle_short_tick(self, current_price: Decimal, sl_ratio: Decimal, ratchet_step_ratio: Decimal) -> str:
        # Check Take Profit
        if current_price <= self.tp_target:
            self.status = "CLOSED_TP"
            self.exit_price = current_price
            return "TRIGGER_TP"

        # Check Trailing Stop Loss
        if current_price >= self.trailing_sl:
            self.status = "CLOSED_SL"
            self.exit_price = current_price
            return "TRIGGER_SL"

        # Update trough if lower
        if current_price < self.extreme_price:
            self.extreme_price = current_price
            new_sl = current_price * (Decimal("1") + sl_ratio)
            # Short SL must only ratchet DOWN
            if new_sl < self.trailing_sl:
                self.trailing_sl = new_sl

                # Check if advance exceeds ratchet threshold to update exchange order
                step = (self.last_ratchet_extreme - self.extreme_price) / self.last_ratchet_extreme
                if step >= ratchet_step_ratio:
                    self.last_ratchet_extreme = self.extreme_price
                    return "RATCHET_TRIGGER"

        return "HOLD"
