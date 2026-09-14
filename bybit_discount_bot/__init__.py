"""
bybit_discount_bot — Autonomous Bybit UTA Discount Buy Suite.
Includes 3 independent $1,000-budget execution engines:
  1. Options Cash-Secured Put Underwriter (Bybit Options)
  2. Spot Maker Limit Accumulator (Bybit Spot)
  3. Delta-Hedged Market-Neutral Harvester (Spot + Linear Perp Short)
"""

__version__ = "1.0.0"
