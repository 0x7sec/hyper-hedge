import os
import argparse
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()


# -- Champion defaults per market (Option A: Pure Single-Leg Trend Runner) ---
DEFAULT_PROFILES: Dict[str, Dict[str, Any]] = {
    "BTCUSDT": {
        "candle_interval": "60",
        "ema_fast": 9,
        "ema_slow": 21,
        "macro_ema_period": 200,
        "use_macro_trend_filter": True,
        "adx_min": Decimal("20"),
        "adx_period": 14,
        "adx_rising_required": True,
        "d_pct": Decimal("0.80"),
        "use_dynamic_atr": True,
        "atr_mult": Decimal("0.85"),
        "exhaustion_mult": Decimal("1.50"),
        "confirm_mult": Decimal("0.35"),
        "b1_confirm": Decimal("0.35"),    # Move to BE at +0.35D
        "b2_confirm": Decimal("1.20"),    # Initial SL barrier at -1.20D
        "b2_upsize": False,               # Pure Single-Leg (No upsize)
        "sl_pct": Decimal("0.34"),
        "tp_pct": Decimal("2.80"),
        "b1_tp_mult": Decimal("3.50"),    # Apex TP at +3.50D
        "b1_r1_trig": Decimal("1.00"),    # Stage 1 Ratchet trigger (+1.00D)
        "b1_r1_sl": Decimal("0.60"),      # Stage 1 SL raised to (+0.60D)
        "b1_r2_trig": Decimal("1.30"),    # Stage 2 Ratchet trigger (+1.30D)
        "b1_r2_sl": Decimal("0.90"),      # Stage 2 SL raised to (+0.90D)
        "b2_tp_mult": Decimal("2.00"),
        "b2_be_cushion": Decimal("0.10"),
        "b2_r2_trig": Decimal("2.50"),
        "b2_r2_sl": Decimal("2.10"),
        "ratchet_step_pct": Decimal("0.25"),
        "be_lock": True,
        "be_buffer_pct": Decimal("0.34"),
        "size": Decimal("0.01"),          # ~$1,000 notional (for $1,000 capital, 4x leverage, max 3 slots)
        "asymmetric": True,
        "hedge_ratio": Decimal("0.0"),    # PURE SINGLE-LEG (NO COUNTER)
        "timeout_bars": 50,
    },
    "ETHUSDT": {
        "candle_interval": "60",
        "ema_fast": 9,
        "ema_slow": 21,
        "macro_ema_period": 200,
        "use_macro_trend_filter": True,
        "adx_min": Decimal("20"),
        "adx_period": 14,
        "adx_rising_required": True,
        "d_pct": Decimal("0.80"),
        "use_dynamic_atr": True,
        "atr_mult": Decimal("0.85"),
        "exhaustion_mult": Decimal("1.50"),
        "confirm_mult": Decimal("0.40"),
        "b1_confirm": Decimal("0.40"),    # Move to BE at +0.40D
        "b2_confirm": Decimal("1.00"),    # Initial SL barrier at -1.00D
        "b2_upsize": False,
        "sl_pct": Decimal("0.38"),
        "tp_pct": Decimal("2.56"),
        "b1_tp_mult": Decimal("3.20"),    # Apex TP at +3.20D
        "b1_r1_trig": Decimal("0.80"),    # Stage 1 Ratchet trigger (+0.80D)
        "b1_r1_sl": Decimal("0.40"),      # Stage 1 SL raised to (+0.40D)
        "b1_r2_trig": Decimal("1.40"),    # Stage 2 Ratchet trigger (+1.40D)
        "b1_r2_sl": Decimal("1.00"),      # Stage 2 SL raised to (+1.00D)
        "b2_tp_mult": Decimal("2.00"),
        "b2_be_cushion": Decimal("0.10"),
        "b2_r2_trig": Decimal("2.50"),
        "b2_r2_sl": Decimal("2.10"),
        "ratchet_step_pct": Decimal("0.25"),
        "be_lock": True,
        "be_buffer_pct": Decimal("0.38"),
        "size": Decimal("0.35"),          # ~$1,000 notional (for $1,000 capital, 4x leverage, max 3 slots)
        "asymmetric": True,
        "hedge_ratio": Decimal("0.0"),    # PURE SINGLE-LEG (NO COUNTER)
        "timeout_bars": 50,
    },
    "SOLUSDT": {
        "candle_interval": "60",
        "ema_fast": 9,
        "ema_slow": 21,
        "macro_ema_period": 200,
        "use_macro_trend_filter": True,
        "adx_min": Decimal("20"),
        "adx_period": 14,
        "adx_rising_required": True,
        "d_pct": Decimal("0.80"),
        "use_dynamic_atr": True,
        "atr_mult": Decimal("0.85"),
        "exhaustion_mult": Decimal("1.50"),
        "confirm_mult": Decimal("0.40"),
        "b1_confirm": Decimal("0.40"),    # Move to BE at +0.40D
        "b2_confirm": Decimal("1.00"),    # Initial SL barrier at -1.00D
        "b2_upsize": False,
        "sl_pct": Decimal("0.38"),
        "tp_pct": Decimal("2.56"),
        "b1_tp_mult": Decimal("3.20"),    # Apex TP at +3.20D
        "b1_r1_trig": Decimal("1.00"),    # Stage 1 Ratchet trigger (+1.00D)
        "b1_r1_sl": Decimal("0.60"),      # Stage 1 SL raised to (+0.60D)
        "b1_r2_trig": Decimal("1.60"),    # Stage 2 Ratchet trigger (+1.60D)
        "b1_r2_sl": Decimal("1.20"),      # Stage 2 SL raised to (+1.20D)
        "b2_tp_mult": Decimal("2.00"),
        "b2_be_cushion": Decimal("0.10"),
        "b2_r2_trig": Decimal("2.50"),
        "b2_r2_sl": Decimal("2.10"),
        "ratchet_step_pct": Decimal("0.25"),
        "be_lock": True,
        "be_buffer_pct": Decimal("0.38"),
        "size": Decimal("6.0"),           # ~$1,000 notional (for $1,000 capital, 4x leverage, max 3 slots)
        "asymmetric": True,
        "hedge_ratio": Decimal("0.0"),    # PURE SINGLE-LEG (NO COUNTER)
        "timeout_bars": 50,
    },
    "AVAXUSDT": {
        "candle_interval": "60",
        "ema_fast": 9,
        "ema_slow": 21,
        "macro_ema_period": 200,
        "use_macro_trend_filter": True,
        "adx_min": Decimal("20"),
        "adx_period": 14,
        "adx_rising_required": True,
        "d_pct": Decimal("0.80"),
        "use_dynamic_atr": True,
        "atr_mult": Decimal("0.85"),
        "b1_confirm": Decimal("0.35"),
        "b2_confirm": Decimal("1.20"),
        "b2_upsize": False,
        "sl_pct": Decimal("0.38"),
        "tp_pct": Decimal("2.80"),
        "b1_tp_mult": Decimal("3.50"),
        "b1_r1_trig": Decimal("1.00"),
        "b1_r1_sl": Decimal("0.60"),
        "b1_r2_trig": Decimal("1.50"),
        "b1_r2_sl": Decimal("1.10"),
        "size": Decimal("35.0"),          # ~$1,000 notional
        "asymmetric": True,
        "hedge_ratio": Decimal("0.0"),
        "timeout_bars": 50,
    },
    "LINKUSDT": {
        "candle_interval": "60",
        "ema_fast": 9,
        "ema_slow": 21,
        "macro_ema_period": 200,
        "use_macro_trend_filter": True,
        "adx_min": Decimal("20"),
        "adx_period": 14,
        "adx_rising_required": True,
        "d_pct": Decimal("0.80"),
        "use_dynamic_atr": True,
        "atr_mult": Decimal("0.85"),
        "b1_confirm": Decimal("0.35"),
        "b2_confirm": Decimal("1.50"),
        "b2_upsize": False,
        "sl_pct": Decimal("0.38"),
        "tp_pct": Decimal("2.80"),
        "b1_tp_mult": Decimal("3.50"),
        "b1_r1_trig": Decimal("0.80"),
        "b1_r1_sl": Decimal("0.40"),
        "b1_r2_trig": Decimal("1.50"),
        "b1_r2_sl": Decimal("1.10"),
        "size": Decimal("70.0"),          # ~$1,000 notional
        "asymmetric": True,
        "hedge_ratio": Decimal("0.0"),
        "timeout_bars": 50,
    },
    "HYPEUSDT": {
        "candle_interval": "60",
        "ema_fast": 9,
        "ema_slow": 21,
        "macro_ema_period": 200,
        "use_macro_trend_filter": True,
        "adx_min": Decimal("20"),
        "adx_period": 14,
        "adx_rising_required": True,
        "d_pct": Decimal("0.80"),
        "use_dynamic_atr": True,
        "atr_mult": Decimal("0.85"),
        "b1_confirm": Decimal("0.35"),
        "b2_confirm": Decimal("1.00"),
        "b2_upsize": False,
        "sl_pct": Decimal("0.38"),
        "tp_pct": Decimal("2.00"),
        "b1_tp_mult": Decimal("2.50"),
        "b1_r1_trig": Decimal("1.00"),
        "b1_r1_sl": Decimal("0.60"),
        "b1_r2_trig": Decimal("1.50"),
        "b1_r2_sl": Decimal("1.10"),
        "size": Decimal("35.0"),          # ~$1,000 notional
        "asymmetric": True,
        "hedge_ratio": Decimal("0.0"),
        "timeout_bars": 50,
    },
    "XMRUSDT": {
        "candle_interval": "60",
        "ema_fast": 9,
        "ema_slow": 21,
        "macro_ema_period": 200,
        "use_macro_trend_filter": True,
        "adx_min": Decimal("20"),
        "adx_period": 14,
        "adx_rising_required": True,
        "d_pct": Decimal("0.80"),
        "use_dynamic_atr": True,
        "atr_mult": Decimal("0.85"),
        "b1_confirm": Decimal("0.35"),
        "b2_confirm": Decimal("1.50"),
        "b2_upsize": False,
        "sl_pct": Decimal("0.38"),
        "tp_pct": Decimal("2.40"),
        "b1_tp_mult": Decimal("3.00"),
        "b1_r1_trig": Decimal("0.80"),
        "b1_r1_sl": Decimal("0.40"),
        "b1_r2_trig": Decimal("1.50"),
        "b1_r2_sl": Decimal("1.10"),
        "size": Decimal("6.0"),           # ~$1,000 notional
        "asymmetric": True,
        "hedge_ratio": Decimal("0.0"),
        "timeout_bars": 50,
    },
    "DOGEUSDT": {
        "candle_interval": "60",
        "ema_fast": 9,
        "ema_slow": 21,
        "macro_ema_period": 200,
        "use_macro_trend_filter": True,
        "adx_min": Decimal("20"),
        "adx_period": 14,
        "adx_rising_required": True,
        "d_pct": Decimal("0.80"),
        "use_dynamic_atr": True,
        "atr_mult": Decimal("0.85"),
        "b1_confirm": Decimal("0.50"),
        "b2_confirm": Decimal("1.50"),
        "b2_upsize": False,
        "sl_pct": Decimal("0.38"),
        "tp_pct": Decimal("2.80"),
        "b1_tp_mult": Decimal("3.50"),
        "b1_r1_trig": Decimal("0.80"),
        "b1_r1_sl": Decimal("0.40"),
        "b1_r2_trig": Decimal("1.60"),
        "b1_r2_sl": Decimal("1.20"),
        "size": Decimal("7500.0"),        # ~$1,000 notional
        "asymmetric": True,
        "hedge_ratio": Decimal("0.0"),
        "timeout_bars": 50,
    },
    "XAUUSDT": {
        "candle_interval": "60",
        "ema_fast": 9,
        "ema_slow": 21,
        "macro_ema_period": 200,
        "use_macro_trend_filter": True,
        "adx_min": Decimal("20"),
        "adx_period": 14,
        "adx_rising_required": True,
        "d_pct": Decimal("0.40"),
        "use_dynamic_atr": True,
        "atr_mult": Decimal("0.85"),
        "exhaustion_mult": Decimal("1.50"),
        "confirm_mult": Decimal("0.40"),
        "b1_confirm": Decimal("0.40"),
        "b2_confirm": Decimal("1.00"),
        "b2_upsize": False,
        "sl_pct": Decimal("0.40"),
        "tp_pct": Decimal("2.40"),
        "b1_tp_mult": Decimal("3.00"),
        "b1_r1_trig": Decimal("1.00"),
        "b1_r1_sl": Decimal("0.60"),
        "b1_r2_trig": Decimal("1.50"),
        "b1_r2_sl": Decimal("1.10"),
        "b2_tp_mult": Decimal("2.00"),
        "b2_be_cushion": Decimal("0.10"),
        "b2_r2_trig": Decimal("2.50"),
        "b2_r2_sl": Decimal("2.10"),
        "ratchet_step_pct": Decimal("0.25"),
        "be_lock": True,
        "be_buffer_pct": Decimal("0.20"),
        "size": Decimal("1.5"),
        "asymmetric": True,
        "hedge_ratio": Decimal("0.0"),    # PURE SINGLE-LEG (NO COUNTER)
        "timeout_bars": 50,
    },
}


