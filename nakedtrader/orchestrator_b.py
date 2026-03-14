"""
nakedtrader.orchestrator_b — Shadow orchestrator B: agressief (paper only).

Verschilt van A:
  - Hogere blootstellingslimiet (70%)
  - Lagere win_prob drempel (0.55)
  - Geen Kelly-correctie (×1.0)
  - Handelt ook bij orange risk + Breakout bij red
  - Gebruikt recente win-rate (10 trades) boven historisch (50)
  - Bij twijfel: uitvoeren
"""

import logging
from typing import Optional

from nakedtrader.config import Config
from nakedtrader.types import TradeSignal, ExecutionReport
from nakedtrader.orchestrator import BaseOrchestrator

log = logging.getLogger(__name__)


class OrchestratorB(BaseOrchestrator):
    """Agressieve shadow orchestrator. Altijd paper mode."""

    orchestrator_id = "B"
    exposure_limit_pct = 0.70
    win_prob_threshold = 0.55
    win_prob_orange_add = 0.05     # → 0.60 bij orange
    kelly_correction = 1.0         # geen reductie
    force_paper = True             # altijd shadow

    # Alle strategieën actief bij orange, Breakout ook bij red
    allow_at_orange = [
        "momentum", "mean-reversion", "breakout", "trend-follow", "arbitrage",
    ]
    allow_at_red = ["breakout"]

    def _calculate_size(self, signal: TradeSignal) -> float:
        """Override: gebruik recente win-rate (10 trades) i.p.v. rolling (50)."""
        sid = signal.strategy_id or ""

        # Recente win-rate (10 trades) — agressiever dan 50-trade rolling
        win_prob = signal.win_probability
        if sid:
            state = self.adaptive.get_state(sid)
            recent = state.recent_trades[-10:] if len(state.recent_trades) >= 10 else state.recent_trades
            if recent:
                wins = sum(1 for t in recent if t.get("outcome") == "win")
                win_prob = wins / len(recent)

        kelly_frac = self.sizer.calculate(win_prob, signal.expected_win_pct, signal.expected_loss_pct)

        # Macro risk Kelly-reductie (maar geen extra orchestrator correctie)
        if self._current_risk:
            kelly_frac *= self._current_risk.kelly_mult

        kelly_frac *= self.kelly_correction

        # R4: Drawdown caution (>5%) — halveer (gedeelde regel)
        if self.risk_mgr.drawdown_pct >= 0.05:
            kelly_frac *= 0.5

        size_eur = self.config.total_capital * kelly_frac

        if sid and not self.risk_mgr.allow_trade(sid, size_eur):
            return 0.0

        return size_eur

    def _check_portfolio_coherence(self, signal: TradeSignal) -> Optional[ExecutionReport]:
        """Override: bij twijfel uitvoeren — minder strenge coherentie."""
        sid = signal.strategy_id or ""

        # P4: Cooldown check (behouden)
        if sid and self.adaptive.is_in_cooldown(sid):
            return self._make_report(
                signal, "blocked", f"{sid} in cooldown na verliesreeks", "P4",
            )

        # P1: Conflicten worden genegeerd (agressief)
        # P2: Geen kapitaalschaarste-blokkade (agressief)

        return None
