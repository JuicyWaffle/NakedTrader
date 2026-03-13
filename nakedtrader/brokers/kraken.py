"""
kraken.py — Kraken crypto exchange connector via krakenex.

Standaard crypto broker voor spot orders met stop-loss/take-profit.
Kraken symboolnotatie: XXBTZEUR (BTC/EUR), XETHZEUR (ETH/EUR), XBTUSDT, etc.
"""

import logging
from typing import Optional

log = logging.getLogger(__name__)


class KrakenBroker:
    """
    Verbinding met Kraken via krakenex (direct, zonder pykrakenapi).

    Kraken gebruikt andere symboolnotatie dan Binance:
      Bitcoin  -> XXBTZEUR  (BTC/EUR)  of  XBTUSDT
      Ethereum -> XETHZEUR  (ETH/EUR)  of  ETHUSD
    Gebruik altijd de volledige Kraken paar-naam.
    """

    def __init__(self, config):
        self.config = config
        self.api = None

    def _check_errors(self, response, context="Kraken"):
        """Controleer op API fouten en gooi RuntimeError als nodig."""
        errors = response.get("error", [])
        if errors:
            raise RuntimeError(f"{context}: {errors}")

    def connect(self):
        import krakenex
        self.api = krakenex.API(
            key=self.config.kraken_api_key,
            secret=self.config.kraken_api_secret,
        )
        # Verbinding testen via accountbalans
        response = self.api.query_private("Balance")
        self._check_errors(response, "Kraken connect")
        assets = [a for a, v in response["result"].items() if float(v) > 0]
        log.info(f"Kraken verbonden. Assets met saldo: {assets}")

    def get_price(self, pair: str) -> float:
        """
        Haal de huidige biedprijs op.
        pair: bv. "XXBTZEUR", "XETHZEUR", "XBTUSDT"
        """
        response = self.api.query_public("Ticker", {"pair": pair})
        self._check_errors(response, f"Kraken prijs {pair}")
        # Eerste (en enige) key in result bevat de ticker data
        ticker_key = list(response["result"].keys())[0]
        price = float(response["result"][ticker_key]["b"][0])  # beste biedprijs
        log.info(f"Kraken prijs {pair}: {price}")
        return price

    def place_market_order(
        self,
        pair: str,
        side: str,          # "buy" of "sell"
        volume: float,
    ) -> dict:
        """Plaats een marktorder op Kraken."""
        response = self.api.query_private("AddOrder", {
            "pair": pair,
            "type": side,
            "ordertype": "market",
            "volume": str(round(volume, 8)),
        })
        self._check_errors(response, "Kraken market order")
        tx_ids = response["result"].get("txid", [])
        log.info(f"Kraken market order: {side} {volume} {pair}  txid={tx_ids}")
        return response["result"]

    def place_stop_loss_take_profit(
        self,
        pair: str,
        volume: float,
        stop_price: float,
        take_profit_price: float,
    ):
        """
        Plaats twee aparte orders na een koop:
          1. Stop-loss (stop-market order)
          2. Take-profit (limit sell order)

        Kraken heeft geen native OCO — beide orders staan open
        en je annuleert de andere zodra één geraakt wordt.
        """
        # Stop-loss
        sl_response = self.api.query_private("AddOrder", {
            "pair": pair,
            "type": "sell",
            "ordertype": "stop-loss",
            "price": str(round(stop_price, 2)),
            "volume": str(round(volume, 8)),
        })
        self._check_errors(sl_response, "Kraken SL")

        # Take-profit
        tp_response = self.api.query_private("AddOrder", {
            "pair": pair,
            "type": "sell",
            "ordertype": "take-profit",
            "price": str(round(take_profit_price, 2)),
            "volume": str(round(volume, 8)),
        })
        self._check_errors(tp_response, "Kraken TP")

        sl_id = sl_response["result"].get("txid", [])
        tp_id = tp_response["result"].get("txid", [])
        log.info(f"Kraken SL+TP: {pair}  SL={stop_price} (txid={sl_id})  TP={take_profit_price} (txid={tp_id})")
        return {"stop_loss": sl_response["result"], "take_profit": tp_response["result"]}

    def get_open_orders(self) -> dict:
        """Geeft alle open orders terug."""
        response = self.api.query_private("OpenOrders")
        self._check_errors(response, "Kraken open orders")
        return response.get("result", {}).get("open", {})

    def cancel_order(self, txid: str):
        """Annuleer een order op basis van zijn transactie-ID."""
        response = self.api.query_private("CancelOrder", {"txid": txid})
        self._check_errors(response, "Kraken cancel")
        log.info(f"Kraken order geannuleerd: {txid}")
        return response

    def get_ohlc(self, pair: str, interval: int = 60, since: int = None) -> list:
        """
        Haal OHLC (candlestick) data op via de publieke Kraken API.

        Args:
            pair: bv. "XXBTZEUR", "XETHZEUR"
            interval: interval in minuten (1, 5, 15, 30, 60, 240, 1440, 10080, 21600)
            since: Unix timestamp — geeft candles na dit tijdstip

        Returns:
            list van [time, open, high, low, close, vwap, volume, count]
        """
        import krakenex
        api = self.api if self.api else krakenex.API()
        params = {"pair": pair, "interval": interval}
        if since is not None:
            params["since"] = since
        response = api.query_public("OHLC", params)
        self._check_errors(response, f"Kraken OHLC {pair}")
        # Resultaat bevat pair-key + "last" — we willen de pair-key
        result = response["result"]
        ohlc_key = [k for k in result if k != "last"][0]
        return result[ohlc_key]

    def get_orderbook(self, pair: str, count: int = 25) -> dict:
        """
        Haal het orderboek op via de publieke Kraken Depth API.

        Args:
            pair: bv. "XXBTZEUR", "XETHZEUR"
            count: aantal levels (max 500)

        Returns:
            dict met "bids" en "asks", elk een list van [prijs, volume, timestamp]
        """
        import krakenex
        api = self.api if self.api else krakenex.API()
        response = api.query_public("Depth", {"pair": pair, "count": count})
        self._check_errors(response, f"Kraken orderbook {pair}")
        result = response["result"]
        book_key = [k for k in result][0]
        return {
            "bids": result[book_key].get("bids", []),
            "asks": result[book_key].get("asks", []),
        }

    def get_balances(self) -> dict:
        """Geeft alle saldi terug als dict {asset: balance}."""
        response = self.api.query_private("Balance")
        self._check_errors(response, "Kraken balance")
        return {asset: float(vol) for asset, vol in response["result"].items() if float(vol) > 0}
