"""Unit tests for nakedtrader.money.adaptive."""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nakedtrader.money.adaptive import (
    AdaptiveEngine, StrategyState,
    ROLLING_WINDOW, COOLDOWN_THRESHOLD, COOLDOWN_HOURS,
)


@pytest.fixture
def engine(tmp_path):
    """AdaptiveEngine with temporary state file."""
    state_path = tmp_path / "strategy_state.json"
    return AdaptiveEngine(state_path=str(state_path))


class TestStrategyState:
    def test_defaults(self):
        state = StrategyState(strategy_id="test")
        assert state.rolling_win_rate == 0.5
        assert state.rolling_sharpe == 0.0
        assert state.consecutive_losses == 0
        assert state.cooldown_until is None
        assert state.recent_trades == []

    def test_custom_values(self):
        state = StrategyState(
            strategy_id="test",
            rolling_win_rate=0.65,
            consecutive_losses=3,
        )
        assert state.rolling_win_rate == 0.65
        assert state.consecutive_losses == 3


class TestAdaptiveEngineBasic:
    def test_get_state_creates_new(self, engine):
        state = engine.get_state("momentum")
        assert state.strategy_id == "momentum"
        assert state.rolling_win_rate == 0.5

    def test_get_state_returns_existing(self, engine):
        engine.get_state("momentum")
        engine.record_trade("momentum", 5.0, "win")
        state = engine.get_state("momentum")
        assert len(state.recent_trades) == 1


class TestTradeRecording:
    def test_record_win(self, engine):
        engine.record_trade("test", 5.0, "win")
        state = engine.get_state("test")
        assert len(state.recent_trades) == 1
        assert state.recent_trades[0]["outcome"] == "win"
        assert state.consecutive_losses == 0

    def test_record_loss(self, engine):
        engine.record_trade("test", -3.0, "loss")
        state = engine.get_state("test")
        assert state.consecutive_losses == 1

    def test_consecutive_losses_reset_on_win(self, engine):
        engine.record_trade("test", -3.0, "loss")
        engine.record_trade("test", -2.0, "loss")
        engine.record_trade("test", 5.0, "win")
        state = engine.get_state("test")
        assert state.consecutive_losses == 0

    def test_rolling_window_cap(self, engine):
        for i in range(ROLLING_WINDOW + 10):
            engine.record_trade("test", 1.0, "win")
        state = engine.get_state("test")
        assert len(state.recent_trades) == ROLLING_WINDOW


class TestCooldown:
    def test_cooldown_triggers(self, engine):
        for _ in range(COOLDOWN_THRESHOLD):
            engine.record_trade("test", -2.0, "loss")
        assert engine.is_in_cooldown("test")

    def test_cooldown_not_triggered_below_threshold(self, engine):
        for _ in range(COOLDOWN_THRESHOLD - 1):
            engine.record_trade("test", -2.0, "loss")
        assert not engine.is_in_cooldown("test")

    def test_cooldown_expires(self, engine):
        state = engine.get_state("test")
        # Set cooldown in the past
        state.cooldown_until = (datetime.now() - timedelta(hours=1)).isoformat()
        assert not engine.is_in_cooldown("test")

    def test_cooldown_resets_consecutive_losses(self, engine):
        for _ in range(COOLDOWN_THRESHOLD):
            engine.record_trade("test", -2.0, "loss")
        state = engine.get_state("test")
        # After cooldown triggers, consecutive_losses resets to 0
        assert state.consecutive_losses == 0


class TestRollingMetrics:
    def test_win_rate_all_wins(self, engine):
        for _ in range(20):
            engine.record_trade("test", 5.0, "win")
        assert engine.get_rolling_win_rate("test") == 1.0

    def test_win_rate_all_losses(self, engine):
        for _ in range(20):
            engine.record_trade("test", -3.0, "loss")
        # After cooldown triggers at 5 losses, keeps going
        assert engine.get_rolling_win_rate("test") == 0.0

    def test_win_rate_mixed(self, engine):
        for _ in range(6):
            engine.record_trade("test", 5.0, "win")
        for _ in range(4):
            engine.record_trade("test", -3.0, "loss")
        assert abs(engine.get_rolling_win_rate("test") - 0.6) < 0.01

    def test_sharpe_positive(self, engine):
        # Mostly wins with small losses → positive Sharpe
        for i in range(20):
            if i % 3 == 0:
                engine.record_trade("test", -1.0, "loss")
            else:
                engine.record_trade("test", 3.0, "win")
        assert engine.get_rolling_sharpe("test") > 0

    def test_sharpe_negative(self, engine):
        # Mostly losses → negative Sharpe
        for i in range(20):
            if i % 5 == 0:
                engine.record_trade("test", 1.0, "win")
            else:
                engine.record_trade("test", -3.0, "loss")
        assert engine.get_rolling_sharpe("test") < 0

    def test_sharpe_no_trades(self, engine):
        assert engine.get_rolling_sharpe("test") == 0.0


class TestPersistence:
    def test_save_and_reload(self, tmp_path):
        state_path = tmp_path / "state.json"
        engine1 = AdaptiveEngine(state_path=str(state_path))
        engine1.record_trade("momentum", 5.0, "win")
        engine1.record_trade("momentum", -2.0, "loss")

        # Reload from disk
        engine2 = AdaptiveEngine(state_path=str(state_path))
        state = engine2.get_state("momentum")
        assert len(state.recent_trades) == 2
        assert state.consecutive_losses == 1


class TestABTesting:
    def test_start_ab_test(self, engine):
        engine.start_ab_test("test", {"period": 10}, {"period": 20})
        state = engine.get_state("test")
        assert state.ab_active

    def test_get_ab_params_alternates(self, engine):
        engine.start_ab_test("test", {"period": 10}, {"period": 20})
        state = engine.get_state("test")

        # Even trade count → baseline
        params = engine.get_ab_params("test")
        assert params == {"period": 10}

        # After recording a trade, count increments
        engine.record_trade("test", 2.0, "win")
        params = engine.get_ab_params("test")
        assert params == {"period": 20}

    def test_no_ab_returns_none(self, engine):
        assert engine.get_ab_params("test") is None

    def test_evaluate_not_enough_trades(self, engine):
        engine.start_ab_test("test", {"a": 1}, {"b": 2})
        result = engine.evaluate_ab_test("test")
        assert result is None


class TestMonthlyCheck:
    def test_first_check(self, engine):
        for _ in range(25):
            engine.record_trade("test", 5.0, "win")
        result = engine.monthly_check("test")
        assert result["action"] in ("hold", "increase", "reduce")

    def test_skip_if_recent(self, engine):
        engine.monthly_check("test")
        result = engine.monthly_check("test")
        assert result["action"] == "skip"

    def test_reduce_on_low_win_rate(self, engine):
        for _ in range(25):
            engine.record_trade("test", -2.0, "loss")
        # Reset cooldown so we can check the state
        state = engine.get_state("test")
        state.cooldown_until = None
        result = engine.monthly_check("test")
        assert result["action"] == "reduce"
