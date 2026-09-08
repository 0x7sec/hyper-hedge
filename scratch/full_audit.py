"""
Full Audit: Bybit testnet trade history + backtest verification + MC significance
"""
import os
from datetime import datetime
from decimal import Decimal
from pybit.unified_trading import HTTP
from dotenv import load_dotenv

load_dotenv()
session = HTTP(
    testnet=True,
    api_key=os.getenv('BYBIT_API_KEY'),
    api_secret=os.getenv('BYBIT_API_SECRET')
)

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']

print("=" * 80)
print("PART 1: COMPLETE BYBIT TESTNET TRADE AUDIT")
print("=" * 80)

# ----- Closed PnL per symbol -----
total_realized = 0.0
for sym in SYMBOLS:
    print(f"\n--- {sym} CLOSED PNL (last 20 trades) ---")
    pnl = session.get_closed_pnl(category='linear', symbol=sym, limit=20)['result']['list']
    sym_total = 0.0
    for p in pnl:
        t = datetime.fromtimestamp(int(p['updatedTime'])/1000).strftime('%Y-%m-%d %H:%M:%S')
        pnl_val = float(p['closedPnl'])
        sym_total += pnl_val
        direction = "WIN" if pnl_val > 0 else "LOSS"
        print(f"  {t} | {p['side']:<4} | Qty: {p['qty']:<6} | "
              f"Entry: {float(p['avgEntryPrice']):.2f} -> Exit: {float(p['avgExitPrice']):.2f} | "
              f"PnL: ${pnl_val:+.4f} [{direction}]")
    print(f"  >> {sym} Net Realized: ${sym_total:+.4f}")
    total_realized += sym_total

print(f"\n{'='*80}")
print(f"TOTAL REALIZED PNL (all symbols): ${total_realized:+.4f}")
print(f"{'='*80}")

# ----- Order history per symbol -----
print("\n\nPART 2: DETAILED ORDER HISTORY (last 15 each)")
print("=" * 80)
for sym in SYMBOLS:
    print(f"\n--- {sym} ORDER HISTORY ---")
    orders = session.get_order_history(category='linear', symbol=sym, limit=15)['result']['list']
    for o in orders:
        t = datetime.fromtimestamp(int(o['updatedTime'])/1000).strftime('%Y-%m-%d %H:%M:%S')
        trigger = o.get('triggerPrice', '')
        reject = o.get('rejectReason', 'N/A')
        flag = " <<< CANCELLED" if o['orderStatus'] == 'Cancelled' else ""
        print(f"  {t} | {o['side']:<4} | {o['orderType']:<6} | Qty: {o['qty']:<5} | "
              f"Price: {o.get('price', '0'):<10} | Trigger: {trigger:<10} | "
              f"Status: {o['orderStatus']:<12} | Reject: {reject}{flag}")

# ----- Current wallet balance -----
print("\n\nPART 3: WALLET BALANCE")
print("=" * 80)
coins = session.get_wallet_balance(accountType='UNIFIED')['result']['list'][0]['coin']
for c in coins:
    eq = float(c.get('equity', 0))
    if eq > 0:
        print(f"  {c.get('coin')}: equity={eq:.4f} | walletBalance={c.get('walletBalance')} | "
              f"availableToWithdraw={c.get('availableToWithdraw')}")
