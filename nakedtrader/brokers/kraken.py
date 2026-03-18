"""
kraken.py — Kraken crypto exchange connector via krakenex.

Standaard crypto broker voor spot orders met stop-loss/take-profit.
Kraken symboolnotatie: XXBTZEUR (BTC/EUR), XETHZEUR (ETH/EUR), XBTUSDT, etc.
"""

import logging
from typing import Optional

from nakedtrader.brokers.base import AbstractBroker
from nakedtrader.types import TradeSignal
from nakedtrader.exceptions import KrakenError, BrokerConnectionError, BrokerOrderError

log = logging.getLogger(__name__)


class KrakenBroker(AbstractBroker):
    """
    Verbinding met Kraken via krakenex.
    """

    def __init__(self, config):
        super().__init__(config)
        self.api = None
        self._connected = False

    def _check_errors(self, response, context="Kraken"):
        """Controleer op API fouten en gooi KrakenError als nodig."""
        if not response:
             raise KrakenError(f"{context}: Lege response")
        errors = response.get("error", [])
        if errors:
            raise KrakenError(f"{context}: {errors}")

    def connect(self) -> bool:
        """Maak verbinding met de broker API. Retourneert True bij succes."""
        try:
            import krakenex
            self.api = krakenex.API(
                key=self.config.kraken_api_key,
                secret=self.config.kraken_api_secret,
            )
            # Verbinding testen via accountbalans
            response = self.api.query_private("Balance")
            self._check_errors(response, "Kraken connect")
            self._connected = True
            log.info(f"Kraken verbonden. Assets: {list(response.get('result', {}).keys())}")
            return True
        except (KrakenError, ConnectionError, OSError) as e:
            log.error(f"Kraken verbinding mislukt: {e}")
            self._connected = False
            return False

    def disconnect(self):
        """Sluit de verbinding."""
        self.api = None
        self._connected = False
        log.info("Kraken verbinding gesloten")

    def get_balance(self) -> float:
        """Retourneert het vrije saldo in EUR (ZEUR in Kraken-taal)."""
        if not self._connected:
            return 0.0
        
        try:
            response = self.api.query_private("Balance")
            self._check_errors(response, "Kraken balance")
            # ZEUR is Kraken code voor EUR
            balance = float(response["result"].get("ZEUR", 0.0))
            return balance
        except (KrakenError, ConnectionError, KeyError) as e:
            log.error(f"Fout bij ophalen Kraken balans: {e}")
            return 0.0

    def get_positions(self) -> dict[str, float]:
        """
        Retourneert open posities als {symbol: size_eur}.
        Dit is complexer bij Kraken omdat we crypto hebben, geen 'positions' zoals bij futures.
        We nemen de balans van alle crypto assets en vermenigvuldigen met huidige prijs in EUR.
        """
        if not self._connected:
            return {}

        positions = {}
        try:
            # 1. Haal balances
            bal_resp = self.api.query_private("Balance")
            self._check_errors(bal_resp, "Kraken positions")
            balances = bal_resp["result"]

            # Filter ZEUR en KFEE (Kraken fee credits) eruit
            crypto_assets = {k: float(v) for k, v in balances.items() if k not in ["ZEUR", "KFEE"] and float(v) > 0}

            if not crypto_assets:
                return {}

            # 2. Haal prijzen op voor deze assets (in EUR)
            # Kraken pairs: XXBTZEUR, XETHZEUR, etc.
            pairs = []
            asset_map = {} # map "XXBT" -> "XXBTZEUR"
            
            for asset in crypto_assets:
                # Eenvoudige gok voor pair naam: ASSET + ZEUR
                # Werkt voor XXBT -> XXBTZEUR, XETH -> XETHZEUR
                pair = f"{asset}ZEUR"
                pairs.append(pair)
                asset_map[asset] = pair

            if not pairs:
                 return {}

            # Ticker info ophalen voor alle pairs in 1 call
            ticker_resp = self.api.query_public("Ticker", {"pair": ",".join(pairs)})
            # Fouten in ticker (bv ongeldig pair) kunnen optreden, maar query_public gooit meestal error voor hele batch
            # We negeren errors hier even voor robuustheid, of pakken wat lukt
            
            if ticker_resp.get("error"):
                 log.warning(f"Kraken ticker error (mogelijk ongeldig pair): {ticker_resp['error']}")
                 # Fallback: probeer 1 voor 1 of skip
                 return {} # Voor nu simpel

            ticker_data = ticker_resp["result"]
            
            for asset, amount in crypto_assets.items():
                pair = asset_map.get(asset)
                # Soms geeft kraken een andere key terug in ticker (bv XXBTZEUR -> XXBTZEUR)
                # We zoeken in ticker_data keys
                matching_key = next((k for k in ticker_data if k == pair or k == asset), None)
                
                if matching_key:
                    # 'c' = last trade closed array [price, lot volume]
                    price = float(ticker_data[matching_key]["c"][0])
                    value_eur = amount * price
                    # Gebruik het paar-naam als symbool voor consistentie met signalen
                    positions[matching_key] = value_eur

        except (KrakenError, ConnectionError, KeyError, ValueError) as e:
            log.error(f"Fout bij ophalen Kraken posities: {e}")

        return positions

    def place_order(
        self,
        signal: TradeSignal,
        size_eur: float,
        sl_pct: float,
        tp_pct: float
    ) -> Optional[dict]:
        """
        Plaats een entry order.
        Kraken heeft geen native 'Bracket' order in de eenvoudige API.
        We plaatsen een Market Buy, en als die lukt, 2 aparte orders (Stop Loss + Take Profit).
        LET OP: Kraken vereist dat je margin hebt voor shorting, of balans voor buying.
        """
        if not self._connected:
            return None

        symbol = signal.symbol # Verwacht formaat: "XXBTZEUR"
        side = "buy" if signal.direction == "long" else "sell"
        
        try:
            # 1. Bereken volume
            # We hebben prijs nodig om volume te berekenen
            price = self.get_price(symbol)
            if price <= 0:
                log.error(f"Kan geen prijs ophalen voor {symbol}")
                return None

            volume = size_eur / price
            
            # Rond volume af (Kraken heeft limieten, 8 decimalen is vaak ok voor crypto)
            volume_str = f"{volume:.8f}"

            # 2. Plaats Market Order
            log.info(f"Kraken plaatsen: {side} {volume_str} {symbol}")
            order_resp = self.api.query_private("AddOrder", {
                "pair": symbol,
                "type": side,
                "ordertype": "market",
                "volume": volume_str,
            })
            self._check_errors(order_resp, "Kraken entry")
            
            txid = order_resp["result"]["txid"]
            log.info(f"Kraken entry geslaagd: {txid}")

            # 3. Plaats SL en TP (als aparte orders)
            # OCO (One Cancels Other) wordt niet direct ondersteund via simple AddOrder zonder advanced features
            # Voor nu plaatsen we ze los. Het risico is dat beide raken in extreme volatiliteit.
            # Beter zou zijn: 'stop-loss-profit' order type indien beschikbaar, of advanced order workflows.
            # Kraken ondersteunt conditionele close orders via 'close' parameters in AddOrder, maar dat is complex.
            
            # Entry prijs is nodig voor SL/TP berekening. We gebruiken huidige marktprijs als benadering
            # of wachten op execution report (wat traag is hier).
            
            stop_price = price * (1 - sl_pct) if side == "buy" else price * (1 + sl_pct)
            tp_price = price * (1 + tp_pct) if side == "buy" else price * (1 - tp_pct)
            
            # SL Order
            sl_side = "sell" if side == "buy" else "buy"
            self.api.query_private("AddOrder", {
                "pair": symbol,
                "type": sl_side,
                "ordertype": "stop-loss",
                "price": f"{stop_price:.2f}",
                "volume": volume_str
            })
            
            # TP Order
            self.api.query_private("AddOrder", {
                "pair": symbol,
                "type": sl_side,
                "ordertype": "take-profit",
                "price": f"{tp_price:.2f}",
                "volume": volume_str
            })
            
            log.info(f"Kraken SL ({stop_price:.2f}) en TP ({tp_price:.2f}) geplaatst")

            return {
                "id": txid[0],
                "symbol": symbol,
                "quantity": float(volume_str),
                "price": price
            }

        except (KrakenError, ConnectionError, KeyError, ValueError) as e:
            log.error(f"Fout bij plaatsen Kraken order {symbol}: {e}")
            return None

    def get_price(self, pair: str) -> float:
        """Helper voor huidige prijs."""
        try:
            response = self.api.query_public("Ticker", {"pair": pair})
            self._check_errors(response, f"Kraken prijs {pair}")
            ticker_key = list(response["result"].keys())[0]
            # 'c' = last trade closed [price, lot volume]
            return float(response["result"][ticker_key]["c"][0])
        except (KrakenError, ConnectionError, KeyError, ValueError):
            return 0.0
