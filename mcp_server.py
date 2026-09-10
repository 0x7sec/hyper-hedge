#!/usr/bin/env python3
"""
Model Context Protocol (MCP) Server for Hyper Hedge Trading & Research System.
Exposes real-time bot telemetry, trade ledgers, 1-minute sub-candle replay backtesting,
and Monte Carlo / RST statistical verification over JSON-RPC 2.0 (stdio).
"""

import sys
import os
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

# Configure logging strictly to stderr so stdout is purely JSON-RPC messages
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[%(asctime)s] [MCP] %(levelname)s: %(message)s",
)
logger = logging.getLogger("HyperHedgeMCP")

# Ensure project root is on sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    from research.replay_engine import ReplayEngine, CHAMPION_PROFILES
    from research.monte_carlo import MonteCarloEngine
    from research.optimizer import StrategyOptimizer
except ImportError as e:
    logger.error(f"Failed to import research package: {e}")
    ReplayEngine = None
    CHAMPION_PROFILES = {}
    MonteCarloEngine = None
    StrategyOptimizer = None


def get_bot_status_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Reads current daemon state from bot_state.json and runtime health."""
    state_file = os.path.join(BASE_DIR, "bot_state.json")
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            return {
                "status": "online",
                "state_file": state_file,
                "data": state,
            }
        except Exception as e:
            return {"status": "error", "message": f"Failed reading bot_state.json: {e}"}
    return {
        "status": "idle",
        "message": "bot_state.json not found. The trading daemon may not have completed an initial cycle yet.",
    }


def get_recent_trades_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Reads recent trades from local bybit_trades.csv."""
    limit = int(args.get("limit", 25))
    csv_file = os.path.join(BASE_DIR, "bybit_trades.csv")
    if not os.path.exists(csv_file):
        return {"trades": [], "count": 0, "message": "bybit_trades.csv not found"}

    trades = []
    try:
        import csv
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                trades.append(row)
        trades_slice = trades[-limit:]
        return {
            "total_count": len(trades),
            "returned_count": len(trades_slice),
            "trades": list(reversed(trades_slice)),
        }
    except Exception as e:
        return {"error": str(e)}


