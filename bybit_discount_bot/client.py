"""
client.py — Bybit V5 Unified Trading Account (UTA) client wrapper.
Supports Spot, Linear Perps, and European Options with full dry-run simulation mode.
"""

import time
import math
import logging
from typing import Dict, Any, List, Optional

try:
    from pybit.unified_trading import HTTP
except ImportError:
    HTTP = None

from bybit_discount_bot.config import BYBIT_API_KEY, BYBIT_API_SECRET, TESTNET

logger = logging.getLogger("discount_suite.client")


class BybitDiscountClient:
    """Unified client for Spot, Linear Perps, and Options on Bybit V5."""

    def __init__(self, api_key: str = BYBIT_API_KEY, api_secret: str = BYBIT_API_SECRET,
                 testnet: bool = TESTNET, dry_run: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.dry_run = dry_run

        if not self.dry_run and HTTP and self.api_key and self.api_secret:
            self.session = HTTP(testnet=self.testnet, api_key=self.api_key, api_secret=self.api_secret)
        else:
            self.session = None

        # Simulated state for dry-run
        self._simulated_orders: Dict[str, Dict[str, Any]] = {}
        self._order_counter = 1000
        self.simulated_price: Optional[float] = None
        self.use_linear_spot_fallback: bool = False
        self._order_categories: Dict[str, str] = {}

    # ── Market Data ──────────────────────────────────────────────────────────

    def get_spot_price(self, symbol: str = "BTCUSDT") -> float:
        """Fetch current best bid/ask or last price on spot."""
        if self.simulated_price is not None:
            return self.simulated_price
        if self.session:
            try:
                res = self.session.get_tickers(category="spot", symbol=symbol)
                if res.get("retCode") == 0 and res.get("result", {}).get("list"):
                    item = res["result"]["list"][0]
                    return float(item.get("lastPrice") or item.get("bid1Price") or 0.0)
            except Exception as e:
                logger.error(f"Error fetching spot price for {symbol}: {e}")

        # Fallback to public endpoint without auth
        try:
            import requests
            domain = "api-testnet.bybit.com" if self.testnet else "api.bybit.com"
            url = f"https://{domain}/v5/market/tickers?category=spot&symbol={symbol}"
            r = requests.get(url, timeout=5).json()
            if r.get("retCode") == 0 and r.get("result", {}).get("list"):
                item = r["result"]["list"][0]
                return float(item.get("lastPrice") or 0.0)
        except Exception:
            pass

        # Also try perp mark price as fallback
        return self.get_perp_price(symbol)

    def get_perp_price(self, symbol: str = "BTCUSDT") -> float:
        """Fetch current mark price on linear perpetual."""
        if self.simulated_price is not None:
            return self.simulated_price
        if self.session:
            try:
                res = self.session.get_tickers(category="linear", symbol=symbol)
                if res.get("retCode") == 0 and res.get("result", {}).get("list"):
                    item = res["result"]["list"][0]
                    return float(item.get("markPrice") or item.get("lastPrice") or 0.0)
            except Exception as e:
                logger.error(f"Error fetching perp price for {symbol}: {e}")

        try:
            import requests
            domain = "api-testnet.bybit.com" if self.testnet else "api.bybit.com"
            url = f"https://{domain}/v5/market/tickers?category=linear&symbol={symbol}"
            r = requests.get(url, timeout=5).json()
            if r.get("retCode") == 0 and r.get("result", {}).get("list"):
                item = r["result"]["list"][0]
                return float(item.get("markPrice") or 0.0)
        except Exception:
            pass
        return 77000.0

    def get_options_chain(self, base_coin: str = "BTC") -> List[Dict[str, Any]]:
        """Fetch active European put options for the base coin."""
        options = []
        if self.session:
            try:
                res = self.session.get_tickers(category="option", baseCoin=base_coin)
                if res.get("retCode") == 0:
                    options = res.get("result", {}).get("list", [])
            except Exception as e:
                logger.warning(f"Error fetching options chain for {base_coin}: {e}")

        if not options:
            try:
                import requests
                domain = "api-testnet.bybit.com" if self.testnet else "api.bybit.com"
                url = f"https://{domain}/v5/market/tickers?category=option&baseCoin={base_coin}"
                r = requests.get(url, timeout=5).json()
                if r.get("retCode") == 0:
                    options = r.get("result", {}).get("list", [])
            except Exception:
                pass
        return options

    # ── Spot / Linear Accumulator Management ──────────────────────────────────

    def place_spot_maker_order(self, symbol: str, side: str, qty: float, price: float) -> Optional[str]:
        """Place a Post-Only maker limit order on spot (or 1x Linear Perp Accumulator if UTA restricts spot)."""
        order_id = f"sim_spot_{int(time.time()*1000)}_{self._order_counter}"
        self._order_counter += 1

        if self.dry_run or not self.session:
            logger.info(f"[SIMULATED SPOT] {side.upper()} {qty:.5f} {symbol} @ ${price:,.2f} (Post-Only Maker)")
            self._simulated_orders[order_id] = {
                "category": "spot", "symbol": symbol, "side": side,
                "qty": qty, "price": price, "status": "New", "time": time.time(),
            }
            return order_id

        # If not yet using linear fallback, attempt standard spot order first
        if not self.use_linear_spot_fallback:
            try:
                res = self.session.place_order(
                    category="spot",
                    symbol=symbol,
                    side=side.capitalize(),
                    orderType="Limit",
                    qty=str(round(qty, 5)),
                    price=str(round(price, 2)),
                    timeInForce="PostOnly",
                    orderLinkId=order_id,
                )
                if res.get("retCode") == 0:
                    real_id = res["result"].get("orderId", order_id)
                    self._order_categories[real_id] = "spot"
                    logger.info(f"[LIVE SPOT MAKER] Order placed: {real_id} | {side} {qty:.5f} @ ${price:,.2f}")
                    return real_id
                elif res.get("retCode") == 10029:
                    logger.warning("[BYBIT UTA] Spot category is not whitelisted (10029). Automatically falling back to UTA Linear Perp 1x Accumulator.")
                    self.use_linear_spot_fallback = True
                else:
                    logger.error(f"Bybit Spot Order Error: {res.get('retMsg')} (code {res.get('retCode')})")
                    return None
            except Exception as e:
                err_str = str(e)
                if "10029" in err_str or "whitelisted" in err_str.lower():
                    logger.warning("[BYBIT UTA] Spot category restricted (10029). Switching to UTA Linear Perp 1x Accumulator fallback.")
                    self.use_linear_spot_fallback = True
                else:
                    logger.error(f"Exception placing spot maker order: {e}")
                    return None

        # Fallback: In Bybit UTA, a 1x Long Linear Perpetual is economically identical to Spot
        if self.use_linear_spot_fallback:
            try:
                res = self.session.place_order(
                    category="linear",
                    symbol=symbol,
                    side=side.capitalize(),
                    orderType="Limit",
                    qty=str(round(qty, 3)),
                    price=str(round(price, 2)),
                    timeInForce="PostOnly",
                    positionIdx=1 if side.capitalize() == "Buy" else 2,
                    orderLinkId=order_id,
                )
                if res.get("retCode") == 0:
                    real_id = res["result"].get("orderId", order_id)
                    self._order_categories[real_id] = "linear"
                    logger.info(f"[LIVE UTA LINEAR ACCUMULATOR] Limit order placed: {real_id} | {side} {qty:.3f} @ ${price:,.2f}")
                    return real_id
                else:
                    logger.error(f"Bybit Linear Accumulator Error: {res.get('retMsg')} (code {res.get('retCode')})")
            except Exception as e:
                logger.error(f"Exception placing linear accumulator order: {e}")
        return None

    def cancel_spot_order(self, symbol: str, order_id: str) -> bool:
        """Cancel a resting spot or linear accumulator order."""
        if self.dry_run or not self.session:
            if order_id in self._simulated_orders:
                self._simulated_orders[order_id]["status"] = "Cancelled"
                logger.info(f"[SIMULATED SPOT] Cancelled order {order_id}")
                return True
            return False

        cat = self._order_categories.get(order_id, "linear" if self.use_linear_spot_fallback else "spot")
        try:
            res = self.session.cancel_order(category=cat, symbol=symbol, orderId=order_id)
            if res.get("retCode") == 0:
                logger.info(f"[LIVE {cat.upper()}] Cancelled order {order_id}")
                return True
            # Try alternate category if not found
            alt_cat = "linear" if cat == "spot" else "spot"
            res2 = self.session.cancel_order(category=alt_cat, symbol=symbol, orderId=order_id)
            return res2.get("retCode") == 0
        except Exception as e:
            logger.error(f"Exception cancelling order {order_id}: {e}")
            return False

    # ── Linear Perp (Hedge) Management ───────────────────────────────────────

    def place_perp_short_hedge(self, symbol: str, qty: float) -> Optional[str]:
        """Open a 1x Short on Linear Perpetual in Hedge Mode (positionIdx=2)."""
        order_id = f"sim_perp_{int(time.time()*1000)}_{self._order_counter}"
        self._order_counter += 1

        if self.dry_run or not self.session:
            logger.info(f"[SIMULATED PERP HEDGE] SELL {qty:.5f} {symbol} (Market Short, positionIdx=2)")
            self._simulated_orders[order_id] = {
                "category": "linear", "symbol": symbol, "side": "Sell",
                "qty": qty, "price": self.get_perp_price(symbol), "status": "Filled", "time": time.time(),
            }
            return order_id

        try:
            res = self.session.place_order(
                category="linear",
                symbol=symbol,
                side="Sell",
                orderType="Market",
                qty=str(round(qty, 3)),
                positionIdx=2,   # BothSides Hedge Mode Short
                orderLinkId=order_id,
            )
            if res.get("retCode") == 0:
                real_id = res["result"].get("orderId", order_id)
                logger.info(f"[LIVE PERP HEDGE] Opened Short Hedge {real_id}: {qty:.3f} {symbol}")
                return real_id
            else:
                logger.error(f"Bybit Perp Hedge Error: {res.get('retMsg')} (code {res.get('retCode')})")
        except Exception as e:
            logger.error(f"Exception placing perp hedge: {e}")
        return None

    def close_perp_short_hedge(self, symbol: str, qty: float) -> bool:
        """Close the Short Hedge position by buying back at market."""
        if self.dry_run or not self.session:
            logger.info(f"[SIMULATED PERP HEDGE] Closed Short position of {qty:.5f} {symbol}")
            return True

        try:
            res = self.session.place_order(
                category="linear",
                symbol=symbol,
                side="Buy",
                orderType="Market",
                qty=str(round(qty, 3)),
                positionIdx=2,   # Closes positionIdx=2
                reduceOnly=True,
            )
            return res.get("retCode") == 0
        except Exception as e:
            logger.error(f"Exception closing perp hedge: {e}")
            return False

    # ── Options Management ───────────────────────────────────────────────────

    def sell_put_option(self, symbol: str, qty: float, price: float) -> Optional[str]:
        """Sell a cash-secured put option on Bybit Options with tick size formatting."""
        order_id = f"sim_opt_{int(time.time()*1000)}_{self._order_counter}"
        self._order_counter += 1

        # Determine tick size: BTC options on Bybit have tickSize = 5.0, ETH has 0.5
        tick_size = 5.0 if "BTC" in symbol else (0.5 if "ETH" in symbol else 1.0)
        formatted_price = max(tick_size, round(price / tick_size) * tick_size)
        formatted_qty = max(0.01, round(qty, 2))

        if self.dry_run or not self.session:
            logger.info(f"[SIMULATED OPTION] SELL PUT {symbol} | Qty: {formatted_qty:.2f} | Premium: ${formatted_price:,.2f}")
            self._simulated_orders[order_id] = {
                "category": "option", "symbol": symbol, "side": "Sell",
                "qty": formatted_qty, "price": formatted_price, "status": "Filled", "time": time.time(),
            }
            return order_id

        try:
            px_str = str(int(formatted_price)) if formatted_price.is_integer() else str(formatted_price)
            res = self.session.place_order(
                category="option",
                symbol=symbol,
                side="Sell",
                orderType="Limit",
                qty=str(formatted_qty),
                price=px_str,
                orderLinkId=order_id,
            )
            if res.get("retCode") == 0:
                real_id = res["result"].get("orderId", order_id)
                logger.info(f"[LIVE OPTION] Sold Put {real_id} | {symbol} @ ${formatted_price:,.2f}")
                return real_id
            else:
                logger.warning(f"Bybit Option Order: {res.get('retMsg')} (code {res.get('retCode')})")
        except Exception as e:
            logger.error(f"Exception selling put option: {e}")
        return None
