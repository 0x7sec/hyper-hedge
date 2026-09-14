"""
state.py — Atomic JSON state manager for the Discount Buy Suite.
Tracks capital allocation, active orders, positions, and PnL for each of the 3 engines.
"""

import os
import json
import time
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional

from bybit_discount_bot.config import STATE_FILE_PATH, MAX_CAPITAL_PER_ENGINE

logger = logging.getLogger("discount_suite.state")


@dataclass
class EngineState:
    name: str
    allocated_capital: float = MAX_CAPITAL_PER_ENGINE
    current_capital: float = MAX_CAPITAL_PER_ENGINE
    total_realized_pnl: float = 0.0
    total_cycles: int = 0
    profitable_cycles: int = 0
    status: str = "IDLE"
    active_orders: List[Dict[str, Any]] = field(default_factory=list)
    active_positions: List[Dict[str, Any]] = field(default_factory=list)
    last_cycle_start: Optional[str] = None
    last_cycle_end: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SuiteState:
    updated_at: str
    dry_run: bool
    options_engine: EngineState
    spot_engine: EngineState
    neutral_engine: EngineState
    system_status: str = "RUNNING"
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class DiscountStateManager:
    """Manages thread-safe, crash-resilient atomic state on disk."""

    def __init__(self, file_path: str = STATE_FILE_PATH, dry_run: bool = False):
        self.file_path = file_path
        self.dry_run = dry_run
        self.state: SuiteState = self._load_or_initialize()

    def _default_state(self) -> SuiteState:
        now_str = datetime.now(timezone.utc).isoformat()
        return SuiteState(
            updated_at=now_str,
            dry_run=self.dry_run,
            options_engine=EngineState(name="Options Cash-Secured Put"),
            spot_engine=EngineState(name="Spot Maker Accumulator"),
            neutral_engine=EngineState(name="Delta-Hedged Market-Neutral"),
            system_status="RUNNING",
            started_at=now_str,
        )

    def _load_or_initialize(self) -> SuiteState:
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return SuiteState(
                    updated_at=data.get("updated_at", ""),
                    dry_run=data.get("dry_run", self.dry_run),
                    options_engine=EngineState(**data.get("options_engine", {})),
                    spot_engine=EngineState(**data.get("spot_engine", {})),
                    neutral_engine=EngineState(**data.get("neutral_engine", {})),
                    system_status=data.get("system_status", "RUNNING"),
                    started_at=data.get("started_at") or data.get("session_start_iso") or datetime.now(timezone.utc).isoformat(),
                )
            except Exception as e:
                logger.warning(f"Could not load state file {self.file_path} ({e}), initializing fresh state.")

        fresh = self._default_state()
        self._atomic_save(fresh)
        return fresh

    def _atomic_save(self, state: SuiteState):
        """Atomically persist state to disk via temp file swap."""
        state.updated_at = datetime.now(timezone.utc).isoformat()
        temp_path = f"{self.file_path}.tmp"
        try:
            dirname = os.path.dirname(self.file_path)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(asdict(state), f, indent=2)
            os.replace(temp_path, self.file_path)
        except Exception as e:
            logger.error(f"Failed to atomically save discount state: {e}")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def save(self):
        """Public save method."""
        self._atomic_save(self.state)

    def get_summary_dict(self) -> Dict[str, Any]:
        """Convert state to clean dict for API / telemetry responses."""
        return asdict(self.state)
