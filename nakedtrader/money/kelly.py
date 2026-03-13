"""
nakedtrader/money/kelly.py — Kelly Criterion positiebepaling.

Berekent de optimale positiegrootte via Kelly Criterion.
Standalone versie: accepteert individuele parameters i.p.v. een Config object.
"""

import logging

log = logging.getLogger(__name__)


class KellyPositionSizer:
    """
    Berekent de optimale positiegrootte via Kelly Criterion.

    f* = (p / a) - ((1-p) / b)
      p = winkans
      b = gemiddeld winstpercentage bij een win
      a = gemiddeld verliespercentage bij een loss
    """

    def __init__(self, kelly_fraction: float = 0.5, max_position_pct: float = 0.20):
        self.kelly_fraction = kelly_fraction
        self.max_position_pct = max_position_pct

    def calculate(
        self,
        win_probability: float,
        avg_win_pct: float,
        avg_loss_pct: float,
    ) -> float:
        """Geeft de positiegrootte als fractie van het kapitaal (0–max_position_pct)."""
        if avg_loss_pct <= 0 or avg_win_pct <= 0:
            return 0.0

        loss_prob = 1 - win_probability
        kelly_full = (win_probability / avg_loss_pct) - (loss_prob / avg_win_pct)
        kelly_adjusted = kelly_full * self.kelly_fraction
        fraction = max(0.0, min(kelly_adjusted, self.max_position_pct))

        log.debug(
            f"Kelly: volledig={kelly_full:.3f}  "
            f"gecorrigeerd={kelly_adjusted:.3f}  "
            f"toegepast={fraction:.3f}"
        )
        return fraction

    def position_size_eur(
        self,
        total_capital: float,
        win_probability: float,
        avg_win_pct: float,
        avg_loss_pct: float,
    ) -> float:
        fraction = self.calculate(win_probability, avg_win_pct, avg_loss_pct)
        return total_capital * fraction
