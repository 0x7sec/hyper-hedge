import logging
import time
from datetime import datetime
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Dict, Any, List, Optional
from pybit.unified_trading import HTTP

from bybit_bot.config import Config

logger = logging.getLogger("BybitClient")


class BybitService:
    def __init__(self, config: Config):
        self.config = config
        self.is_dry_run = config.dry_run

        if not self.is_dry_run and (not config.api_key or not config.api_secret):
            logger.warning("No API credentials provided -- switching to DRY-RUN mode.")
            self.is_dry_run = True

        self.session = HTTP(
            testnet=config.testnet,
            api_key=config.api_key if not self.is_dry_run else None,
            api_secret=config.api_secret if not self.is_dry_run else None,
        )

        # Per-symbol instrument specs: {symbol: {price_scale, tick_size, min_qty, qty_step}}
        self.instruments: Dict[str, Dict[str, Any]] = {}

    # -- Market & account initialisation --------------------------------------

    def init_market_and_account(self, symbols: Optional[List[str]] = None) -> None:
        """Fetch instrument info, configure Dual-Side Hedge mode and leverage for all symbols."""
        target_symbols = symbols if symbols else [s.symbol for s in self.config.symbols]
        net = "Testnet" if self.config.testnet else "Mainnet"
        logger.info(f"Connecting to Bybit {net} for {', '.join(target_symbols)}...")

        for sym in target_symbols:
            # 1. Instrument details
            res = self.session.get_instruments_info(category="linear", symbol=sym)
            if res.get("retCode") != 0:
                raise RuntimeError(f"Instrument info failed for {sym}: {res.get('retMsg')}")

            instruments = res["result"]["list"]
            if not instruments:
                raise RuntimeError(f"{sym} not found on Bybit linear markets.")

            info = instruments[0]
            price_scale = int(info.get("priceScale", 2))
            tick_size   = Decimal(str(info["priceFilter"].get("tickSize",   "0.01")))
            min_qty     = Decimal(str(info["lotSizeFilter"].get("minOrderQty", "0.001")))
            qty_step    = Decimal(str(info["lotSizeFilter"].get("qtyStep",     "0.001")))

            self.instruments[sym] = {
                "price_scale": price_scale,
                "tick_size": tick_size,
                "min_qty": min_qty,
                "qty_step": qty_step,
            }

            logger.info(
                f"Market: {sym} | Tick={tick_size} | "
                f"MinQty={min_qty} | QtyStep={qty_step} | Decimals={price_scale}"
            )

            if self.is_dry_run:
                continue

            # 2. BothSides / Hedge mode (mode=3)
            try:
                res = self.session.switch_position_mode(
                    category="linear", symbol=sym, mode=3
                )
                if res.get("retCode") == 0:
                    logger.info(f"[{sym}] Position mode set to BothSides (Hedge Mode).")
                else:
                    logger.info(f"[{sym}] switch_position_mode: {res.get('retMsg')}")
            except Exception as e:
                msg = str(getattr(e, "message", e))
                if "110025" in str(e) or "not modified" in msg.lower():
                    logger.info(f"[{sym}] Already in BothSides (Hedge Mode).")
                else:
                    logger.warning(f"[{sym}] switch_position_mode warning: {msg}")

            # 3. Leverage
            try:
                lev = str(self.config.leverage)
                res = self.session.set_leverage(
                    category="linear", symbol=sym,
                    buyLeverage=lev, sellLeverage=lev,
                )
                if res.get("retCode") == 0:
                    logger.info(f"[{sym}] Leverage set to {lev}x.")
                else:
                    logger.info(f"[{sym}] set_leverage: {res.get('retMsg')}")
            except Exception as e:
                msg = str(getattr(e, "message", e))
                if "110043" in str(e) or "not modified" in msg.lower():
                    logger.info(f"[{sym}] Leverage already {self.config.leverage}x.")
                else:
                    logger.warning(f"[{sym}] set_leverage warning: {msg}")

        if self.is_dry_run:
            logger.info("DRY-RUN: skipping on-chain leverage / hedge-mode setup.")

    # -- Price helpers ---------------------------------------------------------

    def get_instrument_spec(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        sym = symbol if symbol else self.config.symbol
        if sym in self.instruments:
            return self.instruments[sym]
        return {
            "price_scale": 2,
            "tick_size": Decimal("0.01"),
            "min_qty": Decimal("0.001"),
            "qty_step": Decimal("0.001"),
        }

    def round_price(self, price: Decimal, symbol: Optional[str] = None) -> str:
        spec = self.get_instrument_spec(symbol)
        tick = spec["tick_size"]
        scale = spec["price_scale"]
        rounded = (price / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick
        return f"{rounded:.{scale}f}"

    def round_qty(self, qty: Decimal, symbol: Optional[str] = None) -> str:
        spec = self.get_instrument_spec(symbol)
        step = spec["qty_step"]
        min_q = spec["min_qty"]
        steps = (qty / step).quantize(Decimal("1"), rounding=ROUND_DOWN)
        rounded = max(steps * step, min_q)
        decimals = len(str(step).split(".")[1]) if "." in str(step) else 0
        return f"{rounded:.{decimals}f}"

    def get_market_price(self, symbol: Optional[str] = None) -> Decimal:
        sym = symbol if symbol else self.config.symbol
        res = self.session.get_tickers(category="linear", symbol=sym)
        if res.get("retCode") != 0 or not res["result"]["list"]:
            raise RuntimeError(f"Ticker fetch failed for {sym}: {res.get('retMsg')}")
        t = res["result"]["list"][0]
        return Decimal(str(t.get("lastPrice") or t.get("markPrice")))

    # -- Candle data -----------------------------------------------------------

    def get_recent_candles(self, symbol: Optional[str] = None, interval: str = "15", limit: int = 150) -> List[Dict[str, Any]]:
        """
        Fetch recent `limit` kline candles for `symbol`.
        Returns a list of dicts with Decimal prices ordered oldest-first (chronological).
        """
        sym = symbol if symbol else self.config.symbol
        res = self.session.get_kline(
            category="linear",
            symbol=sym,
            interval=interval,
            limit=limit,
        )
        if res.get("retCode") != 0:
            raise RuntimeError(f"Kline fetch failed for {sym}: {res.get('retMsg')}")

        klines = res["result"]["list"]
        klines.reverse()  # Oldest first

        parsed = []
        for k in klines:
            parsed.append({
                "timestamp": int(k[0]),
                "datetime":  datetime.fromtimestamp(int(k[0]) / 1000),
                "open":      Decimal(str(k[1])),
                "high":      Decimal(str(k[2])),
                "low":       Decimal(str(k[3])),
                "close":     Decimal(str(k[4])),
                "volume":    Decimal(str(k[5])),
            })
        return parsed

    # -- Position queries ------------------------------------------------------

    def get_open_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Return currently open positions on Bybit linear perps.
        If symbol is specified, filter by symbol; otherwise return all open positions.
        """
        if self.is_dry_run:
            return []
        params: Dict[str, Any] = {"category": "linear"}
        if symbol:
            params["symbol"] = symbol
        else:
            params["settleCoin"] = "USDT"

        res = self.session.get_positions(**params)
        if res.get("retCode") != 0:
            raise RuntimeError(f"get_positions failed: {res.get('retMsg')}")
        return [p for p in res["result"].get("list", []) if float(p.get("size", 0)) > 0]

    def get_position_size(self, position_idx: int, symbol: Optional[str] = None) -> Decimal:
        """Query remaining open size for a specific positionIdx on Bybit linear perps."""
        if self.is_dry_run:
            return Decimal("0")
        sym = symbol if symbol else self.config.symbol
        try:
            positions = self.get_open_positions(symbol=sym)
            for p in positions:
                if int(p.get("positionIdx", 0)) == position_idx:
                    return Decimal(str(p.get("size", "0")))
            return Decimal("0")
        except Exception as e:
            logger.warning(f"[{sym}] Error checking position size: {e}")
            return Decimal("0")

    def get_available_balance(self) -> Decimal:
        """Return available USDT wallet balance."""
        if self.is_dry_run:
            return Decimal("1000.00")
        try:
            res = self.session.get_wallet_balance(accountType="UNIFIED")
            if res.get("retCode") == 0:
                coins = res.get("result", {}).get("list", [{}])[0].get("coin", [])
                for c in coins:
                    if c.get("coin") == "USDT":
                        return Decimal(str(c.get("availableToWithdraw") or c.get("walletBalance", "0")))
            return Decimal("1000.00")
        except Exception:
            return Decimal("1000.00")

    def get_last_closed_pnl(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch the most recent closed PnL record from Bybit for this symbol."""
        if self.is_dry_run:
            return None
        try:
            res = self.session.get_closed_pnl(category="linear", symbol=symbol, limit=2)
            if res.get("retCode") == 0:
                p_list = res.get("result", {}).get("list", [])
                if p_list:
                    return p_list[0]
            return None
        except Exception as e:
            logger.debug(f"[{symbol}] get_closed_pnl error: {e}")
            return None

    # -- Order execution -------------------------------------------------------

    def place_market_open(self, side: str, qty: Decimal, position_idx: int, symbol: Optional[str] = None) -> Decimal:
        """
        Place market entry or upsize order with fill verification and marketable Limit fallback.
        Ensures partial fills and orderbook liquidity rejections (EC_NoImmediateQtyToFill)
        are retried and verified via exchange position size.
        """
        sym = symbol if symbol else self.config.symbol
        current_px = self.get_market_price(sym)
        qty_str    = self.round_qty(qty, sym)

        if self.is_dry_run:
            logger.info(
                f"[DRY-RUN] Market {side.upper()} {qty_str} {sym} (idx={position_idx}) @ ~{current_px}"
            )
            return current_px

        start_size = self.get_position_size(position_idx, sym)
        target_size = start_size + qty
        rem_qty = qty
        max_attempts = 3

        for attempt in range(1, max_attempts + 1):
            current_px = self.get_market_price(sym)
            qty_str = self.round_qty(rem_qty, sym)
            if Decimal(qty_str) <= Decimal("0"):
                break

            if attempt == 1:
                logger.info(f"[{sym}] Opening/Upsizing positionIdx={position_idx} (Attempt {attempt}/{max_attempts}): Market {side.upper()} {qty_str}...")
                try:
                    res = self.session.place_order(
                        category="linear",
                        symbol=sym,
                        side=side,
                        orderType="Market",
                        qty=qty_str,
                        positionIdx=position_idx,
                    )
                    if res.get("retCode") != 0:
                        logger.warning(f"[{sym}] Market order error: {res.get('retMsg')} (code {res.get('retCode')})")
                    else:
                        logger.info(f"[{sym}] Market order placed. ID: {res['result'].get('orderId')}")
                except Exception as e:
                    if "110123" in str(e):
                        logger.error(
                            f"\n{'='*70}\n"
                            f"[BYBIT CONTRACT TERMS REQUIRED (Error 110123)]\n"
                            f"Visit: https://{'testnet.' if self.config.testnet else ''}bybit.com/trade/usdt/{sym}\n"
                            f"Accept the one-time trading terms popup, then restart.\n"
                            f"{'='*70}"
                        )
                    logger.warning(f"[{sym}] Market order exception: {e}")
            else:
                # Marketable Limit order crossing spread by 0.5% with GTC
                slip = Decimal("1.005") if side.lower() == "buy" else Decimal("0.995")
                limit_px = self.round_price(current_px * slip, sym)
                logger.info(
                    f"[{sym}] Upsize retry positionIdx={position_idx} (Attempt {attempt}/{max_attempts}): "
                    f"Marketable Limit GTC {side.upper()} {qty_str} @ {limit_px}..."
                )
                try:
                    res = self.session.place_order(
                        category="linear",
                        symbol=sym,
                        side=side,
                        orderType="Limit",
                        price=limit_px,
                        qty=qty_str,
                        timeInForce="GTC",
                        positionIdx=position_idx,
                    )
                    if res.get("retCode") != 0:
                        logger.warning(f"[{sym}] Limit order error: {res.get('retMsg')} (code {res.get('retCode')})")
                    else:
                        logger.info(f"[{sym}] Marketable limit placed. ID: {res['result'].get('orderId')}")
                except Exception as e:
                    logger.warning(f"[{sym}] Limit order exception: {e}")

            time.sleep(0.5)
            actual_size = self.get_position_size(position_idx, sym)
            filled_so_far = actual_size - start_size
            if actual_size >= target_size or filled_so_far >= qty:
                logger.info(f"[{sym}] REST verification SUCCESS: positionIdx={position_idx} reached target size {actual_size}.")
                return current_px

            rem_qty = qty - filled_so_far
            logger.warning(f"[{sym}] Incomplete fill (filled {filled_so_far}/{qty}). Remaining {rem_qty}. Retrying...")

        final_size = self.get_position_size(position_idx, sym)
        logger.info(f"[{sym}] Final position size on exchange: {final_size}")
        return current_px

    def set_trading_stop(
        self,
        position_idx: int,
        stop_loss: Decimal,
        take_profit: Optional[Decimal] = None,
        symbol: Optional[str] = None,
    ) -> None:
        """Push updated SL/TP to Bybit resting triggers."""
        sym = symbol if symbol else self.config.symbol
        sl_str = self.round_price(stop_loss, sym)
        tp_str = self.round_price(take_profit, sym) if take_profit is not None else ""

        if self.is_dry_run:
            logger.info(f"[DRY-RUN] [{sym}] set_trading_stop idx={position_idx}: SL={sl_str} TP={tp_str}")
            return

        params: Dict[str, Any] = {
            "category":    "linear",
            "symbol":      sym,
            "positionIdx": position_idx,
            "stopLoss":    sl_str,
            "slTriggerBy": "LastPrice",
        }
        if tp_str:
            params["takeProfit"] = tp_str
            params["tpTriggerBy"] = "LastPrice"

        try:
            res = self.session.set_trading_stop(**params)
            if res.get("retCode") == 0:
                logger.info(f"[{sym}] Exchange stop updated idx={position_idx}: SL={sl_str}")
            else:
                logger.warning(f"[{sym}] set_trading_stop warning: {res.get('retMsg')} (code {res.get('retCode')})")
        except Exception as e:
            logger.warning(f"[{sym}] Failed to update exchange stop: {e}")

    def close_position(self, position_idx: int, qty: Decimal, symbol: Optional[str] = None) -> Decimal:
        """
        Close position immediately via market reduce-only order.
        Features:
        - Up to 3 retries
        - If market order fails or is rejected, uses aggressive marketable Limit order
          (GTC, reduceOnly=True) crossing spread by 0.5% (Sell @ -0.5%, Buy @ +0.5%)
        - Verifies remaining position size is 0 via REST
        """
        sym = symbol if symbol else self.config.symbol
        current_px = self.get_market_price(sym)
        close_side = "Sell" if position_idx == 1 else "Buy"

        if self.is_dry_run:
            qty_str = self.round_qty(qty, sym)
            logger.info(f"[DRY-RUN] CLOSE [{sym}] positionIdx={position_idx}: {close_side} {qty_str} @ ~{current_px}")
            return current_px

        rem_qty = qty
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            current_px = self.get_market_price(sym)
            qty_str = self.round_qty(rem_qty, sym)

            if attempt == 1:
                logger.info(
                    f"[{sym}] Closing positionIdx={position_idx} (Attempt {attempt}/{max_attempts}): "
                    f"Market {close_side} {qty_str}..."
                )
                try:
                    res = self.session.place_order(
                        category="linear",
                        symbol=sym,
                        side=close_side,
                        orderType="Market",
                        qty=qty_str,
                        reduceOnly=True,
                        positionIdx=position_idx,
                    )
                    if res.get("retCode") != 0:
                        logger.warning(f"[{sym}] Market close order error: {res.get('retMsg')} (code {res.get('retCode')})")
                    else:
                        logger.info(f"[{sym}] Market close order placed. ID: {res['result'].get('orderId')}")
                except Exception as e:
                    logger.warning(f"[{sym}] Market close exception: {e}")
            else:
                # Aggressive marketable Limit order crossing spread by 0.5% with GTC and reduceOnly
                # For Sell: 0.5% lower than current price so it immediately sweeps bids
                # For Buy: 0.5% higher than current price so it immediately sweeps asks
                slip = Decimal("0.995") if close_side == "Sell" else Decimal("1.005")
                limit_px = self.round_price(current_px * slip, sym)
                logger.info(
                    f"[{sym}] Closing positionIdx={position_idx} (Attempt {attempt}/{max_attempts}): "
                    f"Marketable Limit GTC {close_side} {qty_str} @ {limit_px} (crossing spread 0.5%)..."
                )
                try:
                    res = self.session.place_order(
                        category="linear",
                        symbol=sym,
                        side=close_side,
                        orderType="Limit",
                        price=limit_px,
                        qty=qty_str,
                        timeInForce="GTC",
                        reduceOnly=True,
                        positionIdx=position_idx,
                    )
                    if res.get("retCode") != 0:
                        logger.warning(f"[{sym}] Limit close order error: {res.get('retMsg')} (code {res.get('retCode')})")
                    else:
                        logger.info(f"[{sym}] Marketable limit close placed. ID: {res['result'].get('orderId')}")
                except Exception as e:
                    logger.warning(f"[{sym}] Limit close exception: {e}")

            # Brief pause for matching engine settlement
            time.sleep(0.5)

            # Verify position size via REST
            actual_size = self.get_position_size(position_idx, sym)
            if actual_size == Decimal("0"):
                logger.info(f"[{sym}] REST verification SUCCESS: positionIdx={position_idx} is fully closed (size=0).")
                return current_px
            else:
                logger.warning(
                    f"[{sym}] Position positionIdx={position_idx} still open on exchange (size={actual_size}). "
                    f"Proceeding to next attempt..."
                )
                rem_qty = actual_size

        logger.error(f"[{sym}] CRITICAL: positionIdx={position_idx} could not be fully closed after {max_attempts} attempts!")
        return current_px
