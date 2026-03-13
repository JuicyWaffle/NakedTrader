"""
binance.py — Binance crypto exchange connector via python-binance.

Optioneel alternatief voor Kraken. Ondersteunt market orders en OCO
(one-cancels-the-other) orders voor stop-loss + take-profit.
"""

import logging
from typing import Optional

log = logging.getLogger(__name__)


class BinanceBroker:
    """
    Verbinding met Binance via python-binance.
    pip install python-binance
    """

    def __init__(self, config):
        self.config = config
        self.client = None

    def connect(self):
        from binance.client import Client
        self.client = Client(
            self.config.binance_api_key,
            self.config.binance_api_secret,
            testnet=self.config.binance_testnet,
        )
        log.info(f"Binance verbonden  testnet={self.config.binance_testnet}")

    def get_price(self, symbol: str) -> float:
        ticker = self.client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])

    def place_market_order(self, symbol: str, side: str, quantity: float):
        from binance.enums import ORDER_TYPE_MARKET
        return self.client.create_order(
            symbol=symbol,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=quantity,
        )

    def place_oco_order(
        self,
        symbol: str,
        quantity: float,
        stop_price: float,
        take_profit_price: float,
    ):
        """OCO: stop-loss + take-profit, één annuleert de andere."""
        return self.client.create_oco_order(
            symbol=symbol,
            side="SELL",
            quantity=quantity,
            price=str(take_profit_price),
            stopPrice=str(stop_price),
            stopLimitPrice=str(round(stop_price * 0.99, 2)),
            stopLimitTimeInForce="GTC",
        )

    def get_balances(self) -> dict:
        account = self.client.get_account()
        return {
            b["asset"]: float(b["free"])
            for b in account["balances"]
            if float(b["free"]) > 0
        }