@dataclass
class SymbolConfig:
    symbol: str
    candle_interval: str = "60"
    ema_fast: int = 9
    ema_slow: int = 21
    adx_min: Decimal = Decimal("0")
    adx_period: int = 14
    d_pct: Decimal = Decimal("0.80")
    use_dynamic_atr: bool = True
    atr_mult: Decimal = Decimal("0.85")
    exhaustion_mult: Decimal = Decimal("1.50")
    confirm_mult: Decimal = Decimal("0.80")  # Confirm direction at 0.80D
    sl_pct: Decimal = Decimal("0.38")
    tp_pct: Decimal = Decimal("1.60")
    b1_tp_mult: Decimal = Decimal("2.00")    # 2.0D Take-Profit on Branch 1
    b2_tp_mult: Decimal = Decimal("2.00")    # Branch 2 TP target
    b1_r1_trig: Decimal = Decimal("1.40")    # Stage 1 Ratchet trigger (+1.40D)
    b1_r1_sl: Decimal = Decimal("1.00")      # Stage 1 SL raised to (+1.00D)
    b1_r2_trig: Decimal = Decimal("2.20")    # Stage 2 Ratchet trigger (+2.20D)
    b1_r2_sl: Decimal = Decimal("1.70")      # Stage 2 SL raised to (+1.70D)
    b2_be_cushion: Decimal = Decimal("0.10") # Fast True BE lock cushion (+0.10D)
    b2_r2_trig: Decimal = Decimal("2.50")    # Milestone 2 profit ratchet on B2 (2.50D)
    b2_r2_sl: Decimal = Decimal("2.10")      # Milestone 2 SL raised to 2.10D (locks +0.65D net)
    ratchet_step_pct: Decimal = Decimal("0.25")
    be_lock: bool = True
    be_buffer_pct: Decimal = Decimal("0.38")
    macro_ema_period: int = 200
    use_macro_trend_filter: bool = True
    adx_rising_required: bool = True
    b1_confirm: Decimal = Decimal("0.40")
    b2_confirm: Decimal = Decimal("1.00")
    b2_upsize: bool = False
    size: Decimal = Decimal("0.001")
    asymmetric: bool = True
    hedge_ratio: Decimal = Decimal("0.0")
    timeout_bars: int = 50

    @classmethod
    def from_profile_or_defaults(cls, symbol: str, overrides: Optional[Dict[str, Any]] = None) -> "SymbolConfig":
        sym = symbol.upper().replace("/", "").replace("-", "")
        if sym in ("GOLD", "GOLDUSDT", "XAU", "XAUUSDT"):
            sym = "XAUUSDT"

        params = DEFAULT_PROFILES.get(sym, DEFAULT_PROFILES["BTCUSDT"]).copy()
        params["symbol"] = sym

        # Check per-symbol environment variables (e.g. BTC_SIZE, BTC_EMA_FAST, BTCUSDT_ADX_MIN)
        prefix = sym.replace("USDT", "")
        for pfx in (f"{sym}_", f"{prefix}_"):
            if os.getenv(f"{pfx}SIZE"):
                try: params["size"] = Decimal(os.getenv(f"{pfx}SIZE"))
                except Exception: pass
            if os.getenv(f"{pfx}D_PCT"):
                try: params["d_pct"] = Decimal(os.getenv(f"{pfx}D_PCT"))
                except Exception: pass
            if os.getenv(f"{pfx}USE_DYNAMIC_ATR"):
                params["use_dynamic_atr"] = os.getenv(f"{pfx}USE_DYNAMIC_ATR").lower() in ("true", "1", "yes")
            if os.getenv(f"{pfx}ATR_MULT"):
                try: params["atr_mult"] = Decimal(os.getenv(f"{pfx}ATR_MULT"))
                except Exception: pass
            if os.getenv(f"{pfx}EXHAUSTION_MULT"):
                try: params["exhaustion_mult"] = Decimal(os.getenv(f"{pfx}EXHAUSTION_MULT"))
                except Exception: pass
            if os.getenv(f"{pfx}CONFIRM_MULT"):
                try: params["confirm_mult"] = Decimal(os.getenv(f"{pfx}CONFIRM_MULT"))
                except Exception: pass
            if os.getenv(f"{pfx}SL_PCT"):
                try: params["sl_pct"] = Decimal(os.getenv(f"{pfx}SL_PCT"))
                except Exception: pass
            if os.getenv(f"{pfx}TP_PCT"):
                try: params["tp_pct"] = Decimal(os.getenv(f"{pfx}TP_PCT"))
                except Exception: pass
            if os.getenv(f"{pfx}B1_TP_MULT"):
                try: params["b1_tp_mult"] = Decimal(os.getenv(f"{pfx}B1_TP_MULT"))
                except Exception: pass
            if os.getenv(f"{pfx}B2_TP_MULT"):
                try: params["b2_tp_mult"] = Decimal(os.getenv(f"{pfx}B2_TP_MULT"))
                except Exception: pass
            if os.getenv(f"{pfx}B1_R1_TRIG"):
                try: params["b1_r1_trig"] = Decimal(os.getenv(f"{pfx}B1_R1_TRIG"))
                except Exception: pass
            if os.getenv(f"{pfx}B1_R1_SL"):
                try: params["b1_r1_sl"] = Decimal(os.getenv(f"{pfx}B1_R1_SL"))
                except Exception: pass
            if os.getenv(f"{pfx}B1_R2_TRIG"):
                try: params["b1_r2_trig"] = Decimal(os.getenv(f"{pfx}B1_R2_TRIG"))
                except Exception: pass
            if os.getenv(f"{pfx}B1_R2_SL"):
                try: params["b1_r2_sl"] = Decimal(os.getenv(f"{pfx}B1_R2_SL"))
                except Exception: pass
            if os.getenv(f"{pfx}B2_BE_CUSHION"):
                try: params["b2_be_cushion"] = Decimal(os.getenv(f"{pfx}B2_BE_CUSHION"))
                except Exception: pass
            if os.getenv(f"{pfx}B2_R2_TRIG"):
                try: params["b2_r2_trig"] = Decimal(os.getenv(f"{pfx}B2_R2_TRIG"))
                except Exception: pass
            if os.getenv(f"{pfx}B2_R2_SL"):
                try: params["b2_r2_sl"] = Decimal(os.getenv(f"{pfx}B2_R2_SL"))
                except Exception: pass
            if os.getenv(f"{pfx}EMA_FAST"):
                try: params["ema_fast"] = int(os.getenv(f"{pfx}EMA_FAST"))
                except Exception: pass
            if os.getenv(f"{pfx}EMA_SLOW"):
                try: params["ema_slow"] = int(os.getenv(f"{pfx}EMA_SLOW"))
                except Exception: pass
            if os.getenv(f"{pfx}ADX_MIN"):
                try: params["adx_min"] = Decimal(os.getenv(f"{pfx}ADX_MIN"))
                except Exception: pass
            if os.getenv(f"{pfx}ADX_PERIOD"):
                try: params["adx_period"] = int(os.getenv(f"{pfx}ADX_PERIOD"))
                except Exception: pass
            if os.getenv(f"{pfx}CANDLE_INTERVAL"):
                params["candle_interval"] = os.getenv(f"{pfx}CANDLE_INTERVAL")
            if os.getenv(f"{pfx}BE_LOCK"):
                params["be_lock"] = os.getenv(f"{pfx}BE_LOCK").lower() in ("true", "1", "yes")
            if os.getenv(f"{pfx}BE_BUFFER_PCT"):
                try: params["be_buffer_pct"] = Decimal(os.getenv(f"{pfx}BE_BUFFER_PCT"))
                except Exception: pass
            if os.getenv(f"{pfx}ASYMMETRIC"):
                params["asymmetric"] = os.getenv(f"{pfx}ASYMMETRIC").lower() in ("true", "1", "yes")
            if os.getenv(f"{pfx}HEDGE_RATIO"):
                try: params["hedge_ratio"] = Decimal(os.getenv(f"{pfx}HEDGE_RATIO"))
                except Exception: pass
            if os.getenv(f"{pfx}TIMEOUT_BARS"):
                try: params["timeout_bars"] = int(os.getenv(f"{pfx}TIMEOUT_BARS"))
                except Exception: pass

        if overrides:
            for k, v in overrides.items():
                if v is not None:
                    params[k] = v
        return cls(**params)

    @property
    def d_ratio(self) -> Decimal:
        return self.d_pct / Decimal("100")

    @property
    def sl_ratio(self) -> Decimal:
        return self.sl_pct / Decimal("100")

    @property
    def tp_ratio(self) -> Decimal:
        return self.tp_pct / Decimal("100")

    @property
    def ratchet_step_ratio(self) -> Decimal:
        return self.ratchet_step_pct / Decimal("100")

    @property
    def effective_counter_size(self) -> Decimal:
        return self.size * self.hedge_ratio

    @property
    def be_buffer_ratio(self) -> Decimal:
        return self.be_buffer_pct / Decimal("100")


