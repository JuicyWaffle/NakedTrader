"""
base.py — BaseStrategy: basisklasse voor alle trading strategieen.

Biedt:
  - set_adaptive(): koppel AdaptiveEngine voor zelfaanpassing
  - _get_custom_state(): haal custom_state op uit AdaptiveEngine
  - _set_custom_state(): sla custom_state op in AdaptiveEngine
  - _get_rolling_win_rate(): haal rolling win-rate uit AdaptiveEngine
  - generate_signals(): abstracte methode (override in subklassen)
"""

from typing import Optional

from nakedtrader.types import TradeSignal, StrategyMeta
from nakedtrader.brokers import KrakenBroker, IBKRBroker


class BaseStrategy:
    meta: StrategyMeta

    # Optionele referentie naar AdaptiveEngine (inject via TradingBot)
    _adaptive = None

    def set_adaptive(self, adaptive):
        """Koppel AdaptiveEngine voor zelfaanpassing."""
        self._adaptive = adaptive

    def _get_custom_state(self) -> dict:
        """Haal custom_state op uit AdaptiveEngine."""
        if self._adaptive:
            return self._adaptive.get_state(self.meta.id).custom_state
        return {}

    def _set_custom_state(self, key: str, value):
        """Sla custom_state op in AdaptiveEngine."""
        if self._adaptive:
            state = self._adaptive.get_state(self.meta.id)
            state.custom_state[key] = value
            self._adaptive._save()

    def _get_rolling_win_rate(self) -> float:
        """Haal rolling win-rate uit AdaptiveEngine."""
        if self._adaptive:
            return self._adaptive.get_rolling_win_rate(self.meta.id)
        return 0.5

    def generate_signals(
        self,
        kraken: Optional[KrakenBroker] = None,
        ibkr: Optional[IBKRBroker] = None,
        aggressive: bool = False,
    ) -> list[TradeSignal]:
        raise NotImplementedError
