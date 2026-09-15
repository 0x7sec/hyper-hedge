"""
client.py — Bybit V5 Unified Trading Account (UTA) client for Options and Linear Perps.
Supports live execution and dry-run simulation with real-time public market tickers.
"""

import time
import logging
from typing import Dict, Any, List, Optional

try:
    from pybit.unified_trading import HTTP
except ImportError:
    HTTP = None

from options_harvester.config import (
    BYBIT_API_KEY,
    BYBIT_API_SECRET,
    TESTNET,
    BASE_PERP_SYMBOL,
)

logger = logging.getLogger("options_harvester.client")


class BybitOptionsClient:
    """Unified client for Bybit V5 Options and Linear Perpetuals."""

    def __init__(
        self,
        api_key: str = BYBIT_API_KEY,
        api_secret: str = BYBIT_API_SECRET,
        testnet: bool = TESTNET,
        dry_run: bool = False,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.dry_run = dry_run

        if not self.dry_run and HTTP and self.api_key and self.api_secret:
            self.session = HTTP(
                testnet=self.testnet,
                api_key=self.api_key,
                api_secret=self.api_secret,
            )
        else:
            self.session = None

        # Simulated order & position tracker for dry-run
        self._simulated_orders: Dict[str, Dict[str, Any]] = {}
        self._order_counter = 1000
        self.simulated_spot_price: Optional[float] = None
        self._cached_tickers: List[Dict[str, Any]] = []
        self._last_ticker_fetch = 0.0

    # ── Market Data ──────────────────────────────────────────────────────────

    def get_perp_price(self, symbol: str = BASE_PERP_SYMBOL) -> float:
        """Fetch current mark price for the linear perpetual."""
        if self.simulated_spot_price is not None:
            return self.simulated_spot_price

        if self.session:
            try:
                res = self.session.get_tickers(category="linear", symbol=symbol)
                if res.get("retCode") == 0 and res.get("result", {}).get("list"):
                    item = res["result"]["list"][0]
                    return float(item.get("markPrice") or item.get("lastPrice") or 0.0)
            except Exception as e:
                logger.error(f"Error fetching perp price for {symbol}: {e}")

        # Public fallback
        try:
            import requests
            domain = "api-testnet.bybit.com" if self.testnet else "api.bybit.com"
            url = f"https://{domain}/v5/market/tickers?category=linear&symbol={symbol}"
            r = requests.get(url, timeout=5).json()
            if r.get("retCode") == 0 and r.get("result", {}).get("list"):
                item = r["result"]["list"][0]
                return float(item.get("markPrice") or item.get("lastPrice") or 0.0)
        except Exception:
            pass

        return 75500.0

    def get_options_chain(self, base_coin: str = "BTC", force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Fetch active European options for the base coin with full Greeks."""
        now = time.time()
        if not force_refresh and self._cached_tickers and (now - self._last_ticker_fetch < 1.0):
            return self._cached_tickers

        options: List[Dict[str, Any]] = []
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
                r = requests.get(url, timeout=6).json()
                if r.get("retCode") == 0:
                    options = r.get("result", {}).get("list", [])
            except Exception as e:
                logger.warning(f"Public fallback options chain fetch failed: {e}")

        if options:
            self._cached_tickers = options
            self._last_ticker_fetch = now

        return options

    def get_option_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch current ticker, mark price, and Greeks for a specific option contract."""
        chain = self.get_options_chain(base_coin="BTC")
        for item in chain:
            if item.get("symbol") == symbol:
                return item
        return None

    # ── Options Order Execution ──────────────────────────────────────────────

    def sell_option_leg(
        self,
        symbol: str,
        qty: float,
        price: float,
        tag: str = "strangle",
    ) -> Optional[str]:
        """
        Sell an option leg (Short Put or Short Call) with tick size formatting.
        Uses Post-Only Limit order to capture maker rebate / avoid taker fees.
        """
        order_id = f"opt_{tag}_{int(time.time()*1000)}_{self._order_counter}"
        self._order_counter += 1

        # BTC options tickSize = 5.0, minOrderQty = 0.01, qtyStep = 0.01
        tick_size = 5.0 if "BTC" in symbol else 0.5
        formatted_price = max(tick_size, round(price / tick_size) * tick_size)
        formatted_qty = max(0.01, round(qty, 2))

        if self.dry_run or not self.session:
            logger.info(
                f"[SIMULATED OPTION] SELL {symbol} | Qty: {formatted_qty:.2f} | Premium: ${formatted_price:,.2f}"
            )
            self._simulated_orders[order_id] = {
                "category": "option",
                "symbol": symbol,
                "side": "Sell",
                "qty": formatted_qty,
                "price": formatted_price,
                "status": "Filled",
                "time": time.time(),
                "orderLinkId": order_id,
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
                timeInForce="PostOnly",
                orderLinkId=order_id,
            )
            if res.get("retCode") == 0:
                real_id = res["result"].get("orderId", order_id)
                logger.info(f"[LIVE OPTION] Sold {symbol} (PostOnly) | ID: {real_id} | {formatted_qty:.2f} @ ${formatted_price:,.2f}")
                return real_id
            elif res.get("retCode") in (110007, 10001, 110006):
                logger.info(f"[LIVE OPTION] PostOnly rejected ({res.get('retMsg')}), retrying with standard GTC Limit...")
                order_id_gtc = f"{order_id}_gtc"
                res_gtc = self.session.place_order(
                    category="option",
                    symbol=symbol,
                    side="Sell",
                    orderType="Limit",
                    qty=str(formatted_qty),
                    price=px_str,
                    timeInForce="GTC",
                    orderLinkId=order_id_gtc,
                )
                if res_gtc.get("retCode") == 0:
                    real_id = res_gtc["result"].get("orderId", order_id_gtc)
                    logger.info(f"[LIVE OPTION] Sold {symbol} (GTC Limit) | ID: {real_id} | {formatted_qty:.2f} @ ${formatted_price:,.2f}")
                    return real_id
                else:
                    logger.warning(f"Bybit Option GTC Limit error: {res_gtc.get('retMsg')} (code {res_gtc.get('retCode')})")
            else:
                logger.warning(f"Bybit Option Order error: {res.get('retMsg')} (code {res.get('retCode')})")
        except Exception as e:
            logger.error(f"Exception selling option leg {symbol}: {e}")
        return None

    def close_option_leg(
        self,
        symbol: str,
        qty: float,
        price: Optional[float] = None,
        reason: str = "PROFIT_TAKE",
    ) -> bool:
        """
        Close an open short option leg by buying it back.
        Uses Marketable Limit or Market order for rapid closure.
        """
        order_id = f"opt_close_{int(time.time()*1000)}_{self._order_counter}"
        self._order_counter += 1
        formatted_qty = max(0.01, round(qty, 2))

        if self.dry_run or not self.session:
            logger.info(
                f"[SIMULATED OPTION CLOSE] BUY BACK {symbol} | Qty: {formatted_qty:.2f} | Reason: {reason}"
            )
            return True

        try:
            params: Dict[str, Any] = {
                "category": "option",
                "symbol": symbol,
                "side": "Buy",
                "qty": str(formatted_qty),
                "orderLinkId": order_id,
            }
            if price is not None and price > 0:
                tick_size = 5.0 if "BTC" in symbol else 0.5
                formatted_price = max(tick_size, round(price / tick_size) * tick_size)
                px_str = str(int(formatted_price)) if formatted_price.is_integer() else str(formatted_price)
                params["orderType"] = "Limit"
                params["price"] = px_str
            else:
                params["orderType"] = "Market"

            res = self.session.place_order(**params)
            if res.get("retCode") == 0:
                logger.info(f"[LIVE OPTION CLOSE] Closed {symbol} | Reason: {reason}")
                return True
            else:
                logger.error(f"Error closing option leg {symbol}: {res.get('retMsg')} (code {res.get('retCode')})")
        except Exception as e:
            logger.error(f"Exception closing option leg {symbol}: {e}")
        return False

    # ── DDH Linear Perpetual Management ─────────────────────────────────────

    def place_ddh_perp(
        self,
        symbol: str,
        side: str,
        qty: float,
    ) -> Optional[str]:
        """
        Place micro Dynamic Delta Hedge order on linear perpetual.
        In Bybit UTA Hedge Mode (BothSides), Buy uses positionIdx=1, Sell uses positionIdx=2.
        """
        order_id = f"opt_ddh_{int(time.time()*1000)}_{self._order_counter}"
        self._order_counter += 1
        formatted_qty = max(0.001, round(qty, 3))
        pos_idx = 1 if side.capitalize() == "Buy" else 2

        if self.dry_run or not self.session:
            logger.info(
                f"[SIMULATED DDH] {side.upper()} {formatted_qty:.3f} {symbol} (positionIdx={pos_idx})"
            )
            self._simulated_orders[order_id] = {
                "category": "linear",
                "symbol": symbol,
                "side": side.capitalize(),
                "qty": formatted_qty,
                "status": "Filled",
                "time": time.time(),
                "orderLinkId": order_id,
            }
            return order_id

        try:
            res = self.session.place_order(
                category="linear",
                symbol=symbol,
                side=side.capitalize(),
                orderType="Market",
                qty=str(formatted_qty),
                positionIdx=pos_idx,
                orderLinkId=order_id,
            )
            if res.get("retCode") == 0:
                real_id = res["result"].get("orderId", order_id)
                logger.info(f"[LIVE DDH PERP] {side} {formatted_qty:.3f} {symbol} | ID: {real_id}")
                return real_id
            else:
                logger.warning(f"Bybit DDH order error: {res.get('retMsg')} (code {res.get('retCode')})")
        except Exception as e:
            logger.error(f"Exception placing DDH perp order: {e}")
        return None

    def close_ddh_perp(
        self,
        symbol: str,
        side: str,
        qty: float,
    ) -> bool:
        """Close linear perpetual DDH position slice."""
        formatted_qty = max(0.001, round(qty, 3))
        # To close positionIdx=1 (Long), side="Sell". To close positionIdx=2 (Short), side="Buy".
        pos_idx = 2 if side.capitalize() == "Buy" else 1

        if self.dry_run or not self.session:
            logger.info(f"[SIMULATED DDH CLOSE] {side.upper()} {formatted_qty:.3f} {symbol}")
            return True

        try:
            res = self.session.place_order(
                category="linear",
                symbol=symbol,
                side=side.capitalize(),
                orderType="Market",
                qty=str(formatted_qty),
                positionIdx=pos_idx,
                reduceOnly=True,
            )
            return res.get("retCode") == 0
        except Exception as e:
            logger.error(f"Exception closing DDH perp: {e}")
            return False

    # ── Account & Margin Audit ───────────────────────────────────────────────

    def get_wallet_balance(self) -> Dict[str, float]:
        """Fetch UTA wallet balance and margin health metrics."""
        default_metrics = {
            "total_equity": 1000.0,
            "total_margin_balance": 1000.0,
            "total_available_balance": 1000.0,
            "im_rate": 0.0,
            "mm_rate": 0.0,
        }
        if self.dry_run or not self.session:
            return default_metrics

        try:
            res = self.session.get_wallet_balance(accountType="UNIFIED")
            if res.get("retCode") == 0 and res.get("result", {}).get("list"):
                acc = res["result"]["list"][0]
                return {
                    "total_equity": float(acc.get("totalEquity") or 1000.0),
                    "total_margin_balance": float(acc.get("totalMarginBalance") or 1000.0),
                    "total_available_balance": float(acc.get("totalAvailableBalance") or 1000.0),
                    "im_rate": float(acc.get("accountIMRate") or 0.0),
                    "mm_rate": float(acc.get("accountMMRate") or 0.0),
                }
        except Exception as e:
            logger.error(f"Error querying wallet balance: {e}")

        return default_metrics