@dataclass
class Config:
    # -- Credentials ----------------------------------------------------------
    api_key: str
    api_secret: str

    # -- Multi-Symbol Setup ---------------------------------------------------
    symbols: List[SymbolConfig] = field(default_factory=list)
    testnet: bool = True
    leverage: int = 10
    max_concurrent_pairs: int = 3

    # -- Cycle & Execution management -----------------------------------------
    poll_interval: int = 15             # seconds between indicator checks
    cooldown_secs: int = 0             # seconds after a pair closes before next entry
    max_cycles: int = 0                 # 0 = unlimited cycles
    dry_run: bool = False
    log_csv: str = "bybit_trades.csv"
    test_entry: bool = False
    force_entry: Optional[str] = None

    # Compatibility properties for single-symbol code
    @property
    def symbol(self) -> str:
        return self.symbols[0].symbol if self.symbols else "BTCUSDT"

    @property
    def candle_interval(self) -> str:
        return self.symbols[0].candle_interval if self.symbols else "60"

    @property
    def size(self) -> Decimal:
        return self.symbols[0].size if self.symbols else Decimal("0.001")

    @property
    def d_pct(self) -> Decimal:
        return self.symbols[0].d_pct if self.symbols else Decimal("0.80")

    @property
    def d_ratio(self) -> Decimal:
        return self.symbols[0].d_ratio if self.symbols else Decimal("0.008")

    @property
    def sl_pct(self) -> Decimal:
        return self.symbols[0].sl_pct if self.symbols else Decimal("0.38")

    @property
    def tp_pct(self) -> Decimal:
        return self.symbols[0].tp_pct if self.symbols else Decimal("1.60")

    @property
    def sl_ratio(self) -> Decimal:
        return self.symbols[0].sl_ratio if self.symbols else Decimal("0.0038")

    @property
    def tp_ratio(self) -> Decimal:
        return self.symbols[0].tp_ratio if self.symbols else Decimal("0.016")

    @property
    def ratchet_step_ratio(self) -> Decimal:
        return self.symbols[0].ratchet_step_ratio if self.symbols else Decimal("0.0025")

    @property
    def be_lock(self) -> bool:
        return self.symbols[0].be_lock if self.symbols else True

    @property
    def be_buffer_ratio(self) -> Decimal:
        return self.symbols[0].be_buffer_ratio if self.symbols else Decimal("0.002")

    @property
    def be_buffer_pct(self) -> Decimal:
        return self.symbols[0].be_buffer_pct if self.symbols else Decimal("0.20")

    @property
    def asymmetric(self) -> bool:
        return self.symbols[0].asymmetric if self.symbols else False

    @property
    def hedge_ratio(self) -> Decimal:
        return self.symbols[0].hedge_ratio if self.symbols else Decimal("0.50")

    @property
    def effective_counter_size(self) -> Decimal:
        return self.symbols[0].effective_counter_size if self.symbols else Decimal("0")

    @property
    def ema_fast(self) -> int:
        return self.symbols[0].ema_fast if self.symbols else 9

    @property
    def ema_slow(self) -> int:
        return self.symbols[0].ema_slow if self.symbols else 21

    @property
    def adx_min(self) -> Decimal:
        return self.symbols[0].adx_min if self.symbols else Decimal("0")

    @property
    def adx_period(self) -> int:
        return self.symbols[0].adx_period if self.symbols else 14

    @classmethod
    def from_args_and_env(cls) -> "Config":
        parser = argparse.ArgumentParser(
            description="Bybit Multi-Pair Concurrent Dual-Leg Hedge Bot (BTC, ETH, SOL)"
        )

        # Credentials
        parser.add_argument("--api-key", default=os.getenv("BYBIT_API_KEY", ""), help="Bybit API Key")
        parser.add_argument("--api-secret", default=os.getenv("BYBIT_API_SECRET", ""), help="Bybit API Secret")

        # Network
        parser.add_argument("--testnet", action="store_true",
                            default=os.getenv("TESTNET", "true").lower() in ("true", "1", "yes"),
                            help="Use Bybit Testnet (default)")
        parser.add_argument("--mainnet", action="store_true", help="Use Bybit Mainnet")

        # Symbols selection: e.g. --symbols AVAXUSDT,LINKUSDT,HYPEUSDT,XMRUSDT,DOGEUSDT,BTCUSDT,ETHUSDT,SOLUSDT
        parser.add_argument("--symbols", default=os.getenv("SYMBOLS", "AVAXUSDT,LINKUSDT,HYPEUSDT,XMRUSDT,DOGEUSDT,BTCUSDT,ETHUSDT,SOLUSDT"),
                            help="Comma-separated symbols to trade concurrently (default: 8 champion universe)")
        parser.add_argument("--symbol", default=os.getenv("SYMBOL", None),
                            help="Single symbol override (e.g. --symbol XAUUSDT)")

        # Global risk & sizing
        parser.add_argument("--leverage", type=int, default=int(os.getenv("LEVERAGE", "4")), help="Leverage (default: 4)")
        parser.add_argument("--max-concurrent-pairs", type=int,
                            default=int(os.getenv("MAX_CONCURRENT_PAIRS", "3")),
                            help="Max pairs trading simultaneously (default: 3)")

        # Optional universal overrides (if specified, applies to all symbols)
        parser.add_argument("--size", type=str, default=None, help="Override size per leg")
        parser.add_argument("--d-pct", type=str, default=None, help="Override displacement threshold %% (e.g. 0.80)")
        parser.add_argument("--sl-pct", type=str, default=None, help="Override Trailing SL %%")
        parser.add_argument("--tp-pct", type=str, default=None, help="Override Take Profit %%")
        parser.add_argument("--candle-interval", type=str, default=None, help="Override candle interval (e.g. 60)")
        parser.add_argument("--ema-fast", type=int, default=None, help="Override fast EMA period (default: 9)")
        parser.add_argument("--ema-slow", type=int, default=None, help="Override slow EMA period (default: 21)")
        parser.add_argument("--adx-min", type=str, default=None, help="Override minimum ADX threshold")
        parser.add_argument("--asymmetric", action="store_true", default=None, help="Enable asymmetric 100%%/30%% entry")
        parser.add_argument("--no-asymmetric", action="store_true", help="Disable asymmetric entry")
        parser.add_argument("--hedge-ratio", type=str, default=None, help="Counter-hedge ratio (default: 0.30)")
        parser.add_argument("--timeout-bars", type=int, default=None, help="Max consolidation wait bars (default: 50)")
        parser.add_argument("--be-lock", action="store_true", default=None, help="Force break-even lock")
        parser.add_argument("--no-be-lock", action="store_true", help="Disable break-even lock")

        # Cycle management
        parser.add_argument("--poll-interval", type=int, default=int(os.getenv("POLL_INTERVAL", "15")),
                            help="Seconds between scans (default: 15)")
        parser.add_argument("--cooldown-secs", type=int, default=int(os.getenv("COOLDOWN_SECS", "0")),
                            help="Cooldown seconds after each cycle closes")
        parser.add_argument("--max-cycles", type=int, default=int(os.getenv("MAX_CYCLES", "0")),
                            help="Max cycles (0 = unlimited)")

        # Misc
        parser.add_argument("--dry-run", action="store_true",
                            default=os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes"),
                            help="Simulation mode (live data, no real orders)")
        parser.add_argument("--test-entry", action="store_true",
                            default=os.getenv("TEST_ENTRY", "false").lower() in ("true", "1", "yes"),
                            help="Immediately trigger a dual-leg test entry on boot")
        parser.add_argument("--force-entry", type=str, default=os.getenv("FORCE_ENTRY", None),
                            help="Specific symbol to force entry on boot (e.g. BTCUSDT)")
        parser.add_argument("--csv", default=os.getenv("LOG_CSV", "bybit_trades.csv"), help="CSV trade log path")

        args = parser.parse_args()
        testnet = (not args.mainnet) if args.mainnet else args.testnet

        # Parse symbols list
        if args.symbol:
            raw_syms = [args.symbol]
        else:
            raw_syms = [s.strip() for s in args.symbols.split(",") if s.strip()]

        # Common overrides (only applied if explicitly passed)
        overrides: Dict[str, Any] = {}
        if args.size:
            overrides["size"] = Decimal(str(args.size))
        if args.d_pct:
            overrides["d_pct"] = Decimal(str(args.d_pct))
        if args.sl_pct:
            overrides["sl_pct"] = Decimal(str(args.sl_pct))
        if args.tp_pct:
            overrides["tp_pct"] = Decimal(str(args.tp_pct))
        if args.candle_interval:
            overrides["candle_interval"] = args.candle_interval
        if args.ema_fast:
            overrides["ema_fast"] = args.ema_fast
        if args.ema_slow:
            overrides["ema_slow"] = args.ema_slow
        if args.adx_min:
            overrides["adx_min"] = Decimal(str(args.adx_min))
        if args.no_asymmetric:
            overrides["asymmetric"] = False
        elif args.asymmetric:
            overrides["asymmetric"] = True
        if args.hedge_ratio:
            overrides["hedge_ratio"] = Decimal(str(args.hedge_ratio))
        if args.timeout_bars:
            overrides["timeout_bars"] = args.timeout_bars
        if args.no_be_lock:
            overrides["be_lock"] = False
        elif args.be_lock:
            overrides["be_lock"] = True

        symbols_list = []
        for s in raw_syms:
            sym_cfg = SymbolConfig.from_profile_or_defaults(s, overrides if overrides else None)
            symbols_list.append(sym_cfg)

        return cls(
            api_key=args.api_key.strip(),
            api_secret=args.api_secret.strip(),
            symbols=symbols_list,
            testnet=testnet,
            leverage=args.leverage,
            max_concurrent_pairs=args.max_concurrent_pairs,
            poll_interval=args.poll_interval,
            cooldown_secs=args.cooldown_secs,
            max_cycles=args.max_cycles,
            dry_run=args.dry_run,
            log_csv=args.csv,
            test_entry=args.test_entry,
            force_entry=args.force_entry,
        )
