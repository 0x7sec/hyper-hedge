import os
from pybit.unified_trading import HTTP
from dotenv import load_dotenv

load_dotenv()
session = HTTP(
    testnet=True,
    api_key=os.getenv('BYBIT_API_KEY'),
    api_secret=os.getenv('BYBIT_API_SECRET')
)

try:
    inv = session.get_positions(category='inverse', settleCoin='BTC')['result']['list']
    print('Inverse BTC:', [p for p in inv if float(p['size']) > 0])
except Exception as e:
    print('Inverse check err:', e)

try:
    coins = session.get_wallet_balance(accountType='UNIFIED')['result']['list'][0]['coin']
    for c in coins:
        eq = float(c.get('equity', 0))
        if eq > 0:
            print(f"Coin: {c.get('coin')} | Equity: {eq} | WalletBal: {c.get('walletBalance')}")
except Exception as e:
    print('Wallet check err:', e)
