"""nakedtrader.orchestrator.signal_filter — 3-laags regelset (H/R/P)."""

import logging
from datetime import datetime
from typing import Optional

from nakedtrader.types import TradeSignal, ExecutionReport
from nakedtrader.orchestrator_pkg.constants import (
    MAX_SIGNAL_AGE, STRATEGY_PRIORITY,
)

log = logging.getLogger(__name__)


class SignalFilterMixin:
    """Mixin met de H/R/P signaalfilters.

    Verwacht dat de host-klasse deze attributen heeft:
      config, _current_risk, risk_mgr, open_positions, adaptive,
      exposure_limit_pct, win_prob_threshold, win_prob_orange_add,
      allow_at_orange, allow_at_red, orchestrator_id, _make_report().
    """

    # ── LAAG 1: HARDE GRENZEN ────────────────────────

    def _check_hard_limits(self, signal: TradeSignal) -> Optional[ExecutionReport]:
        sid = signal.strategy_id or ""

        # H1: Emergency brake
        if self._current_risk and self._current_risk.emergency_brake:
            return self._make_report(
                signal, "blocked",
                f"Noodrem actief (score={self._current_risk.risk_score:.2f})", "H1",
            )

        # H2: Maximale blootstelling
        total_exposure = sum(self.open_positions.values())
        limit = self.config.total_capital * self.exposure_limit_pct
        if total_exposure >= limit:
            return self._make_report(
                signal, "blocked",
                f"Exposure €{total_exposure:,.0f} >= limiet €{limit:,.0f} ({self.exposure_limit_pct:.0%})", "H2",
            )

        # H3: Instrument-concentratie
        symbol_exposure = self.open_positions.get(signal.symbol, 0.0)
        max_per_instrument = self.config.total_capital * self.config.max_position_pct
        if symbol_exposure >= max_per_instrument:
            return self._make_report(
                signal, "blocked",
                f"Concentratie {signal.symbol}: €{symbol_exposure:,.0f} >= max €{max_per_instrument:,.0f}", "H3",
            )

        # H5: Signaalverval
        max_age = MAX_SIGNAL_AGE.get(sid, 4 * 3600)
        if hasattr(signal, 'timestamp') and signal.timestamp:
            try:
                signal_time = datetime.fromisoformat(signal.timestamp)
                age = (datetime.now() - signal_time).total_seconds()
                if age > max_age:
                    return self._make_report(
                        signal, "expired",
                        f"Signaal te oud ({age:.0f}s > {max_age}s)", "H5",
                    )
            except (ValueError, TypeError):
                pass

        return None

    # ── LAAG 2: RISICO-GEWOGEN ───────────────────────

    def _check_risk_rules(self, signal: TradeSignal) -> Optional[ExecutionReport]:
        sid = signal.strategy_id or ""
        risk_level = self._current_risk.risk_level if self._current_risk else "green"
        drawdown = self.risk_mgr.drawdown_pct

        # R6: Drawdown halt (>15%, circuit-breaker)
        if self.risk_mgr.halted or drawdown >= 0.15:
            return self._make_report(
                signal, "blocked",
                f"Circuit-breaker: drawdown {drawdown:.1%}", "R6",
            )

        # R5: Drawdown reduce (>10%)
        if drawdown >= 0.10:
            allowed = {"mean-reversion", "trend-follow"}
            if sid not in allowed:
                return self._make_report(
                    signal, "blocked",
                    f"Drawdown {drawdown:.1%} > 10%: alleen {allowed} actief", "R5",
                )

        # R3: Red — geen nieuwe posities (uitzondering via allow_at_red)
        if risk_level == "red" and sid not in self.allow_at_red:
            return self._make_report(
                signal, "blocked",
                f"Risk level RED: {sid} niet toegelaten", "R3",
            )

        # R2: Orange — verhoogde drempel
        if risk_level == "orange":
            threshold = self.win_prob_threshold + self.win_prob_orange_add
            if signal.win_probability < threshold and sid not in self.allow_at_orange:
                return self._make_report(
                    signal, "blocked",
                    f"Orange: win_prob {signal.win_probability:.2f} < drempel {threshold:.2f}", "R2",
                )

        # R1: Green — win_prob check
        if risk_level == "green":
            if signal.win_probability < self.win_prob_threshold:
                return self._make_report(
                    signal, "blocked",
                    f"Win prob {signal.win_probability:.2f} < drempel {self.win_prob_threshold:.2f}", "R1",
                )

        return None

    # ── LAAG 3: PORTEFEUILLE-COHERENTIE ──────────────

    def _check_portfolio_coherence(self, signal: TradeSignal) -> Optional[ExecutionReport]:
        sid = signal.strategy_id or ""

        # P4: Cooldown na verliesreeks (AdaptiveEngine)
        if sid and self.adaptive.is_in_cooldown(sid):
            return self._make_report(
                signal, "blocked",
                f"{sid} in cooldown na verliesreeks", "P4",
            )

        # P1: Conflictresolutie — 2 bots tegengesteld op zelfde instrument
        for existing_sym in self.open_positions:
            if existing_sym == signal.symbol:
                own_wr = self.adaptive.get_rolling_win_rate(sid)
                if own_wr < 0.5:
                    return self._make_report(
                        signal, "blocked",
                        f"Conflict: {signal.symbol} al in portefeuille, win-rate {own_wr:.2f} < 0.50", "P1",
                    )

        # P2: Prioriteit bij kapitaalschaarste
        total_exposure = sum(self.open_positions.values())
        if total_exposure > self.config.total_capital * self.exposure_limit_pct * 0.8:
            prio = STRATEGY_PRIORITY.index(sid) if sid in STRATEGY_PRIORITY else 99
            if prio > 2:  # Alleen top 3 strategieën bij schaarste
                return self._make_report(
                    signal, "blocked",
                    f"Kapitaalschaarste: {sid} (prio {prio}) overgeslagen", "P2",
                )

        return None
