"""
base.py — BaseStrategy: basisklasse voor alle trading strategieen.

Biedt:
  - set_adaptive(): koppel AdaptiveEngine voor zelfaanpassing
  - _get_custom_state(): haal custom_state op uit AdaptiveEngine
  - _set_custom_state(): sla custom_state op in AdaptiveEngine
  - _get_rolling_win_rate(): haal rolling win-rate uit AdaptiveEngine
  - _llm_enhance_signals(): LLM-gebaseerde signaalkwaliteit-analyse
  - generate_signals(): abstracte methode (override in subklassen)
"""

import logging
from typing import Optional

from nakedtrader.types import TradeSignal, StrategyMeta
from nakedtrader.brokers import KrakenBroker, IBKRBroker

log = logging.getLogger(__name__)


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

    def _llm_enhance_signals(self, signals: list[TradeSignal]) -> list[TradeSignal]:
        """Verrijk signalen met LLM-analyse. Past win_probability aan.

        Graceful degradation: als LLM niet beschikbaar, retourneert originele signalen.
        """
        if not signals:
            return signals

        try:
            from nakedtrader.ai.signal_analyzer import analyze_signal, build_market_context

            win_rate = self._get_rolling_win_rate()

            for signal in signals:
                context = build_market_context(signal.symbol, signal.strategy_id, win_rate)
                result = analyze_signal(
                    strategy_id=signal.strategy_id,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    win_prob=signal.win_probability,
                    price=signal.current_price,
                    notes=signal.notes or "",
                    market_context=context,
                )
                if result and "adjustment" in result:
                    old_wp = signal.win_probability
                    signal.win_probability = max(0.30, min(0.90, old_wp + result["adjustment"]))
                    quality = result.get("quality", 0.5)
                    reason = result.get("reason", "")
                    signal.notes = (signal.notes or "") + f" | LLM: q={quality:.2f} adj={result['adjustment']:+.3f} ({reason})"
        except Exception as e:
            log.debug("LLM signal enhancement overgeslagen: %s", e)

        return signals

    def generate_signals(
        self,
        kraken: Optional[KrakenBroker] = None,
        ibkr: Optional[IBKRBroker] = None,
        aggressive: bool = False,
    ) -> list[TradeSignal]:
        raise NotImplementedError
