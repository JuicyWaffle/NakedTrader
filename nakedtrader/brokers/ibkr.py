"""
ibkr.py — Interactive Brokers connector via ib_async.

Verbinding met Interactive Brokers TWS of IB Gateway.
Ondersteunt aandelen, ETF, forex en futures via bracket orders.
"""

import logging
from typing import Optional

log = logging.getLogger(__name__)


class IBKRBroker:
    """
    Verbinding met Interactive Brokers via ib_async.
    pip install ib_async
    TWS of IB Gateway moet draaien op de achtergrond.
    """

    def __init__(self, config):
        self.config = config
        self.ib = None

    def connect(self):
        from ib_async import IB
        self.ib = IB()
        self.ib.connect(
            self.config.ibkr_host,
            self.config.ibkr_port,
            clientId=self.config.ibkr_client_id,
        )
        log.info(f"IBKR verbonden  {self.config.ibkr_host}:{self.config.ibkr_port}")

    def disconnect(self):
        if self.ib and self.ib.isConnected():
            self.ib.disconnect()
            log.info("IBKR verbinding gesloten")

    def get_price(
        self,
        symbol: str,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> Optional[float]:
        from ib_async import Stock
        contract = Stock(symbol, exchange, currency)
        self.ib.qualifyContracts(contract)
        ticker = self.ib.reqMktData(contract)
        self.ib.sleep(1)
        return ticker.last or ticker.close

    def place_bracket_order(
        self,
        symbol: str,
        quantity: int,
        entry_price: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        exchange: str = "SMART",
        currency: str = "USD",
    ):
        """Bracket order: entry + stop-loss + take-profit in één keer."""
        from ib_async import Stock
        contract = Stock(symbol, exchange, currency)
        self.ib.qualifyContracts(contract)

        stop_price = round(entry_price * (1 - stop_loss_pct), 2)
        profit_price = round(entry_price * (1 + take_profit_pct), 2)

        bracket = self.ib.bracketOrder(
            action="BUY",
            quantity=quantity,
            limitPrice=entry_price,
            takeProfitPrice=profit_price,
            stopLossPrice=stop_price,
        )
        for order in bracket:
            self.ib.placeOrder(contract, order)

        log.info(
            f"IBKR bracket: {symbol} qty={quantity}  "
            f"entry={entry_price}  SL={stop_price}  TP={profit_price}"
        )
        return bracket

    def get_portfolio(self) -> list:
        return self.ib.portfolio()

    def get_stock_bars(
        self,
        symbol: str,
        duration: str = "1 Y",
        bar_size: str = "1 day",
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> list:
        """
        Haal historische bars op voor aandelen/ETFs (Stock contracts).
        Gebruikt voor SPY, TLT, GLD etc. — regime detectie.
        """
        from ib_async import Stock
        contract = Stock(symbol, exchange, currency)
        self.ib.qualifyContracts(contract)
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )
        log.info(f"IBKR stock bars: {symbol}  {len(bars)} bars ({bar_size})")
        return bars

    def get_historical_bars(
        self,
        symbol: str,
        duration: str = "60 D",
        bar_size: str = "1 day",
        what_to_show: str = "MIDPOINT",
        exchange: str = "IDEALPRO",
        currency: str = "USD",
    ) -> list:
        """
        Haal historische bars op via IB Gateway.

        Args:
            symbol: bv. "EUR" (voor EURUSD forex)
            duration: bv. "60 D", "1 Y"
            bar_size: bv. "1 day", "1 hour", "15 mins"
            what_to_show: "MIDPOINT", "TRADES", "BID", "ASK"
            exchange: "IDEALPRO" voor forex, "SMART" voor aandelen
            currency: tegenvaluta

        Returns:
            list van BarData objecten met .date, .open, .high, .low, .close, .volume
        """
        from ib_async import Forex
        contract = Forex(symbol + currency)
        self.ib.qualifyContracts(contract)
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=True,
            formatDate=1,
        )
        log.info(f"IBKR historisch: {symbol}{currency}  {len(bars)} bars ({bar_size})")
        return bars
