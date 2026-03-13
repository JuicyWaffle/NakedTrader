"""
nakedtrader/money/risk.py — Portfolio-level risicobeheer voor NakedTrader.

Features:
  - Drawdown circuit-breaker: halt alle handel bij -15% van peak
  - Per-strategie risk budgets
  - Per-strategie SL/TP overrides
  - Kapitaal tracking
"""

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# RISK LIMITS
# ─────────────────────────────────────────────

@dataclass
class RiskLimits:
    """Globale risk parameters."""
    drawdown_limit_pct: float = 0.15    # 15% max drawdown van peak
    default_sl_pct: float = 0.05        # 5% stop-loss
    default_tp_pct: float = 0.10        # 10% take-profit
    max_position_pct: float = 0.20      # max 20% per positie


# ─────────────────────────────────────────────
# PER-STRATEGIE RISK BUDGETS
# ─────────────────────────────────────────────

DEFAULT_RISK_BUDGETS = {
    "momentum":       0.20,   # 20%
    "mean-reversion": 0.20,   # 20%
    "breakout":       0.25,   # 25%
    "arbitrage":      0.15,   # 15%
    "trend-follow":   0.20,   # 20%
}

# Per-strategie SL/TP overrides (None = gebruik default)
DEFAULT_SL_TP_OVERRIDES = {
    "momentum":       {"sl": 0.05, "tp": 0.10},
    "mean-reversion": {"sl": 0.03, "tp": 0.08},
    "breakout":       {"sl": 0.07, "tp": 0.15},
    "arbitrage":      {"sl": 0.02, "tp": 0.05},
    "trend-follow":   {"sl": 0.05, "tp": 0.12},
}


# ─────────────────────────────────────────────
# RISK MANAGER
# ─────────────────────────────────────────────

class RiskManager:
    """
    Portfolio-level risicobeheer.

    Houdt bij:
      - Huidige kapitaal vs peak → drawdown berekening
      - Per-strategie allocated capital
      - Circuit-breaker: halt alle handel bij max drawdown

    Gebruik:
      rm = RiskManager(RiskLimits(), starting_capital=10000)
      rm.allow_trade("momentum", trade_size=500)
      rm.update_capital(pnl_eur=50.0)
    """

    def __init__(
        self,
        limits: RiskLimits,
        starting_capital: float,
        risk_budgets: dict = None,
        sl_tp_overrides: dict = None,
    ):
        self.limits = limits
        self.starting_capital = starting_capital
        self.current_capital = starting_capital
        self.peak_capital = starting_capital
        self.halted = False

        self.risk_budgets = risk_budgets or dict(DEFAULT_RISK_BUDGETS)
        self.sl_tp_overrides = sl_tp_overrides or dict(DEFAULT_SL_TP_OVERRIDES)

        # Per-strategie allocated (ingezet) kapitaal
        self.allocated: dict[str, float] = {}

    # ── Drawdown ────────────────────────────────

    @property
    def drawdown_pct(self) -> float:
        """Huidige drawdown als fractie (0.0 = geen, 0.15 = -15%)."""
        if self.peak_capital <= 0:
            return 0.0
        return max(0.0, (self.peak_capital - self.current_capital) / self.peak_capital)

    def _check_drawdown(self):
        """Activeer circuit-breaker als drawdown limiet bereikt."""
        dd = self.drawdown_pct
        if dd >= self.limits.drawdown_limit_pct and not self.halted:
            self.halted = True
            log.critical(
                f"[RISK] CIRCUIT BREAKER: drawdown {dd:.1%} >= limiet "
                f"{self.limits.drawdown_limit_pct:.1%} — ALLE HANDEL GESTOPT"
            )

    def reset_halt(self):
        """Manuele reset van de circuit-breaker."""
        self.halted = False
        log.info("[RISK] Circuit-breaker gereset — handel hervat")

    # ── Trade toestaan ──────────────────────────

    def allow_trade(self, strategy_id: str, trade_size: float) -> bool:
        """
        Check of een trade is toegestaan op basis van:
        1. Circuit-breaker niet actief
        2. Per-strategie budget niet overschreden

        Args:
            strategy_id: ID van de strategie
            trade_size: grootte van de trade in EUR

        Returns:
            True als trade is toegestaan
        """
        if self.halted:
            log.warning(f"[RISK] Trade geblokkeerd: circuit-breaker actief")
            return False

        budget_pct = self.risk_budgets.get(strategy_id, self.limits.max_position_pct)
        max_budget = self.current_capital * budget_pct
        currently_allocated = self.allocated.get(strategy_id, 0.0)

        if currently_allocated + trade_size > max_budget:
            log.warning(
                f"[RISK] Trade geblokkeerd: {strategy_id} budget "
                f"(€{currently_allocated:.0f} + €{trade_size:.0f} > "
                f"€{max_budget:.0f} = {budget_pct:.0%})"
            )
            return False

        return True

    def allocate(self, strategy_id: str, amount: float):
        """Registreer gealloceerd kapitaal voor een strategie."""
        self.allocated[strategy_id] = self.allocated.get(strategy_id, 0.0) + amount

    def deallocate(self, strategy_id: str, amount: float):
        """Geef gealloceerd kapitaal terug (na trade close)."""
        current = self.allocated.get(strategy_id, 0.0)
        self.allocated[strategy_id] = max(0.0, current - amount)

    # ── Kapitaal update ─────────────────────────

    def update_capital(self, pnl_eur: float, strategy_id: str = ""):
        """
        Update huidig kapitaal na een trade resultaat.

        Args:
            pnl_eur: winst/verlies in EUR
            strategy_id: optioneel — dealloceer gealloceerd budget
        """
        self.current_capital += pnl_eur

        # Update peak
        if self.current_capital > self.peak_capital:
            self.peak_capital = self.current_capital

        # Check drawdown
        self._check_drawdown()

        # Dealloceer als strategie meegegeven
        if strategy_id and strategy_id in self.allocated:
            # Hele allocatie teruggeven na trade close
            self.allocated[strategy_id] = 0.0

        log.debug(
            f"[RISK] Kapitaal: €{self.current_capital:.2f}  "
            f"peak: €{self.peak_capital:.2f}  "
            f"drawdown: {self.drawdown_pct:.1%}  "
            f"halted: {self.halted}"
        )

    # ── Risk parameters per strategie ───────────

    def get_risk_params(self, strategy_id: str) -> dict:
        """
        Geef SL/TP parameters voor een strategie.

        Returns:
            dict met "sl" en "tp" als fracties
        """
        overrides = self.sl_tp_overrides.get(strategy_id, {})
        return {
            "sl": overrides.get("sl", self.limits.default_sl_pct),
            "tp": overrides.get("tp", self.limits.default_tp_pct),
        }

    # ── Overzicht ───────────────────────────────

    def get_status(self) -> dict:
        """Geef een overzicht van de risk manager status."""
        return {
            "current_capital": round(self.current_capital, 2),
            "peak_capital": round(self.peak_capital, 2),
            "drawdown_pct": round(self.drawdown_pct * 100, 2),
            "drawdown_limit_pct": round(self.limits.drawdown_limit_pct * 100, 2),
            "halted": self.halted,
            "allocated": {k: round(v, 2) for k, v in self.allocated.items()},
            "risk_budgets": self.risk_budgets,
        }