def run_backtest_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Runs 1-Minute Sub-Candle Replay backtest."""
    if not ReplayEngine:
        return {"error": "ReplayEngine module not loaded"}

    symbol = args.get("symbol", "BTCUSDT").upper()
    bars = int(args.get("bars", 2000))
    custom_params = {}
    for param in ["d_pct", "confirm_mult", "b1_tp_mult", "b2_tp_mult", "leverage"]:
        if param in args:
            custom_params[param] = float(args[param])

    try:
        candles = ReplayEngine.load_candles(symbol, limit=bars)
        engine = ReplayEngine(initial_capital=float(args.get("initial_capital", 1000.0)))
        res = engine.run_backtest(symbol, candles, custom_params=custom_params if custom_params else None)

        return {
            "symbol": res.symbol,
            "candles_evaluated": len(candles),
            "total_trades": res.total_trades,
            "win_rate_pct": res.win_rate,
            "shield_rate_pct": res.shield_rate,
            "net_profit_usd": res.net_profit,
            "profit_factor": res.profit_factor,
            "max_drawdown_usd": res.max_drawdown,
            "max_drawdown_pct": res.max_drawdown_pct,
            "sharpe_ratio": res.sharpe_ratio,
            "sortino_ratio": res.sortino_ratio,
            "calmar_ratio": res.calmar_ratio,
            "expectancy_usd": res.expectancy_usd,
            "cagr_pct": res.cagr_pct,
            "long_performance": {"trades": res.long_trades, "win_rate_pct": res.long_win_rate, "net_profit_usd": res.long_profit},
            "short_performance": {"trades": res.short_trades, "win_rate_pct": res.short_win_rate, "net_profit_usd": res.short_profit},
            "win_loss_ratio": res.win_loss_ratio,
            "avg_win_usd": res.avg_win,
            "avg_loss_usd": res.avg_loss,
            "max_consecutive_wins": res.max_consecutive_wins,
            "max_consecutive_losses": res.max_consecutive_losses,
            "avg_bars_held": res.avg_bars_held,
            "total_fees_usd": res.total_fees,
            "scenario_counts": res.scenario_counts,
            "recent_trades_sample": [
                {
                    "cycle_id": t.cycle_id,
                    "direction": t.direction,
                    "scenario": t.scenario,
                    "net_pnl": t.net_pnl,
                    "return_pct": t.return_pct,
                    "bars_held": t.bars_held,
                    "exhaustion_guard": t.exhaustion_guard_triggered,
                }
                for t in res.trades[-10:]
            ],
        }
    except Exception as e:
        logger.error(f"Backtest execution failed: {e}", exc_info=True)
        return {"error": str(e)}


def run_monte_carlo_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Runs 5,000-iteration Monte Carlo and 1,000-run RST permutation test."""
    if not MonteCarloEngine or not ReplayEngine:
        return {"error": "Research engines not loaded"}

    symbol = args.get("symbol", "BTCUSDT").upper()
    bars = int(args.get("bars", 2000))
    iterations = int(args.get("iterations", 5000))
    permutations = int(args.get("permutations", 1000))

    try:
        candles = ReplayEngine.load_candles(symbol, limit=bars)
        engine = ReplayEngine(initial_capital=1000.0)
        res = engine.run_backtest(symbol, candles)
        pnls = [t.net_pnl for t in res.trades]

        mc_engine = MonteCarloEngine(initial_capital=1000.0)
        mc_res = mc_engine.run_monte_carlo(pnls, iterations=iterations)
        rst_res = mc_engine.run_rst_permutation(pnls, permutations=permutations)

        return {
            "symbol": symbol,
            "trades_count": len(pnls),
            "monte_carlo": {
                "iterations": mc_res.iterations,
                "median_profit": mc_res.median_profit,
                "mean_profit": mc_res.mean_profit,
                "confidence_interval_90": mc_res.conf_interval_90,
                "percentile_5": mc_res.percentile_5,
                "percentile_50": mc_res.percentile_50,
                "percentile_95": mc_res.percentile_95,
                "prob_profit_pct": mc_res.prob_profit,
                "risk_of_ruin_pct": mc_res.risk_of_ruin,
                "median_max_dd_usd": mc_res.median_max_dd,
                "percentile_95_max_dd": mc_res.percentile_95_max_dd,
            },
            "rule_significance_test": {
                "permutations": rst_res.permutations,
                "strategy_profit": rst_res.strategy_net_profit,
                "null_mean_profit": rst_res.null_mean_profit,
                "z_score": rst_res.z_score,
                "p_value": rst_res.p_value,
                "confidence_level_pct": rst_res.confidence_level_pct,
                "is_statistically_significant": rst_res.is_significant,
            },
        }
    except Exception as e:
        logger.error(f"Monte Carlo execution failed: {e}", exc_info=True)
        return {"error": str(e)}


