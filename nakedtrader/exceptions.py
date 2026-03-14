"""nakedtrader.exceptions — Custom exceptions voor alle componenten."""


class NakedTraderError(Exception):
    """Basis-uitzondering voor NakedTrader."""


class KrakenError(NakedTraderError):
    """Fout bij Kraken API communicatie."""


class IBKRError(NakedTraderError):
    """Fout bij Interactive Brokers communicatie."""


class StrategyError(NakedTraderError):
    """Fout in strategie-uitvoering (signalen, parameters, etc.)."""
