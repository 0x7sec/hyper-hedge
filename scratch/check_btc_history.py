import os
from datetime import datetime
from pybit.unified_trading import HTTP
from dotenv import load_dotenv

load_dotenv()
session = HTTP(
    testnet=True,
    api_key=os.getenv('BYBIT_API_KEY'),
    api_secret=os.getenv('BYBIT_API_SECRET')
)

print('=== RECENT BTCUSDT ORDERS ===')
orders = session.get_order_history(category='linear', symbol='BTCUSDT', limit=20)['result']['list']
for o in orders:
    t = datetime.fromtimestamp(int(o['updatedTime'])/1000).strftime('%Y-%m-%d %H:%M:%S')
    print(f"{t} | Side: {o['side']:<4} | Type: {o['orderType']:<6} | Qty: {o['qty']:<6} | Price: {o.get('price'):<8} | Trigger: {o.get('triggerPrice')} | Status: {o['orderStatus']:<10} | Reject: {o.get('rejectReason')}")

print('\n=== RECENT BTCUSDT CLOSED PNL ===')
pnl = session.get_closed_pnl(category='linear', symbol='BTCUSDT', limit=10)['result']['list']
for p in pnl:
    t = datetime.fromtimestamp(int(p['updatedTime'])/1000).strftime('%Y-%m-%d %H:%M:%S')
    print(f"{t} | Side: {p['side']:<4} | Qty: {p['qty']:<6} | Entry: {p['avgEntryPrice']} | Exit: {p['avgExitPrice']} | PnL: {p['closedPnl']}")