def run_optimization_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Runs Walk-Forward Hyperparameter Optimization adopting Jesse's in-sample/out-of-sample modeling."""
    if not StrategyOptimizer or not ReplayEngine:
        return {"error": "Optimizer engine not loaded"}

    symbol = args.get("symbol", "BTCUSDT").upper()
    bars = int(args.get("bars", 2000))
    trials = int(args.get("trials", 25))
    objective = args.get("objective", "sharpe")
    train_ratio = float(args.get("train_ratio", 0.70))

    try:
        candles = ReplayEngine.load_candles(symbol, limit=bars)
        opt = StrategyOptimizer(initial_capital=1000.0)
        res = opt.run_optimization(symbol, candles, num_trials=trials, train_ratio=train_ratio, objective=objective)

        return {
            "symbol": res.symbol,
            "trials_evaluated": res.trials_evaluated,
            "objective": res.objective,
            "train_test_split": res.train_test_split,
            "best_params": res.best_params,
            "best_fitness_score": res.best_fitness,
            "in_sample_training_metrics": res.best_training_metrics,
            "out_of_sample_testing_metrics": res.best_testing_metrics,
            "top_candidates_leaderboard": [
                {
                    "trial": t.trial_number,
                    "params": t.params,
                    "train_profit": t.training_profit,
                    "train_sharpe": t.training_sharpe,
                    "test_profit": t.testing_profit,
                    "test_sharpe": t.testing_sharpe,
                    "test_max_dd": t.testing_max_dd,
                    "fitness": t.fitness_score,
                    "is_overfit": bool(t.is_overfit),
                }
                for t in res.leaderboard[:5]
            ],
        }
    except Exception as e:
        logger.error(f"Optimization execution failed: {e}", exc_info=True)
        return {"error": str(e)}


def get_indicators_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Computes real-time EMA(9), EMA(21), ADX(14) and confirmation bands."""
    symbol = args.get("symbol", "BTCUSDT").upper()
    try:
        from research.replay_engine import calc_ema, calc_adx
        candles = ReplayEngine.load_candles(symbol, limit=100)
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]

        import numpy as np
        c_arr = np.array(closes, dtype=np.float64)
        h_arr = np.array(highs, dtype=np.float64)
        l_arr = np.array(lows, dtype=np.float64)

        ema9 = calc_ema(c_arr, 9)[-1]
        ema21 = calc_ema(c_arr, 21)[-1]
        adx14 = calc_adx(h_arr, l_arr, c_arr, 14)[-1]
        curr_px = float(closes[-1])

        prof = CHAMPION_PROFILES.get(symbol, CHAMPION_PROFILES["BTCUSDT"])
        d_val = float(prof["d_pct"]) / 100.0
        c_mult = float(prof["confirm_mult"])

        return {
            "symbol": symbol,
            "current_price": curr_px,
            "ema_fast_9": round(float(ema9), 4),
            "ema_slow_21": round(float(ema21), 4),
            "adx_14": round(float(adx14), 2),
            "trend_bias": "BULLISH" if ema9 > ema21 else "BEARISH",
            "spread_pct": round(float(abs(ema9 - ema21) / curr_px * 100.0), 3),
            "b1_bull_confirm": round(curr_px * (1.0 + c_mult * d_val), 4),
            "b2_bear_confirm": round(curr_px * (1.0 - c_mult * d_val), 4),
        }
    except Exception as e:
        return {"error": str(e)}


def get_ai_summary_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Fetches high-signal AI markdown summary."""
    try:
        import urllib.request
        pw = os.environ.get("TELEMETRY_PASSWORD", "hedge_research_telemetry_2026")
        url = f"http://localhost:8080/api/ai-summary?password={pw}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            text = resp.read().decode("utf-8")
        return {"summary_markdown": text}
    except Exception as e:
        # Fallback to local state
        state_info = get_bot_status_tool({})
        return {
            "summary_markdown": f"# Bot Summary (Local State Fallback)\nState: {json.dumps(state_info, indent=2)}\nError querying local telemetry: {e}"
        }


