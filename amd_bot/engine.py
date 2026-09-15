#!/usr/bin/env python3
"""
Real-Time Multi-Pair State Machine Engine for AMD + FVG Bot.
Subscribes to Bybit V5 Linear WebSocket (15m, 240m klines, live tickers).
Runs isolated state machine for BTCUSDT, DOGEUSDT, SOLUSDT, ETHUSDT.
Atomically caches runtime state to amd_bot_state.json and logs closed trades to amd_trades.csv.
"""

import os
import sys
import json
import csv
import time
import math
import logging
import threading
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
from pybit.unified_trading import WebSocket

from amd_bot.config import (
    STATE_FILE, TRADES_FILE, LOG_FILE, AMD_PROFILES, AMDPairProfile,
    ACTIVE_SYMBOLS, REST_URL, WS_LINEAR_URL, TESTNET, MAX_CONCURRENT_PAIRS,
    ACCOUNT_CAPITAL, MARGIN_PER_TRADE, DEFAULT_LEVERAGE
)
from amd_bot.client import AMDBitService

logger = logging.getLogger("AMDEngine")


# ==============================================================================
# INDICATOR HELPER FUNCTIONS
# ==============================================================================

def calc_ema(values: List[float], period: int) -> float:
    if len(values) < period:
        return float("nan")
    mult = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = (v - ema) * mult + ema
    return ema


def calc_atr(candles: List[Dict[str, float]], period: int = 14) -> float:
    n = len(candles)
    if n < period + 1:
        return float("nan")
    tr_list = []
    for i in range(1, n):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_list.append(tr)
    if len(tr_list) < period:
        return float("nan")
    atr = sum(tr_list[:period]) / period
    for tr in tr_list[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


# ==============================================================================
# PER-PAIR AMD STATE MACHINE
# ==============================================================================

class PairAMDState:
    def __init__(self, symbol: str, profile: AMDPairProfile):
        self.symbol = symbol
        self.profile = profile

        # Live Prices
        self.mark_price: float = 0.0
        self.last_price: float = 0.0
        self.last_tick_time: float = time.time()

        # Candle Data: List of dicts {open, high, low, close, volume, timestamp}
        self.candles_15m: List[Dict[str, float]] = []
        self.candles_4h: List[Dict[str, float]] = []

        # Indicators
        self.macro_200_ema: float = 0.0
        self.macro_bias: str = "NEUTRAL"  # "BULLISH", "BEARISH", "NEUTRAL"
        self.current_atr: float = 0.0
        self.bsl: float = 0.0             # Range High
        self.ssl: float = 0.0             # Range Low
        self.range_width_pct: float = 0.0

        # Strategy State Machine
        # Phases: "ACCUMULATING", "SWEEP_DETECTED", "FVG_PENDING", "IN_POSITION"
        self.phase: str = "ACCUMULATING"

        # Active Sweep Details
        self.sweep_dir: Optional[str] = None       # "BULLISH" or "BEARISH"
        self.sweep_extreme_px: float = 0.0
        self.sweep_time: float = 0.0
        self.sweep_candle_ts: int = 0
        self.sweep_bar_idx: int = 0

        # Active FVG & Pending Order
        self.fvg_top: float = 0.0
        self.fvg_bottom: float = 0.0
        self.fvg_ce: float = 0.0                  # Consequent Encroachment (50%)
        self.pending_order_id: Optional[str] = None
        self.pending_order_link_id: Optional[str] = None
        self.pending_side: Optional[str] = None
        self.pending_ep: float = 0.0
        self.pending_order_qty: float = 0.0
        self.pending_sl: float = 0.0
        self.pending_tp: float = 0.0
        self.pending_expire_time: float = 0.0

        # Active Position Details
        self.position_side: Optional[str] = None  # "LONG" or "SHORT"
        self.entry_price: float = 0.0
        self.entry_time: float = 0.0
        self.position_size: float = 0.0
        self.stop_loss: float = 0.0
        self.take_profit: float = 0.0
        self.floating_pnl: float = 0.0
        self.floating_pnl_pct: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "mark_price": self.mark_price,
            "last_price": self.last_price,
            "last_tick_time": round(self.last_tick_time, 2),
            "seconds_since_tick": round(time.time() - self.last_tick_time, 1),
            "phase": self.phase,
            "macro_bias": self.macro_bias,
            "macro_200_ema": self.macro_200_ema,
            "current_atr": self.current_atr,
            "bsl": self.bsl,
            "ssl": self.ssl,
            "range_width_pct": round(self.range_width_pct * 100.0, 2),
            "sweep_dir": self.sweep_dir,
            "sweep_extreme_px": self.sweep_extreme_px,
            "sweep_time": self.sweep_time,
            "sweep_candle_ts": self.sweep_candle_ts,
            "fvg_top": self.fvg_top,
            "fvg_bottom": self.fvg_bottom,
            "fvg_ce": self.fvg_ce,
            "pending_order_id": self.pending_order_id,
            "pending_side": self.pending_side,
            "pending_ep": self.pending_ep,
            "pending_order_qty": self.pending_order_qty,
            "pending_sl": self.pending_sl,
            "pending_tp": self.pending_tp,
            "position_side": self.position_side,
            "entry_price": self.entry_price,
            "position_size": self.position_size,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "floating_pnl": round(self.floating_pnl, 2),
            "floating_pnl_pct": round(self.floating_pnl_pct, 2),
            "entry_time_iso": datetime.fromtimestamp(self.entry_time).isoformat() if self.entry_time else None,
        }


