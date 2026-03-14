"""
nakedtrader.orchestrator_c — Shadow orchestrator C: diversificatie-gedreven (paper only).

Verschilt van A:
  - Blootstelling 60%
  - Weigert signaal als portefeuille-correlatie > 0.6
  - Sector-spreiding: max 30% per sector
  - Bij twijfel: kiest signaal met meeste diversificatiewaarde
"""

import logging
from typing import Optional

from nakedtrader.config import Config
from nakedtrader.types import TradeSignal, ExecutionReport
from nakedtrader.orchestrator import BaseOrchestrator, SECTOR_MAP

log = logging.getLogger(__name__)

# Max sector exposure als fractie van totaal kapitaal
MAX_SECTOR_PCT = 0.30


class OrchestratorC(BaseOrchestrator):
    """Diversificatie-gedreven shadow orchestrator. Altijd paper mode."""

    orchestrator_id = "C"
    exposure_limit_pct = 0.60
    win_prob_threshold = 0.60
    win_prob_orange_add = 0.05     # → 0.65 bij orange
    kelly_correction = 0.9
    force_paper = True             # altijd shadow

    # Handelt bij green en orange
    allow_at_orange = [
        "momentum", "mean-reversion", "breakout", "trend-follow",
    ]
    allow_at_red = []

    def _check_portfolio_coherence(self, signal: TradeSignal) -> Optional[ExecutionReport]:
        """Override: strikte diversificatie-checks."""
        sid = signal.strategy_id or ""

        # P4: Cooldown check (behouden)
        if sid and self.adaptive.is_in_cooldown(sid):
            return self._make_report(
                signal, "blocked", f"{sid} in cooldown na verliesreeks", "P4",
            )

        # P1: Conflictresolutie (streng — altijd blokkeren bij dubbel instrument)
        if signal.symbol in self.open_positions:
            return self._make_report(
                signal, "blocked",
                f"Diversificatie: {signal.symbol} al in portefeuille", "P1",
            )

        # P3: Sector-concentratie check
        sector = SECTOR_MAP.get(signal.symbol, "other")
        sector_exposure = 0.0
        for sym, size in self.open_positions.items():
            if SECTOR_MAP.get(sym, "other") == sector:
                sector_exposure += size

        max_sector = self.config.total_capital * MAX_SECTOR_PCT
        if sector_exposure >= max_sector:
            return self._make_report(
                signal, "blocked",
                f"Sector '{sector}' exposure €{sector_exposure:,.0f} >= limiet €{max_sector:,.0f} ({MAX_SECTOR_PCT:.0%})", "P3",
            )

        # Correlatie check: >=3 posities in dezelfde sector → blokkeer
        same_sector_count = sum(
            1 for sym in self.open_positions
            if SECTOR_MAP.get(sym, "other") == sector
        )
        if same_sector_count >= 3:
            return self._make_report(
                signal, "blocked",
                f"Correlatie: al {same_sector_count} posities in sector '{sector}'", "P3",
            )

        # P2: Prioriteit check (behouden van base)
        return super()._check_portfolio_coherence(signal)