TOOLS_DEFINITIONS = [
    {
        "name": "hyper_hedge_get_status",
        "description": "Fetch live process health, uptime, active positions, floating PnL, margin, and current state machine phase.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "hyper_hedge_get_trades",
        "description": "Fetch recent closed trade ledger from Bybit UTA V5 and local CSV audit log.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of trades to return (default 25)"},
            },
        },
    },
    {
        "name": "hyper_hedge_run_backtest",
        "description": "Execute the high-fidelity 1-Minute Sub-Candle Replay backtest on any symbol (BTCUSDT, ETHUSDT, SOLUSDT, PAXGUSDT) with institutional metrics.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Symbol name, e.g. BTCUSDT, ETHUSDT, SOLUSDT"},
                "bars": {"type": "integer", "description": "Number of 60m macro bars to test (default 2000)"},
                "d_pct": {"type": "number", "description": "Displacement percentage (e.g. 0.80)"},
                "confirm_mult": {"type": "number", "description": "Confirmation multiplier in D (e.g. 0.80)"},
                "b1_tp_mult": {"type": "number", "description": "Branch 1 Take Profit multiplier (e.g. 2.80)"},
                "b2_tp_mult": {"type": "number", "description": "Branch 2 Take Profit multiplier (e.g. 3.50)"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "hyper_hedge_run_monte_carlo",
        "description": "Execute 5,000-path Monte Carlo bootstrap resampling and 1,000-run Rule Significance Permutation Tests (RST).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Symbol to test, e.g. BTCUSDT"},
                "bars": {"type": "integer", "description": "Number of bars to replay first"},
                "iterations": {"type": "integer", "description": "Monte Carlo iterations (default 5000)"},
                "permutations": {"type": "integer", "description": "RST permutations (default 1000)"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "hyper_hedge_get_indicators",
        "description": "Compute real-time EMA(9), EMA(21), ADX(14), and confirmation price bands for a symbol.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Symbol name (e.g. BTCUSDT)"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "hyper_hedge_run_optimization",
        "description": "Execute Walk-Forward Hyperparameter Optimization adopting Jesse's in-sample (70%) and out-of-sample (30%) validation split with sample-volume weighted fitness scoring.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Symbol to optimize, e.g. BTCUSDT"},
                "bars": {"type": "integer", "description": "Number of 60m bars to use (e.g. 2000)"},
                "trials": {"type": "integer", "description": "Number of parameter trials to evaluate (default 25)"},
                "objective": {"type": "string", "enum": ["sharpe", "profit", "calmar"], "description": "Target fitness objective"},
                "train_ratio": {"type": "number", "description": "In-sample training ratio (default 0.70)"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "hyper_hedge_ai_summary",
        "description": "Fetch high-signal clean Markdown system summary (~500 tokens) formatted for AI reasoning.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

TOOL_HANDLERS = {
    "hyper_hedge_get_status": get_bot_status_tool,
    "hyper_hedge_get_trades": get_recent_trades_tool,
    "hyper_hedge_run_backtest": run_backtest_tool,
    "hyper_hedge_run_monte_carlo": run_monte_carlo_tool,
    "hyper_hedge_run_optimization": run_optimization_tool,
    "hyper_hedge_get_indicators": get_indicators_tool,
    "hyper_hedge_ai_summary": get_ai_summary_tool,
}


def handle_request(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    msg_id = req.get("id")
    method = req.get("method")
    params = req.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": "hyper-hedge-mcp",
                    "version": "1.0.0",
                },
            },
        }

    elif method == "notifications/initialized":
        return None

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": TOOLS_DEFINITIONS,
            },
        }

    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        handler = TOOL_HANDLERS.get(tool_name)

        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Tool '{tool_name}' not found",
                },
            }

        try:
            res = handler(tool_args)
            def _json_default(obj):
                try:
                    import numpy as np
                    if isinstance(obj, (np.bool_,)):
                        return bool(obj)
                    if isinstance(obj, (np.integer,)):
                        return int(obj)
                    if isinstance(obj, (np.floating,)):
                        return float(obj)
                    if isinstance(obj, (np.ndarray,)):
                        return obj.tolist()
                except ImportError:
                    pass
                return str(obj)

            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(res, indent=2, default=_json_default),
                        }
                    ],
                    "isError": False if "error" not in res else True,
                },
            }
        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                    "isError": True,
                },
            }

    elif method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {},
        }

    else:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {
                "code": -32601,
                "message": f"Method '{method}' not recognized",
            },
        }


def main():
    logger.info("Hyper Hedge MCP Server started (stdio JSON-RPC 2.0).")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = handle_request(req)
            if resp:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
        except Exception as e:
            logger.error(f"Failed parsing message: {e}")
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
