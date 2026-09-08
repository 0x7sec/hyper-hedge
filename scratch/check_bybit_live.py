import os
from pybit.unified_trading import HTTP
from dotenv import load_dotenv

load_dotenv()
session = HTTP(
    testnet=True,
    api_key=os.getenv('BYBIT_API_KEY'),
    api_secret=os.getenv('BYBIT_API_SECRET')
)

print('=== OPEN POSITIONS ===')
pos = session.get_positions(category='linear', settleCoin='USDT')['result']['list']
open_pos = [p for p in pos if float(p['size']) > 0]
for p in open_pos:
    print(f"{p['symbol']} | Side: {p['side']} | Idx: {p['positionIdx']} | Size: {p['size']} | Entry: {p['avgPrice']} | Liq: {p['liqPrice']}")

print('\n=== OPEN / CONDITIONAL ORDERS ===')
orders = session.get_open_orders(category='linear', settleCoin='USDT')['result']['list']
for o in orders:
    print(f"{o['symbol']} | Side: {o['side']} | Type: {o['orderType']} | Qty: {o['qty']} | Price: {o.get('price')} | Trigger: {o.get('triggerPrice')} | Status: {o['orderStatus']}")

print('\n=== RECENT EXECUTIONS (LAST 10) ===')
execs = session.get_executions(category='linear', limit=10)['result']['list']
for e in execs:
    print(f"{e['symbol']} | Side: {e['side']} | Qty: {e['execQty']} | Price: {e['execPrice']} | Time: {e['execTime']}")
