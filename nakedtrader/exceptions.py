"""nakedtrader.exceptions — Custom exceptions voor alle componenten."""


class NakedTraderError(Exception):
    """Basis-uitzondering voor NakedTrader."""


class KrakenError(NakedTraderError):
    """Fout bij Kraken API communicatie."""


class IBKRError(NakedTraderError):
    """Fout bij Interactive Brokers communicatie."""


class StrategyError(NakedTraderError):
    """Fout in strategie-uitvoering (signalen, parameters, etc.)."""


class ConfigError(NakedTraderError):
    """Ongeldige configuratiewaarden."""


class BrokerConnectionError(NakedTraderError):
    """Kan geen verbinding maken met broker."""


class BrokerOrderError(NakedTraderError):
    """Fout bij het plaatsen of beheren van een order."""


class BinanceError(NakedTraderError):
    """Fout bij Binance API communicatie."""


class MacroRiskError(NakedTraderError):
    """Fout in macro risk engine evaluatie."""


class DataFetchError(NakedTraderError):
    """Fout bij ophalen van externe data (FRED, yfinance, GDELT, etc.)."""