# ==============================================================================
# MAIN ENGINE SERVICE
# ==============================================================================

class AMDEngine:
    def __init__(self, client: AMDBitService, symbols: Optional[List[str]] = None):
        self.client = client
        self.symbols = symbols if symbols else ACTIVE_SYMBOLS
        self.is_running = False

        # Session Start (Persist across daemon restarts so uptime never resets)
        self.session_start = datetime.now()
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    prev_state = json.load(f)
                    if "session_start_iso" in prev_state and prev_state["session_start_iso"]:
                        self.session_start = datetime.fromisoformat(prev_state["session_start_iso"])
            except Exception:
                pass

        # State Mapping: {symbol: PairAMDState}
        self.pairs: Dict[str, PairAMDState] = {}
        for sym in self.symbols:
            prof = AMD_PROFILES.get(sym, AMD_PROFILES["BTCUSDT"])
            self.pairs[sym] = PairAMDState(sym, prof)

        # Threading lock
        self._lock = threading.Lock()
        self.ws: Optional[WebSocket] = None
        self.last_ws_msg_time: float = time.time()

        # Closed Trades Memory
        self.closed_trades: List[Dict[str, Any]] = []
        self._load_trade_history()

    # -- Initialization & Historical Seed --------------------------------------

    def initialize(self) -> None:
        """Fetch historical klines to seed 4H EMA and 15m range calculations."""
        logger.info("Initializing AMD Engine: Seeding historical candles from Bybit...")
        self.client.init_market_and_account(self.symbols)

        for sym in self.symbols:
            pair = self.pairs[sym]
            # 1. Fetch 240m (4H) candles for 200 EMA
            try:
                res_4h = self.client.session.get_kline(category="linear", symbol=sym, interval="240", limit=250)
                if res_4h.get("retCode") == 0:
                    raw_4h = res_4h["result"]["list"]
                    raw_4h.reverse()
                    pair.candles_4h = [
                        {"open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]), "timestamp": int(k[0])}
                        for k in raw_4h
                    ]
                    closes_4h = [c["close"] for c in pair.candles_4h]
                    pair.macro_200_ema = calc_ema(closes_4h, 200)
                    if not math.isnan(pair.macro_200_ema) and closes_4h:
                        pair.macro_bias = "BULLISH" if closes_4h[-1] > pair.macro_200_ema else "BEARISH"
                    logger.info(f"[{sym}] 4H 200 EMA seeded: {pair.macro_200_ema:.2f} | Bias: {pair.macro_bias}")
            except Exception as e:
                logger.error(f"[{sym}] Failed to seed 4H klines: {e}")

            # 2. Fetch 15m candles for Accumulation range
            try:
                res_15m = self.client.session.get_kline(category="linear", symbol=sym, interval="15", limit=100)
                if res_15m.get("retCode") == 0:
                    raw_15m = res_15m["result"]["list"]
                    raw_15m.reverse()
                    pair.candles_15m = [
                        {"open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]), "timestamp": int(k[0])}
                        for k in raw_15m
                    ]
                    self._update_15m_range(pair)
            except Exception as e:
                logger.error(f"[{sym}] Failed to seed 15m klines: {e}")

        # 3. Crash-restart reconciliation
        self._reconcile_positions()
        self._dump_state_atomic()

    def _update_15m_range(self, pair: PairAMDState) -> None:
        """Update 15m ATR and Accumulation range (BSL & SSL) from prior consolidation bars."""
        lookback = pair.profile.range_lookback
        if len(pair.candles_15m) < lookback + 14:
            return

        pair.current_atr = calc_atr(pair.candles_15m, 14)
        # Prior 16 bars excluding the current candle that is being evaluated for sweep
        recent = pair.candles_15m[-(lookback + 1):-1]
        pair.bsl = max(c["high"] for c in recent)
        pair.ssl = min(c["low"] for c in recent)
        curr_c = pair.candles_15m[-1]["close"]
        if curr_c > 0:
            pair.range_width_pct = (pair.bsl - pair.ssl) / curr_c

    # -- WebSocket Connection & Dual-Feed Synchronization ---------------------

    def _connect_websocket(self) -> None:
        """Create or recreate Bybit Linear WebSocket connection and subscribe."""
        try:
            if self.ws:
                try:
                    self.ws.exit()
                except Exception:
                    pass
            self.ws = WebSocket(
                testnet=TESTNET,
                channel_type="linear",
            )
            for sym in self.symbols:
                self.ws.kline_stream(interval=15, symbol=sym, callback=self._handle_kline_15m)
                self.ws.kline_stream(interval=240, symbol=sym, callback=self._handle_kline_4h)
                self.ws.ticker_stream(symbol=sym, callback=self._handle_ticker)
            self.last_ws_msg_time = time.time()
            logger.info(f"Subscribed WebSocket to 15m, 240m klines and tickers for {', '.join(self.symbols)}.")
        except Exception as e:
            logger.error(f"WebSocket connection error: {e}")

    def _ws_watchdog_loop(self) -> None:
        """Watchdog to ensure WebSocket connection never stays dead silently."""
        while self.is_running:
            time.sleep(10.0)
            now = time.time()
            silence_duration = now - self.last_ws_msg_time
            if silence_duration > 30.0:
                logger.warning(
                    f"[WATCHDOG] WebSocket silent for {silence_duration:.1f}s. Reconnecting Bybit WebSocket..."
                )
                self._connect_websocket()

    def _rest_sync_loop(self) -> None:
        """Dual-Feed REST Synchronization running every 10 seconds.
        Ensures mark prices never freeze and closed klines are never missed,
        even if WebSocket disconnects or drops packets."""
        while self.is_running:
            time.sleep(10.0)
            for sym in self.symbols:
                try:
                    pair = self.pairs.get(sym)
                    if not pair:
                        continue

                    # 1. Sync live ticker via REST
                    t = self.client.get_ticker(sym)
                    if t and (t["mark_price"] > 0 or t["last_price"] > 0):
                        with self._lock:
                            if t["mark_price"] > 0:
                                pair.mark_price = t["mark_price"]
                            if t["last_price"] > 0:
                                pair.last_price = t["last_price"]
                            pair.last_tick_time = time.time()
                            if pair.phase == "IN_POSITION" and pair.entry_price > 0 and pair.position_size > 0:
                                cur_px = pair.mark_price if pair.mark_price > 0 else pair.last_price
                                if pair.position_side == "LONG":
                                    pair.floating_pnl = (cur_px - pair.entry_price) * pair.position_size
                                    pair.floating_pnl_pct = ((cur_px - pair.entry_price) / pair.entry_price) * 100.0
                                elif pair.position_side == "SHORT":
                                    pair.floating_pnl = (pair.entry_price - cur_px) * pair.position_size
                                    pair.floating_pnl_pct = ((pair.entry_price - cur_px) / pair.entry_price) * 100.0

                    # 2. Sync latest closed 15m kline
                    k15 = self.client.get_recent_closed_kline(sym, "15")
                    if k15 and pair.candles_15m:
                        with self._lock:
                            last_ts = pair.candles_15m[-1]["timestamp"]
                            if k15["timestamp"] > last_ts:
                                pair.candles_15m.append(k15)
                                if len(pair.candles_15m) > 100:
                                    pair.candles_15m.pop(0)
                                self._update_15m_range(pair)
                                self._evaluate_amd_transition(pair)
                                logger.info(
                                    f"[{sym}] Processed 15m candle close via REST sync: Close={k15['close']:.2f} "
                                    f"| Phase: {pair.phase}"
                                )

                    # 3. Sync latest closed 4H kline
                    k4h = self.client.get_recent_closed_kline(sym, "240")
                    if k4h and pair.candles_4h:
                        with self._lock:
                            last_4h_ts = pair.candles_4h[-1]["timestamp"]
                            if k4h["timestamp"] > last_4h_ts:
                                pair.candles_4h.append(k4h)
                                if len(pair.candles_4h) > 250:
                                    pair.candles_4h.pop(0)
                                closes = [c["close"] for c in pair.candles_4h]
                                pair.macro_200_ema = calc_ema(closes, 200)
                                if not math.isnan(pair.macro_200_ema):
                                    pair.macro_bias = "BULLISH" if closes[-1] > pair.macro_200_ema else "BEARISH"
                                    logger.info(
                                        f"[{sym}] 4H Bar Confirmed via REST sync. Close: {closes[-1]:.2f} "
                                        f"| 200-EMA: {pair.macro_200_ema:.2f} | Bias: {pair.macro_bias}"
                                    )
                except Exception as e:
                    logger.debug(f"[{sym}] REST sync error: {e}")

    def start(self) -> None:
        """Start Bybit V5 Public WebSocket listener, Watchdog, REST sync, and Maintenance Loop."""
        self.is_running = True
        self.last_ws_msg_time = time.time()
        logger.info(f"Connecting to Bybit Linear WebSocket ({WS_LINEAR_URL})...")
        self._connect_websocket()

        # Start Watchdog thread
        threading.Thread(target=self._ws_watchdog_loop, daemon=True).start()
        # Start Dual-Feed REST sync thread
        threading.Thread(target=self._rest_sync_loop, daemon=True).start()
        # Start periodic state dump and pending order timeout thread
        threading.Thread(target=self._maintenance_loop, daemon=True).start()

    # -- WebSocket Callbacks ---------------------------------------------------

    def _handle_ticker(self, message: Dict[str, Any]) -> None:
        self.last_ws_msg_time = time.time()
        topic = message.get("topic", "")
        data = message.get("data", {})
        sym = data.get("symbol")
        if not sym or sym not in self.pairs:
            return

        with self._lock:
            pair = self.pairs[sym]
            pair.last_tick_time = time.time()
            mp = data.get("markPrice")
            lp = data.get("lastPrice")
            if mp:
                pair.mark_price = float(mp)
            if lp:
                pair.last_price = float(lp)

            # Update floating PnL if in position
            if pair.phase == "IN_POSITION" and pair.entry_price > 0 and pair.position_size > 0:
                cur_px = pair.mark_price if pair.mark_price > 0 else pair.last_price
                if pair.position_side == "LONG":
                    pair.floating_pnl = (cur_px - pair.entry_price) * pair.position_size
                    pair.floating_pnl_pct = ((cur_px - pair.entry_price) / pair.entry_price) * 100.0
                elif pair.position_side == "SHORT":
                    pair.floating_pnl = (pair.entry_price - cur_px) * pair.position_size
                    pair.floating_pnl_pct = ((pair.entry_price - cur_px) / pair.entry_price) * 100.0

    def _handle_kline_4h(self, message: Dict[str, Any]) -> None:
        self.last_ws_msg_time = time.time()
        data = message.get("data", [])
        if not data:
            return
        k = data[0]
        sym = message.get("topic", "").split(".")[-1]
        if sym not in self.pairs:
            return

        with self._lock:
            pair = self.pairs[sym]
            pair.last_tick_time = time.time()
            c_data = {
                "open": float(k.get("open", 0)),
                "high": float(k.get("high", 0)),
                "low": float(k.get("low", 0)),
                "close": float(k.get("close", 0)),
                "volume": float(k.get("volume", 0)),
                "timestamp": int(k.get("start", 0)),
            }
            # If candle confirms (closed), update list and recompute 200 EMA
            if k.get("confirm", False):
                pair.candles_4h.append(c_data)
                if len(pair.candles_4h) > 250:
                    pair.candles_4h.pop(0)
                closes = [c["close"] for c in pair.candles_4h]
                pair.macro_200_ema = calc_ema(closes, 200)
                if not math.isnan(pair.macro_200_ema):
                    pair.macro_bias = "BULLISH" if closes[-1] > pair.macro_200_ema else "BEARISH"
                    logger.info(f"[{sym}] 4H Bar Confirmed. Close: {closes[-1]:.2f} | 200-EMA: {pair.macro_200_ema:.2f} | Bias: {pair.macro_bias}")

    def _handle_kline_15m(self, message: Dict[str, Any]) -> None:
        self.last_ws_msg_time = time.time()
        data = message.get("data", [])
        if not data:
            return
        k = data[0]
        sym = message.get("topic", "").split(".")[-1]
        if sym not in self.pairs:
            return

        with self._lock:
            pair = self.pairs[sym]
            pair.last_tick_time = time.time()
            c_data = {
                "open": float(k.get("open", 0)),
                "high": float(k.get("high", 0)),
                "low": float(k.get("low", 0)),
                "close": float(k.get("close", 0)),
                "volume": float(k.get("volume", 0)),
                "timestamp": int(k.get("start", 0)),
            }

            # If 15m candle confirmed closed, execute AMD state transitions
            if k.get("confirm", False):
                pair.candles_15m.append(c_data)
                if len(pair.candles_15m) > 100:
                    pair.candles_15m.pop(0)
                self._update_15m_range(pair)
                self._evaluate_amd_transition(pair)

    # -- State Machine Logic ---------------------------------------------------

    def _evaluate_amd_transition(self, pair: PairAMDState) -> None:
        """Run AMD state machine upon each confirmed 15m candle close."""
        if len(pair.candles_15m) < 20:
            return

        cur_candle = pair.candles_15m[-1]
        prev_candle = pair.candles_15m[-2]
        prev2_candle = pair.candles_15m[-3]

        c = cur_candle["close"]
        o = cur_candle["open"]
        h = cur_candle["high"]
        l = cur_candle["low"]

        # Phase 1 & 2: Check for Manipulation Liquidity Sweep
        if pair.phase == "ACCUMULATING":
            # Range must be within max compression threshold
            if pair.range_width_pct <= pair.profile.max_range_pct:
                # Bullish Sweep: Low pierced SSL, closed back inside, and 4H is Bullish
                if l < pair.ssl and c > pair.ssl and pair.macro_bias == "BULLISH":
                    pair.phase = "SWEEP_DETECTED"
                    pair.sweep_dir = "BULLISH"
                    pair.sweep_extreme_px = l
                    pair.sweep_time = time.time()
                    pair.sweep_candle_ts = int(cur_candle.get("timestamp", 0))
                    pair.sweep_bar_idx = len(pair.candles_15m) - 1
                    logger.info(f"[{pair.symbol}] BULLISH SWEEP DETECTED! Low {l:.2f} < SSL {pair.ssl:.2f} | 4H Bias: BULLISH")

                # Bearish Sweep: High pierced BSL, closed back inside, and 4H is Bearish
                elif h > pair.bsl and c < pair.bsl and pair.macro_bias == "BEARISH":
                    pair.phase = "SWEEP_DETECTED"
                    pair.sweep_dir = "BEARISH"
                    pair.sweep_extreme_px = h
                    pair.sweep_time = time.time()
                    pair.sweep_candle_ts = int(cur_candle.get("timestamp", 0))
                    pair.sweep_bar_idx = len(pair.candles_15m) - 1
                    logger.info(f"[{pair.symbol}] BEARISH SWEEP DETECTED! High {h:.2f} > BSL {pair.bsl:.2f} | 4H Bias: BEARISH")

        # Phase 3: Displacement & Fair Value Gap (FVG) Detection
        elif pair.phase == "SWEEP_DETECTED":
            if pair.sweep_candle_ts > 0:
                bars_since = sum(1 for cand in pair.candles_15m if cand.get("timestamp", 0) > pair.sweep_candle_ts)
            else:
                bars_since = 4  # Stale or uninitialized sweep, force expiration

            if 1 <= bars_since <= 3:
                # Displacement quality check
                disp_range = prev_candle["high"] - prev_candle["low"]
                disp_body = abs(prev_candle["close"] - prev_candle["open"])
                strong_disp = (disp_body / disp_range >= pair.profile.disp_body_min) if disp_range > 0 else False

                # Bullish FVG: prev2_candle High < cur_candle Low
                if pair.sweep_dir == "BULLISH" and strong_disp and prev_candle["close"] > prev_candle["open"]:
                    if prev2_candle["high"] < l:
                        fvg_top = l
                        fvg_bottom = prev2_candle["high"]
                        fvg_ce = 0.5 * (fvg_top + fvg_bottom)

                        pair.fvg_top = fvg_top
                        pair.fvg_bottom = fvg_bottom
                        pair.fvg_ce = fvg_ce

                        # Arm Post-Only Limit Order
                        pending_ep = fvg_top
                        sl_buf = pair.profile.sl_atr_buffer * pair.current_atr
                        pending_sl = pair.sweep_extreme_px - sl_buf
                        risk = pending_ep - pending_sl

                        if risk > 0:
                            pending_tp = max(pair.bsl, pending_ep + pair.profile.rr_ratio * risk)
                            order_id_link = f"amd_{pair.symbol.lower()}_{int(time.time())}"

                            # Strict $1,000 Capital Guard: Check active position slot count across portfolio
                            active_slots = sum(1 for p in self.pairs.values() if p.phase in ["IN_POSITION", "FVG_PENDING"])
                            if active_slots < MAX_CONCURRENT_PAIRS:
                                # Strict sizing: $250 margin * 4x leverage = $1,000 notional
                                target_notional = MARGIN_PER_TRADE * pair.profile.leverage
                                raw_qty = target_notional / pending_ep
                                order_qty = round(raw_qty) if pair.profile.qty_precision == 0 else round(raw_qty, pair.profile.qty_precision)
                                order_qty = max(order_qty, pair.profile.min_order_qty)

                                res = self.client.place_fvg_limit_order(
                                    pair.symbol, "Buy", order_qty, pending_ep, order_id_link
                                )
                                if res:
                                    pair.phase = "FVG_PENDING"
                                    pair.pending_side = "BUY"
                                    pair.pending_ep = pending_ep
                                    pair.pending_order_qty = order_qty
                                    pair.pending_sl = pending_sl
                                    pair.pending_tp = pending_tp
                                    pair.pending_order_id = res.get("orderId")
                                    pair.pending_order_link_id = order_id_link
                                    pair.pending_expire_time = time.time() + (pair.profile.order_timeout_minutes * 60)
                                    logger.info(
                                        f"[{pair.symbol}] FVG FORMED! Limit BUY armed: {order_qty} @ {pending_ep:.2f} "
                                        f"($1,000 notional / $250 margin) | SL: {pending_sl:.2f} | TP: {pending_tp:.2f} "
                                        f"| Slots: {active_slots + 1}/{MAX_CONCURRENT_PAIRS} active"
                                    )

                # Bearish FVG: prev2_candle Low > cur_candle High
                elif pair.sweep_dir == "BEARISH" and strong_disp and prev_candle["close"] < prev_candle["open"]:
                    if prev2_candle["low"] > h:
                        fvg_bottom = h
                        fvg_top = prev2_candle["low"]
                        fvg_ce = 0.5 * (fvg_top + fvg_bottom)

                        pair.fvg_top = fvg_top
                        pair.fvg_bottom = fvg_bottom
                        pair.fvg_ce = fvg_ce

                        pending_ep = fvg_bottom
                        sl_buf = pair.profile.sl_atr_buffer * pair.current_atr
                        pending_sl = pair.sweep_extreme_px + sl_buf
                        risk = pending_sl - pending_ep

                        if risk > 0:
                            pending_tp = min(pair.ssl, pending_ep - pair.profile.rr_ratio * risk)
                            order_id_link = f"amd_{pair.symbol.lower()}_{int(time.time())}"

                            # Strict $1,000 Capital Guard: Check active position slot count across portfolio
                            active_slots = sum(1 for p in self.pairs.values() if p.phase in ["IN_POSITION", "FVG_PENDING"])
                            if active_slots < MAX_CONCURRENT_PAIRS:
                                # Strict sizing: $250 margin * 4x leverage = $1,000 notional
                                target_notional = MARGIN_PER_TRADE * pair.profile.leverage
                                raw_qty = target_notional / pending_ep
                                order_qty = round(raw_qty) if pair.profile.qty_precision == 0 else round(raw_qty, pair.profile.qty_precision)
                                order_qty = max(order_qty, pair.profile.min_order_qty)

                                res = self.client.place_fvg_limit_order(
                                    pair.symbol, "Sell", order_qty, pending_ep, order_id_link
                                )
                                if res:
                                    pair.phase = "FVG_PENDING"
                                    pair.pending_side = "SELL"
                                    pair.pending_ep = pending_ep
                                    pair.pending_order_qty = order_qty
                                    pair.pending_sl = pending_sl
                                    pair.pending_tp = pending_tp
                                    pair.pending_order_id = res.get("orderId")
                                    pair.pending_order_link_id = order_id_link
                                    pair.pending_expire_time = time.time() + (pair.profile.order_timeout_minutes * 60)
                                    logger.info(
                                        f"[{pair.symbol}] BEARISH FVG FORMED! Limit SELL armed: {order_qty} @ {pending_ep:.2f} "
                                        f"($1,000 notional / $250 margin) | SL: {pending_sl:.2f} | TP: {pending_tp:.2f} "
                                        f"| Slots: {active_slots + 1}/{MAX_CONCURRENT_PAIRS} active"
                                    )

            elif bars_since > 3:
                # Sweep expired without displacement
                logger.info(f"[{pair.symbol}] Sweep timed out without valid displacement ({bars_since} bars). Resetting to ACCUMULATING.")
                pair.phase = "ACCUMULATING"
                pair.sweep_dir = None
                pair.sweep_candle_ts = 0

    # -- Maintenance Loop (Fill Checks, SL/TP Monitoring, Atomic Dump) ----------

    def _maintenance_loop(self) -> None:
        """Runs every 2 seconds to check order fills, timeouts, SL/TP hits, and dumps state."""
        while self.is_running:
            try:
                with self._lock:
                    now = time.time()
                    for sym, pair in self.pairs.items():
                        # 1. Check Pending Order Timeout or Fill
                        if pair.phase == "FVG_PENDING":
                            # Check Timeout
                            if now > pair.pending_expire_time:
                                logger.info(f"[{sym}] Pending FVG Limit Order timed out. Canceling...")
                                self.client.cancel_order(sym, pair.pending_order_id, pair.pending_order_link_id)
                                pair.phase = "ACCUMULATING"
                                pair.pending_order_id = None
                                pair.pending_side = None
                                pair.pending_order_qty = 0.0
                                continue

                            # Check Fill via Bybit order query (if live) or simulation price crossing
                            cur_px = pair.mark_price if pair.mark_price > 0 else pair.last_price
                            filled = False
                            actual_ep = pair.pending_ep
                            actual_qty = pair.pending_order_qty if pair.pending_order_qty > 0 else pair.profile.base_size

                            if not self.client.is_dry_run and (pair.pending_order_id or pair.pending_order_link_id):
                                ord_status = self.client.get_order_status(sym, pair.pending_order_id, pair.pending_order_link_id)
                                if ord_status:
                                    st_str = ord_status.get("orderStatus", "")
                                    if st_str == "Filled":
                                        filled = True
                                        avg_p = float(ord_status.get("avgPrice", 0) or 0)
                                        if avg_p > 0:
                                            actual_ep = avg_p
                                        cum_q = float(ord_status.get("cumExecQty", 0) or 0)
                                        if cum_q > 0:
                                            actual_qty = cum_q
                                    elif st_str in ["Cancelled", "Deactivated", "Rejected"]:
                                        logger.info(f"[{sym}] Pending order {st_str} on Bybit. Resetting...")
                                        pair.phase = "ACCUMULATING"
                                        pair.pending_order_id = None
                                        pair.pending_side = None
                                        pair.pending_order_qty = 0.0
                                        continue

                            # Fallback Fill Check via mark price touch
                            if not filled and cur_px > 0:
                                if pair.pending_side == "BUY" and cur_px <= pair.pending_ep:
                                    filled = True
                                elif pair.pending_side == "SELL" and cur_px >= pair.pending_ep:
                                    filled = True

                            if filled:
                                logger.info(f"[{sym}] FVG LIMIT ORDER FILLED: {actual_qty} @ {actual_ep:.2f}!")
                                pair.phase = "IN_POSITION"
                                pair.position_side = "LONG" if pair.pending_side == "BUY" else "SHORT"
                                pair.entry_price = actual_ep
                                pair.entry_time = now
                                pair.position_size = actual_qty
                                pair.stop_loss = pair.pending_sl
                                pair.take_profit = pair.pending_tp
                                pair.pending_order_id = None
                                pair.pending_side = None
                                pair.pending_order_qty = 0.0

                                # Arm Exchange Structural SL
                                self.client.set_structural_stop_loss(sym, pair.position_side, pair.stop_loss)
                                # Arm Exchange Limit TP
                                close_side = "Sell" if pair.position_side == "LONG" else "Buy"
                                tp_link = f"tp_{sym.lower()}_{int(now)}"
                                self.client.place_take_profit_order(
                                    sym, close_side, pair.position_size, pair.take_profit, tp_link
                                )

                        # 2. Check Active Position SL / TP Hits
                        elif pair.phase == "IN_POSITION":
                            cur_px = pair.mark_price if pair.mark_price > 0 else pair.last_price
                            sl_hit = False
                            tp_hit = False

                            if cur_px > 0:
                                if pair.position_side == "LONG":
                                    if pair.stop_loss > 0 and cur_px <= pair.stop_loss:
                                        sl_hit = True
                                    elif pair.take_profit > 0 and cur_px >= pair.take_profit:
                                        tp_hit = True
                                elif pair.position_side == "SHORT":
                                    if pair.stop_loss > 0 and cur_px >= pair.stop_loss:
                                        sl_hit = True
                                    elif pair.take_profit > 0 and cur_px <= pair.take_profit:
                                        tp_hit = True

                            # If live, also check if position was closed on Bybit via exchange order
                            if not self.client.is_dry_run and not (sl_hit or tp_hit):
                                open_pos = self.client.get_open_positions()
                                sym_pos = next((p for p in open_pos if p.get("symbol") == sym and float(p.get("size", 0) or 0) > 0), None)
                                if not sym_pos:
                                    closed_records = self.client.get_closed_pnl(sym, limit=1)
                                    exit_px = pair.take_profit if pair.take_profit > 0 else (cur_px if cur_px > 0 else pair.entry_price)
                                    exit_reason = "EXCHANGE_CLOSE"
                                    if closed_records:
                                        rec = closed_records[0]
                                        exit_px = float(rec.get("avgExitPrice", 0) or exit_px)
                                        pnl_val = float(rec.get("closedPnl", 0) or 0)
                                        exit_reason = "TP" if pnl_val >= 0 else "SL"
                                    if exit_px <= 0:
                                        exit_px = cur_px if cur_px > 0 else pair.entry_price
                                    logger.info(f"[{sym}] Exchange position closed via {exit_reason} @ {exit_px:.2f}!")
                                    self._record_closed_trade(pair, exit_px, exit_reason)
                                    pair.phase = "ACCUMULATING"
                                    pair.position_side = None
                                    pair.entry_price = 0.0
                                    pair.position_size = 0.0
                                    pair.floating_pnl = 0.0
                                    pair.floating_pnl_pct = 0.0
                                    continue

                            if sl_hit or tp_hit:
                                exit_reason = "TP" if tp_hit else "SL"
                                exit_px = pair.take_profit if tp_hit else pair.stop_loss
                                if exit_px <= 0:
                                    exit_px = cur_px if cur_px > 0 else pair.entry_price
                                logger.info(f"[{sym}] POSITION CLOSED via {exit_reason} @ {exit_px:.2f}!")
                                if not self.client.is_dry_run and exit_reason == "SL":
                                    self.client.close_position_market(sym, pair.position_side, pair.position_size)
                                self._record_closed_trade(pair, exit_px, exit_reason)

                                # Reset pair state
                                pair.phase = "ACCUMULATING"
                                pair.position_side = None
                                pair.entry_price = 0.0
                                pair.position_size = 0.0
                                pair.floating_pnl = 0.0
                                pair.floating_pnl_pct = 0.0

                    self._dump_state_atomic()
            except Exception as e:
                logger.error(f"Error in maintenance loop: {e}")

            time.sleep(2.0)

    # -- Trade Recording & State Persistence -----------------------------------

    def _record_closed_trade(self, pair: PairAMDState, exit_px: float, exit_reason: str) -> None:
        """Calculate PnL, deduct fees, log to CSV, and retain in memory."""
        ep = pair.entry_price
        xp = exit_px if exit_px > 0 else (pair.mark_price if pair.mark_price > 0 else ep)
        sz = pair.position_size
        dur_min = (time.time() - pair.entry_time) / 60.0 if pair.entry_time else 0.0

        if pair.position_side == "LONG":
            gross_pnl = (xp - ep) * sz
        else:
            gross_pnl = (ep - xp) * sz

        # Bybit VIP0 / MEXC fee modeling: Maker entry (0.02% or 0%), TP is Maker, SL is Taker (0.055% or 0.032%)
        maker_rate = 0.00020
        taker_rate = 0.00055 if exit_reason == "SL" else 0.00020
        fees = (maker_rate * ep * sz) + (taker_rate * xp * sz)
        net_pnl = gross_pnl - fees

        trade_record = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": pair.symbol,
            "side": pair.position_side,
            "entry_price": ep,
            "exit_price": xp,
            "qty": sz,
            "gross_pnl": round(gross_pnl, 2),
            "fees": round(fees, 2),
            "net_pnl": round(net_pnl, 2),
            "exit_reason": exit_reason,
            "duration_minutes": round(dur_min, 1),
            "macro_bias": pair.macro_bias,
        }

        self.closed_trades.append(trade_record)

        # Write to isolated amd_trades.csv
        file_exists = os.path.exists(TRADES_FILE)
        try:
            with open(TRADES_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow([
                        "timestamp", "symbol", "side", "entry_price", "exit_price",
                        "qty", "gross_pnl", "fees", "net_pnl", "exit_reason",
                        "duration_minutes", "macro_bias"
                    ])
                writer.writerow([
                    trade_record["timestamp"], trade_record["symbol"], trade_record["side"],
                    f"{trade_record['entry_price']:.4f}", f"{trade_record['exit_price']:.4f}",
                    trade_record["qty"], trade_record["gross_pnl"], trade_record["fees"],
                    trade_record["net_pnl"], trade_record["exit_reason"],
                    trade_record["duration_minutes"], trade_record["macro_bias"]
                ])
        except Exception as e:
            logger.error(f"Failed to append to {TRADES_FILE}: {e}")

    def _load_trade_history(self) -> None:
        """Load closed trade records from amd_trades.csv, automatically filtering out corrupt exit_price=0.0 records."""
        if not os.path.exists(TRADES_FILE):
            return
        valid_rows = []
        header_row = None
        has_corrupt = False
        try:
            with open(TRADES_FILE, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                header_row = next(reader, None)
                for row in reader:
                    if len(row) >= 10:
                        xp = float(row[4])
                        # Filter out corrupt/phantom records where exit price is zero
                        if xp <= 0.0:
                            has_corrupt = True
                            logger.warning(f"Purging corrupt historical trade record with exit_price=0.0: {row[:5]}")
                            continue
                        self.closed_trades.append({
                            "timestamp": row[0],
                            "symbol": row[1],
                            "side": row[2],
                            "entry_price": float(row[3]),
                            "exit_price": xp,
                            "qty": float(row[5]),
                            "gross_pnl": float(row[6]),
                            "fees": float(row[7]),
                            "net_pnl": float(row[8]),
                            "exit_reason": row[9],
                            "duration_minutes": float(row[10]) if len(row) > 10 else 0.0,
                            "macro_bias": row[11] if len(row) > 11 else "",
                        })
                        valid_rows.append(row)

            # Rewrite clean amd_trades.csv without corrupt exit_price=0.0 records
            if has_corrupt and header_row:
                with open(TRADES_FILE, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(header_row)
                    writer.writerows(valid_rows)
                logger.info(f"Cleaned {TRADES_FILE}: purged corrupt records, retained {len(valid_rows)} authentic trades.")
        except Exception as e:
            logger.debug(f"Could not load trade history from {TRADES_FILE}: {e}")

    def _reconcile_positions(self) -> None:
        """Adopt any live open positions from Bybit on engine startup, strictly validating size and SL/TP."""
        if self.client.is_dry_run:
            return
        logger.info("Reconciling live Bybit exchange positions for AMD bot...")
        try:
            open_positions = self.client.get_open_positions()
            for pos in open_positions:
                sym = pos.get("symbol")
                if sym in self.pairs:
                    pair = self.pairs[sym]
                    sz = float(pos.get("size", 0) or 0)
                    side_str = pos.get("side")
                    ep = float(pos.get("avgPrice", 0) or 0)
                    sl = float(pos.get("stopLoss", 0) or 0)
                    tp = float(pos.get("takeProfit", 0) or 0)

                    if sz > 0:
                        # Safety Guard: Check if position sizing matches AMD profile ($1,000 notional)
                        # Avoid adopting foreign bot positions (e.g. hedge bot 0.49 BTC)
                        expected_notional = MARGIN_PER_TRADE * pair.profile.leverage  # $1,000.0
                        actual_notional = sz * ep
                        if actual_notional > expected_notional * 1.5 or actual_notional < expected_notional * 0.5:
                            logger.warning(
                                f"[{sym}] Skipping foreign position: size={sz} (notional ~${actual_notional:.1f} vs expected ${expected_notional:.1f})"
                            )
                            continue

                        # Never adopt without valid SL and TP on exchange
                        if sl <= 0 or tp <= 0:
                            logger.warning(f"[{sym}] Skipping unmanaged position without valid SL/TP on exchange")
                            continue

                        pair.phase = "IN_POSITION"
                        pair.position_side = "LONG" if side_str == "Buy" else "SHORT"
                        pair.entry_price = ep
                        pair.position_size = sz
                        pair.stop_loss = sl
                        pair.take_profit = tp
                        pair.entry_time = time.time()
                        logger.info(f"[{sym}] Reconciled active exchange position: {pair.position_side} {sz} @ {ep:.2f}")
        except Exception as e:
            logger.error(f"Reconciliation error: {e}")

    def _dump_state_atomic(self) -> None:
        """Atomically write full engine state to amd_bot_state.json."""
        acc_bal = self.client.get_wallet_balance()
        tot_realized_pnl = sum(t.get("net_pnl", 0.0) for t in self.closed_trades)

        active_slots = sum(1 for p in self.pairs.values() if p.phase in ["IN_POSITION", "FVG_PENDING"])
        used_margin = active_slots * MARGIN_PER_TRADE
        empty_slots = max(0, MAX_CONCURRENT_PAIRS - active_slots)
        cash_reserve = max(0.0, ACCOUNT_CAPITAL - used_margin)

        state_data = {
            "timestamp": datetime.now().isoformat(),
            "session_start_iso": self.session_start.isoformat(),
            "uptime_seconds": max(0, int((datetime.now() - self.session_start).total_seconds())),
            "concurrency": {
                "total_slots": MAX_CONCURRENT_PAIRS,
                "active_slots": active_slots,
                "empty_slots": empty_slots,
                "margin_in_use": used_margin,
                "cash_reserve": cash_reserve,
                "allocated_capital": ACCOUNT_CAPITAL,
            },
            "account": {
                "equity": acc_bal.get("equity", 0.0),
                "wallet_balance": acc_bal.get("wallet_balance", 0.0),
                "available_balance": acc_bal.get("available_balance", 0.0),
                "total_realized_pnl": round(tot_realized_pnl, 2),
                "closed_trades_count": len(self.closed_trades),
            },
            "symbols": {sym: pair.to_dict() for sym, pair in self.pairs.items()},
            "recent_trades": self.closed_trades[-15:],
        }

        tmp_file = f"{STATE_FILE}.tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2)
            os.replace(tmp_file, STATE_FILE)
        except Exception as e:
            logger.debug(f"State dump error: {e}")
