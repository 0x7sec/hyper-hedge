#!/usr/bin/env python3
"""
Bybit V5 REST Client & Order Management Service for AMD + FVG Bot.
Isolated from the single-leg trend hedge engine.
"""

import logging
import time
from datetime import datetime
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Dict, Any, List, Optional
from pybit.unified_trading import HTTP

from amd_bot.config import API_KEY, API_SECRET, TESTNET, AMD_PROFILES, AMDPairProfile

logger = logging.getLogger("AMDClient")


class AMDBitService:
    def __init__(self, is_dry_run: bool = False, testnet: bool = TESTNET):
        self.is_dry_run = is_dry_run
        self.testnet = testnet

        if not self.is_dry_run and (not API_KEY or not API_SECRET):
            logger.warning("No API credentials provided in .env -- running in DRY-RUN mode.")
            self.is_dry_run = True

        self.session = HTTP(
            testnet=self.testnet,
            api_key=API_KEY if not self.is_dry_run else None,
            api_secret=API_SECRET if not self.is_dry_run else None,
        )

        # Per-symbol instrument specs: {symbol: {price_scale, tick_size, min_qty, qty_step}}
        self.instruments: Dict[str, Dict[str, Any]] = {}

    def init_market_and_account(self, symbols: List[str]) -> None:
        """Fetch instrument info, set Hedge Mode (BothSides), and configure leverage."""
        net = "Testnet" if self.testnet else "Mainnet"
        logger.info(f"Connecting to Bybit {net} for {', '.join(symbols)}...")

        for sym in symbols:
            # 1. Instrument specifications
            try:
                res = self.session.get_instruments_info(category="linear", symbol=sym)
                if res.get("retCode") != 0:
                    raise RuntimeError(f"Instrument info failed for {sym}: {res.get('retMsg')}")

                instruments = res["result"]["list"]
                if not instruments:
                    raise RuntimeError(f"{sym} not found on Bybit linear markets.")

                info = instruments[0]
                price_scale = int(info.get("priceScale", 2))
                tick_size = Decimal(str(info["priceFilter"].get("tickSize", "0.01")))
                min_qty = Decimal(str(info["lotSizeFilter"].get("minOrderQty", "0.001")))
                qty_step = Decimal(str(info["lotSizeFilter"].get("qtyStep", "0.001")))

                self.instruments[sym] = {
                    "price_scale": price_scale,
                    "tick_size": tick_size,
                    "min_qty": min_qty,
                    "qty_step": qty_step,
                }

                logger.info(
                    f"[{sym}] TickSize={tick_size} | MinQty={min_qty} | QtyStep={qty_step} | Decimals={price_scale}"
                )
            except Exception as e:
                logger.error(f"Failed to fetch instrument info for {sym}: {e}")
                # Fallback to profile defaults
                prof = AMD_PROFILES.get(sym)
                self.instruments[sym] = {
                    "price_scale": prof.price_precision if prof else 2,
                    "tick_size": Decimal(f"1e-{prof.price_precision}") if prof else Decimal("0.01"),
                    "min_qty": Decimal(str(prof.min_order_qty)) if prof else Decimal("0.001"),
                    "qty_step": Decimal(f"1e-{prof.qty_precision}") if prof else Decimal("0.001"),
                }

            if self.is_dry_run:
                continue

            # 2. BothSides / Hedge Mode (mode=3)
            try:
                res = self.session.switch_position_mode(category="linear", symbol=sym, mode=3)
                if res.get("retCode") == 0:
                    logger.info(f"[{sym}] Position mode set to BothSides (Hedge Mode).")
            except Exception as e:
                msg = str(getattr(e, "message", e))
                if "110025" in str(e) or "not modified" in msg.lower():
                    logger.info(f"[{sym}] Already in BothSides (Hedge Mode).")
                else:
                    logger.warning(f"[{sym}] switch_position_mode warning: {msg}")

            # 3. Configure Leverage
            prof = AMD_PROFILES.get(sym)
            lev = str(prof.leverage if prof else 4)
            try:
                res = self.session.set_leverage(category="linear", symbol=sym, buyLeverage=lev, sellLeverage=lev)
                if res.get("retCode") == 0:
                    logger.info(f"[{sym}] Leverage set to {lev}x.")
            except Exception as e:
                msg = str(getattr(e, "message", e))
                if "110043" in str(e) or "not modified" in msg.lower():
                    logger.info(f"[{sym}] Leverage already {lev}x.")
                else:
                    logger.warning(f"[{sym}] set_leverage warning: {msg}")

        if self.is_dry_run:
            logger.info("DRY-RUN: Simulation mode enabled (no live orders will be placed).")

    # -- Precision Rounding Helpers --------------------------------------------

    def get_spec(self, symbol: str) -> Dict[str, Any]:
        return self.instruments.get(symbol, {
            "price_scale": 2,
            "tick_size": Decimal("0.01"),
            "min_qty": Decimal("0.001"),
            "qty_step": Decimal("0.001"),
        })

    def round_price(self, price: float, symbol: str) -> str:
        spec = self.get_spec(symbol)
        d_price = Decimal(str(price))
        tick = spec["tick_size"]
        scale = spec["price_scale"]
        rounded = (d_price / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick
        return f"{rounded:.{scale}f}"

    def round_qty(self, qty: float, symbol: str) -> str:
        spec = self.get_spec(symbol)
        d_qty = Decimal(str(qty))
        step = spec["qty_step"]
        min_q = spec["min_qty"]
        if d_qty < min_q:
            d_qty = min_q
        rounded = (d_qty / step).quantize(Decimal("1"), rounding=ROUND_DOWN) * step
        # Format string according to step decimals
        step_str = str(step)
        decimals = len(step_str.split(".")[1]) if "." in step_str else 0
        return f"{rounded:.{decimals}f}"

    # -- Order Placement -------------------------------------------------------

    def place_fvg_limit_order(self, symbol: str, side: str, qty: float, price: float, client_order_id: str) -> Dict[str, Any]:
        """Submit a Post-Only Maker Limit Order at the FVG retest level."""
        pos_idx = 1 if side.lower() == "buy" else 2
        px_str = self.round_price(price, symbol)
        qty_str = self.round_qty(qty, symbol)

        if self.is_dry_run:
            logger.info(f"[SIM ORDER] {side} {qty_str} {symbol} @ {px_str} (PostOnly Limit, posIdx={pos_idx})")
            return {
                "orderId": f"sim_{int(time.time()*1000)}",
                "orderLinkId": client_order_id,
                "symbol": symbol,
                "side": side,
                "price": float(px_str),
                "qty": float(qty_str),
                "status": "New",
            }

        try:
            res = self.session.place_order(
                category="linear",
                symbol=symbol,
                side=side,
                orderType="Limit",
                price=px_str,
                qty=qty_str,
                positionIdx=pos_idx,
                timeInForce="PostOnly",
                orderLinkId=client_order_id,
            )
            if res.get("retCode") == 0:
                ord_id = res["result"]["orderId"]
                logger.info(f"[ORDER PLACED] {side} {qty_str} {symbol} @ {px_str} | ID: {ord_id}")
                return res["result"]
            else:
                logger.error(f"[{symbol}] place_order failed: {res.get('retMsg')} (code={res.get('retCode')})")
                return {}
        except Exception as e:
            logger.error(f"[{symbol}] Exception placing limit order: {e}")
            return {}

    def set_structural_stop_loss(self, symbol: str, side: str, sl_price: float) -> bool:
        """Set or update exchange-side structural Stop Loss."""
        pos_idx = 1 if side.lower() == "buy" else 2
        sl_str = self.round_price(sl_price, symbol)

        if self.is_dry_run:
            logger.info(f"[SIM SL] {symbol} posIdx={pos_idx} SL set to {sl_str}")
            return True

        try:
            res = self.session.set_trading_stop(
                category="linear",
                symbol=symbol,
                stopLoss=sl_str,
                positionIdx=pos_idx,
                slTriggerBy="MarkPrice",
            )
            if res.get("retCode") == 0:
                logger.info(f"[{symbol}] Exchange SL updated to {sl_str} (posIdx={pos_idx})")
                return True
            else:
                logger.warning(f"[{symbol}] set_trading_stop SL failed: {res.get('retMsg')}")
                return False
        except Exception as e:
            logger.error(f"[{symbol}] Exception setting SL: {e}")
            return False

    def place_take_profit_order(self, symbol: str, side: str, qty: float, tp_price: float, client_order_id: str) -> Dict[str, Any]:
        """Place Post-Only Limit Take-Profit order."""
        # For closing Long, side is Sell with positionIdx=1. For closing Short, side is Buy with positionIdx=2.
        pos_idx = 1 if side.lower() == "sell" else 2
        tp_str = self.round_price(tp_price, symbol)
        qty_str = self.round_qty(qty, symbol)

        if self.is_dry_run:
            logger.info(f"[SIM TP] {side} {qty_str} {symbol} @ {tp_str} (ReduceOnly PostOnly TP)")
            return {"orderId": f"sim_tp_{int(time.time()*1000)}", "status": "New"}

        try:
            res = self.session.place_order(
                category="linear",
                symbol=symbol,
                side=side,
                orderType="Limit",
                price=tp_str,
                qty=qty_str,
                positionIdx=pos_idx,
                timeInForce="PostOnly",
                reduceOnly=True,
                orderLinkId=client_order_id,
            )
            if res.get("retCode") == 0:
                logger.info(f"[{symbol}] Limit TP placed @ {tp_str} (ID: {res['result']['orderId']})")
                return res["result"]
            else:
                logger.warning(f"[{symbol}] place TP failed: {res.get('retMsg')}")
                return {}
        except Exception as e:
            logger.error(f"[{symbol}] Exception placing TP: {e}")
            return {}

    def cancel_order(self, symbol: str, order_id: Optional[str] = None, order_link_id: Optional[str] = None) -> bool:
        """Cancel an open order."""
        if self.is_dry_run:
            logger.info(f"[SIM CANCEL] {symbol} orderId={order_id} linkId={order_link_id}")
            return True

        try:
            res = self.session.cancel_order(
                category="linear",
                symbol=symbol,
                orderId=order_id,
                orderLinkId=order_link_id,
            )
            return res.get("retCode") == 0
        except Exception as e:
            logger.debug(f"[{symbol}] cancel_order: {e}")
            return False

    def close_position_market(self, symbol: str, side: str, qty: float) -> bool:
        """Emergency close position at market."""
        # Closing Long (posIdx=1) requires Sell order; closing Short (posIdx=2) requires Buy order
        close_side = "Sell" if side.lower() == "buy" else "Buy"
        pos_idx = 1 if side.lower() == "buy" else 2
        qty_str = self.round_qty(qty, symbol)

        if self.is_dry_run:
            logger.info(f"[SIM CLOSE] {close_side} {qty_str} {symbol} (Market Close)")
            return True

        try:
            res = self.session.place_order(
                category="linear",
                symbol=symbol,
                side=close_side,
                orderType="Market",
                qty=qty_str,
                positionIdx=pos_idx,
                reduceOnly=True,
            )
            return res.get("retCode") == 0
        except Exception as e:
            logger.error(f"[{symbol}] Emergency close failed: {e}")
            return False

    def get_order_status(self, symbol: str, order_id: Optional[str] = None, order_link_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Query order status from Bybit open orders or order history."""
        if self.is_dry_run:
            return None
        try:
            kwargs: Dict[str, Any] = {"category": "linear", "symbol": symbol}
            if order_id:
                kwargs["orderId"] = order_id
            if order_link_id:
                kwargs["orderLinkId"] = order_link_id
            res = self.session.get_open_orders(**kwargs)
            if res.get("retCode") == 0:
                orders = res.get("result", {}).get("list", [])
                if orders:
                    return orders[0]
            res_hist = self.session.get_order_history(**kwargs)
            if res_hist.get("retCode") == 0:
                hist_orders = res_hist.get("result", {}).get("list", [])
                if hist_orders:
                    return hist_orders[0]
        except Exception as e:
            logger.debug(f"[{symbol}] get_order_status error: {e}")
        return None

    def get_closed_pnl(self, symbol: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Fetch latest closed PnL records for reconciliation."""
        if self.is_dry_run:
            return []
        try:
            res = self.session.get_closed_pnl(category="linear", symbol=symbol, limit=limit)
            if res.get("retCode") == 0:
                return res.get("result", {}).get("list", [])
        except Exception as e:
            logger.debug(f"[{symbol}] get_closed_pnl error: {e}")
        return []

    def get_ticker(self, symbol: str) -> Optional[Dict[str, float]]:
        """Fetch latest ticker via REST API."""
        try:
            res = self.session.get_tickers(category="linear", symbol=symbol)
            if res.get("retCode") == 0:
                tickers = res.get("result", {}).get("list", [])
                if tickers:
                    t = tickers[0]
                    return {
                        "mark_price": float(t.get("markPrice", 0) or 0),
                        "last_price": float(t.get("lastPrice", 0) or 0),
                    }
        except Exception as e:
            logger.debug(f"[{symbol}] get_ticker REST error: {e}")
        return None

    def get_recent_closed_kline(self, symbol: str, interval: str = "15") -> Optional[Dict[str, Any]]:
        """Fetch latest confirmed closed kline via REST API."""
        try:
            res = self.session.get_kline(category="linear", symbol=symbol, interval=interval, limit=3)
            if res.get("retCode") == 0:
                raw_list = res.get("result", {}).get("list", [])
                if len(raw_list) >= 2:
                    # Index 0 is currently forming; index 1 is the latest confirmed closed candle
                    k = raw_list[1]
                    return {
                        "timestamp": int(k[0]),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                    }
        except Exception as e:
            logger.debug(f"[{symbol}] get_recent_closed_kline REST error: {e}")
        return None

    # -- Reconciliation & Account Data -----------------------------------------

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Fetch all currently open positions from Bybit."""
        if self.is_dry_run:
            return []
        try:
            res = self.session.get_positions(category="linear", settleCoin="USDT")
            if res.get("retCode") == 0:
                pos_list = res.get("result", {}).get("list", [])
                return [p for p in pos_list if float(p.get("size", 0) or 0) > 0]
            return []
        except Exception as e:
            logger.error(f"get_open_positions error: {e}")
            return []

    def get_wallet_balance(self) -> Dict[str, float]:
        """Fetch Unified Account equity and wallet balance."""
        if self.is_dry_run:
            return {"equity": 1000.0, "wallet_balance": 1000.0, "available_balance": 1000.0}
        try:
            res = self.session.get_wallet_balance(accountType="UNIFIED")
            if res.get("retCode") == 0:
                acc = res.get("result", {}).get("list", [{}])[0]
                return {
                    "equity": float(acc.get("totalEquity", 0) or 0),
                    "wallet_balance": float(acc.get("totalWalletBalance", 0) or 0),
                    "available_balance": float(acc.get("totalAvailableBalance", 0) or 0),
                }
        except Exception as e:
            logger.debug(f"get_wallet_balance: {e}")
        return {"equity": 0.0, "wallet_balance": 0.0, "available_balance": 0.0}
