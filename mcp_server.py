#!/usr/bin/env python3
"""
Model Context Protocol (MCP) Server for Bybit Algorithmic Trading System.
Exposes real-time bot telemetry, trade ledgers, live indicators,
and AI-optimized markdown summaries over JSON-RPC 2.0 (stdio).
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
    from bybit_bot.config import DEFAULT_PROFILES
except ImportError:
    DEFAULT_PROFILES = {}


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


def get_indicators_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Fetches or computes real-time EMA(9), EMA(21), ADX(14) for a symbol."""
    symbol = args.get("symbol", "BTCUSDT").upper()
    state_file = os.path.join(BASE_DIR, "bot_state.json")
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            pair_data = state.get("pairs", {}).get(symbol, {})
            if pair_data:
                fast_e = pair_data.get("fast_ema")
                slow_e = pair_data.get("slow_ema")
                adx = pair_data.get("adx")
                px = pair_data.get("latest_price")
                return {
                    "symbol": symbol,
                    "status": pair_data.get("status", "SCANNING"),
                    "current_price": px,
                    "ema_fast_9": fast_e,
                    "ema_slow_21": slow_e,
                    "adx_14": adx,
                    "trend_bias": "BULLISH" if (fast_e and slow_e and fast_e > slow_e) else "BEARISH",
                    "source": "bot_state.json (live runtime)",
                }
        except Exception as e:
            logger.debug(f"Indicators reading from state error: {e}")

    # Fallback to defaults
    prof = DEFAULT_PROFILES.get(symbol, {})
    return {
        "symbol": symbol,
        "status": "SCANNING",
        "profile_defaults": {
            "candle_interval": prof.get("candle_interval", "60"),
            "ema_fast": prof.get("ema_fast", 9),
            "ema_slow": prof.get("ema_slow", 21),
            "adx_min": float(prof.get("adx_min", 20)),
        },
        "message": "Live values available while trading daemon is running.",
    }


def get_ai_summary_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Fetches high-signal AI markdown summary from local telemetry server."""
    try:
        import urllib.request
        pw = os.environ.get("TELEMETRY_PASSWORD", "hedge_bot_sec_2026")
        port = os.environ.get("PORT", "8080")
        url = f"http://localhost:{port}/api/ai-summary?password={pw}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            text = resp.read().decode("utf-8")
        return {"summary_markdown": text}
    except Exception as e:
        # Fallback to local state
        state_info = get_bot_status_tool({})
        return {
            "summary_markdown": f"# Bot Summary (Local State Fallback)\nState: {json.dumps(state_info, indent=2)}\nNotice: Telemetry server query returned {e}"
        }


TOOLS_DEFINITIONS = [
    {
        "name": "hyper_hedge_get_status",
        "description": "Fetch live process health, uptime, active single-leg positions, floating PnL, margin, and current state machine phase.",
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
        "name": "hyper_hedge_get_indicators",
        "description": "Fetch real-time EMA(9), EMA(21), and ADX(14) indicator values and trend bias for any monitored symbol.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Symbol name (e.g. AVAXUSDT, BTCUSDT)"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "hyper_hedge_ai_summary",
        "description": "Fetch high-signal clean Markdown system summary (~500 tokens) formatted for AI quantitative reasoning.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

TOOL_HANDLERS = {
    "hyper_hedge_get_status": get_bot_status_tool,
    "hyper_hedge_get_trades": get_recent_trades_tool,
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
                    "version": "2.0.0",
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
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(res, indent=2),
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
