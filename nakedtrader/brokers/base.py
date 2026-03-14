"""
nakedtrader/brokers/base.py — Abstracte base class voor brokers.

Zorgt ervoor dat de orchestrator onafhankelijk is van specifieke broker-implementaties.
"""

from abc import ABC, abstractmethod
from typing import Optional, Literal
from nakedtrader.config import Config
from nakedtrader.types import TradeSignal

class AbstractBroker(ABC):
    """Abstracte base class voor alle broker implementaties."""

    def __init__(self, config: Config):
        self.config = config

    @abstractmethod
    def connect(self) -> bool:
        """Maak verbinding met de broker API. Retourneert True bij succes."""
        pass

    @abstractmethod
    def disconnect(self):
        """Sluit de verbinding met de broker API."""
        pass

    @abstractmethod
    def get_balance(self) -> float:
        """Retourneert het vrije saldo in de base currency (EUR)."""
        pass

    @abstractmethod
    def get_positions(self) -> dict[str, float]:
        """
        Retourneert open posities als {symbol: size_eur}.
        Bijv: {"AAPL": 1500.0, "XXBTZEUR": 500.0}
        """
        pass

    @abstractmethod
    def place_order(
        self,
        signal: TradeSignal,
        size_eur: float,
        sl_pct: float,
        tp_pct: float
    ) -> Optional[dict]:
        """
        Plaats een entry order met gekoppelde SL/TP (indien ondersteund).

        Args:
            signal: Het TradeSignal object (bevat symbol, direction, entry_price).
            size_eur: De grootte van de positie in EUR.
            sl_pct: Stop-loss percentage (bijv. 0.05 voor 5%).
            tp_pct: Take-profit percentage (bijv. 0.10 voor 10%).

        Returns:
            Een dict met order details of None bij falen.
        """
        pass
