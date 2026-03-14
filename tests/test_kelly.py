"""Unit tests for nakedtrader.money.kelly."""

import pytest

from nakedtrader.money.kelly import KellyPositionSizer


class TestKellyPositionSizer:
    def setup_method(self):
        self.sizer = KellyPositionSizer(kelly_fraction=0.5, max_position_pct=0.20)

    def test_basic_calculation(self):
        # p=0.60, b=0.10, a=0.05
        # kelly_full = (0.60 / 0.05) - (0.40 / 0.10) = 12 - 4 = 8
        # kelly_adjusted = 8 * 0.5 = 4.0
        # capped at 0.20
        result = self.sizer.calculate(0.60, 0.10, 0.05)
        assert result == 0.20  # capped at max

    def test_negative_kelly(self):
        # When expected loss is much larger than expected win
        # p=0.30, b=0.05, a=0.10
        # kelly_full = (0.30 / 0.10) - (0.70 / 0.05) = 3 - 14 = -11
        result = self.sizer.calculate(0.30, 0.05, 0.10)
        assert result == 0.0

    def test_zero_win_pct(self):
        result = self.sizer.calculate(0.60, 0.0, 0.05)
        assert result == 0.0

    def test_zero_loss_pct(self):
        result = self.sizer.calculate(0.60, 0.10, 0.0)
        assert result == 0.0

    def test_fifty_fifty(self):
        # p=0.50, b=0.10, a=0.10
        # kelly_full = (0.50 / 0.10) - (0.50 / 0.10) = 5 - 5 = 0
        result = self.sizer.calculate(0.50, 0.10, 0.10)
        assert result == 0.0

    def test_moderate_edge(self):
        # p=0.55, b=0.10, a=0.05
        # kelly_full = (0.55 / 0.05) - (0.45 / 0.10) = 11 - 4.5 = 6.5
        # adjusted = 6.5 * 0.5 = 3.25 → capped at 0.20
        result = self.sizer.calculate(0.55, 0.10, 0.05)
        assert result == 0.20

    def test_small_edge(self):
        # p=0.52, b=0.02, a=0.02
        # kelly_full = (0.52/0.02) - (0.48/0.02) = 26 - 24 = 2
        # adjusted = 2 * 0.5 = 1.0 → capped at 0.20
        result = self.sizer.calculate(0.52, 0.02, 0.02)
        assert result == 0.20

    def test_custom_fraction(self):
        sizer = KellyPositionSizer(kelly_fraction=0.25, max_position_pct=0.50)
        result = sizer.calculate(0.52, 0.02, 0.02)
        # kelly_full = 2.0, adjusted = 2.0 * 0.25 = 0.50
        assert result == 0.50

    def test_position_size_eur(self):
        result = self.sizer.position_size_eur(100_000, 0.60, 0.10, 0.05)
        assert result == 20_000.0  # 0.20 * 100000

    def test_position_size_eur_zero(self):
        result = self.sizer.position_size_eur(100_000, 0.30, 0.05, 0.10)
        assert result == 0.0

    def test_max_position_respected(self):
        sizer = KellyPositionSizer(kelly_fraction=1.0, max_position_pct=0.10)
        result = sizer.calculate(0.60, 0.10, 0.05)
        assert result == 0.10  # capped at 10%

    def test_result_in_bounds(self):
        """Kelly should always return 0 <= result <= max_position_pct."""
        import random
        random.seed(42)
        for _ in range(100):
            wp = random.uniform(0.0, 1.0)
            aw = random.uniform(0.01, 0.50)
            al = random.uniform(0.01, 0.50)
            result = self.sizer.calculate(wp, aw, al)
            assert 0.0 <= result <= 0.20
