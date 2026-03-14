"""
binance.py — Binance crypto exchange connector via python-binance.

Optioneel alternatief voor Kraken. Ondersteunt market orders en OCO
(one-cancels-the-other) orders voor stop-loss + take-profit.
"""

import logging
from typing import Optional

from nakedtrader.brokers.base import AbstractBroker
from nakedtrader.types import TradeSignal

log = logging.getLogger(__name__)


class BinanceBroker(AbstractBroker):
    """
    Verbinding met Binance via python-binance.
    pip install python-binance
    """

    def __init__(self, config):
        super().__init__(config)
        self.client = None
        self._connected = False

    def connect(self) -> bool:
        """Maak verbinding met de broker API. Retourneert True bij succes."""
        try:
            from binance.client import Client
            self.client = Client(
                self.config.binance_api_key,
                self.config.binance_api_secret,
                testnet=self.config.binance_testnet,
            )
            # Test verbinding
            self.client.ping()
            self._connected = True
            log.info(f"Binance verbonden (testnet={self.config.binance_testnet})")
            return True
        except Exception as e:
            log.error(f"Kan niet verbinden met Binance: {e}")
            self._connected = False
            return False

    def disconnect(self):
        """Sluit de verbinding met de broker API."""
        if self.client:
            self.client.close_connection() # python-binance heeft geen expliciete disconnect, maar wel close
            self._connected = False
            log.info("Binance verbinding gesloten")

    def get_balance(self) -> float:
        """Retourneert het vrije saldo in EUR (USDT als proxy in crypto-land soms handiger, maar we houden EUR aan)."""
        if not self._connected:
            return 0.0
        
        try:
            # We zoeken naar EUR of USDT afhankelijk van de strategie, maar interface eist EUR
            # Voor nu pakken we EUR
            asset_balance = self.client.get_asset_balance(asset='EUR')
            if asset_balance:
                return float(asset_balance['free'])
            
            # Fallback: USDT → EUR conversie
            usdt_balance = self.client.get_asset_balance(asset='USDT')
            if usdt_balance:
                usdt_free = float(usdt_balance['free'])
                try:
                    ticker = self.client.get_symbol_ticker(symbol='EURUSDT')
                    eur_rate = float(ticker['price'])  # EUR per USDT
                    return usdt_free / eur_rate
                except Exception:
                    log.warning("EURUSDT ticker niet beschikbaar, schatting 1.08 gebruikt")
                    return usdt_free / 1.08

        except Exception as e:
            log.error(f"Fout bij ophalen Binance balans: {e}")
            
        return 0.0

    def get_positions(self) -> dict[str, float]:
        """Retourneert open posities als {symbol: size_eur}."""
        if not self._connected:
            return {}

        positions = {}
        try:
            account = self.client.get_account()
            balances = [b for b in account["balances"] if float(b["free"]) > 0 or float(b["locked"]) > 0]
            
            # Haal prijzen op voor alle assets
            tickers = self.client.get_all_tickers()
            price_map = {t['symbol']: float(t['price']) for t in tickers}
            
            for b in balances:
                asset = b['asset']
                if asset in ['EUR', 'USDT', 'BUSD']: continue # Valuta overslaan
                
                amount = float(b['free']) + float(b['locked'])
                
                # Probeer prijs te vinden. Eerst EUR paar, dan USDT
                pair_eur = f"{asset}EUR"
                pair_usdt = f"{asset}USDT"
                
                price = 0.0
                symbol = ""
                
                if pair_eur in price_map:
                    price = price_map[pair_eur]
                    symbol = pair_eur
                elif pair_usdt in price_map:
                     # Converteer USDT prijs naar EUR (ongeveer)
                     eur_usdt = price_map.get("EURUSDT", 1.08) # Fallback 1.08
                     price = price_map[pair_usdt] / eur_usdt
                     symbol = pair_usdt # We gebruiken het USDT paar als symbool als fallback
                
                if price > 0:
                    positions[symbol] = amount * price

        except Exception as e:
             log.error(f"Fout bij ophalen Binance posities: {e}")
             
        return positions

    def place_order(
        self,
        signal: TradeSignal,
        size_eur: float,
        sl_pct: float,
        tp_pct: float
    ) -> Optional[dict]:
        """
        Plaats een entry order met OCO voor SL/TP.
        Binance ondersteunt OCO orders, maar die combineren een limit maker en stop loss.
        Hier willen we eerst BUYMARKET en dan OCO SELL.
        """
        if not self._connected:
            return None

        symbol = signal.symbol
        side = "BUY" if signal.direction == "long" else "SELL"
        if side == "SELL":
            log.warning("Shorting op Binance Spot niet ondersteund in deze implementatie")
            return None

        try:
            # 1. Haal prijs op
            ticker = self.client.get_symbol_ticker(symbol=symbol)
            price = float(ticker['price'])
            
            quantity = size_eur / price
            # Precisie correctie nodig voor Binance (LOT_SIZE filter)
            # Voor nu ronden we af op 5 decimalen, in productie moet je exchange info ophalen
            quantity = round(quantity, 5) 

            # 2. Market Buy
            from binance.enums import ORDER_TYPE_MARKET
            order = self.client.create_order(
                symbol=symbol,
                side=side,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            log.info(f"Binance Market Buy {symbol}: {order['orderId']}")
            
            # 3. OCO Sell (Stop Loss + Take Profit)
            # OCO limit = TP prijs
            # OCO stop = SL trigger prijs
            # OCO stopLimit = SL execution prijs (iets lager voor gegarandeerde fill)
            
            tp_price = round(price * (1 + tp_pct), 2)
            stop_price = round(price * (1 - sl_pct), 2)
            stop_limit = round(stop_price * 0.995, 2)

            oco = self.client.create_oco_order(
                symbol=symbol,
                side="SELL",
                quantity=quantity,
                price=str(tp_price),
                stopPrice=str(stop_price),
                stopLimitPrice=str(stop_limit),
                stopLimitTimeInForce="GTC"
            )
            log.info(f"Binance OCO geplaatst: TP={tp_price}, SL={stop_price}")

            return {
                "id": order['orderId'],
                "symbol": symbol,
                "quantity": quantity,
                "price": price,
                "oco_id": oco['orderListId']
            }

        except Exception as e:
            log.error(f"Fout bij plaatsen Binance order {symbol}: {e}")
            return None
