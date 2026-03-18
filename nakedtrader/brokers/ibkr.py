"""
ibkr.py — Interactive Brokers connector via ib_async.

Verbinding met Interactive Brokers TWS of IB Gateway.
Ondersteunt aandelen, ETF, forex en futures via bracket orders.
"""

import logging
from typing import Optional

from nakedtrader.brokers.base import AbstractBroker
from nakedtrader.types import TradeSignal
from nakedtrader.exceptions import IBKRError, BrokerConnectionError, BrokerOrderError

log = logging.getLogger(__name__)


class IBKRBroker(AbstractBroker):
    """
    Verbinding met Interactive Brokers via ib_async.
    pip install ib_async
    TWS of IB Gateway moet draaien op de achtergrond.
    """

    def __init__(self, config):
        super().__init__(config)
        self.ib = None
        self._connected = False

    def connect(self) -> bool:
        """Maak verbinding met de broker API. Retourneert True bij succes."""
        try:
            from ib_async import IB
            self.ib = IB()
            self.ib.connect(
                self.config.ibkr_host,
                self.config.ibkr_port,
                clientId=self.config.ibkr_client_id,
            )
            self._connected = True
            log.info(f"IBKR verbonden {self.config.ibkr_host}:{self.config.ibkr_port}")
            return True
        except (IBKRError, ConnectionError, OSError, TimeoutError) as e:
            log.error(f"Kan niet verbinden met {self.config.ibkr_host}:{self.config.ibkr_port}: {e}")
            self._connected = False
            return False

    def disconnect(self):
        """Sluit de verbinding met de broker API."""
        if self.ib and self.ib.isConnected():
            self.ib.disconnect()
            self._connected = False
            log.info("IBKR verbinding gesloten")

    def get_balance(self) -> float:
        """Retourneert het vrije saldo in de base currency (EUR)."""
        if not self._connected:
            return 0.0
        
        # Zoek naar TotalCashBalance in de account summary
        # Dit is een vereenvoudiging; in productie moet je de juiste currency en account checken
        try:
            summary = self.ib.accountSummary()
            for item in summary:
                if item.tag == 'TotalCashValue' and item.currency == 'EUR':
                    return float(item.value)
        except (IBKRError, ConnectionError, ValueError) as e:
            log.error(f"Fout bij ophalen balans: {e}")
        
        return 0.0

    def get_positions(self) -> dict[str, float]:
        """Retourneert open posities als {symbol: size_eur}."""
        if not self._connected:
            return {}
        
        positions = {}
        try:
            # ib.positions() retourneert een lijst van Position objecten
            # Position(account='...', contract=Contract(...), position=100.0, avgCost=150.0)
            for pos in self.ib.positions():
                if pos.position != 0:
                    symbol = pos.contract.symbol
                    market_value = pos.position * pos.avgCost # Benadering, beter is marketPrice
                    positions[symbol] = market_value
        except (IBKRError, ConnectionError, AttributeError) as e:
            log.error(f"Fout bij ophalen posities: {e}")
            
        return positions

    def place_order(
        self,
        signal: TradeSignal,
        size_eur: float,
        sl_pct: float,
        tp_pct: float
    ) -> Optional[dict]:
        """Plaats een entry order met gekoppelde SL/TP."""
        if not self._connected:
            log.error("IBKR niet verbonden, kan geen order plaatsen")
            return None

        try:
            from ib_async import Stock, Forex, Order
            
            # Bepaal contract type (vereenvoudigd)
            symbol = signal.symbol
            if len(symbol) == 6 and symbol.isupper() and "USD" in symbol: # Forex gok
                 contract = Forex(symbol)
            else:
                 contract = Stock(symbol, "SMART", "USD") # Default naar US stocks
            
            self.ib.qualifyContracts(contract)
            
            # Bereken quantity op basis van huidige prijs (indien beschikbaar)
            # Voor nu gebruiken we de signal.current_price
            price = signal.current_price
            if price <= 0:
                 log.error(f"Ongeldige prijs {price} voor {symbol}")
                 return None
            
            quantity = int(size_eur / price)
            if quantity < 1:
                 log.warning(f"Quantity {quantity} te klein voor {symbol} (size={size_eur}, price={price})")
                 return None

            action = "BUY" if signal.direction == "long" else "SELL"
            
            # Bracket logic
            stop_price = round(price * (1 - sl_pct), 2)
            profit_price = round(price * (1 + tp_pct), 2)

            # Hoofdorder
            parent = Order()
            parent.orderId = self.ib.client.getReqId()
            parent.action = action
            parent.orderType = "LMT"
            parent.totalQuantity = quantity
            parent.lmtPrice = price
            parent.transmit = False

            # Take Profit
            tp_order = Order()
            tp_order.orderId = self.ib.client.getReqId()
            tp_order.action = "SELL" if action == "BUY" else "BUY"
            tp_order.orderType = "LMT"
            tp_order.totalQuantity = quantity
            tp_order.lmtPrice = profit_price
            tp_order.parentId = parent.orderId
            tp_order.transmit = False

            # Stop Loss
            sl_order = Order()
            sl_order.orderId = self.ib.client.getReqId()
            sl_order.action = "SELL" if action == "BUY" else "BUY"
            sl_order.orderType = "STP"
            sl_order.auxPrice = stop_price
            sl_order.totalQuantity = quantity
            sl_order.parentId = parent.orderId
            sl_order.transmit = True

            bracket = [parent, tp_order, sl_order]
            for o in bracket:
                self.ib.placeOrder(contract, o)

            log.info(
                f"IBKR bracket geplaatst: {symbol} {action} {quantity} @ {price} "
                f"(SL={stop_price}, TP={profit_price})"
            )
            
            return {
                "id": parent.orderId,
                "symbol": symbol,
                "quantity": quantity,
                "price": price,
                "sl": stop_price,
                "tp": profit_price
            }

        except (IBKRError, ConnectionError, ValueError, AttributeError) as e:
            log.error(f"Fout bij plaatsen order {symbol}: {e}")
            return None

    @property
    def is_connected(self) -> bool:
        """Controleer of IBKR verbinding actief is."""
        return self._connected and self.ib is not None and self.ib.isConnected()

    def get_stock_bars(
        self,
        symbol: str,
        duration: str = "1 Y",
        bar_size: str = "1 day",
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> list:
        """Haal historische bars op voor aandelen/ETFs via IBKR.

        Returns:
            Lijst van BarData objecten met .open, .high, .low, .close, .volume.
            Leeg bij fout.
        """
        if not self.is_connected:
            return []
        try:
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
            return bars or []
        except (IBKRError, ConnectionError, OSError, TimeoutError, ValueError) as e:
            log.warning("get_stock_bars %s mislukt: %s", symbol, e)
            return []

    def get_historical_bars(
        self,
        symbol: str,
        duration: str = "1 Y",
        bar_size: str = "1 day",
        what_to_show: str = "MIDPOINT",
        currency: str = "USD",
    ) -> list:
        """Haal historische bars op voor forex via IBKR.

        Returns:
            Lijst van BarData objecten. Leeg bij fout.
        """
        if not self.is_connected:
            return []
        try:
            from ib_async import Forex
            contract = Forex(symbol)
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
            return bars or []
        except (IBKRError, ConnectionError, OSError, TimeoutError, ValueError) as e:
            log.warning("get_historical_bars %s mislukt: %s", symbol, e)
            return []

    def get_stock_price(self, symbol: str, currency: str = "USD") -> float:
        """Haal huidige prijs op voor een aandeel/ETF. Retourneert 0.0 bij fout."""
        if not self.is_connected:
            return 0.0
        try:
            from ib_async import Stock, util
            contract = Stock(symbol, "SMART", currency)
            self.ib.qualifyContracts(contract)
            ticker = self.ib.reqMktData(contract, "", False, False)
            util.sleep(2)
            price = ticker.marketPrice()
            self.ib.cancelMktData(contract)
            return float(price) if price and price == price else 0.0  # NaN check
        except (IBKRError, ConnectionError, OSError, TimeoutError, ValueError) as e:
            log.warning("get_stock_price %s mislukt: %s", symbol, e)
            return 0.0

    def get_price(self, symbol: str) -> Optional[float]:
        """Legacy price method."""
        p = self.get_stock_price(symbol)
        return p if p > 0 else None
